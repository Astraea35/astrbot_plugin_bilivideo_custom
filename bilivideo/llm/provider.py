"""LLM provider abstraction with ordered fallback models."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, Any

from ..core.config import PluginConfig, normalize_provider_ids
from ..core.exceptions import LLMError


class LLMProvider(Protocol):
    """Async interface for any LLM backend."""

    async def chat(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        image_urls: Sequence[str] = (),
        model: str = "",
    ) -> str:
        ...


class DisabledLLMProvider:
    """Provider placeholder used when startup some conditions aren't met."""

    def __init__(self, user_message: str) -> None:
        self.user_message = user_message

    async def chat(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        image_urls: Sequence[str] = (),
        model: str = "",
    ) -> str:
        raise LLMError("LLM provider disabled", user_message=self.user_message)


def build_provider(
    config: PluginConfig, 
    *, 
    astrbot_context: object | None, 
    provider_id: str = "",
    raw_config: dict | None = None
) -> LLMProvider:
    """Return a provider with optional ordered AstrBot fallback models."""

    raw_config_dict = raw_config or {}
    raw_llm_block = raw_config_dict.get("llm", {}) if isinstance(raw_config_dict.get("llm"), dict) else raw_config_dict
    
    llm_provider = str(raw_llm_block.get("llm_provider", "astrbot")).strip().lower()

    # 1. 创建主力大模型驱动 (Primary Provider)
    if llm_provider == "openai_compatible" or getattr(config, "is_openai_compatible", False):
        from .openai_provider import OpenAICompatibleProvider

        if not config.has_llm_credentials():
            primary = DisabledLLMProvider(
                "❌ AI 未配置:请填写 llm_api_base 和 llm_api_key,或切回 AstrBot 内置 LLM"
            )
        else:
            timeout_window = getattr(config, "processing_timeout", 300)
            primary = OpenAICompatibleProvider(
                api_base=config.llm_api_base,
                api_key=config.llm_api_key,
                model=config.llm_model,
                temperature=config.llm_temperature,
                timeout_seconds=timeout_window,
            )
    else:
        from .astrbot_provider import AstrbotProvider
        if llm_provider == "astrbot_custom":
            final_provider_id = str(raw_llm_block.get("astrbot_provider_id", "")).strip()
        else:
            final_provider_id = provider_id
        primary = AstrbotProvider(astrbot_context, provider_id=final_provider_id)

    enable_fallback = bool(raw_llm_block.get("enable_fallback", False))
    backup_ids = [
        backup_id for backup_id in normalize_provider_ids(raw_llm_block.get("backup_provider_id"))
        if backup_id != getattr(primary, "provider_id", "")
    ]

    if enable_fallback and backup_ids:
        from .astrbot_provider import AstrbotProvider
        from ..core.logging import get_logger
        
        fallback_logger = get_logger("BiliVideo/LLM/Fallback")
        fallback_providers = [AstrbotProvider(astrbot_context, provider_id=backup_id) for backup_id in backup_ids]
        
        class FallbackLLMProvider:
            """Try each selected backup once, stopping at the first success."""
            def __init__(self, primary_prov: LLMProvider, fallbacks: list[AstrbotProvider], log: Any) -> None:
                self.primary = primary_prov
                self.fallbacks = fallbacks
                self.logger = log

            async def chat(
                self,
                prompt: str,
                *,
                session_id: str | None = None,
                image_urls: Sequence[str] = (),
                model: str = "",
            ) -> str:
                try:
                    return await self.primary.chat(
                        prompt,
                        session_id=session_id,
                        image_urls=image_urls,
                        model=model,
                    )
                except Exception as exc:
                    errors = [f"主力模型报错: {exc}"]
                    last_error = exc
                    for fallback in self.fallbacks:
                        self.logger.warning(
                            f"⚠️ 模型请求失败: {last_error}。正在尝试备用模型【{fallback.provider_id}】..."
                        )
                        try:
                            # Keep each backup model's own AstrBot configuration.
                            return await fallback.chat(
                                prompt,
                                session_id=session_id,
                                image_urls=image_urls,
                            )
                        except Exception as fallback_exc:
                            errors.append(f"备用模型【{fallback.provider_id}】报错: {fallback_exc}")
                            last_error = fallback_exc
                    raise LLMError("❌ 主力模型与所有备用模型均失败。\n" + "\n".join(errors)) from last_error

        return FallbackLLMProvider(primary, fallback_providers, fallback_logger)

    return primary
