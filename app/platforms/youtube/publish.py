"""YouTube 发布入口(浏览器自动化 YouTube Studio)。

用账号专属持久 profile(已含 Google/YouTube 登录态)打开 Studio,
上传视频、填标题/描述、点发布。

⚠️ 实验性:Studio 选择器随改版可能失效;发布时弹真实窗口,
   遇验证码 / 频道未创建 / 需补缩略图可在窗口里手动处理。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

STUDIO_URL = "https://studio.youtube.com/"
_DEBUG_DIR = Path("./data/debug")

_UPLOAD_BTN = [
    '#upload-icon', '#upload-button',
    'ytcp-button#upload-button',
    'button[aria-label*="Upload"]', 'button[aria-label*="上传"]',
    'ytcp-button:has-text("Upload")', 'ytcp-button:has-text("上传")',
    'tp-yt-paper-button:has-text("UPLOAD")',
]
_CREATE_BTN = [
    '#create-icon', '#create-button',
    'ytcp-button#create-button',
    'button[aria-label*="Create"]', 'button[aria-label*="创建"]',
]
_UPLOAD_MENU = [
    'tp-yt-paper-item:has-text("Upload videos")',
    'tp-yt-paper-item:has-text("上传视频")',
    'text=Upload videos', 'text=上传视频',
]
_TITLE_SEL = [
    '#textbox[aria-label*="title"]',
    '#textbox[aria-label*="标题"]',
    'div#textbox[contenteditable="true"]',
    '#title-textarea #textbox',
    'ytcp-social-suggestions-textbox#title-textarea #textbox',
]
_DESC_SEL = [
    '#textbox[aria-label*="description"]',
    '#textbox[aria-label*="说明"]',
    '#textbox[aria-label*="描述"]',
    '#description-textarea #textbox',
    'ytcp-social-suggestions-textbox#description-textarea #textbox',
]
_NEXT_BTN = [
    '#next-button', 'ytcp-button#next-button',
    'button:has-text("Next")', 'button:has-text("下一步")',
]
_PUBLISH_BTN = [
    '#done-button', 'ytcp-button#done-button',
    'button:has-text("Publish")', 'button:has-text("发布")',
    'button:has-text("Save")', 'button:has-text("保存")',
]
_PUBLIC_RADIO = [
    'tp-yt-paper-radio-button[name="PUBLIC"]',
    '#privacy-radios [name="PUBLIC"]',
    'tp-yt-paper-radio-button:has-text("Public")',
    'tp-yt-paper-radio-button:has-text("公开")',
]


def _log(msg: str) -> None:
    print(f"[yt-publish] {msg}", flush=True)


async def _click_first(page, selectors, timeout=3500) -> bool:
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


async def _fill_editable(page, selectors, text, timeout=4000) -> bool:
    if not text:
        return True
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.click(timeout=timeout)
            await page.wait_for_timeout(200)
            # contenteditable:全选后输入
            await page.keyboard.press("Meta+a")
            await page.keyboard.press("Control+a")
            await page.keyboard.type(text[:100 if "title" in sel.lower() or "标题" in sel else 5000],
                                    delay=8)
            return True
        except Exception:
            continue
    return False


async def _dump(page, tag: str) -> str:
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        png = str(_DEBUG_DIR / f"yt_publish_{tag}_{stamp}.png")
        try:
            await page.screenshot(path=png, full_page=True)
        except Exception:
            png = ""
        _log(f"debug dump {tag}: url={page.url} shot={png}")
        return png
    except Exception:
        return ""


async def publish_youtube(mgr: BrowserManager, identity: Identity,
                          storage_state_json: str, media_type: str, title: str,
                          desc: str, media_paths: List[str], topics: str = "",
                          headed: bool = True, timeout_seconds: int = 360
                          ) -> Tuple[bool, str, str]:
    """发布一条 YouTube 视频。返回 (ok, result_url, error)。
    仅支持 video;图集请先合成视频。storage_state_json 仅校验用(登录态在持久 profile)。"""
    if media_type != "video":
        return False, "", "YouTube 目前仅支持上传视频(请选择「视频」类型)"
    files = [str(Path(p)) for p in media_paths if p and Path(p).exists()]
    if not files:
        return False, "", "没有可用的本地媒体文件(路径不存在)"
    tags = [t.strip().lstrip("#") for t in (topics or "").split(",") if t.strip()]
    body = (desc or "").strip()
    if tags:
        body = (body + "\n\n" + " ".join(f"#{t}" for t in tags)).strip()
    title = (title or Path(files[0]).stem)[:100]

    ctx = await mgr.open_headed(identity)
    page = await ctx.new_page()
    ok, result_url, error = False, "", ""
    try:
        await page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        url_now = page.url or ""
        if "accounts.google.com" in url_now or "ServiceLogin" in url_now:
            return False, "", "logged_out:YouTube Studio 未登录,请重新完成 YouTube 登录"
        if "channel_create" in url_now or "create_channel" in url_now:
            return False, "", "该 Google 账号尚未创建 YouTube 频道,请先在浏览器里创建频道"

        # 打开上传:优先点 Create → Upload videos,否则点 upload 图标
        opened = False
        if await _click_first(page, _CREATE_BTN, timeout=4000):
            await page.wait_for_timeout(800)
            opened = await _click_first(page, _UPLOAD_MENU, timeout=4000)
        if not opened:
            opened = await _click_first(page, _UPLOAD_BTN, timeout=4000)
        if not opened:
            await _dump(page, "no_upload_btn")
            return False, "", "未找到上传入口(Studio 可能改版)"

        await page.wait_for_timeout(1500)
        try:
            # 对话框里的 file input(可能多个,取第一个可见/可用)
            inp = page.locator('input[type="file"]').first
            await inp.set_input_files(files[:1], timeout=20000)
        except Exception as e:
            await _dump(page, "set_files")
            return False, "", f"上传文件失败: {e!r}"

        # 等详情页标题框出现(上传中也会先出详情表单)
        title_ready = False
        for _ in range(60):
            for sel in _TITLE_SEL:
                try:
                    if await page.locator(sel).first.is_visible():
                        title_ready = True
                        break
                except Exception:
                    continue
            if title_ready:
                break
            await page.wait_for_timeout(2000)
        if not title_ready:
            await _dump(page, "no_title")
            return False, "", "上传后未出现标题编辑框(请到弹出窗口手动完成,或检查网络/验证)"

        await _fill_editable(page, _TITLE_SEL, title)
        await page.wait_for_timeout(500)
        if body:
            await _fill_editable(page, _DESC_SEL, body[:5000])
        await page.wait_for_timeout(800)

        # 连点 Next 直到可见性步骤(Details → Video elements → Checks → Visibility)
        for step in range(5):
            if not await _click_first(page, _NEXT_BTN, timeout=5000):
                break
            await page.wait_for_timeout(1200)

        # 选公开
        await _click_first(page, _PUBLIC_RADIO, timeout=4000)
        await page.wait_for_timeout(600)

        if not await _click_first(page, _PUBLISH_BTN, timeout=6000):
            await _dump(page, "no_publish")
            return False, "", "未找到发布/完成按钮(请到弹出窗口手动点 Publish)"

        # 等成功:链接 / 文案
        deadline = timeout_seconds
        waited = 0
        while waited < deadline:
            # 抓结果链接
            try:
                for sel in (
                    "a[href*='youtu.be/']",
                    "a[href*='youtube.com/watch']",
                    "a[href*='studio.youtube.com/video/']",
                ):
                    loc = page.locator(sel).first
                    if await loc.count():
                        href = await loc.get_attribute("href") or ""
                        if href:
                            if href.startswith("//"):
                                href = "https:" + href
                            elif href.startswith("/"):
                                href = "https://www.youtube.com" + href
                            # studio edit → watch
                            m = re.search(r"/video/([A-Za-z0-9_-]{11})", href)
                            if m:
                                href = f"https://www.youtube.com/watch?v={m.group(1)}"
                            result_url = href
                            ok = True
                            break
                if ok:
                    break
            except Exception:
                pass
            for kw in ("Video published", "已发布", "Published", "上传完毕", "Processing"):
                try:
                    if await page.get_by_text(kw, exact=False).first.is_visible():
                        ok = True
                        break
                except Exception:
                    continue
            if ok:
                break
            await page.wait_for_timeout(2000)
            waited += 2

        if ok and not result_url:
            result_url = page.url
        if not ok:
            await _dump(page, "uncertain")
            error = "已点发布但未确认成功(请到 YouTube Studio 确认;调试截图见 data/debug/)"
        else:
            _log(f"publish ok url={result_url}")
    except Exception as e:
        error = f"发布异常: {e!r}"
        try:
            await _dump(page, "exception")
        except Exception:
            pass
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
    return ok, result_url, error
