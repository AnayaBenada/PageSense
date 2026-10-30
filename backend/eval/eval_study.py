"""Student mode quality: quiz questions and study notes on the eval pages.

Quiz checks, per question:
- usable:      the model returned a valid 4-choice question (after one retry)
- key agrees:  the cross-encoder, reading the evidence sentence the panel will
               highlight, prefers the marked answer over all 3 distractors. A wrong
               answer key or a distractor that's also right usually fails this.
Questions are also written to the results file for reading by hand.

Notes: time, number of sections/bullets, and the share of bullets whose content is
found on the page (best cross-encoder score over the page's sentences above 0).

Run: python -m eval.eval_study [--pages a,b] [--n 5] [--notes]
"""
import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PAGESENSE_LIBRARY_PATH", os.path.join(tempfile.mkdtemp(), "library.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app.find import page_pieces  # noqa: E402
from app.main import create_app  # noqa: E402

HERE = Path(__file__).parent
PAGES = ["fastapi_first_steps", "git_branching_merging", "mdn_using_fetch", "python_datastructures", "react_useeffect", "wiki_transformer"]


def sse_events(text: str):
    for block in text.split("\n\n"):
        event, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            yield event, json.loads(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default=",".join(PAGES))
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--round", type=int, default=0)
    ap.add_argument("--notes", action="store_true", help="also evaluate study notes")
    args = ap.parse_args()

    out = {"quiz": [], "notes": []}
    with TestClient(create_app()) as c:
        reranker = c.app.state.reranker
        totals = {"asked": 0, "usable": 0, "agree": 0, "s": 0.0}
        for name in args.pages.split(","):
            page = json.loads((HERE / "pages" / f"{name}.json").read_text())
            ref = {"url": page["url"], "title": page["title"], "text": page["text"]}
            start = time.perf_counter()
            r = c.post("/study/quiz", json={**ref, "n": args.n, "round": args.round})
            secs = time.perf_counter() - start
            qs = [d for e, d in sse_events(r.text) if e == "question"]
            errors = [d for e, d in sse_events(r.text) if e == "error"]
            agree = 0
            for q in qs:
                scores = reranker.score(q["evidence"], [f"{q['question']} {ch}" for ch in q["choices"]])
                q["key_agrees"] = max(range(4), key=lambda i: scores[i]) == q["answer"]
                agree += q["key_agrees"]
            totals["asked"] += args.n
            totals["usable"] += len(qs)
            totals["agree"] += agree
            totals["s"] += secs
            print(f"{name:24} quiz: {len(qs)}/{args.n} usable, key agrees {agree}/{len(qs)}, {secs:.1f}s ({secs / max(len(qs), 1):.1f}s/question)" + (f" errors={errors}" if errors else ""))
            out["quiz"].append({"page": name, "seconds": round(secs, 1), "questions": qs})

            if args.notes:
                start = time.perf_counter()
                r = c.post("/study/notes", json=ref)
                secs = time.perf_counter() - start
                notes = "".join(d["t"] for e, d in sse_events(r.text) if e == "token")
                partial = next(d["partial"] for e, d in sse_events(r.text) if e == "meta")
                bullets = [re.sub(r"\*\*|`", "", b).strip() for b in re.findall(r"^\s*[-*] (.+)$", notes, re.M)]
                sentences = [p.text for p in page_pieces(page["text"]) if not p.is_heading]
                found = sum(max(reranker.score(b, sentences)) > 0 for b in bullets)
                sections = len(re.findall(r"^#{2,4} ", notes, re.M))
                print(f"{'':24} notes: {sections} sections, {len(bullets)} bullets, {found}/{len(bullets)} found on page, {secs:.1f}s, partial={partial}")
                out["notes"].append({"page": name, "seconds": round(secs, 1), "partial": partial, "sections": sections,
                                     "bullets": len(bullets), "found": found, "notes": notes})

        n = max(totals["usable"], 1)
        print(f"\nquiz total: {totals['usable']}/{totals['asked']} usable ({totals['usable'] / max(totals['asked'], 1):.0%}), "
              f"key agrees {totals['agree']}/{totals['usable']} ({totals['agree'] / n:.0%}), {totals['s'] / n:.1f}s/question")
        if out["notes"]:
            b = sum(x["bullets"] for x in out["notes"])
            f = sum(x["found"] for x in out["notes"])
            print(f"notes total: {f}/{b} bullets found on page ({f / max(b, 1):.0%}), "
                  f"{sum(x['seconds'] for x in out['notes']) / len(out['notes']):.1f}s/page")
    path = HERE / "results" / "study.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
