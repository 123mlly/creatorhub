"""直播录制:YouTube 用 yt-dlp;抖音用直播间拉流 + ffmpeg。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

log = logging.getLogger("creatorhub.live")

_SAFE_RE = re.compile(r"[\\/:*?\"<>|\r\n]+")
_FLV_KEYS = ("FULL_HD1", "HD1", "SD1", "SD2", "ORIGION", "ORIGIN")


def _safe_stem(text: str, fallback: str) -> str:
    s = _SAFE_RE.sub("_", (text or "").strip())[:80].strip(" ._")
    return s or fallback


def _ffmpeg_bin() -> str:
    which = shutil.which("ffmpeg")
    if which:
        return which
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


def _pick_stream_from_obj(obj: Any, found: List[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in ("flv_pull_url", "hls_pull_url_map", "hls_pull_url",
                      "rtmp_pull_url", "flv_url", "hls_url") and isinstance(v, (dict, str)):
                if isinstance(v, str) and v.startswith("http"):
                    found.append(v)
                elif isinstance(v, dict):
                    for pref in _FLV_KEYS:
                        if v.get(pref) and str(v[pref]).startswith("http"):
                            found.append(str(v[pref]))
                    for vv in v.values():
                        if isinstance(vv, str) and vv.startswith("http"):
                            found.append(vv)
            else:
                _pick_stream_from_obj(v, found)
    elif isinstance(obj, list):
        for it in obj:
            _pick_stream_from_obj(it, found)


def _prefer_stream(urls: List[str], quality: str = "highest") -> str:
    if not urls:
        return ""
    # 去重保序
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            seen.add(u); uniq.append(u)
    if quality == "lowest":
        return uniq[-1]
    # 优先 flv 高清
    for u in uniq:
        if ".flv" in u or "flv" in u:
            return u
    for u in uniq:
        if ".m3u8" in u:
            return u
    return uniq[0]


async def extract_douyin_stream_url(
    browser,
    identity,
    live_url: str,
    *,
    quality: str = "highest",
) -> Tuple[str, str, str]:
    """打开抖音直播间,从 webcast 接口/页面 JSON 抽出拉流地址。

    返回 (stream_url, title, error)
    """
    if not live_url:
        return "", "", "缺少直播间链接"
    found: List[str] = []
    title = ""
    page = None

    async def _on_response(resp):
        try:
            ctype = (resp.headers.get("content-type") or "").lower()
            url = resp.url or ""
            if "json" not in ctype and "webcast" not in url and "room" not in url:
                return
            if resp.status != 200:
                return
            data = await resp.json()
            _pick_stream_from_obj(data, found)
        except Exception:
            pass

    try:
        page = await browser.new_page(identity, block_media=False)
        page.on("response", _on_response)
        await page.goto(live_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        # 页面内嵌再扫一遍
        try:
            html = await page.content()
            for m in re.finditer(r'https:\\?/\\?/[^"\'\s]+?\.(?:flv|m3u8)[^"\'\s]*', html or ""):
                u = m.group(0).replace("\\/", "/")
                if u.startswith("http"):
                    found.append(u)
            tm = re.search(r'"title"\s*:\s*"([^"]{1,120})"', html or "")
            if tm:
                title = tm.group(1)
        except Exception:
            pass
        stream = _prefer_stream(found, quality)
        if not stream:
            return "", title, "未从直播间解析到拉流地址(可能未开播或页面改版)"
        return stream, title, ""
    except Exception as exc:
        return "", title, str(exc)[:300]
    finally:
        try:
            if page:
                page.remove_listener("response", _on_response)
        except Exception:
            pass
        try:
            if page:
                await page.close()
        except Exception:
            pass


def remux_to_mp4_sync(src_path: str, *, delete_src: bool = True) -> Tuple[bool, str, str]:
    """把 flv/ts 等无损转封装为 mp4。成功返回 (True, mp4_path, '')。"""
    src = Path(src_path)
    if not src.exists() or src.stat().st_size < 1024:
        return False, src_path, "源文件不存在或过小"
    if src.suffix.lower() == ".mp4":
        return True, str(src), ""
    ff = _ffmpeg_bin()
    if not ff:
        return False, src_path, "未找到 ffmpeg,无法转 mp4"
    dst = src.with_suffix(".mp4")
    cmd = [
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-c", "copy",
        "-movflags", "+faststart",
        str(dst),
    ]
    # 部分 flv 音频需要 aac bitstream filter
    cmd_bsf = [
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        str(dst),
    ]
    last_err = ""
    for attempt in (cmd_bsf, cmd):
        try:
            proc = subprocess.run(attempt, capture_output=True, text=True)
            if dst.exists() and dst.stat().st_size > 1024:
                if delete_src:
                    try:
                        src.unlink(missing_ok=True)
                    except Exception:
                        pass
                log.info("remux ok %s → %s", src.name, dst.name)
                return True, str(dst), ""
            last_err = (proc.stderr or proc.stdout or "转封装失败")[-300:]
        except Exception as exc:
            last_err = str(exc)[:300]
    return False, src_path, last_err or "转 mp4 失败"


def record_with_ffmpeg_sync(
    stream_url: str,
    out_path: str,
    *,
    user_agent: str = "",
    referer: str = "https://live.douyin.com/",
    proxy: str = "",
) -> Tuple[bool, str, str]:
    """ffmpeg 录到下播/断流。返回 (ok, path, error)。结束后若是 flv 会转成 mp4。"""
    ff = _ffmpeg_bin()
    if not ff:
        return False, "", "未找到 ffmpeg,请安装: brew install ffmpeg"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    ua = user_agent or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    headers = f"User-Agent: {ua}\r\nReferer: {referer}\r\n"
    base = [
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "15",
        "-headers", headers,
    ]
    if proxy:
        base += ["-http_proxy", proxy]
    attempts = [
        base + ["-i", stream_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", out_path],
        base + ["-i", stream_url, "-c", "copy", out_path],
    ]
    last_err = ""
    env = {**os.environ, "http_proxy": proxy, "https_proxy": proxy} if proxy else None
    recorded = ""
    try:
        for cmd in attempts:
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if Path(out_path).exists() and Path(out_path).stat().st_size > 1024:
                recorded = out_path
                break
            last_err = (proc.stderr or proc.stdout or "ffmpeg 录制失败")[-400:]
        if not recorded:
            return False, "", last_err
    except Exception as exc:
        if Path(out_path).exists() and Path(out_path).stat().st_size > 1024:
            recorded = out_path
        else:
            return False, "", str(exc)[:400]

    # flv/ts → mp4(copy),失败则仍返回原始文件
    if Path(recorded).suffix.lower() != ".mp4":
        ok_m, mp4_path, merr = remux_to_mp4_sync(recorded, delete_src=True)
        if ok_m:
            return True, mp4_path, ""
        log.warning("live remux to mp4 failed, keep original: %s", merr)
        return True, recorded, ""
    return True, recorded, ""


async def record_douyin_live(
    browser,
    identity,
    live_url: str,
    out_dir: str,
    *,
    filename_stem: str = "",
    proxy: str = "",
    user_agent: str = "",
    quality: str = "highest",
) -> Tuple[bool, str, str]:
    stream, title, err = await extract_douyin_stream_url(
        browser, identity, live_url, quality=quality)
    if not stream:
        return False, "", err or "无拉流地址"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(filename_stem or title or "live", "live")
    # flv 源先落 flv 再转 mp4(直播中途写 mp4 容易花屏/损坏)
    ext = ".flv" if ".flv" in stream.split("?")[0] else ".mp4"
    out_path = str(Path(out_dir) / f"{stem}{ext}")
    return await asyncio.to_thread(
        record_with_ffmpeg_sync,
        stream,
        out_path,
        user_agent=user_agent,
        referer="https://live.douyin.com/",
        proxy=proxy,
    )


def record_live_stream_sync(
    watch_url: str,
    out_dir: str,
    *,
    filename_stem: str = "",
    proxy: str = "",
    cookie_file: str = "",
    user_agent: str = "",
    quality: str = "highest",
    platform: str = "",
) -> Tuple[bool, str, str]:
    """录制直播(YouTube / 通用 yt-dlp)。返回 (ok, path, error)。"""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return False, "", "缺少 yt-dlp 依赖"

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(filename_stem, "live")
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
        "retries": 10,
        "fragment_retries": 20,
        "skip_unavailable_fragments": True,
        "live_from_start": False,
        "http_headers": {
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.youtube.com/",
        },
    }
    if platform == "youtube":
        from ..youtube.fetcher import apply_yt_dlp_youtube_opts
        apply_yt_dlp_youtube_opts(opts)
    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(watch_url, download=True)
        if not info:
            return False, "", "yt-dlp 未返回录制结果"
        req = info.get("requested_downloads") or []
        if req and req[0].get("filepath"):
            path = req[0]["filepath"]
            if path and Path(path).exists():
                return True, path, ""
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                path = ydl.prepare_filename(info)
            p = Path(path)
            for cand in (p, p.with_suffix(".mp4"), p.with_suffix(".mkv"),
                         p.with_suffix(".webm"), p.with_suffix(".ts")):
                if cand.exists() and cand.stat().st_size > 0:
                    return True, str(cand), ""
        except Exception:
            pass
        matches = sorted(Path(out_dir).glob(f"{stem}.*"),
                         key=lambda x: x.stat().st_mtime, reverse=True)
        for cand in matches:
            if cand.is_file() and cand.stat().st_size > 0:
                return True, str(cand), ""
        return False, "", "录制结束但找不到输出文件"
    except Exception as exc:
        return False, "", str(exc)[:500]


async def record_live_stream(
    watch_url: str,
    out_dir: str,
    *,
    filename_stem: str = "",
    proxy: str = "",
    state_json: str = "",
    user_agent: str = "",
    quality: str = "highest",
    platform: str = "",
) -> Tuple[bool, str, str]:
    cookie_file = ""
    if state_json and platform == "youtube":
        from ..youtube.fetcher import storage_state_to_cookiefile
        cookie_file = storage_state_to_cookiefile(state_json)
    try:
        return await asyncio.to_thread(
            record_live_stream_sync,
            watch_url,
            out_dir,
            filename_stem=filename_stem,
            proxy=proxy,
            cookie_file=cookie_file,
            user_agent=user_agent,
            quality=quality,
            platform=platform,
        )
    finally:
        if cookie_file:
            try:
                Path(cookie_file).unlink(missing_ok=True)
            except Exception:
                pass
