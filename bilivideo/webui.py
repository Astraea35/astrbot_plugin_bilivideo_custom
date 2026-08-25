"""AstrBot WebUI page and API integration for the plugin."""

from __future__ import annotations

import copy
import inspect
import io
import mimetypes
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .core.config import PluginConfig, normalize_list_for_json

try:
    from astrbot.api.web import PluginUploadFile, error_response, file_response, json_response, request
except ImportError:
    PluginUploadFile = object  # type: ignore[assignment,misc]
    request = None  # type: ignore[assignment]

    def json_response(payload: Any) -> Any:
        return payload

    def error_response(message: str, status_code: int = 400) -> Any:
        raise ValueError(f"{status_code}: {message}")

    def file_response(path: str | Path, **kwargs: Any) -> Any:
        return path


PLUGIN_NAME = "astrbot_plugin_bilivideo_custom"
MAX_BACKGROUND_BYTES = 20 * 1024 * 1024
MAX_BACKGROUND_PIXELS = 12000 * 12000

CONFIG_GROUPS: dict[str, tuple[str, ...]] = {
    "platforms": ("enabled_platforms",),
    "general": ("debug_mode", "processing_timeout", "user_cooldown_seconds", "task_gap_secs", "reconnect_silent", "reconnect_silent_threshold_secs", "recent_dynamic_cache", "dynamic_limit"),
    "llm": ("llm_provider", "astrbot_provider_id", "llm_provider_id", "llm_api_base", "llm_api_key", "llm_model", "llm_temperature", "enable_fallback", "backup_provider_id", "bangumi_token"),
    "summary": ("note_style", "enable_link", "enable_summary", "max_note_length", "prefer_subtitle", "enable_bilibili_ai_subtitle", "summary_featured_comment_count", "summary_comment_reply_count", "subtitle_langs", "download_quality"),
    "coolapk": ("coolapk_summary_provider", "coolapk_summary_model"),
    "zhihu": ("zhihu_cookie", "zhihu_summary_provider", "zhihu_summary_model"),
    "render": ("output_image", "theme", "renderer_template", "custom_font_path", "image_scale_factor", "image_width", "image_output_format", "image_quality", "enable_auto_split", "max_cards_per_image", "image_font_size"),
    "message": ("enable_forward_message", "forward_bot_name", "forward_bot_uin"),
    "detect": ("enable_miniapp_detect", "detect_show_cover", "detect_show_uploader", "detect_show_desc", "detect_show_pubtime", "detect_show_link", "detect_show_stats", "detect_auto_summary", "trigger_keywords"),
    "subscription": ("enable_auto_push", "auto_push_summary", "check_interval_minutes", "max_subscriptions", "sub_list_render_method", "enable_dynamic_ai_summary", "dynamic_summary_provider", "dynamic_summary_model", "enable_multimodal_dynamic_summary", "plain_push_template", "plain_push_forward_template", "ai_summary_prompt"),
    "access": ("access_mode", "access_list", "manual_summary_mode", "manual_summary_list", "auto_summary_mode", "auto_summary_list"),
    "search": ("default_count", "default_download_count", "search_max_concurrent", "search_show_progress"),
    "webui": ("background_image",),
}

CONFIG_FIELDS = {field_name for group in CONFIG_GROUPS.values() for field_name in group}
SECRET_FIELDS = {"llm_api_key", "bangumi_token", "zhihu_cookie"}
LIST_FIELDS = {"subtitle_langs", "trigger_keywords", "access_list", "manual_summary_list", "auto_summary_list", "enabled_platforms"}
DEFAULT_EXTRA_VALUES: dict[str, Any] = {"astrbot_provider_id": "", "enable_fallback": False, "backup_provider_id": "", "background_image": ""}

IMAGE_MIME_BY_EXTENSION = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".svg": "image/svg+xml", ".bmp": "image/bmp",
}

FORMAT_EXTENSIONS = {
    "PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "GIF": (".gif", "image/gif"),
    "WEBP": (".webp", "image/webp"), "AVIF": (".avif", "image/avif"), "BMP": (".png", "image/png"),
    "ICO": (".png", "image/png"), "TIFF": (".png", "image/png"), "JPEG2000": (".png", "image/png"),
    "TGA": (".png", "image/png"), "PPM": (".png", "image/png"), "QOI": (".png", "image/png"),
}
PRESERVED_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "AVIF"}
SVG_BLOCKED_TAGS = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video"}


def _flatten_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, Mapping):
            flattened.update(value)
        else:
            flattened[key] = value
    return flattened


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _csv_values(value: Any) -> list[str]:
    return normalize_list_for_json(value)


def _mime_for_path(path: Path) -> str:
    return IMAGE_MIME_BY_EXTENSION.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _sanitize_svg(data: bytes) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("SVG 文件结构无效") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError("上传内容不是有效 SVG 图片")
    for element in root.iter():
        if _local_name(element.tag) in SVG_BLOCKED_TAGS:
            raise ValueError("SVG 不允许包含脚本或嵌入式媒体")
        for attribute, value in list(element.attrib.items()):
            attribute_name = _local_name(attribute)
            lowered = str(value).strip().lower()
            if attribute_name.startswith("on"):
                del element.attrib[attribute]
                continue
            if attribute_name in {"href", "src", "url"} and (
                lowered.startswith("javascript:") or lowered.startswith("data:")
                or "http://" in lowered or "https://" in lowered
            ):
                raise ValueError("SVG 不允许引用外部资源或脚本")
            if attribute_name == "style" and ("url(" in lowered or "expression(" in lowered):
                raise ValueError("SVG 样式包含不安全内容")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def prepare_background_image(data: bytes, filename: str, content_type: str = "") -> tuple[bytes, str, str]:
    """Validate an image and normalize formats browsers do not render reliably."""
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > MAX_BACKGROUND_BYTES:
        raise ValueError("图片不能超过 20 MB")
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".svg" or content_type.lower().split(";", 1)[0] == "image/svg+xml":
        return _sanitize_svg(data), ".svg", "image/svg+xml"
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            format_name = str(image.format or "").upper()
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_BACKGROUND_PIXELS:
                raise ValueError("图片尺寸过大，最大支持 12000 × 12000 像素")
            if format_name in PRESERVED_FORMATS:
                extension, mime = FORMAT_EXTENSIONS[format_name]
                return data, extension, mime
            if not format_name:
                raise ValueError("无法识别该图片编码")
            image.seek(0)
            converted = image.convert("RGBA")
            output = io.BytesIO()
            converted.save(output, format="PNG", optimize=True)
            return output.getvalue(), ".png", "image/png"
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("无法识别图片内容，请选择有效的图片文件") from exc


class PluginWebUI:
    """Registers the page APIs and keeps the page independent from internals."""

    def __init__(self, plugin: Any, context: Any, services: Any, data_dir: str) -> None:
        self.plugin = plugin
        self.context = context
        self.services = services
        self.data_dir = Path(data_dir)
        self.background_dir = self.data_dir / "ui"
        self.background_dir.mkdir(parents=True, exist_ok=True)

    def register(self) -> None:
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            return
        routes = (
            ("ui/state", self.get_state, ["GET"], "Get BiliVideo WebUI state"),
            ("settings/save", self.save_settings, ["POST"], "Save BiliVideo settings"),
            ("ui/background", self.get_background, ["GET"], "Get BiliVideo WebUI background"),
            ("ui/background/upload", self.upload_background, ["POST"], "Upload BiliVideo WebUI background"),
            ("ui/background/reset", self.reset_background, ["POST"], "Reset BiliVideo WebUI background"),
            ("platform/bilibili/cookies", self.save_bilibili_cookies, ["POST"], "Save Bilibili cookies"),
            ("platform/bilibili/logout", self.logout_bilibili, ["POST"], "Clear Bilibili cookies"),
            ("platform/youtube/cookies", self.save_youtube_cookies, ["POST"], "Save YouTube cookies"),
            ("platform/youtube/logout", self.logout_youtube, ["POST"], "Clear YouTube cookies"),
        )
        for route, handler, methods, description in routes:
            register(f"/{PLUGIN_NAME}/{route}", handler, methods, description)

    def _config_mapping(self) -> dict[str, Any]:
        source = getattr(self.plugin, "config", None)
        if isinstance(source, Mapping):
            return dict(source)
        raw = getattr(self.services, "raw_config", {})
        return dict(raw) if isinstance(raw, Mapping) else {}

    def _background_name(self, raw: Mapping[str, Any] | None = None) -> str:
        source = raw if raw is not None else self._config_mapping()
        name = _flatten_mapping(source).get("background_image", "")
        if isinstance(name, str) and re.fullmatch(r"background\.[a-z0-9]+", name.lower()):
            path = self.background_dir / name
            if path.is_file():
                return name
        for path in sorted(self.background_dir.glob("background.*")):
            if path.is_file():
                return path.name
        return ""

    def _background_path(self) -> Path | None:
        name = self._background_name()
        if not name:
            return None
        path = self.background_dir / name
        return path if path.is_file() else None

    def _state_config(self) -> tuple[dict[str, Any], dict[str, bool]]:
        raw = self._config_mapping()
        flattened = _flatten_mapping(raw)
        parsed = PluginConfig.from_mapping(raw)
        values = {field.name: _json_value(getattr(parsed, field.name)) for field in fields(parsed)}
        for field_name in CONFIG_FIELDS:
            if field_name in flattened:
                values[field_name] = _json_value(flattened[field_name])
            else:
                values.setdefault(field_name, _json_value(DEFAULT_EXTRA_VALUES.get(field_name, "")))
        for field_name in LIST_FIELDS:
            values[field_name] = _csv_values(values.get(field_name))
        secret_present = {field_name: bool(str(flattened.get(field_name, "")).strip()) for field_name in SECRET_FIELDS}
        for field_name in SECRET_FIELDS:
            values[field_name] = ""
        values["background_image"] = self._background_name(raw)
        return values, secret_present

    async def _subscription_count(self) -> int:
        manager = getattr(self.services, "subscription_manager", None)
        getter = getattr(manager, "get_all_subscriptions", None)
        if not callable(getter):
            return 0
        try:
            grouped = await getter()
        except Exception:
            return 0
        if not isinstance(grouped, Mapping):
            return 0
        return sum(len(items) for items in grouped.values() if isinstance(items, (list, tuple)))

    async def get_state(self):
        config, secret_present = self._state_config()
        scheduler = getattr(self.services, "scheduler", None)
        background = self._background_path()
        cookies = getattr(getattr(self.services, "cookies", None), "get", lambda: {})()
        return json_response(
            {
                "config": config,
                "secret_present": secret_present,
                "status": {
                    "bilibili_logged_in": bool(getattr(self.services, "is_logged_in", lambda: False)()),
                    "bilibili_cookie_keys": sorted(cookies),
                    "youtube_cookies": bool(getattr(getattr(self.services, "youtube_cookies", None), "has", lambda: False)()),
                    "scheduler_running": bool(getattr(scheduler, "is_running", lambda: False)()),
                    "subscription_count": await self._subscription_count(),
                    "enabled_platforms": config.get("enabled_platforms", ["B站", "酷安"]),
                },
                "background": {
                    "active": background is not None,
                    "filename": background.name if background else "",
                    "content_type": _mime_for_path(background) if background else "",
                    "updated_at": int(background.stat().st_mtime) if background else 0,
                },
            }
        )

    def _candidate_with_settings(
        self,
        settings: Mapping[str, Any],
        clear_secrets: set[str] | None = None,
    ) -> dict[str, Any]:
        candidate = copy.deepcopy(self._config_mapping())
        flattened = _flatten_mapping(settings)
        current_flat = _flatten_mapping(candidate)
        clear_secrets = clear_secrets or set()
        for field_name, value in flattened.items():
            if field_name not in CONFIG_FIELDS:
                continue
            if field_name in SECRET_FIELDS and not str(value or "").strip() and field_name not in clear_secrets:
                continue
            if field_name in LIST_FIELDS:
                value = normalize_list_for_json(value)
            group = next(group for group, fields_in_group in CONFIG_GROUPS.items() if field_name in fields_in_group)
            if isinstance(candidate.get(group), dict):
                candidate[group][field_name] = value
            elif group == "webui":
                candidate[group] = {field_name: value}
            else:
                candidate[field_name] = value
        for field_name in SECRET_FIELDS:
            if field_name in clear_secrets:
                group = next(group for group, fields_in_group in CONFIG_GROUPS.items() if field_name in fields_in_group)
                if isinstance(candidate.get(group), dict):
                    candidate[group][field_name] = ""
                else:
                    candidate[field_name] = ""
            elif field_name not in flattened and field_name in current_flat:
                continue
        return candidate

    async def _persist_candidate(self, candidate: dict[str, Any]) -> PluginConfig:
        config_object = getattr(self.plugin, "config", None)
        if isinstance(config_object, dict):
            config_object.clear()
            config_object.update(candidate)
        raw_config = getattr(self.services, "raw_config", None)
        if isinstance(raw_config, dict) and raw_config is not config_object:
            raw_config.clear()
            raw_config.update(candidate)
        save_config = getattr(config_object, "save_config", None)
        if callable(save_config):
            result = save_config()
            if inspect.isawaitable(result):
                await result
        parsed = PluginConfig.from_mapping(candidate)
        apply_runtime = getattr(self.plugin, "apply_runtime_config", None)
        if callable(apply_runtime):
            result = apply_runtime(parsed)
            if inspect.isawaitable(result):
                await result
        else:
            self.services.config = parsed
        return parsed

    async def save_settings(self):
        payload = await request.json(default={})
        if not isinstance(payload, Mapping):
            return error_response("设置数据格式无效", status_code=400)
        settings = payload.get("settings", payload)
        if not isinstance(settings, Mapping):
            return error_response("设置数据格式无效", status_code=400)
        clear_secrets = payload.get("clear_secrets", [])
        if isinstance(clear_secrets, str):
            clear_secrets = [clear_secrets]
        if not isinstance(clear_secrets, (list, tuple, set)):
            clear_secrets = []
        clear_set = {str(item) for item in clear_secrets if str(item) in SECRET_FIELDS}
        candidate = self._candidate_with_settings(settings, clear_set)
        try:
            parsed = await self._persist_candidate(candidate)
        except Exception as exc:
            return error_response(f"保存设置失败: {exc}", status_code=500)
        return json_response(
            {
                "saved": True,
                "config": {key: _json_value(value) for key, value in asdict(parsed).items()},
                "restart_required": bool(
                    any(key in settings for key in {"llm_provider", "llm_api_base", "llm_api_key", "llm_model"})
                ),
            }
        )

    async def get_background(self):
        path = self._background_path()
        if path is None:
            return error_response("暂无背景图", status_code=404)
        return file_response(
            path,
            filename=path.name,
            content_type=_mime_for_path(path),
        )

    async def upload_background(self):
        files = await request.files()
        upload = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            return error_response("缺少图片文件", status_code=400)
        try:
            data = await upload.read(MAX_BACKGROUND_BYTES + 1)
            normalized, extension, content_type = prepare_background_image(
                data,
                str(getattr(upload, "filename", "background")),
                str(getattr(upload, "content_type", "")),
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        target = self.background_dir / f"background{extension}"
        temporary = self.background_dir / f".background.{int(time.time() * 1000)}.tmp"
        try:
            temporary.write_bytes(normalized)
            temporary.replace(target)
            for old_path in self.background_dir.glob("background.*"):
                if old_path != target and old_path.is_file():
                    old_path.unlink()
            await self._persist_candidate(self._candidate_with_settings({"background_image": target.name}))
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            return error_response(f"背景图保存失败: {exc}", status_code=500)
        return json_response(
            {
                "saved": True,
                "filename": target.name,
                "content_type": content_type,
                "size": len(normalized),
            }
        )

    async def reset_background(self):
        for path in self.background_dir.glob("background.*"):
            if path.is_file():
                path.unlink(missing_ok=True)
        await self._persist_candidate(self._candidate_with_settings({"background_image": ""}))
        return json_response({"saved": True})

    async def save_bilibili_cookies(self):
        payload = await request.json(default={})
        if not isinstance(payload, Mapping):
            return error_response("凭据格式无效", status_code=400)
        cookies = payload.get("cookies")
        if not isinstance(cookies, Mapping):
            cookies = {key: payload.get(key, "") for key in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")}
        clean = {str(key): str(value).strip() for key, value in cookies.items() if str(value).strip()}
        if not clean.get("SESSDATA"):
            return error_response("SESSDATA 不能为空", status_code=400)
        self.services.update_cookies(clean)
        return json_response({"saved": True, "logged_in": bool(self.services.is_logged_in())})

    async def logout_bilibili(self):
        self.services.update_cookies(None)
        if getattr(self.services, "data_manager", None) is not None:
            clear_credential = getattr(self.services.data_manager, "clear_credential", None)
            if callable(clear_credential):
                result = clear_credential()
                if inspect.isawaitable(result):
                    await result
        return json_response({"saved": True, "logged_in": False})

    async def save_youtube_cookies(self):
        payload = await request.json(default={})
        content = payload.get("content") if isinstance(payload, Mapping) else ""
        if not isinstance(content, str) or not content.strip():
            return error_response("YouTube cookies 内容不能为空", status_code=400)
        count = self.services.youtube_cookies.save(content)
        if not count:
            return error_response("无法识别 cookies，请粘贴 Netscape cookies.txt 或 Cookie 请求头", status_code=400)
        return json_response({"saved": True, "cookie_count": count})

    async def logout_youtube(self):
        cleared = bool(self.services.youtube_cookies.clear())
        return json_response({"saved": cleared, "cookies": False})
