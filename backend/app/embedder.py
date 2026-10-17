from typing import Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalized float32 embeddings, shape (len(texts), dim)."""
        ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, device: str = "cpu"):
        import os

        import torch
        from sentence_transformers import SentenceTransformer

        # OMP_NUM_THREADS=1 (see app/__init__.py) is meant for faiss; give torch the cores.
        torch.set_num_threads(min(8, os.cpu_count() or 4))
        # CPU by default: on Apple Silicon, MPS adds multi-second stalls for small
        # batches and competes with Ollama for unified memory.
        self.model = SentenceTransformer(model_name, device=device)
        self.dim = self.model.get_embedding_dimension()
        self.encode(["warm up"])

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(
            texts,
            batch_size=64,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.ascontiguousarray(vecs, dtype=np.float32)


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]:
        """Relevance score per text (higher is better)."""
        ...


class CrossEncoderReranker:
    def __init__(self, model_name: str, device: str = "cpu"):
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name, device=device)
        self.score("warm up", ["warm up"])

    def score(self, query: str, texts: list[str]) -> list[float]:
        return self.model.predict([(query, t) for t in texts], batch_size=32, show_progress_bar=False).tolist()
