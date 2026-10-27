"""Saved pages ("My library"): persisted in SQLite, searchable across pages.

Chunks and their embeddings live in SQLite; one FAISS index over every saved
chunk is rebuilt from it at startup. Saving reuses the embeddings computed when
the page was indexed, so it costs no model calls. If the embedding model changes,
stored chunks are re-embedded on the next start.
"""
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

from .chunker import Chunk
from .embedder import Embedder, Reranker
from .index_store import IndexStore, PageIndex

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    page_id TEXT NOT NULL,  -- IndexStore page_id of the saved version
    saved_at REAL NOT NULL,
    n_chunks INTEGER NOT NULL,
    n_tokens INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    saved_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_saved_id ON chunks(saved_id);
"""
_PAGE_COLS = "p.id, p.url, p.title, p.page_id, p.saved_at, p.n_chunks, p.n_tokens"


def normalize_url(url: str) -> str:
    return url.split("#")[0]


@dataclass
class SavedPage:
    id: int
    url: str
    title: str
    page_id: str
    saved_at: float
    n_chunks: int
    n_tokens: int


@dataclass
class LibraryHit:
    page: SavedPage
    chunk: Chunk
    score: float


class Library:
    def __init__(self, path: str, embedder: Embedder, embed_model: str):
        self.embedder = embedder
        self.embed_model = embed_model
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path = str(Path(path).expanduser())
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)
        self.index = faiss.IndexIDMap2(faiss.IndexFlatIP(embedder.dim))
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        rows = self.db.execute(
            "SELECT c.id, c.heading, c.text, c.embedding, p.title FROM chunks c JOIN pages p ON p.id = c.saved_id"
        ).fetchall()
        stored = self.db.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone()
        if rows and (
            stored is None or stored[0] != self.embed_model or len(rows[0][3]) != self.embedder.dim * 4
        ):
            texts = [IndexStore.embed_text(Chunk(0, text, heading, 0), title) for _, heading, text, _, title in rows]
            vecs = self.embedder.encode(texts)
            with self.db:
                self.db.executemany(
                    "UPDATE chunks SET embedding = ? WHERE id = ?", [(v.tobytes(), r[0]) for v, r in zip(vecs, rows)]
                )
        elif rows:
            vecs = np.frombuffer(b"".join(r[3] for r in rows), dtype=np.float32).reshape(len(rows), -1)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('embed_model', ?)", (self.embed_model,))
        if rows:
            self.index.add_with_ids(vecs, np.array([r[0] for r in rows], dtype=np.int64))

    def save(self, page: PageIndex) -> SavedPage:
        """Save a page, replacing any earlier version saved from the same URL."""
        vecs = page.index.reconstruct_n(0, page.index.ntotal)
        url = normalize_url(page.url)
        with self._lock, self.db:
            if old := self.db.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone():
                self._delete(old[0])
            saved_id = self.db.execute(
                "INSERT INTO pages (url, title, page_id, saved_at, n_chunks, n_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                (url, page.title, page.page_id, time.time(), len(page.chunks), page.n_tokens),
            ).lastrowid
            ids = [
                self.db.execute(
                    "INSERT INTO chunks (saved_id, idx, heading, text, embedding) VALUES (?, ?, ?, ?, ?)",
                    (saved_id, c.id, c.heading, c.text, v.tobytes()),
                ).lastrowid
                for c, v in zip(page.chunks, vecs)
            ]
            self.index.add_with_ids(vecs, np.array(ids, dtype=np.int64))
        return self.get(saved_id)

    def delete(self, saved_id: int) -> bool:
        with self._lock, self.db:
            return self._delete(saved_id)

    def _delete(self, saved_id: int) -> bool:
        ids = [r[0] for r in self.db.execute("SELECT id FROM chunks WHERE saved_id = ?", (saved_id,))]
        if ids:
            self.index.remove_ids(np.array(ids, dtype=np.int64))
        return self.db.execute("DELETE FROM pages WHERE id = ?", (saved_id,)).rowcount > 0

    def get(self, saved_id: int) -> SavedPage | None:
        with self._lock:
            row = self.db.execute(f"SELECT {_PAGE_COLS} FROM pages p WHERE p.id = ?", (saved_id,)).fetchone()
        return SavedPage(*row) if row else None

    def pages(self, q: str = "", url: str = "") -> list[SavedPage]:
        """Saved pages, newest first; `q` filters by title or URL, `url` finds one page."""
        sql, args = f"SELECT {_PAGE_COLS} FROM pages p", []
        if url:
            sql += " WHERE p.url = ?"
            args.append(normalize_url(url))
        elif q:
            sql += " WHERE p.title LIKE ? OR p.url LIKE ?"
            args += [f"%{q}%"] * 2
        with self._lock:
            return [SavedPage(*r) for r in self.db.execute(sql + " ORDER BY p.saved_at DESC", args)]

    def search(
        self,
        query: str,
        top_k: int,
        min_score: float,
        reranker: Reranker | None = None,
        rerank_candidates: int = 20,
    ) -> list[LibraryHit]:
        """Best passages across all saved pages, most relevant first. Same steps as
        rag.retrieve: FAISS candidates above min_score (keeping at least the best),
        then rerank. Results aren't capped per page: on the eval pages a cap of 2
        lowered passage hit@5 from 98.0% to 91.8%."""
        qvec = self.embedder.encode([query])
        with self._lock:
            n = min(self.index.ntotal, max(top_k, rerank_candidates) if reranker else top_k)
            if not n:
                return []
            scores, ids = self.index.search(qvec, n)
            cands = [(int(i), float(s)) for s, i in zip(scores[0], ids[0]) if i != -1]
            cands = [c for c in cands if c[1] >= min_score] or cands[:1]
            marks = ",".join("?" * len(cands))
            rows = {
                r[0]: r
                for r in self.db.execute(
                    f"SELECT c.id, c.idx, c.text, c.heading, {_PAGE_COLS} FROM chunks c JOIN pages p ON p.id = c.saved_id WHERE c.id IN ({marks})",
                    [i for i, _ in cands],
                )
            }
        hits = [
            LibraryHit(SavedPage(*rows[i][4:]), Chunk(rows[i][1], rows[i][2], rows[i][3], 0), s)
            for i, s in cands
            if i in rows
        ]
        if reranker and len(hits) > 1:
            scores = reranker.score(query, [h.chunk.text for h in hits])
            hits = [h for _, h in sorted(zip(scores, hits), key=lambda x: -x[0])]
        return hits[:top_k]

    def close(self) -> None:
        self.db.close()

    def __len__(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
