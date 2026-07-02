"""
Silent MCP launcher — replaces start_mcps.ps1 for startup use.
Uses pythonw.exe for every MCP server (zero console window, ever).
Run via: pythonw.exe start_mcps_silent.py
"""
import os, subprocess, socket, time, sys
from pathlib import Path

CB   = Path(__file__).resolve().parent.parent
PYW  = Path(sys.executable).parent / "pythonw.exe"

# Servers that live outside Corvus_Careebridge get their own cwd
SERVER_CWD_OVERRIDES = {
    "tutor_mcp.server": Path("E:/annotation-tutor"),
}

SERVERS = [
    ("humanizer_mcp.server",  8701),
    ("capture_mcp.server",    8702),
    ("uia_mcp.server",        8703),
    ("browser_mcp.server",    8704),
    ("gemini_mcp.server",     8705),
    ("telegram_mcp.server",   8706),
    ("answer_mcp.server",     8707),
    ("sqlite_mcp.server",     8708),
    ("memory_mcp.server",     8709),
    ("dom_mcp.server",        8710),
    ("cdp_mcp.server",        8712),
    ("vps_mcp.server",        8713),
    ("schools_mcp.server",    8714),
    ("ixbrowser_mcp.server",  8715),
    ("tutor_mcp.server",      8716),
]

# Load .env
env_file = CB / ".env"
env = os.environ.copy()
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip()
            if k and k not in env:
                env[k] = v


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


for mod, port in SERVERS:
    if port_open(port):
        continue   # already running, skip silently
    subprocess.Popen(
        [str(PYW), "-m", mod, "--http", str(port)],
        cwd=str(SERVER_CWD_OVERRIDES.get(mod, CB)),
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    time.sleep(0.4)
