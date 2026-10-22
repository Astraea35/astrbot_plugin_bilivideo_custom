from itertools import pairwise
from types import SimpleNamespace

import pytest
from PIL import Image

from bilivideo.render import wkhtml_renderer


@pytest.mark.parametrize("breaks", [[], [900, 1800, 2700]])
def test_pixel_regions_cover_the_whole_document_without_oversized_parts(breaks):
    regions = wkhtml_renderer._screenshot_regions(3100, 1000, breaks, 64)
    assert regions[0][0] == 0
    assert regions[-1][0] + regions[-1][1] == 3100
    assert all(0 < height <= 1000 for _, height in regions)
    for (top, height), (next_top, _) in pairwise(regions):
        assert top < next_top <= top + height


@pytest.mark.asyncio
@pytest.mark.parametrize("image_format", ["png", "jpg"])
async def test_actual_browser_splits_one_long_chapter_even_when_auto_split_is_off(
    tmp_path, monkeypatch, image_format,
):
    # Exercise actual screenshot dimensions, not a mocked renderer response.
    pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(wkhtml_renderer, "MAX_IMAGE_SIDE", 1200)
    monkeypatch.setattr(wkhtml_renderer, "MAX_IMAGE_PIXELS", 800_000)
    services = SimpleNamespace(config=SimpleNamespace(
        image_width=800, image_scale_factor=1, image_font_size=16,
        image_output_format=image_format, image_quality=100,
    ))
    renderer = wkhtml_renderer.WkHtmlRenderer(output_dir=tmp_path, services=services)
    text = "# 总结\n\n## 唯一章节\n\n" + "\n\n".join(f"第{i}段：完整内容" for i in range(60))
    paths = await renderer.render(text, base_filename="long", enable_split=False)
    assert len(paths) > 1
    for path in paths:
        with Image.open(path) as image:
            assert image.format == ("PNG" if image_format == "png" else "JPEG")
            assert max(image.size) <= 1200
            assert image.width * image.height <= 800_000
