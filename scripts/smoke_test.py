"""Smoke-test a running PageSense backend: the happy path plus every API error case.

    backend/.venv/bin/python scripts/smoke_test.py [--base-url http://localhost:8000]

Exits non-zero if any check fails. Requires the backend (and Ollama) to be running.
"""
import argparse
import json
import sys
import time

import httpx

PAGE = {
    "url": "https://smoke.test/docs",
    "title": "Smoke Test Docs",
    "text": (
        "# Installation\n\nInstall with pip install smokepkg. Python 3.10 or newer is required.\n\n"
        "# Rate limits\n\nThe free tier allows 100 requests per minute. Paid plans allow 5000 requests per minute.\n\n"
        "# Authentication\n\nSet the SMOKE_TOKEN environment variable to authenticate."
    ),
}

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def code(r: httpx.Response) -> str:
    try:
        return r.json().get("detail", {}).get("code", "")
    except Exception:
        return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    c = httpx.Client(base_url=ap.parse_args().base_url, timeout=180)

    # --- health
    try:
        h = c.get("/health").json()
    except httpx.ConnectError:
        sys.exit("Backend not reachable. Start it with: scripts/start.sh")
    check("health: backend up", True)
    check("health: Ollama reachable", h["ollama"]["reachable"], h["ollama"].get("error", ""))
    check("health: model pulled", h["ollama"]["model_available"], h["llm_model"])
    check("health: reranker loaded", bool(h.get("rerank_model")), str(h.get("rerank_model")))

    # --- indexing
    r = c.post("/index", json=PAGE)
    check("index: valid page → 200", r.status_code == 200, r.text[:120])
    pid = r.json().get("page_id")
    r2 = c.post("/index", json=PAGE).json()
    check("index: same page again is cached", r2.get("cached") is True)
    r = c.post("/index", json={**PAGE, "text": "   \n  "})
    check("index: empty text → 422 empty_page", r.status_code == 422 and code(r) == "empty_page", f"{r.status_code} {code(r)}")
    r = c.post("/index", json={**PAGE, "text": "word " * 100_000})
    check("index: oversized page → 413 page_too_large", r.status_code == 413 and code(r) == "page_too_large", f"{r.status_code} {code(r)}")
    r = c.post("/index", json={"url": PAGE["url"]})
    check("index: missing text field → 422", r.status_code == 422, str(r.status_code))
    r = c.post("/index", content=b"not json", headers={"Content-Type": "application/json"})
    check("index: malformed JSON → 422", r.status_code == 422, str(r.status_code))

    # --- asking: errors
    r = c.post("/ask", json={"page_id": "doesnotexist", "question": "hi"})
    check("ask: unknown page_id → 404 page_not_indexed", r.status_code == 404 and code(r) == "page_not_indexed", f"{r.status_code} {code(r)}")
    r = c.post("/ask", json={"page_id": pid, "question": "   "})
    check("ask: blank question → 422 empty_question", r.status_code == 422 and code(r) == "empty_question", f"{r.status_code} {code(r)}")
    r = c.post("/ask", json={"page_id": pid, "question": "x" * 2001})
    check("ask: 2001-char question → 422 question_too_long", r.status_code == 422 and code(r) == "question_too_long", f"{r.status_code} {code(r)}")
    r = c.post("/ask", json={"page_id": pid, "question": "hi", "top_k": 99})
    check("ask: top_k out of range → 422", r.status_code == 422, str(r.status_code))

    # --- asking: answers
    t = time.perf_counter()
    r = c.post("/ask", json={"page_id": pid, "question": "How many requests per minute does the free tier allow?"})
    body = r.json()
    check("ask: answerable question → 200", r.status_code == 200, f"{time.perf_counter() - t:.1f}s")
    check("ask: answer mentions 100", "100" in body.get("answer", ""), body.get("answer", "")[:100])
    check("ask: sources returned in page order", [s["chunk_id"] for s in body["sources"]] == sorted(s["chunk_id"] for s in body["sources"]))
    check("ask: retrieval under 2 s", body["timings"]["retrieve_ms"] < 2000, f"{body['timings']['retrieve_ms']:.0f} ms")

    r = c.post("/ask", json={"page_id": pid, "question": "What is the capital of France?"})
    ans = r.json().get("answer", "")
    check("ask: off-page question is declined", "couldn" in ans.lower() or "not" in ans.lower(), ans[:100])

    hist = [{"question": "How do I install it?", "answer": "pip install smokepkg"}]
    r = c.post("/ask", json={"page_id": pid, "question": "Which Python version does it need?", "history": hist})
    check("ask: follow-up with history", r.status_code == 200 and "3.10" in r.json().get("answer", ""), r.json().get("answer", "")[:100])

    r = c.post("/ask", json={**PAGE, "url": "https://smoke.test/inline", "question": "What env var authenticates?"})
    check("ask: inline text (no page_id)", r.status_code == 200 and "SMOKE_TOKEN" in r.json().get("answer", ""), r.json().get("answer", "")[:100])

    # --- streaming
    events = []
    with c.stream("POST", "/ask/stream", json={"page_id": pid, "question": "What does a paid plan allow?"}) as r:
        for block in r.read().decode().strip().split("\n\n"):
            lines = block.split("\n")
            events.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    kinds = [e for e, _ in events]
    check("stream: meta → tokens → done", kinds[0] == "meta" and "token" in kinds and kinds[-1] == "done", f"{kinds[0]}…{kinds[-1]}, {kinds.count('token')} tokens")
    text = "".join(d["t"] for e, d in events if e == "token")
    check("stream: answer mentions 5000", "5000" in text or "5,000" in text, text[:100])

    # --- CORS (extension origin allowed, random sites not)
    r = c.options("/ask", headers={"Origin": "chrome-extension://abcdefg", "Access-Control-Request-Method": "POST"})
    check("cors: chrome-extension origin allowed", r.headers.get("access-control-allow-origin") == "chrome-extension://abcdefg")
    r = c.options("/ask", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    check("cors: other websites blocked", "access-control-allow-origin" not in r.headers)

    failed = [n for ok, n, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("Failed:\n  " + "\n  ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
