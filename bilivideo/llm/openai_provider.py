"""OpenAI-compatible HTTP LLM provider (DeepSeek, Moonshot, etc.) with Dynamic Timeout."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import aiohttp

from ..core.exceptions import LLMError, RateLimitError
from ..core.logging import get_logger

logger = get_logger("BiliVideo/LLM/OpenAI")


class OpenAICompatibleProvider:
    """POSTs to `<base>/chat/completions` with a Bearer token and dynamic timeout window."""

    def __init__(self, *, api_base: str, api_key: str, model: str, temperature: float, timeout_seconds: int = 300) -> None:
        self._url = f"{api_base.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        # 🌟 动态构建 aiohttp 超时时间，不再硬死在 120 秒
        self._request_timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def chat(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        image_urls: Sequence[str] = (),
        model: str = "",
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        content: str | list[dict[str, object]] = prompt
        if image_urls:
            content = [{"type": "text", "text": prompt}]
            content.extend(
                {"type": "image_url", "image_url": {"url": image_url}}
                for image_url in image_urls
                if image_url
            )
        payload = {
            "model": model or self._model,
            "temperature": self._temperature,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            # 🌟 传入动态生成的超时配置
            async with aiohttp.ClientSession(timeout=self._request_timeout) as session:
                async with session.post(self._url, json=payload, headers=headers) as resp:
                    body = await resp.text()
                    if resp.status == 429:
                        retry_after_raw = resp.headers.get("Retry-After")
                        try:
                            retry_after = float(retry_after_raw) if retry_after_raw else None
                        except (TypeError, ValueError):
                            retry_after = None
                        raise RateLimitError(retry_after=retry_after)
                    if resp.status != 200:
                        raise LLMError(f"OpenAI HTTP {resp.status}: {body[:200]}")
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise LLMError(f"OpenAI 请求大模型超时或失败 (当前限时 {self._request_timeout.total} 秒): {exc}") from exc

        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"OpenAI 响应体解析错误: {exc}") from exc
