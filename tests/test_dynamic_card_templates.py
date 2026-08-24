"""Layout invariants for the dynamic-card HTML templates."""

from __future__ import annotations

from pathlib import Path
import re


TEMPLATE_DIR = Path(__file__).parents[1] / "assets" / "templates"


def _css_rule(template: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\}}", template, re.DOTALL)
    assert match is not None, f"missing CSS rule for {selector}"
    return match.group(1)


def test_classic_template_uses_a_uniform_card_inset() -> None:
    template = (TEMPLATE_DIR / "template_1.html").read_text(encoding="utf-8")
    body = _css_rule(template, "body")
    card = _css_rule(template, ".dynamic-card")

    assert "padding: 10px;" in body
    assert "margin: 0;" in card
    assert "max-width: none;" in card
    assert "margin-left:" not in card


def test_pink_template_fills_the_padded_screenshot_canvas() -> None:
    template = (TEMPLATE_DIR / "template_2.html").read_text(encoding="utf-8")
    body = _css_rule(template, "body")
    card = _css_rule(template, ".card")

    assert "padding: 15px 15px 15px 15px;" in body
    assert "width: 100%;" in card
    assert "max-width: none;" in card
    assert "box-sizing: border-box;" in card


def test_simple_template_uses_a_border_box_card() -> None:
    template = (TEMPLATE_DIR / "template_simple.html").read_text(encoding="utf-8")
    body = _css_rule(template, "body")
    card = _css_rule(template, ".card")

    assert "padding: 25px;" in body
    assert "width: 100%;" in card
    assert "max-width: 100%;" in card
    assert "box-sizing: border-box;" in card
