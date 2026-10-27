"""Smart Find accuracy: does the top sentence it jumps to contain the evidence?

For each answerable question, Smart Find returns sentences; a match is a hit
if what it highlights (the sentence, plus the line after it when the sentence
introduces one) contains an evidence quote or is part of one. "Visible" is looser:
the evidence is within 2 lines of the top match, so it's on screen after the jump.
Unanswerable questions show how the best score compares when the page has no answer.

Run: python -m eval.eval_find [--dataset dataset_holdout] [--sweep]
"""
import argparse
import statistics
import time

from app.config import settings
from app.find import find, page_pieces

from .common import load_dataset, load_pages, make_reranker, make_store, norm, save_result


def is_hit(match, evidence: list[str]) -> bool:
    for text in filter(None, (match.text, match.then)):
        s = norm(text)
        if any(norm(e) in s or s in norm(e) for e in evidence):
            return True
    return False


def is_visible(match, page, evidence: list[str]) -> bool:
    pieces = [p.text for p in page_pieces(page.text)]
    i = pieces.index(match.text) if match.text in pieces else 0
    return any(is_hit(type(match)(t, "", "", 0, 0), evidence) for t in pieces[max(0, i - 2) : i + 3])


def evaluate(store, indexed, reranker, n_chunks: int, rerank_chunks: bool, dataset: str = "dataset") -> dict:
    hits = {1: 0, 3: 0, 5: 0}
    visible = 0
    answerable_top, unanswerable_top = [], []
    total_ms = 0.0
    questions = load_dataset(dataset)
    for q in questions:
        start = time.perf_counter()
        matches = find(
            store, indexed[q["page"]], q["question"], 5, n_chunks, settings.min_score, reranker,
            rerank_chunks, settings.rerank_candidates, settings.hybrid_search,
        )
        total_ms += (time.perf_counter() - start) * 1000
        if not q["evidence"]:
            unanswerable_top.append(matches[0].score if matches else float("-inf"))
            continue
        answerable_top.append(matches[0].score if matches else float("-inf"))
        rank = next((i for i, m in enumerate(matches, 1) if is_hit(m, q["evidence"])), None)
        visible += bool(matches) and (rank == 1 or is_visible(matches[0], indexed[q["page"]], q["evidence"]))
        for k in hits:
            hits[k] += rank is not None and rank <= k
    n = len(answerable_top)
    return {
        "dataset": dataset,
        "n_chunks": n_chunks,
        "rerank_chunks": rerank_chunks,
        "reranker": settings.rerank_model if reranker else None,
        "n_questions": n,
        **{f"hit@{k}": round(v / n, 4) for k, v in hits.items()},
        "visible@1": round(visible / n, 4),
        "median_top_score_answerable": round(statistics.median(answerable_top), 3),
        "max_top_score_unanswerable": round(max(unanswerable_top), 3) if unanswerable_top else None,
        "avg_query_ms": round(total_ms / len(questions), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=settings.find_chunks, help="chunks to split into sentences")
    ap.add_argument("--sweep", action="store_true", help="try several chunk counts, with and without chunk reranking")
    ap.add_argument("--no-rerank", action="store_true", help="embedding similarity only")
    ap.add_argument("--dataset", default="dataset", help="dataset, dataset_holdout or dataset_holdout2")
    args = ap.parse_args()
    reranker = None if args.no_rerank else make_reranker()
    store = make_store(settings.chunk_tokens, settings.chunk_overlap)
    indexed = {name: store.add(p["url"], p["title"], p["text"])[0] for name, p in load_pages().items()}

    configs = [(c, rc) for c in (2, 3, 5) for rc in (True, False)] if args.sweep else [(args.chunks, settings.find_rerank_chunks)]
    results = []
    for n_chunks, rerank_chunks in configs:
        r = evaluate(store, indexed, reranker, n_chunks, rerank_chunks and reranker is not None, args.dataset)
        results.append(r)
        print(
            f"chunks={n_chunks} chunk-rerank={'on ' if r['rerank_chunks'] else 'off'}:  hit@1={r['hit@1']:.1%}  visible@1={r['visible@1']:.1%}  hit@3={r['hit@3']:.1%}  "
            f"hit@5={r['hit@5']:.1%}  top score: answerable median {r['median_top_score_answerable']}, "
            f"unanswerable max {r['max_top_score_unanswerable']}  ({r['avg_query_ms']} ms/query)"
        )
    suffix = ("" if reranker else "_norerank") + ("" if args.dataset == "dataset" else "_" + args.dataset.removeprefix("dataset_"))
    print(f"saved {save_result(f'find_sweep{suffix}.json' if args.sweep else f'find{suffix}.json', results if args.sweep else results[0])}")


if __name__ == "__main__":
    main()
