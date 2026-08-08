"""Composition root for the plugin.

`BiliVideoServices` wires every component together and exposes a single
object that the AstrBot Star class can poke without knowing about the
underlying details.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from .access.cooldown import CooldownTracker
from .access.inflight import InflightDeduper
from .api.client import BilibiliHTTPClient
from .auth.cookies import CookieJar
from .auth.qrlogin import QRLoginService
from .auth.youtube_cookies import YouTubeCookieStore
from .core.config import PluginConfig
from .core.logging import get_logger
from .downloader.ytdlp_downloader import YtDlpDownloader
from .llm.provider import DisabledLLMProvider, LLMProvider, build_provider
from .render.wkhtml_renderer import WkHtmlRenderer
from .search import SearchService
from .subscription.manager import SubscriptionManager
from .subscription.scheduler import CheckScheduler
from .summarize.orchestrator import SummaryOrchestrator
from .transcription.bcut_provider import BCutTranscriber
from .transcription.pipeline import TranscriptPipeline


class BiliVideoServices:
    """All plugin-level singletons assembled in one place.

    Construction is intentionally synchronous so the Star class can build
    services in `__init__` and tests can mount everything without an event
    loop.
    """

    def __init__(
        self,
        *,
        config: PluginConfig,
        data_dir: str,
        astrbot_context: object | None = None,
        raw_config: dict | None = None,
        data_manager: object | None = None,  # 新增：用于 Cookie 同步
    ) -> None:
        self.logger = get_logger("BiliVideo", debug_enabled=config.debug_mode)
        self.config = config
        self.raw_config = raw_config if isinstance(raw_config, dict) else {}
        self.data_dir = data_dir
        self.astrbot_context = astrbot_context
        self.data_manager = data_manager  # 保存引用

        Path(data_dir).mkdir(parents=True, exist_ok=True)
        Path(data_dir, "images").mkdir(parents=True, exist_ok=True)

        # Auth + cookies
        self.cookies = CookieJar(data_dir)
        self.qrlogin = QRLoginService()

        # Networking
        self.http_client = BilibiliHTTPClient(self.cookies.get())

        # Download / transcription
        self.youtube_cookies = YouTubeCookieStore(data_dir)
        self.youtube_cookies_file = self.youtube_cookies.path
        self.downloader = YtDlpDownloader(
            data_dir=str(Path(data_dir) / "audio"),
            cookies=self.cookies.get(),
            youtube_cookies_file=self.youtube_cookies_file,
        )
        self.transcriber = BCutTranscriber()
        self.pipeline = TranscriptPipeline(self.downloader, self.transcriber)

        # AstrBot configuration is the sole persisted source of plugin settings.
        initial_provider_id = config.llm_provider_id
        self.llm: LLMProvider = build_provider(
            config,
            astrbot_context=astrbot_context,
            provider_id=initial_provider_id,
            raw_config=self.raw_config
        )
        if isinstance(self.llm, DisabledLLMProvider):
            self.logger.warning(
                "openai_compatible credentials missing; LLM disabled but plugin startup continues"
            )

        # Summary + render
        self.orchestrator = SummaryOrchestrator(
            config=config,
            llm=self.llm,
            pipeline=self.pipeline,
            http_client=self.http_client,
            data_dir=data_dir,
        )

        # 使用 WkHtmlRenderer（Playwright 驱动）
        self.renderer = WkHtmlRenderer(
            output_dir=str(Path(data_dir) / "images"),
            image_width=config.image_width,
            services=self
        )
        self.logger.info("Playwright 同步高清渲染内核已成功挂载。")

        # Search service for AI tools
        self.search_service = SearchService(
            data_dir=data_dir,
            http_client=self.http_client,
            pipeline=self.pipeline,
        )

        # Subscription / scheduler (callback wired later by Star)
        self.subscription_manager = SubscriptionManager(data_dir)
        self.scheduler: CheckScheduler | None = None
        self.dynamic_listener: object | None = None

        # Anti-spam
        self.cooldown = CooldownTracker(window_seconds=config.user_cooldown_seconds)
        self.inflight: InflightDeduper[str, object] = InflightDeduper()

        self.enable_miniapp_detect = config.enable_miniapp_detect

        # Track in-flight long jobs (e.g. AI search download)
        self._download_task: asyncio.Task | None = None

        # AI 服务故障自动重试机制
        orig_generate = self.orchestrator.generate

        async def generate_with_retry(url: str, *args, **kwargs):
            max_retries = 3
            delay_seconds = 180

            for attempt in range(1, max_retries + 1):
                try:
                    return await orig_generate(url, *args, **kwargs)
                except Exception as exc:
                    if attempt == max_retries:
                        raise exc
                    self.logger.warning(
                        f"🤖 触发 AI 服务高阶重试网闸：当前视频总结生成失败（原因: {exc}）。"
                        f"系统将在 3 分钟后执行第 {attempt}/{max_retries} 次硬性自动重试..."
                    )
                    await asyncio.sleep(delay_seconds)

        self.orchestrator.generate = generate_with_retry

    # ------------------------------------------------------------------
    # cookie/login helpers used by handlers
    # ------------------------------------------------------------------
    def update_cookies(self, cookies: dict[str, str] | None) -> None:
        """更新 cookies，同步持久化到 DataManager（兼容主类同步初始化）。"""
        if cookies is None:
            self.cookies.clear()
        else:
            self.cookies.save(cookies)
            # 同步更新 DataManager（如果存在）
            if self.data_manager is not None:
                try:
                    # 方式1：直接操作内部数据并触发保存（兼容大多数 AstrBot 版本）
                    if hasattr(self.data_manager, '_data'):
                        self.data_manager._data["credential"] = cookies
                    # 方式2：尝试调用同步保存方法
                    if hasattr(self.data_manager, '_save_sync'):
                        self.data_manager._save_sync()
                    elif hasattr(self.data_manager, 'save'):
                        self.data_manager.save()  # 某些实现中 save 是同步的
                except Exception as e:
                    self.logger.warning(f"同步凭据到 DataManager 失败: {e}")

        active = self.cookies.get()
        self.http_client.update_cookies(active)
        self.downloader.update_cookies(active)

    def is_logged_in(self) -> bool:
        return self.cookies.is_logged_in()

    # ------------------------------------------------------------------
    # download task helpers
    # ------------------------------------------------------------------
    def replace_download_task(self, task: asyncio.Task) -> None:
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
        self._download_task = task

    @property
    def download_task(self) -> asyncio.Task | None:
        return self._download_task

    async def shutdown(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._download_task
        await self.http_client.close()
