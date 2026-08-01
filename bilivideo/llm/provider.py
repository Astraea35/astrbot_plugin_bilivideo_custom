"""LLM provider abstraction with Single Fallback Proxy Wrapper Support."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, Any

from ..core.config import PluginConfig
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
    """Return a concrete provider instance based on the config with single fallback routing."""

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

    # 2. 🌟 终极自愈重试：单平台故障兜底降级代理（彻底告别多选死锁冲突版）
    enable_fallback = bool(raw_llm_block.get("enable_fallback", False))
    backup_id = str(raw_llm_block.get("backup_provider_id", "")).strip()

    if enable_fallback and backup_id:
        from .astrbot_provider import AstrbotProvider
        from ..core.logging import get_logger
        
        fallback_logger = get_logger("BiliVideo/LLM/Fallback")
        # 实例化唯一的备用 AstrBot 稳定驱动通道
        fallback_provider = AstrbotProvider(astrbot_context, provider_id=backup_id)
        
        class FallbackLLMProvider:
            """内部容灾包装器：单级故障自愈重试拦截机"""
            def __init__(self, primary_prov: LLMProvider, fallback_prov: LLMProvider, log: Any) -> None:
                self.primary = primary_prov
                self.fallback = fallback_prov
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
                    # 优先冲主力模型
                    return await self.primary.chat(
                        prompt,
                        session_id=session_id,
                        image_urls=image_urls,
                        model=model,
                    )
                except Exception as exc:
                    # 主力模型崩溃时，静默捕获并启动兜底重试
                    self.logger.warning(
                        f"⚠️ 主力大模型请求异常: {exc}。正在启动动态容灾机制，"
                        f"自动切换到备用 AstrBot 模型通道【{backup_id}】执行二次重试..."
                    )
                    try:
                        return await self.fallback.chat(
                            prompt,
                            session_id=session_id,
                            image_urls=image_urls,
                            model=model,
                        )
                    except Exception as fallback_exc:
                        raise LLMError(
                            f"❌ 容灾失败：主力模型与唯一的备用兜底模型均告失败。\n"
                            f"主力模型报错: {exc}\n"
                            f"备用模型【{backup_id}】报错: {fallback_exc}"
                        ) from fallback_exc

        return FallbackLLMProvider(primary, fallback_provider, fallback_logger)

    return primary
