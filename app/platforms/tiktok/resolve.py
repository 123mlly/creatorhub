"""从 TikTok 链接解析 @handle / user / video_id。

支持:
  - https://www.tiktok.com/@handle
  - https://www.tiktok.com/@handle/video/1234567890
  - https://vm.tiktok.com/xxxxx / https://vt.tiktok.com/xxxxx
  - 纯 @handle / 数字 video id
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

_HANDLE_RE = re.compile(r"^@[\w.-]{2,24}$")
_VIDEO_ID_RE = re.compile(r"^\d{8,25}$")
_VIDEO_PATH_RE = re.compile(r"/video/(\d{8,25})")
_PHOTO_PATH_RE = re.compile(r"/photo/(\d{8,25})")
_SHORTDRAMA_RE = re.compile(r"/shortdrama/episode/(\d{8,25})(?:/\d+)?", re.I)
_HANDLE_PATH_RE = re.compile(r"/(@[\w.-]{2,24})")
_SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com", "m.tiktok.com")


def looks_like_video(text: str) -> bool:
    text = (text or "").strip()
    if _VIDEO_ID_RE.match(text):
        return True
    if (_VIDEO_PATH_RE.search(text) or _PHOTO_PATH_RE.search(text)
            or _SHORTDRAMA_RE.search(text)):
        return True
    host = (urlparse(text).hostname or "").lower()
    return any(host.endswith(h) for h in _SHORT_HOSTS)


def resolve_tiktok_video_id(text: str) -> Optional[str]:
    text = (text or "").strip()
    if _VIDEO_ID_RE.match(text):
        return text
    m = (_VIDEO_PATH_RE.search(text) or _PHOTO_PATH_RE.search(text)
         or _SHORTDRAMA_RE.search(text))
    return m.group(1) if m else None


def is_tiktok_shortdrama(url: str) -> bool:
    return bool(_SHORTDRAMA_RE.search(url or ""))


def shortdrama_parts(url: str) -> tuple[str, int]:
    """/shortdrama/episode/{seriesId}/{集数}。集数缺省为 1。"""
    m = _SHORTDRAMA_RE.search(url or "")
    if not m:
        return "", 1
    episode = 1
    tail = re.search(r"/shortdrama/episode/\d{8,25}/(\d{1,4})", url or "", re.I)
    if tail:
        episode = max(1, int(tail.group(1)))
    return m.group(1), episode


def shortdrama_episode_url(series_id: str, episode: int) -> str:
    return f"https://www.tiktok.com/shortdrama/episode/{series_id}/{max(1, int(episode))}"


def canonicalize_tiktok_share_url(url: str) -> str:
    """yt-dlp 不认 /shortdrama/episode/{id}/n,改写成普通作品页再提取。"""
    url = (url or "").strip()
    m = _SHORTDRAMA_RE.search(url)
    if not m:
        return url
    rewritten = video_watch_url(m.group(1))
    if rewritten != url.split("?")[0].rstrip("/"):
        print(f"[share] tiktok shortdrama -> {rewritten}", flush=True)
    return rewritten


def resolve_tiktok_user_ref(text: str) -> Optional[str]:
    """返回可用于 yt-dlp 的用户标识:@handle 或规范化主页 URL。"""
    text = (text or "").strip()
    if not text:
        return None
    if _HANDLE_RE.match(text):
        return text
    if text.startswith("@") and 2 <= len(text) <= 25:
        return text
    if looks_like_video(text) and not _HANDLE_PATH_RE.search(text):
        # 短链/纯视频 id 不能当监控目标,交给单条下载
        return None
    m = _HANDLE_PATH_RE.search(text)
    if m:
        return m.group(1)
    host = (urlparse(text).hostname or "").lower()
    if "tiktok.com" in host and not looks_like_video(text):
        return text.split("?")[0].rstrip("/")
    # 裸 handle(无 @)
    if re.fullmatch(r"[\w.-]{2,24}", text) and not _VIDEO_ID_RE.match(text):
        return "@" + text
    return None


def handle_from_user_ref(user_ref: str) -> str:
    """从 @handle / 主页 URL 取出 uniqueId(不含 @)。"""
    ref = (user_ref or "").strip()
    if not ref:
        return ""
    m = _HANDLE_PATH_RE.search(ref)
    if m:
        return m.group(1).lstrip("@")
    if ref.startswith("@"):
        return ref[1:].split("/")[0].split("?")[0]
    if ref.startswith("http"):
        return ""
    return ref.lstrip("@")


def user_feed_url(user_ref: str) -> str:
    """把 user_ref 变成 yt-dlp 可拉列表的主页 URL。"""
    ref = (user_ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("http"):
        return ref.split("?")[0].rstrip("/")
    if not ref.startswith("@"):
        ref = "@" + ref.lstrip("@")
    return f"https://www.tiktok.com/{ref}"


def video_watch_url(video_id: str, handle: str = "") -> str:
    hid = (handle or "tiktok").lstrip("@") or "tiktok"
    return f"https://www.tiktok.com/@{hid}/video/{video_id}"
