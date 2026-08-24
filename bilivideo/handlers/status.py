"""Health-check (`/总结状态`) and cache-clear (`/总结清缓存`) handlers."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator

from ..api.endpoints import clear_video_info_cache, video_info_cache_size
from ..api.wbi import clear_wbi_cache
from ..access.control import is_admin
from ..core.logging import get_logger
from ..llm.provider import DisabledLLMProvider
from ..services import BiliVideoServices

logger = get_logger("BiliVideo/Status")


async def handle_status(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    """输出插件运行状态。优先使用无头浏览器渲染图片，失败则降级为纯文本。"""
    cfg = services.config

    # ---- 收集原有状态信息（保持与旧版完全一致） ----
    cookie_state = "✅ 已登录" if services.is_logged_in() else "❌ 未登录"
    if not cfg.enable_auto_push:
        scheduler_state = "关闭(配置)"
    elif services.scheduler is not None and services.scheduler.is_running():
        scheduler_state = "✅ 运行中"
    else:
        scheduler_state = "⚠️ 已启用但未运行"

    targets = await services.subscription_manager.get_push_targets()
    backends = ", ".join(services.renderer.available_backends) or "无 (将回退纯文本)"
    diagnostics = getattr(services.renderer, "backend_diagnostics", {})
    render_diag_lines = "\n".join(
        f"  - {name}: {reason}" for name, reason in diagnostics.items()
    )
    if render_diag_lines:
        render_diag_lines = f"\n🔎 渲染诊断:\n{render_diag_lines}"

    wkhtml = "✅" if shutil.which("wkhtmltoimage") or shutil.which("wkhtmltoimage.exe") else "❌"
    if shutil.which("ffmpeg") or shutil.which("ffmpeg.exe"):
        ffmpeg = "✅ 系统"
    else:
        try:
            import imageio_ffmpeg
            ffmpeg = "✅ 内建" if imageio_ffmpeg.get_ffmpeg_exe() else "❌"
        except Exception:
            ffmpeg = "❌"

    if isinstance(services.llm, DisabledLLMProvider):
        llm_state = "未配置"
    elif cfg.is_openai_compatible:
        llm_state = cfg.llm_model
    else:
        # 从 services.llm 实例中获取真实模型
        llm_instance = services.llm
        model_display = "astrbot 当前模型"

        try:
            # OpenAICompatibleProvider
            if hasattr(llm_instance, "_model"):
                model_display = llm_instance._model
            # AstrbotProvider 且有固定 provider_id
            elif hasattr(llm_instance, "provider_id") and llm_instance.provider_id:
                provider_id = llm_instance.provider_id
                model_display = f"astrbot:{provider_id}"
                # 尝试获取具体模型名称
                ctx = services.astrbot_context
                if ctx and hasattr(ctx, "get_provider_by_id"):
                    provider_obj = ctx.get_provider_by_id(provider_id)
                    if provider_obj and hasattr(provider_obj, "get_model"):
                        model_name = provider_obj.get_model()
                        if model_name:
                            model_display = f"astrbot:{provider_id} ({model_name})"
            # FallbackLLMProvider 包装器
            elif hasattr(llm_instance, "primary"):
                primary = llm_instance.primary
                if hasattr(primary, "provider_id") and primary.provider_id:
                    model_display = f"astrbot:{primary.provider_id}"
                elif hasattr(primary, "_model"):
                    model_display = primary._model
            else:
                # 兜底：从 AstrBot 全局上下文获取（保持兼容）
                ctx = services.astrbot_context
                if ctx and hasattr(ctx, "get_using_provider"):
                    provider = ctx.get_using_provider()
                    if provider:
                        if hasattr(provider, "get_model"):
                            model_name = provider.get_model()
                            if model_name:
                                model_display = f"astrbot ({model_name})"
                        elif hasattr(provider, "meta") and hasattr(provider.meta(), "id"):
                            provider_id = provider.meta().id
                            model_display = f"astrbot (provider: {provider_id})"
        except Exception as e:
            services.logger.debug(f"获取模型信息失败: {e}")

        llm_state = model_display

    yt_cookies = "有" if services.youtube_cookies.has() else "无"
    multi_state = f"{', '.join(cfg.enabled_platforms) or '无'} (YT cookies:{yt_cookies})"

    # 原始纯文本内容（用于降级）
    plain_body = (
        "🩺 biliVideo 状态\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📌 版本: 2.0.0\n"
        f"🔐 登录: {cookie_state}\n"
        f"🤖 LLM: {cfg.llm_provider} / {llm_state}\n"
        f"🎨 渲染后端: {backends}\n"
        f"🛠 系统工具: ffmpeg {ffmpeg}  wkhtmltoimage {wkhtml}\n"
        f"🔁 自动识别: {'开' if services.enable_miniapp_detect else '关'}\n"
        f"🌐 多平台: {multi_state}\n"
        f"📡 定时检查: {scheduler_state} (间隔 {cfg.check_interval_minutes} 分钟)\n"
        f"📋 推送目标: {len(targets)} 个\n"
        f"🗄  视频信息缓存: {video_info_cache_size()} 条\n"
        f"⏳ 用户冷却窗口: {cfg.user_cooldown_seconds} 秒\n"
        f"🖼 图片输出: {'on' if cfg.output_image else 'off'}  /  "
        f"自动分图: {'on' if cfg.enable_auto_split else 'off'}\n"
        f"💬 合并转发: {'on' if cfg.enable_forward_message else 'off'}\n"
        f"{render_diag_lines}\n"
    )

    # ---- 构建用于渲染的 Markdown 精美卡片（与纯文本信息一致） ----
    markdown_body = f"""# 🩺 biliVideo 状态报告

## 🔐 认证与登录
- **B站登录**: {cookie_state}
- **多平台支持**: {multi_state}

## 🤖 AI 模型
- **提供方**: {cfg.llm_provider}
- **当前模型**: {llm_state}

## 🎨 渲染引擎
- **可用后端**: {backends}
- **wkhtmltoimage**: {wkhtml}
- **ffmpeg**: {ffmpeg}
{render_diag_lines if render_diag_lines else ""}

## ⚙️ 运行配置
- **自动识别链接**: {'开' if services.enable_miniapp_detect else '关'}
- **定时检查**: {scheduler_state} (间隔 {cfg.check_interval_minutes} 分钟)
- **推送目标数**: {len(targets)}
- **视频信息缓存**: {video_info_cache_size()} 条
- **用户冷却窗口**: {cfg.user_cooldown_seconds} 秒
- **图片输出**: {'开启' if cfg.output_image else '关闭'}  **自动分图**: {'开启' if cfg.enable_auto_split else '关闭'}
- **合并转发**: {'开启' if cfg.enable_forward_message else '关闭'}

📌 **版本**: 2.0.0
"""

    # ---- 尝试使用无头浏览器渲染图片（使用线程池避免阻塞异步循环） ----
    try:
        logger.info("尝试使用无头浏览器渲染状态卡片...")
        image_paths = await services.renderer.render(
            markdown_body,
            base_filename="status",
        )

        if image_paths and len(image_paths) > 0:
            logger.info(f"状态卡片渲染成功: {image_paths[0]}")
            from astrbot.api.message_components import Image
            res = event.make_result().message("📊 插件状态报告已生成：")
            res.chain.append(Image.fromFileSystem(str(image_paths[0])))
            yield res
            return
    except Exception as e:
        logger.error(f"无头浏览器渲染状态卡片失败，将降级为纯文本。错误: {e}", exc_info=True)

    # ---- 降级：发送原始纯文本 ----
    yield event.plain_result(plain_body)


async def handle_clear_cache(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return
    before = video_info_cache_size()
    await clear_video_info_cache()
    await clear_wbi_cache()
    await services.orchestrator.clear_cache()
    yield event.plain_result(
        f"🧹 已清除缓存(视频信息 {before} 条 + WBI 密钥 + 总结结果)"
    )
