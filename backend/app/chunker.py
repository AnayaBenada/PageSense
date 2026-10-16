"""Clean page text and split it into overlapping, token-bounded chunks.

The extension sends text with headings marked Markdown-style (`# `, `## `, ...),
so chunks can carry the heading they live under. Paragraph boundaries are
respected where possible; only paragraphs larger than a whole chunk are cut
mid-paragraph.
"""
import re
from dataclasses import dataclass

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


@dataclass
class Chunk:
    id: int
    text: str
    heading: str
    n_tokens: int


@dataclass
class _Para:
    text: str
    heading: str
    n_tokens: int


_FENCE = re.compile(r"^\s*```")


def clean_text(text: str) -> str:
    """Normalize whitespace outside fenced code blocks; code is kept verbatim."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    parts = re.split(r"(^\s*```[^\n]*\n.*?^\s*```[ \t]*$)", text, flags=re.M | re.S)
    out = []
    for i, part in enumerate(parts):
        if i % 2:  # fenced code
            out.append(part.strip("\n"))
            continue
        part = re.sub(r"[ \t]+", " ", part)
        part = re.sub(r" *\n *", "\n", part)
        out.append(part)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _paragraphs(text: str) -> list[_Para]:
    """Split into paragraphs (blank-line separated; code fences kept whole),
    tracking the heading path each paragraph sits under."""
    paras: list[_Para] = []
    path: list[tuple[int, str]] = []
    body: list[str] = []
    has_content = False  # body holds more than just heading titles
    in_code = False

    def flush() -> None:
        nonlocal has_content
        joined = "\n".join(body).strip("\n")
        if joined.strip():
            heading = " > ".join(h for _, h in path)
            paras.append(_Para(joined, heading, count_tokens(joined)))
        body.clear()
        has_content = False

    for line in text.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
            body.append(line)
            has_content = True
            if not in_code:
                flush()  # a code block ends its paragraph
            continue
        if in_code:
            body.append(line)
            continue
        m = _HEADING.match(line)
        if m:
            if has_content:
                flush()
            elif body:
                body.clear()  # a heading with no content of its own before the next heading
            level, title = len(m.group(1)), m.group(2).strip()
            path[:] = [(lv, h) for lv, h in path if lv < level] + [(level, title)]
            # Keep the heading text searchable as the first line of its section's paragraph.
            body.append(title)
        elif not line.strip():
            if has_content:
                flush()  # paragraph break (a heading alone waits for its content)
        else:
            body.append(line)
            has_content = True
    flush()
    return paras


def _split_long(para: _Para, size: int, overlap: int) -> list[_Para]:
    tokens = _enc.encode(para.text, disallowed_special=())
    step = max(1, size - overlap)
    out = []
    for start in range(0, len(tokens), step):
        window = tokens[start : start + size]
        out.append(_Para(_enc.decode(window), para.heading, len(window)))
        if start + size >= len(tokens):
            break
    return out


def _tail(paras: list[_Para], overlap: int) -> list[_Para]:
    """Trailing paragraphs (or a trailing slice of the last one) worth ~overlap tokens."""
    if overlap <= 0 or not paras:
        return []
    tail: list[_Para] = []
    total = 0
    for p in reversed(paras):
        if total + p.n_tokens > overlap:
            if not tail:
                tokens = _enc.encode(p.text, disallowed_special=())[-overlap:]
                tail.append(_Para(_enc.decode(tokens), p.heading, len(tokens)))
            break
        tail.insert(0, p)
        total += p.n_tokens
    return tail


def chunk_text(text: str, size: int = 300, overlap: int = 50) -> list[Chunk]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than chunk size")
    paras: list[_Para] = []
    for p in _paragraphs(clean_text(text)):
        paras.extend(_split_long(p, size, overlap) if p.n_tokens > size else [p])

    chunks: list[Chunk] = []
    current: list[_Para] = []
    current_tokens = 0
    fresh = 0  # paragraphs in `current` that aren't overlap carried from the previous chunk

    def emit() -> None:
        body = "\n\n".join(p.text for p in current)
        # Label the chunk by its own (non-overlap) content.
        heading = current[len(current) - fresh].heading
        chunks.append(Chunk(len(chunks), body, heading, count_tokens(body)))

    for p in paras:
        # Break at section boundaries once the chunk is reasonably full.
        new_section = bool(fresh) and p.heading != current[-1].heading and current_tokens >= size // 2
        if current and (current_tokens + p.n_tokens > size or new_section):
            emit()
            current = _tail(current, overlap)
            # Drop the carried overlap if it would still leave no room for p.
            if sum(c.n_tokens for c in current) + p.n_tokens > size:
                current = []
            current_tokens = sum(c.n_tokens for c in current)
            fresh = 0
        current.append(p)
        current_tokens += p.n_tokens
        fresh += 1
    if current and fresh:
        emit()
    return chunks
