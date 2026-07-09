"""
eye_daemon.py — Telegram-triggered "intelligent eye" for the CareerBridge desktop.

Send a natural language message to the bot. No start/stop buttons needed.

Supported intents (case-insensitive):
  "watch [for N min/sec]"         — record screen, auto-stop, analyze, reply
  "watch video and answer ..."    — same but carries the question as context
  "screenshot" / "look" / "see"  — instant MSS grab → inline Gemini → reply (~3s)
  "stop" / "done"                 — stop active recording early, analyze now
  "cancel"                        — stop recording without analyzing
  "status"                        — report current state

Speed strategy:
  Clips ≤ 20MB  → Part.from_bytes() inline  — zero upload wait (~5-8s after recording)
  Clips > 20MB  → File API upload + poll     (fallback for long recordings)

Usage:
  python eye_daemon.py
  Runs forever. Ctrl+C to stop.
"""
from __future__ import annotations

import io
import logging
import os
import re
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
    format="%(asctime)s [eye] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("eye")

from telegram_mcp._bot import admin_chat_ids, bus, send_message
from gemini_mcp._gemini import delete_file, get_all_keys, upload_video, analyse_video
from gemini_mcp._recorder import start_recording, stop_recording
from capture_mcp._backend_mss import available as mss_ok
from capture_mcp._backend_mss import capture as mss_capture

# ── Constants ─────────────────────────────────────────────────────────────────

_INLINE_LIMIT  = 20 * 1024 * 1024   # 20MB — above this use File API
_TMP_MP4       = "C:/tmp/eye_recording.mp4"
_DEFAULT_WATCH = 120.0               # seconds when no duration specified

# ── Gemini helpers ────────────────────────────────────────────────────────────


def _keys() -> list[str]:
    return get_all_keys()


def _gemini_inline(data: bytes, mime: str, prompt: str) -> str:
    """Send bytes (image or video) inline — no File API, no upload wait."""
    from google import genai
    from google.genai import types

    for key in _keys():
        try:
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=data, mime_type=mime),
                    prompt,
                ],
            )
            return (resp.text or "").strip()
        except Exception as exc:
            msg = str(exc)
            if any(x in msg for x in ("quota", "RESOURCE_EXHAUSTED", "429", "PerDay", "FreeTier")):
                log.warning("Key quota hit — trying next key")
                continue
            raise
    raise RuntimeError("All Gemini keys exhausted")


def _gemini_video(video_path: str, prompt: str) -> str:
    size = Path(video_path).stat().st_size
    if size <= _INLINE_LIMIT:
        log.info("Inline path (%.1fMB)", size / 1_048_576)
        return _gemini_inline(Path(video_path).read_bytes(), "video/mp4", prompt)

    log.info("File API path (%.1fMB)", size / 1_048_576)
    upload = upload_video(video_path)
    if "error" in upload:
        raise RuntimeError(upload["error"])
    try:
        result = analyse_video(upload["uri"], prompt)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["text"]
    finally:
        delete_file(upload["name"])


def _gemini_screenshot(png_bytes: bytes, prompt: str) -> str:
    # Convert PNG → JPEG for smaller payload, faster round-trip
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(png_bytes)).convert("RGB").save(buf, format="JPEG", quality=75)
    return _gemini_inline(buf.getvalue(), "image/jpeg", prompt)


# ── Intent parsing ────────────────────────────────────────────────────────────

_RE_WATCH      = re.compile(r"\b(watch|record|capture|monitor|observe|track)\b", re.I)
_RE_SCREENSHOT = re.compile(r"\b(screenshot|look|see|what.s on|describe|read|check|show|snap|grab|peek)\b", re.I)
_RE_STOP       = re.compile(r"\b(stop|done|finish|end|enough|analyze now|analyse now)\b", re.I)
_RE_CANCEL     = re.compile(r"\b(cancel|abort|nevermind|never mind)\b", re.I)
_RE_STATUS     = re.compile(r"\b(status|state|busy|running|what are you doing)\b", re.I)
_RE_DURATION   = re.compile(r"(?:for\s+)?(\d+(?:\.\d+)?)\s*(min(?:utes?)?|sec(?:onds?)?|s\b|m\b)", re.I)


def _classify(text: str) -> str:
    if _RE_CANCEL.search(text):  return "cancel"
    if _RE_STOP.search(text):    return "stop"
    if _RE_STATUS.search(text):  return "status"
    if _RE_WATCH.search(text):   return "watch"
    if _RE_SCREENSHOT.search(text): return "screenshot"
    return "unknown"


def _parse_duration(text: str) -> float:
    m = _RE_DURATION.search(text)
    if not m:
        return _DEFAULT_WATCH
    val = float(m.group(1))
    return val * 60 if m.group(2).lower().startswith("m") else val


# ── State ─────────────────────────────────────────────────────────────────────

class _State:
    def __init__(self):
        self._lock       = threading.Lock()
        self.recording   = False
        self.started_at  = 0.0
        self.prompt      = ""
        self.chat_id     = 0

    def start(self, chat_id: int, prompt: str) -> None:
        with self._lock:
            self.recording  = True
            self.started_at = time.monotonic()
            self.prompt     = prompt
            self.chat_id    = chat_id

    def stop(self) -> tuple[bool, int, str]:
        """Returns (was_recording, chat_id, prompt)."""
        with self._lock:
            was = self.recording
            cid, p = self.chat_id, self.prompt
            self.recording = False
            return was, cid, p

    def update_prompt(self, prompt: str) -> None:
        with self._lock:
            self.prompt = prompt

    @property
    def status_text(self) -> str:
        with self._lock:
            if not self.recording:
                return "Idle — ready."
            elapsed = time.monotonic() - self.started_at
            return f"Recording... {elapsed:.0f}s elapsed."


_state = _State()


# ── Actions ───────────────────────────────────────────────────────────────────

def _do_screenshot(chat_id: int, prompt: str) -> None:
    send_message(chat_id, "Grabbing screen...")
    try:
        if not mss_ok():
            send_message(chat_id, "MSS backend not available.")
            return
        png = mss_capture()
        t0 = time.monotonic()
        answer = _gemini_screenshot(
            png,
            prompt or "Describe everything visible on this screen in detail.",
        )
        ms = int((time.monotonic() - t0) * 1000)
        send_message(chat_id, f"<b>Screen ({ms}ms):</b>\n{answer}")
    except Exception as exc:
        log.exception("Screenshot failed")
        send_message(chat_id, f"Screenshot error: {exc}")


def _finish(chat_id: int, prompt: str) -> None:
    """Stop recorder, analyze, reply. Called from timer thread or explicit stop."""
    result = stop_recording(_TMP_MP4)
    if "error" in result:
        send_message(chat_id, f"Recording error: {result['error']}")
        return

    dur    = result.get("duration_s", 0)
    frames = result.get("frame_count", 0)
    size   = result.get("size_mb", 0)
    send_message(chat_id, f"Recorded {dur:.0f}s ({frames} frames, {size:.1f}MB). Analyzing...")

    try:
        t0     = time.monotonic()
        answer = _gemini_video(
            _TMP_MP4,
            prompt or (
                "Describe everything that happened in this screen recording. "
                "Include all visible text, actions, and important events."
            ),
        )
        ms = int((time.monotonic() - t0) * 1000)
        send_message(chat_id, f"<b>Analysis ({ms}ms):</b>\n{answer}")
    except Exception as exc:
        log.exception("Gemini analysis failed")
        send_message(chat_id, f"Analysis error: {exc}")


def _start_watch(chat_id: int, duration_s: float, prompt: str) -> None:
    was, _, _ = _state.stop()   # ensure clean state (idempotent if not recording)
    if was:
        send_message(chat_id, "Stopped previous recording.")

    result = start_recording(fps=5)
    if "error" in result:
        send_message(chat_id, f"Could not start recording: {result['error']}")
        return

    _state.start(chat_id, prompt)
    label = f"{duration_s:.0f}s" if duration_s < 60 else f"{duration_s/60:.1f}min"
    send_message(chat_id, f"Watching for {label}... Send 'stop' to finish early.")

    def _timer():
        time.sleep(duration_s)
        was_still, cid, p = _state.stop()
        if was_still:
            _finish(cid, p)

    threading.Thread(target=_timer, daemon=True).start()


# ── Dispatcher ────────────────────────────────────────────────────────────────

_ADMINS: list[int] = []


def _handle(msg: dict) -> None:
    chat_id = msg.get("chat", {}).get("id", 0)
    text    = (msg.get("text") or "").strip()
    if not text or chat_id not in _ADMINS:
        return

    intent = _classify(text)
    log.info("chat=%s  %r → %s", chat_id, text[:60], intent)

    if intent == "status":
        send_message(chat_id, _state.status_text)

    elif intent == "cancel":
        was, _, _ = _state.stop()
        if was:
            stop_recording(_TMP_MP4)
            send_message(chat_id, "Recording cancelled.")
        else:
            send_message(chat_id, "Not recording.")

    elif intent == "stop":
        was, cid, prompt = _state.stop()
        if was:
            threading.Thread(target=_finish, args=(cid, prompt), daemon=True).start()
        else:
            send_message(chat_id, "Not currently recording.")

    elif intent == "watch":
        duration_s = _parse_duration(text)
        threading.Thread(
            target=_start_watch, args=(chat_id, duration_s, text), daemon=True
        ).start()

    elif intent == "screenshot":
        threading.Thread(
            target=_do_screenshot, args=(chat_id, text), daemon=True
        ).start()

    else:
        # Unknown message while recording → treat as context/question update
        if _state.recording:
            _state.update_prompt(text)
            send_message(chat_id, "Context updated — I'll use that when analyzing.")
        else:
            # Default: screenshot + describe
            threading.Thread(
                target=_do_screenshot, args=(chat_id, text), daemon=True
            ).start()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global _ADMINS
    _ADMINS = admin_chat_ids()
    log.info("Eye daemon starting — admins: %s", _ADMINS)

    bus.start()
    for cid in _ADMINS:
        try:
            send_message(
                cid,
                "Eye online.\n"
                "Commands: <b>watch [for N min]</b>, <b>screenshot</b>, <b>stop</b>, <b>cancel</b>, <b>status</b>",
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
