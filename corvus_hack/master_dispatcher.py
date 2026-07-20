"""
master_dispatcher.py — Central coordination server for CorvusClient sessions.

Runs on Mike's Windows session, port 9200.
Workers on CorvusClient accounts call in via localhost TCP.
Mike manages sessions via Telegram admin commands.
Clients receive guidance via Telegram bot.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).parent.parent   # E:\Corvus_Careebridge
sys.path.insert(0, str(ROOT))

from flask import Flask, request, jsonify

from claude_cli_lock import claude_cli_lock

# ── Env / config ──────────────────────────────────────────────────────────────

def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [master] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("master")

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CLAUDE_BIN   = r"C:\Users\Mike\AppData\Roaming\npm\claude.cmd"
CLAUDE_TOOLS = "Read,Bash,Glob,Grep"   # minimal — guidance only
CLAUDE_TIMEOUT = 90
# CLAUDE_TOOLS uses no MCP tools, but without --mcp-config the CLI still
# connects every server in the global config before answering — measured
# ~40s of pure startup overhead in telegram_claude_bridge.py's identical
# case (53.7s -> 16.8s once scoped). --strict-mcp-config makes --mcp-config
# fully replace rather than merge with globals.
CLAUDE_MCP_CONFIG_EMPTY = str(ROOT / "empty_mcp.json")
# Neutral working dir for Claude subprocesses — keeps CLAUDE.md project discovery
# out of the repo root, matching the telegram bridge's subprocess hygiene.
CLAUDE_CWD   = r"C:\tmp"


def _claude_env() -> dict:
    """Environment for Claude subprocesses.

    Removing ANTHROPIC_API_KEY forces subscription/OAuth auth so guidance calls are
    NOT billed against an API key (project auth standard). CLAUDE_CODE_SIMPLE must
    NOT be set: it is equivalent to --bare, which forces strict API-key-only auth
    and never reads OAuth/keychain credentials — combined with the API key removal
    above, that guarantees "Could not resolve authentication method" on every call.
    Mirrors telegram_claude_bridge.py.
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    return env

app = Flask(__name__)

# ── In-memory session state ────────────────────────────────────────────────────
# {session_id: {chat_id, platform, account, status, history, start_time, last_screen}}
_sessions: dict[str, dict] = {}
_lock = threading.Lock()

# Accounts waiting for their worker to call /worker/register
# {account_name: session_id}
_pending_accounts: dict[str, str] = {}
_pending_lock = threading.Lock()

# Limit concurrent Claude subprocesses — each can take 30–90s, events arrive every 2s
_claude_sem = threading.Semaphore(3)

# ── Telegram helper ───────────────────────────────────────────────────────────

def _telegram_send(chat_id: int, text: str) -> None:
    import requests as req
    try:
        req.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.error("Telegram send failed: %s", e)

# ── Claude helper ─────────────────────────────────────────────────────────────

def _call_claude(prompt_text: str) -> str:
    # NOTE: pass the prompt TEXT as a positional arg. The old code passed "-p <path>",
    # but -p is an alias for --print, so the temp-file *path* became the prompt and
    # Claude never saw the actual question. Prompts here are small (screen text is
    # capped upstream), so a positional arg is safe and avoids the temp file entirely.
    try:
        with claude_cli_lock():
            result = subprocess.run(
                [
                    CLAUDE_BIN, "--print",
                    "--output-format", "json",
                    "--allowedTools", CLAUDE_TOOLS,
                    "--mcp-config", CLAUDE_MCP_CONFIG_EMPTY,
                    "--strict-mcp-config",
                    prompt_text,
                ],
                capture_output=True, text=True, encoding="utf-8",
                timeout=CLAUDE_TIMEOUT, cwd=CLAUDE_CWD, env=_claude_env(),
            )
        raw = (result.stdout or "").strip()
        if not raw:
            err = (result.stderr or "").strip()
            return f"(Claude error: {err[:200]})" if err else "(no output)"
        try:
            data = json.loads(raw)
            return (data.get("result") or data.get("text") or raw).strip()
        except json.JSONDecodeError:
            return raw
    except subprocess.TimeoutExpired:
        return "(Claude timed out — screen too complex)"
    except Exception as e:
        return f"(Claude error: {e})"

# ── Prompt builder ────────────────────────────────────────────────────────────

def _guidance_prompt(session: dict, screen: str, event_type: str) -> str:
    history = list(session.get("history", []))
    hist_text = ""
    if history:
        hist_text = "Recent context:\n"
        for h in history[-4:]:
            hist_text += f"Screen: {h['screen']}\nYour last guidance: {h['guidance']}\n\n"

    return f"""You are guiding a user through an online assessment in real time via Telegram.
The user navigates and clicks on their own — you read the screen and advise them.

Platform: {session.get('platform', 'online assessment')}
Screen event: {event_type}

{hist_text}Current screen content:
{screen}

Respond with SHORT, DIRECT guidance — this is a Telegram message.
- If there is a question on screen: answer it directly
- If it is a video/audio: say "Watch/listen carefully — I'll advise after"
- If it is instructions or setup: summarize key actions
- If the screen shows navigation/loading: say "Keep going" or "Good, continue"
- If nothing actionable: reply with just the word OK (it will be suppressed)
Do NOT explain your reasoning. Just tell the user what to do."""

def _client_question_prompt(session: dict, question: str) -> str:
    last_screen = session.get("last_screen", "(no recent screen)")
    return f"""A user is in an active online assessment and is asking you a question.

Platform: {session.get('platform', 'online assessment')}
Current screen context: {last_screen[:400]}

User's question: {question}

Answer directly and concisely — this is a Telegram message reply."""

# ── Worker endpoints ──────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    with _lock:
        active = len(_sessions)
    return jsonify({"status": "ok", "active_sessions": active})


@app.route("/worker/register", methods=["POST"])
def worker_register():
    """Worker calls this at login. Returns session_id if one is pending."""
    data = request.json or {}
    account = data.get("account", "")
    with _pending_lock:
        session_id = _pending_accounts.pop(account, None)
    if session_id:
        with _lock:
            if session_id in _sessions:
                _sessions[session_id]["account"] = account
        log.info("Worker %s claimed session %s", account, session_id)
    return jsonify({"session_id": session_id})


@app.route("/worker/heartbeat", methods=["POST"])
def worker_heartbeat():
    data = request.json or {}
    session_id = data.get("session_id", "")
    account = data.get("account", "")
    try:
        from corvus_hack.db import account_heartbeat
        account_heartbeat(account)
    except Exception:
        pass
    with _lock:
        sess = _sessions.get(session_id)
    active = sess is not None and sess.get("status") == "active"
    return jsonify({"active": active})


@app.route("/session/<session_id>/status", methods=["GET"])
def session_status(session_id: str):
    with _lock:
        sess = _sessions.get(session_id)
    if not sess:
        return jsonify({"status": "unknown"})
    return jsonify({"status": sess.get("status", "idle")})


@app.route("/event", methods=["POST"])
def handle_event():
    data = request.json or {}
    session_id  = data.get("session_id", "")
    screen      = data.get("screen", "")
    event_type  = data.get("event_type", "navigation")

    with _lock:
        session = _sessions.get(session_id)

    if not session:
        return jsonify({"error": "unknown session"}), 404
    if session.get("status") != "active":
        return jsonify({"status": "paused"})

    with _lock:
        session["last_screen"] = screen[:500]

    def _respond():
        with _claude_sem:
            prompt   = _guidance_prompt(session, screen, event_type)
            guidance = _call_claude(prompt)
        if guidance.strip().upper() == "OK":
            return
        _telegram_send(session["chat_id"], guidance)
        with _lock:
            if "history" not in session:
                session["history"] = deque(maxlen=10)
            session["history"].append({
                "screen":   screen[:200],
                "guidance": guidance[:200],
            })

    threading.Thread(target=_respond, daemon=True).start()
    return jsonify({"status": "queued"})


@app.route("/video_event", methods=["POST"])
def handle_video():
    data = request.json or {}
    session_id = data.get("session_id", "")
    clip_path  = data.get("clip_path", "")

    with _lock:
        session = _sessions.get(session_id)
    if not session:
        return jsonify({"error": "unknown session"}), 404

    def _analyse():
        import requests as req
        try:
            r = req.post(
                "http://localhost:8705/upload_video",
                json={"video_path": clip_path},
                timeout=120,
            )
            uri = r.json().get("uri")
            if not uri:
                return
            r2 = req.post(
                "http://localhost:8705/analyse_video",
                json={
                    "file_uri": uri,
                    "prompt": (
                        "Describe this video/audio clip completely. "
                        "What does it show, narrate, ask, or require from the viewer?"
                    ),
                },
                timeout=120,
            )
            desc = r2.json().get("text", "")
            if not desc:
                return
            with _claude_sem:
                prompt   = f"Assessment video clip:\n{desc}\n\nWhat should I tell the user to do now? Be direct."
                guidance = _call_claude(prompt)
            _telegram_send(session["chat_id"], f"Video: {guidance}")
        except Exception as e:
            log.error("Video analysis failed: %s", e)

    threading.Thread(target=_analyse, daemon=True).start()
    return jsonify({"status": "queued"})


# ── Client message endpoint (called by Telegram bridge) ───────────────────────

@app.route("/client/message", methods=["POST"])
def client_message():
    """Telegram bridge routes client messages here during active sessions."""
    data    = request.json or {}
    chat_id = data.get("chat_id")
    text    = data.get("text", "")

    try:
        from corvus_hack.db import get_client_session
        sess_row = get_client_session(chat_id)
    except Exception:
        sess_row = None

    if not sess_row:
        _telegram_send(chat_id, "No active session. Ask the admin to start one for you.")
        return jsonify({"status": "no_session"})

    session_id = sess_row["session_id"]
    with _lock:
        session = _sessions.get(session_id)

    if not session:
        _telegram_send(chat_id, "Session is starting up, please wait a moment...")
        return jsonify({"status": "starting"})

    def _answer():
        with _claude_sem:
            prompt   = _client_question_prompt(session, text)
            guidance = _call_claude(prompt)
        _telegram_send(chat_id, guidance)

    threading.Thread(target=_answer, daemon=True).start()
    return jsonify({"status": "ok"})


# ── Admin endpoints (called by Telegram bridge for Mike's commands) ────────────

@app.route("/admin/start_session", methods=["POST"])
def admin_start_session():
    """
    Mike texts: 'start session for [chat_id] on [platform]'
    Bridge parses and calls this endpoint.
    """
    data           = request.json or {}
    client_chat_id = int(data.get("client_chat_id", 0))
    platform       = data.get("platform", "assessment")

    if not client_chat_id:
        return jsonify({"error": "client_chat_id required"}), 400

    try:
        from corvus_hack.db import allocate_account, register_client
        register_client(client_chat_id, data.get("username", ""))
        session_id = uuid.uuid4().hex[:8]
        account    = allocate_account(session_id, client_chat_id, platform)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not account:
        return jsonify({"error": "No accounts available — all 4 are in use"}), 503

    with _lock:
        _sessions[session_id] = {
            "chat_id":     client_chat_id,
            "platform":    platform,
            "account":     account,
            "status":      "active",
            "start_time":  time.time(),
            "history":     deque(maxlen=10),
            "last_screen": "",
        }

    with _pending_lock:
        _pending_accounts[account] = session_id

    log.info("Session %s started → client=%s account=%s platform=%s",
             session_id, client_chat_id, account, platform)

    _telegram_send(
        client_chat_id,
        f"Your session is ready!\n\n"
        f"Platform: {platform}\n\n"
        f"Connect via UltraViewer when the admin sends you the credentials.\n"
        f"I'll guide you as you navigate — just keep going normally.",
    )

    return jsonify({"session_id": session_id, "account": account})


@app.route("/admin/end_session", methods=["POST"])
def admin_end_session():
    data       = request.json or {}
    session_id = data.get("session_id", "")

    with _lock:
        sess = _sessions.pop(session_id, None)

    if sess:
        try:
            from corvus_hack.db import release_account, increment_client_sessions
            release_account(session_id)
            increment_client_sessions(sess["chat_id"])
        except Exception:
            pass
        _telegram_send(sess["chat_id"], "Session ended. Thanks for using Corvus!")

    return jsonify({"status": "ended"})


@app.route("/admin/pool", methods=["GET"])
def admin_pool():
    try:
        from corvus_hack.db import pool_status
        return jsonify(pool_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/sessions", methods=["GET"])
def admin_sessions():
    with _lock:
        result = {
            sid: {
                "chat_id":    s["chat_id"],
                "account":    s.get("account", ""),
                "platform":   s.get("platform", ""),
                "status":     s.get("status", ""),
                "uptime_min": round((time.time() - s["start_time"]) / 60, 1),
            }
            for sid, s in _sessions.items()
        }
    return jsonify(result)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from corvus_hack.db import init_db

    _startup_log = Path(__file__).resolve().parent.parent / "logs" / "master_dispatcher_startup.log"
    try:
        _startup_log.parent.mkdir(parents=True, exist_ok=True)
        init_db()
        log.info("Master dispatcher starting on port 9200...")
        with _startup_log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} startup OK, launching on 127.0.0.1:9200\n")
        # Bind loopback only. Workers reach the dispatcher via localhost, and
        # 127.0.0.1 spans all local Windows sessions on this machine — so binding
        # to 0.0.0.0 would expose the endpoint to the network for no benefit.
        app.run(host="127.0.0.1", port=9200, threaded=True)
    except Exception:
        with _startup_log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} STARTUP FAILED:\n")
            f.write(traceback.format_exc())
            f.write("\n")
        raise
