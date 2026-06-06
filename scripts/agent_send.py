"""
agent_send.py — Send a real-time instruction to the other machine's agent.

Usage:
    python scripts/agent_send.py secondary "Fix your settings.json hook paths"
    python scripts/agent_send.py primary   "What MCP servers are currently down?"
    python scripts/agent_send.py broadcast "Both machines: run git pull origin master"

The other machine's agent_listener.py picks it up instantly via Redis pub/sub,
runs it through claude --print, and publishes the response back.
This script waits up to 5 minutes for the response and prints it.
"""
import json, os, sys, time, uuid
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

ROLE       = os.environ.get("CB_SYNC_ROLE", "primary")
CHANNEL    = "cb_agents"
REDIS_PORT = int(os.environ.get("VPS_REDIS_PORT", 6380))

import redis as _redis
r = _redis.Redis(host="127.0.0.1", port=REDIS_PORT, decode_responses=True)

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

to_role  = sys.argv[1]
message  = " ".join(sys.argv[2:])
msg_id   = str(uuid.uuid4())[:8]

payload = {
    "to": to_role, "from": ROLE,
    "id": msg_id, "type": "instruction", "msg": message,
}
r.publish(CHANNEL, json.dumps(payload))
print(f"[{ROLE} -> {to_role}] Sent (id={msg_id}): {message[:80]}")

if to_role == "broadcast":
    sys.exit(0)

# Wait for response
print(f"Waiting for {to_role} response...")
pubsub = r.pubsub()
pubsub.subscribe(CHANNEL)
deadline = time.time() + 300

for raw in pubsub.listen():
    if time.time() > deadline:
        print("[TIMEOUT] No response from other agent within 5 minutes.")
        break
    if raw["type"] != "message":
        continue
    try:
        msg = json.loads(raw["data"])
    except json.JSONDecodeError:
        continue
    if msg.get("to") == ROLE and msg.get("reply_to") == msg_id:
        print(f"\n[{msg['from']} -> {ROLE}] Response:\n{'='*60}")
        print(msg["msg"])
        print("="*60)
        break
