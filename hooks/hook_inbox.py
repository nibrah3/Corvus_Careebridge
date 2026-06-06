"""
hook_inbox.py — PreToolUse hook. Reads logs/inbox.json.
If instructions are waiting, prints them so Claude Code injects them
into the live session context before the next tool executes.
Clears the file after reading so each instruction is shown only once.
"""
import json, sys
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
INBOX  = CB_DIR / "logs" / "inbox.json"

if not INBOX.exists():
    sys.exit(0)

try:
    items = json.loads(INBOX.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

if not items:
    sys.exit(0)

# Clear immediately so we don't show same instructions twice
INBOX.write_text("[]", encoding="utf-8")

print("\n" + "="*60)
print(f"[INBOX] {len(items)} instruction(s) from peer machine:")
print("="*60)
for item in items:
    sender = item.get("from_machine", "peer")
    instr  = item.get("instruction", "")
    print(f"\nFROM {sender.upper()}:\n{instr}")
print("="*60 + "\n")
