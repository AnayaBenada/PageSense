"""Retrieve relevant chunks and build the grounded prompt."""
import re

import numpy as np

from .embedder import Reranker
from .index_store import Hit, IndexStore, PageIndex
from .schemas import Turn

SYSTEM_PROMPT = """You are PageSense, an assistant that answers questions about the web page the user is reading.

Rules:
- Answer ONLY using the numbered page excerpts provided. Do not use outside knowledge.
- If the excerpts do not contain the answer, say: "I couldn't find that on this page." and stop.
- Read every excerpt before answering. Never say a detail is missing if an excerpt mentions it.
- Keep the page's own wording for technical details (what something returns, removes or shares, where it appears, what order things happen in). Rephrasing them often changes the meaning.
- If the page gives several cases, conditions, rules or steps that answer the question, include all of them.
- Only show a code example if it does exactly what was asked.
- Be concise and direct. Quote exact values, names, code, and numbers from the page.
- Cite the excerpts you used with their numbers in square brackets, e.g. [2] or [1][3].
- Talk about "the page", never about "excerpts" or "context".
- Use Markdown for lists and code blocks when helpful."""

# Small models follow instructions placed next to the question more reliably than
# ones only in the system prompt, so the key rules are repeated here.
QUESTION_SUFFIX = (
    "Answer using only the page excerpts above, citing them like [1]. "
    "Stick close to the page's wording and include every point the page makes that answers the question. "
    "If they don't contain the answer, reply exactly: I couldn't find that on this page."
)

# Summaries need the whole page, not the passages most similar to "summarize this".
OVERVIEW_SUFFIX = (
    "The excerpts above are spread evenly across the page. Use them to answer, "
    "citing them like [1]. Only use what they say."
)

_OVERVIEW_RE = re.compile(
    r"\b(summari[sz]e|summary|tl;?dr|overview|gist|recap|key (points|takeaways)|main (points|ideas?|takeaways))\b"
    r"|\bwhat(\'s| is)( this| the)? (page|article|post|doc(ument)?)( all)? about\b",
    re.IGNORECASE,
)


def is_overview_question(question: str) -> bool:
    return bool(_OVERVIEW_RE.search(question))


def overview_hits(page: PageIndex, k: int) -> list[Hit]:
    """k chunks evenly spaced through the page, always including the first."""
    n = len(page.chunks)
    k = min(k, n)
    ids = sorted({round(i * (n - 1) / max(k - 1, 1)) for i in range(k)})
    return [Hit(page.chunks[i], None) for i in ids]


def retrieval_query(question: str, history: list[Turn]) -> str:
    """Follow-ups like "what about the second one?" retrieve poorly on their own,
    so fold in the previous question for context."""
    if history:
        return f"{history[-1].question}\n{question}"
    return question


# How many keyword (BM25) matches are added to the embedding candidates.
KEYWORD_CANDIDATES = 8


def retrieve(
    store: IndexStore,
    page: PageIndex,
    query: str,
    top_k: int,
    min_score: float,
    reranker: Reranker | None = None,
    rerank_candidates: int = 20,
    hybrid: bool = True,
) -> list[Hit]:
    """Best chunks for the query, most relevant first.

    FAISS (cosine) finds candidates; chunks below min_score are dropped (keeping at
    least the best one). With hybrid on, the best BM25 keyword matches are added
    too. If a reranker is given, it re-scores the candidates and the top_k by
    reranker score are returned; without one, candidates are ordered by
    reciprocal rank fusion of the two rankings."""
    n_dense = max(top_k, rerank_candidates) if reranker else top_k
    if not hybrid:
        hits = store.search(page, query, n_dense)
        hits = [h for h in hits if h.score >= min_score] or hits[:1]
    else:
        ranked = store.search(page, query, len(page.chunks))
        cosine = {h.chunk.id: h.score for h in ranked}
        dense = [h for h in ranked if h.score >= min_score][:n_dense] or ranked[:1]
        bm25 = page.bm25.scores(query)
        keyword = [int(i) for i in np.argsort(bm25)[::-1][:KEYWORD_CANDIDATES] if bm25[i] > 0]
        rrf: dict[int, float] = {}
        for rank, cid in enumerate([h.chunk.id for h in dense]):
            rrf[cid] = rrf.get(cid, 0) + 1 / (60 + rank)
        for rank, cid in enumerate(keyword):
            rrf[cid] = rrf.get(cid, 0) + 1 / (60 + rank)
        hits = [Hit(page.chunks[cid], cosine[cid]) for cid in sorted(rrf, key=lambda c: -rrf[c])]
    if reranker and len(hits) > 1:
        scores = reranker.score(query, [h.chunk.text for h in hits])
        hits = [h for _, h in sorted(zip(scores, hits), key=lambda x: -x[0])]
    return hits[:top_k]


def add_neighbors(page: PageIndex, hits: list[Hit], n_best: int) -> list[Hit]:
    """Add the chunks just before and after the n_best top hits (hits are most
    relevant first), so an explanation that runs into the next chunk isn't cut off."""
    have = {h.chunk.id for h in hits}
    extra = []
    for h in hits[:n_best]:
        for cid in (h.chunk.id - 1, h.chunk.id + 1):
            if 0 <= cid < len(page.chunks) and cid not in have:
                have.add(cid)
                extra.append(Hit(page.chunks[cid], None))
    return hits + extra


def page_order(hits: list[Hit]) -> list[Hit]:
    """Excerpts are shown (and cited) in page order: excerpt [n] is page_order(hits)[n-1]."""
    return sorted(hits, key=lambda h: h.chunk.id)


def build_messages(
    page: PageIndex, question: str, hits: list[Hit], history: list[Turn], overview: bool = False
) -> list[dict]:
    excerpts = "\n\n".join(
        f"[{n}]" + (f" (section: {h.chunk.heading})" if h.chunk.heading else "") + f"\n{h.chunk.text}"
        for n, h in enumerate(hits, 1)
    )
    context = f"Page title: {page.title or '(untitled)'}\nURL: {page.url}\n\nPage excerpts:\n\n{excerpts}"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer})
    messages.append({"role": "user", "content": f"{context}\n\n---\n\nQuestion: {question}\n\n{OVERVIEW_SUFFIX if overview else QUESTION_SUFFIX}"})
    return messages
