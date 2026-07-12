# start_telegram_plugin.ps1 — Keep Claude Code Telegram channel plugin alive.
# Telegram DMs are forwarded to Claude Code and answered interactively.
# Restart on exit (network blip, update, etc.)

Set-Location "E:\Corvus_Careebridge"

$restarts = 0
while ($true) {
    $restarts++
    if ($restarts -gt 1) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss') Telegram plugin exited. Restarting (#$restarts)..."
        Start-Sleep -Seconds 10
    }
    claude --dangerously-skip-permissions --channels plugin:telegram@claude-plugins-official
}
