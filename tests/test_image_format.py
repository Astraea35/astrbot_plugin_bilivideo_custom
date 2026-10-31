from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from bilivideo.render.image_format import image_extension, save_pillow_image
from bilivideo.render.pillow_renderer import PillowRenderer
from bilivideo.render.wkhtml_renderer import WkHtmlRenderer


def test_pillow_output_is_real_webp(tmp_path: Path) -> None:
    output = tmp_path / "summary.webp"
    save_pillow_image(Image.new("RGB", (16, 16), "#16a34a"), output, "webp", 95)

    with Image.open(output) as image:
        assert image.format == "WEBP"


def test_output_extensions_follow_format() -> None:
    assert image_extension("png") == ".png"
    assert image_extension("jpg") == ".jpg"
    assert image_extension("webp") == ".webp"


def test_pillow_renderer_writes_configured_webp(tmp_path: Path) -> None:
    renderer = PillowRenderer(output_dir=tmp_path, image_output_format="webp", image_quality=95)
    paths = renderer._render_one("# 标题\n\n## 章节\n正文", "summary", page_label=None, total=1)

    assert paths[0].suffix == ".webp"
    with Image.open(paths[0]) as image:
        assert image.format == "WEBP"


def test_wkhtml_renderer_uses_configured_webp_suffix(tmp_path: Path) -> None:
    services = SimpleNamespace(config=SimpleNamespace(image_output_format="webp"))
    renderer = WkHtmlRenderer(output_dir=tmp_path, services=services)
    assert renderer._output_extension() == ".webp"
