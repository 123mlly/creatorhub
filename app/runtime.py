"""运行时环境探测（Docker / 无桌面 / 打包冻结等）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """PyInstaller / 冻结可执行文件。"""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def is_docker() -> bool:
    """CREATORHUB_DOCKER=1 优先；否则探测 /.dockerenv 或 cgroup。"""
    flag = (os.environ.get("CREATORHUB_DOCKER") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "docker" in cgroup or "containerd" in cgroup


def qr_login_enabled() -> bool:
    """Docker 默认关闭扫码登录（无桌面弹窗）；可用 CREATORHUB_QR_LOGIN=1 强制开启。"""
    override = (os.environ.get("CREATORHUB_QR_LOGIN") or "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return not is_docker()


def resource_dir() -> Path:
    """只读资源目录:源码树根,或 PyInstaller 解包目录(_MEIPASS)。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """可写用户数据根目录(配置 / DB / 下载 / profile)。

    优先级: CREATORHUB_DATA_DIR > 冻结态平台默认目录 > 项目下 ./data 的父级(开发态用项目根)。
    """
    env = (os.environ.get("CREATORHUB_DATA_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        if sys.platform == "darwin":
            return (Path.home() / "Library" / "Application Support" / "CreatorHub").resolve()
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            return (Path(base) / "CreatorHub").resolve()
        return (Path.home() / ".local" / "share" / "CreatorHub").resolve()
    return Path(__file__).resolve().parent.parent


def prepare_user_data() -> Path:
    """确保用户数据目录与默认 config.yaml 存在;设置环境变量供 load_config 使用。

    返回 user_data_dir()。
    """
    root = user_data_dir()
    data = root / "data"
    (data / "media").mkdir(parents=True, exist_ok=True)
    (data / "profiles").mkdir(parents=True, exist_ok=True)
    (data / "uploads").mkdir(parents=True, exist_ok=True)
    (data / "debug").mkdir(parents=True, exist_ok=True)

    cfg_path = root / "config.yaml"
    if not cfg_path.exists():
        example = resource_dir() / "config.example.yaml"
        if example.exists():
            text = example.read_text(encoding="utf-8")
        else:
            text = (
                "server:\n  host: 127.0.0.1\n  port: 8000\n"
                "storage:\n  db_path: ./data/creatorhub.db\n"
                "engine:\n  media_dir: ./data/media\n  profiles_dir: ./data/profiles\n"
            )
        cfg_path.write_text(text, encoding="utf-8")

    os.environ.setdefault("CREATORHUB_CONFIG_PATH", str(cfg_path))
    os.environ.setdefault("CREATORHUB_DATA_DIR", str(root))

    # 打包进应用的 Playwright 浏览器
    bundled_browsers = resource_dir() / "ms-playwright"
    if bundled_browsers.is_dir() and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)

    # 相对路径 ./data/... 相对于用户数据根
    try:
        os.chdir(root)
    except OSError:
        pass
    return root
