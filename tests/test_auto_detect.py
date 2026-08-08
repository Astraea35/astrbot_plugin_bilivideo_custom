'''Automatic link detection tests.'''

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bilivideo.core.config import PluginConfig
from bilivideo.handlers import auto_detect


@pytest.mark.asyncio
async def test_disabled_multi_platform_skips_douyin_auto_detection(monkeypatch) -> None:
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError('Douyin metadata should not be fetched when multi-platform is disabled')

    monkeypatch.setattr(auto_detect, 'fetch_douyin_metadata', fail_if_called)
    services = SimpleNamespace(
        config=PluginConfig(enable_multi_platform=False),
        enable_miniapp_detect=True,
    )
    event = SimpleNamespace(message_str='https://www.douyin.com/video/123')

    responses = [response async for response in auto_detect.handle_auto_detect(services, event)]

    assert responses == []
