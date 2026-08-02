"""Async chat client over any OpenAI-compatible endpoint.

One code path covers vLLM, SGLang, LightLLM, Ollama, llama.cpp, TGI, LM Studio,
a LiteLLM proxy, and every hosted API that speaks /v1/chat/completions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from openai import AsyncOpenAI, APIError, APIStatusError

from .config import Endpoint, TEMPERATURE, TOP_P


@dataclass
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None
    error: str | None = None


class ChatClient:
    """Thin wrapper. The SDK already retries connection errors, 408, 409, 429 and 5xx."""

    def __init__(self, endpoint: Endpoint, max_retries: int = 4, timeout: float = 180.0) -> None:
        self.endpoint = endpoint
        self._client = AsyncOpenAI(
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
            max_retries=max_retries,
            timeout=timeout,
        )

    async def chat(self, messages: list[dict], *, max_tokens: int | None = None) -> Completion:
        try:
            r = await self._client.chat.completions.create(
                model=self.endpoint.model,
                messages=messages,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                max_tokens=max_tokens or self.endpoint.max_tokens,
            )
        except APIStatusError as e:
            return Completion(text="", error=f"http_{e.status_code}: {str(e)[:200]}")
        except APIError as e:
            return Completion(text="", error=f"api_error: {str(e)[:200]}")
        except asyncio.TimeoutError:
            return Completion(text="", error="timeout")
        except Exception as e:  # noqa: BLE001 - surface anything the endpoint throws
            return Completion(text="", error=f"{type(e).__name__}: {str(e)[:200]}")

        choice = r.choices[0] if r.choices else None
        usage = r.usage
        return Completion(
            text=(choice.message.content or "") if choice else "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            finish_reason=getattr(choice, "finish_reason", None) if choice else None,
        )

    async def probe(self) -> str | None:
        """Return None if the endpoint answers, else an error string. Fails fast before a long run."""
        c = await self.chat([{"role": "user", "content": "hi"}], max_tokens=8)
        return c.error

    async def close(self) -> None:
        await self._client.close()
