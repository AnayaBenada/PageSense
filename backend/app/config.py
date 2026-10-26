"""Runtime settings. Every value can be overridden with a PAGESENSE_* env var."""
import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(f"PAGESENSE_{name}", default)


@dataclass(frozen=True)
class Settings:
    embed_model: str = _env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    embed_device: str = _env("EMBED_DEVICE", "cpu")
    # Cross-encoder that re-scores the top candidates from FAISS. Empty string disables it.
    rerank_model: str = _env("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-12-v2")
    rerank_candidates: int = int(_env("RERANK_CANDIDATES", "20"))
    # Also add keyword (BM25) matches to the candidates, not just embedding matches.
    # Off by default: with the reranker on it lowered hit@5 from 98.0% to 93.9% on
    # the eval set and doubled retrieval time (see README).
    hybrid_search: bool = _env("HYBRID_SEARCH", "0") not in ("0", "false", "")
    # Also send the chunks on either side of the best `neighbor_hits` matches, so
    # explanations that continue into the next chunk (code, lists) aren't cut off.
    neighbor_hits: int = int(_env("NEIGHBOR_HITS", "1"))
    llm_model: str = _env("LLM_MODEL", "llama3.2")
    ollama_url: str = _env("OLLAMA_URL", "http://localhost:11434")

    chunk_tokens: int = int(_env("CHUNK_TOKENS", "300"))
    chunk_overlap: int = int(_env("CHUNK_OVERLAP", "50"))
    top_k: int = int(_env("TOP_K", "5"))
    # Chunks scoring below this cosine similarity are dropped (but we always keep the best one).
    min_score: float = float(_env("MIN_SCORE", "0.15"))

    max_text_chars: int = int(_env("MAX_TEXT_CHARS", "400000"))
    max_question_chars: int = int(_env("MAX_QUESTION_CHARS", "2000"))
    cache_size: int = int(_env("CACHE_SIZE", "20"))
    history_turns: int = int(_env("HISTORY_TURNS", "3"))

    llm_temperature: float = float(_env("LLM_TEMPERATURE", "0"))
    llm_num_ctx: int = int(_env("LLM_NUM_CTX", "4096"))
    llm_timeout_s: float = float(_env("LLM_TIMEOUT_S", "120"))


settings = Settings()
