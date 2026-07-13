# start_mike_mode.ps1
# Run this once after login to start all Corvus services on Mike's account.
# Relies on CORVUS_ACCOUNT=CorvusGoLogin being set in .env — no env vars needed here.

$ErrorActionPreference = "SilentlyContinue"

$py   = "C:\Python314\pythonw.exe"
$root = "E:\Corvus_Careebridge"
$hack = "$root\corvus_hack"
$logs = "$root\logs"

if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

function Start-Hidden($name, $script, $log, [string[]]$args_) {
    $argList = @($script) + $args_
    Start-Process $py -ArgumentList $argList -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$logs\$log.log" `
        -RedirectStandardError  "$logs\$log.err.log"
    Write-Host "  Started $name"
}

Write-Host ""
Write-Host "=== Corvus Mike-Mode Startup ===" -ForegroundColor Cyan
Write-Host ""

# 1 — master_dispatcher (port 9200)
Write-Host "[1/5] master_dispatcher"
$up = (Test-NetConnection localhost -Port 9200 -InformationLevel Quiet 2>$null)
if ($up) { Write-Host "  Already running on port 9200." }
else {
    Start-Hidden "master_dispatcher" "$hack\master_dispatcher.py" "master_dispatcher"
    Start-Sleep -Seconds 4
    $up = (Test-NetConnection localhost -Port 9200 -InformationLevel Quiet 2>$null)
    if (-not $up) { Write-Host "  WARNING: port 9200 not open yet — check $logs\master_dispatcher.err.log" -ForegroundColor Yellow }
}

# 2 — telegram_claude_bridge
Write-Host "[2/5] telegram_claude_bridge"
$cnt = (Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*telegram_claude_bridge*" }).Count
if ($cnt -gt 0) { Write-Host "  Already running." }
else { Start-Hidden "telegram_claude_bridge" "$root\telegram_claude_bridge.py" "telegram_bridge" }

# 3 — capture_server (port 8703) — DXGI on Mike's screen
Write-Host "[3/5] capture_server"
$up = (Test-NetConnection localhost -Port 8703 -InformationLevel Quiet 2>$null)
if ($up) { Write-Host "  Already running on port 8703." }
else {
    Start-Hidden "capture_server" "$hack\capture_server.py" "capture_server" @("8703")
    Start-Sleep -Seconds 3
}

# 4 — worker (CORVUS_ACCOUNT set via .env, bypasses the Mike/Administrator skip)
Write-Host "[4/5] worker"
$cnt = (Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*worker.py*" }).Count
if ($cnt -gt 0) { Write-Host "  Already running." }
else { Start-Hidden "worker" "$hack\worker.py" "worker_mike" }

# 5 — watchdog (monitors all 4 above, restarts on failure)
Write-Host "[5/5] watchdog"
$cnt = (Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*watchdog*" }).Count
if ($cnt -gt 0) { Write-Host "  Already running." }
else { Start-Hidden "watchdog" "$hack\watchdog.py" "watchdog" }

Write-Host ""
Write-Host "=== All services started ===" -ForegroundColor Green
Write-Host ""
Write-Host "Check logs:  Get-Content $logs\master_dispatcher.log -Tail 20 -Wait"
Write-Host "Pool status: Invoke-RestMethod http://localhost:9200/admin/pool"
Write-Host ""
Write-Host "Client flow:"
Write-Host "  1. Client texts bot 'start imocha'  →  bot confirms session ready"
Write-Host "  2. You open GoLogin profile for that client"
Write-Host "  3. Share UltraViewer ID+password with client  →  they connect to this screen"
Write-Host "  4. Claude reads screen events via DXGI  →  sends guidance to client on Telegram"
Write-Host "  5. Client texts 'stop' when done"
Write-Host ""
