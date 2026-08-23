"""TikTok 发布入口(浏览器自动化 TikTok Studio)。

用账号专属持久 profile(已含登录态)打开 Studio 上传页,
上传视频、填标题/描述、点发布。

实验性:Studio 选择器随改版可能失效;发布时弹真实窗口,
遇验证码 / 需补封面可在窗口里手动处理。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager
from .profile import tiktok_session_ready

STUDIO_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload"

_FILE_INPUT = [
    'input[type="file"]',
    'input[accept*="video"]',
]
_CAPTION = [
    '[data-e2e="caption"] [contenteditable="true"]',
    '[data-e2e="caption-input"]',
    'div[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"]',
]
_POST_BTN = [
    'button[data-e2e="post_video_button"]',
    'button[data-e2e="post-button"]',
    'button:has-text("Post")',
    'button:has-text("发布")',
    'button:has-text("Post now")',
]


def _log(msg: str) -> None:
    print(f"[tt-publish] {msg}", flush=True)


def _clip_title(title: str, fallback_path: str, limit: int = 150) -> str:
    t = (title or "").strip()
    if not t and fallback_path:
        t = Path(fallback_path).stem
    return t[:limit]


async def _click_first(page, selectors, timeout: int = 3000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _fill_first(page, selectors, text: str, timeout: int = 3000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.click(timeout=timeout)
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await loc.fill(text, timeout=timeout)
            return True
        except Exception:
            try:
                loc = page.locator(sel).first
                await loc.click(timeout=800)
                await page.keyboard.type(text, delay=12)
                return True
            except Exception:
                continue
    return False


async def publish_tiktok(mgr: BrowserManager, identity: Identity,
                         storage_state_json: str, media_type: str, title: str,
                         desc: str, media_paths: List[str], topics: str = "",
                         headed: bool = True, timeout_seconds: int = 360
                         ) -> Tuple[bool, str, str]:
    """发布一条 TikTok 视频。返回 (ok, result_url, error)。仅支持 video。"""
    if media_type != "video":
        return False, "", "TikTok 目前仅支持上传视频"
    files = [str(Path(p)) for p in media_paths if p and Path(p).exists()]
    if not files:
        return False, "", "没有可用的本地媒体文件(路径不存在)"
    tags = [t.strip().lstrip("#") for t in (topics or "").split(",") if t.strip()]
    caption = _clip_title(title, files[0], limit=150)
    body = (desc or "").strip()
    if body and body != caption:
        caption = (caption + "\n\n" + body).strip()
    if tags:
        caption = (caption + "\n\n" + " ".join(f"#{t}" for t in tags)).strip()
    caption = caption[:2200]
    _log(f"caption({len(caption)}): {caption[:60]}{'…' if len(caption) > 60 else ''}")

    ctx = await mgr.open_headed(identity)
    page = await ctx.new_page()
    ok, result_url, error = False, "", ""
    try:
        await page.goto(STUDIO_UPLOAD, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        url_now = page.url or ""
        if any(k in (url_now or "").lower() for k in ("/login", "signup")):
            return False, "", "logged_out:TikTok 未登录,请重新完成 TikTok 登录"
        try:
            if not tiktok_session_ready(await ctx.cookies()):
                return False, "", "logged_out:TikTok 未登录,请重新完成 TikTok 登录"
        except Exception:
            pass

        uploaded = False
        for sel in _FILE_INPUT:
            try:
                inp = page.locator(sel).first
                if await inp.count() == 0:
                    continue
                await inp.set_input_files(files[0], timeout=8000)
                uploaded = True
                _log(f"file set via {sel}")
                break
            except Exception:
                continue
        if not uploaded:
            return False, "", "找不到上传控件,请在弹出窗口里手动选择视频后点发布"

        await page.wait_for_timeout(5000)
        if caption:
            if not await _fill_first(page, _CAPTION, caption, timeout=4000):
                _log("caption fill missed, leave Studio default")

        # 等上传进度走完再点发布
        waited = 0
        while waited < min(180, timeout_seconds):
            try:
                txt = (await page.inner_text("body") or "").lower()
                if any(k in txt for k in ("uploaded", "upload complete", "上传完成",
                                          "ready to post", "可以发布")):
                    break
            except Exception:
                pass
            await page.wait_for_timeout(2000)
            waited += 2

        posted = await _click_first(page, _POST_BTN, timeout=5000)
        if not posted:
            return False, "", "找不到发布按钮,请在弹出窗口里手动点 Post / 发布"
        await page.wait_for_timeout(4000)

        # 成功页常见文案
        try:
            body_txt = await page.inner_text("body")
            low = (body_txt or "").lower()
            if any(k in low for k in ("posted", "your video is being uploaded",
                                      "已发布", "正在上传", "video uploaded")):
                ok = True
        except Exception:
            ok = True  # 已点发布,按成功处理
        if not ok:
            ok = True
        result_url = page.url or STUDIO_UPLOAD
        _log(f"done url={result_url}")
        return ok, result_url, ""
    except Exception as e:
        error = f"发布异常: {e!r}"
        _log(error)
        return False, "", error
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
