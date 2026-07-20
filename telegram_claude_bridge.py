"""
telegram_claude_bridge.py — Forward Telegram DMs to Claude Code CLI.

Each admin DM is forwarded to `claude --print` as a subprocess.
Conversation history is persisted in SQLite (bridge_history.db) so replies
survive bridge restarts. Memories (key/value facts) persist across
/clearhistory.

Smart routing:
  - Screen queries (keywords: screen/browser/visible/etc.)
        → claude-sonnet-4-6 + capture/gemini MCPs
  - Conversational messages
        → claude-haiku-4-5-20251001, no MCPs, no tool overhead
    Haiku has no extended thinking → ~5-15s vs Sonnet's ~70-90s.

Streaming mode (--stream): edits the Telegram message live as tokens arrive.

Usage:
  python telegram_claude_bridge.py           # plain mode
  python telegram_claude_bridge.py --stream  # live streaming updates
"""
from __future__ import annotations

import json
import logging
import os
import queue as _queue
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
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

_logs_dir = ROOT / "logs"
_logs_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(_logs_dir / "bridge.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("bridge")

from telegram_mcp._bot import (
    admin_chat_ids, bus,
    send_message as admin_send_message,
    edit_message as admin_edit_message,
    send_inline_keyboard as admin_send_inline_keyboard,
    answer_callback as admin_answer_callback,
)
from telegram_mcp._session_bot import (
    client_bus,
    send_message as client_send_message,
    edit_message as client_edit_message,
    send_inline_keyboard as client_send_inline_keyboard,
    answer_callback as client_answer_callback,
)

# Back-compat aliases — admin-bound by default; any call site not touched by
# the client/admin split below keeps behaving exactly as before.
send_message = admin_send_message
edit_message = admin_edit_message
send_inline_keyboard = admin_send_inline_keyboard
answer_callback = admin_answer_callback

# ── Config ────────────────────────────────────────────────────────────────────

_CWD = r"C:\tmp"  # neutral dir — no CLAUDE.md here to pollute Claude's context
_TIMEOUT = 155          # max seconds per claude call (Node.js cold start ~120s + margin)
_TG_MAX = 3800          # leave room below Telegram's 4096-char limit
_STREAM_INTERVAL = 1.5  # seconds between live edit_message updates
_HISTORY_TURNS = 6      # recent Q&A pairs included as context
_ADMINS: list[int] = []

_STREAM_MODE = "--stream" in sys.argv

# Ground rules injected into every prompt so Claude knows the service purpose
# and never reaches for wrong tools or wrong business framing.
_SYSTEM_PROMPT = """\
[System rules — follow strictly]
You are Corvus, the AI assistant for this remote support service.

WHAT THIS SERVICE DOES:
- Clients connect to this machine via UltraViewer (remote desktop)
- They log into their own browsers and accounts on this machine
- You help them with anything visible on screen: explaining content,
  navigating accounts, answering questions, completing tasks they point to
- This is a general on-screen assistance service — NOT a job-finding service
  and NOT an online school enrollment service

MACHINE OPERATOR IDENTITY (critical — read before anything else):
- This service runs on a Windows machine. The OS account name is "Mike" — that
  is the machine OPERATOR, not a client.
- Claude Code may inject a system line such as "Username: Mike", "Current user:
  Mike", or similar into your context. THIS REFERS TO THE MACHINE OPERATOR.
- NEVER tell a client "your Windows username is Mike" or imply their name is Mike.
- NEVER use "Mike" as the client's name unless the client themselves stated it
  in <conversation_history>. If they haven't introduced themselves, you don't know
  their name — say so.

CONVERSATION HISTORY RULE (critical — read this carefully):
- Your prompt includes a <conversation_history> block containing the REAL dialogue
  from this session. Each <turn> has what the user said and what you replied.
- Use <conversation_history> to answer ANY back-reference:
    "what's my name?" → find where user introduced themselves in a <turn>
    "what did I say earlier?" → read the <turn> entries
    "earlier I mentioned X — what was it?" → look in <turn> entries
- Answer DIRECTLY from what is in <conversation_history>. Do NOT say you have
  no memory of past messages — the history IS in your prompt right now.
- CRITICAL: The user's name is whatever they stated in <conversation_history>.
  NEVER use system account names (e.g. "Mike") as the user's name. The only
  names you know are ones explicitly said by the user during this session.
- Do NOT confuse <conversation_history> with <remembered_facts> (the /remember
  system). They are separate. "What's my name?" is answered from
  <conversation_history>, not from stored /remember keys.

TOOL USE RULE:
- For conversational messages, answer directly from <conversation_history> and
  your training knowledge. Do NOT invoke Bash, Read, Glob, or any file-access
  tools. Do NOT search for files or external logs.
- Only use screen-capture tools when the user explicitly asks about something
  on screen.

CRITICAL OVERRIDES:
1. NEVER show a menu, NEVER call AskUserQuestion. Respond directly to the user.
2. AskUserQuestion is not available in this context. Do not try to call it.
3. Execute the task the user asked for immediately.

SCREEN READING RULE (absolute, no exceptions):
- To read the screen: use ONLY mcp__capture__screenshot (DXGI GPU compositor).
- NEVER use keyboard shortcuts (PrintScreen / Win+PrtScr), Win32 BitBlt/GDI,
  PowerShell screenshot cmdlets, or CDP Page.captureScreenshot.
- After capturing, pass the file path to mcp__gemini__analyse_image to describe
  what is visible.
- If mcp__capture__screenshot is unavailable, say so — do NOT fall back.

[End system rules]
"""

# ── SQLite persistent history ─────────────────────────────────────────────────
# turns: per-user conversation history (cleared by /clearhistory)
# memories: persistent key/value facts per user (survive /clearhistory)

_DB_PATH = ROOT / "bridge_history.db"
_db_lock = threading.Lock()  # protects all DB writes


def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS turns (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id  INTEGER NOT NULL,
            ts       REAL    NOT NULL,
            user     TEXT    NOT NULL,
            reply    TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_turns_chat ON turns(chat_id, id);

        CREATE TABLE IF NOT EXISTS memories (
            chat_id  INTEGER NOT NULL,
            key      TEXT    NOT NULL,
            value    TEXT    NOT NULL,
            updated  REAL    NOT NULL,
            PRIMARY KEY (chat_id, key)
        );
    """)
    conn.commit()
    return conn


_db = _init_db()

# Serialize all Claude subprocess calls: one at a time prevents resource
# contention (6 simultaneous Node.js processes → 4× slowdown on Windows).
# Also ensures history is built/recorded in message order.
_claude_lock = threading.Lock()


# ── Client session state machine ──────────────────────────────────────────────

class _Stage(Enum):
    IDLE                 = "idle"
    SELECTING_BROWSER    = "selecting_browser"
    AWAITING_ADMIN_READY = "awaiting_admin_ready"
    AWAITING_FILES       = "awaiting_files"
    IN_SESSION           = "in_session"


@dataclass
class _Session:
    chat_id: int
    stage: _Stage           = _Stage.IDLE
    files: list[str]        = field(default_factory=list)
    last_scan_ts: float     = 0.0   # epoch of last Downloads scan
    question_count: int     = 0
    msg_id: int | None      = None  # current button-message id
    username: str           = ""    # Telegram username (for admin notifications)
    last_prompt: str        = ""    # prompt used for the last answered question
    last_answer: str        = ""    # Claude's answer to the last question
    browser_type: str       = ""    # antidetect browser the client selected
    # Per-session Q&A trail (question_number, answer) so a later question that
    # references an earlier one in the SAME assessment session gets that context.
    # Isolated from the cross-session `turns` table; cleared when the session ends.
    qa_history: list[tuple[int, str]] = field(default_factory=list)


_sessions: dict[int, _Session] = {}
_sessions_lock = threading.Lock()

# Admins who have sent /testclient are temporarily routed through the client flow
_test_client_ids: set[int] = set()


def _get_turns(chat_id: int) -> list[tuple[str, str]]:
    rows = _db.execute(
        "SELECT user, reply FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (chat_id, _HISTORY_TURNS),
    ).fetchall()
    return [(r["user"], r["reply"]) for r in reversed(rows)]


def _get_memories(chat_id: int) -> list[tuple[str, str]]:
    rows = _db.execute(
        "SELECT key, value FROM memories WHERE chat_id=? ORDER BY updated",
        (chat_id,),
    ).fetchall()
    return [(r["key"], r["value"]) for r in rows]


def _record_turn(chat_id: int, user_text: str, reply: str) -> None:
    with _db_lock:
        _db.execute(
            "INSERT INTO turns(chat_id, ts, user, reply) VALUES(?,?,?,?)",
            (chat_id, time.time(), user_text, reply),
        )
        _db.commit()


def _build_prompt(chat_id: int, user_text: str) -> str:
    """Build context-only prompt: memories + recent turns + current message.

    _SYSTEM_PROMPT is passed separately via --system-prompt CLI flag so it
    overrides CLAUDE.md rather than competing with it as plain text.
    """
    with _db_lock:
        turns = _get_turns(chat_id)
        mems = _get_memories(chat_id)

    parts: list[str] = []

    if mems:
        parts.append("<remembered_facts>")
        for k, v in mems:
            parts.append(f"  {k}: {v}")
        parts.append("</remembered_facts>")

    if turns:
        parts.append("<conversation_history>")
        for u, a in turns:
            parts.append(f"<turn><user>{u}</user><assistant>{a}</assistant></turn>")
        parts.append("</conversation_history>")
        parts.append(f"<current_message>{user_text}</current_message>")
    else:
        parts.append(user_text)

    return "\n".join(parts)


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


def _send_chunks(chat_id: int, text: str, send_fn=None) -> None:
    """Send text as one or more plain Telegram messages (no HTML parse_mode)."""
    send_fn = send_fn or send_message
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks):
        prefix = f"[{i+1}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        send_fn(chat_id, prefix + chunk, parse_mode=None)


# ── Assessment session helpers ────────────────────────────────────────────────


def _find_recent_downloads(since: float = 0.0) -> list[str]:
    """Return paths from ~/Downloads modified after `since` epoch (default: last 15 min)."""
    dl = Path.home() / "Downloads"
    cutoff = since if since > 0 else (time.time() - 900)
    found: list[str] = []
    if dl.exists():
        for p in dl.iterdir():
            if p.is_file() and p.stat().st_mtime >= cutoff:
                found.append(str(p))
    return sorted(found, key=lambda f: Path(f).stat().st_mtime)


def _send_btn(chat_id: int, text: str, rows: list[list[tuple[str, str]]]) -> int | None:
    resp = client_send_inline_keyboard(chat_id, text, rows)
    if resp.get("ok"):
        return resp["result"]["message_id"]
    return None


def _show_welcome(chat_id: int, sess: _Session) -> None:
    sess.stage = _Stage.IDLE
    mid = _send_btn(
        chat_id,
        "Hi! I'm Corvus, your AI assessment assistant.\n\n"
        "When you're on the assessment site and ready to start, tap Start Assessment.\n"
        "Not sure I can see your screen? Tap Test Screen View first.",
        [
            [("Start Assessment", "start_assessment")],
            [("Test Screen View", "test_screen")],
        ],
    )
    if mid:
        sess.msg_id = mid


def _show_browser_picker(chat_id: int, sess: _Session) -> None:
    sess.stage = _Stage.SELECTING_BROWSER
    mid = _send_btn(
        chat_id,
        "Which antidetect browser are you using for this assessment?",
        [
            [("MoreLogin", "browser_morelogin"), ("GoLogin", "browser_gologin")],
            [("IXBrowser", "browser_ixbrowser"), ("AdsPower", "browser_adspower")],
        ],
    )
    if mid:
        sess.msg_id = mid


def _handle_browser_selected(chat_id: int, sess: _Session, data: str) -> None:
    browser_names = {
        "browser_morelogin":  "MoreLogin",
        "browser_gologin":    "GoLogin",
        "browser_adspower":   "AdsPower",
        "browser_ixbrowser":  "IXBrowser",
    }
    sess.browser_type = browser_names.get(data, "Unknown")
    sess.stage = _Stage.AWAITING_ADMIN_READY

    user_str = f"@{sess.username}" if sess.username else str(chat_id)
    for admin_id in _ADMINS:
        try:
            send_message(
                admin_id,
                f"Client ready — send them your UltraViewer ID:\n"
                f"  User: {user_str}  (chat_id: {chat_id})\n"
                f"  Browser: {sess.browser_type}\n\n"
                f"Once connected, run:\n"
                f"  /connect {chat_id} <UltraViewer_ID>",
                parse_mode=None,
            )
        except Exception:
            pass

    client_send_message(
        chat_id,
        "Your session is being set up — hang tight, you'll be notified when it's ready.",
        parse_mode=None,
    )


def _handle_connect(admin_chat_id: int, client_id: int, uv_id: str) -> None:
    """Admin sends /connect <client_chat_id> <UltraViewer_ID> after setup is ready."""
    with _sessions_lock:
        sess = _sessions.get(client_id)

    if sess is None:
        send_message(admin_chat_id, f"No active session for chat_id {client_id}.", parse_mode=None)
        return
    if sess.stage != _Stage.AWAITING_ADMIN_READY:
        send_message(
            admin_chat_id,
            f"Client {client_id} is not waiting for setup (stage: {sess.stage.value}).",
            parse_mode=None,
        )
        return

    sess.stage = _Stage.AWAITING_FILES
    sess.last_scan_ts = time.time()

    mid = _send_btn(
        client_id,
        f"You're in. Connect to UltraViewer using ID: {uv_id}\n\n"
        "Once connected, start by downloading your assessment guidelines and any general "
        "materials provided — PDFs, instruction sheets, anything on the download page. "
        "When your downloads are done, tap Files Ready.",
        [[("Files Ready", "files_done"), ("No Files", "no_files")]],
    )
    if mid:
        sess.msg_id = mid

    send_message(admin_chat_id, f"Client {client_id} notified. Session is live.", parse_mode=None)


def _handle_test_screen(chat_id: int, sess: _Session) -> None:
    """Screenshot + plain description — lets client verify Corvus can see the screen."""
    resp = send_message(chat_id, "Let me take a look at your screen...", parse_mode=None)
    msg_id: int | None = None
    if resp.get("ok"):
        msg_id = resp["result"]["message_id"]

    def _update(acc: str) -> None:
        if msg_id:
            preview = acc[-_TG_MAX:] if len(acc) > _TG_MAX else acc
            try:
                edit_message(chat_id, msg_id, preview, parse_mode=None)
            except Exception:
                pass

    prompt_text = (
        "Take a screenshot and describe what you can see on the screen right now. "
        "Mention the browser, the website or page name, any visible text, forms, or images. "
        "This is a screen verification check — just describe what's there clearly."
    )
    with _claude_lock:
        prompt = _build_prompt(chat_id, prompt_text)
        try:
            if _STREAM_MODE:
                final = _ask_claude_stream(prompt, _update, screen=True)
            else:
                final = _ask_claude_plain(prompt, screen=True)
        except subprocess.TimeoutExpired:
            if msg_id:
                try:
                    edit_message(chat_id, msg_id, "Timed out — try again.", parse_mode=None)
                except Exception:
                    pass
            _show_welcome(chat_id, sess)
            return
        except Exception as exc:
            log.exception("Screen test failed")
            if msg_id:
                try:
                    edit_message(chat_id, msg_id, f"Screen test failed: {exc}", parse_mode=None)
                except Exception:
                    pass
            _show_welcome(chat_id, sess)
            return

    if msg_id and len(final) <= _TG_MAX:
        try:
            edit_message(chat_id, msg_id, final, parse_mode=None)
        except Exception:
            _send_chunks(chat_id, final)
    else:
        _send_chunks(chat_id, final)

    _show_welcome(chat_id, sess)


def _handle_files_step(chat_id: int, sess: _Session, has_files: bool) -> None:
    if has_files:
        new_files = _find_recent_downloads(since=sess.last_scan_ts)
        if new_files:
            existing = set(sess.files)
            sess.files.extend(f for f in new_files if f not in existing)
            names = ", ".join(Path(f).name for f in new_files[:5])
            extra = f" (+{len(new_files)-5} more)" if len(new_files) > 5 else ""
            file_info = f"Got it — picked up {len(new_files)} file(s): {names}{extra}"
        else:
            file_info = "No new files found in Downloads — I'll work from the screen."
        sess.last_scan_ts = time.time()
    else:
        file_info = "No files — I'll answer from what's on screen."

    sess.stage = _Stage.IN_SESSION
    mid = _send_btn(
        chat_id,
        f"{file_info}\n\n"
        "Navigate to your first question. If the question includes any files — "
        "audio clips or video — download those now as well. "
        "Then scroll slowly through the page from top to bottom, "
        "and tap I'm Ready when you're on your question. "
        "If the question has no file to download, tap No Attachment instead.",
        [
            [("I'm Ready", "answer_q"), ("No Attachment", "answer_q_noatt")],
            [("End Session", "done_session")],
        ],
    )
    if mid:
        sess.msg_id = mid


def _show_in_session_buttons(chat_id: int, sess: _Session) -> None:
    mid = _send_btn(
        chat_id,
        "For your next question: navigate to it, download any question files if there are any, "
        "scroll slowly through the page, then tap I'm Ready. "
        "If it has no file, tap No Attachment. Tap Explanation to understand the last answer.",
        [
            [("I'm Ready", "answer_q"), ("No Attachment", "answer_q_noatt")],
            [("Explanation", "explain"), ("End Session", "done_session")],
        ],
    )
    if mid:
        sess.msg_id = mid


def _handle_answer_question(chat_id: int, sess: _Session, no_attachment: bool = False) -> None:
    sess.question_count += 1
    qn = sess.question_count

    if no_attachment:
        # Client explicitly said this question has no downloadable file — don't
        # scan Downloads or imply a file is expected.
        sess.last_scan_ts = time.time()
    else:
        # Scan for files added since last scan (question-specific audio/video/docs)
        new_files = _find_recent_downloads(since=sess.last_scan_ts)
        if new_files:
            existing = set(sess.files)
            added = [f for f in new_files if f not in existing]
            sess.files.extend(added)
            if added:
                names = ", ".join(Path(f).name for f in added[:3])
                send_message(chat_id, f"New file(s) picked up: {names}", parse_mode=None)
        sess.last_scan_ts = time.time()

    # Tell the client to scroll — natural language, no mention of capture
    send_message(
        chat_id,
        "Got it — scroll through the page now, nice and steady.",
        parse_mode=None,
    )

    # Build context: instruct Claude to take periodic screenshots during the scroll window
    ctx_lines = [f"[Question #{qn}]"]

    # #11: give Claude the answers to earlier questions THIS session so it can
    # keep a later question consistent with one it already answered.
    if sess.qa_history:
        ctx_lines.append("<previous_questions_this_session>")
        for prev_qn, prev_ans in sess.qa_history[-5:]:
            snippet = prev_ans if len(prev_ans) <= 600 else prev_ans[:600] + " …"
            ctx_lines.append(f"[Q#{prev_qn} answer] {snippet}")
        ctx_lines.append("</previous_questions_this_session>")
        ctx_lines.append(
            "If this question refers back to, builds on, or reuses anything from a "
            "previous question above (e.g. 'as in the previous question', a shared "
            "scenario, a running case study, or defined terms), stay consistent with "
            "those earlier answers. Otherwise answer it independently."
        )

    if no_attachment:
        ctx_lines.append(
            "The client indicated this question has NO attachment — answer from the "
            "on-screen content only; do not wait for or expect a downloaded file."
        )
    if sess.files:
        ctx_lines.append(
            "Assessment files collected this session (guidelines + any question files): "
            + ", ".join(Path(f).name for f in sess.files)
        )
    ctx_lines.append(
        "The client is currently scrolling through the assessment page. "
        "Capture the full page content by taking 7 screenshots spaced 2 seconds apart:\n"
        "  1. Call mcp__capture__screenshot and record the path\n"
        "  2. Call Bash: python -c \"import time; time.sleep(2)\"\n"
        "  Repeat steps 1 and 2 seven times total (7 screenshots, ~14 seconds of scroll).\n"
        "Then call mcp__gemini__analyse_image on each screenshot path in sequence.\n"
        "Combine everything you see across all frames plus any listed assessment files "
        "as a single unified context.\n\n"
        "Answer the question visible in the screenshots.\n"
        "Format your response in TWO clearly separated parts:\n\n"
        "ANSWER\n"
        "Give the direct answer. For multiple-choice state the correct option and why, briefly. "
        "For written answers give the complete response the student should submit.\n\n"
        "---\n"
        "KEY NOTES\n"
        "3-5 bullets max:\n"
        "- Any mandatory keywords or exact phrases that must appear\n"
        "- What can be paraphrased vs. what must be kept verbatim\n"
        "- Why this is correct based on visible instructions or examples\n"
        "- Any tricky parts or common mistakes to avoid"
    )
    prompt_text = "\n".join(ctx_lines)

    # Send "working" message
    resp = send_message(chat_id, f"Reading question {qn}...", parse_mode=None)
    msg_id: int | None = None
    if resp.get("ok"):
        msg_id = resp["result"]["message_id"]

    def _update(acc: str) -> None:
        if msg_id:
            preview = acc[-_TG_MAX:] if len(acc) > _TG_MAX else acc
            try:
                edit_message(chat_id, msg_id, preview, parse_mode=None)
            except Exception:
                pass

    with _claude_lock:
        prompt = _build_prompt(chat_id, prompt_text)
        try:
            if _STREAM_MODE:
                final = _ask_claude_stream(prompt, _update, screen=True)
            else:
                final = _ask_claude_plain(prompt, screen=True)
        except subprocess.TimeoutExpired:
            if msg_id:
                try:
                    edit_message(
                        chat_id, msg_id,
                        f"Sorry, that took too long ({_TIMEOUT}s). Tap I'm Ready to try again.",
                        parse_mode=None,
                    )
                except Exception:
                    pass
            _show_in_session_buttons(chat_id, sess)
            return
        except Exception as exc:
            log.exception("Assessment Claude call failed")
            send_message(chat_id, f"Error: {exc}", parse_mode=None)
            _show_in_session_buttons(chat_id, sess)
            return
        # Cross-session `turns` history: store a COMPACT label as the user turn,
        # never the full prompt_text (which contains this question's entire
        # 7-screenshot analysis). Storing the raw prompt_text would re-inject every
        # prior question's screen analysis into <conversation_history> on every
        # later prompt — huge bloat, higher latency, and diluted focus. The answer
        # (`final`) is the part worth remembering long-term; rich in-session
        # continuity is carried separately by sess.qa_history below.
        _record_turn(chat_id, f"[Assessment question #{qn}]", final)
        sess.last_prompt = prompt_text
        sess.last_answer = final
        # #11: remember this Q&A so a later related question stays consistent.
        sess.qa_history.append((qn, final))

    if msg_id and len(final) <= _TG_MAX:
        try:
            edit_message(chat_id, msg_id, final, parse_mode=None)
        except Exception:
            _send_chunks(chat_id, final)
    else:
        _send_chunks(chat_id, final)

    _show_in_session_buttons(chat_id, sess)


def _handle_more_details(chat_id: int, sess: _Session) -> None:
    """Ask Claude to elaborate on the last answer without taking a new screenshot."""
    if not sess.last_answer:
        send_message(chat_id, "No previous answer to elaborate on — tap I'm Ready first.", parse_mode=None)
        _show_in_session_buttons(chat_id, sess)
        return

    resp = send_message(chat_id, "Let me expand on that...", parse_mode=None)
    msg_id: int | None = None
    if resp.get("ok"):
        msg_id = resp["result"]["message_id"]

    def _update(acc: str) -> None:
        if msg_id:
            preview = acc[-_TG_MAX:] if len(acc) > _TG_MAX else acc
            try:
                edit_message(chat_id, msg_id, preview, parse_mode=None)
            except Exception:
                pass

    elaboration_prompt = (
        f"My previous answer was:\n{sess.last_answer}\n\n"
        "Explain this answer so the student understands WHY it is correct. Cover:\n"
        "- The reasoning: why each part of the answer is right, tied to the "
        "question and the assessment's actual instructions/guidelines.\n"
        "- Supporting evidence or examples from the material.\n\n"
        "Follow the assessment's guidelines and instructions strictly, to the letter — "
        "do not add, soften, or override anything they say. If the KEY NOTES section "
        "of the previous answer above already identifies mandatory keywords, exact "
        "phrases, or verbatim requirements, honor those exactly as stated in your "
        "explanation. Do not invent additional requirements that aren't grounded in "
        "the guidelines or the previous answer."
    )
    with _claude_lock:
        prompt = _build_prompt(chat_id, elaboration_prompt)
        try:
            if _STREAM_MODE:
                final = _ask_claude_stream(prompt, _update, screen=False)
            else:
                final = _ask_claude_plain(prompt, screen=False)
        except subprocess.TimeoutExpired:
            if msg_id:
                try:
                    edit_message(chat_id, msg_id, "Timed out — please try again.", parse_mode=None)
                except Exception:
                    pass
            _show_in_session_buttons(chat_id, sess)
            return
        except Exception as exc:
            log.exception("More details call failed")
            if msg_id:
                try:
                    edit_message(chat_id, msg_id, f"Error: {exc}", parse_mode=None)
                except Exception:
                    pass
            _show_in_session_buttons(chat_id, sess)
            return
        # Compact label for the same reason as the answer flow — don't store the
        # elaboration_prompt (it embeds the full previous answer).
        _record_turn(chat_id, "[Explanation of the previous answer]", final)

    if msg_id and len(final) <= _TG_MAX:
        try:
            edit_message(chat_id, msg_id, final, parse_mode=None)
        except Exception:
            _send_chunks(chat_id, final)
    else:
        _send_chunks(chat_id, final)

    _show_in_session_buttons(chat_id, sess)


def _handle_done_session(chat_id: int, sess: _Session) -> None:
    count = sess.question_count
    sess.stage = _Stage.IDLE
    sess.files = []
    sess.question_count = 0
    sess.last_scan_ts = 0.0
    sess.last_prompt = ""
    sess.last_answer = ""
    sess.browser_type = ""
    sess.qa_history = []
    mid = _send_btn(
        chat_id,
        f"Session complete — {count} question(s) answered.\n\n"
        "Tap Start Assessment whenever you're ready for another.",
        [[("Start Assessment", "start_assessment")]],
    )
    if mid:
        sess.msg_id = mid


def _show_client_prompt(chat_id: int, sess: _Session) -> None:
    """Called when a client sends free text — guide them back to buttons."""
    if sess.stage == _Stage.IDLE:
        _show_welcome(chat_id, sess)
    elif sess.stage == _Stage.SELECTING_BROWSER:
        _show_browser_picker(chat_id, sess)
    elif sess.stage == _Stage.AWAITING_ADMIN_READY:
        client_send_message(
            chat_id,
            "Your session is still being set up — hang tight, you'll be notified when it's ready.",
            parse_mode=None,
        )
    elif sess.stage == _Stage.AWAITING_FILES:
        mid = _send_btn(
            chat_id,
            "Download your assessment guidelines and any general materials, then tap Files Ready.",
            [[("Files Ready", "files_done"), ("No Files", "no_files")]],
        )
        if mid:
            sess.msg_id = mid
    elif sess.stage == _Stage.IN_SESSION:
        _show_in_session_buttons(chat_id, sess)


# ── Claude invocation ─────────────────────────────────────────────────────────

_CLAUDE_TOOLS_SCREEN = "Read,Edit,Bash,Glob,Grep,Write,mcp__capture__screenshot,mcp__gemini__analyse_image"
_CLAUDE_BIN = r"C:\Users\Mike\AppData\Roaming\npm\claude.cmd"
_MCP_CONFIG = str(ROOT / "bridge_mcp.json")

# Fast model: no extended thinking, no MCP overhead → ~5-15s for conversational
# Smart model: extended thinking + MCPs → used only for screen-reading queries
_MODEL_FAST  = "claude-haiku-4-5-20251001"
_MODEL_SMART = "claude-sonnet-4-6"

_SCREEN_KEYWORDS = (
    "screen", "on screen", "what's on", "what is on", "visible",
    "screenshot", "browser", "window", "show me", "what do you see",
    "can you see", "look at", "what can", "what's visible",
)


def _needs_screen(text: str) -> bool:
    lower = text.lower()
    return any(k in lower for k in _SCREEN_KEYWORDS)


def _subprocess_env() -> dict:
    """Force claude CLI into subscription (OAuth) mode, no CLAUDE.md."""
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""       # force OAuth/subscription mode
    env["CLAUDE_CODE_SIMPLE"] = "1"     # disable CLAUDE.md auto-discovery
    return env


def _startupinfo() -> subprocess.STARTUPINFO:
    """Return STARTUPINFO that hides the console window on Windows.

    CREATE_NO_WINDOW alone doesn't always suppress .cmd wrappers; SW_HIDE
    covers the remaining cases (batch file child processes, etc.).
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


# Natural-language status messages — initial reply sent immediately.
# Phrased as first-person narration; no technical terms ever mentioned.
_STATUS_SCREEN = [
    "Let me check the screen...",
    "Let me have a look at what's on screen...",
    "Let me see what's visible right now...",
    "Hang on while I take a look...",
]
_STATUS_CHAT = [
    "On it...",
    "Let me think about that...",
    "Give me a moment...",
    "Hang tight, I'm looking into that...",
]

# Progressive narration shown every ~8s during the cold-start gap,
# until real response tokens start arriving. Keeps the user in the loop
# without exposing anything about what the system is actually doing.
_PROGRESS_SCREEN = [
    "Taking a closer look...",
    "Reading through what I can see...",
    "Still scanning, nearly there...",
    "Just about done...",
]
_PROGRESS_CHAT = [
    "Thinking through that for you...",
    "Still with you, working on it...",
    "Almost ready...",
    "Just a few more seconds...",
]

_status_counter = 0
_status_lock = threading.Lock()


def _thinking_message(screen: bool) -> str:
    global _status_counter
    pool = _STATUS_SCREEN if screen else _STATUS_CHAT
    with _status_lock:
        idx = _status_counter % len(pool)
        _status_counter += 1
    return pool[idx]


_CLI_ERROR_SIGNATURES = (
    "not logged in",
    "please run /login",
    "credit balance is too low",
    "connectors are disabled",
    "invalid api key",
    "authentication_error",
)


def _is_cli_error_text(text: str) -> bool:
    """True if text looks like a raw Claude CLI auth/billing diagnostic rather
    than a real answer — these must never be forwarded to chat verbatim."""
    lowered = text.lower()
    return any(sig in lowered for sig in _CLI_ERROR_SIGNATURES)


def _ask_claude_plain(prompt: str, screen: bool = False) -> str:
    model = _MODEL_SMART if screen else _MODEL_FAST
    cmd = [
        _CLAUDE_BIN, "--print",
        "--output-format", "json",
        "--model", model,
        "--permission-mode", "acceptEdits",
        "--system-prompt", _SYSTEM_PROMPT,  # replaces default (incl. any CLAUDE.md)
    ]
    if screen:
        cmd += [
            "--allowedTools", _CLAUDE_TOOLS_SCREEN,
            "--mcp-config", _MCP_CONFIG,
            "--strict-mcp-config",
        ]
    else:
        cmd += ["--allowedTools", "none"]  # no tools — answer from prompt only
    # Prompt goes via stdin, not as a positional arg — a long prompt (e.g. the
    # elaboration flow embeds the full previous answer) combined with the
    # multi-KB --system-prompt can push the total command line past the
    # ~8191-char limit the claude.cmd shim inherits from cmd.exe, which drops
    # the final argument and makes the CLI exit with "Input must be provided
    # either through stdin or as a prompt argument". Stdin has no such limit.

    result = subprocess.run(
        cmd,
        cwd=_CWD,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_TIMEOUT,
        env=_subprocess_env(),
        startupinfo=_startupinfo(),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    raw = (result.stdout or "").strip()
    if not raw:
        err = (result.stderr or "").strip()
        log.warning("Claude empty stdout (exit %d); stderr: %.300s", result.returncode, err)
        return "I didn't get a response — please tap the button to try again."
    if result.returncode != 0 or _is_cli_error_text(raw):
        err = (result.stderr or "").strip()
        log.error("Claude CLI auth/billing failure (exit %d); stdout: %.300s; stderr: %.300s",
                   result.returncode, raw[:300], err[:300])
        return "I didn't get a response — please tap the button to try again."
    try:
        data = json.loads(raw)
        answer = (data.get("result") or data.get("text") or raw).strip()
    except json.JSONDecodeError:
        answer = raw.strip()
    if _is_cli_error_text(answer):
        log.error("Claude CLI auth/billing failure leaked into result text: %.300s", answer[:300])
        return "I didn't get a response — please tap the button to try again."
    return answer


def _ask_claude_stream(prompt: str, on_update: "Callable[[str], None]",
                       screen: bool = False) -> str:
    model = _MODEL_SMART if screen else _MODEL_FAST
    cmd = [
        _CLAUDE_BIN, "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", model,
        "--permission-mode", "acceptEdits",
        "--system-prompt", _SYSTEM_PROMPT,  # replaces default (incl. any CLAUDE.md)
    ]
    if screen:
        cmd += [
            "--allowedTools", _CLAUDE_TOOLS_SCREEN,
            "--mcp-config", _MCP_CONFIG,
            "--strict-mcp-config",
        ]
    else:
        cmd += ["--allowedTools", "none"]  # no tools — answer from prompt only
    # Prompt goes via stdin, not as a positional arg — see _ask_claude_plain
    # for why (cmd.exe's ~8191-char command-line limit vs. the multi-KB
    # --system-prompt plus a potentially large prompt).

    proc = subprocess.Popen(
        cmd,
        cwd=_CWD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_subprocess_env(),
        startupinfo=_startupinfo(),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    accumulated = ""
    last_update = 0.0
    final_result = ""
    _did_timeout = threading.Event()
    stdout_q: "_queue.Queue[str | None]" = _queue.Queue()
    stderr_lines: list[str] = []

    def _writer() -> None:
        # Runs in its own thread so a large prompt can't deadlock against a
        # child that isn't draining stdin yet while we're also trying to
        # drain its stdout/stderr.
        try:
            proc.stdin.write(prompt)  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass

    def _reader() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                stdout_q.put(line)
        except Exception:
            pass
        stdout_q.put(None)  # EOF sentinel

    def _reader_stderr() -> None:
        # Must drain stderr concurrently with stdout, or the child blocks on a
        # full pipe buffer (Windows default 64KB) whenever it writes enough
        # diagnostic output there — this manifests as an intermittent hang
        # that looks unrelated to any flag or prompt content.
        try:
            for line in proc.stderr:  # type: ignore[union-attr]
                stderr_lines.append(line)
        except Exception:
            pass

    def _kill_on_timeout() -> None:
        _did_timeout.set()
        try:
            import psutil
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
        except Exception:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5,
                )
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        stdout_q.put(None)  # unblock main loop if kill succeeded

    threading.Thread(target=_writer, daemon=True).start()
    threading.Thread(target=_reader, daemon=True).start()
    threading.Thread(target=_reader_stderr, daemon=True).start()
    kill_timer = threading.Timer(_TIMEOUT, _kill_on_timeout)
    kill_timer.start()

    # Hard deadline: _TIMEOUT + 10s grace so this function ALWAYS returns
    # even if the process tree kill fails and node.exe stays alive.
    deadline = time.monotonic() + _TIMEOUT + 10

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw_line = stdout_q.get(timeout=min(remaining, 1.0))
            except _queue.Empty:
                continue
            if raw_line is None:
                break  # EOF or kill sentinel
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
        kill_timer.cancel()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # process tree already killed by _kill_on_timeout; don't block

    if _did_timeout.is_set():
        stderr_text = "".join(stderr_lines).strip()
        if stderr_text:
            log.warning("Claude stream timed out; stderr was:\n%s", stderr_text)
        raise subprocess.TimeoutExpired("claude", _TIMEOUT)

    result_text = final_result or accumulated.strip()
    if result_text and _is_cli_error_text(result_text):
        stderr_text = "".join(stderr_lines).strip()
        log.warning(
            "Claude stream returned CLI auth/billing diagnostic (did_timeout=%s, exit=%s); "
            "result_text: %.300s; stderr:\n%s",
            _did_timeout.is_set(), proc.returncode, result_text[:300], stderr_text or "(empty)",
        )
        return "I didn't get a response — please tap the button to try again."
    if not result_text:
        stderr_text = "".join(stderr_lines).strip()
        log.warning(
            "Claude stream returned no output (did_timeout=%s, exit=%s); stderr:\n%s",
            _did_timeout.is_set(), proc.returncode, stderr_text or "(empty)",
        )
        return "I didn't get a response — please tap the button to try again."
    return result_text


# ── Message handlers ──────────────────────────────────────────────────────────


def _handle_plain(chat_id: int, user_text: str) -> None:
    screen = _needs_screen(user_text)
    send_message(chat_id, _thinking_message(screen), parse_mode=None)
    with _claude_lock:
        prompt = _build_prompt(chat_id, user_text)
        try:
            answer = _ask_claude_plain(prompt, screen=screen)
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
    screen = _needs_screen(user_text)
    resp = send_message(chat_id, _thinking_message(screen), parse_mode=None)
    msg_id: int | None = None
    if resp.get("ok"):
        msg_id = resp["result"]["message_id"]

    last_sent = [""]
    last_sent_lock = threading.Lock()
    stream_started = threading.Event()  # set when first real token arrives

    # Background ticker: updates the status message every 8s during the
    # Node.js cold-start gap so the user sees the system actively working.
    # Stops itself the moment real response tokens begin streaming.
    def _status_ticker() -> None:
        if msg_id is None:
            return
        pool = _PROGRESS_SCREEN if screen else _PROGRESS_CHAT
        for phrase in pool:
            if stream_started.wait(timeout=8):
                return  # tokens arrived — hand off to _update
            try:
                edit_message(chat_id, msg_id, phrase, parse_mode=None)
                with last_sent_lock:
                    last_sent[0] = phrase
            except Exception:
                pass

    threading.Thread(target=_status_ticker, daemon=True).start()

    def _update(accumulated: str) -> None:
        stream_started.set()  # cancel the ticker
        with last_sent_lock:
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
            final = _ask_claude_stream(prompt, _update, screen=screen)
        except subprocess.TimeoutExpired:
            stream_started.set()
            if msg_id:
                try:
                    edit_message(chat_id, msg_id, "Sorry, that took too long. Please try again.", parse_mode=None)
                except Exception:
                    pass
            send_message(chat_id, f"Sorry, that took too long ({_TIMEOUT}s). Please try again.", parse_mode=None)
            return
        except Exception as exc:
            stream_started.set()
            log.exception("Claude stream failed")
            if msg_id:
                edit_message(chat_id, msg_id, f"Error: {exc}", parse_mode=None)
            return
        _record_turn(chat_id, user_text, final)

    stream_started.set()
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
    if chat_id not in _ADMINS or chat_id in _test_client_ids:
        # Admin test-mode escapes: these commands always route to admin handler
        # even while the admin is in test-client mode.
        if chat_id in _ADMINS:
            _lower_chk = text.lower()
            if _lower_chk == "/stopclienttest":
                _test_client_ids.discard(chat_id)
                with _sessions_lock:
                    _sessions.pop(chat_id, None)
                send_message(chat_id, "Test-client mode OFF. Back to admin mode.", parse_mode=None)
                return
            if _lower_chk == "/testclient":
                _test_client_ids.add(chat_id)
                with _sessions_lock:
                    _sessions[chat_id] = _Session(chat_id=chat_id)
                send_message(chat_id, "Test-client mode ON. You are now in the client flow. Send any text to begin.", parse_mode=None)
                return
            if _lower_chk.startswith("/connect "):
                # Fall through to admin handler so /connect works in test-client mode
                pass
            else:
                log.info("client chat=%s  %r", chat_id, text[:60])

                with _sessions_lock:
                    sess = _sessions.get(chat_id)
                    if sess is None:
                        sess = _Session(chat_id=chat_id)
                        _sessions[chat_id] = sess
                    if username and not sess.username:
                        sess.username = username

                _show_client_prompt(chat_id, sess)
                return
        else:
            log.info("client chat=%s  %r", chat_id, text[:60])

            with _sessions_lock:
                sess = _sessions.get(chat_id)
                if sess is None:
                    sess = _Session(chat_id=chat_id)
                    _sessions[chat_id] = sess
                if username and not sess.username:
                    sess.username = username

            # Notify admin on first contact
            with _db_lock:
                is_new = _db.execute(
                    "SELECT 1 FROM turns WHERE chat_id=? LIMIT 1", (chat_id,)
                ).fetchone() is None
            if is_new:
                user_str = f"@{username}" if username else str(chat_id)
                for admin_id in _ADMINS:
                    try:
                        send_message(
                            admin_id,
                            f"New client: {user_str}  (chat_id: {chat_id})\nFirst message: {text[:200]}",
                            parse_mode=None,
                        )
                    except Exception:
                        pass

            _show_client_prompt(chat_id, sess)
            return

    log.info("chat=%s  %r", chat_id, text[:80])

    lower = text.lower()

    # ── Admin: /connect <client_chat_id> <UltraViewer_ID> ────────────────────
    if lower.startswith("/connect "):
        parts = text.split()
        if len(parts) >= 3 and parts[1].isdigit():
            _handle_connect(chat_id, int(parts[1]), parts[2])
        else:
            send_message(chat_id, "Usage: /connect <client_chat_id> <UltraViewer_ID>", parse_mode=None)
        return

    # ── Admin shortcut: start <chat_id> [platform] ───────────────────────────
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
        with _db_lock:
            _db.execute("DELETE FROM turns WHERE chat_id=?", (chat_id,))
            _db.commit()
        send_message(chat_id, "Conversation history cleared. Memories retained.", parse_mode=None)
        return

    # ── Memory commands ────────────────────────────────────────────────────────

    if lower in ("/memory", "memory", "/memories"):
        with _db_lock:
            rows = _db.execute(
                "SELECT key, value FROM memories WHERE chat_id=? ORDER BY updated",
                (chat_id,),
            ).fetchall()
        if rows:
            lines = [f"  {r['key']}: {r['value']}" for r in rows]
            send_message(chat_id, "Stored memories:\n" + "\n".join(lines), parse_mode=None)
        else:
            send_message(chat_id, "No memories stored yet.\nUse /remember key: value to add one.", parse_mode=None)
        return

    if lower.startswith("/remember "):
        rest = text[len("/remember "):].strip()
        if ":" in rest:
            key, _, value = rest.partition(":")
            key, value = key.strip(), value.strip()
            with _db_lock:
                _db.execute(
                    "INSERT OR REPLACE INTO memories(chat_id,key,value,updated) VALUES(?,?,?,?)",
                    (chat_id, key, value, time.time()),
                )
                _db.commit()
            send_message(chat_id, f"Remembered: {key} = {value}", parse_mode=None)
        else:
            send_message(chat_id, "Usage: /remember key: value\nExample: /remember name: Alex", parse_mode=None)
        return

    if lower.startswith("/forget "):
        key = text[len("/forget "):].strip()
        with _db_lock:
            cur = _db.execute("DELETE FROM memories WHERE chat_id=? AND key=?", (chat_id, key))
            _db.commit()
        if cur.rowcount:
            send_message(chat_id, f"Forgotten: {key}", parse_mode=None)
        else:
            send_message(chat_id, f"No memory found for: {key}", parse_mode=None)
        return

    if lower == "/testclient":
        _test_client_ids.add(chat_id)
        with _sessions_lock:
            _sessions[chat_id] = _Session(chat_id=chat_id)
        send_message(chat_id, "Test-client mode ON. You are now in the client flow. Send any text to begin.", parse_mode=None)
        return

    if lower == "/stopclienttest":
        _test_client_ids.discard(chat_id)
        with _sessions_lock:
            _sessions.pop(chat_id, None)
        send_message(chat_id, "Test-client mode OFF. Back to admin mode.", parse_mode=None)
        return

    if lower in ("/help", "help"):
        send_message(
            chat_id,
            "Corvus Admin Commands\n\n"
            "Client session:\n"
            "  /connect <chat_id> <UV_ID>  — release client to UltraViewer session\n"
            "  start <chat_id>             — start client session via master\n"
            "  start <chat_id> <platform>  — start on named platform\n"
            "  end session <session_id>    — end a session\n"
            "  pool / sessions             — show active sessions\n\n"
            "Conversation:\n"
            "  /clearhistory  — clear chat turns (memories kept)\n"
            "  /memory        — list stored memories\n"
            "  /remember key: value — store a persistent memory\n"
            "  /forget key    — delete a stored memory\n\n"
            "Bridge:\n"
            "  /ping          — health check\n"
            "  /testclient    — enter client button-flow for testing\n"
            "  /stopclienttest — return to admin mode\n"
            "  /help          — this message\n\n"
            f"Mode: {'streaming' if _STREAM_MODE else 'plain'}  |  Timeout: {_TIMEOUT}s\n"
            f"Models: chat={_MODEL_FAST}  screen={_MODEL_SMART}\n"
            f"CWD: {_CWD}",
            parse_mode=None,
        )
        return

    if _STREAM_MODE:
        _handle_stream(chat_id, text)
    else:
        _handle_plain(chat_id, text)


# ── Callback dispatcher ───────────────────────────────────────────────────────


def _handle_callback(cq: dict) -> None:
    """Dispatch inline button taps from non-admin (client) users."""
    chat_id: int = cq.get("from", {}).get("id", 0) or cq.get("message", {}).get("chat", {}).get("id", 0)
    data: str = cq.get("data", "")
    cq_id: str = cq.get("id", "")

    if not chat_id or not data:
        return
    if chat_id in _ADMINS and chat_id not in _test_client_ids:
        return  # admin callbacks are handled elsewhere (MCP tools)

    log.info("client callback chat=%s data=%r", chat_id, data)

    # Acknowledge the tap immediately so Telegram removes the spinner
    try:
        answer_callback(cq_id)
    except Exception:
        pass

    with _sessions_lock:
        sess = _sessions.get(chat_id)
        if sess is None:
            sess = _Session(chat_id=chat_id)
            _sessions[chat_id] = sess

    # Capture username from callback so it's available for admin notifications
    if not sess.username:
        sess.username = cq.get("from", {}).get("username", "")

    if data == "start_assessment":
        _show_browser_picker(chat_id, sess)

    elif data == "test_screen":
        threading.Thread(
            target=_handle_test_screen,
            args=(chat_id, sess),
            daemon=True,
        ).start()

    elif data in ("browser_morelogin", "browser_gologin",
                  "browser_adspower", "browser_ixbrowser"):
        _handle_browser_selected(chat_id, sess, data)

    elif data == "files_done":
        _handle_files_step(chat_id, sess, has_files=True)

    elif data == "no_files":
        _handle_files_step(chat_id, sess, has_files=False)

    elif data in ("answer_q", "answer_q_noatt"):
        if sess.stage == _Stage.IN_SESSION:
            threading.Thread(
                target=_handle_answer_question,
                args=(chat_id, sess),
                kwargs={"no_attachment": data == "answer_q_noatt"},
                daemon=True,
            ).start()
        else:
            _show_welcome(chat_id, sess)

    # "explain" is the current button; "more_details" is kept as a backward-compat
    # alias for any older message still showing the old button.
    elif data in ("explain", "more_details"):
        if sess.stage == _Stage.IN_SESSION:
            threading.Thread(
                target=_handle_more_details,
                args=(chat_id, sess),
                daemon=True,
            ).start()
        else:
            _show_welcome(chat_id, sess)

    elif data == "done_session":
        _handle_done_session(chat_id, sess)

    else:
        log.warning("Unknown callback data=%r from chat=%s", data, chat_id)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    global _ADMINS
    _ADMINS = admin_chat_ids()
    mode = "streaming" if _STREAM_MODE else "plain"
    log.info("Bridge starting (%s mode) — admins: %s", mode, _ADMINS)
    log.info("Models: chat=%s  screen=%s", _MODEL_FAST, _MODEL_SMART)
    log.info("DB: %s", _DB_PATH)

    bus.start()

    # Prewarm: run a trivial Claude call so Node.js JIT is hot before the
    # first real client query. Runs in background; holds _claude_lock while
    # warming so real calls queue behind it rather than racing.
    def _prewarm() -> None:
        log.info("Prewarm: acquiring Claude lock...")
        with _claude_lock:
            try:
                log.info("Prewarm: calling Claude...")
                _ask_claude_plain("Reply with only the word ready.", screen=False)
                log.info("Prewarm: done — Node.js is warm.")
            except Exception as e:
                log.warning("Prewarm failed (non-fatal): %s", e)

    threading.Thread(target=_prewarm, daemon=True, name="prewarm").start()

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
            item = bus.listener_queue.get(timeout=5)
            if "_cq" in item:
                # Inline button tap — dispatch to callback handler
                threading.Thread(
                    target=_handle_callback, args=(item["_cq"],), daemon=True
                ).start()
            else:
                threading.Thread(target=_handle, args=(item,), daemon=True).start()
        except Exception:
            pass


if __name__ == "__main__":
    main()
