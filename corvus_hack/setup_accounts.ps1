# setup_accounts.ps1 — Run ONCE as Mike (admin) to create all CorvusClient accounts.
# Must be run in an elevated PowerShell session.
#
# What this does:
#   1. Creates 4 standard Windows user accounts
#   2. Grants them read access to E:\Corvus_Careebridge
#   3. Deploys the All-Users startup script (corvus_worker.bat)
#
# One-time manual step NOT covered here (requires login to each account):
#   - Install antidetect browser (GoLogin/IXBrowser/MoreLogin/AdsPower)
#   - Set UltraViewer unattended password in-app

param(
    [string]$Password = "Corvus2026!"
)

$ErrorActionPreference = "Stop"

# ── 1. Check elevation ────────────────────────────────────────────────────────
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this script as Administrator (right-click → Run as admin)."
    exit 1
}

# ── 2. Create accounts ────────────────────────────────────────────────────────
$accounts = @(
    @{ Name = "CorvusGoLogin";   Browser = "GoLogin"   }
    @{ Name = "CorvusIXBrowser"; Browser = "IXBrowser" }
    @{ Name = "CorvusMoreLogin"; Browser = "MoreLogin" }
    @{ Name = "CorvusAdsPower";  Browser = "AdsPower"  }
)

foreach ($acct in $accounts) {
    $name = $acct.Name
    $existing = Get-LocalUser -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Account already exists: $name"
        # Ensure password is current
        $existing | Set-LocalUser -Password (ConvertTo-SecureString $Password -AsPlainText -Force)
    } else {
        Write-Host "Creating account: $name"
        New-LocalUser -Name $name `
            -Password (ConvertTo-SecureString $Password -AsPlainText -Force) `
            -FullName "Corvus $($acct.Browser)" `
            -Description "Corvus client account for $($acct.Browser)" `
            -AccountNeverExpires `
            -PasswordNeverExpires
    }

    # Ensure in Users group, not Administrators
    $inUsers = (Get-LocalGroupMember -Group "Users" | Where-Object { $_.Name -like "*\$name" })
    if (-not $inUsers) {
        Add-LocalGroupMember -Group "Users" -Member $name
    }
    $inAdmin = (Get-LocalGroupMember -Group "Administrators" -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*\$name" })
    if ($inAdmin) {
        Remove-LocalGroupMember -Group "Administrators" -Member $name
        Write-Host "  Removed $name from Administrators."
    }

    Write-Host "  [OK] $name ready."
}

# ── 3. Grant read access to E:\Corvus_Careebridge for all Corvus accounts ─────
$corvusPath = "E:\Corvus_Careebridge"
if (Test-Path $corvusPath) {
    foreach ($acct in $accounts) {
        $name = $acct.Name
        # /grant:r = read, /t = tree (all subfolders), /e = only non-empty dirs
        icacls $corvusPath /grant "${name}:(OI)(CI)R" /t /q
        Write-Host "Granted read access to $corvusPath for $name"
    }
} else {
    Write-Warning "Path $corvusPath not found — skipping ACL grant."
}

# ── 4. Create logs directory ──────────────────────────────────────────────────
$logsDir = "E:\Corvus_Careebridge\logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
    Write-Host "Created logs directory: $logsDir"
}
# Allow Corvus accounts to write logs
foreach ($acct in $accounts) {
    icacls $logsDir /grant "$($acct.Name):(OI)(CI)M" /q
}

# ── 5. Deploy All-Users startup script ───────────────────────────────────────
$startupDir = "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp"
$batPath = Join-Path $startupDir "corvus_worker.bat"

$batContent = @'
@echo off
REM Corvus worker startup — runs for ALL Windows users on login.
REM Skip if this is Mike's or Administrator's account.
if /I "%USERNAME%"=="Mike"          goto :EOF
if /I "%USERNAME%"=="Administrator"  goto :EOF

REM Only run for known Corvus accounts
set CORVUS_ACCT=%USERNAME%
set "CORVUS_CAPTURE_PORT=8703"
set "CORVUS_ACCOUNT=%USERNAME%"
set "PATH=C:\Python314;%PATH%"

REM Start capture_mcp on port 8703 (DXGI — must run in this session)
start "corvus_capture" /B C:\Python314\pythonw.exe -m capture_mcp.server --http 8703 --workdir "E:\Corvus_Careebridge\capture_mcp"

REM Wait 5s for capture server to bind
timeout /t 5 /nobreak >NUL

REM Start worker (loops, polling master for session assignment)
start "corvus_worker" /B C:\Python314\pythonw.exe E:\Corvus_Careebridge\corvus_hack\worker.py >> "E:\Corvus_Careebridge\logs\worker_%USERNAME%.log" 2>&1
'@

Set-Content -Path $batPath -Value $batContent -Encoding ASCII
Write-Host ""
Write-Host "Startup script deployed to: $batPath"
Write-Host ""

# ── 6. Summary ───────────────────────────────────────────────────────────────
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Accounts created (password: $Password):"
foreach ($acct in $accounts) {
    Write-Host "  - $($acct.Name)  ($($acct.Browser))"
}
Write-Host ""
Write-Host "Next steps (MANUAL — one-time per account):"
Write-Host "  1. Fast User Switch to each account (Win+L → Switch user)"
Write-Host "  2. Install the antidetect browser for that account"
Write-Host "     GoLogin: https://app.gologin.com/downloads"
Write-Host "     IXBrowser: https://www.ixbrowser.com/download"
Write-Host "     MoreLogin: https://www.morelogin.com/download"
Write-Host "     AdsPower: https://www.adspower.com/download"
Write-Host "  3. Install UltraViewer and set unattended access password"
Write-Host "  4. Log out → the worker bat will auto-run next login"
Write-Host ""
Write-Host "Then from Mike's session:"
Write-Host "  1. Run: corvus_hack\start_master.ps1"
Write-Host "  2. Telegram: 'start session for <client_chat_id> on <platform>'"
