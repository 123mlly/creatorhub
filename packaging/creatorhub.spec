# -*- mode: python ; coding: utf-8 -*-
"""CreatorHub PyInstaller spec（macOS .app / Windows onedir）。

由 packaging/build_macos.sh 或 packaging/build_windows.ps1 调用。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve().parent
os.chdir(ROOT)

datas: list = []
binaries: list = []
hiddenimports: list = []

for pkg in (
    "webview",
    "playwright",
    "uvicorn",
    "fastapi",
    "starlette",
    "sqlmodel",
    "pydantic",
    "yt_dlp",
    "curl_cffi",
    "cv2",
    "imageio_ffmpeg",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] skip collect_all({pkg}): {exc}")

datas += collect_data_files("certifi")

# 前端静态资源 + 默认配置模板
web_dir = ROOT / "app" / "web"
if web_dir.is_dir():
    datas.append((str(web_dir), "app/web"))

example = ROOT / "config.example.yaml"
if example.exists():
    datas.append((str(example), "."))

# Playwright Chromium（由 build 脚本准备到 packaging/ms-playwright）
# macOS：不要经 PyInstaller datas 打入 —— COLLECT 会对 Chrome.app 做 ad-hoc
# codesign，嵌套 Framework 会报 bundle format unrecognized（PyInstaller #7969）。
# build_macos.sh 在生成 .app 后再把 ms-playwright 拷进 _MEIPASS。
browsers = ROOT / "packaging" / "ms-playwright"
if sys.platform == "darwin":
    print("[spec] macOS: skip bundling ms-playwright via datas (post-copy in build script)")
elif browsers.is_dir() and any(browsers.iterdir()):
    datas.append((str(browsers), "ms-playwright"))
else:
    print("[spec] WARNING: packaging/ms-playwright 为空，扫码/发布将无法启动 Chromium")

hiddenimports += [
    "app.main",
    "app.desktop",
    "app.runtime",
    "app.config",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "multipart",
    "websockets",
    "xhshow",
]

a = Analysis(
    [str(ROOT / "packaging" / "entrypoint.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

_icon_icns = ROOT / "packaging" / "icons" / "CreatorHub.icns"
_icon_ico = ROOT / "packaging" / "icons" / "CreatorHub.ico"
if sys.platform == "darwin" and _icon_icns.exists():
    _exe_icon = str(_icon_icns)
elif _icon_ico.exists():
    _exe_icon = str(_icon_ico)
else:
    _exe_icon = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CreatorHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CreatorHub",
)

# 仅 macOS 再包一层 .app；Windows 产物是 dist/CreatorHub/CreatorHub.exe
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="CreatorHub.app",
        icon=str(_icon_icns) if _icon_icns.exists() else None,
        bundle_identifier="com.creatorhub.app",
        info_plist={
            "CFBundleName": "CreatorHub",
            "CFBundleDisplayName": "CreatorHub",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "CFBundleIconFile": "CreatorHub",
            "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {
                "NSAllowsLocalNetworking": True,
            },
            "LSMinimumSystemVersion": "12.0",
        },
    )
