"""
agent_listener.py — Real-time inter-agent communication via VPS Redis pub/sub.

Each machine runs this. Messages arrive via Redis channel 'cb_agents'.
When a message is addressed to this machine's role, it is piped into
claude --print --dangerously-skip-permissions and the response is published back.

Channel message format (JSON):
    {
        "to":   "secondary" | "primary" | "broadcast",
        "from": "primary"   | "secondary",
        "id":   "<uuid>",
        "type": "instruction" | "response" | "status",
        "msg":  "<natural language instruction or response>"
    }

Run:
    pythonw scripts/agent_listener.py     # silent background
    python  scripts/agent_listener.py     # foreground with console output
"""
from __future__ import annotations
import json, logging, os, subprocess, sys, time, uuid
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
LOG    = CB_DIR / "logs" / "agent_listener.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(str(LOG), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("agent")

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

ROLE    = os.environ.get("CB_SYNC_ROLE", "primary")
CHANNEL = "cb_agents"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = int(os.environ.get("VPS_REDIS_PORT", 6380))

import redis as _redis

r = _redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def publish(to: str, msg: str, msg_type: str = "response", reply_to: str | None = None) -> None:
    payload = {
        "to":       to,
        "from":     ROLE,
        "id":       str(uuid.uuid4())[:8],
        "reply_to": reply_to,
        "type":     msg_type,
        "msg":      msg,
    }
    r.publish(CHANNEL, json.dumps(payload))
    log.info(">> [%s -> %s] %s", ROLE, to, msg[:120])


def run_claude(instruction: str) -> str:
    """Pipe instruction into claude --print and return response."""
    log.info("Running claude --print for: %s", instruction[:100])
    try:
        result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", instruction],
            cwd=str(CB_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        out = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            out = f"[exit {result.returncode}] {result.stderr[:500]}\n{out}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT after 300s]"
    except Exception as e:
        return f"[ERROR: {e}]"


def handle(msg: dict) -> None:
    """Process an inbound message addressed to this machine."""
    sender   = msg.get("from", "unknown")
    msg_id   = msg.get("id", "?")
    msg_type = msg.get("type", "instruction")
    text     = msg.get("msg", "")

    log.info("<< [%s -> %s] id=%s type=%s: %s", sender, ROLE, msg_id, msg_type, text[:120])

    if msg_type == "response":
        # Just log it — primary reads responses, doesn't re-execute them
        log.info("RESPONSE from %s: %s", sender, text[:500])
        return

    # It's an instruction — run it through Claude
    response = run_claude(
        f"You are the CareerBridge agent on the {ROLE} machine at {CB_DIR}. "
        f"Another engineer ({sender} machine) sent you this instruction:\n\n"
        f"{text}\n\n"
        f"Execute it. Be concise in your response — report what you did and any issues."
    )

    # Publish response back
    publish(to=sender, msg=response, msg_type="response", reply_to=msg_id)


def listen() -> None:
    log.info("Agent listener starting — role=%s  redis=%s:%d  channel=%s",
             ROLE, REDIS_HOST, REDIS_PORT, CHANNEL)

    pubsub = r.pubsub()
    pubsub.subscribe(CHANNEL)

    # Announce presence
    publish(
        to="broadcast",
        msg=f"{ROLE} agent online at {CB_DIR}. Ready.",
        msg_type="status",
    )

    for raw in pubsub.listen():
        if raw["type"] != "message":
            continue
        try:
            msg = json.loads(raw["data"])
        except json.JSONDecodeError:
            continue

        recipient = msg.get("to", "")
        sender    = msg.get("from", "")

        # Skip our own messages
        if sender == ROLE:
            continue

        # Process if addressed to us or broadcast
        if recipient in (ROLE, "broadcast"):
            handle(msg)


if __name__ == "__main__":
    listen()
