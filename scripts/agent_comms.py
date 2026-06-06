"""
agent_comms.py — Three-tier inter-machine communication with auto-fallback.

Tier 1: Redis pub/sub         (sub-second, requires VPS tunnel on port 6380)
Tier 2: Postgres NOTIFY       (sub-second, requires VPS tunnel on port 5433)
Tier 3: Telegram Bot API      (always reachable, human-visible, cloud)

Each machine runs agent_listener.py which calls listen() here.
Messages arrive on whichever tier is available; deduped by ID.

Usage:
    from scripts.agent_comms import send, listen, who_is_online

    send("secondary", "Fix your hook paths")
    for msg in listen():
        process(msg)
"""
from __future__ import annotations
import json, logging, os, queue, select, threading, time, urllib.request, uuid
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
log    = logging.getLogger("comms")

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

ROLE         = os.environ.get("CB_SYNC_ROLE", "primary")
CHANNEL      = "cb_agents"
REDIS_HOST   = "127.0.0.1"
REDIS_PORT   = int(os.environ.get("VPS_REDIS_PORT", 6380))
DSN          = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
TG_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT      = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
HEARTBEAT_TTL = 90   # Redis key TTL seconds
HEARTBEAT_INT = 30   # write heartbeat every N seconds


# ── Availability checks ───────────────────────────────────────────────────────

def _redis_client():
    import redis
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT,
                       decode_responses=True, socket_connect_timeout=2)

def _redis_up() -> bool:
    try:
        _redis_client().ping(); return True
    except Exception:
        return False

def _pg_conn():
    import psycopg2
    return psycopg2.connect(DSN, connect_timeout=4)

def _pg_up() -> bool:
    try:
        c = _pg_conn(); c.close(); return True
    except Exception:
        return False


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def write_heartbeat() -> None:
    """Write Redis key cb:heartbeat:{role} with TTL. VPS MCP reads these."""
    try:
        r = _redis_client()
        r.setex(f"cb:heartbeat:{ROLE}", HEARTBEAT_TTL, "1")
    except Exception as e:
        log.debug("Heartbeat write failed: %s", e)


def who_is_online() -> list[str]:
    """Return list of machine roles currently online (have live heartbeat)."""
    try:
        r = _redis_client()
        online = []
        for role in ("primary", "secondary"):
            if r.exists(f"cb:heartbeat:{role}"):
                online.append(role)
        return online
    except Exception:
        return []


# ── Send ──────────────────────────────────────────────────────────────────────

def send(to: str, msg: str, msg_type: str = "instruction") -> str:
    """
    Send a message to another machine. Auto-selects best available tier.
    Returns the tier used: 'redis' | 'postgres' | 'telegram'.
    """
    payload = {
        "id":   str(uuid.uuid4())[:8],
        "from": ROLE,
        "to":   to,
        "type": msg_type,
        "msg":  msg,
        "ts":   int(time.time()),
    }

    # Tier 1 — Redis pub/sub
    if _redis_up():
        try:
            r = _redis_client()
            r.publish(CHANNEL, json.dumps(payload))
            log.info("[SEND:redis] %s -> %s: %s", ROLE, to, msg[:80])
            return "redis"
        except Exception as e:
            log.warning("Redis send failed, trying Postgres: %s", e)

    # Tier 2 — Postgres NOTIFY
    if _pg_up():
        try:
            import psycopg2
            conn = _pg_conn()
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT pg_notify(%s, %s)", (CHANNEL, json.dumps(payload)))
            conn.close()
            log.info("[SEND:postgres] %s -> %s: %s", ROLE, to, msg[:80])
            return "postgres"
        except Exception as e:
            log.warning("Postgres NOTIFY failed, falling back to Telegram: %s", e)

    # Tier 3 — Telegram (always reachable, human-visible)
    _telegram_send(
        f"[M2M {ROLE.upper()}→{to.upper()}]\n{msg[:3000]}\n\n"
        f"_(VPS tunnel down — relay this to {to} machine if needed)_"
    )
    log.info("[SEND:telegram] %s -> %s via Telegram", ROLE, to)
    return "telegram"


def _telegram_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        body = json.dumps({"chat_id": int(TG_CHAT), "text": text}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=body, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error("Telegram send failed: %s", e)


# ── Listen ────────────────────────────────────────────────────────────────────

def listen(on_message):
    """
    Subscribe to all available tiers simultaneously.
    Calls on_message(payload_dict) when a message addressed to this machine arrives.
    Deduplicates by message ID.
    Blocks forever — run in a thread.
    """
    seen   = {}             # msg_id -> timestamp, dedup within 120s
    inbox  = queue.Queue()

    def _dispatch(raw: str) -> None:
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if payload.get("to") not in (ROLE, "broadcast"):
            return
        if payload.get("from") == ROLE:
            return
        msg_id = payload.get("id", raw[:16])
        now = time.time()
        if msg_id in seen and now - seen[msg_id] < 120:
            return  # duplicate within 2 minutes, drop
        seen[msg_id] = now
        # Evict old entries
        expired = [k for k, t in seen.items() if now - t > 120]
        for k in expired: del seen[k]
        inbox.put(payload)

    def _redis_thread():
        while True:
            try:
                r      = _redis_client()
                pubsub = r.pubsub()
                pubsub.subscribe(CHANNEL)
                log.info("[LISTEN] Redis subscriber active")
                for raw in pubsub.listen():
                    if raw["type"] == "message":
                        _dispatch(raw["data"])
            except Exception as e:
                log.warning("[LISTEN] Redis dropped, retrying in 10s: %s", e)
                time.sleep(10)

    def _pg_thread():
        import psycopg2
        while True:
            try:
                conn = _pg_conn()
                conn.autocommit = True
                cur  = conn.cursor()
                cur.execute(f"LISTEN {CHANNEL}")
                log.info("[LISTEN] Postgres LISTEN active")
                while True:
                    if select.select([conn], [], [], 5)[0]:
                        conn.poll()
                        while conn.notifies:
                            n = conn.notifies.pop()
                            _dispatch(n.payload)
            except Exception as e:
                log.warning("[LISTEN] Postgres dropped, retrying in 15s: %s", e)
                time.sleep(15)

    threading.Thread(target=_redis_thread, daemon=True).start()
    threading.Thread(target=_pg_thread,   daemon=True).start()

    log.info("[LISTEN] All tiers active — role=%s", ROLE)
    while True:
        try:
            payload = inbox.get(timeout=1)
            on_message(payload)
        except queue.Empty:
            pass
