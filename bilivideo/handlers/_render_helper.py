"""Bridge between the renderer and the AstrBot message components.

`render_note_components()` returns either a list of `Image` components
(image mode) or a plain string (text mode), so handlers can decide how to
package the result without knowing about the renderer internals.
"""

from __future__ import annotations

import html
import io
import time
import uuid
from pathlib import Path
from typing import Any

from ..api.endpoints import get_featured_comments
from ..core.exceptions import PartialRenderError, RenderError
from ..core.logging import get_logger
from ..core.types import FeaturedComment, VideoInfo
from ..services import BiliVideoServices

# 从 render 目录载入已更新的本地降级渲染模块
from ..render.pillow_renderer import draw_subscription_image

logger = get_logger("BiliVideo/RenderHelper")

try:
    from astrbot.api.message_components import Image, Plain
except Exception:
    Image = None
    Plain = None


async def render_note_components(
    services: BiliVideoServices,
    markdown_text: str,
    *,
    video_info: VideoInfo | None = None,
    force_image: bool = False,
) -> list[Any] | str:
    """Render an AI summary, optionally enriching Bilibili image output."""
    try:
        return await _render_note_components_inner(services, markdown_text, video_info=video_info, force_image=force_image)
    except Exception as exc:
        logger.warning(f"render_note_components unexpected failure: {exc}", exc_info=True)
        return _render_fallback_text(markdown_text, f"渲染流程异常: {exc}")


async def _render_note_components_inner(
    services: BiliVideoServices,
    markdown_text: str,
    *,
    video_info: VideoInfo | None,
    force_image: bool,
) -> list[Any] | str:
    if not services.config.output_image and not force_image:
        return markdown_text

    render_markdown = await _decorate_summary_markdown(services, markdown_text, video_info)
    if Image is None:
        return _render_fallback_text(markdown_text, "AstrBot Image 组件不可用")

    base_filename = f"note_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    try:
        paths = await services.renderer.render(
            render_markdown,
            base_filename=base_filename,
            max_cards_per_image=services.config.max_cards_per_image,
            enable_split=services.config.enable_auto_split,
        )
        partial_failed_pages: list[int] = []
        partial_page_errors: dict[int, str] = {}
    except PartialRenderError as exc:
        logger.warning(f"partial render fallback text appended: {exc}")
        paths = exc.generated_paths
        partial_failed_pages = exc.failed_pages
        partial_page_errors = exc.page_errors
    except RenderError as exc:
        logger.warning(f"render fallback to text: {exc}")
        return _render_fallback_text(markdown_text, str(exc))

    components: list[Any] = []
    fallback_texts: list[str] = []
    invalid_paths: list[str] = []
    for path in paths:
        if not isinstance(path, Path):
            path = Path(path)
        try:
            usable = path.exists() and path.stat().st_size > 0
        except OSError:
            usable = False
        if not usable:
            invalid_paths.append(str(path))
            continue
        try:
            components.append(Image.fromFileSystem(str(path)))
        except Exception as exc:
            logger.warning(f"image component build failed for {path}: {exc}")
            fallback_texts.append(f"⚠️ 图片文件 {path.name} 发送失败，以下为文本兜底:\n\n{markdown_text}")
    if partial_failed_pages:
        error_details = "; ".join(
            f"第 {page} 页: {partial_page_errors.get(page, '未知原因')}"
            for page in partial_failed_pages
        )
        fallback_texts.append(
            f"⚠️ 第 {', '.join(str(p) for p in partial_failed_pages)} 页图片生成失败，"
            f"原因: {error_details}\n以下为完整文本兜底:\n\n" + markdown_text
        )
    if invalid_paths:
        logger.warning(f"renderer returned invalid image paths: {invalid_paths}")
        fallback_texts.append(f"图片路径无效: {', '.join(invalid_paths)}")
    if not components:
        reason = "\n".join(fallback_texts) if fallback_texts else "图片组件生成失败"
        return _render_fallback_text(markdown_text, reason)
    if fallback_texts:
        fallback_text = "\n\n".join(fallback_texts)
        if Plain is None:
            logger.warning("Plain component unavailable; cannot append render fallback text")
            return components
        components.append(Plain(fallback_text))
    return components


async def _decorate_summary_markdown(
    services: BiliVideoServices,
    markdown_text: str,
    video_info: VideoInfo | None,
) -> str:
    if video_info is None:
        return markdown_text

    comments: tuple[FeaturedComment, ...] = ()
    count = services.config.summary_featured_comment_count
    if count > 0:
        try:
            comments = await get_featured_comments(
                services.http_client,
                video_info,
                count=count,
                reply_count=services.config.summary_comment_reply_count,
            )
        except Exception as exc:
            services.logger.info(f"featured comments skipped: {exc}")
    return _build_summary_render_markdown(markdown_text, video_info, comments)


def _build_summary_render_markdown(
    markdown_text: str,
    video_info: VideoInfo,
    comments: tuple[FeaturedComment, ...] = (),
) -> str:
    """Add image-only video tags and comments without moving stats into the image."""

    category = html.escape(video_info.category or "B站视频")
    bvid = html.escape(video_info.bvid)
    tags = (
        '<div class="video-tags">'
        f'<span class="video-tag">{category}</span>'
        f'<span class="video-tag">时长 {_format_duration(video_info.duration)}</span>'
        f'<span class="video-tag">{bvid}</span>'
        '</div>'
    )
    if not comments:
        return f"{tags}\n\n{markdown_text.strip()}"

    items: list[str] = []
    for comment in comments:
        replies = "".join(
            '<div class="comment-reply">'
            f'<span class="comment-reply-name">{html.escape(reply.author_name)}</span>'
            f'<span class="comment-like">获赞 {reply.like}</span>'
            f'<div>{html.escape(_shorten(reply.content, 160))}</div>'
            '</div>'
            for reply in comment.replies
        )
        initial = html.escape((comment.author_name or "评")[:1])
        items.append(
            '<article class="featured-comment">'
            '<div class="comment-head">'
            f'<span class="comment-avatar">{initial}</span>'
            '<div>'
            f'<span class="comment-author">{html.escape(comment.author_name)}</span>'
            f'<span class="comment-like">获赞 {comment.like}</span>'
            '</div></div>'
            f'<p class="comment-body">{html.escape(_shorten(comment.content, 300))}</p>'
            f'<div class="comment-replies">{replies}</div>'
            '</article>'
        )
    heading = f'<h2>前排评论 <span class="comment-count">热门 {len(comments)} 条</span></h2>'
    section = f'{heading}<div class="featured-comments">{"".join(items)}</div>'
    return f"{tags}\n\n{markdown_text.strip()}\n\n{section}"


def _format_duration(seconds: int) -> str:
    seconds = max(int(seconds or 0), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _shorten(value: str, limit: int) -> str:
    text = (value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _render_fallback_text(markdown_text: str, reason: str) -> str:
    clean_reason = (reason or "未知原因").strip()
    return f"⚠️ 图片渲染失败，已退回纯文本\n原因: {clean_reason}\n\n{markdown_text}"


# ==============================================================================
# 🚀 订阅列表全功能分流大管线（支持双引擎 + 进阶秘籍铁律全同步）
# ==============================================================================

async def render_subscription_components(services: BiliVideoServices, subs: list) -> tuple[list[Any] | None, str | None]:
    """将订阅列表数据按照后台配置分流输出（已同步最新全参数容错铁律说明）"""
    if Image is None:
        logger.warning("render_subscription_components: Image 组件不可用")
        return None, None

    render_method = getattr(services.config, "sub_list_render_method", "direct")
    logger.info(f"订阅列表渲染启动。当前分流策略指向: '{render_method}'")

    # 1. browser 模式：渲染为 Markdown 表格，并追加全新指令铁律卡片 HTML
    if render_method == "browser":
        logger.info("分流指向 [browser]：正在唤醒 Playwright 无头内核渲染大盘表格...")
        
        md_lines = [
            "## 📢 biliVideo 侦听订阅大盘快照",
            "",
            "| 序号 | 监测目标名称 | 订阅类型 | 视频总结策略 |",
            "| :---: | :--- | :--- | :---: |"
        ]
        
        for i, up in enumerate(subs, start=1):
            up_name = getattr(up, "name", "未知博主")
            up_mid = getattr(up, "mid", "0")
            sub_types = getattr(up, "sub_types", ["视频"])
            if isinstance(sub_types, str):
                sub_types = [sub_types]
            # 类型图标映射 + 全部展示（修改处）
            type_icons = {
                "视频": "📺",
                "动态": "📰",
                "图文": "🖼️",
                "转发": "🔄",
                "专栏": "📝",
                "直播": "🔴",
                "抽奖": "🎁",
                "转发抽奖": "🎟️",
            }
            type_parts = [f"{type_icons.get(t, '')}{t}" for t in sub_types]
            type_display = "、".join(type_parts) if type_parts else "未设置"
            if getattr(up, "filter_regex", None):
                type_display += f" <br>🔍 过滤正则: `{','.join(up.filter_regex)}`"
            if getattr(up, "live_atall", False):
                type_display += " <br>🔴 开播 <b>@全体成员</b>"
            if "视频" in sub_types:
                strategy = "🤖 视频自动总结" if getattr(up, "auto_summary", False) else "🔔 仅推视频提醒"
            else:
                strategy = "📣 动态摘要按全局配置"
            md_lines.append(f"| {i} | **{up_name}**<br>`(UID: {up_mid})` | {type_display} | {strategy} |")
        
        # ================= 订阅操作指南 =================
        instruction_html = """
<div class="sub-instruction-card" style="
    margin: 20px 0 10px 0;
    padding: 16px;
    background-color: rgba(0, 0, 0, 0.03); 
    border-radius: 10px;
    border-left: 4px solid #00aeec;
    font-family: inherit; 
    box-sizing: border-box;
">
    <div style="font-size: 15px; font-weight: bold; color: inherit; margin-bottom: 8px; display: flex; align-items: center;">
        <span style="margin-right: 6px;">💡</span> 订阅常用指令
    </div>
    <div style="font-size: 14px; color: inherit; opacity: 0.85; line-height: 1.7;">
        <p style="margin: 4px 0;">
            <strong>• 查看与检查：</strong>
            <code>/订阅列表</code> 查看当前列表；<code>/检查更新</code> 立即检查订阅 UP 主的新视频。
        </p>
        <p style="margin: 4px 0;">
            <strong>• 订阅与视频总结：</strong>
            <code>/订阅 UID/名称/序号 [类型] [开/关]</code>；例：<code>/订阅 319785096 视频 关</code>，不写开关默认开启。
        </p>
        <p style="margin: 4px 0;">
            <strong>• 取消订阅：</strong>
            <code>/取消订阅 UID/名称/序号 [类型]</code>；不写类型则取消该 UP 的全部订阅类型。
        </p>
        <p style="margin: 4px 0;">
            <strong>• 空间操作：</strong>
            <code>/主页视频 序号1</code> 查看投稿；<code>/访问主页 序号1</code> 打开主页。
        </p>
        <p style="margin: 4px 0;">
            <strong>• 直播提醒：</strong>
            <code>/订阅开播@全体 UID [开/关]</code>；例：<code>/订阅开播@全体 319785096 开</code>。
        </p>
        <p style="margin: 4px 0;">
            <strong>• 定位规则：</strong>
            纯数字默认按 UID 检索；数字昵称使用 <code>/订阅 up名 51972</code>；<code>uid:</code> 前缀可省略。
        </p>
        <p style="margin: 4px 0;">
            <strong>• 支持类型：</strong>
            视频、动态、图文、转发、专栏、直播、抽奖、转发抽奖。
        </p>
        <p style="margin: 6px 0 0 0; color: #00aeec; font-weight: 500;">
            视频自动总结开关只控制视频推送；动态 AI 摘要按全局配置运行。
        </p>
    </div>
</div>
"""
        md_lines.append(instruction_html)
        md_lines.extend([
            "",
            f"📊 *当前共计管理 {len(subs)} / {getattr(services.config, 'max_subscriptions', 20)} 个监测目标*",
            "",
            "---",
            "💡 *使用「序号」关键字可以直接定位当前列表中的 UP 主，例如：`序号1`。*"
        ])
        
        markdown_text = "\n".join(md_lines)
        base_filename = f"subs_playwright_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        
        try:
            paths = await services.renderer.render(
                markdown_text,
                base_filename=base_filename,
                max_cards_per_image=services.config.max_cards_per_image,
                enable_split=services.config.enable_auto_split,
            )
            if paths and len(paths) > 0:
                first_path = Path(paths[0])
                logger.info(f"订阅列表通过 Playwright 网页生图成功: {first_path}")
                return [Image.fromFileSystem(str(first_path))], str(first_path)
        except Exception as browser_err:
            logger.error(f"Playwright 核心生图产生阻碍: {browser_err}，转为 Pillow 进行降级保障...", exc_info=True)

    # 2. direct 模式（Pillow）或降级
    cache_dir = Path("data/bili_video_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_path = cache_dir / f"subs_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"

    try:
        draw_subscription_image(services, subs, file_path)
        if file_path.exists() and file_path.stat().st_size > 0:
            logger.info(f"订阅列表图片渲染成功 (Pillow 机制平稳落地): {file_path}")
            return [Image.fromFileSystem(str(file_path))], str(file_path)
    except Exception as e:
        logger.error(f"Pillow 基础绘制引擎发生严重故障: {e}", exc_info=True)

    return None, None
