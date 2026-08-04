#Requires -Version 5.1
<#
.SYNOPSIS
  在 Windows 上构建 CreatorHub 桌面版（PyInstaller onedir + zip）。

.DESCRIPTION
  必须在 Windows 本机或 Windows CI 上执行；不能在 macOS 上交叉编译。

  用法（PowerShell，仓库根目录）:
    .\packaging\build_windows.ps1

  产物:
    dist\CreatorHub\CreatorHub.exe
    dist\CreatorHub-windows.zip

  用户数据目录: %APPDATA%\CreatorHub\
  需要系统已安装 WebView2 Runtime（Win10/11 多数自带）。
#>
$ErrorActionPreference = "Stop"

function Write-Log([string]$Message) {
    Write-Host "[build] $Message"
}

if ($env:OS -ne "Windows_NT") {
    Write-Log "仅支持 Windows"
    exit 1
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
$BundleBrowsers = Join-Path $Root "packaging\ms-playwright"
$OutDir = Join-Path $Dist "CreatorHub"
$ZipPath = Join-Path $Dist "CreatorHub-windows.zip"

Write-Log "同步依赖(+ pyinstaller)…"
uv sync --extra package
if ($LASTEXITCODE -ne 0) { throw "uv sync 失败" }

Write-Log "准备 Playwright Chromium → packaging\ms-playwright"
New-Item -ItemType Directory -Force -Path $BundleBrowsers | Out-Null

function Copy-BrowserTree([string]$Src) {
    if (-not (Test-Path $Src)) { return $false }
    $found = $false
    $dirs = Get-ChildItem -Path $Src -Directory -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -like "chromium-*" -or $_.Name -like "ffmpeg-*") -and
            ($_.Name -notlike "chromium_headless_shell-*")
        }
    foreach ($d in $dirs) {
        $dest = Join-Path $BundleBrowsers $d.Name
        if (-not (Test-Path $dest)) {
            Write-Log "复制 $($d.Name)"
            Copy-Item -Recurse -Force $d.FullName $dest
        }
        $found = $true
    }
    return $found
}

$cacheCandidates = @(
    $env:PLAYWRIGHT_BROWSERS_PATH,
    (Join-Path $env:LOCALAPPDATA "ms-playwright"),
    (Join-Path $env:USERPROFILE "AppData\Local\ms-playwright")
) | Where-Object { $_ -and (Test-Path $_) }

$copied = $false
foreach ($c in $cacheCandidates) {
    if (Copy-BrowserTree $c) {
        $copied = $true
        break
    }
}

$hasChromium = @(Get-ChildItem -Path $BundleBrowsers -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue).Count -gt 0
if (-not $hasChromium) {
    Write-Log "缓存中无 Chromium，执行 playwright install chromium…"
    $env:PLAYWRIGHT_BROWSERS_PATH = $BundleBrowsers
    uv run playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "playwright install chromium 失败" }
}

$hasChromium = @(Get-ChildItem -Path $BundleBrowsers -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue).Count -gt 0
if (-not $hasChromium) {
    Write-Log "错误: 未能准备 Chromium，请先 uv run playwright install chromium"
    exit 1
}

Write-Log "Chromium 就绪:"
Get-ChildItem $BundleBrowsers -Directory | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    $mb = [math]::Round(($size / 1MB), 1)
    Write-Log ("  {0} (~{1} MB)" -f $_.Name, $mb)
}

Write-Log "PyInstaller 打包…"
if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }

uv run pyinstaller `
    --noconfirm `
    --clean `
    --distpath $Dist `
    --workpath $Build `
    (Join-Path $Root "packaging\creatorhub.spec")
if ($LASTEXITCODE -ne 0) { throw "pyinstaller 失败" }

$ExePath = Join-Path $OutDir "CreatorHub.exe"
if (-not (Test-Path $ExePath)) {
    Write-Log "错误: 未生成 $ExePath"
    exit 1
}

Write-Log "制作 zip…"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $OutDir -DestinationPath $ZipPath -Force

Write-Log "完成:"
Write-Log "  App: $ExePath"
Write-Log "  Zip: $ZipPath"
Write-Log "首次运行若被 SmartScreen 拦截: 更多信息 → 仍要运行（未代码签名时常见）。"
Write-Log "用户数据目录: %APPDATA%\CreatorHub\"
Write-Log "需要 WebView2 Runtime；若窗口打不开请安装: https://developer.microsoft.com/microsoft-edge/webview2/"
