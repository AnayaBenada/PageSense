"""Retrieval accuracy: does a retrieved chunk contain the gold evidence?

Reports hit@1, hit@3, hit@k and MRR over the answerable questions, for one or
more (chunk_tokens, overlap, top_k) settings.

    python -m eval.eval_retrieval                      # default settings
    python -m eval.eval_retrieval --sweep              # compare chunk sizes / k
"""
import argparse
import time

import app  # noqa: F401  (OpenMP env setup)
from app.config import settings
from app.rag import retrieve

from .common import contains_evidence, load_dataset, load_pages, make_reranker, make_store, save_result


def evaluate(chunk_tokens: int, overlap: int, top_k: int, reranker=None, verbose: bool = False) -> dict:
    pages = load_pages()
    questions = [q for q in load_dataset() if q["evidence"]]
    store = make_store(chunk_tokens, overlap)
    indexed = {name: store.add(p["url"], p["title"], p["text"])[0] for name, p in pages.items()}

    hits = {1: 0, 3: 0, top_k: 0}
    rr_total = 0.0
    misses = []
    start = time.perf_counter()
    for q in questions:
        page = indexed[q["page"]]
        # Same retrieval the app runs, keeping enough results to score hit@3 when k < 3.
        ranked = retrieve(store, page, q["question"], max(top_k, 3), settings.min_score, reranker, settings.rerank_candidates)
        rank = next((i for i, h in enumerate(ranked, 1) if contains_evidence(h.chunk.text, q["evidence"])), None)
        for k in hits:
            hits[k] += rank is not None and rank <= k
        rr_total += 1 / rank if rank else 0
        if not rank or rank > top_k:
            misses.append({"page": q["page"], "question": q["question"], "rank": rank})
    elapsed = time.perf_counter() - start

    n = len(questions)
    result = {
        "chunk_tokens": chunk_tokens,
        "overlap": overlap,
        "top_k": top_k,
        "reranker": settings.rerank_model if reranker else None,
        "n_questions": n,
        "hit@1": round(hits[1] / n, 4),
        "hit@3": round(hits[3] / n, 4),
        f"hit@{top_k}": round(hits[top_k] / n, 4),
        "mrr": round(rr_total / n, 4),
        "avg_query_ms": round(elapsed / n * 1000, 2),
        "misses": misses,
    }
    if verbose:
        for m in misses:
            print(f"  miss (rank={m['rank']}): [{m['page']}] {m['question']}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk-tokens", type=int, default=300)
    ap.add_argument("--overlap", type=int, default=50)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--no-rerank", action="store_true", help="FAISS ranking only")
    args = ap.parse_args()
    reranker = None if args.no_rerank else make_reranker()

    configs = (
        [(200, 40, 5), (300, 50, 3), (300, 50, 5), (300, 50, 8), (500, 80, 5)]
        if args.sweep
        else [(args.chunk_tokens, args.overlap, args.top_k)]
    )
    results = []
    for size, overlap, k in configs:
        r = evaluate(size, overlap, k, reranker, verbose=not args.sweep)
        results.append(r)
        print(
            f"rerank={'on ' if reranker else 'off'} chunk={size:>3} overlap={overlap:>2} k={k}:  hit@1={r['hit@1']:.1%}  hit@3={r['hit@3']:.1%}  "
            f"hit@{k}={r[f'hit@{k}']:.1%}  MRR={r['mrr']:.3f}  ({r['n_questions']} q, {r['avg_query_ms']} ms/query)"
        )
    suffix = "" if reranker else "_norerank"
    path = save_result(f"retrieval_sweep{suffix}.json" if args.sweep else f"retrieval{suffix}.json", results if args.sweep else results[0])
    print(f"saved {path}")


if __name__ == "__main__":
    main()
