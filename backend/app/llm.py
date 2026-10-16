"""Thin async client for Ollama's chat API."""
import json
from collections.abc import AsyncIterator

import httpx


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, model: str, temperature: float, num_ctx: int, timeout_s: float):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.options = {"temperature": temperature, "num_ctx": num_ctx}
        self.timeout = httpx.Timeout(timeout_s, connect=5.0)

    async def health(self) -> dict:
        """Report whether Ollama is reachable and the configured model is pulled."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
        except httpx.HTTPError as e:
            return {"reachable": False, "model_available": False, "error": str(e)}
        names = {m["name"] for m in r.json().get("models", [])}
        available = self.model in names or f"{self.model}:latest" in names
        return {"reachable": True, "model_available": available}

    async def chat(self, messages: list[dict]) -> str:
        return "".join([tok async for tok in self.stream(messages)])

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        # keep_alive: avoid reloading the model between questions.
        payload = {"model": self.model, "messages": messages, "stream": True, "options": self.options, "keep_alive": "30m"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode(errors="replace")
                        raise LLMError(f"Ollama returned {r.status_code}: {body[:300]}")
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if err := data.get("error"):
                            raise LLMError(err)
                        if tok := data.get("message", {}).get("content"):
                            yield tok
                        if data.get("done"):
                            break
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot reach Ollama at {self.base_url}. Is it running? ({e})") from e
        except httpx.TimeoutException as e:
            raise LLMError("Ollama timed out while generating an answer") from e
