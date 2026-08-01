"""动态监听器：轮询B站动态和直播状态，解析并分发"""

from __future__ import annotations

import asyncio
import re
import time
import traceback
from collections import OrderedDict, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.message_components import AtAll, Image, Plain

from ..api import endpoints
from ..core.config import PluginConfig
from ..core.exceptions import LLMError
from ..core.constants import (
    VALID_FILTER_TYPES,
    LIVE_ATALL_OPTION,
    RECONNECT_SILENT_THRESHOLD_SECS,
    RECONNECT_SILENT_PADDING_SECS,
    RECENT_DYNAMIC_CACHE,
    CARD_TEMPLATES,
    DEFAULT_TEMPLATE,
)
from ..core.models import DynamicParseResult, RenderPayload
from ..core.utils import (
    create_qrcode,
    render_text_to_plain,
    parse_rich_text,
)
from ..subscription.manager import SubscriptionManager, Subscription
from ..llm.astrbot_provider import AstrbotProvider
from .dispatcher import SubscriptionNotificationDispatcher, SubscriptionNotification
from .renderer import DynamicCardRenderer


class DynamicListener:
    """动态监听器核心"""

    def __init__(
        self,
        context: Any,
        subscription_manager: SubscriptionManager,
        bili_client: Any,
        dispatcher: SubscriptionNotificationDispatcher,
        config: PluginConfig,
        renderer: DynamicCardRenderer,
        services: Any,
    ):
        self.context = context
        self.sub_manager = subscription_manager
        self.bili_client = bili_client
        self.dispatcher = dispatcher
        self.config = config
        self.renderer = renderer
        self.services = services

        self.interval_secs = config.interval_secs
        self.task_gap_secs = config.task_gap_secs
        self.dynamic_limit = config.dynamic_limit
        self.recent_cache_size = config.recent_dynamic_cache
        self.plain_push_template = config.plain_push_template
        self.plain_push_forward_template = config.plain_push_forward_template
        self.enable_ai_summary = config.enable_dynamic_ai_summary
        self.ai_summary_prompt = config.ai_summary_prompt

        self.render_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.render_cache_limit = 32
        self.ai_summary_cache: OrderedDict[str, str] = OrderedDict()
        self.ai_summary_cache_limit = 128
        self.dynamic_summary_provider = (
            AstrbotProvider(context, provider_id=config.dynamic_summary_provider)
            if config.dynamic_summary_provider
            else None
        )

        self.plain_actions = {
            "DYNAMIC_TYPE_AV": "投稿了新视频",
            "DYNAMIC_TYPE_ARTICLE": "发布了新专栏动态",
            "DYNAMIC_TYPE_DRAW": "发布了新图文动态",
            "DYNAMIC_TYPE_FORWARD": "转发了新动态",
            "DYNAMIC_TYPE_WORD": "发布了新动态",
        }

    async def start(self):
        """启动后台监听循环"""
        uid_states: Dict[int, float] = {}
        next_dispatch_at = 0.0

        while True:
            try:
                if self.bili_client is None or not self.bili_client.cookies.get("SESSDATA"):
                    logger.warning("Bilibili 凭据未设置，无法获取动态。请使用 /B站登录 登录。")
                    await asyncio.sleep(self.interval_secs)
                    continue

                uid_targets = await self._build_uid_targets()
                current_uids = set(uid_targets.keys())
                now = time.monotonic()

                for uid in list(uid_states):
                    if uid not in current_uids:
                        uid_states.pop(uid, None)

                for uid in current_uids:
                    uid_states.setdefault(uid, now)

                if not current_uids:
                    await asyncio.sleep(2)
                    continue

                due_uids = [uid for uid in current_uids if uid_states[uid] <= now]
                if not due_uids:
                    next_due_at = min(uid_states[uid] for uid in current_uids)
                    wait_secs = min(max(next_due_at - now, 0.2), 2.0)
                    await asyncio.sleep(wait_secs)
                    continue

                if now < next_dispatch_at:
                    wait_secs = min(max(next_dispatch_at - now, 0.2), 2.0)
                    await asyncio.sleep(wait_secs)
                    continue

                run_uid = min(due_uids, key=lambda uid: (uid_states[uid], uid))
                await self._run_uid_task(run_uid, uid_targets.get(run_uid, []))

                finished_at = time.monotonic()
                uid_states[run_uid] = finished_at + self.interval_secs
                next_dispatch_at = finished_at + self.task_gap_secs
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"UID任务池调度异常: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1)

    async def _build_uid_targets(self) -> Dict[int, List[Tuple[str, Subscription]]]:
        uid_targets = defaultdict(list)
        all_subs = await self.sub_manager.get_all_subscriptions()
        for origin, subs in all_subs.items():
            for sub in subs:
                try:
                    uid_int = int(sub.mid)
                except (TypeError, ValueError):
                    continue
                uid_targets[uid_int].append((origin, sub))
        return dict(uid_targets)

    async def _run_uid_task(self, uid: int, targets: List[Tuple[str, Subscription]]):
        if not targets:
            return

        # ====== 按需拉取：判断是否需要视频/动态数据 ======
        need_video = any(
            "视频" in getattr(sub, "sub_types", ["视频"])
            for _, sub in targets
        )
        need_dynamic = any(
            "动态" in getattr(sub, "sub_types", [])
            for _, sub in targets
        )
        need_dynamic_data = need_video or need_dynamic

        # 判断是否需要直播
        should_check_live = any(
            "live" not in sub.filter_types and "直播" in getattr(sub, "sub_types", ["视频"])
            for _, sub in targets
        )
        # ==============================================

        dyn = None
        if need_dynamic_data:
            dyn = await endpoints.get_latest_dynamics(self.bili_client, uid)

        live_room = None
        if should_check_live:
            live_room = await endpoints.get_live_info_by_uids(self.bili_client, [uid])

        for origin, sub in targets:
            try:
                await self._check_single_up(origin, sub, dyn, live_room, need_video, need_dynamic)
            except Exception as e:
                logger.error(f"处理订阅者 {origin} 的 UP主 {sub.mid} 失败: {e}")

    async def _check_single_up(
        self,
        origin: str,
        sub: Subscription,
        dyn: Optional[Dict[str, Any]],
        live_room: Optional[Dict[str, Any]],
        need_video: bool,
        need_dynamic: bool,
    ):
        uid = int(sub.mid)
        has_new_dynamic = False

        # ---- 动态/视频处理（仅在需要时） ----
        if dyn and (need_video or need_dynamic):
            try:
                result_list = self._parse_and_filter_dynamics(dyn, sub)  # 白名单过滤
                if result_list:
                    sent = 0
                    for result in reversed(result_list):
                        if result.has_payload():
                            if sent < self.dynamic_limit:
                                sent += 1
                                has_new_dynamic = True
                                await self._handle_new_dynamic(origin, result.payload, result.dyn_id, sub)
                            if result.dyn_id:
                                sub.record_dynamic(result.dyn_id, self.recent_cache_size)
                                # 🛠️ 核心修复：移除对 update_last_video 的调用，防止用动态ID污染视频去重
                        elif result.dyn_id:
                            sub.record_dynamic(result.dyn_id, self.recent_cache_size)
                            # 🛠️ 核心修复：同上，移除冲突调用

                    if has_new_dynamic:
                        await self.sub_manager.update_subscription(origin, sub)

            except Exception as e:
                logger.warning(f"解析动态失败 UID={uid}: {e}")

        # ---- 直播处理 ----
        allowed_types = getattr(sub, "sub_types", ["视频"])
        if "直播" not in allowed_types:
            return

        if "live" in sub.filter_types:
            return
        if live_room is not None and isinstance(live_room, dict):
            await self._handle_live_status(origin, sub, live_room)

    # ================ 动态解析核心 ================

    def _parse_and_filter_dynamics(self, dyn: Any, sub: Subscription) -> List[DynamicParseResult]:
        """使用白名单（sub_types）过滤动态，只保留用户订阅的类型（注意视频已从动态宏中剥离）"""
        if not dyn or not isinstance(dyn, dict):
            return []

        items = dyn.get("items")
        if not items or not isinstance(items, list):
            return []

        # ---------- 白名单 sub_types ----------
        allowed_types = getattr(sub, "sub_types", ["视频"])
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        else:
            allowed_types = list(allowed_types)
            
        # 🛑 修改：展开“动态”宏标签时，不再包含“视频”（视频由定时推送单独处理）
        if "动态" in allowed_types:
            allowed_types.extend(["图文", "转发", "专栏"])  # 移除 "视频"
            
        # 🛠️ 核心修复：将“视频”从动态监听白名单彻底剥离，交由定时视频推送器全权处理
        if "视频" in allowed_types:
            allowed_types.remove("视频")
            
        allowed_types = list(set(allowed_types))
        # -------------------------------------

        filter_regex = sub.filter_regex if isinstance(sub.filter_regex, list) else []
        uid = str(sub.mid)

        last = sub.last_bvid or ""
        recent_ids = sub.recent_ids if isinstance(sub.recent_ids, list) else []
        known_ids = {x for x in ([last] + recent_ids) if x}

        result_list: List[DynamicParseResult] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            if "modules" not in item:
                continue

            module_tag = item.get("modules", {}).get("module_tag", {})
            if isinstance(module_tag, dict) and module_tag.get("text") == "置顶":
                continue

            dyn_id = item.get("id_str")
            if dyn_id and not isinstance(dyn_id, str):
                dyn_id = None

            if dyn_id and dyn_id in known_ids:
                break

            item_type = item.get("type")

            # ----- 确定动态类型（中文） -----
            determined_type = None
            if item_type == "DYNAMIC_TYPE_AV":
                determined_type = "视频"
            elif item_type == "DYNAMIC_TYPE_ARTICLE":
                determined_type = "专栏"
            elif item_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
                try:
                    major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
                    opus = major.get("opus", {}) if isinstance(major, dict) else {}
                    rich_nodes = opus.get("summary", {}).get("rich_text_nodes", []) if isinstance(opus, dict) else []
                    first_node_text = rich_nodes[0].get("text") if isinstance(rich_nodes, list) and rich_nodes and isinstance(rich_nodes[0], dict) else ""
                    determined_type = "抽奖" if first_node_text == "互动抽奖" else "图文"
                except Exception:
                    determined_type = "图文"
            elif item_type == "DYNAMIC_TYPE_FORWARD":
                try:
                    orig = item.get("orig", {})
                    if not isinstance(orig, dict): orig = {}
                    rich_nodes = (
                        orig.get("modules", {})
                        .get("module_dynamic", {})
                        .get("major", {})
                        .get("opus", {})
                        .get("summary", {})
                        .get("rich_text_nodes", [])
                    )
                    is_forward_lottery = (
                        isinstance(rich_nodes, list)
                        and len(rich_nodes) > 0
                        and isinstance(rich_nodes[0], dict)
                        and rich_nodes[0].get("text") == "互动抽奖"
                    )
                    desc = item.get("modules", {}).get("module_dynamic", {}).get("desc", {})
                    content_text = desc.get("text", "") if isinstance(desc, dict) else ""
                    is_lottery_result = bool(re.search(r"恭喜.*等\d+位同学中奖，已私信通知", content_text))

                    if is_forward_lottery:
                        determined_type = "转发抽奖"
                    elif is_lottery_result:
                        determined_type = "抽奖"
                    else:
                        determined_type = "转发"
                except Exception:
                    determined_type = "转发"

            # ----- 白名单过滤（视频动态因不在白名单而被跳过） -----
            if determined_type and determined_type not in allowed_types:
                if dyn_id:
                    result_list.append(DynamicParseResult.skip(dyn_id, f"白名单拦截: {determined_type}"))
                continue

            # ----- 调用 Handler -----
            if item_type == "DYNAMIC_TYPE_FORWARD":
                result = self._handle_forward_dynamic(item, dyn_id, uid, filter_regex)
            elif item_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
                result = self._handle_draw_or_word_dynamic(item, dyn_id, uid, filter_regex)
            elif item_type == "DYNAMIC_TYPE_AV":
                result = self._handle_video_dynamic(item, dyn_id, uid)
            elif item_type == "DYNAMIC_TYPE_ARTICLE":
                result = self._handle_article_dynamic(item, dyn_id, uid)
            else:
                result = DynamicParseResult.skip(dyn_id, "unsupported type") if dyn_id else DynamicParseResult.empty()
            result_list.append(result)

        logger.info(f"动态过滤完成: 订阅类型={allowed_types}, 过滤后动态数={len([r for r in result_list if r.has_payload()])}")
        return result_list

    def _match_filter_regex(self, text: Optional[str], filter_regex: List[str]) -> bool:
        if not text or not filter_regex:
            return False
        for pattern in filter_regex:
            try:
                if re.search(pattern, text):
                    return True
            except re.error:
                logger.warning(f"无效的正则表达式: {pattern}")
                continue
        return False

    def _handle_forward_dynamic(
        self, item: Dict, dyn_id: str, uid: str, filter_regex: List[str]
    ) -> DynamicParseResult:
        try:
            desc = item.get("modules", {}).get("module_dynamic", {}).get("desc", {})
            content_text = desc.get("text", "") if isinstance(desc, dict) else ""
        except (TypeError, KeyError):
            content_text = ""

        if self._match_filter_regex(content_text, filter_regex):
            return DynamicParseResult.skip(dyn_id, "regex") if dyn_id else DynamicParseResult.empty()

        render_data = self._build_render_data(item)
        render_data.uid = uid
        if dyn_id:
            render_data.url = f"https://t.bilibili.com/{dyn_id}"
            render_data.qrcode = create_qrcode(render_data.url)

        render_forward = self._build_render_data(item.get("orig", {}), is_forward=True)
        if render_forward.image_urls:
            render_forward.image_urls = [render_forward.image_urls[0]]
        render_data.forward = render_forward.to_forward_payload()
        return DynamicParseResult.deliver(render_data, dyn_id)

    def _handle_draw_or_word_dynamic(
        self, item: Dict, dyn_id: str, uid: str, filter_regex: List[str]
    ) -> DynamicParseResult:
        major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        if not isinstance(major, dict):
            major = {}
        if major.get("type") == "MAJOR_TYPE_BLOCKED":
            return DynamicParseResult.skip(dyn_id, "major_blocked") if dyn_id else DynamicParseResult.empty()

        opus = major.get("opus", {})
        if not isinstance(opus, dict):
            opus = {}
        summary = opus.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        summary_text = summary.get("text", "")

        if self._match_filter_regex(summary_text, filter_regex):
            return DynamicParseResult.skip(dyn_id, "regex") if dyn_id else DynamicParseResult.empty()

        render_data = self._build_render_data(item)
        render_data.uid = uid
        return DynamicParseResult.deliver(render_data, dyn_id)

    def _handle_video_dynamic(
        self, item: Dict, dyn_id: str, uid: str
    ) -> DynamicParseResult:
        render_data = self._build_render_data(item)
        render_data.uid = uid
        return DynamicParseResult.deliver(render_data, dyn_id)

    def _handle_article_dynamic(
        self, item: Dict, dyn_id: str, uid: str
    ) -> DynamicParseResult:
        major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        if not isinstance(major, dict):
            major = {}
        if major.get("type") == "MAJOR_TYPE_BLOCKED":
            return DynamicParseResult.skip(dyn_id, "major_blocked") if dyn_id else DynamicParseResult.empty()

        render_data = self._build_render_data(item)
        render_data.uid = uid
        return DynamicParseResult.deliver(render_data, dyn_id)

    # ================ 构建渲染数据 ================

    def _build_render_data(self, item: Dict[str, Any], is_forward: bool = False) -> RenderPayload:
        author_module = item.get("modules", {}).get("module_author") or {}
        payload = RenderPayload(
            name=str(author_module.get("name") or ""),
            avatar=str(author_module.get("face") or ""),
            pendant=str((author_module.get("pendant") or {}).get("image") or ""),
            type=str(item.get("type") or ""),
        )

        item_type = item.get("type")
        if item_type == "DYNAMIC_TYPE_AV":
            archive = item.get("modules", {}).get("module_dynamic", {}).get("major", {}).get("archive", {})
            payload.title = str(archive.get("title", ""))
            payload.image_urls = [str(archive.get("cover", ""))]
            desc = item.get("modules", {}).get("module_dynamic", {}).get("desc")
            topic = item.get("modules", {}).get("module_dynamic", {}).get("topic")
            payload.text = f"投稿了新视频<br>{parse_rich_text(desc or {}, topic or {})}" if desc else "投稿了新视频<br>"
            if not is_forward:
                payload.url = f"https://www.bilibili.com/video/{archive.get('bvid', '')}"
                payload.qrcode = create_qrcode(payload.url)

        elif item_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD", "DYNAMIC_TYPE_ARTICLE"):
            opus = item.get("modules", {}).get("module_dynamic", {}).get("major", {}).get("opus", {})
            summary = opus.get("summary", {})
            topic = item.get("modules", {}).get("module_dynamic", {}).get("topic")
            payload.summary = str(summary.get("text") or "")
            payload.text = parse_rich_text(summary or {}, topic or {})
            payload.title = str(opus.get("title") or "")
            payload.image_urls = [str(pic.get("url", "")) for pic in opus.get("pics", [])[:9]]
            if not is_forward:
                jump_url = str(opus.get("jump_url", ""))
                if jump_url:
                    payload.url = f"https:{jump_url}"
                    payload.qrcode = create_qrcode(payload.url)

        elif item_type == "DYNAMIC_TYPE_FORWARD":
            desc = item.get("modules", {}).get("module_dynamic", {}).get("desc")
            topic = item.get("modules", {}).get("module_dynamic", {}).get("topic")
            if desc:
                payload.text = parse_rich_text(desc or {}, topic or {})

        return payload

    def _dynamic_summary_prompt(self, payload: RenderPayload) -> str:
        parts = []
        if payload.title:
            parts.append(f"标题：{payload.title}")
        text = render_text_to_plain(payload.text)
        if text:
            parts.append(f"正文：{text}")
        if payload.forward:
            forward_text = render_text_to_plain(payload.forward.text)
            if forward_text:
                parts.append(f"转发内容：{forward_text}")
        if payload.url:
            parts.append(f"链接：{payload.url}")
        content = "\n".join(parts) or "该动态没有可提取的文字内容。"

        instruction = self.ai_summary_prompt.strip()
        if instruction:
            if "{content}" in instruction:
                return instruction.replace("{content}", content)
            return f"{instruction}\n\n{content}"
        return (
            "请用中文为以下 Bilibili 动态生成简洁、准确的摘要。"
            "不要编造动态中没有的信息，控制在 120 字以内。\n\n"
            f"{content}"
        )

    def _dynamic_summary_image_urls(self, payload: RenderPayload) -> tuple[str, ...]:
        if not self.config.enable_multimodal_dynamic_summary:
            return ()
        urls = list(payload.image_urls)
        if payload.forward:
            urls.extend(payload.forward.image_urls)
        return tuple(url for url in urls if url)[:4]

    async def _generate_dynamic_summary(
        self, payload: RenderPayload, dyn_id: Optional[str]
    ) -> str:
        if not self.enable_ai_summary:
            return ""

        cache_key = dyn_id or f"{payload.uid}:{payload.url}:{payload.title}:{payload.text}"
        cached = self.ai_summary_cache.get(cache_key)
        if cached is not None:
            self.ai_summary_cache.move_to_end(cache_key)
            return cached

        provider = self.dynamic_summary_provider or self.services.llm
        try:
            summary = await provider.chat(
                self._dynamic_summary_prompt(payload),
                session_id=f"BiliVideo_dynamic_{dyn_id or payload.uid}",
                image_urls=self._dynamic_summary_image_urls(payload),
                model=self.config.dynamic_summary_model,
            )
        except LLMError as exc:
            logger.warning(f"动态 AI 摘要生成失败: {exc}")
            return ""
        except Exception as exc:
            logger.warning(f"动态 AI 摘要生成异常: {exc}")
            return ""

        summary = str(summary).strip()
        if not summary:
            return ""
        self.ai_summary_cache[cache_key] = summary
        self.ai_summary_cache.move_to_end(cache_key)
        while len(self.ai_summary_cache) > self.ai_summary_cache_limit:
            self.ai_summary_cache.popitem(last=False)
        return summary

    # ================ 发送逻辑（二次校验） ================

    async def _handle_new_dynamic(self, origin: str, payload: RenderPayload, dyn_id: Optional[str], sub: Subscription):
        # ---- 二次白名单校验（与主过滤一气呵成） ----
        allowed_types = getattr(sub, "sub_types", ["视频"])
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        else:
            allowed_types = list(allowed_types)
        if "动态" in allowed_types:
            allowed_types.extend(["图文", "转发", "专栏"])  # 移除 "视频"
            
        # 🛠️ 核心修复：二次校验同步移除“视频”，确保万无一失
        if "视频" in allowed_types:
            allowed_types.remove("视频")
            
        allowed_types = set(allowed_types)

        raw_type = payload.type
        if raw_type == "DYNAMIC_TYPE_AV":
            chinese_type = "视频"
        elif raw_type == "DYNAMIC_TYPE_ARTICLE":
            chinese_type = "专栏"
        elif raw_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
            chinese_type = "图文"
        elif raw_type == "DYNAMIC_TYPE_FORWARD":
            chinese_type = "转发"
        else:
            chinese_type = None

        if chinese_type is None or chinese_type not in allowed_types:
            logger.info(f"二次拦截动态: 类型={raw_type}->{chinese_type}, 订阅允许={allowed_types}, 动态ID={dyn_id}")
            return

        logger.info(f"准备推送动态: 订阅类型={allowed_types}, 动态类型={chinese_type}, 动态ID={dyn_id}")

        ai_summary = await self._generate_dynamic_summary(payload, dyn_id)

        if self.config.output_image:
            img_path = await self.renderer.render_dynamic(payload)
            if img_path:
                with open(img_path, "rb") as image_file:
                    image_bytes = image_file.read()
                chain = [Image.fromBytes(image_bytes)]
                if payload.url:
                    chain.append(Plain(f"\n{payload.url}"))
                if ai_summary:
                    chain.append(Plain(f"\n\n🤖 AI 摘要\n{ai_summary}"))
                notification = SubscriptionNotification(
                    sub_user=origin,
                    chain_parts=chain,
                    category="dynamic",
                    dyn_id=dyn_id,
                )
                await self.dispatcher.publish(notification)
                return

        chain = self._compose_plain_push(payload)
        if ai_summary:
            chain.append(Plain(f"\n🤖 AI 摘要\n{ai_summary}"))
        notification = SubscriptionNotification(
            sub_user=origin,
            chain_parts=chain,
            category="dynamic",
            dyn_id=dyn_id,
        )
        await self.dispatcher.publish(notification)

    def _compose_plain_push(self, payload: RenderPayload) -> List[Any]:
        chain = []
        action = self.plain_actions.get(payload.type, "发布了新动态")
        header = f"📣 UP 主 「{payload.name}」 {action}:"
        lines = [header]
        if payload.title:
            lines.append(f"标题: {payload.title}")
        text = render_text_to_plain(payload.text)
        if text:
            lines.append(text)
        chain.append(Plain("\n".join(lines)))

        for pic in payload.image_urls:
            if pic:
                chain.append(Image.fromURL(pic))

        if payload.forward:
            chain.append(Plain("\n转发内容:"))
            fwd = payload.forward
            fwd_text = render_text_to_plain(fwd.text)
            if fwd_text:
                chain.append(Plain(fwd_text))
            for pic in fwd.image_urls:
                if pic:
                    chain.append(Image.fromURL(pic))

        if payload.url:
            chain.append(Plain(f"\n{payload.url}"))
        return chain

    def _resolve_platform_name(self, sub_user: str) -> str:
        if ":" in sub_user:
            adapter_id = sub_user.split(":", 1)[0]
            platform_inst = self.context.get_platform_inst(adapter_id)
            if platform_inst:
                return platform_inst.meta().name
        return ""

    # ================ 直播处理 ================

    async def _handle_live_status(self, origin: str, sub: Subscription, live_room: Dict[str, Any]):
        if not live_room or not isinstance(live_room, dict):
            return

        is_live_now = live_room.get("live_status", "") == 1
        if is_live_now and not sub.is_live:
            text = f"📣 你订阅的UP 「{sub.name}」 开播了！"
            await self._send_live_notice(origin, text, live_room, sub)
            sub.is_live = True
            await self.sub_manager.update_live_status(origin, sub.mid, True)
        elif not is_live_now and sub.is_live:
            text = f"📣 你订阅的UP 「{sub.name}」 下播了！"
            await self._send_live_notice(origin, text, live_room, sub)
            sub.is_live = False
            await self.sub_manager.update_live_status(origin, sub.mid, False)

    async def _send_live_notice(self, origin: str, text: str, live_room: Dict, sub: Subscription):
        chain = [Plain(text)]
        room_id = live_room.get("room_id", 0)
        if room_id:
            chain.append(Plain(f"\nhttps://live.bilibili.com/{room_id}"))
        notification = SubscriptionNotification(
            sub_user=origin,
            chain_parts=chain,
            category="live",
        )
        await self.dispatcher.publish(notification)
