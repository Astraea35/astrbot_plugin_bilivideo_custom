"""Gemini video transports, including proxies exposing the native Gemini endpoints."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp

from ..core.config import PluginConfig
from ..core.exceptions import BiliVideoError
from ..core.logging import get_logger
from .astrbot_provider import AstrbotProvider
from .openai_provider import OpenAICompatibleProvider

logger = get_logger("BiliVideo/Video")


class VideoCapabilityError(BiliVideoError):
    """The selected endpoint or model rejected a required media capability."""


@dataclass(frozen=True)
class GeminiVideoSettings:
    api_base: str
    api_key: str = field(repr=False)
    model: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    proxy: str = field(default="", repr=False)


@dataclass(frozen=True)
class NativeVideoResult:
    text: str
    processing_mode: str
    warning: str = ""
    usage: dict = field(default_factory=dict)


def resolve_gemini_settings(provider: object, config: PluginConfig) -> GeminiVideoSettings:
    """Reuse the selected provider without inventing native routes for OpenAI-only proxies."""
    provider = getattr(provider, "primary", provider)
    base = key = model = proxy = ""
    headers: dict[str, str] = {}
    native = False
    if isinstance(provider, AstrbotProvider):
        selected = provider.get_provider()
        raw = getattr(selected, "provider_config", {})
        native = raw.get("type") == "googlegenai_chat_completion"
        base = str(raw.get("api_base") or ("https://generativelanguage.googleapis.com" if native else ""))
        model = selected.get_model()
        key = selected.get_current_key() or ""
        headers = dict(getattr(selected, "request_headers", {}) or {})
        proxy = str(raw.get("proxy") or "")
    elif isinstance(provider, OpenAICompatibleProvider):
        base = provider._url.rsplit("/chat/completions", 1)[0]
        key, model = provider._api_key, provider._model
    if config.video_api_base:
        # An explicit override may point to another service; do not send the old service's credentials there.
        same_origin = urlsplit(base)[:2] == urlsplit(config.video_api_base)[:2]
        if not same_origin:
            key, headers, proxy = "", {}, ""
        base = config.video_api_base
        native = True
    if not native or not base:
        raise VideoCapabilityError("native Gemini endpoint unavailable", user_message="当前渠道未配置 Gemini 原生视频接口")
    key = config.video_api_key or key
    model = config.video_summary_model or model
    if not key or not model:
        raise VideoCapabilityError("missing video credentials or model", user_message="视频理解的接口密钥或模型未配置")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        raise VideoCapabilityError("invalid Gemini base URL", user_message="视频原生接口地址格式无效")
    return GeminiVideoSettings(base.rstrip("/"), key, model.removeprefix("models/"), headers, proxy)


class GeminiVideoProvider:
    def __init__(self, settings: GeminiVideoSettings, timeout: float) -> None:
        self.settings = settings
        self.timeout = timeout
        self.root = re.sub(r"/v1(?:beta)?$", "", settings.api_base)
        self.api = settings.api_base if re.search(r"/v1(?:beta)?$", settings.api_base) else self.root + "/v1beta"

    async def _json(self, session: aiohttp.ClientSession, method: str, url: str, **kwargs) -> dict:
        # Never echo response bodies, signed upload URLs, or credentials into errors.
        async with session.request(method, url, proxy=self.settings.proxy or None, allow_redirects=False, **kwargs) as response:
            if response.status not in {200, 201}:
                if response.status in {400, 404, 405, 415, 422, 501}:
                    raise VideoCapabilityError(
                        f"video endpoint HTTP {response.status}", user_message="当前渠道或模型不支持所请求的视频接口或参数"
                    )
                raise BiliVideoError(
                    f"video endpoint HTTP {response.status}",
                    user_message=f"视频接口请求失败（HTTP {response.status}）",
                )
            try:
                result = await response.json(content_type=None)
            except (ValueError, TypeError) as exc:
                raise VideoCapabilityError("invalid video response", user_message="视频接口返回了无法识别的结果") from exc
            if not isinstance(result, dict):
                raise VideoCapabilityError("invalid video response", user_message="视频接口返回了无法识别的结果")
            return result

    async def _upload(self, session: aiohttp.ClientSession, path: Path, mime_type: str) -> dict:
        async with session.post(
            self.root + "/upload/v1beta/files", proxy=self.settings.proxy or None,
            allow_redirects=False,
            headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                     "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size),
                     "X-Goog-Upload-Header-Content-Type": mime_type},
            json={"file": {"display_name": "bilivideo-summary"}},
        ) as response:
            if response.status != 200:
                if response.status in {400, 404, 405, 415, 422, 501}:
                    raise VideoCapabilityError("Files API unavailable", user_message="当前渠道不支持视频文件上传")
                raise BiliVideoError(f"video upload HTTP {response.status}", user_message="视频文件上传失败")
            upload_url = response.headers.get("X-Goog-Upload-URL", "")
        parsed = urlsplit(upload_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                or (urlsplit(self.root).scheme == "https" and parsed.scheme != "https")):
            raise VideoCapabilityError("missing resumable URL", user_message="当前渠道未返回可用的视频上传地址")
        # Resumable URLs carry their own upload token. Avoid forwarding provider headers across origins.
        upload_headers = {"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"}
        if urlsplit(upload_url)[:2] == urlsplit(self.root)[:2]:
            upload_headers.update(self.settings.headers)
            upload_headers["x-goog-api-key"] = self.settings.api_key
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as upload_session:
            with path.open("rb") as video_file:
                body = await self._json(upload_session, "POST", upload_url, data=video_file, headers=upload_headers)
        uploaded = body.get("file", {})
        if not isinstance(uploaded, dict) or not re.fullmatch(r"files/[\w-]+", str(uploaded.get("name", ""))):
            raise VideoCapabilityError("invalid uploaded file", user_message="当前渠道未返回可用的视频文件信息")
        return uploaded

    async def _interactions(self, session, file: dict, prompt: str, processing: str) -> NativeVideoResult:
        part = {"type": "video", "mime_type": file["mimeType"]}
        part["data" if "data" in file else "uri"] = file.get("data", file.get("uri"))
        if processing == "agentic":
            part["processing"] = "agentic"
        response = await self._json(session, "POST", self.api + "/interactions", json={
            "model": self.settings.model, "input": [part, {"type": "text", "text": prompt}], "store": False,
        })
        if response.get("status") in {"failed", "cancelled", "in_progress", "requires_action"}:
            raise BiliVideoError("video interaction incomplete", user_message="视频分析未能完成")
        steps = response.get("steps", [])
        text = "\n".join(
            str(content.get("text", "")) for step in steps if step.get("type") == "model_output"
            for content in step.get("content", []) if content.get("type") == "text"
        ).strip()
        warning = ""
        if processing == "agentic" and not any(step.get("type") == "processing_call" for step in steps):
            processing = "agentic_unverified"
            warning = "已请求 Agentic，但渠道未返回可确认该模式的处理记录"
        return NativeVideoResult(text, processing, warning, response.get("usage", {}))

    async def _static(self, session, file: dict, prompt: str) -> NativeVideoResult:
        video_part = ({"inlineData": {"data": file["data"], "mimeType": file["mimeType"]}}
                      if "data" in file else {"fileData": {"fileUri": file["uri"], "mimeType": file["mimeType"]}})
        response = await self._json(
            session, "POST", f"{self.api}/models/{quote(self.settings.model, safe='-._')}:generateContent",
            json={"contents": [{"role": "user", "parts": [
                video_part, {"text": prompt},
            ]}]},
        )
        candidates = response.get("candidates", [])
        text = "\n".join(
            str(part.get("text", "")) for part in (candidates[0].get("content", {}).get("parts", []) if candidates else [])
            if "text" in part and not part.get("thought")
        ).strip()
        return NativeVideoResult(text, "static", usage=response.get("usageMetadata", {}))

    async def summarize(self, file_path: str, prompt: str, config: PluginConfig) -> NativeVideoResult:
        try:
            return await asyncio.wait_for(self._summarize(file_path, prompt, config), timeout=self.timeout)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise BiliVideoError("video network failure", user_message="视频接口连接失败或请求超时") from exc
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise VideoCapabilityError("malformed video response", user_message="视频接口返回格式不兼容") from exc
        except OSError as exc:
            raise BiliVideoError("video file I/O failed", user_message="视频文件读取失败") from exc

    async def _summarize(self, file_path: str, prompt: str, config: PluginConfig) -> NativeVideoResult:
        # Auto serves a whole-video summary, for which Google's guidance prefers static processing.
        processing = "agentic" if config.video_processing_mode == "agentic" else "static"
        transport = config.video_native_transport
        headers = {**self.settings.headers, "x-goog-api-key": self.settings.api_key}
        uploaded = None
        uploaded_name = ""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                try:
                    path = Path(file_path)
                    mime_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
                    if path.stat().st_size > config.video_max_size_mb * 1024 * 1024:
                        raise BiliVideoError("video size limit", user_message="视频超过视频理解的文件大小上限")
                    try:
                        uploaded = await self._upload(session, path, mime_type)
                        uploaded_name = uploaded["name"]
                    except VideoCapabilityError:
                        # Some reverse proxies expose inference but not Files API. Keep base64 requests below 20 MiB.
                        if not config.video_fallback or path.stat().st_size > 12 * 1024 * 1024:
                            raise
                        uploaded = {"data": base64.b64encode(path.read_bytes()).decode("ascii"),
                                    "mimeType": mime_type, "state": "ACTIVE"}
                    while uploaded.get("state") == "PROCESSING":
                        await asyncio.sleep(2)
                        state = await self._json(session, "GET", f"{self.api}/{uploaded_name}")
                        uploaded.update(state)
                    if uploaded.get("state") != "ACTIVE" or not (uploaded.get("uri") or uploaded.get("data")):
                        raise BiliVideoError("video processing failed", user_message="视频上传后的预处理失败")
                    uploaded.setdefault("mimeType", mime_type)
                    try:
                        if processing == "agentic" or transport == "interactions":
                            if processing == "agentic" and transport == "generate_content":
                                raise VideoCapabilityError("Agentic requires Interactions", user_message="所选视频接口不支持 Agentic")
                            result = await self._interactions(session, uploaded, prompt, processing)
                        else:
                            result = await self._static(session, uploaded, prompt)
                    except VideoCapabilityError:
                        if not config.video_fallback or (processing == "static" and transport != "interactions"):
                            raise
                        result = await self._static(session, uploaded, prompt)
                        result = NativeVideoResult(result.text, "static", "请求的视频模式不可用，已改用 Static", result.usage)
                    if not result.text:
                        raise BiliVideoError("empty video output", user_message="视频模型未返回总结内容")
                    return result
                finally:
                    if uploaded_name:
                        try:
                            async with session.delete(
                                f"{self.api}/{uploaded_name}", proxy=self.settings.proxy or None, allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=10),
                            ) as response:
                                if response.status not in {200, 204, 404}:
                                    logger.warning("remote video deletion was not confirmed")
                        except (aiohttp.ClientError, asyncio.TimeoutError):
                            logger.warning("remote video deletion request failed")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise BiliVideoError("video network failure", user_message="视频接口连接失败或请求超时") from exc
