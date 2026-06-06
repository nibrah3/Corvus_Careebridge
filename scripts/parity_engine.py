"""
parity_engine.py — Run on primary. Reads both machines' state from VPS DB,
computes exact diff, writes targeted fix instructions to pending_instructions
for secondary. Stops when diff is empty.

Run:
    python scripts/parity_engine.py          # one diff pass
    python scripts/parity_engine.py --watch  # repeat every 120s until identical
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

DSN = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")

import psycopg2, psycopg2.extras


def get_states() -> dict[str, dict]:
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM machine_state ORDER BY machine")
    rows = {r["machine"]: dict(r) for r in cur.fetchall()}
    conn.close()
    return rows


def send_instruction(to: str, instruction: str) -> int:
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur  = conn.cursor()
    cur.execute(
        "INSERT INTO pending_instructions (to_machine, from_machine, instruction) VALUES (%s,%s,%s) RETURNING id",
        (to, "primary", instruction)
    )
    row_id = cur.fetchone()[0]
    conn.commit(); conn.close()
    return row_id


def diff_and_instruct(primary: dict, secondary: dict) -> list[str]:
    """
    Compare primary vs secondary state. Return list of instructions sent
    to secondary. Empty list = machines are in parity.
    """
    instructions = []

    # 1. Git SHA
    if primary["git_sha"] != secondary["git_sha"]:
        instructions.append(
            f"Your git SHA ({secondary['git_sha'][:8]}) differs from primary ({primary['git_sha'][:8]}). "
            f"Run: git pull origin master"
        )

    # 2. Hook paths
    if not secondary["hook_paths_ok"]:
        sec_dir = secondary.get("cb_dir", "D:\\cb-core")
        instructions.append(
            f"Your .claude/settings.json hook paths are wrong — they don't point to {sec_dir}. "
            f"Read your .claude/settings.json, replace every occurrence of E:\\Corvus_Careebridge "
            f"with {sec_dir}, save the file, then restart Claude Code for hooks to take effect."
        )

    # 3. Startup VBS
    primary_vbs   = set(primary.get("startup_vbs") or [])
    secondary_vbs = set(secondary.get("startup_vbs") or [])
    missing_vbs   = primary_vbs - secondary_vbs
    if missing_vbs:
        sec_dir = secondary.get("cb_dir", "D:\\cb-core")
        sec_pyw = str(Path(secondary.get("python_exe", "C:\\Python314\\python.exe")).parent / "pythonw.exe")
        for vbs in missing_vbs:
            if vbs == "cb_mcps.vbs":
                content = f'Set oShell = CreateObject("WScript.Shell")\\noShell.Run """{sec_pyw}"" ""{sec_dir}\\scripts\\start_mcps_silent.py""", 0, False'
            elif vbs == "cb_tunnel.vbs":
                content = f'WScript.Sleep 5000\\nSet oShell = CreateObject("WScript.Shell")\\noShell.Run """{sec_pyw}"" ""{sec_dir}\\scripts\\vps_tunnel.py""", 0, False'
            elif vbs == "cb_sync.vbs":
                content = f'WScript.Sleep 12000\\nSet oShell = CreateObject("WScript.Shell")\\noShell.Run """{sec_pyw}"" ""{sec_dir}\\scripts\\sync_daemon.py""", 0, False'
            else:
                continue
            instructions.append(
                f"Create startup file: %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{vbs}\n"
                f"Content (replace \\n with real newlines):\n{content}"
            )

    # 4. MCP ports
    primary_mcp   = primary.get("mcp_ports") or {}
    secondary_mcp = secondary.get("mcp_ports") or {}
    down_on_sec   = [k for k, v in secondary_mcp.items() if not v and primary_mcp.get(k)]
    if down_on_sec:
        sec_dir = secondary.get("cb_dir", "D:\\cb-core")
        sec_pyw = str(Path(secondary.get("python_exe", "C:\\Python314\\python.exe")).parent / "pythonw.exe")
        mcp_port_map = {"humanizer":8701,"capture":8702,"uia":8703,"browser":8704,
                        "gemini":8705,"telegram":8706,"answer":8707,"sqlite":8708,
                        "memory":8709,"dom":8710,"cdp":8712,"vps":8713,"schools":8714,"ixbrowser":8715}
        for svc in down_on_sec:
            port = mcp_port_map.get(svc, "?")
            instructions.append(
                f"MCP server '{svc}' is DOWN (port {port}). Start it: "
                f"Start-Process '{sec_pyw}' -ArgumentList '-m {svc}_mcp.server --http {port}' "
                f"-WorkingDirectory '{sec_dir}' -WindowStyle Hidden"
            )

    # 5. Pip count gap
    pip_gap = (primary.get("pip_count") or 0) - (secondary.get("pip_count") or 0)
    if pip_gap > 10:
        instructions.append(
            f"You are missing ~{pip_gap} pip packages vs primary. "
            f"Run: python scripts/fix_secondary.py — it will install them automatically."
        )

    # 6. Tunnel ports
    primary_tun   = primary.get("tunnel_ports") or {}
    secondary_tun = secondary.get("tunnel_ports") or {}
    down_tun      = [k for k, v in secondary_tun.items() if not v and primary_tun.get(k)]
    if down_tun:
        sec_dir = secondary.get("cb_dir", "D:\\cb-core")
        sec_pyw = str(Path(secondary.get("python_exe", "C:\\Python314\\python.exe")).parent / "pythonw.exe")
        instructions.append(
            f"VPS tunnel ports DOWN: {down_tun}. Start tunnel: "
            f"Start-Process '{sec_pyw}' -ArgumentList '{sec_dir}\\scripts\\vps_tunnel.py' -WindowStyle Hidden"
        )

    return instructions


def run_once() -> bool:
    """Returns True if machines are in parity."""
    states = get_states()
    primary   = states.get("primary")
    secondary = states.get("secondary")

    if not primary:
        print("ERROR: primary has not written state yet. Run state_daemon.py on primary first.")
        return False
    if not secondary:
        print("ERROR: secondary has not written state yet. Run state_daemon.py on secondary first.")
        return False

    age_sec = int(time.time() - secondary["updated_at"].timestamp()) if secondary.get("updated_at") else 999
    print(f"\n{'='*60}")
    print(f"PARITY CHECK — {time.strftime('%H:%M:%S')}")
    print(f"  Primary:   sha={primary['git_sha'][:8]}  pip={primary['pip_count']}  hooks_ok={primary['hook_paths_ok']}")
    print(f"  Secondary: sha={secondary['git_sha'][:8]}  pip={secondary['pip_count']}  hooks_ok={secondary['hook_paths_ok']}  (state {age_sec}s old)")
    print(f"{'='*60}")

    instructions = diff_and_instruct(primary, secondary)

    if not instructions:
        print("PARITY ACHIEVED — both machines are identical.")
        return True

    print(f"GAPS FOUND: {len(instructions)} item(s). Sending to secondary inbox...")
    for i, instr in enumerate(instructions, 1):
        msg_id = send_instruction("secondary", instr)
        print(f"  [{i}] sent (id={msg_id}): {instr[:80]}...")

    return False


def main():
    watch = "--watch" in sys.argv
    while True:
        done = run_once()
        if done or not watch:
            break
        print(f"\nNext check in 120s...")
        time.sleep(120)


if __name__ == "__main__":
    main()
