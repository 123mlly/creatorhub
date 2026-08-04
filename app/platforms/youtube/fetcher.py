"""YouTube 频道作品抓取(yt-dlp flat playlist)。

不走浏览器拦截:YouTube 对自动化限制严,yt-dlp 已是本仓库分享下载的基础。
可选 cookiefile(从账号 storage_state 导出)以访问年龄限制/会员内容。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .extract import parse_yt_channel_meta, parse_yt_entry
from .resolve import channel_feed_url

log = logging.getLogger("creatorhub.youtube")

# 粘贴时可能只挂在 .google.com;导出 cookiefile 时需镜像到 .youtube.com
_GOOGLE_AUTH_COOKIE_NAMES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS", "SIDCC",
    "__Secure-1PSIDCC", "__Secure-3PSIDCC",
}


def yt_dlp_js_runtimes() -> Dict[str, dict]:
    """探测本机可用的 JS runtime(优先 node)。新版 yt-dlp 解 YouTube n challenge 需要。"""
    runtimes: Dict[str, dict] = {}
    # 顺序:node 最常见;deno 是 yt-dlp 默认;bun 兼容
    for name in ("node", "deno", "bun"):
        if shutil.which(name):
            runtimes[name] = {}
    return runtimes


def apply_yt_dlp_youtube_opts(opts: Dict[str, Any]) -> Dict[str, Any]:
    """给 yt-dlp 选项补上 YouTube 所需的 JS runtime + EJS remote components。"""
    js = yt_dlp_js_runtimes()
    if js:
        opts["js_runtimes"] = js
    else:
        log.warning("未检测到 node/deno/bun,YouTube 解 n challenge 可能失败;"
                    "请安装: brew install node 或 brew install deno")
    # 允许从 GitHub 拉取 challenge solver 脚本(yt-dlp 默认禁用远程组件)
    opts["remote_components"] = {"ejs:github"}
    return opts


def storage_state_to_cookiefile(state_json: str) -> str:
    """把 Playwright storage_state JSON 写成 Netscape cookiefile,返回路径(空=失败)。

    - expires<0(Playwright 会话 Cookie)规范为 0,避免被当成已过期
    - .google.com 上的认证 Cookie 镜像一份到 .youtube.com,保证 yt-dlp 请求 YT 时带得上
    """
    if not state_json:
        return ""
    try:
        state = json.loads(state_json)
    except Exception:
        return ""
    cookies = state.get("cookies") or []
    if not cookies:
        return ""
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# This is a generated file! Do not edit.",
        "",
    ]
    seen: Set[Tuple[str, str]] = set()

    def _append(domain: str, path: str, secure: bool, expires: int,
                name: str, value: str) -> None:
        key = (domain, name)
        if key in seen or not name:
            return
        seen.add(key)
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        sec = "TRUE" if secure else "FALSE"
        lines.append("\t".join([
            domain, flag, path or "/", sec, str(max(0, expires)), name, value,
        ]))

    for c in cookies:
        name = c.get("name") or ""
        value = c.get("value") or ""
        if not name:
            continue
        domain = (c.get("domain") or ".youtube.com").strip() or ".youtube.com"
        path = c.get("path") or "/"
        secure = bool(c.get("secure"))
        try:
            expires = int(float(c.get("expires") or 0))
        except (TypeError, ValueError):
            expires = 0
        if expires < 0:
            expires = 0
        _append(domain, path, secure, expires, name, value)
        # Google 认证 Cookie:无论原 domain,都保证 .youtube.com 有一份
        is_google_auth = (
            name in _GOOGLE_AUTH_COOKIE_NAMES
            or "google.com" in domain
        )
        if is_google_auth and "youtube.com" not in domain:
            _append(".youtube.com", path, True, expires, name, value)

    if len(lines) <= 4:
        return ""
    try:
        fd, path = tempfile.mkstemp(prefix="yt-cookies-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except Exception:
        return ""


def _ydl_base(proxy: str = "", cookie_file: str = "", user_agent: str = "") -> dict:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": False,
        "playlistend": 50,
        "socket_timeout": 30,
        "retries": 2,
        "http_headers": {
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    }
    apply_yt_dlp_youtube_opts(opts)
    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file
    return opts


def fetch_channel_entries_sync(
    channel_ref: str,
    *,
    known_ids: Optional[Set[str]] = None,
    limit: int = 30,
    proxy: str = "",
    cookie_file: str = "",
    user_agent: str = "",
) -> Tuple[List[dict], dict, str]:
    """同步拉取频道最近作品。返回 (entries, channel_meta, error)。"""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return [], {}, "缺少 yt-dlp 依赖,请先执行: uv sync"

    url = channel_feed_url(channel_ref)
    if not url:
        return [], {}, "无效的 YouTube 频道链接"
    known = known_ids or set()
    opts = _ydl_base(proxy=proxy, cookie_file=cookie_file, user_agent=user_agent)
    opts["playlistend"] = max(1, min(int(limit or 30), 100))
    error = ""
    entries: List[dict] = []
    meta: dict = {}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return [], {}, "yt-dlp 未返回频道数据"
        meta = parse_yt_channel_meta(info)
        raw = info.get("entries") or []
        for e in raw:
            if not isinstance(e, dict):
                continue
            eid = (e.get("id") or "").strip()
            if not eid or eid in known:
                continue
            entries.append(e)
            if len(entries) >= opts["playlistend"]:
                break
    except Exception as exc:
        error = str(exc)
        log.warning("youtube fetch channel failed: %s", error)
    return entries, meta, error


async def fetch_youtube_videos(
    channel_ref: str,
    known_ids: Optional[Set[str]] = None,
    *,
    limit: int = 30,
    proxy: str = "",
    state_json: str = "",
    user_agent: str = "",
) -> Tuple[List[dict], dict, str]:
    cookie_file = storage_state_to_cookiefile(state_json) if state_json else ""
    try:
        return await asyncio.to_thread(
            fetch_channel_entries_sync,
            channel_ref,
            known_ids=known_ids,
            limit=limit,
            proxy=proxy,
            cookie_file=cookie_file,
            user_agent=user_agent,
        )
    finally:
        if cookie_file:
            try:
                Path(cookie_file).unlink(missing_ok=True)
            except Exception:
                pass


def download_youtube_video_sync(
    watch_url: str,
    out_dir: str,
    *,
    filename_stem: str = "",
    proxy: str = "",
    cookie_file: str = "",
    user_agent: str = "",
    quality: str = "highest",
) -> Tuple[bool, str, str]:
    """用 yt-dlp 下载单条视频到 out_dir。返回 (ok, path, error)。"""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return False, "", "缺少 yt-dlp 依赖"

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stem = filename_stem or "%(id)s_%(title).80B"
    outtmpl = str(Path(out_dir) / f"{stem}.%(ext)s")
    # 画质: highest → bestvideo+bestaudio / 数字 → 高度上限
    if quality in ("", "highest", "best"):
        fmt = "bv*+ba/b"
    elif quality == "lowest":
        fmt = "wv*+wa/w"
    elif quality.isdigit():
        fmt = f"bv*[height<={quality}]+ba/b[height<={quality}]/b"
    else:
        fmt = "bv*+ba/b"

    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "outtmpl": outtmpl,
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    }
    apply_yt_dlp_youtube_opts(opts)
    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(watch_url, download=True)
        if not info:
            return False, "", "yt-dlp 未返回下载结果"
        # 解析实际落地文件
        req = info.get("requested_downloads") or []
        if req and req[0].get("filepath"):
            path = req[0]["filepath"]
            return True, path, ""
        # fallback: prepare_filename
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                path = ydl.prepare_filename(info)
            if path and Path(path).exists():
                return True, path, ""
            # 可能扩展名被 merge 成 mp4
            p = Path(path)
            for cand in (p, p.with_suffix(".mp4"), p.with_suffix(".mkv"), p.with_suffix(".webm")):
                if cand.exists():
                    return True, str(cand), ""
        except Exception:
            pass
        return False, "", "下载完成但找不到输出文件"
    except Exception as exc:
        return False, "", str(exc)


async def download_youtube_video(
    watch_url: str,
    out_dir: str,
    *,
    filename_stem: str = "",
    proxy: str = "",
    state_json: str = "",
    user_agent: str = "",
    quality: str = "highest",
) -> Tuple[bool, str, str]:
    cookie_file = storage_state_to_cookiefile(state_json) if state_json else ""
    try:
        return await asyncio.to_thread(
            download_youtube_video_sync,
            watch_url,
            out_dir,
            filename_stem=filename_stem,
            proxy=proxy,
            cookie_file=cookie_file,
            user_agent=user_agent,
            quality=quality,
        )
    finally:
        if cookie_file:
            try:
                Path(cookie_file).unlink(missing_ok=True)
            except Exception:
                pass
