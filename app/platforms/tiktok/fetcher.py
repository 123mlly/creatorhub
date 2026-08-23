"""TikTok 用户作品抓取(yt-dlp)。

可选 cookiefile(从账号 storage_state 导出)以提高成功率、访问登录可见内容。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .extract import parse_tt_user_meta
from .resolve import user_feed_url

log = logging.getLogger("creatorhub.tiktok")


def storage_state_to_cookiefile(state_json: str) -> str:
    """把 Playwright storage_state JSON 写成 Netscape cookiefile,返回路径(空=失败)。"""
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
        domain = (c.get("domain") or ".tiktok.com").strip() or ".tiktok.com"
        if "tiktok.com" not in domain:
            continue
        path = c.get("path") or "/"
        secure = bool(c.get("secure"))
        try:
            expires = int(float(c.get("expires") or 0))
        except (TypeError, ValueError):
            expires = 0
        if expires < 0:
            expires = 0
        _append(domain, path, secure, expires, name, value)

    if len(lines) <= 4:
        return ""
    try:
        fd, path = tempfile.mkstemp(prefix="tt-cookies-", suffix=".txt")
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
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.6",
            "Referer": "https://www.tiktok.com/",
        },
    }
    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file
    return opts


def fetch_user_entries_sync(
    user_ref: str,
    *,
    known_ids: Optional[Set[str]] = None,
    limit: int = 30,
    proxy: str = "",
    cookie_file: str = "",
    user_agent: str = "",
) -> Tuple[List[dict], dict, str]:
    """同步拉取用户最近作品。返回 (entries, user_meta, error)。"""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return [], {}, "缺少 yt-dlp 依赖,请先执行: uv sync"

    url = user_feed_url(user_ref)
    if not url:
        return [], {}, "无效的 TikTok 主页链接"
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
            return [], {}, "yt-dlp 未返回用户数据"
        meta = parse_tt_user_meta(info)
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
        log.warning("tiktok fetch user failed: %s", error)
    return entries, meta, error


async def fetch_tiktok_videos(
    user_ref: str,
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
            fetch_user_entries_sync,
            user_ref,
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


def download_tiktok_video_sync(
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
            "Referer": "https://www.tiktok.com/",
        },
    }
    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(watch_url, download=True)
        if not info:
            return False, "", "yt-dlp 未返回下载结果"
        req = info.get("requested_downloads") or []
        if req and req[0].get("filepath"):
            return True, req[0]["filepath"], ""
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                path = ydl.prepare_filename(info)
            if path and Path(path).exists():
                return True, path, ""
            p = Path(path)
            for cand in (p, p.with_suffix(".mp4"), p.with_suffix(".mkv"), p.with_suffix(".webm")):
                if cand.exists():
                    return True, str(cand), ""
        except Exception:
            pass
        return False, "", "下载完成但找不到输出文件"
    except Exception as exc:
        return False, "", str(exc)


async def download_tiktok_video(
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
            download_tiktok_video_sync,
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
