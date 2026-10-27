"""Retrieval accuracy when searching the whole library instead of one page.

All eval pages are saved into one in-memory library and every question is asked
across it. Reports how often the right page comes first, the right page is in
the top k, and a passage containing the evidence is in the top k.

Run: python -m eval.eval_library [--dataset dataset_holdout] [--no-rerank]
"""
import argparse
import time

from app.config import settings
from app.library import Library, normalize_url

from .common import contains_evidence, load_dataset, load_pages, make_reranker, make_store, save_result


def evaluate(top_k: int, reranker=None, dataset: str = "dataset") -> dict:
    store = make_store(settings.chunk_tokens, settings.chunk_overlap)
    library = Library(":memory:", store.embedder, settings.embed_model)
    pages = load_pages()
    for p in pages.values():
        library.save(store.add(p["url"], p["title"], p["text"])[0])
    questions = [q for q in load_dataset(dataset) if q["evidence"]]

    top1 = page_hits = passage_hits = 0
    total_ms = 0.0
    for q in questions:
        url = normalize_url(pages[q["page"]]["url"])
        start = time.perf_counter()
        hits = library.search(q["question"], top_k, settings.min_score, reranker, settings.rerank_candidates)
        total_ms += (time.perf_counter() - start) * 1000
        top1 += bool(hits) and hits[0].page.url == url
        page_hits += any(h.page.url == url for h in hits)
        passage_hits += any(h.page.url == url and contains_evidence(h.chunk.text, q["evidence"]) for h in hits)

    n = len(questions)
    return {
        "dataset": dataset,
        "n_pages": len(pages),
        "top_k": top_k,
        "reranker": settings.rerank_model if reranker else None,
        "n_questions": n,
        "page@1": round(top1 / n, 4),
        f"page@{top_k}": round(page_hits / n, 4),
        f"passage@{top_k}": round(passage_hits / n, 4),
        "avg_query_ms": round(total_ms / n, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true", help="FAISS ranking only")
    ap.add_argument("--dataset", default="dataset", help="dataset, dataset_holdout or dataset_holdout2")
    args = ap.parse_args()
    reranker = None if args.no_rerank else make_reranker()

    r = evaluate(args.top_k, reranker, args.dataset)
    k = args.top_k
    print(
        f"rerank={'on ' if reranker else 'off'} k={k}:  right page first={r['page@1']:.1%}  page@{k}={r[f'page@{k}']:.1%}  "
        f"passage@{k}={r[f'passage@{k}']:.1%}  ({r['n_questions']} q over {r['n_pages']} pages, {r['avg_query_ms']} ms/query)"
    )
    suffix = ("" if reranker else "_norerank") + ("" if args.dataset == "dataset" else "_" + args.dataset.removeprefix("dataset_"))
    print(f"saved {save_result(f'library{suffix}.json', r)}")


if __name__ == "__main__":
    main()
