#!/usr/bin/env python3
"""
hook_menu_router.py — UserPromptSubmit hook.
Detects greetings and open-ended messages, injects the Main Menu.
Three options only: Apply | Queue | System.
"""
import io, json, re, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MENU = """\
[SYSTEM - MENU REQUIRED]
The user sent a greeting or open-ended message.
Your ONLY response must be to call AskUserQuestion immediately with this menu.
Do not write any text before the AskUserQuestion call.

question: "Welcome back. What do you want to do?"
header: "CareerBridge"
options:
  - label: "Apply"   description: "Pick a job or school from your ready queue and apply"
  - label: "Queue"   description: "See discovered jobs, applied history, school results"
  - label: "System"  description: "Health check, browser setup, profiles, discovery settings"
"""

TRIGGERS = [
    r"^$",
    r"^(hi|hello|hey|yo|sup|howdy|greetings|hiya)\b",
    r"^good\s+(morning|afternoon|evening|night|day)\b",
    r"^(menu|main menu|home|help|start|back|return|go back|restart)\b",
    r"^(what can you do|what do you do|options|show options|show menu)\b",
]

def main():
    try:
        raw  = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").strip()
        data = json.loads(raw) if raw else {}
        prompt = data.get("prompt", "")
    except Exception:
        prompt = ""

    p = prompt.strip().lower()
    if any(re.match(t, p) for t in TRIGGERS):
        print(MENU)

    sys.exit(0)

if __name__ == "__main__":
    main()
