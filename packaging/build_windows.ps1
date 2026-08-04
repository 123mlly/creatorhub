#Requires -Version 5.1
<#
.SYNOPSIS
  在 Windows 上构建 CreatorHub 桌面版（PyInstaller onedir + zip）。

.DESCRIPTION
  必须在 Windows 本机或 Windows CI 上执行；不能在 macOS 上交叉编译。

  用法（PowerShell，仓库根目录）:
    .\packaging\build_windows.ps1

  可选 Authenticode 签名（设置任一方式即可）:
    # PFX 文件
    $env:CODESIGN_PFX = "C:\path\to\cert.pfx"
    $env:CODESIGN_PASSWORD = "证书密码"   # 无密码可省略
    # 或证书存储 / EV 令牌（按主题名）
    $env:CODESIGN_SUBJECT = "Your Company Name"
    # 可选时间戳服务（默认 DigiCert）
    $env:CODESIGN_TIMESTAMP_URL = "http://timestamp.digicert.com"

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

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin"
    ) | Where-Object { $_ -and (Test-Path $_) }

    foreach ($root in $roots) {
        $found = Get-ChildItem -Path $root -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

function Invoke-CodeSign([string]$TargetExe) {
    $pfx = $env:CODESIGN_PFX
    $subject = $env:CODESIGN_SUBJECT
    $password = $env:CODESIGN_PASSWORD
    $timestamp = $env:CODESIGN_TIMESTAMP_URL
    if (-not $timestamp) { $timestamp = "http://timestamp.digicert.com" }

    if (-not $pfx -and -not $subject) {
        Write-Log "未设置 CODESIGN_PFX / CODESIGN_SUBJECT，跳过代码签名。"
        return $false
    }

    $signtool = Find-SignTool
    if (-not $signtool) {
        throw "需要签名但未找到 signtool.exe。请安装 Windows SDK（含 Signing Tools）。"
    }
    Write-Log "使用 signtool: $signtool"

    $args = @(
        "sign",
        "/fd", "sha256",
        "/td", "sha256",
        "/tr", $timestamp
    )
    if ($pfx) {
        if (-not (Test-Path $pfx)) { throw "CODESIGN_PFX 不存在: $pfx" }
        Write-Log "Authenticode 签名（PFX）…"
        $args += @("/f", $pfx)
        if ($password) { $args += @("/p", $password) }
    } else {
        Write-Log "Authenticode 签名（证书主题: $subject）…"
        $args += @("/n", $subject)
    }
    $args += $TargetExe

    & $signtool @args
    if ($LASTEXITCODE -ne 0) { throw "signtool sign 失败 (exit $LASTEXITCODE)" }

    & $signtool verify /pa $TargetExe
    if ($LASTEXITCODE -ne 0) { throw "signtool verify 失败 (exit $LASTEXITCODE)" }

    Write-Log "代码签名完成。"
    return $true
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

$signed = Invoke-CodeSign $ExePath

Write-Log "制作 zip…"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $OutDir -DestinationPath $ZipPath -Force

Write-Log "完成:"
Write-Log "  App: $ExePath"
Write-Log "  Zip: $ZipPath"
if ($signed) {
    Write-Log "已 Authenticode 签名。"
} else {
    Write-Log "首次运行若被 SmartScreen 拦截: 更多信息 → 仍要运行（未代码签名时常见）。"
}
Write-Log "用户数据目录: %APPDATA%\CreatorHub\"
Write-Log "需要 WebView2 Runtime；若窗口打不开请安装: https://developer.microsoft.com/microsoft-edge/webview2/"
