param(
    [string]$OutputDir = "",
    [string]$OpenCliRoot = "<本机依赖目录>",
    [string]$NodeExe = "",
    [string]$FfprobeExe = "<本机路径>",
    [string]$CompilerRoot = "",
    [switch]$ReuseCompiled
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Split-Path -Parent $projectRoot
if (-not $OutputDir) {
    $OutputDir = Join-Path $projectRoot "dist-product"
}
if (-not $NodeExe) {
    $NodeExe = (Get-Command node.exe -ErrorAction Stop).Source
}
if (-not $CompilerRoot) {
    $CompilerRoot = if ($env:SYMPHONY_BUILD_ROOT) {
        $env:SYMPHONY_BUILD_ROOT
    } else {
        "<本机路径>"
    }
}

function Resolve-SafeChild([string]$Path, [string]$Parent) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $resolvedParent = [System.IO.Path]::GetFullPath($Parent)
    if (-not $resolved.StartsWith($resolvedParent + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path must be a dedicated directory inside ${resolvedParent}: $resolved"
    }
    return $resolved
}

function Write-Utf8File([string]$Path, [string]$Content) {
    Write-Host "Writing UTF-8 file: $Path"
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Find-Standalone([string]$Root, [string]$Executable) {
    $match = Get-ChildItem -LiteralPath $Root -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName $Executable) } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $match) {
        throw "Nuitka standalone directory containing $Executable was not produced"
    }
    return $match.FullName
}

$resolvedOutput = Resolve-SafeChild $OutputDir $projectRoot
# MSVC's linker response files still use the active Windows code page in parts
# of the toolchain. A Chinese output path is therefore corrupted at the final
# link step even when Python itself runs in UTF-8 mode. Keep the large,
# disposable compiler tree on F: under a fixed ASCII-only parent.
$buildDir = Resolve-SafeChild (Join-Path $compilerRoot "symphony-product") $compilerRoot

$required = @(
    (Join-Path $projectRoot "app\license_public_key.pem"),
    (Join-Path $toolsRoot "symphony_video_batch.py"),
    (Join-Path $toolsRoot "canvas_video_batch.py"),
    (Join-Path $OpenCliRoot "dist\src\main.js"),
    (Join-Path $OpenCliRoot "clis\tiktok-symphony"),
    (Join-Path $OpenCliRoot "package.json"),
    (Join-Path $OpenCliRoot "package-lock.json"),
    (Join-Path $OpenCliRoot "cli-manifest.json"),
    (Join-Path $OpenCliRoot "LICENSE"),
    (Join-Path $OpenCliRoot "extension\manifest.json"),
    (Join-Path $OpenCliRoot "extension\dist\background.js"),
    $NodeExe,
    $FfprobeExe
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing build input: $path"
    }
}

if (-not $ReuseCompiled) {
    if (Test-Path -LiteralPath $buildDir) {
        Remove-Item -Recurse -Force -LiteralPath $buildDir
    }
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
}
if (Test-Path -LiteralPath $resolvedOutput) {
    Remove-Item -Recurse -Force -LiteralPath $resolvedOutput
}
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

# Nuitka otherwise inherits the legacy Windows code page and can fail while
# scanning UTF-8 .pth metadata on Chinese Windows.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "$projectRoot;$toolsRoot"

if (-not $ReuseCompiled) {
    Push-Location $projectRoot
    try {
    uv run --isolated --python 3.12 --with "nuitka>=2.7,<3" python -X utf8 -m nuitka `
        --mode=standalone `
        --assume-yes-for-downloads `
        --output-dir="$buildDir" `
        --output-filename="SymphonyLocal.exe" `
        --include-package=app `
        --include-data-files="app/license_public_key.pem=app/license_public_key.pem" `
        --nofollow-import-to=pytest `
        "app\product_cli.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka proxy build failed with exit code $LASTEXITCODE"
    }

    uv run --isolated --python 3.12 --with "nuitka>=2.7,<3" python -X utf8 -m nuitka `
        --mode=standalone `
        --assume-yes-for-downloads `
        --output-dir="$buildDir" `
        --output-filename="SymphonyBatch.exe" `
        --include-package=app `
        --include-module=symphony_video_batch `
        --include-module=canvas_video_batch `
        --include-data-files="app/license_public_key.pem=app/license_public_key.pem" `
        --nofollow-import-to=pytest `
        "app\batch_product_cli.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka batch build failed with exit code $LASTEXITCODE"
    }
    } finally {
        Pop-Location
    }
}

$runtime = Join-Path $resolvedOutput "runtime"
$proxyOut = Join-Path $runtime "proxy"
$batchOut = Join-Path $runtime "batch"
$openCliOut = Join-Path $runtime "opencli"
$nodeOut = Join-Path $runtime "node"
$binOut = Join-Path $runtime "bin"
$extensionOut = Join-Path $resolvedOutput "chrome-extension"
$configOut = Join-Path $resolvedOutput "config"

Copy-Item -Recurse -Force -LiteralPath (Find-Standalone $buildDir "SymphonyLocal.exe") -Destination $proxyOut
Copy-Item -Recurse -Force -LiteralPath (Find-Standalone $buildDir "SymphonyBatch.exe") -Destination $batchOut

New-Item -ItemType Directory -Force -Path $openCliOut, $nodeOut, $binOut, $extensionOut, $configOut | Out-Null
Copy-Item -Recurse -Force -LiteralPath (Join-Path $OpenCliRoot "dist") -Destination (Join-Path $openCliOut "dist")
Copy-Item -Recurse -Force -LiteralPath (Join-Path $OpenCliRoot "clis") -Destination (Join-Path $openCliOut "clis")
Copy-Item -Force -LiteralPath `
    (Join-Path $OpenCliRoot "package.json"), `
    (Join-Path $OpenCliRoot "package-lock.json"), `
    (Join-Path $OpenCliRoot "cli-manifest.json"), `
    (Join-Path $OpenCliRoot "LICENSE") `
    -Destination $openCliOut

# Install production dependencies into the staged package. Ignore lifecycle
# scripts: adapters and the extension are copied from the reviewed build inputs.
Push-Location $openCliOut
try {
    npm.cmd ci --omit=dev --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {
        throw "OpenCLI production dependency install failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

Copy-Item -Force -LiteralPath $NodeExe -Destination (Join-Path $nodeOut "node.exe")
$nodeLicense = Join-Path (Split-Path -Parent $NodeExe) "LICENSE"
if (-not (Test-Path -LiteralPath $nodeLicense)) {
    throw "Node.js LICENSE is missing beside $NodeExe"
}
Copy-Item -Force -LiteralPath $nodeLicense -Destination (Join-Path $nodeOut "LICENSE")

Copy-Item -Force -LiteralPath $FfprobeExe -Destination (Join-Path $binOut "ffprobe.exe")

Copy-Item -Recurse -Force -LiteralPath (Join-Path $OpenCliRoot "extension\dist") -Destination (Join-Path $extensionOut "dist")
Copy-Item -Recurse -Force -LiteralPath (Join-Path $OpenCliRoot "extension\icons") -Destination (Join-Path $extensionOut "icons")
Copy-Item -Force -LiteralPath `
    (Join-Path $OpenCliRoot "extension\manifest.json"), `
    (Join-Path $OpenCliRoot "extension\popup.html"), `
    (Join-Path $OpenCliRoot "extension\popup.js") `
    -Destination $extensionOut

Copy-Item -Force -LiteralPath (Join-Path $projectRoot "app\license_public_key.pem") -Destination $configOut

$apiConfig = @'
{
  "providers": {
    "symphony_video": {
      "kind": "async_video_generation",
      "base_url": "http://127.0.0.1:8765",
      "auth": "none",
      "api_key": ""
    }
  }
}
'@
Write-Utf8File (Join-Path $configOut "api配置.json") $apiConfig

$localConfig = @'
{
  "监听端口": 8765,
  "node": "runtime/node/node.exe",
  "OpenCLI入口": "runtime/opencli/dist/src/main.js",
  "ffprobe": "runtime/bin/ffprobe.exe",
  "默认主账号": null,
  "默认账号": null
}
'@
Write-Utf8File (Join-Path $configOut "symphony配置.json") $localConfig

$environment = @'
@echo off
set "SYMPHONY_ROOT=%~dp0"
if "%SYMPHONY_ROOT:~-1%"=="\" set "SYMPHONY_ROOT=%SYMPHONY_ROOT:~0,-1%"
set "SYMPHONY_DATA=%LOCALAPPDATA%\Symphony"
if not exist "%SYMPHONY_DATA%" mkdir "%SYMPHONY_DATA%"
set "OPENCLI_NODE_PATH=%SYMPHONY_ROOT%\runtime\node\node.exe"
set "OPENCLI_CLI_PATH=%SYMPHONY_ROOT%\runtime\opencli\dist\src\main.js"
set "SYMPHONY_ASSET_CACHE=%SYMPHONY_DATA%\asset_cache.json"
set "SYMPHONY_LICENSE_PUBLIC_KEY=%SYMPHONY_ROOT%\config\license_public_key.pem"
set "SYMPHONY_BATCH_CONFIG=%SYMPHONY_ROOT%\config\api配置.json"
set "SYMPHONY_LOCAL_CONFIG=%SYMPHONY_ROOT%\config\symphony配置.json"
set "SYMPHONY_PRODUCT_ROOT=%SYMPHONY_ROOT%"
set "SYMPHONY_DIRECT_ONLY=1"
'@
Write-Utf8File (Join-Path $resolvedOutput "环境.cmd") $environment

$startService = @'
@echo off
setlocal
call "%~dp0环境.cmd"
cd /d "%SYMPHONY_ROOT%"
"%SYMPHONY_ROOT%\runtime\proxy\SymphonyLocal.exe" serve --host 127.0.0.1 --port 8765
'@
Write-Utf8File (Join-Path $resolvedOutput "启动服务.cmd") $startService

$machine = @'
@echo off
setlocal
call "%~dp0环境.cmd"
"%SYMPHONY_ROOT%\runtime\proxy\SymphonyLocal.exe" machine
pause
'@
Write-Utf8File (Join-Path $resolvedOutput "查看机器码.cmd") $machine

$status = @'
@echo off
setlocal
call "%~dp0环境.cmd"
"%SYMPHONY_ROOT%\runtime\proxy\SymphonyLocal.exe" status
pause
'@
Write-Utf8File (Join-Path $resolvedOutput "许可证状态.cmd") $status

$activate = @'
@echo off
setlocal
call "%~dp0环境.cmd"
if "%~1"=="" (
  echo 请把许可证文件拖到“激活许可证.cmd”上，或在命令行把许可证路径作为第一个参数传入。
  pause
  exit /b 2
)
"%SYMPHONY_ROOT%\runtime\proxy\SymphonyLocal.exe" activate --license-file "%~1"
pause
'@
Write-Utf8File (Join-Path $resolvedOutput "激活许可证.cmd") $activate

$login = @'
@echo off
setlocal
call "%~dp0环境.cmd"
cd /d "%SYMPHONY_ROOT%"
"%OPENCLI_NODE_PATH%" "%OPENCLI_CLI_PATH%" tiktok-symphony direct-session -f json
pause
'@
Write-Utf8File (Join-Path $resolvedOutput "首次登录.cmd") $login

$batch = @'
@echo off
setlocal
call "%~dp0环境.cmd"
cd /d "%SYMPHONY_ROOT%"
"%SYMPHONY_ROOT%\runtime\batch\SymphonyBatch.exe" %*
'@
Write-Utf8File (Join-Path $resolvedOutput "运行批处理.cmd") $batch

$guide = @'
Symphony 受控内测便携版

1. 解压整个目录，不要只复制 EXE。
2. 在 Chrome 打开 chrome://extensions，开启“开发者模式”，选择“加载已解压的扩展程序”，目录选 chrome-extension。
3. 双击“查看机器码.cmd”，把机器码发给发行者；不要发送密码、Cookie 或验证码。
4. 收到许可证后，把许可证文件拖到“激活许可证.cmd”上。
5. 双击“首次登录.cmd”，在 Chrome 中亲自完成登录、验证码、Passkey 或两步验证。
6. 双击“启动服务.cmd”。
7. 从命令行调用“运行批处理.cmd”，参数与项目 README 中的 preflight/warmup/submit/poll 一致。

运行数据保存在：
%LOCALAPPDATA%\Symphony

本包没有 Authenticode 代码签名。首次运行可能出现 SmartScreen；仅在 SHA-256 与发行者提供值一致时选择“更多信息 → 仍要运行”。
'@
Write-Utf8File (Join-Path $resolvedOutput "使用说明.txt") $guide

$thirdParty = @'
Third-party components included in this controlled test bundle:

OpenCLI
  License: Apache License 2.0
  Upstream: https://github.com/jackwener/opencli
  License file: runtime/opencli/LICENSE

Node.js
  License: MIT and bundled third-party notices
  Upstream: https://nodejs.org/
  License file: runtime/node/LICENSE

FFmpeg ffprobe (gyan.dev full build)
  This specific build reports --enable-gpl --enable-version3.
  Upstream: https://ffmpeg.org/
  Build source: https://www.gyan.dev/ffmpeg/builds/
  Public redistribution requires completing the corresponding GPLv3 source
  and license obligations. This bundle is for controlled internal testing
  until that compliance package is added and independently reviewed.
'@
Write-Utf8File (Join-Path $resolvedOutput "THIRD-PARTY-NOTICES.txt") $thirdParty

# The issuer private key remains in the build machine's Windows Credential
# Manager. Only the public key and the signed manifest enter the product.
Push-Location $projectRoot
try {
    uv run python -m app.license_admin sign-bundle --root $resolvedOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Bundle signing failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$archive = "$resolvedOutput.zip"
if (Test-Path -LiteralPath $archive) {
    Remove-Item -Force -LiteralPath $archive
}
Compress-Archive -Path (Join-Path $resolvedOutput "*") -DestinationPath $archive -CompressionLevel Optimal

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
$bytes = (Get-Item -LiteralPath $archive).Length
$files = @(Get-ChildItem -Recurse -File -LiteralPath $resolvedOutput).Count
Write-Host "Product stage: $resolvedOutput"
Write-Host "Portable ZIP: $archive"
Write-Host "Files: $files"
Write-Host "Bytes: $bytes"
Write-Host "SHA-256: $hash"
Write-Host "Unsigned controlled-test build: SmartScreen may warn on first run."
