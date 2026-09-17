"""开发用 uvicorn 入口。Windows + --reload 时仍使用 Proactor 循环,Playwright 才能起 Chrome。

    uv run python -m app.serve --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 CreatorHub (Windows 热重载兼容 Playwright)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    from app.windows_loop import install
    install()

    import uvicorn
    kwargs: dict = {
        "app": "app.main:app",
        "host": args.host,
        "port": args.port,
        # 跳过 uvicorn 在 Windows subprocess 模式下强制 Selector 循环
        "loop": "none",
    }
    if args.reload:
        kwargs.update(
            reload=True,
            reload_dirs=["app"],
            reload_excludes=["data", "*.db"],
        )
    uvicorn.run(**kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
