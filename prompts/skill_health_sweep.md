# Skill: System Health Sweep

**Trigger:** `CareerBridge_HealthSweep` Task Scheduler task (every 6 hours).
**Mode:** Fully autonomous — no user present. Run to completion.

---

## What you are doing

Running the health checklist programmatically and reporting results to Telegram.
Also reads the watchdog's `restart_events` SQLite log to diagnose crash patterns
and attempts root cause fixes before escalating to the operator.

Each step is modular — if one step fails, continue the rest and report UNKNOWN for that step.

---

## Step 0 — Read restart_events (pattern diagnosis)

Read `restart_events` table from SQLite (`E:\Corvus_Careebridge\careerbridge.db`):
```sql
SELECT module, port, COUNT(*) AS restarts, MAX(ts) AS last_restart
FROM restart_events
WHERE ts > strftime('%s','now') - 21600  -- last 6 hours
GROUP BY module, port
ORDER BY restarts DESC
```

For any server with restarts > 2 in the last 6 hours:
1. Read the server's log file: `logs/{module_name}.log` (last 50 lines)
2. Look for: OOM (MemoryError), import errors, port conflicts, unhandled exceptions
3. Attempt diagnosis:
   - OOM → note "reduce batch size or add swap"
   - Import error → check Python path and dependencies
   - Port conflict → check if another process holds the port
   - Crash loop → flag as CRITICAL, do not restart again — needs human investigation
4. If fixable automatically: attempt fix + restart via `scripts/start_mcps.ps1 -Force`
5. Record diagnosis in the health report

Also read `health_issues` table for any open (status='pending') issues from previous sweeps.

## Step 1 — Check local MCP ports (modular)

For each MCP server, probe the port. Run as one PowerShell batch:
```powershell
$ports = @(
  @{mod="humanizer_mcp"; port=8701},
  @{mod="capture_mcp";   port=8702},
  @{mod="uia_mcp";       port=8703},
  @{mod="browser_mcp";   port=8704},
  @{mod="gemini_mcp";    port=8705},
  @{mod="telegram_mcp";  port=8706},
  @{mod="answer_mcp";    port=8707},
  @{mod="sqlite_mcp";    port=8708},
  @{mod="memory_mcp";    port=8709},
  @{mod="dom_mcp";       port=8710},
  @{mod="cdp_mcp";       port=8712},
  @{mod="vps_mcp";       port=8713},
  @{mod="schools_mcp";   port=8714},
  @{mod="ixbrowser_mcp"; port=8715}
)
$ports | ForEach-Object {
  $r = Test-NetConnection -ComputerName localhost -Port $_.port -InformationLevel Quiet 2>$null
  if ($r) { "OK  $($_.port) $($_.mod)" } else { "DOWN $($_.port) $($_.mod)" }
}
```

If any show DOWN and they are NOT in restart_events (watchdog didn't catch it):
→ Restart via: `python scripts/start_mcps.ps1 -Force` for that specific server
→ Log a new restart_events entry manually

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

## Step 5b — Read token_events for weekly report

Query SQLite:
```sql
SELECT tool_name, COUNT(*) AS calls, skill_hint
FROM token_events
WHERE ts > strftime('%s','now') - 604800  -- last 7 days
GROUP BY tool_name
ORDER BY calls DESC
LIMIT 15
```
Include top 5 tools by call frequency in the Telegram report.
High call counts on `mcp__gemini__upload` or `mcp__capture__screenshot` are cost signals.

## Step 6 — Build and send report

Compose a compact Telegram message:

```
🔍 Health Sweep — {timestamp}

MCPs: {N}/15 up {list of DOWN if any}
Restarts (6h): {module: count, ...} or "none"
Crash patterns: {diagnosis if any} or "clean"
VPS: redis={status} postgres={status}
Tunnel: {OK/DOWN per port}
Scheduler: {pass/fail per task}
IXBrowser: {up/down}
Pending approvals: {N} | Jobs pending: {N}
Top tools (7d): {tool: count, ...}
```

Call `mcp__telegram__notify(report)`.

If critical: ⚠️ ACTION NEEDED prefix. Critical = VPS down, >2 MCPs down, tunnel broken, or crash_loop detected.

---

## Rules

- Complete all steps even if one fails — report everything.
- Do not attempt to restart failed MCPs — just report. Restarts are manual.

## On Error

**Health sweep is self-referential — it IS the error handler. Never stop mid-sweep.**

- If any individual check fails: mark it UNKNOWN, continue all other checks
- If Telegram notify fails: write results to logs/health_sweep.log as fallback
- If SQLite unavailable: run checks, report to Telegram only (skip restart_events read)
- If MCP start fails after restart attempt: flag as CRITICAL in report; investigate root cause
