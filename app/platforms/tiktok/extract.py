"""把 yt-dlp 条目转成统一的 Aweme / MediaItem。"""
from __future__ import annotations

from typing import Any, Optional

from ..douyin.extract import Aweme, MediaItem, safe_title  # noqa: F401

from .resolve import resolve_tiktok_video_id, video_watch_url


def _to_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_tt_entry(info: dict, *, quality: str = "highest") -> Optional[Aweme]:
    """从 yt-dlp extract_info(单条或 playlist entry) 构造 Aweme。"""
    if not isinstance(info, dict):
        return None
    raw_id = (info.get("id") or info.get("url") or "").strip()
    vid = resolve_tiktok_video_id(raw_id) or raw_id
    if not vid:
        return None
    title = (info.get("title") or info.get("fulltitle")
             or info.get("description") or "").strip()
    author = (info.get("uploader") or info.get("creator")
              or info.get("channel") or "tiktok").strip()
    handle = (info.get("uploader_id") or info.get("channel_id")
              or info.get("creator") or "").strip()
    if handle and not handle.startswith("@") and not handle.isdigit():
        handle = "@" + handle
    ts = _to_int(info.get("timestamp") or info.get("release_timestamp"))
    if not ts:
        ud = str(info.get("upload_date") or "")
        if len(ud) == 8 and ud.isdigit():
            import datetime as _dt
            try:
                ts = int(_dt.datetime(int(ud[:4]), int(ud[4:6]), int(ud[6:8])).timestamp())
            except Exception:
                ts = 0
    cover = (info.get("thumbnail") or "")
    if not cover:
        thumbs = info.get("thumbnails") or []
        if isinstance(thumbs, list) and thumbs:
            cover = (thumbs[-1] or {}).get("url") or ""
    watch = (info.get("webpage_url")
             or video_watch_url(vid, handle or author))
    aw = Aweme(
        aweme_id=vid,
        desc=title or vid,
        create_time=ts,
        author_name=author,
        media_type="video",
        medias=[MediaItem(url=watch, kind="video", ext="mp4", index=0)],
        cover=cover or "",
        quality_label=quality or "",
        like_count=_to_int(info.get("like_count")),
        comment_count=_to_int(info.get("comment_count")),
        duration=_to_int(info.get("duration")),
        avatar="",
        platform="tiktok",
    )
    return aw


def parse_tt_user_meta(info: dict) -> dict:
    """从用户主页 extract_info 顶层拿昵称/头像。"""
    if not isinstance(info, dict):
        return {}
    handle = (info.get("uploader_id") or info.get("channel_id")
              or info.get("id") or "").strip()
    if handle and not str(handle).startswith("@") and not str(handle).isdigit():
        handle = "@" + handle
    nick = (info.get("uploader") or info.get("channel")
            or info.get("creator") or info.get("title") or "").strip()
    for suf in (" - Videos", " - TikTok", " on TikTok"):
        if nick.endswith(suf):
            nick = nick[: -len(suf)].strip()
    avatar = ""
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        avatar = (thumbs[-1] or {}).get("url") or ""
    return {
        "nickname": nick,
        "sec_uid": str(info.get("channel_id") or info.get("uploader_id") or handle),
        "douyin_id": handle if str(handle).startswith("@") else (f"@{handle}" if handle else ""),
        "avatar": avatar,
        "follower_count": _to_int(info.get("channel_follower_count")
                                  or info.get("follower_count")),
        "aweme_count": _to_int(info.get("playlist_count")
                               or info.get("n_entries")),
    }
