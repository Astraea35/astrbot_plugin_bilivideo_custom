"""Current Chromium template layout and sanitizer hardening tests."""

from __future__ import annotations

import pytest

from bilivideo.render.templates import build_full_html, sanitize_html


def _html(title: str = "标题") -> str:
    return build_full_html(
        "<div class='card'>x</div>", title_text=title, footer_time="t", width=1400
    )


class TestTitleAlwaysVisible:
    def test_no_background_clip_text(self) -> None:
        html = _html()
        assert "background-clip:text" not in html
        assert "-webkit-text-fill-color:transparent" not in html

    def test_title_is_escaped(self) -> None:
        assert "&lt;script&gt;" in _html("<script>")


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", ["light", "dark"])
async def test_title_visible_and_cards_stack_in_current_renderer(theme) -> None:
    from playwright.async_api import async_playwright

    markup = build_full_html(
        '<div class="card">第一章</div><div class="card">第二章</div>',
        title_text="标题", footer_time="t", width=800, theme=theme,
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 800, "height": 600})
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(markup)
            layout = await page.evaluate("""() => {
                const title = document.querySelector('.header h1');
                const style = getComputedStyle(title);
                const content = document.querySelector('.content');
                const contentStyle = getComputedStyle(content);
                const cards = Array.from(document.querySelectorAll('.card'), el => {
                    const box = el.getBoundingClientRect();
                    return {top: box.top, bottom: box.bottom, width: box.width, left: box.left};
                });
                return {
                    color: style.color, fill: style.webkitTextFillColor,
                    visible: style.display !== 'none' && style.visibility === 'visible'
                        && Number(style.opacity) > 0 && title.getBoundingClientRect().height > 0,
                    contentWidth: content.clientWidth - parseFloat(contentStyle.paddingLeft)
                        - parseFloat(contentStyle.paddingRight),
                    cards,
                };
            }""")
        finally:
            await browser.close()

    assert layout["visible"]
    assert layout["color"] != "rgba(0, 0, 0, 0)"
    assert layout["fill"] == layout["color"]
    first, second = layout["cards"]
    assert abs(first["width"] - layout["contentWidth"]) <= 1
    assert abs(second["width"] - first["width"]) <= 1
    assert abs(second["left"] - first["left"]) <= 1
    assert second["top"] >= first["bottom"]


def test_footer_time_is_escaped() -> None:
    markup = build_full_html("<p>正文</p>", title_text="标题", footer_time='<img src=x onerror="alert(1)">')
    assert '<div class="ftime">&lt;img' in markup


class TestSanitizerBlocksFileScheme:
    def test_neutralizes_file_uri(self) -> None:
        out = sanitize_html('<img src="file:///etc/passwd">')
        assert "file:" not in out
        assert "src=" not in out

    def test_still_neutralizes_javascript(self) -> None:
        out = sanitize_html('<a href="javascript:alert(1)">x</a>')
        assert "javascript:" not in out
        assert "href=" not in out
