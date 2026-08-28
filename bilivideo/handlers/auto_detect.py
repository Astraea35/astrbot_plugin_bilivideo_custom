"""Automatic link recognition and metadata extraction handler for both Bilibili and Douyin."""

from __future__ import annotations

import json
import re
import time
import aiohttp
import yt_dlp
from datetime import datetime
from pathlib import Path
from collections.abc import AsyncIterator
from urllib.parse import urlparse, unquote

from ..access.control import is_allowed, is_auto_summary_allowed
from ..api.endpoints import get_video_info
from ..core.exceptions import BiliVideoError
from ..coolapk import extract_coolapk_url, fetch_coolapk_post
from ..zhihu import extract_zhihu_url
from ..messaging.builders import format_video_summary_lines
from ..parsing.message_router import (
    looks_like_quoted_message,
    parse_event,
    url_from_card,
    url_from_raw_payload,
)
from ..parsing.triggers import TriggerSet
from ..parsing.url_extractor import (
    extract_bvid,
    extract_long_url,
    extract_short_url,
    is_bilibili_domain,
)
from ..services import BiliVideoServices
from ._render_helper import render_note_components
from ._send_helper import yield_note_response

try:
    from astrbot.api.message_components import Image, Plain
except Exception:
    Image = Plain = None

# 🔗 核心正则拦截雷达组
BILI_RE = re.compile(r'(b23\.tv/[A-Za-z0-9]+|bilibili\.com/video/[A-Za-z0-9]+)')
DY_SHORT_RE = re.compile(r'(v\.douyin\.com/[A-Za-z0-9]+)')
DY_LONG_RE = re.compile(r'douyin\.com/video/(\d+)')
YT_RE = re.compile(r'https?://(?:www\.|m\.)?(?:youtube\.com/watch\?v=[A-Za-z0-9_-]+|youtu\.be/[A-Za-z0-9_-]+|youtube\.com/shorts/[A-Za-z0-9_-]+)', re.I)


def _extract_bili_url_from_raw(raw) -> str:
    """（保留完整原厂逻辑）递归提取QQ小程序/卡片消息中的B站链接"""
    if raw is None:
        return ""

    if isinstance(raw, dict):
        meta = raw.get("meta")
        if isinstance(meta, dict):
            for _key, val in meta.items():
                if isinstance(val, dict):
                    url = val.get("qqdocurl") or val.get("jumpUrl") or val.get("url")
                    if url and _is_bili_domain(url):
                        return url
        if raw.get("type") == "json":
            inner = raw.get("data", {})
            if isinstance(inner, dict):
                json_str = inner.get("data", "")
                if isinstance(json_str, str):
                    return _try_parse_json_for_url(json_str)
            elif isinstance(inner, str):
                return _try_parse_json_for_url(inner)

    if isinstance(raw, list):
        for seg in raw:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "json":
                inner = seg.get("data", {})
                if isinstance(inner, dict):
                    json_str = inner.get("data", "")
                    if isinstance(json_str, str):
                        url = _try_parse_json_for_url(json_str)
                        if url:
                            return url
                elif isinstance(inner, str):
                    url = _try_parse_json_for_url(inner)
                    if url:
                        return url
            url = _extract_bili_url_from_raw(seg)
            if url:
                return url

    if isinstance(raw, str):
        raw_str = raw.strip()
        if raw_str.startswith("{"):
            url = _try_parse_json_for_url(raw_str)
            if url:
                return url
        cq_match = re.search(r'\[CQ:json,data=(.*?)\]', raw_str, re.S)
        if cq_match:
            cq_data = cq_match.group(1)
            cq_data = cq_data.replace("&amp;", "&").replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]")
            url = _try_parse_json_for_url(cq_data)
            if url:
                return url
    return ""


def _try_parse_json_for_url(text: str) -> str:
    """（保留完整原厂逻辑）解析文本卡片内部的特定跳转URL"""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            meta = data.get("meta")
            if isinstance(meta, dict):
                for val in meta.values():
                    if isinstance(val, dict):
                        url = val.get("qqdocurl") or val.get("jumpUrl") or val.get("url")
                        if url and _is_bili_domain(url):
                            return url
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _is_bili_domain(url: str) -> bool:
    """（保留完整原厂逻辑）B站综合多域名过滤器"""
    if not url:
        return False
    url_lower = url.strip().lower()
    if not url_lower.startswith(("http://", "https://")):
        url_lower = "https://" + url_lower
    try:
        host = urlparse(url_lower).hostname or ""
        host = host.rstrip(".")
        bili_domains = ("bilibili.com", "b23.tv", "bili2233.cn", "bili22.cn", "bili23.cn", "bili33.cn")
        return any(host == d or host.endswith("." + d) for d in bili_domains)
    except Exception:
        return False


def _check_auto_summary_access(services: BiliVideoServices, event: object) -> bool:
    """Automatic detection summaries use the shared total-plus-auto policy."""
    return is_auto_summary_allowed(event, config=services.config)


def _find_key_recursive(data: any, key_name: str) -> any | None:
    """高级递归探测法：能穿透任何多态且复杂的嵌套字典树提取抖音元数据（仅供备用，本文件已不再使用）"""
    if isinstance(data, dict):
        if key_name in data:
            return data[key_name]
        for v in data.values():
            res = _find_key_recursive(v, key_name)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = _find_key_recursive(item, key_name)
            if res is not None:
                return res
    return None


def _format_num(num: any) -> str:
    """将超长数字格式化为更舒适的移动端阅读展现（如 1254300 -> 125.4w）"""
    try:
        n = int(num)
        if n >= 10000:
            return f"{round(n / 10000, 1)}w"
        return str(n)
    except Exception:
        return str(num)


def _cleanup_old_douyin_cache(cache_dir: Path, services: BiliVideoServices, max_age_seconds: int = 1800):
    """全自动物理擦除看门狗：定时融化历史死图片缓存，保持磁盘干净无堆积"""
    try:
        now = time.time()
        if cache_dir.exists():
            for f in cache_dir.glob("dy_thumb_*.png"):
                if f.is_file() and (now - f.stat().st_mtime > max_age_seconds):
                    f.unlink(missing_ok=True)
    except Exception as e:
        services.logger.warning(f"全自动清理旧抖音图片缓存发生异常: {e}")


async def fetch_douyin_metadata(services: BiliVideoServices, raw_url: str) -> dict | None:
    """抖音元数据获取 - 使用 yt-dlp 直接提取（与 /总结 同款引擎，稳定可靠）"""
    url = raw_url if "http" in raw_url else f"https://{raw_url}"
    video_id = "unknown"
    desc = "抖音短视频"
    author = "抖音用户"
    cover = ""
    likes = "0"
    comments = "0"
    collects = "0"
    shares = "0"

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,   # 获取完整元数据
            'skip_download': True,   # 不下载视频/音频
            'socket_timeout': 30,
        }
        # 尝试加载 cookies（如果下载器有提供）
        if hasattr(services, 'downloader') and services.downloader:
            cookiefile = services.downloader._cookiefile_for(url)
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                services.logger.warning(f"yt-dlp 未获取到任何信息: {url}")
                # 返回保底数据，让插件能继续走流程
                return {
                    "video_id": video_id,
                    "desc": f"抖音视频（请点击链接查看）",
                    "author": "抖音用户",
                    "cover": "",
                    "likes": "0",
                    "comments": "0",
                    "collects": "0",
                    "shares": "0",
                    "link": url
                }

            # 提取视频 ID
            video_id = info.get('id', 'unknown')

            # 提取标题
            desc = info.get('title', desc) or desc

            # 提取作者
            uploader = info.get('uploader', '')
            if uploader:
                author = uploader

            # 提取封面
            thumbnail = info.get('thumbnail', '')
            if thumbnail:
                cover = thumbnail

            # 提取统计数据（抖音字段可能不同）
            if info.get('like_count') is not None:
                likes = str(info.get('like_count'))
            elif info.get('like_cnt') is not None:
                likes = str(info.get('like_cnt'))

            if info.get('comment_count') is not None:
                comments = str(info.get('comment_count'))
            elif info.get('comment_cnt') is not None:
                comments = str(info.get('comment_cnt'))

            if info.get('repost_count') is not None:
                shares = str(info.get('repost_count'))
            elif info.get('share_count') is not None:
                shares = str(info.get('share_count'))

            if info.get('favorite_count') is not None:
                collects = str(info.get('favorite_count'))

            services.logger.info(f"yt-dlp 成功获取抖音元数据: 标题={desc[:30]}..., 作者={author}")

    except Exception as e:
        services.logger.warning(f"yt-dlp 获取抖音元数据失败: {e}")
        # 返回保底数据，让插件能继续走流程（至少发个卡片）
        return {
            "video_id": video_id,
            "desc": f"抖音视频（详情请点击链接查看）",
            "author": "抖音用户",
            "cover": "",
            "likes": "0",
            "comments": "0",
            "collects": "0",
            "shares": "0",
            "link": url
        }

    # 终极保底：如果封面或标题还是空的，填充占位
    if not desc or desc == "抖音短视频":
        desc = f"抖音视频 {video_id}"
    if not author or author == "抖音用户":
        author = "抖音用户"

    # 清理旧缓存
    cache_dir = Path("data/bili_video_cache")
    _cleanup_old_douyin_cache(cache_dir, services, max_age_seconds=1800)

    return {
        "video_id": video_id,
        "desc": desc,
        "author": author,
        "cover": cover,
        "likes": _format_num(likes),
        "comments": _format_num(comments),
        "collects": _format_num(collects),
        "shares": _format_num(shares),
        "link": f"https://www.douyin.com/video/{video_id}" if video_id != "unknown" else url
    }


async def handle_auto_detect(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    """主事件拦截总线"""
    ctx = parse_event(event)
    text = (ctx.plain_text or "").strip()
    cmd_text = text.lower()

    # ------------------ 链接自动探针总拦截阀门 ------------------
    if not services.enable_miniapp_detect:
        return

    if ctx.plain_text.strip().startswith("/"):
        return

    link_scan_text = " ".join(
        part for part in (ctx.plain_text, ctx.raw_message, ctx.json_card_text) if part
    )
    if ctx.has_at and not (
        TriggerSet.has_bilibili_hint(link_scan_text)
        or extract_coolapk_url(link_scan_text)
        or extract_zhihu_url(link_scan_text)
        or (YT_RE.search(link_scan_text) is not None)
    ):
        return

    if not is_allowed(event, config=services.config):
        return

    msg_str = getattr(event, "message_str", "") or ""
    coolapk_scan_text = " ".join(part for part in (msg_str, link_scan_text) if part)

    # ==================== 酷安动态识别 ====================
    coolapk_url = extract_coolapk_url(coolapk_scan_text)
    if coolapk_url:
        if not services.config.is_platform_enabled("coolapk"):
            return
        try:
            post = await fetch_coolapk_post(coolapk_url)
        except Exception as exc:
            services.logger.warning(f"酷安动态获取失败: {exc}")
            return

        # 1. 组装并发送自动识别卡片
        response_lines = ["📱 biliVideo 自动识别 · 酷安动态"]
        response_lines.append("━━━━━━━━━━━━━━━━━━━")
        if getattr(services.config, "detect_show_uploader", True) and post.author:
            response_lines.append(f"👤 作者: {post.author}")
        if getattr(services.config, "detect_show_pubtime", True) and post.created_at:
            response_lines.append(f"📅 发布时间: {post.created_at}")
        if getattr(services.config, "detect_show_stats", True) and (post.likes != "0" or post.comments != "0"):
            response_lines.append(f"📊 互动数据:  👍 {post.likes}  |  💬 {post.comments}")
        if getattr(services.config, "detect_show_desc", True) and post.content:
            desc_max = getattr(services.config, "detect_desc_max_len", 0)
            desc_text = post.content if desc_max <= 0 else post.content[:desc_max]
            response_lines.append(f"📝 动态正文: {desc_text}")
        if getattr(services.config, "detect_show_link", True) and post.url:
            response_lines.append(f"🔗 网页直达: {post.url}")

        chain = []
        if getattr(services.config, "detect_show_cover", True) and post.images and Image is not None:
            try:
                chain.append(Image.fromURL(post.images[0]))
            except Exception:
                pass
        if Plain is not None:
            chain.append(Plain("\n".join(response_lines)))
        if chain:
            yield event.chain_result(chain)

        # 2. 自动总结逻辑
        if services.config.detect_auto_summary:
            if not _check_auto_summary_access(services, event):
                return
            yield event.plain_result("⏳ 正在生成酷安动态总结...")
            try:
                note = await services.inflight.run("coolapk:" + post.feed_id, lambda: services.orchestrator.generate_coolapk(post))
                markdown = note.markdown
            except Exception as exc:
                yield event.plain_result(getattr(exc, "user_message", str(exc)))
                return
            components = await render_note_components(services, markdown, force_image=True)
            async for resp in yield_note_response(services, event, components, meta=post):
                yield resp
        return

    # ==================== 🛠️ 抖音多开关高融合全面适配识别 ====================
    dy_match = DY_SHORT_RE.search(msg_str) or DY_LONG_RE.search(msg_str)
    if dy_match:
        if not services.config.is_platform_enabled("douyin"):
            return

        raw_target_url = dy_match.group(1)
        data = await fetch_douyin_metadata(services, raw_target_url)
        if not data:
            return

        # ---------- 1. 组装并发送“自动识别卡片” (无论是否开启总结都会发送) ----------
        response_lines = ["🎵 biliVideo 自动识别 · 抖音短视频"]
        response_lines.append("━━━━━━━━━━━━━━━━━━━")
        
        if getattr(services.config, "detect_show_uploader", True):
            response_lines.append(f"👤 创作者: {data['author']}")
        if getattr(services.config, "detect_show_pubtime", True):
            response_lines.append(f"📅 识别时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if getattr(services.config, "detect_show_stats", True):
            stats_line = f"📊 数据互动:  ❤️ {data['likes']}  |  💬 {data['comments']}  |  ⭐ {data['collects']}  |  🔗 {data['shares']}"
            response_lines.append(stats_line)
        if getattr(services.config, "detect_show_desc", True):
            response_lines.append(f"📝 视频文案: {data['desc']}")
        if getattr(services.config, "detect_show_link", True):
            response_lines.append(f"🔗 网页直达: {data['link']}")

        # 缓存封面图到本地（解决防盗链）
        local_img_exists = False
        local_img_path = None
        cache_dir = Path("data/bili_video_cache")
        
        if getattr(services.config, "detect_show_cover", True) and data["cover"]:
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                local_img_path = cache_dir / f"dy_thumb_{data['video_id']}.png"
                img_headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(data["cover"], headers=img_headers, timeout=aiohttp.ClientTimeout(total=5.0)) as img_resp:
                        if img_resp.status == 200:
                            local_img_path.write_bytes(await img_resp.read())
                            local_img_exists = True
            except Exception as img_err:
                services.logger.warning(f"抖音封面缓存失败: {img_err}")

        # 发送卡片消息链
        chain = []
        if local_img_exists and Image is not None:
            chain.append(Image.fromFileSystem(str(local_img_path)))
        if Plain is not None:
            chain.append(Plain("\n".join(response_lines)))
        if chain:
            yield event.chain_result(chain)

        # ---------- 2. 核心总结逻辑：适配合并转发与权限校验 ----------
        if services.config.detect_auto_summary:
            if not _check_auto_summary_access(services, event):
                return
            
            yield event.plain_result("⏳ 正在生成抖音视频总结（此过程可能需要 1-3 分钟）...")
            try:
                note = await services.orchestrator.generate(data['link'])
            except Exception as exc:
                yield event.plain_result(getattr(exc, 'user_message', str(exc)))
                return

            components = await render_note_components(services, note.markdown, video_info=note.video_info)
            async for resp in yield_note_response(services, event, components, meta=data):
                yield resp
        return

    # ==================== 知乎回答 / 专栏 / 问题识别 ====================
    zhihu_url = extract_zhihu_url(coolapk_scan_text)
    if zhihu_url:
        if not services.config.is_platform_enabled("zhihu"):
            return
        try:
            post = await services.zhihu_client.fetch(zhihu_url)
        except Exception as exc:
            services.logger.warning(f"知乎内容获取失败: {exc}")
            return
        content_label = {"answer": "回答", "article": "专栏", "question": "问题"}.get(
            post.content_type, "内容"
        )
        response_lines = [f"📚 biliVideo 自动识别 · 知乎{content_label}"]
        response_lines.append("━━━━━━━━━━━━━━━━━━━")
        if getattr(services.config, "detect_show_uploader", True) and post.author:
            response_lines.append(f"👤 作者: {post.author}")
        if getattr(services.config, "detect_show_stats", True) and (post.voteup_count or post.comment_count):
            response_lines.append(f"📊 互动数据:  👍 赞同 {post.voteup_count}  |  💬 评论 {post.comment_count}")
        if getattr(services.config, "detect_show_desc", True) and post.content:
            desc_max = getattr(services.config, "detect_desc_max_len", 0)
            desc_text = post.content if desc_max <= 0 else post.content[:desc_max]
            response_lines.append(f"📝 正文简介: {desc_text}")
        if getattr(services.config, "detect_show_link", True) and post.source_url:
            response_lines.append(f"🔗 网页直达: {post.source_url}")

        chain = []
        if getattr(services.config, "detect_show_cover", True) and post.images and Image is not None:
            try:
                chain.append(Image.fromURL(post.images[0]))
            except Exception:
                pass
        if Plain is not None:
            chain.append(Plain("\n".join(response_lines)))
        if chain:
            yield event.chain_result(chain)

        if services.config.detect_auto_summary:
            if not _check_auto_summary_access(services, event):
                return
            yield event.plain_result(f"⏳ 正在生成知乎{content_label}总结...")
            try:
                note = await services.inflight.run(
                    "zhihu:" + post.content_type + ":" + post.content_id,
                    lambda: services.orchestrator.generate_zhihu(post),
                )
                markdown = note.markdown
            except Exception as exc:
                yield event.plain_result(getattr(exc, "user_message", str(exc)))
                return
            components = await render_note_components(services, markdown, force_image=True)
            async for resp in yield_note_response(services, event, components, meta=post):
                yield resp
        return

    # ==================== 🎥 YouTube 视频识别 ====================
    yt_match = YT_RE.search(coolapk_scan_text)
    if yt_match:
        if not services.config.is_platform_enabled("youtube"):
            return
        yt_url = yt_match.group(0)
        if services.config.detect_auto_summary:
            if not _check_auto_summary_access(services, event):
                return
            yield event.plain_result("⏳ 正在获取 YouTube 视频并生成总结...")
            try:
                note = await services.inflight.run(yt_url, lambda: services.orchestrator.generate(yt_url))
            except Exception as exc:
                yield event.plain_result(getattr(exc, "user_message", str(exc)))
                return
            components = await render_note_components(services, note.markdown, video_info=note.video_info)
            async for resp in yield_note_response(services, event, components, meta=note.video_info):
                yield resp
            return

    # ==================== 📺 哔哩哔哩原厂完整逻辑（100% 恢复） ====================
    triggers = TriggerSet(services.config.trigger_keywords)
    is_triggered = triggers.matches(ctx.plain_text)
    is_reply_msg = looks_like_quoted_message(ctx.raw_message, ctx.plain_text) or ctx.is_reply

    if is_reply_msg and not is_triggered:
        raw_scan_pool = f"{ctx.plain_text or ''}"
        if not ("b23.tv" in raw_scan_pool or "BV" in raw_scan_pool or "bilibili" in raw_scan_pool):
            return

    bvid = await _resolve_bvid(services, ctx, allow_full_text=not ctx.is_reply)
    if not bvid:
        return

    if not services.config.is_platform_enabled("bilibili"):
        return

    try:
        info = await get_video_info(services.http_client, bvid)
    except BiliVideoError as exc:
        services.logger.warning(f"video info fetch failed: {exc}")
        return

    chain = []
    if services.config.detect_show_cover and info.normalized_pic and Image is not None:
        chain.append(Image.fromURL(info.normalized_pic))
    if Plain is not None:
        chain.append(Plain("\n".join(format_video_summary_lines(info, config=services.config))))
    if chain:
        yield event.chain_result(chain)

    if services.config.detect_auto_summary:
        if not _check_auto_summary_access(services, event):
            return
        yield event.plain_result("⏳ 正在生成视频总结...")
        try:
            note = await services.inflight.run(bvid, lambda: services.orchestrator.generate(info.url))
        except BiliVideoError as exc:
            yield event.plain_result(exc.user_message)
            return
        components = await render_note_components(services, note.markdown, video_info=note.video_info)
        async for resp in yield_note_response(services, event, components, video_info=info):
            yield resp


async def _resolve_bvid(
    services: BiliVideoServices,
    ctx,
    *,
    allow_full_text: bool,
) -> str | None:
    """（保留完整原厂逻辑）从各种复杂的Payload、文本和卡片中提取Bvid"""
    if hasattr(ctx, 'raw_payload') and ctx.raw_payload:
        bili_url = _extract_bili_url_from_raw(ctx.raw_payload)
        if bili_url:
            bvid = extract_bvid(bili_url)
            if bvid:
                return bvid
            if "b23.tv" in bili_url:
                resolved = await services.http_client.follow_redirect(bili_url)
                if resolved:
                    bvid = extract_bvid(resolved)
                    if bvid:
                        return bvid

    bili_url = url_from_raw_payload(ctx.raw_payload)
    if not bili_url and ctx.json_card_text:
        bili_url = url_from_card(ctx.json_card_text)
        
    # Bug 纠偏：标准的字符串前缀边界判定，绝缘 SyntaxError
    if not bili_url and ctx.raw_message and ctx.raw_message.strip().startswith("{"):
        bili_url = url_from_card(ctx.raw_message)

    text_pool = ctx.plain_text or ""
    if allow_full_text and ctx.raw_message:
        cleaned_raw = ctx.raw_message.replace(r"\/", "/")
        text_pool = f"{text_pool} {cleaned_raw}"

    if bili_url:
        bvid = extract_bvid(bili_url)
        if bvid:
            return bvid
        if _is_bili_domain(bili_url):
            resolved = await services.http_client.follow_redirect(bili_url)
            if resolved:
                bvid = extract_bvid(resolved)
                if bvid:
                    return bvid

    bvid = extract_bvid(text_pool)
    if bvid:
        return bvid

    long_url = extract_long_url(text_pool)
    if long_url:
        bvid = extract_bvid(long_url)
        if bvid:
            return bvid

    short_url = extract_short_url(text_pool)
    if short_url:
        resolved = await services.http_client.follow_redirect(short_url)
        if resolved:
            return extract_bvid(resolved)

    return None
