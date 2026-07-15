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
    admin_chat_ids, bus, send_message, edit_message,
    send_inline_keyboard, answer_callback,
)

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
    IDLE               = "idle"
    SELECTING_BROWSER  = "selecting_browser"
    SELECTING_TYPE     = "selecting_type"
    AWAITING_FILES     = "awaiting_files"
    IN_SESSION         = "in_session"


@dataclass
class _Session:
    chat_id: int
    stage: _Stage           = _Stage.IDLE
    assessment_type: str    = ""
    files: list[str]        = field(default_factory=list)
    last_scan_ts: float     = 0.0   # epoch of last Downloads scan
    question_count: int     = 0
    msg_id: int | None      = None  # current button-message id
    username: str           = ""    # Telegram username (for admin notifications)
    last_prompt: str        = ""    # prompt used for the last answered question
    last_answer: str        = ""    # Claude's answer to the last question
    browser_type: str       = ""    # antidetect browser the client selected


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


def _send_chunks(chat_id: int, text: str) -> None:
    """Send text as one or more plain Telegram messages (no HTML parse_mode)."""
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks):
        prefix = f"[{i+1}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        send_message(chat_id, prefix + chunk, parse_mode=None)


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
    resp = send_inline_keyboard(chat_id, text, rows)
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
        "Which antidetect browser are you using for this assessment?\n"
        "This lets us set up the correct browser profile for you.",
        [
            [("Multilogin", "browser_multilogin"), ("GoLogin", "browser_gologin")],
            [("AdsPower", "browser_adspower"), ("Dolphin Anty", "browser_dolphin")],
            [("Other / Not Sure", "browser_other")],
        ],
    )
    if mid:
        sess.msg_id = mid


def _handle_browser_selected(chat_id: int, sess: _Session, data: str) -> None:
    browser_names = {
        "browser_multilogin": "Multilogin",
        "browser_gologin":    "GoLogin",
        "browser_adspower":   "AdsPower",
        "browser_dolphin":    "Dolphin Anty",
        "browser_other":      "Other / Not specified",
    }
    sess.browser_type = browser_names.get(data, "Unknown")
    user_str = f"@{sess.username}" if sess.username else str(chat_id)
    for admin_id in _ADMINS:
        try:
            send_message(
                admin_id,
                f"Browser setup needed:\nClient: {user_str}  (chat_id: {chat_id})\n"
                f"Browser: {sess.browser_type}\nPlease set up the correct browser profile.",
                parse_mode=None,
            )
        except Exception:
            pass
    _show_type_picker(chat_id, sess)


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


def _show_type_picker(chat_id: int, sess: _Session) -> None:
    sess.stage = _Stage.SELECTING_TYPE
    mid = _send_btn(
        chat_id,
        "What type of assessment is this?",
        [
            [("Multiple Choice", "type_mc"), ("Written / Essay", "type_written")],
            [("Video / Audio Based", "type_media"), ("Mixed / Other", "type_mixed")],
        ],
    )
    if mid:
        sess.msg_id = mid


def _handle_type_selected(chat_id: int, sess: _Session, data: str) -> None:
    types = {
        "type_mc":      "Multiple Choice",
        "type_written": "Written / Essay",
        "type_media":   "Video / Audio Based",
        "type_mixed":   "Mixed / Other",
    }
    sess.assessment_type = types.get(data, "General")
    sess.stage = _Stage.AWAITING_FILES
    sess.last_scan_ts = time.time()
    mid = _send_btn(
        chat_id,
        f"Got it — {sess.assessment_type} assessment.\n\n"
        "Please download all files from the assessment page (PDFs, audio clips, videos, instruction sheets).\n"
        "Tap when done — or No Files if there's nothing to download.",
        [[("Files Downloaded", "files_done"), ("No Files", "no_files")]],
    )
    if mid:
        sess.msg_id = mid


def _handle_files_step(chat_id: int, sess: _Session, has_files: bool) -> None:
    if has_files:
        new_files = _find_recent_downloads(since=sess.last_scan_ts)
        if new_files:
            # Accumulate — don't overwrite; earlier files stay in the list
            existing = set(sess.files)
            sess.files.extend(f for f in new_files if f not in existing)
            names = ", ".join(Path(f).name for f in new_files[:5])
            extra = f" (+{len(new_files)-5} more)" if len(new_files) > 5 else ""
            file_info = f"Found {len(new_files)} new file(s): {names}{extra}"
        else:
            file_info = "No new files detected in Downloads — I'll rely on the screen."
        sess.last_scan_ts = time.time()
    else:
        file_info = "No files — I'll answer from what's on screen."

    sess.stage = _Stage.IN_SESSION
    mid = _send_btn(
        chat_id,
        f"{file_info}\n\nNavigate to the first question, then tap Answer Question.",
        [[("Answer Question", "answer_q")], [("Done", "done_session")]],
    )
    if mid:
        sess.msg_id = mid


def _show_in_session_buttons(chat_id: int, sess: _Session) -> None:
    mid = _send_btn(
        chat_id,
        "Move to the next question when ready, or choose an option below.",
        [
            [("Answer Next Question", "answer_q"), ("Answer Again", "answer_again")],
            [("More Details", "more_details"), ("Back", "go_back")],
            [("Done", "done_session")],
        ],
    )
    if mid:
        sess.msg_id = mid


def _handle_answer_question(chat_id: int, sess: _Session) -> None:
    sess.question_count += 1
    qn = sess.question_count

    # Scan for files added since last scan (e.g., audio for new question)
    new_files = _find_recent_downloads(since=sess.last_scan_ts)
    if new_files:
        existing = set(sess.files)
        added = [f for f in new_files if f not in existing]
        sess.files.extend(added)
        if added:
            names = ", ".join(Path(f).name for f in added[:3])
            send_message(chat_id, f"New file(s) detected: {names}", parse_mode=None)
    sess.last_scan_ts = time.time()

    # Build context prefix — all accumulated files are listed every time
    ctx_lines = [f"[Assessment: {sess.assessment_type}  |  Question #{qn}]"]
    if sess.files:
        ctx_lines.append(
            "Files available for this assessment (accumulated across all questions): "
            + ", ".join(Path(f).name for f in sess.files)
        )
    ctx_lines.append(
        "Take a screenshot and answer the question visible on screen. "
        "Be direct and specific. For multiple-choice, state the correct option clearly."
    )
    prompt_text = "\n".join(ctx_lines)

    # Send "working" message
    resp = send_message(chat_id, f"Looking at question {qn}...", parse_mode=None)
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
                        f"Sorry, that took too long ({_TIMEOUT}s). Try tapping Answer Question again.",
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
        _record_turn(chat_id, prompt_text, final)

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
    sess.assessment_type = ""
    sess.files = []
    sess.question_count = 0
    sess.last_scan_ts = 0.0
    mid = _send_btn(
        chat_id,
        f"Assessment complete! I answered {count} question(s).\n\n"
        "Tap Start Assessment whenever you're ready for a new one.",
        [[("Start Assessment", "start_assessment")]],
    )
    if mid:
        sess.msg_id = mid


def _show_client_prompt(chat_id: int, sess: _Session) -> None:
    """Called when a client sends free text — guide them back to buttons."""
    if sess.stage == _Stage.IDLE:
        _show_welcome(chat_id, sess)
    elif sess.stage == _Stage.SELECTING_TYPE:
        _show_type_picker(chat_id, sess)
    elif sess.stage == _Stage.AWAITING_FILES:
        mid = _send_btn(
            chat_id,
            "Waiting for you to download files from the assessment page. Tap when ready.",
            [[("Files Downloaded", "files_done"), ("No Files", "no_files")]],
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
    cmd.append(prompt)

    result = subprocess.run(
        cmd,
        cwd=_CWD,
        stdin=subprocess.DEVNULL,
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
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return f"Error (exit {result.returncode}):\n{err[:600]}" if err else \
               f"Claude exited with code {result.returncode}."
    try:
        data = json.loads(raw)
        return (data.get("result") or data.get("text") or raw).strip()
    except json.JSONDecodeError:
        return raw.strip()


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
    cmd.append(prompt)

    proc = subprocess.Popen(
        cmd,
        cwd=_CWD,
        stdin=subprocess.DEVNULL,
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

    def _reader() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                stdout_q.put(line)
        except Exception:
            pass
        stdout_q.put(None)  # EOF sentinel

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

    threading.Thread(target=_reader, daemon=True).start()
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
        raise subprocess.TimeoutExpired("claude", _TIMEOUT)

    result_text = final_result or accumulated.strip()
    if not result_text:
        log.warning("Claude stream returned no output (did_timeout=%s)", _did_timeout.is_set())
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
        log.info("client chat=%s  %r", chat_id, text[:60])

        with _sessions_lock:
            sess = _sessions.get(chat_id)
            if sess is None:
                sess = _Session(chat_id=chat_id)
                _sessions[chat_id] = sess

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

        # Any free-text from a client is nudged back to buttons
        _show_client_prompt(chat_id, sess)
        return

    log.info("chat=%s  %r", chat_id, text[:80])

    lower = text.lower()

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
            "Session:\n"
            "  start <chat_id>            — start client session\n"
            "  start <chat_id> <platform> — start on named platform\n"
            "  end session <session_id>   — end a session\n"
            "  pool / sessions            — show active sessions\n\n"
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

    if data == "start_assessment":
        _show_type_picker(chat_id, sess)

    elif data in ("type_mc", "type_written", "type_media", "type_mixed"):
        _handle_type_selected(chat_id, sess, data)

    elif data == "files_done":
        _handle_files_step(chat_id, sess, has_files=True)

    elif data == "no_files":
        _handle_files_step(chat_id, sess, has_files=False)

    elif data == "answer_q":
        if sess.stage == _Stage.IN_SESSION:
            threading.Thread(
                target=_handle_answer_question,
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
