"""数据管理器：管理插件持久化数据，包括订阅记录、凭据、推送时间戳等"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional

from astrbot.api import logger
from astrbot.api.star import StarTools


class DataManager:
    """
    负责管理插件的持久化数据
    包括：订阅记录、B站凭据、最后成功推送时间戳等
    """

    def __init__(self, plugin_name: str = "astrbot_plugin_bilivideo_custom"):
        self.plugin_name = plugin_name
        self.data_dir = StarTools.get_data_dir(plugin_name=plugin_name)
        self.data_path = os.path.join(self.data_dir, "data.json")
        self._data: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        """加载数据文件"""
        if not os.path.exists(self.data_path):
            logger.info(f"数据文件不存在，将创建于: {self.data_path}")
            self._data = self._default_data()
            self._save_sync()
            return

        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载数据文件失败: {e}，使用默认数据")
            self._data = self._default_data()

    def _default_data(self) -> Dict[str, Any]:
        return {
            "credential": None,
            "last_success_sub_notify_ts": 0,
        }

    def _save_sync(self):
        """同步保存数据（内部使用）"""
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    async def save(self):
        """异步保存数据"""
        await asyncio.to_thread(self._save_sync)

    # ==================== 凭据管理 ====================

    def get_credential(self) -> Optional[Dict[str, Any]]:
        """获取保存的B站凭据"""
        return self._data.get("credential")

    async def set_credential(self, credential_data: Dict[str, Any]) -> None:
        """保存B站凭据"""
        self._data["credential"] = credential_data
        await self.save()

    async def clear_credential(self) -> None:
        """清除B站凭据"""
        if "credential" in self._data:
            self._data["credential"] = None
            await self.save()

    # ==================== 推送时间戳 ====================

    def get_last_success_sub_notify_ts(self) -> int:
        """获取上次成功推送订阅通知的时间戳"""
        return max(0, int(self._data.get("last_success_sub_notify_ts", 0)))

    async def set_last_success_sub_notify_ts(self, ts: int) -> None:
        """设置上次成功推送订阅通知的时间戳"""
        ts = max(0, int(ts))
        if self.get_last_success_sub_notify_ts() == ts:
            return
        self._data["last_success_sub_notify_ts"] = ts
        await self.save()