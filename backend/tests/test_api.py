import json

from tests.conftest import FakeLLM

PAGE = {
    "url": "https://example.com/docs",
    "title": "Example Docs",
    "text": (
        "# Installation\n\nInstall the package with pip install examplepkg. Python 3.10 or newer is required.\n\n"
        "# Configuration\n\nSet the EXAMPLE_TOKEN environment variable to authenticate requests.\n\n"
        "# Rate limits\n\nThe free tier allows 100 requests per minute. Paid plans allow 5000 requests per minute.\n\n"
        + "\n\n".join(f"# Appendix {i}\n\nUnrelated filler paragraph number {i} about gardening." for i in range(20))
    ),
}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_and_cache(client):
    r1 = client.post("/index", json=PAGE).json()
    assert r1["n_chunks"] > 1 and r1["cached"] is False
    r2 = client.post("/index", json=PAGE).json()
    assert r2["page_id"] == r1["page_id"] and r2["cached"] is True


def test_index_empty_page(client):
    r = client.post("/index", json={**PAGE, "text": "  \n "})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_page"


def test_index_too_large(client):
    r = client.post("/index", json={**PAGE, "text": "x " * 30_000})
    assert r.status_code == 413


def test_ask_retrieves_relevant_chunk(client, fake_llm):
    pid = client.post("/index", json=PAGE).json()["page_id"]
    r = client.post("/ask", json={"page_id": pid, "question": "How many requests per minute on the free tier rate limits?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "The answer [1]"
    assert any("100 requests per minute" in s["text"] for s in body["sources"])
    ids = [s["chunk_id"] for s in body["sources"]]
    assert ids == sorted(ids)  # page order == citation order
    assert body["timings"]["retrieve_ms"] >= 0
    # The prompt sent to the LLM contains the retrieved excerpt and the question.
    prompt = fake_llm.last_messages[-1]["content"]
    assert "100 requests per minute" in prompt and "free tier" in prompt


def test_ask_unknown_page(client):
    r = client.post("/ask", json={"page_id": "nope", "question": "hi"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "page_not_indexed"


def test_ask_inline_text(client):
    r = client.post("/ask", json={**PAGE, "question": "What environment variable is needed?"})
    assert r.status_code == 200
    assert any("EXAMPLE_TOKEN" in s["text"] for s in r.json()["sources"])


def test_ask_empty_question(client):
    pid = client.post("/index", json=PAGE).json()["page_id"]
    r = client.post("/ask", json={"page_id": pid, "question": "   "})
    assert r.status_code == 422


def test_history_is_included(client, fake_llm):
    pid = client.post("/index", json=PAGE).json()["page_id"]
    history = [{"question": "How do I install it?", "answer": "pip install examplepkg"}]
    client.post("/ask", json={"page_id": pid, "question": "Which Python version?", "history": history})
    roles = [m["role"] for m in fake_llm.last_messages]
    assert roles == ["system", "user", "assistant", "user"]


def test_lru_eviction(client):
    ids = [client.post("/index", json={**PAGE, "url": f"https://e.com/{i}"}).json()["page_id"] for i in range(4)]
    r = client.post("/ask", json={"page_id": ids[0], "question": "install?"})
    assert r.status_code == 404  # capacity is 3, first page evicted


def test_stream(client):
    pid = client.post("/index", json=PAGE).json()["page_id"]
    with client.stream("POST", "/ask/stream", json={"page_id": pid, "question": "rate limits?"}) as r:
        raw = "".join(r.iter_text())
    events = [
        (block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
        for block in raw.strip().split("\n\n")
    ]
    assert events[0][0] == "meta" and events[0][1]["sources"]
    assert "".join(d["t"] for e, d in events if e == "token") == "The answer [1]"
    assert events[-1][0] == "done" and "generate_ms" in events[-1][1]["timings"]


def test_llm_failure(client):
    client.app.state.llm = FakeLLM(fail=True)
    pid = client.post("/index", json=PAGE).json()["page_id"]
    r = client.post("/ask", json={"page_id": pid, "question": "install?"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "llm_unavailable"


def test_reranker_reorders_and_sources_stay_in_page_order(fake_llm):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from tests.conftest import FakeEmbedder

    class PreferGardening:
        def score(self, query, texts):
            return [1.0 if "gardening" in t else 0.0 for t in texts]

    cfg = Settings(chunk_tokens=60, chunk_overlap=10, top_k=2, min_score=0.0, rerank_candidates=10, neighbor_hits=0)
    app = create_app(cfg, embedder=FakeEmbedder(), llm=fake_llm, reranker=PreferGardening())
    with TestClient(app) as c:
        pid = c.post("/index", json=PAGE).json()["page_id"]
        body = c.post("/ask", json={"page_id": pid, "question": "rate limits per minute"}).json()
    assert all("gardening" in s["text"] for s in body["sources"])
    ids = [s["chunk_id"] for s in body["sources"]]
    assert ids == sorted(ids)


def test_ask_summary_uses_whole_page(client, fake_llm):
    pid = client.post("/index", json=PAGE).json()["page_id"]
    body = client.post("/ask", json={"page_id": pid, "question": "Summarize this page"}).json()
    ids = [s["chunk_id"] for s in body["sources"]]
    assert ids[0] == 0 and len(ids) == 3 and ids == sorted(ids) and ids[-1] > ids[1]
    assert all(s["score"] is None for s in body["sources"])
    assert "couldn't find" not in fake_llm.last_messages[-1]["content"]
