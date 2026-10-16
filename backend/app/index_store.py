"""Per-page FAISS indexes, cached by content hash with LRU eviction."""
import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import faiss

from .chunker import Chunk, chunk_text, count_tokens
from .embedder import Embedder


@dataclass
class PageIndex:
    page_id: str
    url: str
    title: str
    chunks: list[Chunk]
    index: faiss.Index
    n_tokens: int
    index_ms: float


@dataclass
class Hit:
    chunk: Chunk
    score: float | None  # None when chosen by position, not similarity


def page_id_for(url: str, text: str) -> str:
    return hashlib.sha256(f"{url}\n{text}".encode()).hexdigest()[:16]


class IndexStore:
    def __init__(self, embedder: Embedder, capacity: int, chunk_tokens: int, chunk_overlap: int):
        self.embedder = embedder
        self.capacity = capacity
        self.chunk_tokens = chunk_tokens
        self.chunk_overlap = chunk_overlap
        self._pages: OrderedDict[str, PageIndex] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, page_id: str) -> PageIndex | None:
        with self._lock:
            page = self._pages.get(page_id)
            if page:
                self._pages.move_to_end(page_id)
            return page

    def add(self, url: str, title: str, text: str) -> tuple[PageIndex, bool]:
        """Index a page. Returns (page, was_cached)."""
        pid = page_id_for(url, text)
        if cached := self.get(pid):
            return cached, True

        start = time.perf_counter()
        chunks = chunk_text(text, self.chunk_tokens, self.chunk_overlap)
        if not chunks:
            raise ValueError("page has no readable text")
        vecs = self.embedder.encode([self._embed_text(c, title) for c in chunks])
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        page = PageIndex(
            page_id=pid,
            url=url,
            title=title,
            chunks=chunks,
            index=index,
            n_tokens=count_tokens(text),
            index_ms=(time.perf_counter() - start) * 1000,
        )
        with self._lock:
            self._pages[pid] = page
            while len(self._pages) > self.capacity:
                self._pages.popitem(last=False)
        return page, False

    def search(self, page: PageIndex, query: str, k: int) -> list[Hit]:
        qvec = self.embedder.encode([query])
        k = min(k, len(page.chunks))
        scores, ids = page.index.search(qvec, k)
        return [
            Hit(page.chunks[i], float(s))
            for s, i in zip(scores[0], ids[0])
            if i != -1
        ]

    @staticmethod
    def _embed_text(chunk: Chunk, title: str) -> str:
        # Prefixing the heading gives short chunks enough context to be found.
        prefix = " > ".join(x for x in (title, chunk.heading) if x)
        return f"{prefix}\n{chunk.text}" if prefix else chunk.text

    def __len__(self) -> int:
        return len(self._pages)
