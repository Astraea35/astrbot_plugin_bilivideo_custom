"""biliVideo plugin entry point.

This file is intentionally thin: it only registers the AstrBot Star
class, builds the service container, and forwards every command to the
matching handler in `bilivideo.handlers`. All non-trivial logic lives
inside the `bilivideo` sub-package.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .bilivideo import handlers
from .bilivideo.bili_services import BiliVideoServices
from .bilivideo.core.config import PluginConfig
from .bilivideo.core.data_manager import DataManager
from .bilivideo.core.logging import get_logger
from .bilivideo.handlers.scheduled_push import push_callback
from .bilivideo.services.dispatcher import SubscriptionNotificationDispatcher
from .bilivideo.services.listener import DynamicListener
from .bilivideo.services.renderer import DynamicCardRenderer
from .bilivideo.subscription.scheduler import CheckScheduler
from .bilivideo.tools import register_ai_tools


class BiliVideoPlugin(Star):
    """AstrBot plugin entry. Registers commands and delegates to handlers."""

    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)

        # 1. 加载配置
        plugin_config = PluginConfig.from_mapping(config or {})
        data_dir = str(StarTools.get_data_dir("astrbot_plugin_bilivideo_custom"))

        self._tag = get_logger("BiliVideo", debug_enabled=plugin_config.debug_mode)
        self._tag.info(f"loading plugin (data_dir={data_dir})")

        # 2. 数据管理器（用于存储凭据和推送时间戳）
        self.data_manager = DataManager(plugin_name="astrbot_plugin_bilivideo_custom")

        # 3. 组装核心服务层（注入 data_manager）
        self._services = BiliVideoServices(
            config=plugin_config,
            data_dir=data_dir,
            astrbot_context=context,
            raw_config=config,
            data_manager=self.data_manager,
        )

        # 4. 从数据管理器恢复凭据
        saved_credential = self.data_manager.get_credential()
        if saved_credential:
            self._services.update_cookies(saved_credential)
            self._tag.info("已从数据管理器恢复B站凭据")

        # 5. 启动推送目标配置引导任务
        # 6. 挂载定时推送调度器（视频总结推送，原有功能）
        self._services.scheduler = CheckScheduler(
            self._services.subscription_manager,
            lambda origin, sub: push_callback(self._services, origin, sub),
            interval_seconds=plugin_config.check_interval_minutes * 60,
        )
        if plugin_config.enable_auto_push:
            self._services.scheduler.start()
            self._tag.info("视频推送调度器已启动")
        else:
            self._tag.info("视频推送调度器已禁用")

        # ==================== 动态订阅新增组件 ====================

        # 7. 创建通知分发器（支持重连静默）
        self.dispatcher = SubscriptionNotificationDispatcher(
            context=context,
            on_sent=self._on_subscription_notification_sent,
        )

        # 8. 创建动态卡片渲染器
        self.dynamic_renderer = DynamicCardRenderer(
            services=self._services,
            style=plugin_config.renderer_template,
        )

        # 9. 创建动态监听器（轮询动态和直播）
        self.dynamic_listener = DynamicListener(
            context=context,
            subscription_manager=self._services.subscription_manager,
            bili_client=self._services.http_client,
            dispatcher=self.dispatcher,
            config=plugin_config,
            renderer=self.dynamic_renderer,
            services=self._services,
        )

        # 10. 配置重连静默
        self._configure_reconnect_silent(plugin_config)

        # 11. 启动动态监听器后台任务
        self._dynamic_listener_task = asyncio.create_task(self.dynamic_listener.start())
        self._tag.info("动态订阅监听器已启动")

        # 12. 注册 AI Function-call 工具
        try:
            register_ai_tools(self._services, context)
        except Exception as exc:
            self._tag.warning(f"AI tool registration failed: {exc}")

        login_state = "logged in" if self._services.is_logged_in() else "no SESSDATA"
        logger.info(f"BiliVideo plugin ready ({login_state})")

    async def _on_subscription_notification_sent(self, _notification: object) -> None:
        """订阅通知发送成功回调，记录时间戳用于重连静默"""
        import time
        now_ts = int(time.time())
        await self.data_manager.set_last_success_sub_notify_ts(now_ts)

    def _configure_reconnect_silent(self, config: PluginConfig) -> None:
        """配置重连静默模式"""
        if not config.reconnect_silent:
            self.dispatcher.set_silent_until_ts(0)
            return

        last_success_ts = self.data_manager.get_last_success_sub_notify_ts()
        if last_success_ts <= 0:
            self._tag.info("重连静默未触发：缺少历史推送成功时间。")
            return

        import time
        now_ts = int(time.time())
        idle_secs = now_ts - last_success_ts
        threshold = config.reconnect_silent_threshold_secs

        if idle_secs <= threshold:
            self._tag.info(f"重连静默未触发：距上次成功推送仅 {idle_secs} 秒（阈值 {threshold} 秒）。")
            return

        uid_count = len(self.dynamic_listener._build_uid_targets())
        silent_duration = config.interval_secs + config.task_gap_secs * uid_count + 60
        silent_until_ts = now_ts + silent_duration
        self.dispatcher.set_silent_until_ts(silent_until_ts)
        self._tag.warning(f"检测到长时间未成功推送订阅通知（{idle_secs} 秒），进入静默模式 {silent_duration} 秒。")

    # ======================== 用户原生聊天命令绑定 ========================

    @filter.command("总结帮助", alias={"bvhelp", "总结help"})
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_help(self._services, event):
            yield resp

    @filter.command("识别开关", alias={"bvdetect", "detect_toggle", "切换识别"})
    async def cmd_toggle_detect(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_toggle_detect(self._services, event):
            yield resp

    @filter.command("总结状态", alias={"bvstat", "总结status", "插件状态"})
    async def cmd_status(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_status(self._services, event):
            yield resp

    @filter.command("总结清缓存", alias={"bvclear", "清缓存"})
    async def cmd_clear_cache(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_clear_cache(self._services, event):
            yield resp

    @filter.command("总结模型", alias={"bvmodel", "模型列表", "切换模型"})
    async def cmd_model(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_model(self._services, event):
            yield resp

    @filter.command("B站登录", alias={"bvlogin", "bili_login", "哔哩登录", "B站扫码登录", "扫码登录"})
    async def cmd_login(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_login(self._services, event):
            yield resp

    @filter.command("B站登出", alias={"bvlogout", "bili_logout", "哔哩登出"})
    async def cmd_logout(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_logout(self._services, event):
            yield resp

    @filter.command("YT登录", alias={"ytlogin", "yt登录", "油管登录", "youtube登录"})
    async def cmd_youtube_login(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_youtube_login(self._services, event):
            yield resp

    @filter.command("YT登出", alias={"ytlogout", "yt登出", "油管登出", "youtube登出"})
    async def cmd_youtube_logout(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_youtube_logout(self._services, event):
            yield resp

    @filter.command("总结", alias={"bv", "BiliVideo", "视频总结"})
    async def cmd_summary(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_summary(self._services, event):
            yield resp

    @filter.command("最新视频", alias={"latest"})
    async def cmd_latest_video(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_latest_video(self._services, event):
            yield resp

    @filter.command("订阅", alias={"sub", "subscribe", "关注UP"})
    async def cmd_subscribe(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_subscribe(self._services, event):
            yield resp

    @filter.command("取消订阅", alias={"unsub", "unsubscribe", "取关UP"})
    async def cmd_unsubscribe(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_unsubscribe(self._services, event):
            yield resp

    @filter.command("订阅列表", alias={"sublist", "subs", "订阅列表查看"})
    async def cmd_list_subs(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_list_subscriptions(self._services, event):
            yield resp

    @filter.command("检查更新", alias={"check", "手动检查"})
    async def cmd_check_updates(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_check_updates(self._services, event):
            yield resp

    @filter.command("推送列表", alias={"pushls", "push_list", "推送目标"})
    async def cmd_list_push(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_list_push(self._services, event):
            yield resp

    @filter.command("移除推送", alias={"rmpush", "remove_push", "删除推送"})
    async def cmd_remove_push(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_remove_push(self._services, event):
            yield resp

    # ========== 空间相关命令（带斜杠） ==========
    @filter.command("主页视频", alias={"主页"})
    async def cmd_space_view(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_space_view(self._services, event):
            yield resp

    @filter.command("访问主页", alias={"访主"})
    async def cmd_visit_homepage(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_visit_homepage(self._services, event):
            yield resp

    @filter.command("视频")
    async def cmd_video_select(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_video_select(self._services, event):
            yield resp

    @filter.command("跳页", alias={"页码"})
    async def cmd_space_page_jump(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_space_page_jump(self._services, event):
            yield resp

    @filter.command("下一页", alias={"下页"})
    async def cmd_next_page(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_space_page_turn(self._services, event, direction=1):
            yield resp

    @filter.command("上一页", alias={"上页"})
    async def cmd_prev_page(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_space_page_turn(self._services, event, direction=-1):
            yield resp

    # ==================== 新增命令 ====================

    @filter.command("bili_global_list", alias={"全局列表"})
    async def cmd_global_list(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_global_list(self._services, event):
            yield resp

    @filter.command("bili_global_del", alias={"全局删除"})
    async def cmd_global_del(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_global_del(self._services, event):
            yield resp

    @filter.command("bili_global_sub", alias={"全局订阅"})
    async def cmd_global_sub(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_global_sub(self._services, event):
            yield resp

    @filter.command("bili_card_style", alias={"卡片样式"})
    async def cmd_card_style(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_card_style(self._services, event):
            yield resp

    # ==================== 新增：单独切换 live_atall ====================

    @filter.command("订阅开播@全体", alias={"开播@全体", "直播@全体"})
    async def cmd_live_atall_toggle(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_live_atall_toggle(self._services, event):
            yield resp

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent) -> AsyncIterator[object]:
        async for resp in handlers.handle_auto_detect(self._services, event):
            yield resp

    # ======================== 生命周期 ========================

    async def terminate(self) -> None:
        if hasattr(self, "_dynamic_listener_task") and self._dynamic_listener_task:
            self._dynamic_listener_task.cancel()
            try:
                await self._dynamic_listener_task
            except asyncio.CancelledError:
                self._tag.info("动态监听器已取消")
            except Exception as e:
                self._tag.error(f"动态监听器停止异常: {e}")

        await self._services.shutdown()
        logger.info("BiliVideo plugin terminated")
