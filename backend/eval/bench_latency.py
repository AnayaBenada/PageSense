"""Retrieval latency vs. page size.

Truncates the largest snapshot (Wikipedia: Transformer, ~20k tokens) to several
sizes, then measures:
  - index time: chunk + embed + build FAISS index (one-off per page)
  - retrieval time per question: embed the query + FAISS search + cross-encoder rerank
    (exactly what the app does on every question)

    python -m eval.bench_latency
"""
import statistics
import time

import tiktoken

import app  # noqa: F401  (OpenMP env setup)
from app.config import settings
from app.rag import retrieve

from .common import load_pages, make_reranker, make_store, save_result

SIZES = [2_000, 5_000, 10_000, 20_000]
QUERIES = [
    "What is learning rate warmup?",
    "How does KV caching work?",
    "Which paper introduced the transformer?",
    "What is FlashAttention?",
    "How does multi-query attention differ from multihead attention?",
    "What tokenizers are used?",
    "What is positional encoding?",
    "What is the pre-LN transformer?",
    "How does speculative decoding work?",
    "What is masked attention?",
]
REPEATS = 3


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def main() -> None:
    enc = tiktoken.get_encoding("cl100k_base")
    page = load_pages()["wiki_transformer"]
    tokens = enc.encode(page["text"], disallowed_special=())
    store = make_store(settings.chunk_tokens, settings.chunk_overlap)
    reranker = make_reranker()
    store.search(store.add("warmup", "", "warm up the model " * 50)[0], "warm up", 1)

    rows = []
    for size in SIZES + ([len(tokens)] if len(tokens) > SIZES[-1] else []):
        text = enc.decode(tokens[:size])
        t0 = time.perf_counter()
        indexed, _ = store.add(f"{page['url']}#{size}", page["title"], text)
        index_ms = (time.perf_counter() - t0) * 1000

        lat = []
        for _ in range(REPEATS):
            for q in QUERIES:
                t0 = time.perf_counter()
                retrieve(store, indexed, q, settings.top_k, settings.min_score, reranker, settings.rerank_candidates)
                lat.append((time.perf_counter() - t0) * 1000)
        row = {
            "tokens": indexed.n_tokens,
            "chunks": len(indexed.chunks),
            "index_ms": round(index_ms, 1),
            "retrieve_p50_ms": round(statistics.median(lat), 2),
            "retrieve_p95_ms": round(pct(lat, 95), 2),
            "retrieve_max_ms": round(max(lat), 2),
            "first_question_ms": round(index_ms + statistics.median(lat), 1),
        }
        rows.append(row)
        print(
            f"{row['tokens']:>6} tokens / {row['chunks']:>3} chunks: index {row['index_ms']:>7.1f} ms | "
            f"retrieval p50 {row['retrieve_p50_ms']:.1f} ms, p95 {row['retrieve_p95_ms']:.1f} ms | "
            f"first question (index+retrieve) {row['first_question_ms']:.0f} ms"
        )
    print(f"reranker: {settings.rerank_model if reranker else 'off'}")
    print(f"saved {save_result('latency.json', rows)}")


if __name__ == "__main__":
    main()
