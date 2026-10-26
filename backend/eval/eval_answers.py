"""End-to-end answers from the running backend, for manual grading.

1. Run (backend must be up):     python -m eval.eval_answers
   → eval/results/answers.csv  (one row per question, `grade` column left blank)
2. Grade each answerable row in a spreadsheet:
     2 = correct and relevant, 1 = partially correct / incomplete, 0 = wrong or irrelevant
3. Summarize:                    python -m eval.eval_answers --summarize eval/results/answers.csv

Unanswerable questions (no gold answer) are scored automatically: the model must
say it couldn't find the answer on the page.
"""
import argparse
import csv
import re
import statistics
import sys
import time

import httpx

from .common import RESULTS_DIR, load_dataset, load_pages

REFUSAL = re.compile(r"couldn.?t find|could not find|not (mentioned|covered|found)|doesn.?t (mention|contain|cover)", re.I)
FIELDS = [
    "id", "page", "question", "reference_answer", "answer", "answerable", "refused", "citations_valid",
    "n_sources", "retrieve_ms", "generate_ms", "grade", "notes",
]


def run(base_url: str, out_path, dataset: str = "dataset") -> None:
    pages = load_pages()
    data = load_dataset(dataset)
    client = httpx.Client(base_url=base_url, timeout=300)
    health = client.get("/health").json()
    print(f"backend: {health['status']} · LLM {health['llm_model']} · reranker {health.get('rerank_model')}")

    page_ids = {name: client.post("/index", json=p).json()["page_id"] for name, p in pages.items()}
    rows = []
    t_start = time.perf_counter()
    for i, q in enumerate(data, 1):
        r = client.post("/ask", json={"page_id": page_ids[q["page"]], "question": q["question"]})
        r.raise_for_status()
        body = r.json()
        answer = body["answer"]
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", answer)]
        rows.append({
            "id": i,
            "page": q["page"],
            "question": q["question"],
            "reference_answer": q["answer"] or "(not on page)",
            "answer": answer,
            "answerable": bool(q["evidence"]),
            "refused": bool(REFUSAL.search(answer)),
            "citations_valid": all(1 <= n <= len(body["sources"]) for n in cited) if cited else "",
            "n_sources": len(body["sources"]),
            "retrieve_ms": body["timings"]["retrieve_ms"],
            "generate_ms": body["timings"]["generate_ms"],
            "grade": "",
            "notes": "",
        })
        print(f"[{i}/{len(data)}] {body['timings']['generate_ms'] / 1000:.1f}s  {q['question'][:60]}", flush=True)

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} answers in {time.perf_counter() - t_start:.0f}s → {out_path}")
    summarize(out_path)


def summarize(path) -> None:
    rows = list(csv.DictReader(open(path)))
    ans = [r for r in rows if r["answerable"] == "True"]
    unans = [r for r in rows if r["answerable"] != "True"]

    print(f"\nQuestions: {len(rows)} ({len(ans)} answerable, {len(unans)} not on the page)")
    if unans:
        ok = sum(r["refused"] == "True" for r in unans)
        print(f"Correctly declined unanswerable questions: {ok}/{len(unans)} ({ok / len(unans):.0%})")
    false_ref = sum(r["refused"] == "True" for r in ans)
    print(f"Answerable questions wrongly declined:     {false_ref}/{len(ans)} ({false_ref / len(ans):.0%})")
    with_cites = [r for r in rows if r["citations_valid"] != ""]
    print(f"Answers with citations: {len(with_cites)}/{len(rows)}; all citation numbers valid in "
          f"{sum(r['citations_valid'] == 'True' for r in with_cites)}/{len(with_cites)}")

    gen = [float(r["generate_ms"]) / 1000 for r in rows]
    ret = [float(r["retrieve_ms"]) for r in rows]
    print(f"Retrieval: median {statistics.median(ret):.0f} ms · Generation: median {statistics.median(gen):.1f}s, max {max(gen):.1f}s")

    graded = [r for r in ans if r["grade"].strip()]
    if not graded:
        print("\nNo grades yet: fill the `grade` column (2/1/0) for answerable rows, then re-run with --summarize.")
        return
    grades = [int(r["grade"]) for r in graded]
    print(f"\nManually graded: {len(graded)}/{len(ans)} answerable questions")
    print(f"  Fully correct & relevant (2):     {grades.count(2)}/{len(grades)} ({grades.count(2) / len(grades):.1%})")
    print(f"  Relevant, at least partially (≥1): {sum(g >= 1 for g in grades)}/{len(grades)} ({sum(g >= 1 for g in grades) / len(grades):.1%})")
    print(f"  Mean score: {statistics.mean(grades) / 2:.1%} of maximum")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--summarize", metavar="CSV")
    ap.add_argument("--dataset", default="dataset", help="dataset, dataset_holdout or dataset_holdout2")
    ap.add_argument("--out", help="CSV path (default eval/results/answers[_holdout|_holdout2].csv)")
    args = ap.parse_args()
    if args.summarize:
        summarize(args.summarize)
        return
    RESULTS_DIR.mkdir(exist_ok=True)
    try:
        default = "answers.csv" if args.dataset == "dataset" else f"answers_{args.dataset.removeprefix('dataset_')}.csv"
        run(args.base_url, args.out or RESULTS_DIR / default, args.dataset)
    except httpx.ConnectError:
        sys.exit(f"Backend not reachable at {args.base_url}. Start it: uvicorn app.main:app --port 8000")


if __name__ == "__main__":
    main()
