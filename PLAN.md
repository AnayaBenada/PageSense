# PageSense — Build Plan

Context-aware Chrome extension that answers questions about the current page using a local RAG pipeline:
**Chrome Extension (MV3) → FastAPI → Sentence-Transformers + FAISS → LLaMA 3.2 (via Ollama)**

Every number on the resume (~80% search-time cut, 85%+ relevance, <2s retrieval at 20k tokens, 95% extraction accuracy) must be **produced by our own eval scripts**, not assumed. Step 7 exists for that.

## Status (2026-10-12)

- [x] Steps 0–6: backend, extension, quality pass (cross-encoder reranking), hardening. 24 tests pass
- [x] Step 7: eval dataset (6 pages, 55 questions) + retrieval, latency and answer evals. Results in README
- [ ] Step 7: search-time user study (needs people; protocol in `backend/eval/search_time.py`)
- [ ] Step 7: re-grade `backend/eval/results/answers.csv` yourself (current grades are Claude's)
- [~] Step 8: README done; still to do: load the extension in Chrome, record the demo video, push to GitHub, fix the resume links

---

## Step 0 — Prerequisites & repo skeleton
- [ ] Check tooling: `python3 --version` (≥3.10), `ollama --version`, Chrome installed.
- [ ] Install Ollama if missing, then `ollama pull llama3.2` (3B; fall back to `llama3.2:1b` on low RAM).
- [ ] Create the project in a real folder (e.g. `~/PageSense`), `git init`.
- [ ] Layout:
  ```
  PageSense/
  ├── backend/
  │   ├── app/
  │   │   ├── main.py          # FastAPI app + routes
  │   │   ├── config.py        # settings (model names, chunk sizes, top_k)
  │   │   ├── schemas.py       # Pydantic request/response models
  │   │   ├── chunker.py       # text cleaning + token-aware chunking
  │   │   ├── embedder.py      # Sentence-Transformers singleton
  │   │   ├── index_store.py   # FAISS index per page (cache by URL+content hash)
  │   │   ├── llm.py           # Ollama client, prompt template, streaming
  │   │   └── rag.py           # orchestrates retrieve → prompt → generate
  │   ├── tests/
  │   ├── eval/                # datasets + benchmark scripts (Step 7)
  │   └── requirements.txt
  ├── extension/
  │   ├── manifest.json
  │   ├── background.js        # service worker, API calls
  │   ├── content.js           # page text extraction
  │   ├── popup.html / popup.css / popup.js   # (or side_panel)
  │   └── icons/
  └── README.md
  ```

## Step 1 — Backend foundation
- [ ] `requirements.txt`: `fastapi`, `uvicorn[standard]`, `pydantic`, `sentence-transformers`, `faiss-cpu`, `numpy`, `httpx`, `tiktoken` (token counting), `pytest`.
- [ ] `config.py`: `EMBED_MODEL="all-MiniLM-L6-v2"`, `LLM_MODEL="llama3.2"`, `CHUNK_TOKENS=300`, `CHUNK_OVERLAP=50`, `TOP_K=5`, `OLLAMA_URL`.
- [ ] `main.py`: `GET /health` (checks Ollama reachable + embed model loaded), CORS allowing `chrome-extension://*`.
- [ ] Load the embedding model once at startup (lifespan handler), not per request.
- [ ] Verify: `uvicorn app.main:app --reload` → `/health` returns ok.

## Step 2 — Chunking + embeddings + FAISS
- [ ] `chunker.py`: normalize whitespace, drop boilerplate lines, split on headings/paragraphs first, then pack into ~300-token windows with 50-token overlap. Keep metadata per chunk (index, heading path, char offsets).
- [ ] `embedder.py`: batch-encode with `normalize_embeddings=True`.
- [ ] `index_store.py`: `faiss.IndexFlatIP` (cosine via normalized vectors). Cache key = `sha256(url + text)`; LRU cache of ~20 pages so repeat questions skip re-embedding.
- [ ] Endpoint `POST /index {url, title, text}` → `{page_id, n_chunks, n_tokens, index_ms}`.
- [ ] Tests: chunk boundaries/overlap, empty page, 20k-token page indexes without error.

## Step 3 — Retrieval + LLM answer
- [ ] `rag.py`: embed question → FAISS top-k → optional score threshold → assemble context (ordered by position on page) → prompt.
- [ ] Prompt: system message instructing "answer only from the provided page context; say you can't find it otherwise; cite chunk numbers."
- [ ] `llm.py`: call Ollama `/api/chat` via `httpx`, support streaming.
- [ ] Endpoint `POST /ask {page_id | url+text, question, history?}` → `{answer, sources:[{chunk_id, text_snippet, score}], timings:{retrieve_ms, generate_ms}}`.
- [ ] Streaming variant `POST /ask/stream` (SSE) so the UI shows tokens live.
- [ ] Always return timings — Step 7 depends on them.
- [ ] Manual check with curl on a real docs page.

## Step 4 — Chrome extension (Manifest V3)
- [ ] `manifest.json`: MV3, permissions `activeTab`, `scripting`, `storage`, `sidePanel`; `host_permissions` for `http://localhost:8000/*`.
- [ ] `content.js`: extract readable text — prefer `<main>`/`<article>`, strip `nav/footer/script/style/aside`, keep heading text and code blocks; fallback to `document.body.innerText`. (Optionally bundle Mozilla Readability.)
- [ ] `background.js`: on request, inject content script via `chrome.scripting.executeScript`, POST to `/index`, cache `page_id` per tab URL in `chrome.storage.session`.
- [ ] UI (side panel preferred; popup fine): chat transcript, input box, "Re-read page" button, source snippets under each answer that scroll/highlight the matching text on the page when clicked.
- [ ] States: backend offline, Ollama not running, restricted pages (`chrome://`, Web Store), empty pages, loading spinner.
- [ ] Load unpacked via `chrome://extensions` → Developer mode, test on Wikipedia, MDN, FastAPI docs, a GitHub README.

## Step 5 — Quality pass on the RAG
- [ ] Tune `CHUNK_TOKENS` (200/300/500), `TOP_K` (3/5/8) using the eval set from Step 7.
- [ ] Optional: cross-encoder rerank (`cross-encoder/ms-marco-MiniLM-L-6-v2`) on top-20 → top-5 if relevance is below target.
- [ ] Multi-turn: send last 3 Q/A pairs; rewrite follow-up questions into standalone queries before retrieval.
- [ ] Handle SPAs: re-extract on URL change / user click.

## Step 6 — Hardening
- [ ] Input limits (max text size, e.g. 200k chars), request timeouts, clear JSON errors.
- [ ] Logging of timings per request.
- [ ] `pytest` suite for chunker, index store, `/index`, `/ask` (mock Ollama).
- [ ] No page content leaves the machine (localhost only) — state this in README.

## Step 7 — Evaluation (source of every resume number)
- [ ] **Dataset**: 5–10 technical docs pages (saved HTML/text snapshots for reproducibility) × ~10 questions each = 50–100 Q/A, each with a gold answer and the gold supporting passage.
- [ ] `eval/bench_latency.py`: pages at ~2k, 5k, 10k, 20k tokens; record index time and per-query retrieval time (p50/p95). Target: retrieval < 2s at 20k tokens (FAISS itself should be ms; report end-to-end too).
- [ ] `eval/eval_retrieval.py`: hit@k — does a retrieved chunk contain the gold passage? → "extraction accuracy".
- [ ] `eval/eval_answers.py`: dump answers to CSV; hand-grade relevance 0/1/2 → "answer relevance %" (manual evaluation, as the resume says).
- [ ] Search-time study: time yourself (or a few friends) finding answers with Ctrl+F/skimming vs. PageSense on ~10 questions → % time reduction.
- [ ] Write results table into README. **Update the resume bullets to whatever the measured numbers actually are.**

## Step 8 — Ship
- [ ] README: architecture diagram, setup (Ollama, `pip install`, `uvicorn`, load unpacked), screenshots, eval results table, limitations.
- [ ] Record 1–2 min demo video (ask question on a docs page → streamed answer → click source to highlight).
- [ ] Push to GitHub; replace the placeholder GitHub/Video links on the resume (current ones point to a ChatGPT chat).

---

### Definition of done
1. Load extension → open any docs page → ask a question → grounded, streamed answer with clickable sources.
2. Works fully offline with local Ollama.
3. `pytest` passes; eval scripts reproduce every number cited on the resume.
