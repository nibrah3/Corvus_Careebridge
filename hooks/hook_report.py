"""
hook_report.py — UserPromptSubmit hook.
Checks logs/status_report.json for an unshown 30-min report.
If found, prints it so it appears in the Claude Code session before
the next response. Marks it shown so it only appears once.
"""
import json, sys
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
REPORT = CB_DIR / "logs" / "status_report.json"

if not REPORT.exists():
    sys.exit(0)

try:
    data = json.loads(REPORT.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

if data.get("shown"):
    sys.exit(0)

# Mark shown immediately
data["shown"] = True
REPORT.write_text(json.dumps(data, indent=2), encoding="utf-8")

machine = data.get("machine", "?").upper()
t       = data.get("time", "")
sha     = data.get("sha", "?")
pip     = data.get("pip", "?")
hooks   = data.get("hooks", "?")
mcp     = data.get("mcp", "?")
tunnel  = data.get("tunnel", "?")
vbs     = data.get("vbs", "?")
pending = data.get("pending", 0)
last    = data.get("last_msg", "")

print(f"\n{'='*55}")
print(f"  {machine} STATUS REPORT — {t}")
print(f"{'='*55}")
print(f"  Git SHA:     {sha}")
print(f"  Pip count:   {pip}")
print(f"  Hooks:       {hooks}")
print(f"  MCPs:        {mcp}")
print(f"  Tunnel:      {tunnel}")
print(f"  Startup VBS: {vbs}")
print(f"  Pending:     {pending} instruction(s)")
if last:
    print(f"  Last msg:    {last[:100]}")
print(f"{'='*55}\n")
