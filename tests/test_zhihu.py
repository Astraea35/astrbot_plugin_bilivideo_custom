"""Zhihu URL and text-post summary integration tests."""

from __future__ import annotations

from bilivideo.core.config import PluginConfig
from bilivideo.parsing.url_extractor import detect_platform
from bilivideo.zhihu import detect_zhihu_url, extract_zhihu_url


def test_zhihu_answer_article_and_question_urls_are_recognized() -> None:
    answer = "https://www.zhihu.com/question/123456/answer/789012"
    article = "https://zhuanlan.zhihu.com/p/123456"
    question = "https://www.zhihu.com/question/123456"

    assert detect_zhihu_url(answer) == ("answer", "789012")
    assert detect_zhihu_url(article) == ("article", "123456")
    assert detect_zhihu_url(question) == ("question", "123456")
    assert extract_zhihu_url(f"/总结 {answer}。") == answer
    assert detect_platform(answer) == "zhihu"
    assert detect_platform(article) == "zhihu"


def test_zhihu_is_individually_controlled_by_platform_selection() -> None:
    disabled = PluginConfig.from_mapping({"enabled_platforms": ["B站", "酷安"]})
    enabled = PluginConfig.from_mapping({"enabled_platforms": ["B站", "知乎"]})

    assert not disabled.is_platform_enabled("zhihu")
    assert enabled.is_platform_enabled("zhihu")


def test_zhihu_model_configuration_is_parsed() -> None:
    config = PluginConfig.from_mapping(
        {
            "zhihu": {
                "zhihu_cookie": "z_c0=test",
                "zhihu_summary_provider": "vision-provider",
                "zhihu_summary_model": "vision-model",
            }
        }
    )

    assert config.zhihu_cookie == "z_c0=test"
    assert config.zhihu_summary_provider == "vision-provider"
    assert config.zhihu_summary_model == "vision-model"
