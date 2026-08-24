"""Subscription + push-target manager backed by `JsonStore` with Strategy Switches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .store import JsonStore


@dataclass(slots=True)
class Subscription:
    mid: str
    name: str
    last_bvid: str = ""
    auto_summary: bool = True
    auto_summary_user_id: str = ""
    filter_types: List[str] = field(default_factory=list)
    filter_regex: List[str] = field(default_factory=list)
    recent_ids: List[str] = field(default_factory=list)
    is_live: bool = False
    live_atall: bool = False
    last_live_start_ts: int = 0
    # 🌟 新增：精细化订阅类型白名单
    sub_types: List[str] = field(default_factory=lambda: ["视频"])

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Subscription:
        return cls(
            mid=str(raw.get("mid", "")),
            name=str(raw.get("name", "")),
            last_bvid=str(raw.get("last_bvid", "")),
            auto_summary=bool(raw.get("auto_summary", True)),
            auto_summary_user_id=str(raw.get("auto_summary_user_id", "") or ""),
            filter_types=list(raw.get("filter_types", [])),
            filter_regex=list(raw.get("filter_regex", [])),
            recent_ids=list(raw.get("recent_ids", [])),
            is_live=bool(raw.get("is_live", False)),
            live_atall=bool(raw.get("live_atall", False)),
            last_live_start_ts=int(raw.get("last_live_start_ts", 0)),
            sub_types=list(raw.get("sub_types", ["视频"])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mid": self.mid,
            "name": self.name,
            "last_bvid": self.last_bvid,
            "auto_summary": self.auto_summary,
            "auto_summary_user_id": self.auto_summary_user_id,
            "filter_types": self.filter_types,
            "filter_regex": self.filter_regex,
            "recent_ids": self.recent_ids,
            "is_live": self.is_live,
            "live_atall": self.live_atall,
            "last_live_start_ts": self.last_live_start_ts,
            "sub_types": self.sub_types,
        }

    def update_filters(self, filter_types: List[str], filter_regex: List[str], live_atall: bool) -> None:
        self.filter_types = list(filter_types)
        self.filter_regex = list(filter_regex)
        self.live_atall = live_atall

    def record_dynamic(self, dyn_id: str, history_limit: int) -> None:
        """记录一条新动态ID，维护 recent_ids 队列"""
        if not dyn_id:
            return
        if dyn_id in self.recent_ids:
            self.recent_ids.remove(dyn_id)
        self.recent_ids.insert(0, dyn_id)
        if len(self.recent_ids) > history_limit:
            del self.recent_ids[history_limit:]


@dataclass(slots=True, frozen=True)
class PushTarget:
    origin: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"origin": self.origin, "label": self.label}


_DEFAULT = {"subscriptions": {}, "push_targets": []}


class SubscriptionManager:
    """High-level subscription operations with advanced routing switches."""

    def __init__(self, data_dir: str) -> None:
        self._store = JsonStore(f"{data_dir.rstrip('/')}/subscriptions.json", default=_DEFAULT)

    # ------------------------------------------------------------------
    # subscriptions
    # ------------------------------------------------------------------
    async def add_subscription(
        self,
        origin: str,
        mid: str,
        name: str,
        auto_summary: bool = True,
        auto_summary_user_id: str = "",
        filter_types: Optional[List[str]] = None,
        filter_regex: Optional[List[str]] = None,
        live_atall: bool = False,
        sub_types: Optional[List[str]] = None,
    ) -> int:
        """添加新追踪或更新已有追踪的推送策略。

        返回值:
            1: 全新追踪建立成功
            2: 推送策略或过滤条件更新成功
            0: 追踪完全重复，未作改动
        """
        status = 0
        filter_types = filter_types or []
        filter_regex = filter_regex or []
        # 确定最终 sub_types
        valid_types = {"视频", "图文", "转发", "专栏", "直播", "抽奖", "转发抽奖", "动态"}
        if sub_types is None:
            # 未显式传入时，从 filter_types 提取有效类型，若为空则默认 ["视频"]
            sub_types = [t for t in filter_types if t in valid_types]
            if not sub_types:
                sub_types = ["视频"]
        else:
            # 传入的 sub_types 需要过滤有效类型
            sub_types = [t for t in sub_types if t in valid_types]
            if not sub_types:
                sub_types = ["视频"]

        def _mutate(data: dict[str, Any]) -> None:
            nonlocal status
            subs = data.setdefault("subscriptions", {}).setdefault(origin, {"up_list": []})
            up_list = subs.setdefault("up_list", [])

            for up in up_list:
                # 统一转为字符串比较
                if str(up.get("mid", "")) == str(mid):
                    changed = False
                    if up.get("auto_summary", True) != auto_summary:
                        up["auto_summary"] = auto_summary
                        changed = True
                    if str(up.get("auto_summary_user_id", "")) != auto_summary_user_id:
                        up["auto_summary_user_id"] = auto_summary_user_id
                        changed = True
                    if up.get("filter_types", []) != filter_types:
                        up["filter_types"] = filter_types
                        changed = True
                    if up.get("filter_regex", []) != filter_regex:
                        up["filter_regex"] = filter_regex
                        changed = True
                    if up.get("live_atall", False) != live_atall:
                        up["live_atall"] = live_atall
                        changed = True

                    existing_types = up.get("sub_types", ["视频"])
                    if isinstance(existing_types, str):
                        existing_types = [existing_types]
                    merged_types = list(dict.fromkeys([*existing_types, *sub_types]))
                    if up.get("sub_types", ["视频"]) != merged_types:
                        up["sub_types"] = merged_types
                        changed = True

                    status = 2 if changed else 0
                    return

            # 新建订阅
            up_list.append(
                Subscription(
                    mid=mid,
                    name=name,
                    last_bvid="",
                    auto_summary=auto_summary,
                    auto_summary_user_id=auto_summary_user_id,
                    filter_types=filter_types,
                    filter_regex=filter_regex,
                    live_atall=live_atall,
                    sub_types=sub_types,
                ).to_dict()
            )
            status = 1

        await self._store.mutate(_mutate)
        return status

    async def remove_subscription(self, origin: str, mid: str) -> bool:
        removed = False

        def _mutate(data: dict[str, Any]) -> None:
            nonlocal removed
            subs = data.get("subscriptions", {})
            entry = subs.get(origin)
            if not entry:
                return
            before = len(entry.get("up_list", []))
            entry["up_list"] = [up for up in entry.get("up_list", []) if up.get("mid") != mid]
            removed = len(entry["up_list"]) < before
            if not entry["up_list"]:
                subs.pop(origin, None)

        await self._store.mutate(_mutate)
        return removed

    async def get_subscriptions(self, origin: str) -> list[Subscription]:
        data = await self._store.read()
        entry = data.get("subscriptions", {}).get(origin) or {}
        return [Subscription.from_mapping(up) for up in entry.get("up_list", [])]

    async def get_subscription_count(self, origin: str) -> int:
        return len(await self.get_subscriptions(origin))

    async def get_all_subscriptions(self) -> dict[str, list[Subscription]]:
        """获取所有会话的所有订阅，按 origin 分组"""
        data = await self._store.read()
        out: dict[str, list[Subscription]] = {}
        for origin, entry in (data.get("subscriptions") or {}).items():
            out[origin] = [Subscription.from_mapping(up) for up in entry.get("up_list", [])]
        return out

    async def all_subscriptions(self) -> dict[str, list[Subscription]]:
        """Backward-compatible alias for callers using the older API name."""
        return await self.get_all_subscriptions()

    async def get_subscription(self, origin: str, mid: str) -> Optional[Subscription]:
        """获取特定订阅记录"""
        subs = await self.get_subscriptions(origin)
        for sub in subs:
            if sub.mid == mid:
                return sub
        return None

    async def update_subscription(self, origin: str, sub: Subscription) -> None:
        """全量更新订阅记录（包括 recent_ids、is_live 等所有字段）"""
        def _mutate(data: dict[str, Any]) -> None:
            entry = data.get("subscriptions", {}).get(origin)
            if not entry:
                return
            for up in entry.get("up_list", []):
                if up.get("mid") == sub.mid:
                    up.clear()
                    up.update(sub.to_dict())
                    return
        await self._store.mutate(_mutate)

    async def update_last_video(self, origin: str, mid: str, bvid: str) -> None:
        def _mutate(data: dict[str, Any]) -> None:
            entry = data.get("subscriptions", {}).get(origin)
            if not entry:
                return
            for up in entry.get("up_list", []):
                if up.get("mid") == mid:
                    up["last_bvid"] = bvid
                    return

        await self._store.mutate(_mutate)

    async def update_live_status(self, origin: str, mid: str, is_live: bool) -> None:
        """更新直播状态"""
        def _mutate(data: dict[str, Any]) -> None:
            entry = data.get("subscriptions", {}).get(origin)
            if not entry:
                return
            for up in entry.get("up_list", []):
                if up.get("mid") == mid:
                    up["is_live"] = is_live
                    return
        await self._store.mutate(_mutate)

    async def toggle_live_atall(self, origin: str, mid: str, enable: bool) -> bool:
        """切换指定订阅的直播@全体开关"""
        def _mutate(data: dict[str, Any]) -> None:
            entry = data.get("subscriptions", {}).get(origin)
            if not entry:
                return
            for up in entry.get("up_list", []):
                if up.get("mid") == mid:
                    up["live_atall"] = enable
                    return
        await self._store.mutate(_mutate)
        return True

    # ------------------------------------------------------------------
    # push targets
    # ------------------------------------------------------------------
    async def add_push_target(self, origin: str, label: str = "") -> bool:
        added = False

        def _mutate(data: dict[str, Any]) -> None:
            nonlocal added
            targets = data.setdefault("push_targets", [])
            if any(t.get("origin") == origin for t in targets):
                return
            targets.append(PushTarget(origin=origin, label=label).to_dict())
            added = True

        await self._store.mutate(_mutate)
        return added

    async def remove_push_target(self, identifier: str) -> bool:
        removed = False

        def _mutate(data: dict[str, Any]) -> None:
            nonlocal removed
            targets = data.get("push_targets", [])
            new_targets = [
                t for t in targets
                if t.get("label") != identifier and t.get("origin") != identifier
            ]
            removed = len(new_targets) < len(targets)
            data["push_targets"] = new_targets

        await self._store.mutate(_mutate)
        return removed

    async def get_push_targets(self) -> list[PushTarget]:
        data = await self._store.read()
        return [PushTarget(origin=t.get("origin", ""), label=t.get("label", ""))
                for t in (data.get("push_targets") or [])]

    async def get_push_origins(self) -> list[str]:
        return [t.origin for t in await self.get_push_targets()]
