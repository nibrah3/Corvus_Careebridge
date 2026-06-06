# MCP Server Restart Guide

When an MCP server stops responding you will receive a Telegram alert automatically.
Follow the steps for the affected server below.

---

## General steps (all servers)

1. Open a PowerShell terminal on your Windows machine.
2. Navigate to `E:\Corvus_Careebridge`.
3. Run the restart command for the affected server (see below).
4. Close and reopen Claude Code so it reconnects to the server.

---

## Per-server restart commands

### humanizer
```
cd E:\Corvus_Careebridge
python -m humanizer_mcp.server
```

### capture
```
cd E:\Corvus_Careebridge
python -m capture_mcp.server
```

### uia
```
cd E:\Corvus_Careebridge
python -m uia_mcp.server
```

### browser
```
cd E:\Corvus_Careebridge
python -m browser_mcp.server
```

### gemini
```
cd E:\Corvus_Careebridge
python -m gemini_mcp.server
```

### telegram
```
cd E:\Corvus_Careebridge
python -m telegram_mcp.server
```

### answer
```
cd E:\Corvus_Careebridge
python -m answer_mcp.server
```

### sqlite
```
cd E:\Corvus_Careebridge
python -m mcp_server_sqlite --db-path E:/Corvus_Careebridge/careerbridge.db
```

### memory (node)
```
node C:\Users\Mike\AppData\Roaming\npm\node_modules\@modelcontextprotocol\server-memory\dist\index.js
```

---

## If the restart command itself fails

- Make sure your Python venv is active: `E:\Corvus_Careebridge\.venv\Scripts\activate`
- Check for import errors: `python -c "import <server_module>"`
- Check `E:\Corvus_Careebridge\logs\mcp_<server>.err` for the error detail
- If the error mentions a missing package: `pip install -r E:\Corvus_Careebridge\requirements.txt`

---

## After restarting

Reload Claude Code (close and reopen the app or run `/reload` in the session).
The MCP server will reconnect automatically on next tool call.
