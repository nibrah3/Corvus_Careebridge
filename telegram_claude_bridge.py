"""
telegram_claude_bridge.py — Forward Telegram DMs to Claude Code CLI.

Each admin DM is forwarded to `claude --print` as a subprocess.
Conversation history is kept in-process so replies have context without
needing --continue (which conflicts with the active desktop session).

Streaming mode (--stream): edits the Telegram message live as tokens arrive.

Usage:
  python telegram_claude_bridge.py           # plain mode
  python telegram_claude_bridge.py --stream  # live streaming updates
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from collections import deque

import requests as _requests

# ── Bootstrap ─────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")

from telegram_mcp._bot import admin_chat_ids, bus, send_message, edit_message

# ── Config ────────────────────────────────────────────────────────────────────

_CWD = str(ROOT)
_TIMEOUT = 90           # max seconds per claude call
_TG_MAX = 3800          # leave room below Telegram's 4096-char limit
_STREAM_INTERVAL = 1.5  # seconds between live edit_message updates
_HISTORY_TURNS = 6      # number of recent Q&A pairs to include as context
_ADMINS: list[int] = []

_STREAM_MODE = "--stream" in sys.argv

# Screen-reading ground rules injected into every prompt so the subprocess
# Claude never reaches for keyboard shortcuts or CDP screenshots.
_SYSTEM_PROMPT = """\
[System rules — follow strictly, these OVERRIDE CLAUDE.md where they conflict]
You are the Corvus remote control subprocess invoked from Telegram.

CRITICAL OVERRIDES (take precedence over everything in CLAUDE.md):
1. NEVER show a menu, NEVER call AskUserQuestion. Respond directly to the
   user's request immediately. The "Menu" section of CLAUDE.md does NOT apply
   here — the Telegram user is already an authenticated admin.
2. AskUserQuestion is not available in this context. Do not try to call it.
3. Execute the task the user asked for. If they ask you to create a folder,
   create it. If they ask what is on screen, capture it.

SCREEN READING RULE (absolute, no exceptions):
- To read the screen: use ONLY mcp__capture__screenshot (DXGI GPU compositor).
- NEVER use keyboard shortcuts (PrintScreen / Win+PrtScr), Win32 BitBlt/GDI,
  PowerShell screenshot cmdlets, or CDP Page.captureScreenshot.
- After capturing, pass the file path to mcp__gemini__analyse_image to describe
  what is visible.
- If mcp__capture__screenshot is unavailable, say so — do NOT fall back.

[End system rules]
"""

# ── In-process conversation history ──────────────────────────────────────────
# Keeps last N turns so each claude call has context without --continue.
# Keys are chat_id; values are deque of (user_msg, assistant_reply) tuples.

_history: dict[int, deque[tuple[str, str]]] = {}
_history_lock = threading.Lock()

# Serialize all Claude subprocess calls: one at a time prevents resource
# contention (6 simultaneous Node.js processes → 4× slowdown on Windows).
# Also ensures history is built/recorded in message order.
_claude_lock = threading.Lock()


def _build_prompt(chat_id: int, user_text: str) -> str:
    """Prepend system rules + recent conversation history so Claude has context."""
    with _history_lock:
        turns = list(_history.get(chat_id, []))

    parts = [_SYSTEM_PROMPT]

    if turns:
        parts.append("[Previous conversation]\n")
        for u, a in turns:
            parts.append(f"User: {u}")
            parts.append(f"Assistant: {a}\n")
        parts.append("[Current message]")
        parts.append(f"User: {user_text}")
    else:
        parts.append(user_text)

    return "\n".join(parts)


def _record_turn(chat_id: int, user_text: str, reply: str) -> None:
    with _history_lock:
        if chat_id not in _history:
            _history[chat_id] = deque(maxlen=_HISTORY_TURNS)
        _history[chat_id].append((user_text, reply))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _chunk(text: str, size: int = _TG_MAX) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= size:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, size)
        if split_at < 1:
            split_at = size
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _send_chunks(chat_id: int, text: str) -> None:
    """Send text as one or more plain Telegram messages (no HTML parse_mode)."""
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks):
        prefix = f"[{i+1}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        send_message(chat_id, prefix + chunk, parse_mode=None)


# ── Claude invocation ─────────────────────────────────────────────────────────

_CLAUDE_TOOLS = "Read,Edit,Bash,Glob,Grep,Write,mcp__capture__screenshot,mcp__gemini__analyse_image"
_CLAUDE_BIN = r"C:\Users\Mike\AppData\Roaming\npm\claude.cmd"
_MCP_CONFIG = str(ROOT / "bridge_mcp.json")


def _subprocess_env() -> dict:
    """Force claude CLI into subscription (OAuth) mode.
    Setting the key to "" instead of removing it skips the Windows keychain
    lookup that adds ~40s of overhead when the key is absent entirely."""
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""
    return env


def _ask_claude_plain(prompt: str) -> str:
    result = subprocess.run(
        [
            _CLAUDE_BIN, "--print",
            "--output-format", "json",
            "--allowedTools", _CLAUDE_TOOLS,
            "--permission-mode", "acceptEdits",
            "--mcp-config", _MCP_CONFIG,
            "--strict-mcp-config",
            prompt,
        ],
        cwd=_CWD,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_TIMEOUT,
        env=_subprocess_env(),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    raw = (result.stdout or "").strip()
    if result.returncode != 0 and not raw:
        err = (result.stderr or "").strip()
        return f"Error (exit {result.returncode}):\n{err[:600]}" if err else \
               f"Claude exited with code {result.returncode} (no output)."
    try:
        data = json.loads(raw)
        return (data.get("result") or data.get("text") or raw or "(no output)").strip()
    except json.JSONDecodeError:
        return raw or "(Claude returned no output.)"


def _ask_claude_stream(prompt: str, on_update: "Callable[[str], None]") -> str:
    proc = subprocess.Popen(
        [
            _CLAUDE_BIN, "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--allowedTools", _CLAUDE_TOOLS,
            "--permission-mode", "acceptEdits",
            "--mcp-config", _MCP_CONFIG,
            "--strict-mcp-config",
            prompt,
        ],
        cwd=_CWD,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_subprocess_env(),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    accumulated = ""
    last_update = 0.0
    final_result = ""

    try:
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                kind = obj.get("type", "")

                if kind == "text":
                    accumulated += obj.get("text", "")
                elif kind == "stream_event":
                    inner = obj.get("event", {})
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta", {})
                        if delta.get("type") == "text_delta":
                            accumulated += delta.get("text", "")
                elif kind == "result" and obj.get("subtype") == "success":
                    final_result = obj.get("result", accumulated).strip()
                    break

                now = time.monotonic()
                if accumulated and now - last_update >= _STREAM_INTERVAL:
                    on_update(accumulated)
                    last_update = now

            except json.JSONDecodeError:
                accumulated += line + "\n"
    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    return final_result or accumulated.strip() or "(Claude returned no output.)"


# ── Message handlers ──────────────────────────────────────────────────────────


def _handle_plain(chat_id: int, user_text: str) -> None:
    send_message(chat_id, "Thinking...", parse_mode=None)
    with _claude_lock:
        prompt = _build_prompt(chat_id, user_text)
        try:
            answer = _ask_claude_plain(prompt)
        except subprocess.TimeoutExpired:
            send_message(chat_id, f"Timed out after {_TIMEOUT}s.", parse_mode=None)
            return
        except Exception as exc:
            log.exception("Claude call failed")
            send_message(chat_id, f"Error: {exc}", parse_mode=None)
            return
        _record_turn(chat_id, user_text, answer)
    _send_chunks(chat_id, answer)


def _handle_stream(chat_id: int, user_text: str) -> None:
    resp = send_message(chat_id, "Thinking...", parse_mode=None)
    msg_id: int | None = None
    if resp.get("ok"):
        msg_id = resp["result"]["message_id"]

    last_sent = [""]

    def _update(accumulated: str) -> None:
        if msg_id is None or accumulated == last_sent[0]:
            return
        preview = accumulated[-_TG_MAX:] if len(accumulated) > _TG_MAX else accumulated
        try:
            edit_message(chat_id, msg_id, preview, parse_mode=None)
            last_sent[0] = accumulated
        except Exception:
            pass

    with _claude_lock:
        prompt = _build_prompt(chat_id, user_text)
        try:
            final = _ask_claude_stream(prompt, _update)
        except subprocess.TimeoutExpired:
            if msg_id:
                edit_message(chat_id, msg_id, f"Timed out after {_TIMEOUT}s.", parse_mode=None)
            return
        except Exception as exc:
            log.exception("Claude stream failed")
            if msg_id:
                edit_message(chat_id, msg_id, f"Error: {exc}", parse_mode=None)
            return
        _record_turn(chat_id, user_text, final)

    if msg_id and len(final) <= _TG_MAX:
        try:
            edit_message(chat_id, msg_id, final, parse_mode=None)
            return
        except Exception:
            pass
    _send_chunks(chat_id, final)


_MASTER = "http://localhost:9200"


def _master_post(path: str, body: dict) -> dict | None:
    try:
        r = _requests.post(f"{_MASTER}{path}", json=body, timeout=10)
        return r.json()
    except Exception as e:
        log.warning("master_dispatcher %s failed: %s", path, e)
        return None


def _handle(msg: dict) -> None:
    chat_id = msg.get("chat", {}).get("id", 0)
    username = msg.get("chat", {}).get("username", "")
    text = (msg.get("text") or "").strip()
    if not text:
        return

    # ── Non-admin (client) messages ────────────────────────────────────────────
    if chat_id not in _ADMINS:
        log.info("client chat=%s  %r", chat_id, text[:60])
        lower = text.lower()

        # Client signalling they're done
        if lower in ("stop", "done", "end", "exit"):
            try:
                from corvus_hack.db import get_client_session
                sess = get_client_session(chat_id)
                if sess:
                    _master_post("/admin/end_session", {"session_id": sess["session_id"]})
                    send_message(chat_id, "Session ended. Thanks!", parse_mode=None)
                else:
                    send_message(chat_id, "No active session.", parse_mode=None)
            except Exception as e:
                log.error("Client stop failed: %s", e)
            return

        # First contact — ping admin with chat_id so Mike can start the session manually
        with _history_lock:
            is_new = chat_id not in _history
        if is_new:
            user_str = f"@{username}" if username else str(chat_id)
            for admin_id in _ADMINS:
                try:
                    send_message(
                        admin_id,
                        f"New client: {user_str}  (chat_id: {chat_id})\nSays: {text[:200]}\n\nTo start their session: start {chat_id}",
                        parse_mode=None,
                    )
                except Exception:
                    pass
            send_message(chat_id, "Connected! Your guide will start your session when ready.", parse_mode=None)
            with _history_lock:
                _history[chat_id] = deque(maxlen=_HISTORY_TURNS)
            return

        # In-session question → route to master for Claude to answer
        _master_post("/client/message", {"chat_id": chat_id, "text": text})
        return

    log.info("chat=%s  %r", chat_id, text[:80])

    lower = text.lower()

    # ── Admin shortcut: start <chat_id> [platform] ───────────────────────────
    # Quick form: start 1234567890  or  start 1234567890 imocha
    if lower.startswith("start "):
        parts = text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            client_id = int(parts[1])
            platform = parts[2] if len(parts) >= 3 else "assessment"
            result = _master_post(
                "/admin/start_session",
                {"client_chat_id": client_id, "platform": platform},
            )
            if result and "session_id" in result:
                send_message(
                    chat_id,
                    f"Session started for {client_id} on {platform}.\nID: {result['session_id']}",
                    parse_mode=None,
                )
            elif result and "error" in result:
                send_message(chat_id, f"Error: {result['error']}", parse_mode=None)
            else:
                send_message(chat_id, "Master dispatcher unreachable.", parse_mode=None)
            return

    # ── Admin shortcut: start session ─────────────────────────────────────────
    # Long form: start session <client_chat_id> <platform>
    if lower.startswith("start session "):
        parts = text.split()
        if len(parts) >= 3:
            try:
                client_id = int(parts[2])
                platform  = parts[3] if len(parts) >= 4 else "assessment"
                result = _master_post(
                    "/admin/start_session",
                    {"client_chat_id": client_id, "platform": platform},
                )
                if result and "session_id" in result:
                    send_message(
                        chat_id,
                        f"Session started.\nID: {result['session_id']}\nAccount: {result['account']}\nPlatform: {platform}",
                        parse_mode=None,
                    )
                elif result and "error" in result:
                    send_message(chat_id, f"Error: {result['error']}", parse_mode=None)
                else:
                    send_message(chat_id, "Master dispatcher unreachable.", parse_mode=None)
            except (ValueError, IndexError) as e:
                send_message(chat_id, f"Usage: start session <chat_id> <platform>\nError: {e}", parse_mode=None)
        return

    # ── Admin shortcut: end session ───────────────────────────────────────────
    # Usage: end session <session_id>
    if lower.startswith("end session "):
        parts = text.split()
        if len(parts) >= 3:
            sid = parts[2]
            result = _master_post("/admin/end_session", {"session_id": sid})
            send_message(chat_id, f"Session {sid} ended." if result else "Master unreachable.", parse_mode=None)
        return

    # ── Admin shortcut: pool / sessions status ────────────────────────────────
    if lower in ("pool", "/pool", "sessions", "/sessions"):
        try:
            path = "/admin/pool" if "pool" in lower else "/admin/sessions"
            r = _requests.get(f"{_MASTER}{path}", timeout=8)
            data = r.json()
            send_message(chat_id, json.dumps(data, indent=2)[:3000], parse_mode=None)
        except Exception as e:
            send_message(chat_id, f"Master unreachable: {e}", parse_mode=None)
        return

    if lower in ("/ping", "ping"):
        mode = "streaming" if _STREAM_MODE else "plain"
        send_message(chat_id, f"Pong. Bridge alive ({mode} mode, timeout={_TIMEOUT}s).", parse_mode=None)
        return

    if lower == "/clearhistory":
        with _history_lock:
            _history.pop(chat_id, None)
        send_message(chat_id, "Conversation history cleared. Starting fresh.", parse_mode=None)
        return

    if lower in ("/help", "help"):
        send_message(
            chat_id,
            "Corvus Admin Commands\n\n"
            "Session:\n"
            "  start <chat_id>            — start client session\n"
            "  start <chat_id> <platform> — start on named platform\n"
            "  end session <session_id>   — end a session\n"
            "  pool / sessions            — show active sessions\n\n"
            "Bridge:\n"
            "  /ping          — health check\n"
            "  /clearhistory  — reset conversation\n"
            "  /help          — this message\n\n"
            f"Mode: {'streaming' if _STREAM_MODE else 'plain'}  |  Timeout: {_TIMEOUT}s\n"
            f"CWD: {_CWD}",
            parse_mode=None,
        )
        return

    if _STREAM_MODE:
        _handle_stream(chat_id, text)
    else:
        _handle_plain(chat_id, text)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    global _ADMINS
    _ADMINS = admin_chat_ids()
    mode = "streaming" if _STREAM_MODE else "plain"
    log.info("Bridge starting (%s mode) — admins: %s", mode, _ADMINS)

    bus.start()
    for cid in _ADMINS:
        try:
            send_message(
                cid,
                f"Corvus bridge online ({mode} mode).\nType anything to ask Claude.\n/help for commands.",
                parse_mode=None,
            )
        except Exception:
            pass

    log.info("Listening for Telegram messages...")
    while True:
        try:
            msg = bus.listener_queue.get(timeout=5)
            threading.Thread(target=_handle, args=(msg,), daemon=True).start()
        except Exception:
            pass


if __name__ == "__main__":
    main()
