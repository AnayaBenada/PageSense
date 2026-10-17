"""Retrieve relevant chunks and build the grounded prompt."""
import re

from .embedder import Reranker
from .index_store import Hit, IndexStore, PageIndex
from .schemas import Turn

SYSTEM_PROMPT = """You are PageSense, an assistant that answers questions about the web page the user is reading.

Rules:
- Answer ONLY using the numbered page excerpts provided. Do not use outside knowledge.
- If the excerpts do not contain the answer, say: "I couldn't find that on this page." and stop.
- Be concise and direct. Quote exact values, names, code, and numbers from the page.
- Cite the excerpts you used with their numbers in square brackets, e.g. [2] or [1][3].
- Talk about "the page", never about "excerpts" or "context".
- Use Markdown for lists and code blocks when helpful."""

# Small models follow instructions placed next to the question more reliably than
# ones only in the system prompt, so the key rules are repeated here.
QUESTION_SUFFIX = (
    "Answer using only the page excerpts above, citing them like [1]. "
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


def retrieve(
    store: IndexStore,
    page: PageIndex,
    query: str,
    top_k: int,
    min_score: float,
    reranker: Reranker | None = None,
    rerank_candidates: int = 20,
) -> list[Hit]:
    """Best chunks for the query, most relevant first.

    FAISS (cosine) finds candidates; chunks below min_score are dropped (keeping at
    least the best one). If a reranker is given, it re-scores up to
    rerank_candidates of them and the top_k by reranker score are returned."""
    hits = store.search(page, query, max(top_k, rerank_candidates) if reranker else top_k)
    hits = [h for h in hits if h.score >= min_score] or hits[:1]
    if reranker and len(hits) > 1:
        scores = reranker.score(query, [h.chunk.text for h in hits])
        hits = [h for _, h in sorted(zip(scores, hits), key=lambda x: -x[0])]
    return hits[:top_k]


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
