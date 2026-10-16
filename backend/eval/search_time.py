"""Search-time study: how much faster is PageSense than finding answers by hand?

This needs real people, so it can't be automated. Protocol:

1. Pick 10 questions from eval/dataset.jsonl (answerable ones, mixed pages).
2. For each participant (aim for 3+; never yourself on questions you wrote):
   - Split the 10 questions randomly into two halves, A and B.
   - Half A, MANUAL: open the page, start a timer, find the answer using any method
     (Ctrl+F, skimming). Stop when they can state the answer.
   - Half B, PAGESENSE: open the page with the side panel, start a timer, type the
     question, stop when the answer appears and they've confirmed it's right.
   - Swap halves for the next participant so every question gets both conditions.
   - Record a row per attempt. Mark found=no if they gave up or answered wrong.
3. Save as eval/results/search_time.csv with header:
       participant,question_id,method,seconds,found
   method is "manual" or "pagesense"; found is "yes"/"no".
4. python -m eval.search_time eval/results/search_time.csv
"""
import csv
import statistics
import sys


def main(path: str) -> None:
    rows = list(csv.DictReader(open(path)))
    by = {m: [float(r["seconds"]) for r in rows if r["method"] == m and r["found"].lower() == "yes"] for m in ("manual", "pagesense")}
    for m, times in by.items():
        attempts = sum(r["method"] == m for r in rows)
        print(f"{m:>9}: {len(times)}/{attempts} found · median {statistics.median(times):.1f}s · mean {statistics.mean(times):.1f}s")
    reduction = 1 - statistics.median(by["pagesense"]) / statistics.median(by["manual"])
    print(f"\nMedian time reduction with PageSense: {reduction:.0%}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
