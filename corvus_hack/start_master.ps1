# start_master.ps1 — Launch master_dispatcher.py in a hidden window.
# Run this from Mike's session before switching to any client account.

$py  = "C:\Python314\pythonw.exe"
$script = "E:\Corvus_Careebridge\corvus_hack\master_dispatcher.py"
$log = "E:\Corvus_Careebridge\logs\master_dispatcher.log"

# Check if already running on port 9200
$listening = (Test-NetConnection -ComputerName localhost -Port 9200 -InformationLevel Quiet 2>$null)
if ($listening) {
    Write-Host "Master dispatcher already running on port 9200."
    exit 0
}

Start-Process $py -ArgumentList $script -WorkingDirectory "E:\Corvus_Careebridge" `
    -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err"

Start-Sleep -Seconds 3
$ok = (Test-NetConnection -ComputerName localhost -Port 9200 -InformationLevel Quiet 2>$null)
if ($ok) {
    Write-Host "Master dispatcher started on port 9200."
} else {
    Write-Host "ERROR: Master dispatcher did not start. Check $log"
    exit 1
}
