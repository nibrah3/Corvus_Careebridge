"""
machine_report.py — Run on any machine to report its full state.
Output is sent to VPS machine_comms table as a HANDSHAKE_RESPONSE.
Usage: python3 scripts/machine_report.py
"""
import json, os, socket, subprocess, sys
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
os.chdir(str(CB_DIR))

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

report = {
    "machine_role": os.environ.get("CB_SYNC_ROLE", "unknown"),
    "cb_dir": str(CB_DIR),
    "python_exe": sys.executable,
    "username": os.environ.get("USERNAME", "unknown"),
    "hostname": socket.gethostname(),
}

# Python packages
try:
    r = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                       capture_output=True, text=True)
    report["pip_packages"] = sorted(r.stdout.splitlines())
    report["pip_count"] = len(report["pip_packages"])
except Exception as e:
    report["pip_error"] = str(e)

# MCP ports
MCP_PORTS = {
    "humanizer": 8701, "capture": 8702, "uia": 8703, "browser": 8704,
    "gemini": 8705, "telegram": 8706, "answer": 8707, "sqlite": 8708,
    "memory": 8709, "dom": 8710, "cdp": 8712, "vps": 8713,
    "schools": 8714, "ixbrowser": 8715,
}
mcp_status = {}
for name, port in MCP_PORTS.items():
    s = socket.socket(); s.settimeout(0.3)
    mcp_status[name] = "UP" if s.connect_ex(("127.0.0.1", port)) == 0 else "DOWN"
    s.close()
report["mcp_ports"] = mcp_status

# VPS tunnel ports
VPS_PORTS = {"redis": 6380, "postgres": 5433, "crawlee": 3101, "firecrawl": 7788}
tunnel_status = {}
for name, port in VPS_PORTS.items():
    s = socket.socket(); s.settimeout(0.3)
    tunnel_status[name] = "UP" if s.connect_ex(("127.0.0.1", port)) == 0 else "DOWN"
    s.close()
report["tunnel_ports"] = tunnel_status

# Startup VBS entries
startup = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
report["startup_entries"] = [f.name for f in startup.glob("*.vbs")] if startup.exists() else []

# Claude settings hooks
settings_file = CB_DIR / ".claude" / "settings.json"
if settings_file.exists():
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        hooks = settings.get("hooks", {})
        report["claude_hooks"] = {k: len(v) for k, v in hooks.items()}
        report["claude_hooks_raw"] = settings
    except Exception as e:
        report["claude_hooks_error"] = str(e)
else:
    report["claude_hooks"] = "NO settings.json"

# Running python processes
try:
    r = subprocess.run(
        ["wmic", "process", "where", "name like '%python%'",
         "get", "ProcessId,CommandLine", "/format:csv"],
        capture_output=True, text=True, timeout=10
    )
    lines = [l.strip() for l in r.stdout.splitlines() if "python" in l.lower() and l.strip()]
    report["python_processes"] = lines
except Exception as e:
    report["python_processes_error"] = str(e)

# Git SHA
try:
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(CB_DIR))
    report["git_sha"] = r.stdout.strip()
except Exception:
    report["git_sha"] = "unknown"

# Send to VPS DB
try:
    import psycopg2
    conn = psycopg2.connect(os.environ.get(
        "VPS_PG_DSN",
        "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge"
    ), connect_timeout=10)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO machine_comms (sender, recipient, msg_type, payload) VALUES (%s, %s, %s, %s)",
        (report["machine_role"], "primary", "HANDSHAKE_RESPONSE", json.dumps(report))
    )
    conn.commit()
    conn.close()
    print(f"[machine_report] Report sent to VPS DB from {report['machine_role']} ({report['hostname']})")
except Exception as e:
    print(f"[machine_report] DB send failed: {e}")
    print("[machine_report] Printing report to stdout instead:")
    print(json.dumps(report, indent=2))
