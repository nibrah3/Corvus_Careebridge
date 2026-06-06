"""
fix_secondary.py — Run on secondary machine (D:\\cb-core) to reach parity with primary.
Sent by primary via machine_comms. Run as:
    cd D:\\cb-core && python scripts/fix_secondary.py

Fixes applied:
  1. .claude/settings.json  — correct hook paths (E:\\Corvus_Careebridge -> D:\\cb-core)
  2. Startup VBS            — add cb_mcps.vbs + cb_tunnel.vbs for reboot persistence
  3. pip packages           — install 160 packages missing vs primary
  4. Report back            — sends FIX_COMPLETE to primary via VPS DB
"""
import json, os, subprocess, sys, time
from pathlib import Path

CB_DIR   = Path(__file__).resolve().parent.parent   # D:\cb-core
APPDATA  = Path(os.environ.get("APPDATA", ""))
STARTUP  = APPDATA / "Microsoft/Windows/Start Menu/Programs/Startup"
PYTHON   = Path(sys.executable)
PYW      = PYTHON.parent / "pythonw.exe"
SETTINGS = CB_DIR / ".claude" / "settings.json"

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

DSN  = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
ROLE = os.environ.get("CB_SYNC_ROLE", "secondary")

results = {}

# ── 1. Fix .claude/settings.json ─────────────────────────────────────────────
print("[1/4] Fixing .claude/settings.json hook paths...")
try:
    raw = SETTINGS.read_text(encoding="utf-8")
    fixed = raw.replace(
        "E:\\\\Corvus_Careebridge", str(CB_DIR).replace("\\", "\\\\")
    ).replace(
        "E:/Corvus_Careebridge", str(CB_DIR).replace("\\", "/")
    ).replace(
        "E:\\Corvus_Careebridge", str(CB_DIR)
    )
    SETTINGS.write_text(fixed, encoding="utf-8")
    # Verify
    data = json.loads(fixed)
    hooks = data.get("hooks", {})
    hook_count = sum(len(v) for v in hooks.values())
    results["settings_json"] = f"OK — {hook_count} hook groups, paths corrected to {CB_DIR}"
    print(f"   OK: {hook_count} hook groups updated")
except Exception as e:
    results["settings_json"] = f"FAILED: {e}"
    print(f"   FAILED: {e}")

# ── 2. Add startup VBS entries ────────────────────────────────────────────────
print("[2/4] Adding startup VBS entries...")

vbs_files = {
    "cb_mcps.vbs": f'Set oShell = CreateObject("WScript.Shell")\noShell.Run """{PYW}"" ""{CB_DIR}\\scripts\\start_mcps_silent.py""", 0, False\n',
    "cb_tunnel.vbs": f'WScript.Sleep 5000\nSet oShell = CreateObject("WScript.Shell")\noShell.Run """{PYW}"" ""{CB_DIR}\\scripts\\vps_tunnel.py""", 0, False\n',
}

startup_results = {}
for fname, content in vbs_files.items():
    target = STARTUP / fname
    if target.exists():
        startup_results[fname] = "already exists"
        print(f"   {fname}: already exists")
    else:
        target.write_text(content, encoding="utf-8")
        startup_results[fname] = "created"
        print(f"   {fname}: created")

results["startup_vbs"] = startup_results

# ── 3. Install missing pip packages ──────────────────────────────────────────
print("[3/4] Installing missing pip packages (160 packages)...")

missing_packages = [
    "Authlib","Flask","ImageHash","Protego","PyPDF2","PyWavelets","SQLAlchemy",
    "aider-install","aiofile","aiolimiter","aiosqlite","aistudio-sdk",
    "apify_fingerprint_datapoints","argcomplete","asyncpg","banks","beartype",
    "blinker","brotli","browserforge","cachetools","caio","camoufox","capsolver",
    "chardet","colorlog","contourpy","crc32c","cssselect2","cycler","cyclopts",
    "dataclasses-json","deprecation","dirtyjson","dnspython","edge-tts",
    "email-validator","exceptiongroup","fastmcp","fastmcp-slim","filetype",
    "fonttools","future","griffe","h2","hpack","html5lib","httplib2","hyperframe",
    "imagesize","iniconfig","itsdangerous","joblib","joserfc","jsonpatch",
    "jsonpointer","jsonref","jsonschema-path","keyring","kiwisolver","langchain-core",
    "langchain-text-splitters","langsmith","llama-cloud","llama-index-core",
    "llama-index-instrumentation","llama-index-workflows","llama-parse","marshmallow",
    "matplotlib","mcp-server-fetch","mcp-server-sqlite","mmh3","more-itertools",
    "mpmath","mypy_extensions","nest-asyncio","networkx","nltk","noise",
    "openapi-pydantic","opencv-contrib-python","opentelemetry-api","orjson","oxymouse",
    "paddleocr","pandas","patchright","pathable","pdfminer.six","pdfplumber","pipx",
    "platformdirs","pluggy","postgrest","prettytable","py-cpuinfo","pyHM",
    "pyTelegramBotAPI","pycryptodome","pydirectinput_rgx","pydyf","pypdfium2",
    "pyphen","pytest","python-bidi","pytz","pywin32-ctypes","readabilipy","realtime",
    "requests-toolbelt","rich-rst","ruamel.yaml","scipy","setuptools","sortedcontainers",
    "sounddevice","soundfile","storage3","strictyaml","supabase","sympy","tabulate",
    "tinycss2","tinyhtml5","typing-inspect","ua-parser","ujson","userpath","uv",
    "voyageai","watchfiles","weasyprint","webencodings","zopfli","zstandard",
]

# Install in batches of 20 to avoid command-line length limits
batch_size = 20
install_ok, install_fail = [], []
for i in range(0, len(missing_packages), batch_size):
    batch = missing_packages[i:i+batch_size]
    print(f"   Installing batch {i//batch_size + 1}: {batch[:3]}...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet"] + batch,
        capture_output=True, text=True, timeout=300
    )
    if r.returncode == 0:
        install_ok.extend(batch)
    else:
        # Try one by one to isolate failures
        for pkg in batch:
            r2 = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", pkg],
                capture_output=True, text=True, timeout=120
            )
            (install_ok if r2.returncode == 0 else install_fail).append(pkg)

results["pip_install"] = {
    "installed": len(install_ok),
    "failed": len(install_fail),
    "failed_packages": install_fail
}
print(f"   Installed: {len(install_ok)} | Failed: {len(install_fail)}")
if install_fail:
    print(f"   Failed: {install_fail}")

# ── 4. Report back to primary ─────────────────────────────────────────────────
print("[4/4] Reporting back to primary via VPS DB...")
try:
    import psycopg2
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur  = conn.cursor()

    # Final MCP + tunnel check
    import socket
    MCP_PORTS = {"humanizer":8701,"capture":8702,"uia":8703,"browser":8704,
                 "gemini":8705,"telegram":8706,"answer":8707,"sqlite":8708,
                 "memory":8709,"dom":8710,"cdp":8712,"vps":8713,"schools":8714,"ixbrowser":8715}
    mcp_status = {}
    for name, port in MCP_PORTS.items():
        s = socket.socket(); s.settimeout(0.3)
        mcp_status[name] = "UP" if s.connect_ex(("127.0.0.1", port)) == 0 else "DOWN"
        s.close()

    payload = {
        "subject": "FIX_COMPLETE",
        "results": results,
        "mcp_final": mcp_status,
        "startup_entries": [f.name for f in STARTUP.glob("*.vbs")],
        "machine": str(CB_DIR),
        "text": f"Fix script complete. settings.json patched, startup VBS added, {len(install_ok)} packages installed, {len(install_fail)} failed."
    }

    cur.execute(
        "INSERT INTO machine_comms (sender, recipient, msg_type, payload) VALUES (%s,%s,%s,%s)",
        (ROLE, "primary", "FIX_COMPLETE", json.dumps(payload, default=str))
    )
    conn.commit(); conn.close()
    print("   Report sent to primary.")
except Exception as e:
    print(f"   DB report failed: {e}")
    print(json.dumps(results, indent=2, default=str))

print("\nDone. Restart Claude Code on this machine for hook changes to take effect.")
