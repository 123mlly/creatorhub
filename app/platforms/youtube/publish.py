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
    '#description-textarea #textbox',
    'ytcp-social-suggestions-textbox#description-textarea #textbox',
    '#textbox[aria-label*="Tell viewers about your video"]',
    '#textbox[aria-label*="向观众介绍"]',
    '#textbox[aria-label*="description"]',
    '#textbox[aria-label*="说明"]',
    '#textbox[aria-label*="描述"]',
]
_DESC_EXPAND = [
    '#description-textarea',
    'ytcp-social-suggestions-textbox#description-textarea',
    '#description-container',
    'text=Add a description',
    'text=Tell viewers about your video',
    'text=添加说明',
    'text=添加描述',
    'text=向观众介绍你的视频',
]
_NEXT_BTN = [
    '#next-button', 'ytcp-button#next-button',
    'button:has-text("Next")', 'button:has-text("下一步")',
]
# 只点「发布」类按钮;刻意不含 Save/保存 —— 未选 Public 时 Done 常是 Save→Draft
_PUBLISH_BTN = [
    'ytcp-button#done-button:has-text("Publish")',
    'ytcp-button#done-button:has-text("发布")',
    '#done-button:has-text("Publish")',
    '#done-button:has-text("发布")',
    'button:has-text("Publish")',
    'button:has-text("发布")',
    'ytcp-button#done-button',
    '#done-button',
]
_PUBLIC_RADIO = [
    '#privacy-radios tp-yt-paper-radio-button[name="PUBLIC"]',
    'tp-yt-paper-radio-button[name="PUBLIC"]',
    '#privacy-radios [name="PUBLIC"]',
    'ytcp-video-visibility-select tp-yt-paper-radio-button[name="PUBLIC"]',
    'tp-yt-paper-radio-button:has-text("Public")',
    'tp-yt-paper-radio-button:has-text("公开")',
]
_VISIBILITY_STEP = [
    '#privacy-radios',
    'ytcp-video-visibility-select',
    '#visibility-container',
    'text=Visibility',
    'text=可见性',
    'text=Who can see this video',
    'text=谁可以观看这部影片',
    'text=谁可以观看此视频',
]
# 「是否专为儿童打造」必填;默认选「否」(非儿童向)
_NOT_FOR_KIDS = [
    'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
    '#audience [name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
    'tp-yt-paper-radio-button:has-text("No, it\'s not made for kids")',
    'tp-yt-paper-radio-button:has-text("No, it’s not made for kids")',
    'tp-yt-paper-radio-button:has-text("不是给孩子看的")',
    'tp-yt-paper-radio-button:has-text("未面向儿童")',
    'tp-yt-paper-radio-button:has-text("否，未面向儿童")',
    'ytcp-audience-picker tp-yt-paper-radio-button:nth-child(2)',
]
_FOR_KIDS = [
    'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_MFK"]',
    '#audience [name="VIDEO_MADE_FOR_KIDS_MFK"]',
    'tp-yt-paper-radio-button:has-text("Yes, it\'s made for kids")',
    'tp-yt-paper-radio-button:has-text("是，专为儿童打造")',
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


async def _fill_editable(page, selectors, text, timeout=4000, max_len: int = 5000) -> bool:
    """写入 contenteditable;先清空再填,避免 Studio 用文件名预填导致超长。"""
    text = (text or "")[:max_len]
    if not text:
        return True
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() == 0:
                continue
            await el.click(timeout=timeout)
            await page.wait_for_timeout(150)
            # 彻底清空(Meta/Ctrl+A 在部分 Studio 组件上不可靠)
            try:
                await el.evaluate(
                    """(node) => {
                      node.focus();
                      if (node.isContentEditable) {
                        node.textContent = '';
                        node.innerText = '';
                        node.dispatchEvent(new InputEvent('input', {bubbles: true}));
                      } else if ('value' in node) {
                        node.value = '';
                        node.dispatchEvent(new Event('input', {bubbles: true}));
                      }
                    }"""
                )
            except Exception:
                await page.keyboard.press("Meta+a")
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
            await page.wait_for_timeout(100)
            await page.keyboard.type(text, delay=6)
            # 再保险:读回长度,超了就用 JS 截断
            try:
                cur = await el.evaluate(
                    "(n) => (n.isContentEditable ? (n.innerText || n.textContent || '') : (n.value || ''))"
                )
                if isinstance(cur, str) and len(cur) > max_len:
                    await el.evaluate(
                        """(node, t) => {
                          if (node.isContentEditable) {
                            node.textContent = t;
                            node.dispatchEvent(new InputEvent('input', {bubbles: true}));
                          } else if ('value' in node) {
                            node.value = t;
                            node.dispatchEvent(new Event('input', {bubbles: true}));
                          }
                        }""",
                        text,
                    )
            except Exception:
                pass
            return True
        except Exception:
            continue
    return False


def _clip_title(title: str, media_path: str = "", limit: int = 100) -> str:
    """YouTube 标题上限 100;去掉常见「数字ID_」前缀。"""
    t = (title or "").strip()
    if not t and media_path:
        t = Path(media_path).stem
    # 7670..._描述 → 描述
    if re.match(r"^\d{10,}_", t):
        t = t.split("_", 1)[-1].strip() or t
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[:limit].rstrip()
    return t or "Untitled"


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


async def _fill_description(page, text: str) -> bool:
    """展开并填写描述;Studio 描述框常折叠,直接 type 会写不进去。"""
    text = (text or "").strip()
    if not text:
        return False
    # 先点展开区域
    for sel in _DESC_EXPAND:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.scroll_into_view_if_needed(timeout=2500)
            await loc.click(timeout=2500)
            await page.wait_for_timeout(350)
            break
        except Exception:
            continue
    ok = await _fill_editable(page, _DESC_SEL, text[:5000], max_len=5000)
    if ok:
        _log(f"description filled ({len(text)} chars)")
        return True
    # 兜底:找任意可见的第二个 contenteditable textbox(第一个通常是标题)
    try:
        boxes = page.locator('div#textbox[contenteditable="true"]')
        n = await boxes.count()
        for i in range(n):
            el = boxes.nth(i)
            try:
                if not await el.is_visible():
                    continue
                # 跳过标题框:aria-label 含 title/标题
                label = (await el.get_attribute("aria-label") or "").lower()
                if "title" in label or "标题" in label:
                    continue
                await el.scroll_into_view_if_needed(timeout=2000)
                await el.click(timeout=2000)
                await el.evaluate(
                    """(node, t) => {
                      node.focus();
                      node.textContent = t;
                      node.dispatchEvent(new InputEvent('input', {bubbles: true}));
                    }""",
                    text[:5000],
                )
                _log(f"description filled via fallback box#{i}")
                return True
            except Exception:
                continue
    except Exception as e:
        _log(f"description fallback failed: {e!r}")
    _log("description NOT filled")
    return False


async def _public_is_checked(page) -> bool:
    """严格确认 Public radio 已被 Studio 勾选(不只看我们是否点过)。"""
    try:
        pub = page.locator("tp-yt-paper-radio-button[name='PUBLIC']").first
        if await pub.count() == 0:
            return False
        checked = await pub.get_attribute("aria-checked")
        if checked == "true":
            return True
        # 部分版本用 selected / checked class
        cls = (await pub.get_attribute("class") or "") + " " + (
            await pub.get_attribute("aria-selected") or ""
        )
        if "iron-selected" in cls or "checked" in cls.lower():
            return True
    except Exception:
        pass
    try:
        return bool(await page.evaluate(
            """() => {
              const walk = (root, acc=[]) => {
                if (!root) return acc;
                root.querySelectorAll &&
                  root.querySelectorAll('tp-yt-paper-radio-button[name="PUBLIC"]').forEach(el => acc.push(el));
                root.querySelectorAll && root.querySelectorAll('*').forEach(el => {
                  if (el.shadowRoot) walk(el.shadowRoot, acc);
                });
                return acc;
              };
              const pubs = walk(document);
              return pubs.some(el =>
                el.getAttribute('aria-checked') === 'true' ||
                el.getAttribute('aria-selected') === 'true' ||
                (el.className || '').includes('iron-selected')
              );
            }"""
        ))
    except Exception:
        return False


async def _goto_visibility_step(page) -> bool:
    """进入 Visibility 步骤(步骤徽章或连点 Next)。"""
    for sel in ("#step-badge-3", "button#step-badge-3", "[id='step-badge-3']"):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.click(timeout=3000, force=True)
                await page.wait_for_timeout(1200)
                _log("jumped to visibility via step-badge-3")
                break
        except Exception:
            continue
    else:
        for _ in range(8):
            if await page.locator(
                "#privacy-radios, ytcp-video-visibility-select, "
                "tp-yt-paper-radio-button[name='PUBLIC']"
            ).count():
                break
            await _click_first(page, _NEXT_BTN, timeout=3000)
            await page.wait_for_timeout(900)

    for _ in range(20):
        try:
            if await page.locator("tp-yt-paper-radio-button[name='PUBLIC']").count():
                return True
            if await page.locator("#privacy-radios").count():
                return True
        except Exception:
            pass
        await page.wait_for_timeout(400)
    return False


async def _select_public_visibility(page) -> bool:
    """在 Visibility 步骤勾选 Public/公开,并以 aria-checked 严格校验。

    失败必须返回 False —— 调用方不得继续点 Done(否则会 Save 成 Draft)。
    """
    if not await _goto_visibility_step(page):
        await _dump(page, "no_visibility_step")
        _log("visibility step not reached")
        return False

    if await _public_is_checked(page):
        _log("visibility=PUBLIC already checked")
        return True

    # 1) Playwright 直接点 radio
    for sel in (
        "tp-yt-paper-radio-button[name='PUBLIC']",
        "#privacy-radios tp-yt-paper-radio-button[name='PUBLIC']",
        "#privacy-radios tp-yt-paper-radio-button:nth-child(3)",
        "ytcp-video-visibility-select tp-yt-paper-radio-button[name='PUBLIC']",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.scroll_into_view_if_needed(timeout=3000)
            await page.wait_for_timeout(200)
            await loc.click(timeout=4000, force=True)
            await page.wait_for_timeout(350)
            for sub in ("#radio", "#offRadio", "#radioLabel", "#radioContainer"):
                try:
                    inner = loc.locator(sub).first
                    if await inner.count():
                        await inner.click(timeout=2000, force=True)
                        break
                except Exception:
                    continue
            await page.wait_for_timeout(500)
            if await _public_is_checked(page):
                _log(f"visibility=PUBLIC via {sel}")
                return True
        except Exception as e:
            _log(f"click {sel} failed: {e!r}")

    # 2) 点文案(必须再校验,不再 unverified 放行)
    for text in (
        "Everyone can watch your video",
        "Public",
        "公开",
        "所有人都可以观看",
    ):
        try:
            loc = page.get_by_text(text, exact=False).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=3000, force=True)
                await page.wait_for_timeout(500)
                if await _public_is_checked(page):
                    _log(f"visibility=PUBLIC via text={text!r}")
                    return True
                _log(f"text={text!r} clicked but PUBLIC not checked")
        except Exception:
            continue

    # 3) JS 真实 click(不伪造 aria-checked;点完再读属性)
    try:
        ok = await page.evaluate(
            """() => {
              const findAll = (root, acc=[]) => {
                root.querySelectorAll &&
                  root.querySelectorAll('tp-yt-paper-radio-button').forEach(el => acc.push(el));
                root.querySelectorAll && root.querySelectorAll('*').forEach(el => {
                  if (el.shadowRoot) findAll(el.shadowRoot, acc);
                });
                return acc;
              };
              const radios = findAll(document);
              const pub = radios.find(r => (r.getAttribute('name') || '') === 'PUBLIC')
                || radios.find(r => /\\bpublic\\b|公开/i.test(r.textContent || ''));
              if (!pub) return false;
              pub.scrollIntoView({block:'center'});
              const inner = pub.shadowRoot
                ? (pub.shadowRoot.querySelector('#radio, #offRadio, #radioContainer') || pub)
                : (pub.querySelector('#radio, #offRadio, #radioContainer, #radioLabel') || pub);
              inner.click();
              pub.click();
              // Polymer paper-radio 常靠 tap 事件
              try {
                pub.dispatchEvent(new CustomEvent('tap', {bubbles: true, composed: true}));
                pub.dispatchEvent(new MouseEvent('click', {bubbles: true, composed: true}));
              } catch (e) {}
              return true;
            }"""
        )
        if ok:
            await page.wait_for_timeout(600)
            if await _public_is_checked(page):
                _log("visibility=PUBLIC via JS evaluate")
                return True
            _log("JS clicked PUBLIC but aria-checked still false")
    except Exception as e:
        _log(f"JS public select failed: {e!r}")

    # 再试一次跳步骤 + 点击(偶发对话框未完全渲染)
    await _goto_visibility_step(page)
    await page.wait_for_timeout(800)
    try:
        loc = page.locator("tp-yt-paper-radio-button[name='PUBLIC']").first
        if await loc.count():
            await loc.click(timeout=4000, force=True)
            await page.wait_for_timeout(600)
            if await _public_is_checked(page):
                _log("visibility=PUBLIC via retry click")
                return True
    except Exception:
        pass

    await _dump(page, "no_public")
    _log("visibility PUBLIC not selected (strict)")
    return False


async def _click_publish(page) -> bool:
    """点 Publish/发布。若按钮仍是 Save/保存则拒绝点击,避免存成 Draft。"""
    # 等 Done 按钮文案从 Save 变成 Publish(勾 Public 后会变)
    for _ in range(12):
        try:
            done = page.locator("#done-button, ytcp-button#done-button").first
            if await done.count():
                label = (await done.inner_text(timeout=1500) or "").strip().lower()
                _log(f"done-button label={label!r}")
                if any(k in label for k in ("publish", "发布")):
                    break
                if any(k in label for k in ("save", "保存", "schedule", "定时")):
                    # 可见性可能还没生效,稍等再读
                    await page.wait_for_timeout(500)
                    continue
        except Exception:
            pass
        await page.wait_for_timeout(400)

    # 优先精确 Publish 文案
    for sel in _PUBLISH_BTN:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            label = ""
            try:
                label = (await loc.inner_text(timeout=1200) or "").strip()
            except Exception:
                pass
            low = label.lower()
            # 明确是保存/定时 → 跳过
            if low and any(k in low for k in ("save", "保存", "schedule", "定时")) \
                    and not any(k in low for k in ("publish", "发布")):
                _log(f"skip done btn label={label!r} (would draft)")
                continue
            await loc.scroll_into_view_if_needed(timeout=2000)
            await loc.click(timeout=5000, force=True)
            _log(f"clicked publish via {sel} label={label!r}")
            return True
        except Exception as e:
            _log(f"publish click {sel} failed: {e!r}")

    # 最后兜底:get_by_role / 文案
    for text in ("Publish", "发布"):
        try:
            loc = page.get_by_role("button", name=re.compile(text, re.I)).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=4000, force=True)
                _log(f"clicked publish via role name={text!r}")
                return True
        except Exception:
            continue
        try:
            loc = page.get_by_text(text, exact=True).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=4000, force=True)
                _log(f"clicked publish via text={text!r}")
                return True
        except Exception:
            continue
    return False


# 二次确认 / 发布成功文案
# 注意:草稿阶段也可能出现 youtu.be,不能「只靠链接」判成功;
# 但「已勾 Public + 已点 Publish」之后,向导关闭 / 分享框 / 处理中 都可视为成功。
_PUBLISH_CONFIRM_BTN = [
    'ytcp-button#publish-button',
    '#publish-button',
    'tp-yt-paper-dialog #publish-button',
    'tp-yt-paper-dialog ytcp-button:has-text("Publish")',
    'tp-yt-paper-dialog ytcp-button:has-text("发布")',
    'ytcp-confirmation-dialog ytcp-button:has-text("Publish")',
    'ytcp-confirmation-dialog ytcp-button:has-text("发布")',
    'ytcp-dialog ytcp-button:has-text("Publish")',
    'ytcp-dialog ytcp-button:has-text("发布")',
]
_PUBLISH_SUCCESS_TEXT = (
    "Video published",
    "Short published",
    "Your video has been published",
    "Your video went live",
    "Your video is now live",
    "Video is published",
    "published successfully",
    "视频已发布",
    "短视频已发布",
    "已成功发布",
    "已发布到 YouTube",
    "已发布到 Youtube",
    "已发布到YouTube",
    "发布成功",
)
# 中间态:已提交但仍在转码 —— 还不算最终成功,需等到 Video published
_PROCESSING_TEXT = (
    "Video processing",
    "视频处理中",
    "Processing up to",
    "needs to finish processing",
    "before your video is public",
    "Your video is being processed",
    "正在处理你的视频",
    "正在处理您的视频",
    "视频正在处理",
)
_DRAFT_SAVED_TEXT = (
    "saved as a draft",
    "Saved as draft",
    "已存为草稿",
    "保存为草稿",
    "已保存为草稿",
)
# 最终成功弹框:「Video published」+ Share a link / Video link
_VIDEO_PUBLISHED_DIALOG = (
    "ytcp-video-share-dialog",
    "tp-yt-paper-dialog:has-text('Video published')",
    "tp-yt-paper-dialog:has-text('Short published')",
    "tp-yt-paper-dialog:has-text('视频已发布')",
    "tp-yt-paper-dialog:has-text('短视频已发布')",
    "ytcp-dialog:has-text('Video published')",
    "ytcp-dialog:has-text('Short published')",
    "ytcp-dialog:has-text('视频已发布')",
    "#dialog-title:has-text('Video published')",
    "#dialog-title:has-text('Short published')",
)

async def _confirm_publish_dialog(page) -> bool:
    """处理点 Publish 后的二次确认框(Publish video?)。未点确认则仍是草稿。"""
    clicked = False
    for attempt in range(10):
        # 已出现成功文案则无需再点
        if await _has_publish_success_banner(page):
            return True
        for sel in _PUBLISH_CONFIRM_BTN:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible():
                    continue
                label = ""
                try:
                    label = (await loc.inner_text(timeout=800) or "").strip()
                except Exception:
                    pass
                low = label.lower()
                if low and any(k in low for k in ("save", "保存", "cancel", "取消", "close", "关闭")):
                    continue
                await loc.click(timeout=3000, force=True)
                _log(f"clicked publish confirm via {sel} label={label!r}")
                clicked = True
                await page.wait_for_timeout(800)
                break
            except Exception:
                continue
        if clicked:
            # 有时确认框要再点一次
            await page.wait_for_timeout(600)
            if await _has_publish_success_banner(page):
                return True
            # 再扫一轮确认按钮(第二层)
            for sel in _PUBLISH_CONFIRM_BTN:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(timeout=2500, force=True)
                        _log(f"clicked publish confirm again via {sel}")
                        return True
                except Exception:
                    continue
            return True
        # 无独立确认框时,也可能仍停在 done-button=Publish(点了没生效)
        try:
            done = page.locator("#done-button, ytcp-button#done-button").first
            if await done.count() and await done.is_visible():
                label = (await done.inner_text(timeout=800) or "").strip().lower()
                if any(k in label for k in ("publish", "发布")):
                    await done.click(timeout=3000, force=True)
                    _log("re-clicked done-button Publish (no separate confirm dialog)")
                    clicked = True
                    await page.wait_for_timeout(800)
                    continue
        except Exception:
            pass
        await page.wait_for_timeout(500)
    return clicked


async def _has_draft_saved_notice(page) -> bool:
    for kw in _DRAFT_SAVED_TEXT:
        try:
            loc = page.get_by_text(kw, exact=False).first
            if await loc.count() and await loc.is_visible():
                return True
        except Exception:
            continue
    return False


async def _has_video_published_dialog(page) -> bool:
    """最终成功:「Video published」分享弹框(含 Share a link / Video link)。"""
    for sel in _VIDEO_PUBLISHED_DIALOG:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0 or not await loc.is_visible():
                continue
            txt = ""
            try:
                txt = (await loc.inner_text(timeout=1200) or "").lower()
            except Exception:
                txt = ""
            # share dialog 组件本身即成功;其它 dialog 需含 published 文案
            if "ytcp-video-share-dialog" in sel:
                _log(f"published dialog hit: {sel}")
                return True
            if any(k in txt for k in (
                "video published", "short published", "视频已发布", "短视频已发布",
                "share a link", "video link", "分享链接",
            )):
                _log(f"published dialog hit: {sel}")
                return True
        except Exception:
            continue
    # 泛匹配可见 dialog
    try:
        dialogs = page.locator(
            "tp-yt-paper-dialog:visible, ytcp-dialog:visible, "
            "ytcp-video-share-dialog:visible"
        )
        n = await dialogs.count()
        for i in range(min(n, 6)):
            try:
                txt = (await dialogs.nth(i).inner_text(timeout=1000) or "").lower()
            except Exception:
                continue
            # 必须是 published,不能是 processing
            if "processing" in txt and "published" not in txt and "已发布" not in txt:
                continue
            if any(k in txt for k in (
                "video published", "short published", "视频已发布", "短视频已发布",
            )):
                _log("published dialog hit via dialog text")
                return True
            # Share a link + Video link 组合(标题可能本地化差异)
            if ("share a link" in txt or "分享" in txt) and (
                "video link" in txt or "youtu.be/" in txt or "/shorts/" in txt
                or "youtube.com/" in txt
            ):
                _log("published dialog hit via share+link")
                return True
    except Exception:
        pass
    return False


async def _has_processing_dialog(page) -> bool:
    """中间态 Video processing(已提交,转码中)。"""
    for kw in _PROCESSING_TEXT:
        try:
            loc = page.get_by_text(kw, exact=False).first
            if await loc.count() and await loc.is_visible():
                return True
        except Exception:
            continue
    try:
        for sel in (
            "tp-yt-paper-dialog:has-text('Video processing')",
            "ytcp-dialog:has-text('Video processing')",
            "ytcp-uploads-still-processing-dialog",
        ):
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                return True
    except Exception:
        pass
    return False


async def _has_publish_success_banner(page) -> bool:
    """最终成功文案或 Video published 弹框。"""
    if await _has_video_published_dialog(page):
        return True
    for kw in _PUBLISH_SUCCESS_TEXT:
        try:
            loc = page.get_by_text(kw, exact=False).first
            if await loc.count() and await loc.is_visible():
                # 避免「before your video is public」这类 processing 文案误命中
                _log(f"success text hit: {kw!r}")
                return True
        except Exception:
            continue
    return False


async def _detect_post_publish_success(page) -> Tuple[bool, str]:
    """在「已勾 Public + 已点 Publish」之后判定是否成功。

    仅「Video published」分享弹框(或等价成功文案)算成功。
    Video processing 只是中间态,继续等待。
    """
    if await _has_draft_saved_notice(page):
        return False, "draft_notice"
    if await _has_video_published_dialog(page):
        return True, "video_published_dialog"
    if await _has_publish_success_banner(page):
        return True, "published_banner"
    if await _has_processing_dialog(page):
        return False, "processing"  # 继续等,不算成功也不算失败
    return False, "pending"


async def _extract_result_url(page) -> str:
    """从页面抓分享链接;草稿阶段也可能有。"""
    try:
        for sel in (
            "a[href*='youtu.be/']",
            "a[href*='youtube.com/watch']",
            "a[href*='youtube.com/shorts/']",
            "a[href*='studio.youtube.com/video/']",
            "input[value*='youtu.be/']",
            "input[value*='youtube.com/']",
        ):
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            href = ""
            try:
                href = await loc.get_attribute("href") or ""
            except Exception:
                href = ""
            if not href:
                try:
                    href = await loc.get_attribute("value") or ""
                except Exception:
                    href = ""
            if not href:
                continue
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://www.youtube.com" + href
            m = re.search(r"(?:youtu\.be/|watch\?v=|shorts/|video/)([A-Za-z0-9_-]{11})", href)
            if m and "youtu" not in href[:20].lower():
                href = f"https://www.youtube.com/watch?v={m.group(1)}"
            elif m and "/video/" in href:
                href = f"https://www.youtube.com/watch?v={m.group(1)}"
            return href
    except Exception:
        pass
    return ""


async def _answer_made_for_kids(page, made_for_kids: bool = False) -> bool:
    """勾选 Audience「是否专为儿童打造」;未答则无法点 Next。"""
    sels = _FOR_KIDS if made_for_kids else _NOT_FOR_KIDS
    if await _click_first(page, sels, timeout=4000):
        _log(f"audience set made_for_kids={made_for_kids}")
        return True
    # 有时要先展开 Audience 区块
    for sel in (
        '#audience', 'ytcp-video-metadata-audience',
        'text=Audience', 'text=受众',
        'text=Is this video made for kids',
        'text=是否专为儿童打造',
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.scroll_into_view_if_needed(timeout=2000)
                await page.wait_for_timeout(300)
                break
        except Exception:
            continue
    if await _click_first(page, sels, timeout=4000):
        _log(f"audience set(after scroll) made_for_kids={made_for_kids}")
        return True
    _log("audience radio not found")
    return False


async def publish_youtube(mgr: BrowserManager, identity: Identity,
                          storage_state_json: str, media_type: str, title: str,
                          desc: str, media_paths: List[str], topics: str = "",
                          headed: bool = True, timeout_seconds: int = 360,
                          made_for_kids: bool = False
                          ) -> Tuple[bool, str, str]:
    """发布一条 YouTube 视频。返回 (ok, result_url, error)。
    仅支持 video;图集请先合成视频。storage_state_json 仅校验用(登录态在持久 profile)。
    made_for_kids: Studio 必填「是否专为儿童打造」,默认否。"""
    if media_type != "video":
        return False, "", "YouTube 目前仅支持上传视频(请选择「视频」类型)"
    files = [str(Path(p)) for p in media_paths if p and Path(p).exists()]
    if not files:
        return False, "", "没有可用的本地媒体文件(路径不存在)"
    tags = [t.strip().lstrip("#") for t in (topics or "").split(",") if t.strip()]
    body = (desc or "").strip()
    title = _clip_title(title, files[0] if files else "", limit=100)
    # 描述为空时回退到标题,避免 Studio 描述栏空白
    if not body:
        body = title
        _log("desc empty, fallback to title")
    if tags:
        body = (body + "\n\n" + " ".join(f"#{t}" for t in tags)).strip()
    _log(f"title({len(title)}): {title[:60]}{'…' if len(title) > 60 else ''}")
    _log(f"desc({len(body)}): {body[:60]}{'…' if len(body) > 60 else ''}")

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

        await _fill_editable(page, _TITLE_SEL, title, max_len=100)
        await page.wait_for_timeout(500)
        if not await _fill_description(page, body):
            await _dump(page, "no_description")
            _log("warn: description field empty after fill attempts")
        await page.wait_for_timeout(500)

        # Studio 必填:是否专为儿童打造(未勾选无法 Next)
        if not await _answer_made_for_kids(page, made_for_kids=made_for_kids):
            await _dump(page, "no_audience")
            # 不直接失败:留给用户在窗口里手点;仍继续尝试 Next
            _log("未能自动勾选 Audience,将尝试继续(请留意弹窗)")

        # 连点 Next 直到可见性步骤(Details → Video elements → Checks → Visibility)
        for step in range(5):
            # 若仍停在 Details 且 Audience 未选,再试一次
            await _answer_made_for_kids(page, made_for_kids=made_for_kids)
            if not await _click_first(page, _NEXT_BTN, timeout=5000):
                break
            await page.wait_for_timeout(1200)
            # 若 Next 后仍看到 audience 报错提示,再勾一次
            try:
                if await page.get_by_text("You need to answer this question", exact=False).first.is_visible():
                    await _answer_made_for_kids(page, made_for_kids=made_for_kids)
                    await _click_first(page, _NEXT_BTN, timeout=5000)
                    await page.wait_for_timeout(1200)
            except Exception:
                pass

        # 选公开:必须严格勾上,否则绝不能点 Done(否则常变成 Save→Draft)
        if not await _select_public_visibility(page):
            await _dump(page, "no_public")
            return False, "", (
                "未能勾选 Public/公开(请到弹出窗口手动选「公开」再点「发布」;"
                "调试截图见 data/debug/)。未自动点保存,避免变成草稿。"
            )
        # 再确认一次,防止点完又被 Studio 重置
        await page.wait_for_timeout(400)
        if not await _public_is_checked(page):
            await _dump(page, "public_lost")
            return False, "", (
                "Public 勾选未保持(请到弹出窗口确认可见性为「公开」后手动发布)"
            )

        if not await _click_publish(page):
            await _dump(page, "no_publish")
            return False, "", (
                "未找到「发布」按钮(当前可能仍是「保存」草稿。"
                "请确认已选 Public 后在弹出窗口手动点 Publish)"
            )

        # 二次确认(Publish video?)——漏点则可能仍是 Draft
        await page.wait_for_timeout(800)
        await _confirm_publish_dialog(page)

        # 转码可能较久;看到 Video processing 后继续等到 Video published
        deadline = max(timeout_seconds, 180)
        waited = 0
        success_reason = ""
        saw_processing = False
        while waited < deadline:
            passed, reason = await _detect_post_publish_success(page)
            if reason == "draft_notice":
                await _dump(page, "saved_draft")
                return False, "", "Studio 将视频存为草稿(发布未真正提交),请在窗口里重选公开并发布"
            if reason == "processing":
                if not saw_processing:
                    _log("Video processing dialog — waiting for Video published…")
                    saw_processing = True
            if passed:
                ok = True
                success_reason = reason
                result_url = await _extract_result_url(page) or result_url
                _log(f"publish confirmed via {reason}")
                break
            # 确认框还在就继续点
            if waited in (2, 6, 12, 20):
                await _confirm_publish_dialog(page)
            if not result_url:
                result_url = await _extract_result_url(page)
            await page.wait_for_timeout(2000)
            waited += 2

        if ok and not result_url:
            result_url = await _extract_result_url(page) or page.url
        if not ok:
            # 仅当最终出现 Video published 才算成功;processing / 仅有链接都不够
            if await _has_video_published_dialog(page):
                ok = True
                success_reason = "video_published_late"
                result_url = await _extract_result_url(page) or result_url
                _log(f"publish ok via {success_reason}")
            else:
                await _dump(page, "uncertain")
                if saw_processing:
                    error = (
                        "已出现 Video processing,但超时未等到「Video published」分享弹框"
                        f"{('；链接 ' + result_url) if result_url else ''}。"
                        "请到 Studio 确认是否已公开;调试截图见 data/debug/"
                    )
                else:
                    error = (
                        "已点 Publish 但未看到「Video published」确认弹框"
                        f"{('；链接 ' + result_url) if result_url else ''}。"
                        "请到 Studio 确认;调试截图见 data/debug/"
                    )
        if ok:
            # 关掉成功弹框(忽略失败)
            try:
                for sel in (
                    "tp-yt-paper-dialog tp-yt-paper-button:has-text('Close')",
                    "ytcp-dialog tp-yt-paper-button:has-text('Close')",
                    "ytcp-video-share-dialog button:has-text('Close')",
                    "button:has-text('Close')",
                    "button:has-text('关闭')",
                ):
                    loc = page.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(timeout=2000, force=True)
                        break
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            _log(f"publish ok ({success_reason or 'ok'}) url={result_url}")
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
