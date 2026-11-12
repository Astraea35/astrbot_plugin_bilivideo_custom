"""LLM provider that delegates to AstrBot's configured Provider."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from ..core.exceptions import LLMError


class AstrbotProvider:
    """Use AstrBot's native generation API for a selected chat provider."""

    def __init__(self, astrbot_context: object | None, provider_id: str = "") -> None:
        self._context = astrbot_context
        self.provider_id = provider_id

    def get_provider(self) -> Any:
        if self._context is None:
            raise LLMError("AstrBot context unavailable")
        provider = (self._context.get_provider_by_id(self.provider_id) if self.provider_id
                    else self._context.get_using_provider())
        if provider is None:
            raise LLMError("selected AstrBot provider not found")
        return provider

    @property
    def cache_identity(self) -> str:
        try:
            provider = self.get_provider()
            settings = getattr(provider, "provider_config", {})
            return f"astrbot:{self.provider_id}:{provider.get_model()}:{settings.get('api_base', '')}"
        except (AttributeError, LLMError):
            return f"astrbot:{self.provider_id}"

    async def chat(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        image_urls: Sequence[str] = (),
        model: str = "",
    ) -> str:
        if self._context is None:
            raise LLMError("AstrBot context unavailable")

        try:
            generate = getattr(self._context, "llm_generate", None)
            if self.provider_id and callable(generate):
                kwargs: dict[str, Any] = {}
                if model:
                    kwargs["model"] = model
                response = await generate(
                    chat_provider_id=self.provider_id,
                    prompt=prompt,
                    session_id=session_id or "BiliVideo_plugin",
                    image_urls=list(image_urls),
                    **kwargs,
                )
            else:
                if self.provider_id:
                    provider = self._context.get_provider_by_id(self.provider_id)
                    if provider is None:
                        raise LLMError(f"AstrBot provider '{self.provider_id}' not found")
                else:
                    provider = self._context.get_using_provider()
                    if provider is None:
                        raise LLMError("AstrBot has no LLM provider configured")

                if image_urls:
                    response = await self._vision_chat(
                        provider,
                        prompt=prompt,
                        session_id=session_id or "BiliVideo_plugin",
                        image_urls=image_urls,
                        model=model,
                    )
                else:
                    response = await self._call_provider_method(
                        provider.text_chat,
                        prompt=prompt,
                        session_id=session_id or "BiliVideo_plugin",
                        model=model,
                    )
        except Exception as exc:  # pragma: no cover - relies on AstrBot
            raise LLMError(f"AstrBot LLM request failed: {exc}") from exc

        if hasattr(response, "completion_text"):
            return str(response.completion_text or "").strip()
        if isinstance(response, str):
            return response.strip()
        return str(response).strip()

    async def _vision_chat(
        self,
        provider: Any,
        *,
        prompt: str,
        session_id: str,
        image_urls: Sequence[str],
        model: str,
    ) -> Any:
        for method_name in ("image_chat", "multimodal_chat"):
            method = getattr(provider, method_name, None)
            if callable(method):
                return await self._call_provider_method(
                    method,
                    prompt=prompt,
                    session_id=session_id,
                    image_urls=image_urls,
                    model=model,
                )
        # Current AstrBot adapters accept images on text_chat itself.
        method = getattr(provider, "text_chat", None)
        if callable(method):
            parameters = inspect.signature(method).parameters
            if "image_urls" in parameters or any(
                item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            ):
                return await self._call_provider_method(
                    method, prompt=prompt, session_id=session_id, image_urls=image_urls, model=model,
                )
        raise LLMError("selected AstrBot provider does not support image input")

    @staticmethod
    async def _call_provider_method(
        method: Any,
        *,
        prompt: str,
        session_id: str,
        image_urls: Sequence[str] = (),
        model: str = "",
    ) -> Any:
        try:
            parameters = inspect.signature(method).parameters
            accepts_kwargs = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            parameters = {}
            accepts_kwargs = False

        def supports(name: str) -> bool:
            return accepts_kwargs or name in parameters

        kwargs: dict[str, Any] = {}
        if supports("prompt"):
            kwargs["prompt"] = prompt
        elif supports("text"):
            kwargs["text"] = prompt
        if supports("session_id"):
            kwargs["session_id"] = session_id
        if model and supports("model"):
            kwargs["model"] = model
        if image_urls:
            if supports("image_urls"):
                kwargs["image_urls"] = list(image_urls)
            elif supports("images"):
                kwargs["images"] = list(image_urls)
            elif supports("image_url"):
                if len(image_urls) != 1:
                    raise LLMError("selected AstrBot provider accepts only one image")
                kwargs["image_url"] = image_urls[0]
            else:
                raise LLMError("selected AstrBot provider does not accept image input")

        result = method(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
