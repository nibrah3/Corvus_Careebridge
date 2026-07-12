"""
telegram_claude_bridge.py — Forward Telegram messages to Claude Code CLI.

Every admin message is sent to `claude --print "text"` as a one-shot subprocess
call. The response is chunked and sent back to Telegram.

Streaming mode (--stream): uses `--output-format stream-json` and edits the
Telegram message in-place as tokens arrive — gives real-time typing effect.

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
_TIMEOUT = 300          # max seconds per claude call (5 min for complex tasks)
_TG_MAX = 3800          # leave room below Telegram's 4096-char limit
_STREAM_INTERVAL = 1.5  # seconds between live edit_message updates
_ADMINS: list[int] = []

# Check if we're in streaming mode
_STREAM_MODE = "--stream" in sys.argv

# ── Helpers ───────────────────────────────────────────────────────────────────


def _chunk(text: str, size: int = _TG_MAX) -> list[str]:
    """Split text into ≤size-char chunks, preferring line boundaries."""
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


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _send_chunks(chat_id: int, text: str) -> None:
    """Send potentially long text as one or more Telegram messages."""
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks):
        prefix = f"[{i+1}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        # Wrap in <pre> so monospace code/output looks right
        escaped = _escape_html(chunk)
        send_message(chat_id, f"{prefix}<pre>{escaped}</pre>")


# ── Claude invocation — plain mode ───────────────────────────────────────────


_CLAUDE_TOOLS = "Read,Edit,Bash,Glob,Grep,Write"

# Full path to claude CLI — npm-installed scripts aren't on Python subprocess PATH
_CLAUDE_BIN = r"C:\Users\Mike\AppData\Roaming\npm\claude.cmd"

# Session file that pins the conversation ID for cross-message continuity.
# All Telegram DMs continue the same Claude session rather than starting fresh.
_SESSION_FILE = ROOT / ".telegram_session_id"


def _resume_args() -> list[str]:
    """Return --resume SESSION_ID args if a pinned session exists, else --continue."""
    if _SESSION_FILE.exists():
        sid = _SESSION_FILE.read_text().strip()
        if sid:
            return ["--resume", sid]
    return ["--continue"]


def _subprocess_env() -> dict:
    """
    Build subprocess env that forces claude CLI into subscription mode.
    Remove ANTHROPIC_API_KEY entirely — if it's set (even empty), claude
    tries API-credit mode and fails. Subscription (OAuth) requires it absent.
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _ask_claude_plain(prompt: str) -> str:
    """Blocking call: `claude --print --continue --output-format json <prompt>` → response text."""
    result = subprocess.run(
        [
            _CLAUDE_BIN, "--print",
            *_resume_args(),
            "--output-format", "json",
            "--allowedTools", _CLAUDE_TOOLS,
            "--permission-mode", "acceptEdits",
            prompt,
        ],
        cwd=_CWD,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=_subprocess_env(),
    )
    raw = (result.stdout or "").strip()
    if result.returncode != 0 and not raw:
        err = (result.stderr or "").strip()
        if err:
            return f"Error (exit {result.returncode}):\n{err[:600]}"
        return f"Claude exited with code {result.returncode} (no output)."
    try:
        data = json.loads(raw)
        sid = data.get("session_id")
        if sid:
            _SESSION_FILE.write_text(sid)
        return (data.get("result") or data.get("text") or raw or "(no output)").strip()
    except json.JSONDecodeError:
        return raw or "(Claude returned no output.)"


# ── Claude invocation — streaming mode ───────────────────────────────────────


def _ask_claude_stream(
    prompt: str,
    on_update: "Callable[[str], None]",
) -> str:
    """
    Stream `claude --print --output-format stream-json <prompt>`.
    Calls on_update(accumulated_text) periodically as tokens arrive.
    Returns the final complete text.
    """
    proc = subprocess.Popen(
        [
            _CLAUDE_BIN, "--print",
            *_resume_args(),
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--allowedTools", _CLAUDE_TOOLS,
            "--permission-mode", "acceptEdits",
            prompt,
        ],
        cwd=_CWD,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_subprocess_env(),
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
                    # Simple text event (non-partial mode)
                    accumulated += obj.get("text", "")
                elif kind == "stream_event":
                    # --include-partial-messages token-level events
                    inner = obj.get("event", {})
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta", {})
                        if delta.get("type") == "text_delta":
                            accumulated += delta.get("text", "")
                elif kind == "result" and obj.get("subtype") == "success":
                    final_result = obj.get("result", accumulated).strip()
                    sid = obj.get("session_id")
                    if sid:
                        _SESSION_FILE.write_text(sid)
                    break  # done — don't read further

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


# ── Message handler ───────────────────────────────────────────────────────────


def _handle_plain(chat_id: int, text: str) -> None:
    send_message(chat_id, "Thinking...")
    try:
        answer = _ask_claude_plain(text)
    except subprocess.TimeoutExpired:
        send_message(chat_id, f"Timed out after {_TIMEOUT}s.")
        return
    except Exception as exc:
        log.exception("Claude call failed")
        send_message(chat_id, f"Error: {_escape_html(str(exc))}")
        return
    _send_chunks(chat_id, answer)


def _handle_stream(chat_id: int, text: str) -> None:
    # Send placeholder and track its message_id for live edits
    resp = send_message(chat_id, "Thinking...")
    msg_id: int | None = None
    if resp.get("ok"):
        msg_id = resp["result"]["message_id"]

    last_sent = [""]  # mutable cell for closure

    def _update(accumulated: str) -> None:
        if msg_id is None:
            return
        if accumulated == last_sent[0]:
            return
        preview = accumulated[-_TG_MAX:] if len(accumulated) > _TG_MAX else accumulated
        try:
            edit_message(chat_id, msg_id, f"<pre>{_escape_html(preview)}</pre>")
            last_sent[0] = accumulated
        except Exception:
            pass  # edit might fail if content unchanged — that's fine

    try:
        final = _ask_claude_stream(text, _update)
    except subprocess.TimeoutExpired:
        if msg_id:
            edit_message(chat_id, msg_id, f"Timed out after {_TIMEOUT}s.")
        return
    except Exception as exc:
        log.exception("Claude stream failed")
        if msg_id:
            edit_message(chat_id, msg_id, f"Error: {_escape_html(str(exc))}")
        return

    # Send final complete response (replace placeholder or send chunks)
    if msg_id and len(final) <= _TG_MAX:
        try:
            edit_message(chat_id, msg_id, f"<pre>{_escape_html(final)}</pre>")
            return
        except Exception:
            pass
    _send_chunks(chat_id, final)


def _handle(msg: dict) -> None:
    chat_id = msg.get("chat", {}).get("id", 0)
    text = (msg.get("text") or "").strip()
    if not text or chat_id not in _ADMINS:
        return

    log.info("chat=%s  %r", chat_id, text[:80])

    # Bridge meta-commands
    lower = text.lower()
    if lower in ("/ping", "ping"):
        mode = "streaming" if _STREAM_MODE else "plain"
        send_message(chat_id, f"Pong. Bridge alive ({mode} mode, timeout={_TIMEOUT}s).")
        return

    if lower in ("/help", "help", "/help"):
        sid = _SESSION_FILE.read_text().strip() if _SESSION_FILE.exists() else "none yet"
        send_message(
            chat_id,
            "<b>Telegram → Claude Code Bridge</b>\n\n"
            "Type anything — it goes to Claude Code running in\n"
            f"<code>{_CWD}</code>\n\n"
            "<b>Meta commands:</b>\n"
            "/ping — check bridge is alive\n"
            "/session — show pinned session ID\n"
            "/newsession — forget session, start fresh\n"
            "/help — this message\n\n"
            "<b>Tips:</b>\n"
            "• All DMs continue the same persistent session\n"
            "• Claude remembers context from previous messages\n"
            "• Long responses are split into chunks\n"
            f"• Session: <code>{sid[:20]}...</code>\n"
            f"• Timeout: {_TIMEOUT}s per call\n"
            f"• Mode: {'streaming (live updates)' if _STREAM_MODE else 'plain (reply when done)'}",
        )
        return

    if lower == "/session":
        sid = _SESSION_FILE.read_text().strip() if _SESSION_FILE.exists() else "none"
        send_message(chat_id, f"Pinned session ID:\n<code>{sid}</code>")
        return

    if lower == "/newsession":
        if _SESSION_FILE.exists():
            _SESSION_FILE.unlink()
        send_message(chat_id, "Session reset. Next message starts a fresh Claude context.")
        return

    # Forward to Claude
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
                f"Claude bridge online ({mode} mode).\n"
                "Type anything to ask Claude Code.\n"
                "/help for info, /ping to test.",
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
