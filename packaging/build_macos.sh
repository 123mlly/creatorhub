#!/usr/bin/env bash
# 在 macOS 上构建 CreatorHub.app 与 CreatorHub.dmg
# 用法: ./packaging/build_macos.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DIST="$ROOT/dist"
BUILD="$ROOT/build"
BUNDLE_BROWSERS="$ROOT/packaging/ms-playwright"
DMG_NAME="CreatorHub"
APP_NAME="CreatorHub.app"

log() { printf '[build] %s\n' "$*"; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  log "仅支持 macOS"
  exit 1
fi

log "同步依赖(+ pyinstaller)…"
uv sync --extra package

log "准备 Playwright Chromium → packaging/ms-playwright"
mkdir -p "$BUNDLE_BROWSERS"

# 优先复用本机缓存，避免重复下载
CACHE_CANDIDATES=(
  "${PLAYWRIGHT_BROWSERS_PATH:-}"
  "$HOME/Library/Caches/ms-playwright"
  "$HOME/.cache/ms-playwright"
)

copy_browser_tree() {
  local src="$1"
  [[ -d "$src" ]] || return 1
  local found=0
  shopt -s nullglob
  for d in "$src"/chromium-* "$src"/ffmpeg-*; do
    [[ -d "$d" ]] || continue
    local name
    name="$(basename "$d")"
    # headless_shell 体积大且本项目主要用完整 Chromium；有则一并拷
    if [[ "$name" == chromium_headless_shell-* ]]; then
      continue
    fi
    if [[ ! -d "$BUNDLE_BROWSERS/$name" ]]; then
      log "复制 $name"
      cp -R "$d" "$BUNDLE_BROWSERS/$name"
    fi
    found=1
  done
  # 默认不拷 chromium_headless_shell（约 +170MB，本项目用完整 Chromium）
  shopt -u nullglob
  [[ "$found" -eq 1 ]]
}

copied=0
for c in "${CACHE_CANDIDATES[@]}"; do
  [[ -n "$c" && -d "$c" ]] || continue
  if copy_browser_tree "$c"; then
    copied=1
    break
  fi
done

if [[ "$copied" -eq 0 ]] || [[ ! -d "$(echo "$BUNDLE_BROWSERS"/chromium-* | awk '{print $1}')" ]]; then
  log "缓存中无 Chromium，执行 playwright install chromium…"
  export PLAYWRIGHT_BROWSERS_PATH="$BUNDLE_BROWSERS"
  uv run playwright install chromium
fi

if ! ls "$BUNDLE_BROWSERS"/chromium-* >/dev/null 2>&1; then
  log "错误: 未能准备 Chromium，请先 uv run playwright install chromium"
  exit 1
fi

log "Chromium 就绪:"
du -sh "$BUNDLE_BROWSERS"/* 2>/dev/null || true

log "PyInstaller 打包…"
rm -rf "$DIST/$APP_NAME" "$DIST/CreatorHub" "$BUILD"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$DIST" \
  --workpath "$BUILD" \
  "$ROOT/packaging/creatorhub.spec"

if [[ ! -d "$DIST/$APP_NAME" ]]; then
  log "错误: 未生成 $DIST/$APP_NAME"
  exit 1
fi

# 去掉隔离属性，便于本机试跑（未签名仍可能被 Gatekeeper 拦）
xattr -cr "$DIST/$APP_NAME" 2>/dev/null || true

log "制作 DMG…"
STAGE="$DIST/dmg-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$DIST/$APP_NAME" "$STAGE/"
ln -sf /Applications "$STAGE/Applications"

DMG_PATH="$DIST/${DMG_NAME}.dmg"
rm -f "$DMG_PATH"
hdiutil create \
  -volname "$DMG_NAME" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG_PATH"
rm -rf "$STAGE"

log "完成:"
log "  App: $DIST/$APP_NAME"
log "  DMG: $DMG_PATH"
du -sh "$DIST/$APP_NAME" "$DMG_PATH"
log "首次打开若提示无法验证开发者: 右键 → 打开，或在「隐私与安全性」里允许。"
log "用户数据目录: ~/Library/Application Support/CreatorHub/"
