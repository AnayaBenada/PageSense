"""Shared helpers for the eval scripts. Run scripts from backend/: python -m eval.<script>"""
import json
import re
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
PAGES_DIR = EVAL_DIR / "pages"
RESULTS_DIR = EVAL_DIR / "results"


def load_pages() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(PAGES_DIR.glob("*.json"))}


def load_dataset(name: str = "dataset") -> list[dict]:
    """`dataset` is the original 55 questions; `dataset_holdout` was written after
    tuning, to check that improvements aren't specific to the original set;
    `dataset_holdout2` is a second, fresh holdout written after that."""
    return [json.loads(line) for line in (EVAL_DIR / f"{name}.jsonl").read_text().splitlines() if line.strip()]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def contains_evidence(text: str, evidence: list[str]) -> bool:
    t = norm(text)
    return any(norm(e) in t for e in evidence)


def save_result(name: str, data) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / name
    path.write_text(json.dumps(data, indent=2) if not isinstance(data, str) else data)
    return path


def make_store(chunk_tokens: int, chunk_overlap: int):
    """An in-process IndexStore with the real embedding model (no server needed)."""
    import app  # noqa: F401  (sets OpenMP env before torch/faiss load)
    from app.config import settings
    from app.embedder import SentenceTransformerEmbedder
    from app.index_store import IndexStore

    return IndexStore(SentenceTransformerEmbedder(settings.embed_model, settings.embed_device), 100, chunk_tokens, chunk_overlap)


def make_reranker():
    import app  # noqa: F401
    from app.config import settings
    from app.embedder import CrossEncoderReranker

    return CrossEncoderReranker(settings.rerank_model, settings.embed_device) if settings.rerank_model else None
