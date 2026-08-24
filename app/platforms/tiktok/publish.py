"""TikTok 发布入口(浏览器自动化 TikTok Studio)。

用账号专属持久 profile(已含登录态)打开 Studio 上传页,
上传视频、填标题/描述、等 Post 按钮可用后再点发布。

实验性:Studio 选择器随改版可能失效;发布时弹真实窗口,
遇验证码 / 需补封面可在窗口里手动处理。
"""
from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager
from .profile import read_tiktok_web_user

STUDIO_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

# 不要用 button:has-text("Post") —— Studio 侧栏「Posts」也会被点到,
# 页面会跳到 /tiktokstudio/content,再被误判成发布成功。
_FILE_INPUT = [
    'input[type="file"][accept*="video"]',
    'input[type="file"][accept*="mp4"]',
    'input[type="file"]',
]
_TITLE = [
    'input[data-e2e="title"]',
    '[data-e2e="video-title"] input',
    '[data-e2e="title"] input',
    'input[placeholder*="Add a title"]',
    'input[placeholder*="Title"]',
    'input[placeholder*="标题"]',
]
_CAPTION = [
    '[data-e2e="caption"] [contenteditable="true"]',
    '[data-e2e="caption-input"]',
    '[data-e2e="caption"] [contenteditable="true"] .public-DraftEditor-content',
    '.public-DraftEditor-content[contenteditable="true"]',
    '[data-e2e="video-caption"] [contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
]
_WRITE_EDITOR_JS = """(node, text) => {
  node.focus();
  try {
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(node);
    sel.removeAllRanges();
    sel.addRange(range);
  } catch (e) {}
  try { document.execCommand('selectAll', false, null); } catch (e) {}
  try { document.execCommand('delete', false, null); } catch (e) {}
  let inserted = false;
  try { inserted = document.execCommand('insertText', false, text); } catch (e) {}
  if (!inserted) {
    if (node.isContentEditable) {
      node.textContent = text;
      node.dispatchEvent(new InputEvent('input', {
        bubbles: true, data: text, inputType: 'insertText'
      }));
    } else if ('value' in node) {
      const proto = node.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value');
      if (setter && setter.set) setter.set.call(node, text);
      else node.value = text;
      node.dispatchEvent(new Event('input', {bubbles: true}));
      node.dispatchEvent(new Event('change', {bubbles: true}));
    }
  }
  return node.isContentEditable
    ? (node.innerText || node.textContent || '')
    : (node.value || '');
}"""
_POST_BTN = [
    'button[data-e2e="post_video_button"]',
    'button[data-e2e="post-button"]',
    'button[data-e2e="publish-button"]',
    '[data-e2e="post_video_button"]',
]
_NEXT_BTN = [
    'button[data-e2e="next_button"]',
    'button[data-e2e="next-button"]',
]
_CONFIRM = [
    'div[role="dialog"] button[data-e2e="post_video_button"]',
    'div[role="dialog"] button:has-text("Post now")',
    'div[role="dialog"] button:has-text("立即发布")',
    'div[role="dialog"] button:has-text("Post")',
    'div[role="dialog"] button:has-text("发布")',
    'button:has-text("Post now")',
    'button:has-text("立即发布")',
]
_DISMISS = [
    'button:has-text("Not now")',
    'button:has-text("暂不")',
    'button:has-text("Skip")',
    'button:has-text("跳过")',
]

_CLOSED_ERR = (
    "发布窗口被关掉了。请不要关弹出的 Chromium;"
    "等右下角 Post / 发布按钮亮起后再等几秒。"
    "8G 内存机器建议发布时先停掉其它平台扫描。"
)


def _log(msg: str) -> None:
    print(f"[tt-publish] {msg}", flush=True)


def _mostly_cjk(text: str) -> bool:
    chars = [c for c in (text or "") if c.isalpha() or "\u4e00" <= c <= "\u9fff"]
    if not chars:
        return False
    cjk = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff")
    return (cjk / len(chars)) >= 0.35


def _clip_title(title: str, fallback_path: str, limit: int = 150) -> str:
    t = (title or "").strip()
    if not t and fallback_path:
        t = Path(fallback_path).stem
    return t[:limit]


def _stage_english_named(src: str, title: str) -> Tuple[str, Optional[str]]:
    """用英文名做一份上传路径。Studio 默认文案吃文件名,抖音下载文件名是中文。"""
    ext = Path(src).suffix or ".mp4"
    stem = re.sub(r"[^A-Za-z0-9]+", "_", title or "video").strip("_")[:48] or "video"
    tmp = Path(tempfile.mkdtemp(prefix="tt_pub_"))
    dest = tmp / f"{stem}{ext}"
    try:
        dest.symlink_to(Path(src).resolve())
        _log(f"staged symlink {dest.name}")
        return str(dest), str(tmp)
    except Exception:
        try:
            shutil.copy2(src, dest)
            _log(f"staged copy {dest.name}")
            return str(dest), str(tmp)
        except Exception as e:
            _log(f"stage skipped: {e!r}")
            shutil.rmtree(tmp, ignore_errors=True)
            return src, None


def _cleanup_stage(tmp: Optional[str]) -> None:
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


def _is_closed_exc(exc: BaseException) -> bool:
    name = type(exc).__name__
    text = str(exc)
    return "TargetClosed" in name or "has been closed" in text


async def _alive(page) -> bool:
    try:
        return bool(page) and not page.is_closed()
    except Exception:
        return False


async def _sleep(page, ms: int) -> bool:
    if not await _alive(page):
        return False
    try:
        await page.wait_for_timeout(ms)
        return await _alive(page)
    except Exception as e:
        if _is_closed_exc(e):
            return False
        return await _alive(page)


def _login_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("/login", "login?", "/signup", "oauth"))


async def _btn_ready(loc) -> bool:
    try:
        if await loc.count() == 0:
            return False
        aria = (await loc.get_attribute("aria-disabled") or "").lower()
        if aria in ("true", "1"):
            return False
        disabled = await loc.get_attribute("disabled")
        if disabled not in (None, "", "false"):
            return False
        try:
            if not await loc.is_enabled():
                return False
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _first_ready(page, selectors) -> Optional[object]:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await _btn_ready(loc):
                return loc
        except Exception:
            continue
    return None


async def _click_loc(loc, label: str) -> bool:
    try:
        await loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    try:
        await loc.click(timeout=4000)
        _log(f"clicked {label}")
        return True
    except Exception:
        try:
            await loc.click(timeout=2500, force=True)
            _log(f"force-clicked {label}")
            return True
        except Exception as e:
            _log(f"{label} click failed: {e!r}")
            return False


async def _dismiss(page) -> None:
    for sel in _DISMISS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            if await loc.is_visible():
                await loc.click(timeout=800)
        except Exception:
            continue


async def _set_video_file(page, path: str) -> bool:
    frames = [page]
    try:
        frames.extend(f for f in page.frames if f is not page)
    except Exception:
        pass
    for frame in frames:
        for sel in _FILE_INPUT:
            try:
                loc = frame.locator(sel)
                n = await loc.count()
                for i in range(min(n, 8)):
                    try:
                        await loc.nth(i).set_input_files(path, timeout=8000)
                        _log(f"file set via {sel}")
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
    return False


async def _first_existing(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


async def _read_box(loc) -> str:
    try:
        val = await loc.evaluate(
            "(n) => (n.isContentEditable ? (n.innerText || n.textContent || '') : (n.value || ''))"
        )
        return (val or "").replace("\r", "")
    except Exception:
        return ""


def _caption_matches(got: str, want: str) -> bool:
    needle = (want or "").strip()[:16]
    if not needle:
        return True
    return needle in (got or "")


async def _write_editor(page, loc, text: str) -> bool:
    try:
        await loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    try:
        await loc.click(timeout=3000)
        await page.wait_for_timeout(120)
    except Exception:
        return False
    try:
        got = await loc.evaluate(_WRITE_EDITOR_JS, text)
        if _caption_matches(got if isinstance(got, str) else "", text):
            return True
    except Exception:
        pass
    try:
        await loc.click(timeout=1500)
        await page.keyboard.press("Meta+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(text[:800], delay=6)
    except Exception:
        return False
    return _caption_matches(await _read_box(loc), text)


async def _try_fill_text(page, caption: str, title: str) -> bool:
    """写入标题/文案并读回核对。Studio 处理完常会用文件名把文案盖掉,必须反复写。"""
    if title:
        tloc = await _first_existing(page, _TITLE)
        if tloc:
            cur = await _read_box(tloc)
            if not _caption_matches(cur, title):
                await _write_editor(page, tloc, title[:90])
    loc = await _first_existing(page, _CAPTION)
    if not loc:
        return False
    cur = await _read_box(loc)
    if _caption_matches(cur, caption):
        return True
    _log(f"caption now {(cur or '')[:40]!r}, writing ours")
    if not await _write_editor(page, loc, caption):
        return False
    cur = await _read_box(loc)
    ok = _caption_matches(cur, caption)
    _log(f"caption {'ok' if ok else 'mismatch'}: {(cur or '')[:50]!r}")
    return ok


async def _posted_ok(page) -> bool:
    """真正发布成功:跳到内容库/作品页,或出现明确成功文案。"""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if "/tiktokstudio/content" in url:
        return True
    if "tiktok.com" in url and "/video/" in url and "tiktokstudio" not in url:
        return True
    try:
        txt = (await page.inner_text("body") or "").lower()
    except Exception:
        return False
    needles = (
        "your video is being uploaded to tiktok",
        "your video has been uploaded",
        "successfully posted",
        "posted to tiktok",
        "正在上传到 tiktok",
        "已发布到 tiktok",
        "发布成功",
    )
    return any(n in txt for n in needles)


async def _wait_login_if_needed(page) -> Optional[str]:
    """在登录页则等用户手动登;已在 Studio 则放行,不靠 Cookie 误判。"""
    url = ""
    try:
        url = page.url or ""
    except Exception:
        return _CLOSED_ERR
    if not _login_url(url):
        try:
            user = await read_tiktok_web_user(page)
            if user.get("loggedIn"):
                return None
        except Exception:
            pass
        return None
    _log("on login page, waiting for manual login")
    deadline = time.time() + 90
    while time.time() < deadline:
        if not await _sleep(page, 2000):
            return _CLOSED_ERR
        try:
            url = page.url or ""
        except Exception:
            return _CLOSED_ERR
        if not _login_url(url):
            return None
    return "logged_out:TikTok 未登录,请重新完成 TikTok 登录"


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
        # 转发弹窗默认正文是抖音中文;只改了英文标题时不要再拼回去
        if _mostly_cjk(body) and caption and not _mostly_cjk(caption):
            _log("skip appending Chinese desc onto English title")
        else:
            caption = (caption + "\n\n" + body).strip()
    if tags:
        caption = (caption + "\n\n" + " ".join(f"#{t}" for t in tags)).strip()
    caption = caption[:2200]
    _log(f"caption({len(caption)}): {caption[:60]}{'…' if len(caption) > 60 else ''}")

    upload_path, stage_dir = _stage_english_named(files[0], title or caption)
    ctx = page = None
    try:
        ctx = await mgr.open_headed(identity)
        page = await ctx.new_page()
        await page.goto(STUDIO_UPLOAD, wait_until="domcontentloaded", timeout=60000)
        if not await _sleep(page, 3500):
            return False, "", _CLOSED_ERR
        login_err = await _wait_login_if_needed(page)
        if login_err:
            return False, "", login_err
        if _login_url(page.url or ""):
            return False, "", "logged_out:TikTok 未登录,请重新完成 TikTok 登录"

        if not await _set_video_file(page, upload_path):
            return False, "", "找不到上传控件,请在弹出窗口里手动选择视频后点发布"
        if not await _sleep(page, 4000):
            return False, "", _CLOSED_ERR

        clicked = False
        caption_ok = False
        deadline = time.time() + min(300, max(60, timeout_seconds))
        while time.time() < deadline:
            if not await _alive(page):
                return False, "", _CLOSED_ERR
            await _dismiss(page)
            if await _posted_ok(page):
                url = page.url or STUDIO_UPLOAD
                _log(f"done url={url}")
                return True, url, ""

            if caption and not caption_ok:
                caption_ok = await _try_fill_text(page, caption, title or "")
            elif caption and caption_ok:
                # 转码结束 Studio 常把文案改回文件名,点发布前再核对一次
                loc = await _first_existing(page, _CAPTION)
                if loc and not _caption_matches(await _read_box(loc), caption):
                    _log("caption overwritten, rewrite")
                    caption_ok = await _try_fill_text(page, caption, title or "")

            nxt = await _first_ready(page, _NEXT_BTN)
            if nxt:
                await _click_loc(nxt, "Next")
                if not await _sleep(page, 1200):
                    return False, "", _CLOSED_ERR

            loc = await _first_ready(page, _POST_BTN)
            if loc and not clicked:
                if caption:
                    caption_ok = await _try_fill_text(page, caption, title or "")
                    if not caption_ok:
                        _log("posting with unverified caption")
                clicked = await _click_loc(loc, "Post")
                if clicked:
                    if not await _sleep(page, 1200):
                        return False, "", _CLOSED_ERR
                    confirm = await _first_ready(page, _CONFIRM)
                    if confirm:
                        await _click_loc(confirm, "confirm")
            if not await _sleep(page, 2000):
                return False, "", _CLOSED_ERR

        if await _posted_ok(page):
            url = page.url or STUDIO_UPLOAD
            _log(f"done url={url}")
            return True, url, ""
        if clicked:
            return False, "", "已点发布但未确认成功,请在 Studio 里核对后重试"
        return False, "", "Post 按钮一直未就绪(视频还在处理)。请等按钮亮起后手动点发布,或稍后再点立即发布"
    except Exception as e:
        if _is_closed_exc(e):
            _log(_CLOSED_ERR)
            return False, "", _CLOSED_ERR
        error = f"发布异常: {e!r}"
        _log(error)
        return False, "", error
    finally:
        try:
            if page and await _alive(page):
                await page.wait_for_timeout(2500)
        except Exception:
            pass
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        _cleanup_stage(stage_dir)
