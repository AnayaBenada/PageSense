from fastapi.testclient import TestClient

from app.config import Settings
from app.library import Library
from app.main import create_app
from tests.conftest import FakeEmbedder, FakeLLM

DOCS = {
    "url": "https://example.com/docs#install",
    "title": "Example Docs",
    "text": (
        "# Installation\n\nInstall the package with pip install examplepkg. Python 3.10 or newer is required.\n\n"
        "# Rate limits\n\nThe free tier allows 100 requests per minute. Paid plans allow 5000 requests per minute.\n\n"
        + "\n\n".join(f"# Notes {i}\n\nRate limits note {i}: requests per minute are counted per key." for i in range(6))
    ),
}
RECIPE = {
    "url": "https://cooking.example.org/bread",
    "title": "Sourdough Bread",
    "text": "# Starter\n\nFeed the sourdough starter with flour and water every day.\n\n"
    "# Baking\n\nBake the bread at 230 degrees for 40 minutes in a dutch oven.",
}


def save(client, page):
    pid = client.post("/index", json=page).json()["page_id"]
    r = client.post("/library/pages", json={"page_id": pid})
    assert r.status_code == 200
    return r.json()


def test_save_list_and_lookup_by_url(client):
    saved = save(client, DOCS)
    assert saved["url"] == "https://example.com/docs"  # fragment dropped
    assert saved["title"] == "Example Docs" and saved["n_chunks"] > 1
    save(client, RECIPE)

    pages = client.get("/library/pages").json()["pages"]
    assert [p["title"] for p in pages] == ["Sourdough Bread", "Example Docs"]  # newest first
    assert [p["title"] for p in client.get("/library/pages", params={"q": "sourdough"}).json()["pages"]] == ["Sourdough Bread"]
    found = client.get("/library/pages", params={"url": "https://example.com/docs#other"}).json()["pages"]
    assert [p["id"] for p in found] == [saved["id"]]
    assert client.get("/health").json()["library_pages"] == 2


def test_resaving_a_url_replaces_it(client):
    first = save(client, DOCS)
    second = save(client, {**DOCS, "text": DOCS["text"] + "\n\n# Changelog\n\nVersion 2 adds webhooks."})
    assert second["page_id"] != first["page_id"]
    pages = client.get("/library/pages").json()["pages"]
    assert [p["id"] for p in pages] == [second["id"]]
    # The old version's chunks are gone from search too.
    results = client.post("/library/search", json={"query": "webhooks changelog version"}).json()["results"]
    assert all(r["page"]["id"] == second["id"] for r in results)


def test_save_unindexed_page(client):
    r = client.post("/library/pages", json={"page_id": "nope"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "page_not_indexed"


def test_search_across_pages(client):
    save(client, DOCS)
    save(client, RECIPE)
    r = client.post("/library/search", json={"query": "what temperature to bake the sourdough bread"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["page"]["title"] == "Sourdough Bread"
    assert "230 degrees" in results[0]["text"]
    r = client.post("/library/search", json={"query": "free tier requests per minute rate limits"})
    assert r.json()["results"][0]["page"]["title"] == "Example Docs"


def test_search_empty_library_and_empty_query(client):
    assert client.post("/library/search", json={"query": "anything"}).json()["results"] == []
    assert client.post("/library/search", json={"query": "  "}).status_code == 422


def test_delete(client):
    saved = save(client, DOCS)
    assert client.delete(f"/library/pages/{saved['id']}").status_code == 200
    assert client.get("/library/pages").json()["pages"] == []
    assert client.post("/library/search", json={"query": "rate limits"}).json()["results"] == []
    assert client.delete(f"/library/pages/{saved['id']}").status_code == 404


def test_library_persists_across_restarts(tmp_path):
    cfg = Settings(chunk_tokens=60, chunk_overlap=10, library_path=str(tmp_path / "lib.db"))
    with TestClient(create_app(cfg, embedder=FakeEmbedder(), llm=FakeLLM())) as c:
        save(c, RECIPE)
    with TestClient(create_app(cfg, embedder=FakeEmbedder(), llm=FakeLLM())) as c:
        assert [p["title"] for p in c.get("/library/pages").json()["pages"]] == ["Sourdough Bread"]
        results = c.post("/library/search", json={"query": "bake bread degrees"}).json()["results"]
        assert "230 degrees" in results[0]["text"]


def test_changed_embed_model_reembeds(tmp_path):
    class SmallEmbedder(FakeEmbedder):
        dim = 64

    cfg = Settings(chunk_tokens=60, chunk_overlap=10, library_path=str(tmp_path / "lib.db"))
    with TestClient(create_app(cfg, embedder=FakeEmbedder(), llm=FakeLLM())) as c:
        save(c, RECIPE)
    lib = Library(str(tmp_path / "lib.db"), SmallEmbedder(), "small-model")
    assert lib.index.d == 64 and lib.index.ntotal == 1
    assert "230 degrees" in lib.search("bake bread degrees", 1, 0.0)[0].chunk.text
    lib.close()
