# Skill: System Health Sweep

**Trigger:** `CareerBridge_HealthSweep` Task Scheduler task (every 6 hours).
**Mode:** Fully autonomous — no user present. Run to completion.

---

## What you are doing

Running the SWEEP.md health checklist programmatically and reporting results to Telegram.

---

## Step 1 — Check local MCP ports

For each MCP server, verify it is listening:

| Server | Port |
|---|---|
| humanizer_mcp | 8701 |
| capture_mcp | 8702 |
| uia_mcp | 8703 |
| browser_mcp | 8704 |
| gemini_mcp | 8705 |
| telegram_mcp | 8706 |
| answer_mcp | 8707 |
| sqlite_mcp | 8708 |
| memory_mcp | 8709 |
| dom_mcp | 8710 |
| cdp_mcp | 8712 |
| vps_mcp | 8713 |
| schools_mcp | 8714 |
| ixbrowser_mcp | 8715 |

Use Bash: `for port in 8701 8702 8703 8704 8705 8706 8707 8708 8709 8710 8712 8713 8714 8715; do (echo >/dev/tcp/127.0.0.1/$port) 2>/dev/null && echo "OK $port" || echo "DOWN $port"; done`

On Windows PowerShell: use `Test-NetConnection -ComputerName localhost -Port $port`.

## Step 2 — Check VPS connection

Call `mcp__vps__get_system_status`.
Record: redis status, postgres status, pending_approvals count, jobs_by_status breakdown.
If either is error: this is a critical failure — flag in report.

## Step 3 — Check SSH tunnel

Use Bash to verify the tunnel ports are forwarded:
- `localhost:6380` (Redis)
- `localhost:5433` (Postgres)
- `localhost:3101` (Crawlee)
- `localhost:7788` (Firecrawl)

## Step 4 — Check Task Scheduler last-run times

Use PowerShell:
```
Get-ScheduledTask -TaskName "CareerBridge_Gate","CareerBridge_Catalogue","CareerBridge_WeeklyGap","CareerBridge_SchoolReport_6h","CareerBridge_HealthSweep" | 
  Select-Object TaskName, @{N="LastRun";E={$_.LastRunTime}}, @{N="LastResult";E={$_.LastTaskResult}}
```

Flag any task that hasn't run within its expected interval or has LastTaskResult != 0.

## Step 5 — Check IXBrowser API

Verify IXBrowser local API is reachable: `curl http://127.0.0.1:53200/api/v2/profile-list` (or PowerShell equivalent).

## Step 6 — Build and send report

Compose a compact Telegram message:

```
🔍 Health Sweep — {timestamp}

MCPs: {N}/14 up {list of DOWN if any}
VPS: redis={status} postgres={status}
Tunnel: {OK/DOWN per port}
Scheduler: {pass/fail per task}
IXBrowser: {up/down}
Pending approvals: {N}
Jobs pending: {N}
```

Call `mcp__telegram__notify(report)`.

If any critical failure (VPS down, >2 MCPs down, tunnel broken): add `⚠️ ACTION NEEDED` prefix.

---

## Rules

- Complete all steps even if one fails — report everything.
- Do not attempt to restart failed MCPs — just report. Restarts are manual.
