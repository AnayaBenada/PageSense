import hashlib
import os
import re
import tempfile

# Settings() defaults are read at import time; keep tests out of ~/.pagesense.
os.environ.setdefault("PAGESENSE_LIBRARY_PATH", os.path.join(tempfile.mkdtemp(), "library.db"))

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

    async def chat_json(self, messages, temperature=None):
        return "".join([t async for t in self.stream(messages, json_mode=True)])

    async def stream(self, messages, json_mode=False, temperature=None):
        self.last_messages = messages
        if self.fail:
            raise LLMError("Cannot reach Ollama")
        for tok in (self.json_reply(messages) if json_mode else ["The ", "answer ", "[1]"]):
            yield tok

    def json_reply(self, messages):
        return ["{}"]


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def client(fake_llm, tmp_path):
    cfg = Settings(
        chunk_tokens=60, chunk_overlap=10, top_k=3, cache_size=3, max_text_chars=50_000,
        library_path=str(tmp_path / "library.db"),
    )
    app = create_app(cfg, embedder=FakeEmbedder(), llm=fake_llm)
    with TestClient(app) as c:
        yield c
