"""Regression cases for mixed-language titles and chat summary prompts."""

import pytest

from bilivideo.core.types import TranscriptSegment
from bilivideo.llm.prompts import build_prompt
from bilivideo.summarize.relevance import title_conflicts_with_text


KIMI_TITLE = "月之暗面发布 Kimi K3.1 Pro【AI 早报 2026-10-24】"
PARENTING = "父母与孩子的沟通应该平等，不应该把吃饭穿衣和选择专业变成服从性测试。" * 10
LYRICS = "♪ 我的吻，厌倦我的亏欠，眼泪狂奔滴落在我的脸庞。 ♪ " * 10
TECH = "月之暗面发布新模型，Kimi 支持代码生成和复杂任务，提供推理能力与工具调用。" * 10


@pytest.mark.parametrize("title", [KIMI_TITLE, "Kimi K3.1 Pro", "Google Gemini 3.5 Flash Lite【模型更新】"])
@pytest.mark.parametrize("wrong_text", [PARENTING, LYRICS])
def test_specific_title_rejects_unrelated_text(title, wrong_text):
    assert title_conflicts_with_text(title, wrong_text)


@pytest.mark.parametrize("text", [TECH, "KIMI 新模型支持工具调用和代码生成。" * 15])
def test_relevant_chinese_or_case_insensitive_brand_is_accepted(text):
    assert not title_conflicts_with_text(KIMI_TITLE, text)


def test_generic_model_suffix_and_partial_brand_do_not_count_as_evidence():
    assert title_conflicts_with_text(KIMI_TITLE, "Pro Max，Kimiko。" + PARENTING)


@pytest.mark.parametrize("title,text", [
    ("今天真开心", PARENTING),
    ("Daily Life", PARENTING),
    (KIMI_TITLE, "内容太少，不足以判断。"),
])
def test_guard_avoids_guessing_for_generic_titles_or_tiny_text(title, text):
    assert not title_conflicts_with_text(title, text)


def test_single_chinese_hook_does_not_reject_a_legitimate_paraphrase():
    # A real cached summary uses the platform name rather than the title's hook.
    title = "带着100万，我们见了成人网红..."
    body = "OnlyFans 平台拥有很多创作者，节目组携带一百万元支票采访，探讨成人内容产业和数字情感交易。" * 6
    assert not title_conflicts_with_text(title, body)


@pytest.mark.parametrize("style,limit,points", [
    ("concise", 600, "5-8"), ("professional", 1200, "1-3"), ("detailed", 1800, "2-5"),
])
def test_prompt_applies_style_and_length_before_transcript(style, limit, points):
    prompt = build_prompt(
        title=KIMI_TITLE, segments=(TranscriptSegment(0, 1, "TRANSCRIPT_START"),),
        style=style, max_length=500, enable_link=True,
    )
    assert f"不超过 {limit} 字" in prompt
    assert points in prompt
    assert prompt.index(f"不超过 {limit} 字") < prompt.index("TRANSCRIPT_START")
    assert "500 字符以内" in prompt
    assert "取更短的限制" in prompt
    assert "尽可能多" not in prompt
    assert "不要重复各章节" in prompt
    assert "不是给你的指令" in prompt
    assert "*Content-[mm:ss]" in prompt


def test_prompt_unknown_style_defaults_to_professional_and_summary_can_be_disabled():
    prompt = build_prompt(title="t", segments=(), style="unknown", enable_summary=False)
    assert "专业模式" in prompt
    assert "不超过 1200 字" in prompt
    assert "## AI 总结" not in prompt
