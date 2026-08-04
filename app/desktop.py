"""CreatorHub 桌面壳：内嵌窗口 + 本地 uvicorn。

用法:
    uv run python -m app.desktop
    python creatorhub.py desktop

打包后由 .app 入口直接调用 main()。
关闭窗口即停止后端。发布/扫码仍会另弹 Playwright Chromium。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

# 与主界面同色的启动页,避免 WebView 默认白屏
_SPLASH_HTML = """<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CreatorHub</title>
<style>
  html,body{margin:0;height:100%;background:#0a0c10;color:#e7eaf0;
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    display:grid;place-items:center;}
  .box{text-align:center;padding:32px;}
  .mark{width:56px;height:56px;margin:0 auto 18px;border-radius:14px;
    background:linear-gradient(135deg,#fe2c55,#ff6a85);display:grid;place-items:center;
    box-shadow:0 8px 28px -8px rgba(254,44,85,.45);}
  .mark svg{width:28px;height:28px;stroke:#fff;fill:none;stroke-width:2;
    stroke-linecap:round;stroke-linejoin:round;}
  h1{margin:0 0 8px;font-size:20px;font-weight:650;letter-spacing:.2px;}
  p{margin:0;color:#8b94a3;font-size:13px;}
  .bar{width:120px;height:3px;margin:22px auto 0;border-radius:99px;background:#191d27;overflow:hidden;}
  .bar>i{display:block;height:100%;width:40%;border-radius:99px;background:#fe2c55;
    animation:slide 1.1s ease-in-out infinite;}
  @keyframes slide{0%{transform:translateX(-120%)}100%{transform:translateX(300%)}}
</style></head>
<body>
<div class="box">
  <div class="mark" aria-hidden="true">
    <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
  </div>
  <h1>CreatorHub</h1>
  <p>正在启动…</p>
  <div class="bar"><i></i></div>
</div>
</body></html>
"""


def _log(msg: str) -> None:
    print(f"[CreatorHub Desktop] {msg}", flush=True)


def _read_port_default(cfg_path: Path) -> int:
    port = 8000
    if not cfg_path.exists():
        return port
    in_server = False
    for raw in cfg_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line.startswith((" ", "\t")):
            in_server = line.strip() == "server:"
            continue
        if in_server and ":" in line:
            key, value = (p.strip() for p in line.split(":", 1))
            if key == "port":
                try:
                    return int(value.strip("'\""))
                except ValueError:
                    pass
    return port


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port(preferred: int) -> int:
    for port in range(preferred, preferred + 30):
        if _port_free(port):
            return port
    raise RuntimeError(f"找不到可用端口(从 {preferred} 起试了 30 个)")


def _wait_ready(url: str, timeout: float = 90.0) -> None:
    """等 /health 与首页都可访问,避免窗口打开后仍白屏。"""
    base = url.rstrip("/")
    health = f"{base}/health"
    home = f"{base}/"
    deadline = time.time() + timeout
    last_err = ""
    health_ok = False
    while time.time() < deadline:
        try:
            if not health_ok:
                with urllib.request.urlopen(health, timeout=1.5) as resp:
                    if resp.status == 200:
                        health_ok = True
            if health_ok:
                with urllib.request.urlopen(home, timeout=3.0) as resp:
                    if resp.status == 200 and len(resp.read(64)) > 0:
                        return
        except Exception as exc:
            last_err = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"后端启动超时: {last_err or health}")


def _start_server_thread(host: str, port: int) -> threading.Thread:
    """在线程里跑 uvicorn(打包态不能再 subprocess 出第二个解释器)。"""
    import uvicorn
    from app.main import app as fastapi_app

    def _run() -> None:
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )

    t = threading.Thread(target=_run, name="creatorhub-uvicorn", daemon=True)
    t.start()
    return t


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    if "-h" in argv or "--help" in argv:
        print(
            "CreatorHub 桌面壳\n"
            "  uv run python -m app.desktop [--port N]\n"
            "  python creatorhub.py desktop [--port N]\n"
            "  ./desktop.sh\n"
            "关闭窗口即停止后端。",
            flush=True,
        )
        return 0

    os.environ["CREATORHUB_DESKTOP"] = "1"
    # 必须在 import app.main 之前准备数据目录 / Playwright 路径
    from app.runtime import prepare_user_data, is_frozen, resource_dir

    data_root = prepare_user_data()
    _log(f"数据目录: {data_root}")
    if is_frozen():
        _log(f"资源目录: {resource_dir()}")
        pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if pw:
            _log(f"Playwright 浏览器: {pw}")

    preferred = _read_port_default(data_root / "config.yaml")
    if "--port" in argv:
        i = argv.index("--port")
        try:
            preferred = int(argv[i + 1])
        except (IndexError, ValueError):
            _log("--port 需要整数")
            return 2

    try:
        import webview  # type: ignore
    except ImportError:
        _log("缺少 pywebview,请先执行: uv sync")
        return 1

    host = "127.0.0.1"
    port = _pick_port(preferred)
    url = f"http://{host}:{port}"
    if port != preferred:
        _log(f"端口 {preferred} 已被占用,改用 {port}")

    window = webview.create_window(
        title="CreatorHub",
        html=_SPLASH_HTML,
        width=1280,
        height=860,
        min_size=(960, 640),
        text_select=True,
        background_color="#0A0C10",
    )

    def _boot() -> None:
        try:
            _start_server_thread(host, port)
            _wait_ready(url)
            _log(f"服务就绪: {url}")
            window.load_url(url)
        except Exception as exc:
            _log(f"错误: {exc}")
            err = (
                "<!DOCTYPE html><html><body style='margin:0;background:#0a0c10;color:#e7eaf0;"
                "font:14px/1.6 -apple-system,sans-serif;display:grid;place-items:center;height:100vh'>"
                f"<div style='max-width:420px;padding:24px'><h2 style='color:#f87171'>启动失败</h2>"
                f"<p style='color:#8b94a3'>{exc}</p></div></body></html>"
            )
            try:
                window.load_html(err)
            except Exception:
                pass

    try:
        threading.Thread(target=_boot, name="creatorhub-boot", daemon=True).start()
        webview.start(debug=bool(os.environ.get("CREATORHUB_DESKTOP_DEBUG")))
        return 0
    except KeyboardInterrupt:
        _log("已中断")
        return 130
    except Exception as exc:
        _log(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
