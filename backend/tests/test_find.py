from fastapi.testclient import TestClient

from app.config import Settings
from app.find import split_sentences
from app.main import create_app
from tests.conftest import FakeEmbedder, FakeLLM

PAGE = {
    "url": "https://shop.example.com/terms",
    "title": "Terms of Service",
    "text": (
        "# Orders\n\nOrders ship within two business days. Tracking numbers are emailed once the parcel leaves the warehouse.\n\n"
        "# Refunds\n\nYou can return any item within 30 days for a full refund. Refunds go back to the original payment method.\n\n"
        "# Cancelling\n\nTo cancel your subscription, run this command:\n\n```\n$ shopctl subscription cancel --now\n```\n\n"
        + "\n\n".join(f"# Section {i}\n\nGeneral filler text number {i} about gardening and weather." for i in range(10))
    ),
}


def test_split_sentences():
    pieces = split_sentences(
        "# Refunds\n\nYou can return any item within 30 days. Refunds go back to the card.\n"
        "- Gift cards are final sale.\n[image: logo] Short.\n```\n$ shopctl cancel --now\n```\nTo cancel, run:"
    )
    assert [p.text for p in pieces] == [
        "Refunds",
        "You can return any item within 30 days.",
        "Refunds go back to the card.",
        "Gift cards are final sale.",
        "$ shopctl cancel --now",
        "To cancel, run:",
    ]
    assert [p.leads_in for p in pieces] == [True, False, False, False, False, True]


def test_find_returns_best_sentence(client):
    r = client.post("/find", json={**PAGE, "query": "return item full refund days"})
    assert r.status_code == 200
    body = r.json()
    assert body["matches"][0]["text"] == "You can return any item within 30 days for a full refund."
    assert body["matches"][0]["heading"] == "Refunds"
    assert len(body["matches"]) <= 5 and body["find_ms"] >= 0


def test_find_includes_the_line_a_lead_in_introduces(client):
    pid = client.post("/index", json=PAGE).json()["page_id"]
    matches = client.post("/find", json={"page_id": pid, "query": "cancel your subscription command"}).json()["matches"]
    m = next(m for m in matches if m["text"] == "To cancel your subscription, run this command:")
    assert m["then"] == "$ shopctl subscription cancel --now"


def test_find_unknown_page_and_empty_query(client):
    assert client.post("/find", json={"page_id": "nope", "query": "refund"}).json()["detail"]["code"] == "page_not_indexed"
    pid = client.post("/index", json=PAGE).json()["page_id"]
    assert client.post("/find", json={"page_id": pid, "query": "  "}).status_code == 422


def test_find_drops_weak_matches_when_reranking(tmp_path):
    class KeywordReranker:
        def score(self, query, texts):
            return [5.0 if "refund" in query and "refund" in t.lower() else -6.0 for t in texts]

    cfg = Settings(chunk_tokens=60, chunk_overlap=10, library_path=str(tmp_path / "lib.db"), find_min_score=-2)
    with TestClient(create_app(cfg, embedder=FakeEmbedder(), llm=FakeLLM(), reranker=KeywordReranker())) as c:
        matches = c.post("/find", json={**PAGE, "query": "refund"}).json()["matches"]
        assert matches and all("refund" in m["text"].lower() for m in matches)
        assert c.post("/find", json={**PAGE, "query": "shipping to mars"}).json()["matches"] == []
