"""HTML sanitization tests for Markdown rendered in Chromium."""

from __future__ import annotations

import html
import sys
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from bilivideo.render.templates import sanitize_html


class TestRemovesDangerousConstructs:
    def test_strips_script_block(self) -> None:
        out = sanitize_html("<p>hi</p><script>alert(1)</script>")
        assert "<script" not in out
        assert "alert(1)" not in out

    def test_strips_style_block(self) -> None:
        out = sanitize_html("<style>body{display:none}</style><p>hi</p>")
        assert "<style" not in out
        assert "display:none" not in out

    def test_strips_event_handler_attributes(self) -> None:
        out = sanitize_html(
            '<img src="x" onerror="alert(1)"><button onclick=\'go()\'>x</button>'
        )
        assert "onerror" not in out
        assert "onclick" not in out
        assert "alert(1)" not in out
        assert "go()" not in out

    def test_neutralizes_javascript_href(self) -> None:
        out = sanitize_html('<a href="javascript:alert(1)">click</a>')
        assert "javascript:" not in out
        assert "href=" not in out
        assert "click</a>" in out

    def test_removes_iframe_with_content(self) -> None:
        out = sanitize_html('<iframe src="http://evil.example">danger</iframe>')
        assert "<iframe" not in out
        assert "danger" not in out


class TestPreservesOrdinaryTags:
    def test_keeps_strong(self) -> None:
        out = sanitize_html("<strong>bold</strong>")
        assert "<strong>bold</strong>" in out

    def test_keeps_heading(self) -> None:
        out = sanitize_html("<h2>Chapter</h2>")
        assert "<h2>Chapter</h2>" in out

    def test_keeps_list_item(self) -> None:
        out = sanitize_html("<ul><li>point</li></ul>")
        assert "<li>point</li>" in out

    def test_keeps_safe_anchor(self) -> None:
        out = sanitize_html('<a href="https://example.com">link</a>')
        assert 'href="https://example.com"' in out
        assert '>link</a>' in out

    def test_keeps_comment_images_and_grid_styles(self) -> None:
        out = sanitize_html(
            '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;'
            'background-color:#fff"><img class="comment-emote" '
            'src="https://i0.hdslb.com/test.png" referrerpolicy="no-referrer">'
            '<table><tr><td colspan="2" style="text-align:right">42</td></tr></table></div>'
        )
        assert 'class="comment-emote"' in out
        assert 'src="https://i0.hdslb.com/test.png"' in out
        assert 'referrerpolicy="no-referrer"' in out
        assert 'grid-template-columns:repeat(3,1fr)' in out.replace(" ", "")
        assert 'background-color:#fff' in out
        assert 'colspan="2"' in out
        assert 'text-align:right' in out


class _Tags(HTMLParser):
    def __init__(self, fragment: str) -> None:
        super().__init__()
        self.tags = []
        self.feed(fragment)

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append((tag, dict(attrs)))


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java&#x73;cript:alert(1)",
    "java&#10;script:alert(1)", "vbscript:msgbox(1)", "file:///C:/Windows/win.ini",
    "data:text/html,<script>alert(1)</script>", "data:image/svg+xml,<svg onload=alert(1)>",
    "ftp://example.com/a", "//example.com/a", "\\\\server\\share\\a", "/etc/passwd",
])
@pytest.mark.parametrize("tag,attribute", [("a", "href"), ("img", "src")])
def test_removes_unsafe_and_relative_urls(url, tag, attribute) -> None:
    # Entities are intentionally left intact to exercise browser-style decoding.
    out = sanitize_html(f'<{tag} {attribute}="{url}">text</{tag}>')
    assert all(attribute not in attrs for _, attrs in _Tags(out).tags)


@pytest.mark.parametrize("fragment", [
    '<ScRiPt>window.__unsafe=1</sCrIpT><p>safe</p>',
    '<svg><foreignObject><img src=x onerror="window.__unsafe=1"></foreignObject></svg>',
    '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=alert(1)>">',
    '<template><img src=x onerror="window.__unsafe=1"></template>',
    '<object data="file:///etc/passwd"><embed src="data:text/html,evil"></object>',
    '<base href="file:///"><meta http-equiv="refresh" content="0;url=https://evil.example">',
    '<img src="https://example.com/a.png" srcset="file:///etc/passwd 1x" onerror=alert(1)>',
])
def test_removes_active_and_malformed_markup(fragment) -> None:
    out = sanitize_html(fragment)
    for tag, attrs in _Tags(out).tags:
        assert tag not in {"script", "style", "svg", "math", "template", "object", "embed", "base", "meta"}
        assert not any(name.startswith("on") for name in attrs)
        assert not {"srcdoc", "srcset"} & attrs.keys()
    assert sanitize_html(out) == out


def test_removes_network_capable_inline_css() -> None:
    out = sanitize_html(
        '<p style="background:url(https://evil.example/);'
        'background-image:url(file:///etc/passwd);cursor:url(https://evil.example/c),auto;'
        'behavior:url(x.htc);-moz-binding:url(x);color:red;text-align:center">safe</p>'
    )
    assert "url(" not in out
    assert "evil.example" not in out
    assert "color:red" in out
    assert "text-align:center" in out


def test_missing_sanitizer_dependency_escapes_instead_of_passing_html(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "nh3", None)
    fragment = '<p>safe</p><script>alert(1)</script>'
    assert sanitize_html(fragment) == html.escape(fragment)


@pytest.mark.asyncio
async def test_actual_renderer_blocks_body_and_footer_injection(tmp_path, monkeypatch) -> None:
    from playwright.async_api import Page
    from bilivideo.render.wkhtml_renderer import WkHtmlRenderer

    observations = []
    requests = []
    original_set_content = Page.set_content
    original_screenshot = Page.screenshot

    async def set_content(page, markup, **kwargs):
        # Keep this security check independent of external CDN availability.
        async def intercept(route):
            requests.append(route.request.url)
            await route.abort()

        await page.route("**/*", intercept)
        return await original_set_content(page, markup, **kwargs)

    async def screenshot(page, **kwargs):
        observations.append(await page.evaluate("""() => ({
            unsafe: window.__unsafe || false,
            frames: document.querySelectorAll('iframe,object,embed').length,
            events: document.querySelectorAll('[onerror],[onclick]').length,
            footer: document.querySelector('.ftxt').textContent,
            table: document.querySelector('td').textContent,
        })"""))
        return await original_screenshot(page, **kwargs)

    monkeypatch.setattr(Page, "set_content", set_content)
    monkeypatch.setattr(Page, "screenshot", screenshot)
    provider = r'模型\1 <img src="https://evil.example/footer" onerror="window.__unsafe=1">'
    services = SimpleNamespace(
        config=SimpleNamespace(image_width=640, image_scale_factor=1),
        llm=SimpleNamespace(provider_id=provider),
    )
    renderer = WkHtmlRenderer(output_dir=tmp_path, services=services)
    paths = await renderer.render(
        '# 测试\n\n<script>window.__unsafe=1</script>\n\n'
        '<iframe src="https://evil.example/frame"></iframe>\n\n'
        '<img src="file:///etc/passwd" onerror="window.__unsafe=1">\n\n'
        '| 项目 | 数值 |\n| --- | --- |\n| 跑分 | 42 |',
        base_filename="sanitized", enable_split=False,
    )

    assert paths and all(path.stat().st_size > 0 for path in paths)
    assert observations
    assert all(not item["unsafe"] and item["frames"] == item["events"] == 0 for item in observations)
    assert all(provider in item["footer"] and item["table"] == "跑分" for item in observations)
    assert all(url.startswith("https://unpkg.com/katex@") for url in requests)
