"""Windows 上让 Playwright 能拉起 Chrome。

uvicorn --reload / workers>1 会调用 asyncio_setup(use_subprocess=True),
把事件循环改成 WindowsSelectorEventLoopPolicy。Selector 循环不能
asyncio.create_subprocess_exec, Playwright 就会 NotImplementedError。

macOS 的 Selector 循环本身能起进程,所以那边 --reload 是好的。
"""
from __future__ import annotations

import asyncio
import sys


def install() -> None:
    if sys.platform != "win32":
        return
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        import uvicorn.loops.asyncio as uvicorn_asyncio
    except Exception:
        return

    def asyncio_setup(use_subprocess: bool = False) -> None:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn_asyncio.asyncio_setup = asyncio_setup
