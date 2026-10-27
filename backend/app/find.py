"""Smart Find: jump to the sentences on a page that match what you mean, not
just the words you typed.

The page is split into sentences once (code lines, list items and headings are
kept whole). Chunk retrieval narrows it down to a few chunks, and every sentence
in them is scored against the query, by the cross-encoder when there is one. A heading or a
sentence ending in ":" usually introduces the answer, so the line after it is
highlighted with it.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

from .chunker import clean_text
from .embedder import Reranker
from .index_store import IndexStore, PageIndex
from .rag import retrieve

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[`])")
_IMAGE = re.compile(r"\[image: [^\]]*\]")
_HEADING = re.compile(r"^#{1,6}\s+")
MIN_SENTENCE_CHARS = 15


@dataclass
class Match:
    text: str
    then: str  # the line after `text` when `text` introduces it, else ""
    heading: str
    chunk_id: int
    score: float


@dataclass
class Piece:
    text: str
    leads_in: bool  # a heading or a sentence ending in ":"
    is_heading: bool = False
    section: str = ""  # the heading it sits under


def split_sentences(text: str) -> list[Piece]:
    """Page text as findable pieces, in order, with the extractor's Markdown
    markers removed so they read (and can be located on the page) like the original."""
    out, in_code, section = [], False, ""
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        line = line.strip()
        if in_code:
            if len(line) >= 8:
                out.append(Piece(line, line.endswith(":"), section=section))
            continue
        line = _IMAGE.sub("", line).strip()
        if _HEADING.match(line):
            section = _HEADING.sub("", line)
            if len(section) >= 3:
                out.append(Piece(section, True, is_heading=True, section=section))
            continue
        line = re.sub(r"\s*\|\s*", " ", re.sub(r"^-\s+", "", line))
        out += [Piece(s, s.endswith(":"), section=section) for s in _SENTENCE_END.split(line) if len(s) >= MIN_SENTENCE_CHARS]
    return out


@lru_cache(maxsize=32)
def page_pieces(text: str) -> tuple[Piece, ...]:
    # Split from the whole page, not from chunks: chunks start with overlap that
    # can begin mid-sentence or mid-code-block, and have the heading markers removed.
    return tuple(split_sentences(clean_text(text)))


def find(
    store: IndexStore,
    page: PageIndex,
    query: str,
    top_k: int,
    n_chunks: int,
    min_score: float,
    reranker: Reranker | None = None,
    rerank_chunks: bool = True,
    rerank_candidates: int = 20,
    hybrid: bool = False,
) -> list[Match]:
    """The top_k sentences that best match the query, best first."""
    hits = retrieve(
        store, page, query, n_chunks, min_score, reranker if rerank_chunks else None, rerank_candidates, hybrid
    )
    pieces = page_pieces(page.text)
    flat = [" ".join(p.text.split()) for p in pieces]
    cands, seen = [], set()
    for h in hits:
        chunk = " ".join(h.chunk.text.split())
        lines = {line.strip() for line in h.chunk.text.split("\n")}
        for i, p in enumerate(pieces):
            # Headings must be a whole line, or a short one would match inside any sentence.
            if flat[i].lower() in seen or (p.text not in lines if p.is_heading else flat[i] not in chunk):
                continue
            seen.add(flat[i].lower())  # chunks overlap, and pages repeat lines
            then = pieces[i + 1].text if p.leads_in and i + 1 < len(pieces) else ""
            cands.append(Match(p.text, then, p.section, h.chunk.id, 0.0))
    if not cands:
        return []
    if reranker:
        scores = reranker.score(query, [m.text for m in cands])
    else:
        vecs = store.embedder.encode([query] + [m.text for m in cands])
        scores = (vecs[1:] @ vecs[0]).tolist()
    for m, score in zip(cands, scores):
        m.score = float(score)
    return sorted(cands, key=lambda m: -m.score)[:top_k]
