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


def parse_tt_play_hit(hit: dict, *, quality: str = "highest") -> Optional[Aweme]:
    """从浏览器拦截到的播放信息构造 Aweme(media.url 必须是可直下的 CDN 链)。"""
    if not isinstance(hit, dict):
        return None
    play = str(hit.get("play_url") or "").strip()
    if not play.startswith("http"):
        return None
    vid = str(hit.get("id") or "").strip()
    if not vid:
        vid = resolve_tiktok_video_id(play) or "tiktok"
    handle = str(hit.get("handle") or "").strip().lstrip("@")
    author = str(hit.get("author") or handle or "tiktok").strip() or "tiktok"
    title = str(hit.get("title") or vid).strip() or vid
    cover = str(hit.get("cover") or "").strip()
    return Aweme(
        aweme_id=vid,
        desc=title,
        create_time=_to_int(hit.get("create_time")),
        author_name=author,
        media_type="video",
        medias=[MediaItem(url=play, kind="video", ext="mp4", index=0)],
        cover=cover,
        quality_label=quality or "",
        like_count=_to_int(hit.get("like_count")),
        comment_count=_to_int(hit.get("comment_count")),
        duration=_to_int(hit.get("duration")),
        avatar=str(hit.get("avatar") or ""),
        platform="tiktok",
        author_id=handle,
    )


def tt_watch_candidates(aweme) -> list[str]:
    """监控下载要试的观看页:已存链接、短剧页、普通 /video/ 页。"""
    from .resolve import handle_from_user_ref, is_tiktok_shortdrama, video_watch_url
    url = ""
    if getattr(aweme, "medias", None):
        url = str(aweme.medias[0].url or "")
    vid = str(getattr(aweme, "aweme_id", "") or "")
    handle = handle_from_user_ref(url)
    if not handle:
        handle = str(getattr(aweme, "author_id", "") or "").strip().lstrip("@")
    if not handle:
        name = str(getattr(aweme, "author_name", "") or "").strip().lstrip("@")
        if name and " " not in name and "/" not in name:
            handle = name
    author = str(getattr(aweme, "author_name", "") or "").strip().lstrip("@").lower()
    # video_watch_url 缺 handle 时会写成 @tiktok,那是官方号,不要拿来当下载体
    if handle.lower() == "tiktok" and author not in ("", "tiktok"):
        handle = ""
    drama = is_tiktok_shortdrama(url)
    out: list[str] = []

    def add(u: str) -> None:
        u = (u or "").split("?")[0].rstrip("/")
        if u.startswith("http") and u not in out:
            out.append(u)

    if url and not tt_media_is_direct(url):
        add(url)
    if vid:
        if drama:
            add(f"https://www.tiktok.com/shortdrama/episode/{vid}/1")
        if handle:
            add(video_watch_url(vid, handle))
    return out


def tt_media_is_direct(url: str) -> bool:
    """是否为可直下的 CDN,而不是 /@user/video/{id} 观看页。"""
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    if "/video/tos/" in u or u.split("?")[0].endswith(".mp4"):
        return True
    if "tiktok.com" in u and "/video/" in u:
        return False
    return False


def parse_tt_web_item(obj: dict, *, quality: str = "highest") -> Optional[Aweme]:
    """浏览器拦截的 itemStruct(带 _hit)或 yt-dlp 条目。"""
    if not isinstance(obj, dict):
        return None
    hit = obj.get("_hit")
    if isinstance(hit, dict) and hit.get("id"):
        if hit.get("play_url"):
            return parse_tt_play_hit(hit, quality=quality)
        handle = str(hit.get("handle") or "").lstrip("@")
        if str(hit.get("kind") or "") == "shortdrama" or not handle:
            webpage = f"https://www.tiktok.com/shortdrama/episode/{hit['id']}/1"
        else:
            webpage = video_watch_url(hit["id"], handle)
        return parse_tt_entry({
            "id": hit["id"],
            "title": hit.get("title") or hit["id"],
            "uploader": hit.get("author") or "tiktok",
            "uploader_id": handle,
            "thumbnail": hit.get("cover") or "",
            "timestamp": hit.get("create_time") or 0,
            "like_count": hit.get("like_count") or 0,
            "comment_count": hit.get("comment_count") or 0,
            "duration": hit.get("duration") or 0,
            "webpage_url": webpage,
        }, quality=quality)
    return parse_tt_entry(obj, quality=quality)


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
        author_id=handle.lstrip("@") if handle else "",
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
