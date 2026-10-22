"""
LLM providers implementation.

Decouples post-call outcome extraction and conversation intelligence from
concrete model SDKs (Google Gemini, OpenAI, Claude, Mock) using the LLMProvider protocol.
Clients are instantiated lazily on demand rather than at import time.
"""
import asyncio
import logging
from typing import Any

from app.core.config import settings
from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)


def _normalize_prompt(prompt_or_messages: str | list[dict[str, Any]]) -> str:
    """Helper to convert string or list of message dicts into a single prompt string."""
    if isinstance(prompt_or_messages, str):
        return prompt_or_messages
    if isinstance(prompt_or_messages, list):
        parts = []
        for msg in prompt_or_messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            parts.append(f"{role}: {content}")
        return "\n".join(parts)
    return str(prompt_or_messages)


class GeminiLLMProvider:
    """
    Google Gemini LLM provider.

    Initializes the Google GenAI client lazily on first invocation rather than
    at module import time, preventing startup crashes and enabling mock injection.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        client: Any | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._client = client

    @property
    def api_key(self) -> str:
        return self._api_key or settings.GEMINI_API_KEY or ""

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def complete(
        self,
        prompt_or_messages: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        from google.genai import types

        client = self._get_client()
        prompt = _normalize_prompt(prompt_or_messages)
        response_mime_type = kwargs.get("response_mime_type", "application/json")

        response = await client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type=response_mime_type),
        )

        # Accessing response.text raises ValueError when Gemini returns no candidates
        # (safety filter block, quota exhaustion, or empty output). Guard explicitly.
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            logger.warning("Gemini returned no candidates (prompt may have been blocked by safety filters).")
            return ""

        candidate = candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        # finish_reason 2 = SAFETY, 3 = RECITATION, 4 = OTHER (non-content stops)
        if finish_reason is not None and str(finish_reason) not in ("0", "1", "FinishReason.STOP", "STOP"):
            logger.warning("Gemini candidate finish_reason=%s — returning empty response.", finish_reason)
            return ""

        try:
            return response.text or ""
        except ValueError as exc:
            logger.warning("Gemini response.text raised ValueError (%s) — returning empty.", exc)
            return ""


class OpenAILLMProvider:
    """OpenAI LLM provider implementation for multi-LLM extensibility."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        client: Any | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._client = client

    @property
    def api_key(self) -> str:
        return self._api_key or getattr(settings, "OPENAI_API_KEY", "") or ""

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import importlib
                openai_mod = importlib.import_module("openai")
                async_openai_cls = getattr(openai_mod, "AsyncOpenAI")
                self._client = async_openai_cls(api_key=self.api_key)
            except ImportError:
                raise RuntimeError(
                    "The 'openai' package is required to use OpenAILLMProvider. "
                    "Install it with: pip install openai"
                )
        return self._client

    async def complete(
        self,
        prompt_or_messages: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        client = self._get_client()
        messages: list[dict[str, Any]]
        if isinstance(prompt_or_messages, list):
            messages = prompt_or_messages
        else:
            messages = [{"role": "user", "content": prompt_or_messages}]

        response_format = {"type": "json_object"} if kwargs.get("response_mime_type") == "application/json" else None
        completion_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if response_format:
            completion_kwargs["response_format"] = response_format

        response = await client.chat.completions.create(**completion_kwargs)
        return response.choices[0].message.content or ""


class MockLLMProvider:
    """Mock LLM provider for fast, deterministic unit testing without network/API keys."""

    def __init__(
        self,
        response: str | None = None,
        handler: Any | None = None,
    ):
        self.response = response
        self.handler = handler
        self.calls: list[str | list[dict[str, Any]]] = []

    async def complete(
        self,
        prompt_or_messages: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        self.calls.append(prompt_or_messages)
        if self.handler:
            if asyncio.iscoroutinefunction(self.handler):
                return await self.handler(prompt_or_messages)
            return self.handler(prompt_or_messages)
        return self.response if self.response is not None else "{}"


_default_llm_provider: LLMProvider | None = None


def get_llm_provider(name: str | None = None) -> LLMProvider:
    """Get the active LLM provider based on configuration or explicit name."""
    global _default_llm_provider
    if name is None and _default_llm_provider is not None:
        return _default_llm_provider

    provider_name = (name or getattr(settings, "LLM_PROVIDER", "gemini")).lower()
    if provider_name in ("openai", "chatgpt"):
        provider = OpenAILLMProvider()
    elif provider_name == "mock":
        provider = MockLLMProvider()
    else:
        provider = GeminiLLMProvider()

    if name is None:
        _default_llm_provider = provider
    return provider


def set_default_llm_provider(provider: LLMProvider | None) -> None:
    """Override default LLM provider."""
    global _default_llm_provider
    _default_llm_provider = provider
