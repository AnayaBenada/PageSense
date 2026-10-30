"""Student mode: practice quizzes and study notes generated from the page.

Each quiz question is written about one key sentence picked from a passage (small
models write far better questions about one clear fact than about a whole page),
then checked: the model takes its own quiz
without the answer key, and questions it gets "wrong" (usually a wrong key, or two
right choices) are dropped. The key sentence is what the panel highlights after you answer.
"""
import json
import random
import re
from dataclasses import dataclass

from .chunker import Chunk
from .embedder import Reranker
from .find import page_pieces
from .index_store import IndexStore, PageIndex

QUIZ_SYSTEM = """You write a multiple-choice quiz question that checks whether a student understood one key sentence from a web page.

Reply with JSON only, in this shape:
{"question": "...", "choices": ["...", "...", "...", "..."], "answer": 0}

Rules:
- The question must be answered by the key sentence. The correct choice states what the key sentence says.
- The question must make sense on its own: never say "the sentence", "the passage", "the text" or "the author".
- Exactly 4 short choices. The other 3 must be plausible but clearly false according to the key sentence and the context.
- "answer" is the index (0-3) of the correct choice.
- Never use "all of the above" or "none of the above"."""

# Two independent checks, each blind to the answer key: pick a lettered choice (asked
# in two orders, so a model that always picks the same position fails), and answer
# with no choices at all. On 15 hand-labeled questions, requiring all three to agree
# kept 5 of 6 good questions and 2 of 9 bad ones, and caught both wrong answer keys.
CHECK_CHOICE_SYSTEM = """You are taking a quiz about a passage. Using only the passage, pick the one correct choice.
Reply with JSON only: {"answer": "<letter>"}"""

CHECK_OPEN_SYSTEM = """Answer the question using only the passage, in one short phrase.
Reply with JSON only: {"answer": "..."}"""

LETTERS = "ABCD"

NOTES_SYSTEM = """You write study notes for a student from a web page.

Rules:
- Use ONLY the page content provided. Do not add outside knowledge.
- Organize the notes by the page's own sections, in page order: a "### Section name" line, then 2-4 short bullet points with the key ideas, facts, steps or definitions.
- Each bullet is one short sentence or phrase. Keep the page's exact names, numbers, commands and terms.
- Skip sections that are only navigation, links or boilerplate.
- End with a "### Key terms" section: up to 8 bullets like "- **term**: short definition from the page".
- No introduction or conclusion sentences."""

# Leaves room in the model's context window for the prompt and ~900 tokens of notes.
NOTES_BUDGET_TOKENS = 2600
MIN_QUIZ_CHUNK_TOKENS = 60
_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)


@dataclass
class QuizQuestion:
    question: str
    choices: list[str]
    answer: int
    evidence: str  # exact page sentence that supports the answer, for highlighting
    heading: str
    chunk_id: int


def _prose_tokens(chunk: Chunk) -> float:
    """Tokens of the chunk that aren't code; quizzes on code listings come out poorly."""
    prose = _FENCE.sub("", chunk.text)
    return chunk.n_tokens * len(prose) / max(len(chunk.text), 1)


def quiz_chunks(page: PageIndex, n: int, round_: int = 0) -> list[Chunk]:
    """n prose-heavy chunks spread through the page; later rounds pick different ones.
    Callers ask for a couple extra, since some questions fail the check."""
    eligible = [c for c in page.chunks if _prose_tokens(c) >= MIN_QUIZ_CHUNK_TOKENS] or list(page.chunks)
    if len(eligible) <= n:
        return eligible
    if round_ == 0:
        ids = sorted({round(i * (len(eligible) - 1) / max(n - 1, 1)) for i in range(n)})
        return [eligible[i] for i in ids]
    return sorted(random.Random(f"{page.page_id}:{round_}").sample(eligible, n), key=lambda c: c.id)


_CUES = re.compile(
    r"\b(is|are|means|lets|allows?|returns?|must|should|can't|cannot|because|so that|when|if|only|always|never|instead|called|creates?|causes?)\b",
    re.IGNORECASE,
)
_CODEY = re.compile(r"[{};=<>]|=>|\(\)|^\$ |\w+\.\w+\(")


def key_sentence(page: PageIndex, chunk: Chunk) -> str:
    """The sentence in the chunk most worth a quiz question: a self-contained statement
    of fact (a definition, rule, reason or result), not code, a heading or a fragment."""
    flat_chunk = " ".join(chunk.text.split())
    best, best_score = "", 0.0
    for p in page_pieces(page.text):
        text = " ".join(p.text.split())
        if p.is_heading or p.leads_in or text not in flat_chunk or not 50 <= len(text) <= 260 or _CODEY.search(text):
            continue
        if not text[0].isupper() or not text.endswith((".", "!", "?")):
            continue
        score = min(len(text), 160) / 160 + 0.5 * len(_CUES.findall(text))
        if score > best_score:
            best, best_score = p.text, score
    return best


def quiz_messages(page: PageIndex, chunk: Chunk, fact: str) -> list[dict]:
    section = f"Section: {chunk.heading}\n" if chunk.heading else ""
    return [
        {"role": "system", "content": QUIZ_SYSTEM},
        {"role": "user", "content": f"Page: {page.title}\n{section}\nContext:\n{chunk.text}\n\nKey sentence: {fact}\n\nWrite one question about the key sentence."},
    ]


def check_choice_messages(chunk: Chunk, q: dict, order: list[int]) -> list[dict]:
    choices = "\n".join(f"{LETTERS[k]}) {q['choices'][i]}" for k, i in enumerate(order))
    return [
        {"role": "system", "content": CHECK_CHOICE_SYSTEM},
        {"role": "user", "content": f"Passage:\n{chunk.text}\n\nQuestion: {q['question']}\n{choices}"},
    ]


def check_open_messages(chunk: Chunk, q: dict) -> list[dict]:
    return [
        {"role": "system", "content": CHECK_OPEN_SYSTEM},
        {"role": "user", "content": f"Passage:\n{chunk.text}\n\nQuestion: {q['question']}"},
    ]


def _answer(raw: str) -> str:
    try:
        r = json.loads(raw)
        return str(r.get("answer", "")).strip() if isinstance(r, dict) else ""
    except json.JSONDecodeError:
        return ""


def picked_choice(raw: str, order: list[int]) -> int | None:
    """The choice index a lettered reply refers to (None if unreadable)."""
    letter = _answer(raw)[:1].upper()
    return order[LETTERS.index(letter)] if letter and letter in LETTERS else None


def matched_choice(store: IndexStore, raw: str, q: dict) -> int | None:
    """The choice closest in meaning to a free-text answer."""
    answer = _answer(raw)
    if not answer:
        return None
    vecs = store.embedder.encode([answer] + q["choices"])
    sims = (vecs[1:] @ vecs[0]).tolist()
    return max(range(len(sims)), key=lambda i: sims[i])


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def parse_question(raw: str) -> dict | None:
    """The model's JSON, or None if it isn't a usable multiple-choice question."""
    try:
        q = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(q, dict):
        return None
    question, choices, answer = q.get("question"), q.get("choices"), q.get("answer")
    if not isinstance(question, str) or len(question.strip()) < 10:
        return None
    if not isinstance(choices, list) or len(choices) != 4 or not all(isinstance(c, str) and c.strip() for c in choices):
        return None
    if isinstance(answer, str) and answer.strip().isdigit():
        answer = int(answer.strip())
    if not isinstance(answer, int) or not 0 <= answer < 4:
        return None
    choices = [c.strip() for c in choices]
    if len({_norm(c) for c in choices}) < 4 or any(re.search(r"\b(all|none) of the above\b", c, re.I) for c in choices):
        return None
    return {
        "question": question.strip(),
        "choices": choices,
        "answer": answer,
    }


def shuffle_choices(q: dict, seed: str) -> dict:
    """Models put the right answer first far too often; shuffle so it isn't guessable."""
    order = list(range(4))
    random.Random(seed).shuffle(order)
    return {**q, "choices": [q["choices"][i] for i in order], "answer": order.index(q["answer"])}


def notes_chunks(page: PageIndex, budget: int = NOTES_BUDGET_TOKENS) -> list[Chunk]:
    """The whole page if it fits; otherwise chunks spread evenly through it."""
    chunks = page.chunks
    if sum(c.n_tokens for c in chunks) <= budget:
        return list(chunks)
    avg = sum(c.n_tokens for c in chunks) / len(chunks)
    k = max(1, int(budget // avg))
    ids = sorted({round(i * (len(chunks) - 1) / max(k - 1, 1)) for i in range(k)})
    picked, used = [], 0
    for i in ids:
        if used + chunks[i].n_tokens > budget and picked:
            break
        picked.append(chunks[i])
        used += chunks[i].n_tokens
    return picked


def notes_messages(page: PageIndex, chunks: list[Chunk]) -> list[dict]:
    partial = len(chunks) < len(page.chunks)
    body = "\n\n".join(f"[Section: {c.heading or page.title}]\n{c.text}" for c in chunks)
    scope = "These are excerpts spread through a long page." if partial else "This is the whole page."
    return [
        {"role": "system", "content": NOTES_SYSTEM},
        {"role": "user", "content": f"Page: {page.title}\n{scope}\n\n{body}\n\nWrite the study notes for this page, following the rules."},
    ]
