"""
setup_secondary.py — Run ONCE on secondary machine to reach full parity setup.
Wires the inbox hook, fixes settings.json paths, creates missing startup VBS,
starts state_daemon in background.

Run from D:\\cb-core:
    python scripts/setup_secondary.py
"""
import json, os, subprocess, sys, time
from pathlib import Path

CB_DIR  = Path(__file__).resolve().parent.parent
APPDATA = Path(os.environ.get("APPDATA", ""))
STARTUP = APPDATA / "Microsoft/Windows/Start Menu/Programs/Startup"
PYTHON  = Path(sys.executable)
PYW     = PYTHON.parent / "pythonw.exe"
SETTINGS = CB_DIR / ".claude" / "settings.json"

print(f"[setup_secondary] CB_DIR   = {CB_DIR}")
print(f"[setup_secondary] python   = {PYTHON}")
print(f"[setup_secondary] pythonw  = {PYW}")

# ── 1. Fix settings.json hook paths + add inbox hook ─────────────────────────
print("\n[1/4] Patching .claude/settings.json...")
if SETTINGS.exists():
    raw  = SETTINGS.read_text(encoding="utf-8")
    data = json.loads(raw)

    # Repath: replace any path that isn't CB_DIR
    fixed_raw = raw.replace("E:\\\\Corvus_Careebridge", str(CB_DIR).replace("\\","\\\\")) \
                   .replace("E:/Corvus_Careebridge",    str(CB_DIR).replace("\\","/")) \
                   .replace("E:\\Corvus_Careebridge",   str(CB_DIR))
    data = json.loads(fixed_raw)

    # Inject inbox hook as first PreToolUse entry if not already present
    inbox_cmd = f"{PYTHON} {CB_DIR}\\hooks\\hook_inbox.py"
    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    already = any(
        any(h.get("command","").endswith("hook_inbox.py") for h in e.get("hooks",[]))
        for e in pre
    )
    if not already:
        pre.insert(0, {
            "matcher": "*",
            "hooks": [{"type": "command", "command": inbox_cmd}]
        })
        print(f"   Added inbox hook: {inbox_cmd}")
    else:
        print("   Inbox hook already present")

    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("   settings.json saved")
else:
    # Create minimal settings.json
    inbox_cmd = f"{PYTHON} {CB_DIR}\\hooks\\hook_inbox.py"
    data = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": inbox_cmd}]}]}}
    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"   Created new settings.json with inbox hook")

# ── 2. Create missing startup VBS ────────────────────────────────────────────
print("\n[2/4] Creating startup VBS entries...")
STARTUP.mkdir(parents=True, exist_ok=True)

vbs = {
    "cb_mcps.vbs": f'Set oShell = CreateObject("WScript.Shell")\noShell.Run """{PYW}"" ""{CB_DIR}\\scripts\\start_mcps_silent.py""", 0, False\n',
    "cb_tunnel.vbs": f'WScript.Sleep 5000\nSet oShell = CreateObject("WScript.Shell")\noShell.Run """{PYW}"" ""{CB_DIR}\\scripts\\vps_tunnel.py""", 0, False\n',
    "cb_sync.vbs": f'WScript.Sleep 12000\nSet oShell = CreateObject("WScript.Shell")\noShell.Run """{PYW}"" ""{CB_DIR}\\scripts\\sync_daemon.py""", 0, False\n',
}
for fname, content in vbs.items():
    path = STARTUP / fname
    if path.exists():
        print(f"   {fname}: already exists")
    else:
        path.write_text(content, encoding="utf-8")
        print(f"   {fname}: created")

# ── 3. Start state_daemon in background ──────────────────────────────────────
print("\n[3/4] Starting state_daemon in background...")
existing = subprocess.run(
    ["wmic","process","where","CommandLine like '%state_daemon.py%'","get","ProcessId","/value"],
    capture_output=True, text=True
)
running = [l for l in existing.stdout.splitlines() if l.startswith("ProcessId=") and l.strip() != "ProcessId="]
if running:
    print(f"   state_daemon already running: {running}")
else:
    subprocess.Popen(
        [str(PYW), str(CB_DIR / "scripts" / "state_daemon.py")],
        cwd=str(CB_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print("   state_daemon started (pythonw, no window)")
    time.sleep(3)

# ── 4. Write initial state to DB ─────────────────────────────────────────────
print("\n[4/4] Writing initial state to VPS DB...")
r = subprocess.run(
    [sys.executable, str(CB_DIR / "scripts" / "machine_report.py")],
    cwd=str(CB_DIR), capture_output=True, text=True
)
print(r.stdout.strip() or "   Done (no output)")
if r.returncode != 0:
    print(f"   Warning: {r.stderr.strip()[:200]}")

print("\n[setup_secondary] Complete.")
print("   Next: restart Claude Code from D:\\cb-core for hook to take effect.")
print("   Primary's parity_engine will detect remaining gaps and send fix instructions.")
print("   Those instructions will appear in your session before each tool use.")
