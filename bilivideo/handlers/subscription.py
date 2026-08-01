"""Subscription-related command handlers - with filter support and index resolution."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

from ..access.control import is_admin, is_allowed, is_auto_summary_allowed
from ..api.endpoints import (
    get_latest_videos,
    get_uploader_info,
    get_video_info,
    search_uploader_by_name,
)
from ..core.exceptions import BiliVideoError
from ..core.utils import create_qrcode, image_to_base64
from ..parsing.url_extractor import extract_uid
from ..services import BiliVideoServices
from ..subscription.manager import Subscription
from ._render_helper import render_note_components, render_subscription_components
from ._send_helper import yield_note_response
from ._utils import parse_command_args, parse_sub_args, resolve_subscription_index, build_filter_desc

# 新增导入 Pillow 绘图函数和消息组件
from ..render.pillow_renderer import draw_subscription_image
try:
    from astrbot.api.message_components import Image
except ImportError:
    Image = None

# ================= 精细化类型白名单（支持动态） =================
VALID_TYPES = ["视频", "图文", "转发", "专栏", "直播", "抽奖", "转发抽奖", "动态"]


# ================= 🚀 handle_subscribe：智能解析 + 强制名字搜索 + 序号支持 + 开关控制 =================
async def handle_subscribe(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    """
    /订阅 <UID 或 UP主名称> [类型1] [类型2] ... [开|关]
    示例:
      /订阅 123               -> 默认订阅视频，自动总结开启
      /订阅 笔吧评测室 关      -> 订阅视频，关闭自动总结
      /订阅 笔吧评测室 动态 开 -> 订阅动态，开启自动总结
      /订阅 uid:123 直播 图文 关 -> 订阅多个类型，关闭自动总结
      /订阅 up名 51972 动态 开 -> 强制按名称搜索，开启自动总结
      /订阅 序号15 动态 关     -> 从订阅列表中取第15个UP主，订阅动态，关闭自动总结
      /订阅 原神 全部 开      -> 订阅所有具体类型，开启自动总结
    """
    if not is_allowed(event, config=services.config):
        yield event.plain_result("⛔ 你没有权限使用此插件")
        return

    msg_text = getattr(event, "message_str", "") or ""
    if not msg_text:
        yield event.plain_result(
            "❌ 请提供订阅目标\n"
            "格式: /订阅 <UID 或 UP主名称> [类型1] [类型2] ... [开|关]\n"
            "类型可选: " + "、".join(VALID_TYPES) + "\n"
            "末尾可加「开」或「关」控制是否自动总结，不写则默认开\n"
            "若UP主名称为纯数字，请使用: /订阅 up名 <数字名称>"
        )
        return

    # 1. 按空格切分原始消息
    parts = msg_text.split()
    if len(parts) < 2:
        yield event.plain_result("❌ 请输入 UID 或 UP主名称，例如：/订阅 123 或 /订阅 笔吧评测室 直播")
        return

    # ---------- 解析开关参数（从末尾提取） ----------
    auto_summary = True  # 默认开启
    # 检查最后一个参数是否为开关
    last_arg = parts[-1].strip()
    if last_arg in ("开", "开启", "on", "true", "1"):
        auto_summary = True
        parts = parts[:-1]  # 移除开关参数
    elif last_arg in ("关", "关闭", "off", "false", "0"):
        auto_summary = False
        parts = parts[:-1]  # 移除开关参数

    # 现在 parts 不包含开关参数

    # ---------- 解析目标与类型参数 ----------
    force_name_search = False
    # 检查是否使用 "up名" 前缀强制按名称搜索
    if len(parts) > 1 and parts[1] == "up名":
        target = parts[2].strip() if len(parts) > 2 else ""
        force_name_search = True
        type_args = parts[3:] if len(parts) > 3 else []
    else:
        # 处理 "序号" 和数字分开的情况（如 "/订阅 序号 1 视频"）
        if len(parts) > 2 and parts[1] == "序号" and parts[2].isdigit():
            target = f"序号{parts[2]}"
            type_args = parts[3:] if len(parts) > 3 else []
        else:
            target = parts[1].strip()
            type_args = parts[2:] if len(parts) > 2 else []

    # 清洗 "uid:" 前缀
    if target.lower().startswith("uid:"):
        target = target[4:].strip()

    # ---------- 解析订阅类型（支持 "全部"/"all"） ----------
    if not type_args:
        type_list = ["视频"]  # 默认
    else:
        if "全部" in type_args or "all" in type_args:
            type_list = [t for t in VALID_TYPES if t != "动态"]
            yield event.plain_result(f"🔍 检测到「全部」关键词，将订阅所有类型：{'、'.join(type_list)}")
        else:
            type_list = []
            for arg in type_args:
                if arg in VALID_TYPES:
                    type_list.append(arg)
                else:
                    services.logger.warning(f"忽略无效订阅类型：{arg}")
            if not type_list:
                type_list = ["视频"]  # 回退

    origin = getattr(event, "unified_msg_origin", "")
    auto_summary_denied = auto_summary and not is_auto_summary_allowed(event, config=services.config)
    if auto_summary_denied:
        auto_summary = False
    sender_id = str(getattr(event, "get_sender_id", lambda: "")() or "")

    # ---------- 解析目标（序号、UID、名称） ----------
    # 序号解析（格式：序号15 或 序号 15 已在上层合并）
    match = re.search(r"序号\s*(\d+)", target)
    skip_search = False
    if match:
        idx = int(match.group(1))
        subs = await services.subscription_manager.get_subscriptions(origin)
        if 1 <= idx <= len(subs):
            target_sub = subs[idx - 1]
            uid = target_sub.mid
            up_name = target_sub.name
            skip_search = True
            yield event.plain_result(f"🔍 从订阅列表中找到序号 {idx}：{up_name} (UID: {uid})")
        else:
            yield event.plain_result(f"❌ 序号 {idx} 无效，当前共有 {len(subs)} 个订阅")
            return

    # 非序号：按 UID 或名称
    if not skip_search:
        if target.isdigit() and not force_name_search:
            # 纯数字 → 作为 UID 直接使用
            uid = target
            up_name = "未知UP主"
            up_info = await get_uploader_info(services.http_client, uid)
            if up_info:
                up_name = getattr(up_info, "name", getattr(up_info, "uname", "未知UP主"))
            yield event.plain_result(f"🔍 检测到纯数字，作为 UID 直接获取: {uid}")
        else:
            # 非纯数字 或 强制名称搜索 → 按名称搜索
            yield event.plain_result(f"🔍 正在搜索 UP主：{target}...")
            up_info = await search_uploader_by_name(services.http_client, target)
            if not up_info:
                yield event.plain_result(f"❌ 找不到名为「{target}」的 UP主，请检查拼写或尝试直接输入 UID。")
                return
            uid = str(getattr(up_info, "mid", target))
            up_name = getattr(up_info, "name", getattr(up_info, "uname", target))

    # ---------- 检查订阅数量限制 ----------
    existing_sub = await services.subscription_manager.get_subscription(origin, uid)
    if existing_sub is None:
        count = await services.subscription_manager.get_subscription_count(origin)
        if count >= services.config.max_subscriptions:
            yield event.plain_result(f"❌ 已达到最大订阅数 ({services.config.max_subscriptions})")
            return

    # ---------- 添加订阅 ----------
    await services.subscription_manager.add_subscription(
        origin=origin,
        mid=uid,
        name=up_name,
        auto_summary=auto_summary,      # 用户指定的开关
        auto_summary_user_id=sender_id if auto_summary else "",
        filter_types=type_list,
        filter_regex=[],
        live_atall=False,
        sub_types=type_list,
    )

    # 记录最近视频（单独更新 last_bvid）
    videos = await get_latest_videos(services.http_client, uid, count=1)
    if videos:
        await services.subscription_manager.update_last_video(origin, uid, videos[0].bvid)

    type_str = "、".join(type_list)
    summary_status = "开启" if auto_summary else "关闭"
    access_notice = "\n⚠️ 当前不具备自动总结权限，已自动设为仅推送提醒。" if auto_summary_denied else ""
    yield event.plain_result(
        f"🎉 订阅成功！UP主: {up_name} (UID:{uid})\n"
        f"📋 订阅类型: {type_str}\n"
        f"🤖 自动总结: {summary_status}\n"
        f"💡 提示: 发送 /订阅 uid:{uid} 开/关 可切换自动总结{access_notice}"
    )


# ================= 优化 handle_unsubscribe：支持序号、UID、名称、up名前缀 =================
async def handle_unsubscribe(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not is_allowed(event, config=services.config):
        yield event.plain_result("⛔ 你没有权限使用此插件")
        return

    msg_text = getattr(event, "message_str", "") or ""
    if not msg_text:
        yield event.plain_result("❌ 请提供取消订阅的目标\n格式: /取消订阅 <序号/UID/名称> [类型]\n"
                                 "若UP主名称为纯数字，请使用: /取消订阅 up名 <数字名称>")
        return

    # 1. 按空格切分
    parts = msg_text.split()
    if len(parts) < 2:
        yield event.plain_result("❌ 请提供目标，例如：/取消订阅 序号1  或  /取消订阅 319785096")
        return

    # 2. 解析目标、类型、强制名称搜索标志
    force_name_search = False
    # 处理 "序号 1" 中间带空格的情况
    if parts[1] == "序号" and len(parts) > 2:
        target = f"序号{parts[2].strip()}"
        type_arg = parts[3].strip() if len(parts) > 3 else None
    # 处理 "up名" 前缀
    elif parts[1] == "up名" and len(parts) > 2:
        target = parts[2].strip()
        force_name_search = True
        type_arg = parts[3].strip() if len(parts) > 3 else None
    else:
        target = parts[1].strip()
        type_arg = parts[2].strip() if len(parts) > 2 else None

    # 3. 清洗 "uid:" 前缀
    if target.lower().startswith("uid:"):
        target = target[4:].strip()

    origin = getattr(event, "unified_msg_origin", "")

    # 4. 获取当前订阅列表
    subs = await services.subscription_manager.get_subscriptions(origin)
    if not subs:
        yield event.plain_result("📋 当前没有任何订阅。")
        return

    # 5. 根据目标类型查找订阅
    sub = None
    uid = None

    # 5.1 序号处理
    if target.startswith("序号"):
        idx_str = target.replace("序号", "").strip()
        if not idx_str.isdigit():
            yield event.plain_result("⚠️ 序号格式不正确，请输入正确的数字。例如：/取消订阅 序号1")
            return
        sub_index = int(idx_str)
        if sub_index < 1 or sub_index > len(subs):
            yield event.plain_result(f"⚠️ 序号 {sub_index} 无效，当前共有 {len(subs)} 个订阅。")
            return
        sub = subs[sub_index - 1]
        uid = sub.mid
    else:
        # 5.2 非序号：按 UID 或名称匹配
        # 如果目标是纯数字且不是强制名称搜索，则作为 UID 精确匹配
        if target.isdigit() and not force_name_search:
            # 直接在订阅列表中查找 mid == target
            for s in subs:
                if s.mid == target:
                    sub = s
                    uid = target
                    break
            if not sub:
                yield event.plain_result(f"⚠️ 未找到 UID {target} 的订阅")
                return
        else:
            # 按名称精确匹配（不区分大小写？使用精确）
            matched = [s for s in subs if s.name == target]
            if not matched:
                yield event.plain_result(f"⚠️ 未找到名称为「{target}」的订阅，请确认名称是否正确")
                return
            if len(matched) > 1:
                # 理论上名称唯一，但以防万一，提示使用UID
                yield event.plain_result(f"⚠️ 存在多个名称为「{target}」的订阅，请使用 UID 取消：/取消订阅 uid:{matched[0].mid}")
                return
            sub = matched[0]
            uid = sub.mid

    # 6. 执行取消操作（支持指定类型）
    if type_arg and type_arg in VALID_TYPES:
        current_types = getattr(sub, "sub_types", [])
        if type_arg in current_types:
            current_types.remove(type_arg)
        else:
            yield event.plain_result(f"⚠️ 该订阅并未订阅【{type_arg}】类型")
            return
        if not current_types:
            # 类型列表为空 → 删除整个订阅
            removed = await services.subscription_manager.remove_subscription(origin, uid)
            if removed:
                yield event.plain_result(f"✅ 已取消订阅 【{sub.name}】(UID:{uid}) 全部类型（因移除后为空）")
            else:
                yield event.plain_result(f"⚠️ 删除订阅失败")
        else:
            sub.sub_types = current_types
            await services.subscription_manager.update_subscription(origin, sub)
            yield event.plain_result(f"✅ 已取消订阅 【{sub.name}】(UID:{uid}) 的【{type_arg}】类型，剩余: {', '.join(current_types)}")
    else:
        # 未指定类型 → 取消全部
        removed = await services.subscription_manager.remove_subscription(origin, uid)
        if removed:
            yield event.plain_result(f"✅ 已取消订阅 【{sub.name}】(UID:{uid}) 全部类型")
        else:
            yield event.plain_result(f"⚠️ 取消订阅失败")


# ================= 以下为其他功能函数（未修改，保持原样） =================
async def handle_list_subscriptions(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    origin = getattr(event, "unified_msg_origin", "")
    if not is_allowed(event, config=services.config):
        yield event.plain_result("⛔ 你没有权限使用此插件")
        return

    subs = await services.subscription_manager.get_subscriptions(origin)
    if not subs:
        yield event.plain_result("📋 当前没有任何活跃的订阅。")
        return

    render_method = services.config.sub_list_render_method

    # 分支 1：浏览器截图（原有）
    if render_method == "browser":
        try:
            result = await render_subscription_components(services, subs)
            components, img_path = result
            if components:
                yield event.chain_result(components)
                return
        except Exception as e:
            services.logger.warning(f"订阅列表浏览器渲染失败: {e}")

    # 分支 2：Pillow 代码生图（新增）
    elif render_method == "direct":
        try:
            timestamp = int(time.time())
            img_dir = Path(services.data_dir) / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            img_path = img_dir / f"sub_list_{timestamp}.png"

            draw_subscription_image(services, subs, img_path)

            if Image is not None:
                yield event.chain_result([Image.fromFileSystem(str(img_path))])
                return
            else:
                services.logger.warning("Image 组件不可用，回退纯文本")
        except Exception as e:
            services.logger.warning(f"订阅列表代码生图失败，回退纯文本: {e}")

    # 分支 3：保底纯文本（修改显示 sub_types）
    lines = ["📋 当前订阅列表:", "━━━━━━━━━━━━━━━━━━━"]
    for i, sub in enumerate(subs, start=1):
        strategy = "🤖 自动总结" if sub.auto_summary else "🔔 仅推提醒"
        types = getattr(sub, "sub_types", ["视频"])
        if isinstance(types, str):
            types = [types]
        type_display = "、".join(types) if types else "（无）"
        filter_str = f" | 类型: {type_display}"
        if getattr(sub, "filter_regex", []):
            filter_str += f" | 正则: {', '.join(sub.filter_regex)}"
        if getattr(sub, "live_atall", False):
            filter_str += " | 🔴直播@全体"
        lines.append(f"  {i}. {sub.name} (UID:{sub.mid}) | {strategy}{filter_str}")

    lines.append(f"\n共 {len(subs)} 个订阅")
    lines.append("\n💡 提示：发送 `/取消订阅 序号1` (纯数字名字UP主使用：`/取消订阅 up名 51972`) 可指定类型取消")
    lines.append("💡 提示：发送 `/主页视频 序号1` 查看空间 (支持纯数字UID默认分流路径)")
    yield event.plain_result("\n".join(lines))


async def handle_check_updates(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not is_allowed(event, config=services.config):
        yield event.plain_result("⛔ 你没有权限使用此插件")
        return

    origin = getattr(event, "unified_msg_origin", "")
    subs = await services.subscription_manager.get_subscriptions(origin)
    if not subs:
        yield event.plain_result("📋 当前没有任何订阅，无法检查更新")
        return

    yield event.plain_result(f"🔍 正在执行手动检查 ({len(subs)}个订阅)...")

    found = 0
    for sub in subs:
        try:
            sub_types = getattr(sub, "sub_types", ["视频"])
            if isinstance(sub_types, str):
                sub_types = [sub_types]
            if "视频" not in sub_types:
                continue

            videos = await get_latest_videos(services.http_client, sub.mid, count=1)
            if not videos:
                continue
            latest = videos[0]
            if not latest.bvid or not latest.bvid.startswith("BV"):
                services.logger.warning(f"手动检查跳过无效 BV 号 {latest.bvid!r}: {sub.name}")
                continue
            if latest.bvid == sub.last_bvid:
                continue
            if not sub.last_bvid:
                services.logger.warning(
                    f"手动检查发现空 BV 游标，按新视频处理: {sub.name} ({sub.mid})"
                )

            await services.subscription_manager.update_last_video(origin, sub.mid, latest.bvid)
            found += 1
            yield event.plain_result(
                f"🔔 订阅的 [{sub.name}] 发布了新视频!\n"
                f"📺 {latest.title}\n"
                f"🔗 https://www.bilibili.com/video/{latest.bvid}"
            )

            if sub.auto_summary and not is_auto_summary_allowed(
                origin,
                config=services.config,
                sender_id=sub.auto_summary_user_id,
            ):
                sub.auto_summary = False
                sub.auto_summary_user_id = ""
                await services.subscription_manager.update_subscription(origin, sub)
                yield event.plain_result(f"⚠️ {sub.name} 的自动总结权限已失效，已改为仅推送提醒。")
            elif sub.auto_summary:
                try:
                    note = await services.orchestrator.generate(f"https://www.bilibili.com/video/{latest.bvid}")
                    components = await render_note_components(services, note.markdown)
                    async for resp in yield_note_response(services, event, components, video_info=note.video_info):
                        yield resp
                except BiliVideoError as exc:
                    yield event.plain_result(f"⚠️ 总结生成失败: {exc.user_message}")

            await asyncio.sleep(1)
        except Exception as exc:
            services.logger.warning(f"手动检查失败 {sub.name}: {exc}")

    yield event.plain_result(f"✅ 检查完成，共发现 {found} 个新视频。")


async def handle_live_atall_toggle(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return

    args = parse_command_args(getattr(event, "message_str", "") or "")
    if not args:
        yield event.plain_result("❌ 用法: /订阅开播@全体 <UID> [开/关]\n不写参数默认为关闭")
        return

    parts = args.split()
    uid_str = parts[0]
    if not uid_str.isdigit():
        yield event.plain_result("❌ UID 必须是数字")
        return

    enable = False
    if len(parts) > 1:
        if parts[1] == "开":
            enable = True
        elif parts[1] == "关":
            enable = False
        else:
            yield event.plain_result("❌ 参数错误，请输入「开」或「关」，不写则默认关闭")
            return

    origin = getattr(event, "unified_msg_origin", "")
    sub = await services.subscription_manager.get_subscription(origin, uid_str)
    if not sub:
        yield event.plain_result(f"❌ 未找到 UID {uid_str} 的订阅")
        return

    await services.subscription_manager.toggle_live_atall(origin, uid_str, enable)
    status = "开启" if enable else "关闭"
    yield event.plain_result(f"✅ 已{status} UID {uid_str} 的直播@全体功能")
