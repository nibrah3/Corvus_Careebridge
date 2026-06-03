# Shared Error Recovery Reference

This file is referenced by all SOPs and skills. Do not modify the shared patterns
without updating all referencing files.

---

## Standard Diagnosis + Fix Patterns

### Tunnel down (Redis 6380, Postgres 5433, Crawlee 3101, Firecrawl 7788)
```
1. Test-NetConnection localhost 6380  (or 5433 / 3101 / 7788)
2. If down: run scripts/vps_tunnel.ps1
3. Wait 10s
4. Retry the original step once
5. If still down: mcp__telegram__notify("❌ [skill] stuck: tunnel down") → stop
```

### MCP server down (any port 8701-8715)
```
1. Test-NetConnection localhost {port}
2. If down: run scripts/start_mcps.ps1 (starts all, skips running ones)
3. Wait 5s, retry once
4. If still down: check logs/ for that server's error log
5. Report: mcp__telegram__notify("❌ {server} on :{port} failing — check logs/")
```

### IXBrowser / CDP stale connection
```
1. mcp__ixbrowser__reconnect(profile_id)
2. Wait 3s, retry CDP tool call
3. If reconnect fails: mcp__ixbrowser__close_profile(profile_id), wait 3s, mcp__ixbrowser__connect_profile(profile_id)
4. If IXBrowser API unreachable: check process via Get-Process -Name "*ixbrowser*"
   If not running: Start-Process "C:\Program Files\IXBrowser\ixBrowser.exe"; wait 15s
5. Max 2 reconnect attempts before escalating to Telegram and stopping
```

### Gemini Files API failure
```
Upload timeout: retry once with 30s extra timeout
File stuck PROCESSING > 60s: delete and re-upload
API key error: stop immediately — mcp__telegram__notify("❌ Gemini API key error — check .env")
Never fall back to screenshot for annotation content
```

### Redis gate / queue failure
```
Gate push fails: write answer to SQLite as backup gate record (token_events table)
mcp__telegram__notify with the answer content so operator can manually approve
This is non-fatal — continue with next item
```

### General retry rule
- Max 2 retries per step
- Wait: 5s after first failure, 15s after second
- Log every fix attempt: what failed, what was tried, outcome
- After 2 failed retries: notify Telegram, stop cleanly
- Never silently skip a failed step and proceed
