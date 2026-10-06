"""Windows 上让 Playwright 能拉起 Chrome。

uvicorn --reload / workers>1 会调用 asyncio_setup(use_subprocess=True),
把事件循环改成 WindowsSelectorEventLoopPolicy。Selector 循环不能
asyncio.create_subprocess_exec, Playwright 就会 NotImplementedError。

macOS 的 Selector 循环本身能起进程,所以那边 --reload 是好的。

Windows 上 uvicorn --reload 还会把 listen socket 传给子进程;Proactor
不能对继承来的 socket Accept(WinError 87)。热重载改由 app.serve 父进程
只监文件、子进程自己 bind,见 app/serve.py _windows_reload。
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
