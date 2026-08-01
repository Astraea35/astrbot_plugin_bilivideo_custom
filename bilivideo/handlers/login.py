"""`/B站登录` and `/B站登出` handlers."""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from ..access.control import is_admin
from ..auth.qrlogin import LoginStatus
from ..services import BiliVideoServices

# Lazy import (test environments lack AstrBot)
try:
    from astrbot.api.message_components import Image, Plain
except Exception:
    Image = Plain = None


async def handle_login(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return

    if services.is_logged_in():
        yield event.plain_result("✅ B站已登录，如需重新登录请先 /B站登出")
        return

    yield event.plain_result("🔄 正在生成B站登录二维码...")

    qr = await services.qrlogin.generate()
    if qr is None:
        yield event.plain_result("❌ 生成二维码失败，请稍后重试")
        return

    qr_path = Path(services.data_dir) / f"login_qr_{uuid.uuid4().hex[:8]}.png"
    try:
        import segno

        segno.make(qr.url).save(str(qr_path), scale=10, border=4)
    except ImportError:
        yield event.plain_result("❌ 缺少 segno 依赖，请运行: pip install segno")
        return
    except Exception as exc:
        yield event.plain_result(f"❌ 生成二维码图片失败: {exc}")
        return

    chain = [
        Plain("📱 请使用B站App扫描下方二维码登录\n⏳ 二维码有效期 3 分钟\n"),
        Image.fromFileSystem(str(qr_path)),
    ]
    yield event.chain_result(chain)

    result = await services.qrlogin.run_until_complete(qr.key, total_timeout=180)

    if result.status == LoginStatus.SUCCESS and result.cookies:
        # 同步调用，内部已处理 DataManager 持久化
        services.update_cookies(result.cookies)
        yield event.plain_result("✅ B站登录成功!")
    elif result.status == LoginStatus.EXPIRED:
        yield event.plain_result("⏰ 二维码已过期，请重新发送 /B站登录")
    elif result.status == LoginStatus.TIMEOUT:
        yield event.plain_result("⏰ 登录超时，请重新发送 /B站登录")
    else:
        yield event.plain_result("❌ 登录失败，请重新发送 /B站登录")

    with contextlib.suppress(OSError):
        os.remove(qr_path)


async def handle_logout(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return

    if not services.is_logged_in():
        yield event.plain_result("ℹ️ 当前未登录B站")
        return

    # 同步调用，内部已处理 DataManager 清除
    services.update_cookies(None)
    yield event.plain_result("✅ 已退出B站登录")
