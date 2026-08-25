"""Config validation tests."""

from __future__ import annotations

from bilivideo.core.config import PluginConfig


def test_defaults() -> None:
    cfg = PluginConfig.from_mapping({})
    assert cfg.note_style == "professional"
    assert cfg.max_cards_per_image == 6
    assert cfg.access_mode == "all"
    assert cfg.check_interval_minutes == 10
    assert "总结" in cfg.trigger_keywords


def test_subscription_interval_is_shared_by_all_update_types() -> None:
    cfg = PluginConfig.from_mapping(
        {"check_interval_minutes": 12, "interval_secs": 30}
    )

    assert cfg.subscription_check_interval_seconds == 12 * 60


def test_invalid_enum_falls_back() -> None:
    cfg = PluginConfig.from_mapping({"note_style": "invalid"})
    assert cfg.note_style == "professional"


def test_int_clamps_within_range() -> None:
    cfg = PluginConfig.from_mapping({"max_note_length": 99})
    assert cfg.max_note_length == 500  # clamped to lo=500
    cfg = PluginConfig.from_mapping({"max_note_length": 99999})
    assert cfg.max_note_length == 60000  # clamped to hi


def test_csv_split() -> None:
    cfg = PluginConfig.from_mapping({"access_list": "100, 200,abc"})
    assert cfg.access_list == ("100", "200", "abc")

    cfg = PluginConfig.from_mapping({"manual_summary_list": ["400", 500]})
    assert cfg.manual_summary_list == ("400", "500")


def test_trigger_keywords_custom() -> None:
    cfg = PluginConfig.from_mapping({"trigger_keywords": "abc,def"})
    assert cfg.trigger_keywords == ("abc", "def")


def test_openai_compatible_predicate() -> None:
    cfg = PluginConfig.from_mapping(
        {
            "llm_provider": "openai_compatible",
            "llm_api_base": "https://x/v1",
            "llm_api_key": "sk-x",
        }
    )
    assert cfg.is_openai_compatible
    assert cfg.has_llm_credentials()


def test_nested_groups_are_flattened() -> None:
    cfg = PluginConfig.from_mapping(
        {
            "general": {"debug_mode": True, "processing_timeout": 120},
            "llm": {"llm_provider": "openai_compatible"},
            "experimental": {"enable_multi_platform": True},
        }
    )
    assert cfg.debug_mode is True
    assert cfg.processing_timeout == 120
    assert cfg.llm_provider == "openai_compatible"
    assert cfg.enable_multi_platform is True


def test_flat_config_still_supported() -> None:
    # legacy flat layout must keep working alongside the new nested groups
    cfg = PluginConfig.from_mapping({"debug_mode": True, "max_note_length": 800})
    assert cfg.debug_mode is True
    assert cfg.max_note_length == 800


def test_dynamic_summary_configuration_is_parsed() -> None:
    cfg = PluginConfig.from_mapping(
        {
            "subscription": {
                "enable_dynamic_ai_summary": True,
                "dynamic_summary_provider": "vision-provider",
                "dynamic_summary_model": "vision-model",
                "enable_multimodal_dynamic_summary": True,
            }
        }
    )
    assert cfg.enable_dynamic_ai_summary is True
    assert cfg.dynamic_summary_provider == "vision-provider"
    assert cfg.dynamic_summary_model == "vision-model"
    assert cfg.enable_multimodal_dynamic_summary is True


def test_coolapk_vision_configuration_is_parsed() -> None:
    cfg = PluginConfig.from_mapping(
        {"summary": {"coolapk_summary_provider": "vision-provider", "coolapk_summary_model": "vision-model"}}
    )
    assert cfg.coolapk_summary_provider == "vision-provider"
    assert cfg.coolapk_summary_model == "vision-model"


def test_enabled_platforms_are_independently_configurable() -> None:
    cfg = PluginConfig.from_mapping({"enabled_platforms": ["B站", "YouTube"]})
    assert cfg.is_platform_enabled("bilibili")
    assert cfg.is_platform_enabled("youtube")
    assert not cfg.is_platform_enabled("douyin")
    assert not cfg.is_platform_enabled("coolapk")


def test_legacy_access_fields_are_migrated() -> None:
    cfg = PluginConfig.from_mapping(
        {
            "access_mode": "whitelist",
            "group_list": "100,200",
            "summary_command_mode": "blacklist",
            "summary_command_list": "300",
        }
    )
    assert cfg.access_list == ("100", "200")
    assert cfg.manual_summary_mode == "blacklist"
    assert cfg.manual_summary_list == ("300",)


def test_corrupted_single_char_list_healing() -> None:
    # Simulates what happens if AstrBot WebUI iterated over "709532435, 746238535" and stored single chars
    corrupted = ["7", "0", "9", "5", "3", "2", "4", "3", "5", ",", " ", "7", "4", "6", "2", "3", "8", "5", "3", "5"]
    cfg = PluginConfig.from_mapping({"access_list": corrupted})
    assert cfg.access_list == ("709532435", "746238535")


def test_chinese_comma_and_mixed_list_healing() -> None:
    cfg = PluginConfig.from_mapping(
        {"access_list": "709532435， 746238535; 1080530485\n201698347"}
    )
    assert cfg.access_list == ("709532435", "746238535", "1080530485", "201698347")


def test_list_containing_unsplit_comma_string() -> None:
    cfg = PluginConfig.from_mapping(
        {"access_list": ["709532435, 746238535", "1080530485"]}
    )
    assert cfg.access_list == ("709532435", "746238535", "1080530485")

