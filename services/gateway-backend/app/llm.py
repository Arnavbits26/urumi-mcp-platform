"""A small client for any OpenAI-compatible chat-completions API (Groq, Gemini's compatible endpoint,
a local Ollama, and so on). Which provider is used is configuration: base URL, model and key."""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("gateway.llm")


class LLMError(Exception):
    """Something went wrong talking to the AI service. The message is safe to show to the user."""


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None, max_attempts: int = 3) -> None:
        self.model = model
        self.max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        """One model turn. Returns the assistant message (which may ask for tool calls)."""
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            payload["tools"], payload["tool_choice"] = tools, "auto"
        last = "no answer"
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                raise LLMError("The AI service did not answer in time. Try again.") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Could not reach the AI service ({type(exc).__name__}).") from exc

            if resp.status_code == 429:
                last = "rate-limited"
                if attempt < self.max_attempts:
                    try:
                        wait = float(resp.headers.get("retry-after", "2"))
                    except ValueError:
                        wait = 2.0
                    await asyncio.sleep(min(max(wait, 0.0), 15.0))
                    continue
                raise LLMError("The AI service is rate-limiting this account (free-tier limits). Wait a minute and try again.")
            if resp.status_code in (401, 403):
                raise LLMError("The AI service rejected the API key. An admin needs to update it (scripts/llm-secret.sh).")
            if resp.status_code == 400 and "tool_use_failed" in resp.text and attempt < self.max_attempts:
                last = "the model produced a malformed tool call"   # models are not deterministic; one more try usually works
                continue
            if resp.status_code >= 400:
                try:
                    detail = str(resp.json().get("error", {}).get("message", ""))[:300]
                except Exception:
                    detail = ""
                if resp.status_code >= 500:
                    raise LLMError("The AI service is having problems right now. Try again shortly.")
                raise LLMError(f"The AI service could not process the request. {detail}".strip())
            try:
                return resp.json()["choices"][0]["message"]
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError("The AI service sent an answer in an unexpected format.") from exc
        raise LLMError(f"The AI service did not give a usable answer ({last}).")
