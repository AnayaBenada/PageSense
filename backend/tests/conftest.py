import hashlib
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import LLMError
from app.main import create_app


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder: shared words → higher cosine."""

    dim = 256

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out


class FakeLLM:
    def __init__(self, fail=False):
        self.fail = fail
        self.last_messages = None

    async def health(self):
        return {"reachable": True, "model_available": True}

    async def chat(self, messages):
        return "".join([t async for t in self.stream(messages)])

    async def stream(self, messages):
        self.last_messages = messages
        if self.fail:
            raise LLMError("Cannot reach Ollama")
        for tok in ["The ", "answer ", "[1]"]:
            yield tok


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def client(fake_llm):
    cfg = Settings(chunk_tokens=60, chunk_overlap=10, top_k=3, cache_size=3, max_text_chars=50_000)
    app = create_app(cfg, embedder=FakeEmbedder(), llm=fake_llm)
    with TestClient(app) as c:
        yield c
