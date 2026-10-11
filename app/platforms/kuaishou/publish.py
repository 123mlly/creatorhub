"""快手发布入口(浏览器自动化快手创作者服务平台 cp.kuaishou.com)。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

VIDEO_URL = "https://cp.kuaishou.com/article/publish/video"
IMAGE_URL = "https://cp.kuaishou.com/article/publish/atlas"
_DESC_SEL = ['div[contenteditable="true"]', '#work-description-edit',
             'textarea[placeholder*="描述"]', '.editor-content', 'textarea']
_CONFIRM_LABELS = ("确定", "确认", "确认发布", "继续发布")
_SUCCESS_TOKS = ("发布成功", "作品已发布", "提交成功", "发布完成")
_UPLOAD_WARN = "请在视频上传完成后再点击发布"

# 上传未完成时底部「发布」也能点,点了会弹出操作提示。先认进度,再点。
_UPLOAD_STATUS_JS = """() => {
  const text = (document.body.innerText || '').replace(/\\s+/g, '');
  if (text.includes('请在视频上传完成后再点击发布')) return 'warn';
  const hits = [];
  const nodes = document.querySelectorAll('div, span, p');
  for (const el of nodes) {
    if (el.children && el.children.length > 3) continue;
    const t = ((el.innerText || '').trim()).replace(/\\s+/g, '');
    if (!t || t.length > 20) continue;
    const cls = (el.className || '').toString();
    const progress = /progress|upload|percent/i.test(cls);
    if (/上传中|正在上传|视频上传中|转码中|上传成功|上传完成/.test(t)
        || (progress && /^\\d{1,3}%$/.test(t)))
      hits.push(t);
  }
  return hits.slice(0, 6).join('|');
}"""

_DISMISS_UPLOAD_WARN_JS = """() => {
  const nodes = document.querySelectorAll('div, p, span');
  let tip = null;
  for (const el of nodes) {
    const t = (el.innerText || '').replace(/\\s+/g, '');
    if (t.includes('请在视频上传完成后再点击发布') && t.length < 80) { tip = el; break; }
  }
  if (!tip) return false;
  let box = tip;
  for (let i = 0; i < 8 && box; i++) {
    const btns = box.querySelectorAll('button, [role="button"], div, span');
    for (const el of btns) {
      const t = ((el.innerText || '').trim()).replace(/\\s+/g, '');
      if (t !== '确认' && t !== '确定') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 18) continue;
      el.click();
      return true;
    }
    box = box.parentElement;
  }
  return false;
}"""

# 底部粉色「发布」class 带哈希且常在视口外;不要用 has-text,会点到右上角菜单。
_FIND_PUBLISH_JS = """() => {
  const labels = new Set(['发布', '立即发布', '确认发布']);
  const hits = [];
  const nodes = document.querySelectorAll('button, [role="button"], a, div, span');
  for (const el of nodes) {
    const t = ((el.innerText || el.textContent || '').trim()).replace(/\\s+/g, '');
    if (!labels.has(t)) continue;
    if (el.children && el.children.length > 4) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 18) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0)
      continue;
    const cls = (el.className || '').toString();
    const disabled = !!(el.disabled
      || el.getAttribute('aria-disabled') === 'true'
      || /(?:^|[\\s_-])disabled(?:[\\s_-]|$)/i.test(cls));
    const bg = st.backgroundColor || '';
    const pink = /rgb\\(\\s*(2[0-4]\\d|255)\\s*,/.test(bg);
    hits.push({
      t, tag: el.tagName, cls: cls.slice(0, 90), disabled, pink,
      y: Math.round(r.y), x: Math.round(r.x),
      w: Math.round(r.width), h: Math.round(r.height),
      primary: /button-primary|ant-btn-primary|publish/i.test(cls) || pink,
    });
  }
  hits.sort((a, b) => (Number(b.primary) - Number(a.primary)) || (b.y - a.y));
  return hits.slice(0, 24);
}"""

_CLICK_PUBLISH_JS = """() => {
  const labels = new Set(['发布', '立即发布', '确认发布']);
  let best = null;
  let bestScore = -1e9;
  const nodes = document.querySelectorAll('button, [role="button"], a, div, span');
  for (const el of nodes) {
    const t = ((el.innerText || el.textContent || '').trim()).replace(/\\s+/g, '');
    if (!labels.has(t)) continue;
    if (el.children && el.children.length > 4) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 18) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    const cls = (el.className || '').toString();
    const disabled = !!(el.disabled
      || el.getAttribute('aria-disabled') === 'true'
      || /(?:^|[\\s_-])disabled(?:[\\s_-]|$)/i.test(cls));
    if (disabled) continue;
    const pink = /rgb\\(\\s*(2[0-4]\\d|255)\\s*,/.test(st.backgroundColor || '');
    const primary = /button-primary|ant-btn-primary/i.test(cls) || pink;
    const score = (primary ? 10000 : 0) + r.y + (r.width > 70 ? 20 : 0);
    if (score >= bestScore) { bestScore = score; best = el; }
  }
  if (!best) return '';
  best.scrollIntoView({ block: 'center', inline: 'nearest' });
  const ev = { bubbles: true, cancelable: true, view: window };
  best.dispatchEvent(new PointerEvent('pointerdown', ev));
  best.dispatchEvent(new MouseEvent('mousedown', ev));
  best.dispatchEvent(new PointerEvent('pointerup', ev));
  best.dispatchEvent(new MouseEvent('mouseup', ev));
  best.dispatchEvent(new MouseEvent('click', ev));
  try { best.click(); } catch (e) {}
  return ((best.innerText || '').trim() + '|' + (best.className || '').toString()).slice(0, 80);
}"""


def _log(msg: str) -> None:
    print(f"[ks-publish] {msg}", flush=True)


async def _fill_first(page, selectors, text, timeout=2500) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.click(timeout=timeout)
            await el.fill(text, timeout=timeout)
            return True
        except Exception:
            try:
                await page.keyboard.type(text)
                return True
            except Exception:
                continue
    return False


async def _dismiss_guides(page) -> None:
    for sel in ('[aria-label="Skip"]', '[data-action="skip"]',
                'button:has-text("跳过")', 'button:has-text("知道了")',
                'button:has-text("我知道了")'):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.click(timeout=800)
                _log(f"已关掉引导 {sel}")
        except Exception:
            continue


async def _js_click_publish(page) -> str:
    try:
        return str(await page.evaluate(_CLICK_PUBLISH_JS) or "")
    except Exception as e:
        _log(f"JS 点击失败: {e!r}")
        return ""


async def _playwright_click_publish(page) -> bool:
    """截图里是底部粉色「发布」,旁边是「取消」。优先点这个精确文案。"""
    for loc in (
        page.get_by_role("button", name="发布", exact=True),
        page.get_by_text("发布", exact=True),
    ):
        try:
            n = await loc.count()
        except Exception:
            continue
        for i in range(n - 1, -1, -1):
            b = loc.nth(i)
            try:
                await b.scroll_into_view_if_needed(timeout=2000)
                await b.click(timeout=2500, force=True)
                _log("Playwright 已点「发布」")
                return True
            except Exception:
                continue
    return False


async def _upload_status(page) -> str:
    try:
        return str(await page.evaluate(_UPLOAD_STATUS_JS) or "")
    except Exception:
        return ""


def _upload_busy(status: str) -> bool:
    if not status or status == "warn":
        return False
    if "上传成功" in status or "上传完成" in status:
        return False
    return True


async def _dismiss_upload_warning(page) -> bool:
    try:
        tip = page.get_by_text(_UPLOAD_WARN, exact=False).first
        if not await tip.count() or not await tip.is_visible():
            return False
    except Exception:
        return False
    try:
        dismissed = bool(await page.evaluate(_DISMISS_UPLOAD_WARN_JS))
    except Exception:
        dismissed = False
    if not dismissed:
        try:
            await page.get_by_role("button", name="确认", exact=True).last.click(timeout=2000)
            dismissed = True
        except Exception:
            pass
    if dismissed:
        _log("上传未完成,已关掉操作提示")
    return dismissed


async def _click_confirm(page) -> bool:
    try:
        tip = page.get_by_text(_UPLOAD_WARN, exact=False).first
        if await tip.count() and await tip.is_visible():
            return False
    except Exception:
        pass
    try:
        loc = page.locator("button, [role='button']")
        n = await loc.count()
    except Exception:
        return False
    for i in range(min(n, 60)):
        b = loc.nth(i)
        try:
            if not await b.is_visible():
                continue
            t = ((await b.inner_text()) or "").strip().replace("\n", "")
            if t in _CONFIRM_LABELS:
                await b.click(timeout=2500)
                _log(f"已点确认「{t}」")
                return True
        except Exception:
            continue
    return False


async def _looks_success(page) -> bool:
    url = (page.url or "").lower()
    if any(tok in url for tok in ("/manage", "/content", "/article/list", "published")):
        if "publish/video" not in url and "publish/atlas" not in url:
            return True
    for tok in _SUCCESS_TOKS:
        try:
            el = page.get_by_text(tok, exact=False).first
            if await el.count() and await el.is_visible():
                return True
        except Exception:
            continue
    return False


async def publish_kuaishou(mgr: BrowserManager, identity: Identity,
                           storage_state_json: str, media_type: str, title: str,
                           desc: str, media_paths: List[str], topics: str = "",
                           headed: bool = True, timeout_seconds: int = 180
                           ) -> Tuple[bool, str, str]:
    files = [str(Path(p)) for p in media_paths if p and Path(p).exists()]
    if not files:
        return False, "", "没有可用的本地媒体文件(路径不存在)"
    tags = [t.strip().lstrip("#") for t in (topics or "").split(",") if t.strip()][:4]
    body = ((title + "\n" if title else "") + (desc or "")
            + ("\n" + " ".join(f"#{t}" for t in tags) if tags else "")).strip()[:1000]

    ctx = await mgr.open_headed(identity, steal_focus=False)
    page = await ctx.new_page()
    ok, result_url, error = False, "", ""
    try:
        url = VIDEO_URL if media_type == "video" else IMAGE_URL
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(2500)
        if "passport" in page.url or "/login" in page.url:
            return False, "", "logged_out:快手创作平台未登录"
        try:
            await page.locator('input[type="file"]').first.set_input_files(
                files if media_type == "images" else files[:1], timeout=15000)
        except Exception as e:
            return False, "", f"上传文件失败: {e!r}"
        _log("已选文件,等待表单/发布按钮")
        await page.wait_for_timeout(2500)
        await _dismiss_guides(page)
        if body:
            await _fill_first(page, _DESC_SEL, body)

        clicked = ""
        saw_upload_warn = False
        rounds = max(8, int(timeout_seconds / 2))
        for i in range(rounds):
            await _dismiss_guides(page)
            if await _dismiss_upload_warning(page):
                saw_upload_warn = True
                await page.wait_for_timeout(3000)
                continue
            status = await _upload_status(page)
            if _upload_busy(status):
                if i % 3 == 0:
                    _log(f"视频仍在上传,暂不点发布 ({status})")
                await page.wait_for_timeout(2000)
                continue
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            if i in (0, 3, 8):
                try:
                    _log(f"发布候选 {await page.evaluate(_FIND_PUBLISH_JS)}")
                except Exception as e:
                    _log(f"枚举失败 {e!r}")
            hit = ""
            if await _playwright_click_publish(page):
                hit = "pw"
            else:
                hit = await _js_click_publish(page)
                if hit:
                    _log(f"已自动点击发布 {hit}")
            if hit:
                await page.wait_for_timeout(800)
                if await _dismiss_upload_warning(page):
                    saw_upload_warn = True
                    _log("点发布时上传未完成,稍后重试")
                    await page.wait_for_timeout(3000)
                    continue
                clicked = hit
                break
            if i in (0, 4, 10) or i == rounds - 1:
                _log(f"发布按钮仍未点到 i={i}/{rounds}")
            await page.wait_for_timeout(2000)
        if not clicked:
            if saw_upload_warn:
                return False, "", "视频仍在上传,快手提示上传完成后再发布"
            return False, "", "未找到可点击的发布按钮(视频可能仍在转码,或发布页改版)"
        await page.wait_for_timeout(800)
        await _click_confirm(page)

        for _ in range(max(8, int(timeout_seconds / 3))):
            if await _looks_success(page):
                ok = True
                break
            await page.wait_for_timeout(1500)
        result_url = page.url if ok else ""
        if not ok:
            error = "已点发布但未确认成功(请到快手创作平台确认)"
    except Exception as e:
        error = f"发布异常: {e!r}"
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
    return ok, result_url, error
