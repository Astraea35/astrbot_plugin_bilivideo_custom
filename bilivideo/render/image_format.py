"""Shared image output format helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SUPPORTED_IMAGE_FORMATS = {"png", "jpg", "webp"}


def normalize_image_format(value: Any, default: str = "png") -> str:
    value = str(value or default).lower()
    return value if value in SUPPORTED_IMAGE_FORMATS else default


def image_extension(image_format: str) -> str:
    return {"jpg": ".jpg", "webp": ".webp"}.get(image_format, ".png")


def save_pillow_image(image: Any, output_path: Path, image_format: str, quality: int) -> None:
    image_format = normalize_image_format(image_format)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if image_format == "png":
        image.save(output_path, format="PNG", optimize=True)
        return

    image.convert("RGB").save(
        output_path,
        format="JPEG" if image_format == "jpg" else "WEBP",
        quality=max(1, min(100, int(quality))),
        optimize=True,
        **({"method": 6} if image_format == "webp" else {}),
    )


def transcode_image(source: Path, destination: Path, image_format: str, quality: int) -> None:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dependency is declared by the plugin
        raise RuntimeError(f"Pillow 不可用，无法生成 {image_format.upper()} 图片") from exc

    try:
        with Image.open(source) as image:
            save_pillow_image(image, destination, image_format, quality)
    except OSError as exc:
        raise RuntimeError(f"图片转换为 {image_format.upper()} 失败: {exc}") from exc
