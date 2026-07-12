# start_telegram_plugin.ps1 — Keep Telegram → Claude Code bridge alive at boot.
# telegram_claude_bridge.py polls DMs and calls claude --print for each message.

Set-Location "E:\Corvus_Careebridge"

$py     = "C:\Python314\python.exe"
$bridge = "E:\Corvus_Careebridge\telegram_claude_bridge.py"

$restarts = 0
while ($true) {
    $restarts++
    if ($restarts -gt 1) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss') Bridge exited. Restarting (#$restarts)..."
        Start-Sleep -Seconds 10
    }
    & $py $bridge --stream
}
