"""PageSense API.

POST /index       index a page's text, returns a page_id
POST /ask         answer a question about an indexed page (JSON)
POST /ask/stream  same, streamed as Server-Sent Events
POST /find        Smart Find: sentences on the page that match what the query means
GET  /health      backend, embedder and Ollama status

POST   /library/pages       save an indexed page to the library
GET    /library/pages       list saved pages (?q= filters, ?url= finds one)
DELETE /library/pages/{id}  remove a saved page
POST   /library/search      best passages across all saved pages
"""
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .config import Settings, settings as default_settings
from .embedder import Embedder, Reranker
from .find import find
from .index_store import IndexStore, PageIndex
from .library import Library, SavedPage
from .llm import LLMError, OllamaClient
from .rag import add_neighbors, build_messages, is_overview_question, overview_hits, page_order, retrieval_query, retrieve
from .schemas import (
    AskRequest,
    AskResponse,
    FindMatch,
    FindRequest,
    FindResponse,
    IndexRequest,
    IndexResponse,
    LibraryPagesResponse,
    LibraryResult,
    LibrarySearchRequest,
    LibrarySearchResponse,
    SavedPageOut,
    SaveRequest,
    Source,
    Timings,
)

log = logging.getLogger("pagesense")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def create_app(
    cfg: Settings = default_settings, embedder: Embedder | None = None, llm=None, reranker: Reranker | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        emb = embedder
        if emb is None:
            from .embedder import SentenceTransformerEmbedder

            log.info("Loading embedding model %s", cfg.embed_model)
            emb = SentenceTransformerEmbedder(cfg.embed_model, cfg.embed_device)
        rr = reranker
        if rr is None and embedder is None and cfg.rerank_model:
            from .embedder import CrossEncoderReranker

            log.info("Loading reranker %s", cfg.rerank_model)
            rr = CrossEncoderReranker(cfg.rerank_model, cfg.embed_device)
        app.state.reranker = rr
        app.state.store = IndexStore(emb, cfg.cache_size, cfg.chunk_tokens, cfg.chunk_overlap)
        app.state.library = Library(cfg.library_path, emb, cfg.embed_model)
        app.state.llm = llm or OllamaClient(
            cfg.ollama_url, cfg.llm_model, cfg.llm_temperature, cfg.llm_num_ctx, cfg.llm_timeout_s
        )
        yield
        app.state.library.close()

    app = FastAPI(title="PageSense", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^chrome-extension://.*$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    def _validate_text(text: str) -> None:
        if not text.strip():
            raise HTTPException(422, detail={"code": "empty_page", "message": "The page has no readable text."})
        if len(text) > cfg.max_text_chars:
            raise HTTPException(
                413,
                detail={"code": "page_too_large", "message": f"Page text exceeds {cfg.max_text_chars} characters."},
            )

    async def _index(store: IndexStore, url: str, title: str, text: str) -> tuple[PageIndex, bool]:
        _validate_text(text)
        try:
            return await run_in_threadpool(store.add, url, title, text)
        except ValueError as e:
            raise HTTPException(422, detail={"code": "empty_page", "message": str(e)}) from e

    async def _resolve_page(store: IndexStore, req: AskRequest | FindRequest) -> tuple[PageIndex, float]:
        """Find the page for a question; index it inline if text was sent."""
        if req.page_id and (page := store.get(req.page_id)):
            return page, 0.0
        if req.text is not None and req.url is not None:
            start = time.perf_counter()
            page, _ = await _index(store, req.url, req.title, req.text)
            return page, _ms(start)
        raise HTTPException(
            404,
            detail={"code": "page_not_indexed", "message": "Page not indexed (or evicted). Re-send its text."},
        )

    def _validate_question(q: str) -> str:
        q = q.strip()
        if not q:
            raise HTTPException(422, detail={"code": "empty_question", "message": "Question is empty."})
        if len(q) > cfg.max_question_chars:
            raise HTTPException(422, detail={"code": "question_too_long", "message": "Question is too long."})
        return q

    async def _prepare(request: Request, req: AskRequest):
        store: IndexStore = request.app.state.store
        question = _validate_question(req.question)
        page, index_ms = await _resolve_page(store, req)
        history = req.history[-cfg.history_turns :] if cfg.history_turns else []
        start = time.perf_counter()
        overview = is_overview_question(question)
        if overview:
            hits = overview_hits(page, req.top_k or cfg.top_k)
        else:
            hits = await run_in_threadpool(
                retrieve,
                store,
                page,
                retrieval_query(question, history),
                req.top_k or cfg.top_k,
                cfg.min_score,
                request.app.state.reranker,
                cfg.rerank_candidates,
                cfg.hybrid_search,
            )
            hits = add_neighbors(page, hits, cfg.neighbor_hits)
        hits = page_order(hits)
        retrieve_ms = _ms(start)
        sources = [Source(chunk_id=h.chunk.id, heading=h.chunk.heading, text=h.chunk.text, score=None if h.score is None else round(h.score, 4)) for h in hits]
        messages = build_messages(page, question, hits, history, overview)
        return page, sources, messages, index_ms, retrieve_ms

    @app.get("/health")
    async def health(request: Request):
        llm_status = await request.app.state.llm.health()
        return {
            "status": "ok" if llm_status.get("model_available") else "degraded",
            "embed_model": cfg.embed_model,
            "llm_model": cfg.llm_model,
            "rerank_model": cfg.rerank_model if request.app.state.reranker else None,
            "ollama": llm_status,
            "cached_pages": len(request.app.state.store),
            "library_pages": len(request.app.state.library),
        }

    @app.post("/index", response_model=IndexResponse)
    async def index_page(request: Request, req: IndexRequest):
        page, cached = await _index(request.app.state.store, req.url, req.title, req.text)
        log.info("index url=%s chunks=%d tokens=%d ms=%.1f cached=%s", req.url, len(page.chunks), page.n_tokens, page.index_ms, cached)
        return IndexResponse(
            page_id=page.page_id,
            n_chunks=len(page.chunks),
            n_tokens=page.n_tokens,
            index_ms=round(page.index_ms, 2),
            cached=cached,
        )

    @app.post("/ask", response_model=AskResponse)
    async def ask(request: Request, req: AskRequest):
        total_start = time.perf_counter()
        page, sources, messages, index_ms, retrieve_ms = await _prepare(request, req)
        gen_start = time.perf_counter()
        try:
            answer = await request.app.state.llm.chat(messages)
        except LLMError as e:
            raise HTTPException(503, detail={"code": "llm_unavailable", "message": str(e)}) from e
        timings = Timings(index_ms=index_ms, retrieve_ms=retrieve_ms, generate_ms=_ms(gen_start), total_ms=_ms(total_start))
        log.info("ask page=%s retrieve_ms=%.1f generate_ms=%.1f", page.page_id, timings.retrieve_ms, timings.generate_ms)
        return AskResponse(page_id=page.page_id, answer=answer.strip(), sources=sources, timings=timings)

    @app.post("/ask/stream")
    async def ask_stream(request: Request, req: AskRequest):
        total_start = time.perf_counter()
        page, sources, messages, index_ms, retrieve_ms = await _prepare(request, req)
        llm = request.app.state.llm

        def sse(event: str, data) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        async def events():
            yield sse("meta", {"page_id": page.page_id, "sources": [s.model_dump() for s in sources]})
            gen_start = time.perf_counter()
            try:
                async for tok in llm.stream(messages):
                    if await request.is_disconnected():
                        return
                    yield sse("token", {"t": tok})
            except LLMError as e:
                yield sse("error", {"code": "llm_unavailable", "message": str(e)})
                return
            timings = Timings(index_ms=index_ms, retrieve_ms=retrieve_ms, generate_ms=_ms(gen_start), total_ms=_ms(total_start))
            log.info("ask/stream page=%s retrieve_ms=%.1f generate_ms=%.1f", page.page_id, retrieve_ms, timings.generate_ms)
            yield sse("done", {"timings": timings.model_dump()})

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/find", response_model=FindResponse)
    async def find_on_page(request: Request, req: FindRequest):
        query = _validate_question(req.query)
        page, _ = await _resolve_page(request.app.state.store, req)
        start = time.perf_counter()
        reranker = request.app.state.reranker
        matches = await run_in_threadpool(
            find,
            request.app.state.store,
            page,
            query,
            req.top_k or cfg.find_top_k,
            cfg.find_chunks,
            cfg.min_score,
            reranker,
            cfg.find_rerank_chunks,
            cfg.rerank_candidates,
            cfg.hybrid_search,
        )
        if reranker:
            matches = [m for m in matches if m.score >= cfg.find_min_score]
        log.info("find page=%s matches=%d ms=%.1f", page.page_id, len(matches), _ms(start))
        return FindResponse(page_id=page.page_id, matches=[FindMatch(**vars(m)) for m in matches], find_ms=_ms(start))

    def _saved(p: SavedPage) -> SavedPageOut:
        return SavedPageOut(**vars(p))

    @app.post("/library/pages", response_model=SavedPageOut)
    async def save_page(request: Request, req: SaveRequest):
        page = request.app.state.store.get(req.page_id)
        if page is None:
            raise HTTPException(
                404,
                detail={"code": "page_not_indexed", "message": "Page not indexed (or evicted). Re-send its text."},
            )
        saved = await run_in_threadpool(request.app.state.library.save, page)
        log.info("library save url=%s chunks=%d", saved.url, saved.n_chunks)
        return _saved(saved)

    @app.get("/library/pages", response_model=LibraryPagesResponse)
    async def list_pages(request: Request, q: str = "", url: str = ""):
        pages = await run_in_threadpool(request.app.state.library.pages, q.strip(), url)
        return LibraryPagesResponse(pages=[_saved(p) for p in pages])

    @app.delete("/library/pages/{saved_id}")
    async def delete_page(request: Request, saved_id: int):
        if not await run_in_threadpool(request.app.state.library.delete, saved_id):
            raise HTTPException(404, detail={"code": "not_saved", "message": "That page isn't in your library."})
        return {"deleted": saved_id}

    @app.post("/library/search", response_model=LibrarySearchResponse)
    async def search_library(request: Request, req: LibrarySearchRequest):
        query = _validate_question(req.query)
        start = time.perf_counter()
        hits = await run_in_threadpool(
            request.app.state.library.search,
            query,
            req.top_k or cfg.top_k,
            cfg.min_score,
            request.app.state.reranker,
            cfg.rerank_candidates,
        )
        results = [
            LibraryResult(page=_saved(h.page), chunk_id=h.chunk.id, heading=h.chunk.heading, text=h.chunk.text, score=round(h.score, 4))
            for h in hits
        ]
        return LibrarySearchResponse(results=results, search_ms=_ms(start))

    return app


app = create_app()
