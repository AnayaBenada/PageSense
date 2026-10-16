import pytest

from app.chunker import chunk_text, clean_text, count_tokens


def test_clean_text_collapses_whitespace():
    assert clean_text("a  \t b\r\n\n\n\nc d") == "a b\n\nc d"


def test_empty_text_gives_no_chunks():
    assert chunk_text("   \n\n  ") == []


def test_chunks_respect_size_limit():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(50))
    chunks = chunk_text(text, size=120, overlap=20)
    assert len(chunks) > 5
    assert all(c.n_tokens <= 125 for c in chunks)  # small slack for joining newlines
    assert [c.id for c in chunks] == list(range(len(chunks)))


def test_every_paragraph_is_covered():
    paras = [f"Unique marker {i} " + "filler " * 30 for i in range(30)]
    chunks = chunk_text("\n\n".join(paras), size=100, overlap=20)
    joined = "\n".join(c.text for c in chunks)
    for i in range(30):
        assert f"Unique marker {i} " in joined


def test_consecutive_chunks_overlap():
    paras = [f"Sentence number {i} is here." for i in range(200)]
    chunks = chunk_text("\n\n".join(paras), size=80, overlap=20)
    for a, b in zip(chunks, chunks[1:]):
        last_para_of_a = a.text.split("\n\n")[-1]
        assert last_para_of_a in b.text


def test_long_paragraph_is_split_with_overlap():
    text = " ".join(f"tok{i}" for i in range(2000))
    chunks = chunk_text(text, size=200, overlap=40)
    assert len(chunks) > 5
    assert all(c.n_tokens <= 200 for c in chunks)


def test_headings_are_tracked():
    text = "# Install\n\nRun pip install.\n\n## Linux\n\nUse apt.\n\n# Usage\n\nCall the API."
    chunks = chunk_text(text, size=10, overlap=0)
    by_text = {c.text: c.heading for c in chunks}
    assert any("apt" in t and h == "Install > Linux" for t, h in by_text.items())
    assert any("Call the API" in t and h == "Usage" for t, h in by_text.items())


def test_20k_token_page():
    text = "\n\n".join(f"Section {i}. " + "lorem ipsum dolor sit amet " * 20 for i in range(200))
    assert count_tokens(text) >= 20_000
    chunks = chunk_text(text, size=300, overlap=50)
    assert 60 < len(chunks) < 120


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_text("hello", size=50, overlap=50)


def test_code_blocks_kept_verbatim():
    code = "```\ndef f():\n    return 1\n\n\nclass A:\n    pass\n```"
    text = f"# Example\n\nSome    intro   text.\n\n{code}\n\nAfter."
    chunks = chunk_text(text, size=300, overlap=20)
    assert len(chunks) == 1
    assert "def f():\n    return 1\n\nclass A:\n    pass" in chunks[0].text  # indentation kept; 3+ newlines squashed
    assert "Some intro text." in chunks[0].text


def test_code_block_with_blank_lines_is_one_paragraph():
    from app.chunker import _paragraphs, clean_text

    text = "Intro.\n\n```\na = 1\n\nb = 2\n```\n\nOutro."
    paras = _paragraphs(clean_text(text))
    assert [p.text for p in paras] == ["Intro.", "```\na = 1\n\nb = 2\n```", "Outro."]
