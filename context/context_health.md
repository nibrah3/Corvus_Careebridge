# Context: Health & Maintenance Session

Loaded when session involves system health, MCP restarts, or infrastructure diagnosis.

## Health check order
1. Read restart_events from SQLite (health_daemon logs here)
2. Check all 15 MCP ports (8701-8715)
3. Check SSH tunnel (6380, 5433, 3101, 7788)
4. Check Task Scheduler last-run times
5. Check IXBrowser API (localhost:53200)
6. VPS status via `mcp__vps__get_system_status`

## MCP restart
Use `scripts/start_mcps.ps1 -Force` to restart a specific server.
The health daemon handles automatic restarts — health sweep handles diagnosis and root cause.

## Tunnel restart
Kill zombie SSH: `taskkill /F /IM ssh.exe`
Rebuild tunnel: `scripts/vps_tunnel.ps1`
Verify: `Test-NetConnection localhost 6380`

## Pattern diagnosis
If a server restarts > 3 times in 6 hours:
1. Check the server's log file in `logs/`
2. Look for memory errors, import errors, port conflicts
3. Check if a recent code change affected that server
4. If memory: consider reducing batch sizes in that MCP's tools
5. If import error: check Python environment and dependencies

## Reporting
All health sweeps send a summary to Telegram including restart history.
Format: one line per component, ✓ or ✗, restart count if > 0.
