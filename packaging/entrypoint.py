"""PyInstaller / .app 入口：启动桌面壳。"""
from __future__ import annotations

import sys

from app.desktop import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
