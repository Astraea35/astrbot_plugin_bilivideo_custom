"""LLM provider that delegates to AstrBot's configured Provider."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from ..core.exceptions import LLMError
from ..core.logging import get_logger

logger = get_logger("BiliVideo/LLM/AstrBot")


class AstrbotProvider:
    """Calls `context.get_using_provider().text_chat()`.

    The AstrBot framework hands us a `Context` object; we keep a weak
    handle so the provider object stays in sync with whatever the user
    selected in the AstrBot dashboard.
    """

    def __init__(self, astrbot_context: object | None, provider_id: str = "") -> None:
        self._context = astrbot_context
        self.provider_id = provider_id

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

        provider = None

        # 融合 1.0.5 修改版：多框架版本兼容的健壮查找逻辑
        if self.provider_id:
            if hasattr(self._context, "get_provider_by_id"):
                provider = self._context.get_provider_by_id(self.provider_id)
            elif hasattr(self._context, "get_provider"):
                provider = self._context.get_provider(self.provider_id)
            elif hasattr(self._context, "providers") and isinstance(self._context.providers, dict):
                if self.provider_id in self._context.providers:
                    provider = self._context.providers[self.provider_id]
                else:
                    # 最后的模糊兜底匹配
                    for k, v in self._context.providers.items():
                        if self.provider_id.lower() in k.lower():
                            provider = v
                            break

            if provider is None:
                logger.warning(
                    f"provider id '{self.provider_id}' not found; falling back to AstrBot current provider"
                )

        # 如果没有指定 ID 或者指定的 ID 在系统里没找到，回退到当前正在激活使用的默认模型
        if provider is None:
            if hasattr(self._context, "get_using_provider"):
                provider = self._context.get_using_provider()
            else:
                raise LLMError("AstrBot has no get_using_provider method available")

        if provider is None:
            raise LLMError("AstrBot has no LLM provider configured")

        try:
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
            raise LLMError(f"AstrBot text_chat failed: {exc}") from exc

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
                kwargs["image_url"] = image_urls[0]

        result = method(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
