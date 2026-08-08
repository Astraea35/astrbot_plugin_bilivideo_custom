"""Dynamic subscription polling selection tests."""

from __future__ import annotations

from bilivideo.services.listener import (
    DYNAMIC_DETAIL_TYPES,
    DYNAMIC_SUB_TYPES,
    DynamicListener,
)
from bilivideo.subscription.manager import Subscription


def _subscription(sub_types: list[str]) -> Subscription:
    return Subscription(mid="42", name="UP", sub_types=sub_types)


def test_specific_dynamic_types_request_the_dynamic_feed() -> None:
    listener = object.__new__(DynamicListener)

    for sub_type in DYNAMIC_SUB_TYPES:
        assert listener._needs_dynamic_data(_subscription([sub_type]))

    assert not listener._needs_dynamic_data(_subscription(["视频"]))
    assert not listener._needs_dynamic_data(_subscription(["直播"]))


def test_dynamic_macro_covers_all_non_video_dynamic_types() -> None:
    assert DYNAMIC_DETAIL_TYPES == {
        "图文",
        "转发",
        "专栏",
        "抽奖",
        "转发抽奖",
    }
