"""开发用 uvicorn 入口。Windows + --reload 时仍使用 Proactor 循环,Playwright 才能起 Chrome。

    uv run python -m app.serve --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 CreatorHub (Windows 热重载兼容 Playwright)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    from app.windows_loop import install
    install()

    # uvicorn --reload 会把 listen socket 传给子进程。Windows 上 Proactor/IOCP
    # 不能对已关联的继承 socket 再 Accept,热重载后报 WinError 87。
    # 父进程只监文件,子进程自己 bind,Playwright 才能继续用 Proactor。
    if args.reload and sys.platform == "win32":
        return _windows_reload(args)

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


def _is_listening(port: int) -> bool:
    """用 connect 探测,不要自己 bind:Windows 上探测 bind 会把端口打进 TIME_WAIT。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _listen_pids(port: int) -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"], text=True, errors="ignore",
        )
    except Exception:
        return []
    pids: list[int] = []
    suffix = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        state = parts[3].upper()
        if state not in ("LISTENING", "LISTEN") and "侦听" not in parts[3]:
            continue
        if parts[1].endswith(suffix):
            try:
                pids.append(int(parts[-1]))
            except ValueError:
                pass
    return list(dict.fromkeys(pid for pid in pids if pid > 0))


def _wait_not_listening(port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_listening(port):
            return True
        time.sleep(0.15)
    return not _is_listening(port)


def _reap_port(port: int, keep: set[int]) -> None:
    """清掉仍占着端口的残留进程,再等监听真正消失。"""
    keep = {pid for pid in keep if pid}
    for pid in _listen_pids(port):
        if pid in keep:
            continue
        print(f"[serve] 端口 {port} 被 PID {pid} 占用,正在结束", flush=True)
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True,
        )
    _wait_not_listening(port, timeout=12.0)


def _stop_worker(proc: subprocess.Popen, port: int) -> None:
    keep = {os.getpid()}
    if proc.poll() is None:
        keep.add(proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    _reap_port(port, keep)


def _windows_reload(args: argparse.Namespace) -> int:
    try:
        from watchfiles import PythonFilter, watch
    except ImportError:
        print("[serve] 缺少 watchfiles,无法热重载。请 uv sync 后重试。", flush=True)
        return 1

    app_dir = Path(__file__).resolve().parent
    project = app_dir.parent
    child_argv = [
        sys.executable, "-m", "app.serve",
        "--host", args.host,
        "--port", str(args.port),
    ]

    def spawn() -> subprocess.Popen:
        _reap_port(args.port, {os.getpid()})
        return subprocess.Popen(child_argv, cwd=str(project))

    print("[serve] Windows 热重载:子进程自行绑端口(避免 IOCP WinError 87)", flush=True)
    proc = spawn()
    last_spawn = time.monotonic()
    try:
        for changes in watch(str(app_dir), watch_filter=PythonFilter(),
                             yield_on_timeout=True):
            if proc.poll() is not None:
                elapsed = time.monotonic() - last_spawn
                delay = 0.4 if elapsed > 8 else 2.0
                print(f"[serve] worker 退出({proc.returncode}),{delay:.0f}s 后重启",
                      flush=True)
                time.sleep(delay)
                proc = spawn()
                last_spawn = time.monotonic()
                continue
            if not changes:
                continue
            names = sorted({Path(path).name for _, path in changes})
            print(f"[serve] 重载: {', '.join(names)}", flush=True)
            _stop_worker(proc, args.port)
            proc = spawn()
            last_spawn = time.monotonic()
    except KeyboardInterrupt:
        print("[serve] 正在停止", flush=True)
    finally:
        _stop_worker(proc, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
