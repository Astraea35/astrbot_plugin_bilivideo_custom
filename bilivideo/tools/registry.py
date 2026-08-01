"""AI function-call tools registry - 完整版，所有工具注册在这里"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from ..api.endpoints import search_videos
from ..core.logging import get_logger
from ..messaging.forward import build_video_forward_nodes
from ..services import BiliVideoServices

logger = get_logger("BiliVideo/Tools")

try:
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Image, Node, Nodes, Plain
    from astrbot.core.agent.tool import FunctionTool
except Exception:
    FunctionTool = None
    Image = Node = Nodes = Plain = MessageChain = None

# ==================== 新增工具导入 ====================
from .bgm_daily import BgmDailyTool
from .bgm_subject import BgmAdvancedSubjectSearchTool, BgmRecommendHotSubjectsTool
from .bili_hot_video import BiliSearchHotVideosTool
from .bili_user_dynamics import BiliUserDynamicsTool


def register_ai_tools(services: BiliVideoServices, astrbot_context: object) -> None:
    """Attach all AI tools to AstrBot's tool registry."""
    if FunctionTool is None or astrbot_context is None:
        logger.warning("AstrBot AI tools not available; skipping tool registration")
        return

    # ==================== 搜索列表工具（原 registry_legacy） ====================
    @dataclass
    class _SearchListTool(FunctionTool):
        name: str = "bilibili_search_list"
        description: str = (
            "搜索B站视频并返回视频列表(包含BV号)。"
            "仅返回搜索结果，不下载转写视频内容。"
            "如需下载转写视频内容，请将选中的BV号传给 bilibili_search_download 工具。"
        )
        parameters: dict = field(
            default_factory=lambda: {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "count": {"type": "integer", "description": "返回视频数量"},
                    "order": {
                        "type": "string",
                        "description": "totalrank/click/pubdate/dm/stow",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "0=全部 1=10m内 2=10-30m 3=30-60m 4=60m+",
                    },
                },
                "required": ["keyword"],
            }
        )

        async def call(self, _ctx, **kwargs):
            keyword = (kwargs.get("keyword") or "").strip()
            if not keyword:
                return "错误:请提供搜索关键词"
            count = int(kwargs.get("count") or services.config.default_count)
            order = kwargs.get("order") or "totalrank"
            duration = int(kwargs.get("duration") or 0)
            if order not in ("totalrank", "click", "pubdate", "dm", "stow"):
                order = "totalrank"
            if duration not in (0, 1, 2, 3, 4):
                duration = 0
            try:
                result = await search_videos(
                    services.http_client,
                    keyword,
                    page_size=count,
                    order=order,
                    duration=duration,
                )
            except Exception as exc:
                logger.error(f"search list failed: {exc}", exc_info=True)
                return f"搜索失败: {exc}"
            if not result or not result.results:
                return f"未找到与「{keyword}」相关的视频"
            payload = {
                "keyword": keyword,
                "total": result.num_results,
                "returned": len(result.results),
                "videos": [
                    {
                        "index": i + 1,
                        "bvid": v.bvid,
                        "title": v.title,
                        "author": v.author,
                        "play": v.play,
                        "duration": v.duration,
                        "url": v.url,
                    }
                    for i, v in enumerate(result.results)
                ],
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

    # ==================== 搜索下载工具（原 registry_legacy） ====================
    default_dl = services.config.default_download_count

    @dataclass
    class _SearchDownloadTool(FunctionTool):
        name: str = "bilibili_search_download"
        description: str = (
            "下载并转写视频内容。先通过 bilibili_search_list 搜索获取BV号，再调用此工具下载转写。"
            f"bv_list 支持一次传入多个BV号，建议每次下载 {default_dl} 个左右。"
            "重要:调用一次即可，后台自动处理所有视频，完成后会自动唤醒你继续处理。"
        )
        parameters: dict = field(
            default_factory=lambda: {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "文件夹名称"},
                    "bv_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "BV号数组，如 ['BV1xxx','BV2yyy']",
                    },
                },
                "required": ["folder_name", "bv_list"],
            }
        )

        async def call(self, ctx, **kwargs):
            folder_name = (kwargs.get("folder_name") or "").strip()
            bv_list = kwargs.get("bv_list") or []
            if not folder_name:
                return "错误:请提供文件夹名称"
            if not isinstance(bv_list, list) or not bv_list:
                return "错误:请提供BV号列表"

            try:
                event = ctx.context.event
            except Exception as exc:
                return f"错误:获取会话上下文失败 - {exc}"

            new_task = asyncio.create_task(
                _process_bv_list(
                    services=services,
                    astrbot_context=astrbot_context,
                    event=event,
                    folder_name=folder_name,
                    bv_list=list(bv_list),
                )
            )
            services.replace_download_task(new_task)
            return f"已开始下载转写 {len(bv_list)} 个视频，完成后会自动通知你。"

    # ==================== 新增工具 ====================
    bgm_daily_tool = BgmDailyTool(
        token=services.config.bangumi_token if hasattr(services.config, "bangumi_token") else ""
    )
    bgm_advanced_tool = BgmAdvancedSubjectSearchTool(
        token=services.config.bangumi_token if hasattr(services.config, "bangumi_token") else ""
    )
    bgm_recommend_tool = BgmRecommendHotSubjectsTool(
        token=services.config.bangumi_token if hasattr(services.config, "bangumi_token") else ""
    )
    hot_video_tool = BiliSearchHotVideosTool(bili_client=services.http_client)
    user_dynamics_tool = BiliUserDynamicsTool(
        bili_client=services.http_client,
        parse_dynamics=services.dynamic_listener._parse_and_filter_dynamics if hasattr(services, "dynamic_listener") else None,
    )

    add = getattr(astrbot_context, "add_llm_tools", None)
    if not callable(add):
        logger.warning("astrbot_context.add_llm_tools missing; tools not registered")
        return

    # 注册所有工具
    tools = [
        _SearchListTool(),
        _SearchDownloadTool(),
        bgm_daily_tool,
        bgm_advanced_tool,
        bgm_recommend_tool,
        hot_video_tool,
        user_dynamics_tool,
    ]
    for tool in tools:
        if tool is not None:
            add(tool)

    logger.info(f"registered {len(tools)} AI tools")


# ==================== 辅助函数 ====================

async def _process_bv_list(
    *,
    services: BiliVideoServices,
    astrbot_context: object,
    event: object,
    folder_name: str,
    bv_list: list[str],
) -> None:
    """批量处理BV列表（下载+转写）"""
    show_progress = services.config.search_show_progress
    umo = getattr(event, "unified_msg_origin", "")

    async def progress_callback(progress: dict) -> None:
        if not show_progress and not progress.get("is_last"):
            return
        completed = progress.get("completed", 0)
        total = progress.get("total", 0)
        title = progress.get("title", "")
        success = progress.get("success", True)
        prefix = "✅" if success else "❌"
        line = f"{prefix} 进度: {completed}/{total} - {title}"
        if progress.get("is_last"):
            line += (
                f"\n📝 转写完成("
                f"成功{progress.get('success_count', 0)}个，失败{progress.get('failed_count', 0)}个)，"
                f"即将为您分析..."
            )
        try:
            chain = MessageChain().message(line)
            await astrbot_context.send_message(umo, chain)
        except Exception as exc:
            logger.warning(f"progress dispatch failed: {exc}")

    try:
        result = await services.search_service.process_bv_list(
            bv_list=bv_list,
            folder_name=folder_name,
            max_concurrent=services.config.search_max_concurrent,
            prefer_subtitle=services.config.prefer_subtitle,
            quality=services.config.download_quality,
            subtitle_langs=services.config.subtitle_langs,
            progress_callback=progress_callback,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"search download task failed: {exc}", exc_info=True)
        await _safe_send_text(
            astrbot_context,
            umo,
            "❌ B站视频下载转写任务失败\n"
            f"📂 文件夹: {folder_name}\n"
            f"原因: {exc}",
        )
        return

    successful = [v for v in result.videos if v.success and v.transcript]
    if successful:
        try:
            await _send_combined_summary(
                services=services,
                astrbot_context=astrbot_context,
                event=event,
                successful=successful,
            )
            return
        except Exception as exc:
            logger.warning(f"combined summary path failed: {exc}", exc_info=True)

    completion = (
        "📝 B站视频下载转写任务已完成\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📂 文件夹: {folder_name}\n"
        f"✅ 成功: {result.success_count} 个\n"
        f"❌ 失败: {result.failed_count} 个\n"
        f"📁 文件位置: {result.folder_path}"
    )
    await _safe_send_text(astrbot_context, umo, completion)


async def _safe_send_text(astrbot_context: object, umo: str, text: str) -> None:
    if MessageChain is None:
        logger.warning(f"cannot send message, MessageChain unavailable: {text}")
        return
    try:
        await astrbot_context.send_message(umo, MessageChain().message(text))
    except Exception as exc:
        logger.warning(f"message dispatch failed: {exc}")


async def _send_combined_summary(
    *,
    services: BiliVideoServices,
    astrbot_context: object,
    event: object,
    successful: list,
) -> None:
    """发送综合总结（合并转发模式）"""
    from ..handlers._render_helper import render_note_components

    # 构建提示词
    transcript_text = "\n\n".join(
        f"【视频 {i + 1}】{v.info.title if v.info else v.bvid}\n{v.transcript}"
        for i, v in enumerate(successful)
    )
    summary_prompt = (
        "请为以下B站视频内容生成一份详细的结构化总结。\n\n"
        "要求:\n"
        "1. 使用 Markdown 格式\n"
        "2. 包含:核心观点、关键要点、时间线(如有)、总结\n"
        "3. 语言简洁清晰，突出重点\n"
        "4. 如果是多个视频，分别总结并加上视频标题\n\n"
        f"{transcript_text}"
    )

    note_text = await services.llm.chat(summary_prompt, session_id="BiliVideo_search")
    rendered = await render_note_components(services, note_text)
    umo = getattr(event, "unified_msg_origin", "")

    if services.config.enable_forward_message and successful and successful[0].info is not None:
        try:
            if len(successful) > 1:
                nodes = []
                bot_name = services.config.forward_bot_name
                bot_uin = services.config.forward_bot_uin
                nodes.append(
                    Node(
                        content=[Plain(f"📝 搜索结果总结(共 {len(successful)} 个视频)")],
                        name=bot_name,
                        uin=bot_uin,
                    )
                )
                for i, v in enumerate(successful, start=1):
                    info = v.info
                    parts = []
                    if info.normalized_pic:
                        parts.append(Image.fromURL(info.normalized_pic))
                    parts.append(
                        Plain(
                            f"📺 视频 {i}: {info.title}\n"
                            f"👤 UP主: {info.owner_name}\n"
                            f"🔗 {info.url}"
                        )
                    )
                    nodes.append(Node(content=parts, name=bot_name, uin=bot_uin))
                if isinstance(rendered, list):
                    for j, comp in enumerate(rendered):
                        label = "📝 AI 综合总结" if j == 0 else f"📝 AI 综合总结(第 {j + 1} 页)"
                        nodes.append(Node(content=[Plain(label), comp], name=bot_name, uin=bot_uin))
                else:
                    nodes.append(
                        Node(
                            content=[Plain(f"📝 AI 综合总结\n\n{rendered}")],
                            name=bot_name,
                            uin=bot_uin,
                        )
                    )
                forward = Nodes(nodes=nodes)
            else:
                forward = build_video_forward_nodes(
                    successful[0].info,
                    rendered,
                    bot_name=services.config.forward_bot_name,
                    bot_uin=services.config.forward_bot_uin,
                )
            chain = MessageChain()
            chain.chain.append(forward)
            await astrbot_context.send_message(umo, chain)
            return
        except Exception as exc:
            logger.warning(f"forward path failed: {exc}")

    # 纯文本降级
    if isinstance(rendered, list):
        for comp in rendered:
            chain = MessageChain()
            chain.chain.append(comp)
            await astrbot_context.send_message(umo, chain)
    else:
        await astrbot_context.send_message(umo, MessageChain().message(rendered))