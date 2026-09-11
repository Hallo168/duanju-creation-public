param([switch]$NoBrowser, [switch]$RestartBackend)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$workbenchDirectory = $PSScriptRoot
$workbenchProjectRoot = [IO.Path]::GetFullPath((Join-Path $workbenchDirectory '..\..'))
$workbenchLogDirectory = Join-Path $workbenchDirectory '.local'
$workbenchPageUrl = 'http://localhost:3000/pipeline'
$workbenchApiUrl = 'http://127.0.0.1:4317/api/health'
$workbenchRepairUrl = 'http://127.0.0.1:14317/api/health'
$workbenchNeedsRepairService = $false

function Test-WorkbenchReady {
    param([string]$Url, [switch]$Api)
    try {
        $result = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
    } catch {
        return $false
    }
    if ($result.StatusCode -ne 200) { return $false }
    if ($Api) {
        try { $health = $result.Content | ConvertFrom-Json } catch { return $false }
        if (-not $health.ok -or -not $health.projectRoot) { return $false }
        if ([IO.Path]::GetFullPath($health.projectRoot) -ne $workbenchProjectRoot) {
            throw '后台端口正在被另一份项目使用，请先关闭那份工作台。'
        }
    }
    return $true
}

function Start-WorkbenchService {
    param(
        [string]$Name,
        [string]$LogName,
        [string]$Url,
        [int]$Port,
        [string[]]$NodeArguments,
        [switch]$Api
    )
    if (Test-WorkbenchReady -Url $Url -Api:$Api) {
        Write-Host "$Name 已在运行，直接使用。"
        return
    }

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    $startedProcess = $null
    if ($listeners.Count -eq 0) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $stdout = Join-Path $workbenchLogDirectory "$LogName-$stamp.log"
        $stderr = Join-Path $workbenchLogDirectory "$LogName-$stamp-error.log"
        $startedProcess = Start-Process -FilePath $workbenchNode -ArgumentList $NodeArguments `
            -WorkingDirectory $workbenchDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        Write-Host "正在启动 $Name……"
    } else {
        Write-Host "$Name 的端口已经开启，正在等待连接……"
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-WorkbenchReady -Url $Url -Api:$Api) {
            Write-Host "$Name 已就绪。"
            return
        }
        if ($startedProcess) {
            $startedProcess.Refresh()
            if ($startedProcess.HasExited) {
                throw "$Name 启动失败，请查看日志：$stderr"
            }
        }
        Start-Sleep -Milliseconds 750
    }
    throw "$Name 未能及时就绪，请检查是否有其他程序占用了端口 $Port。日志保存在 $workbenchLogDirectory"
}

function Update-WorkbenchBackend {
    if (-not (Test-WorkbenchReady -Url $workbenchApiUrl -Api)) { return }
    $health = (Invoke-WebRequest -UseBasicParsing -Uri $workbenchApiUrl -TimeoutSec 3).Content | ConvertFrom-Json
    if (-not $RestartBackend -and $health.repairContextMode -eq 'source-snapshots-v1' -and $health.studioApiVersion -ge 3 -and $health.videoCatalogVersion -ge 4) { return }
    $listener = Get-NetTCPConnection -State Listen -LocalPort 4317 | Select-Object -First 1
    $backendProcess = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $listener.OwningProcess)
    if ($backendProcess.Name -ne 'node.exe' -or $backendProcess.CommandLine -notmatch 'local-server\.mjs') {
        throw '无法确认后台进程身份，已停止自动重启。'
    }
    $children = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId=" + $backendProcess.ProcessId) |
        Where-Object { $_.Name -ne 'conhost.exe' })
    if ($children.Count -gt 0 -or $health.activeTaskCount -gt 0) {
        $message = '后台仍有任务运行，保留当前后台，并单独启动修复功能。'
        if ($RestartBackend) { throw '后台仍有任务运行，未重启。直接双击启动脚本可使用独立修复后台。' }
        Write-Host $message -ForegroundColor Yellow
        $script:workbenchNeedsRepairService = $true
        return
    }
    Write-Host '后台已空闲，正在加载工作台更新……'
    Stop-Process -Id $backendProcess.ProcessId -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (Get-NetTCPConnection -State Listen -LocalPort 4317 -ErrorAction SilentlyContinue) {
        if ([DateTime]::UtcNow -ge $deadline) { throw '旧后台尚未释放端口，请稍后重试。' }
        Start-Sleep -Milliseconds 250
    }
}

function Stop-IdleRepairService {
    if (-not (Test-WorkbenchReady -Url $workbenchRepairUrl -Api)) { return }
    $health = (Invoke-WebRequest -UseBasicParsing -Uri $workbenchRepairUrl -TimeoutSec 3).Content | ConvertFrom-Json
    if ($health.repairContextMode -ne 'source-snapshots-v1' -or $health.activeTaskCount -gt 0) { return }
    $listener = Get-NetTCPConnection -State Listen -LocalPort 14317 | Select-Object -First 1
    $repairProcess = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $listener.OwningProcess)
    $children = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId=" + $repairProcess.ProcessId) |
        Where-Object { $_.Name -ne 'conhost.exe' })
    if ($repairProcess.Name -eq 'node.exe' -and $repairProcess.CommandLine -match 'local-server\.mjs' -and $children.Count -eq 0) {
        Stop-Process -Id $repairProcess.ProcessId -ErrorAction Stop
    }
}

try {
    $nodeCandidates = @()
    $installedNode = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($installedNode) { $nodeCandidates += $installedNode.Source }
    $nodeCandidates += Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    $nodeCandidates += Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'nodejs\node.exe'
    $workbenchNode = $null
    foreach ($candidate in $nodeCandidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $reportedVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($candidate).ProductVersion
            if ([version]$reportedVersion -ge [version]'22.13.0') {
                $workbenchNode = $candidate
                break
            }
        } catch {
            # Try the next installed runtime.
        }
    }
    if (-not $workbenchNode) {
        throw '未找到可用的 Node.js 运行组件，需要 Node.js 22.13 或更新版本。'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $workbenchDirectory 'node_modules\vinext\dist\cli.js') -PathType Leaf)) {
        throw '工作台组件缺失，请在工作台目录重新准备依赖后再启动。'
    }
    $env:Path = "$(Split-Path -Parent $workbenchNode);$env:Path"
    $codexCandidates = @()
    $installedCodex = Get-Command codex.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($installedCodex) { $codexCandidates += $installedCodex.Source }
    $codexBinRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'OpenAI\Codex\bin'
    if (Test-Path -LiteralPath $codexBinRoot -PathType Container) {
        foreach ($versionDirectory in (Get-ChildItem -LiteralPath $codexBinRoot -Directory | Sort-Object LastWriteTimeUtc -Descending)) {
            $codexCandidates += Join-Path $versionDirectory.FullName 'codex.exe'
        }
    }
    $workbenchCodex = $codexCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ($workbenchCodex) {
        $env:Path = "$(Split-Path -Parent $workbenchCodex);$env:Path"
        Write-Host '已找到 Codex 程序。'
    } else {
        Write-Host '未找到 Codex 程序，AI 修复暂时不可用；请安装或打开 Codex 后重新启动后台。' -ForegroundColor Yellow
    }
    if (-not $env:ComSpec) {
        $env:ComSpec = Join-Path ([Environment]::GetFolderPath('Windows')) 'System32\cmd.exe'
    }
    New-Item -ItemType Directory -Path $workbenchLogDirectory -Force | Out-Null

    Update-WorkbenchBackend
    Start-WorkbenchService -Name '工作台后台' -LogName 'api-start' -Url $workbenchApiUrl `
        -Port 4317 -NodeArguments @('local-server.mjs') -Api
    if ($workbenchNeedsRepairService) {
        $previousApiPort = $env:STORYBOARD_API_PORT
        try {
            $env:STORYBOARD_API_PORT = '14317'
            Start-WorkbenchService -Name 'AI 修复后台' -LogName 'repair-start' -Url $workbenchRepairUrl `
                -Port 14317 -NodeArguments @('local-server.mjs') -Api
        } finally {
            $env:STORYBOARD_API_PORT = $previousApiPort
        }
    } else {
        Stop-IdleRepairService
    }
    Start-WorkbenchService -Name '文案工作台页面' -LogName 'page-start' -Url $workbenchPageUrl `
        -Port 3000 -NodeArguments @('node_modules/vinext/dist/cli.js', 'dev')

    Write-Host "工作台已启动：$workbenchPageUrl" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $workbenchPageUrl }
    exit 0
} catch {
    Write-Host "启动未完成：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
