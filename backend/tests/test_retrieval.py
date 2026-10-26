from app.index_store import IndexStore
from app.lexical import BM25, tokenize
from app.rag import add_neighbors, retrieve
from tests.conftest import FakeEmbedder


def test_tokenize_drops_stopwords_and_punctuation():
    assert tokenize("How can I clone() the Request?") == ["clone", "request"]


def test_bm25_ranks_exact_keyword_first():
    docs = ["send a request with fetch", "use request.clone() to copy a request", "headers and bodies"]
    scores = BM25(docs).scores("clone")
    assert scores.index(max(scores)) == 1
    assert scores[0] == 0 and scores[2] == 0


def _page(paragraphs):
    store = IndexStore(FakeEmbedder(), capacity=5, chunk_tokens=20, chunk_overlap=0)
    page, _ = store.add("https://example.com", "Example", "\n\n".join(paragraphs))
    return store, page


class BlindEmbedder:
    """Same vector for every text, so embedding search can't tell chunks apart."""

    dim = 4

    def encode(self, texts):
        import numpy as np

        return np.tile(np.array([[1, 0, 0, 0]], dtype=np.float32), (len(texts), 1))


def test_hybrid_search_finds_keyword_only_match():
    paras = [f"Filler paragraph {i} about gardening and soil." for i in range(30)]
    paras[17] = "Orchids have zygomorphic flowers."
    store = IndexStore(BlindEmbedder(), capacity=5, chunk_tokens=20, chunk_overlap=0)
    page, _ = store.add("https://example.com", "Example", "\n\n".join(paras))
    q = "Which flowers are zygomorphic?"
    dense_only = retrieve(store, page, q, top_k=2, min_score=0.0, hybrid=False)
    hybrid = retrieve(store, page, q, top_k=2, min_score=0.0, hybrid=True)
    assert not any("zygomorphic" in h.chunk.text for h in dense_only)
    assert any("zygomorphic" in h.chunk.text for h in hybrid)


def test_add_neighbors_adds_adjacent_chunks_of_best_hit_only():
    store, page = _page([f"Paragraph {i} " + "word " * 12 for i in range(10)])
    hits = retrieve(store, page, "Paragraph 5", top_k=2, min_score=0.0, hybrid=False)
    best = hits[0].chunk.id
    out = add_neighbors(page, hits, n_best=1)
    ids = [h.chunk.id for h in out]
    assert ids[: len(hits)] == [h.chunk.id for h in hits]
    assert {best - 1, best + 1} & set(range(len(page.chunks))) <= set(ids)
    assert len(ids) == len(set(ids))  # no duplicates
    assert add_neighbors(page, hits, n_best=0) == hits
