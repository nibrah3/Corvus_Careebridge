"""
_session_bot.py — Telegram Bot API wrapper + live UpdateBus for Passerclue
(the client-facing bot, SESSION_BOT_TOKEN).

Sibling of _bot.py (which is bound to TELEGRAM_BOT_TOKEN / Cyhub, the
admin-facing bot). Kept as a separate module — rather than parameterizing
_bot.py — so the two bot identities stay fully independent: separate
long-poll threads, separate offsets, separate waiter/queue state, zero
shared mutable state between admin and client traffic.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import requests

log = logging.getLogger("bridge.session_bot")

_BASE = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 35


def _token() -> str:
    t = os.environ.get("SESSION_BOT_TOKEN", "")
    if not t:
        raise EnvironmentError("SESSION_BOT_TOKEN not set")
    return t


def _url(method: str) -> str:
    return _BASE.format(token=_token(), method=method)


def _post(method: str, **kwargs) -> dict:
    r = requests.post(_url(method), json=kwargs, timeout=_TIMEOUT)
    return r.json()


def _get(method: str, params: dict | None = None) -> dict:
    r = requests.get(_url(method), params=params, timeout=_TIMEOUT)
    return r.json()


# ── Send ─────────────────────────────────────────────────────────────────────

def send_message(chat_id: int | str, text: str, parse_mode: str | None = "HTML",
                 reply_markup: dict | None = None) -> dict:
    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("sendMessage", **payload)


def send_photo(chat_id: int | str, photo_path: str, caption: str = "") -> dict:
    token = _token()
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(photo_path, "rb") as f:
        r = requests.post(url, data={"chat_id": chat_id, "caption": caption},
                          files={"photo": f}, timeout=60)
    return r.json()


def send_document(chat_id: int | str, doc_bytes: bytes, filename: str, caption: str = "") -> dict:
    token = _token()
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    r = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"document": (filename, doc_bytes, "application/pdf")},
        timeout=60,
    )
    return r.json()


def edit_message(chat_id: int | str, message_id: int, text: str,
                 parse_mode: str | None = "HTML", reply_markup: dict | None = None) -> dict:
    payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("editMessageText", **payload)


def answer_callback(callback_query_id: str, text: str = "") -> dict:
    return _post("answerCallbackQuery", callback_query_id=callback_query_id, text=text)


def send_inline_keyboard(chat_id: int | str, text: str,
                         rows: list[list[tuple[str, str]]]) -> dict:
    keyboard = [[{"text": label, "callback_data": data} for label, data in row]
                for row in rows]
    markup = {"inline_keyboard": keyboard}
    return send_message(chat_id, text, reply_markup=markup)


# ── Raw poll (used only by UpdateBus) ────────────────────────────────────────

def get_updates(offset: int | None = None, timeout: int = 30) -> list[dict]:
    params: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    r = _get("getUpdates", params)
    if r.get("ok"):
        return r.get("result", [])
    return []


# ── UpdateBus ─────────────────────────────────────────────────────────────────

class _UpdateBus:
    """
    Single long-poll thread against SESSION_BOT_TOKEN (Passerclue). Routes every
    incoming Telegram update exactly once:

      callback_query  →  _cb_waiters[message_id]   (tool waiting for button tap)
      text message    →  _text_waiters[chat_id]     (tool waiting for typed reply)
                      OR listener_queue              (no tool claimed it → bridge)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offset: int | None = None
        self._thread: threading.Thread | None = None

        self._cb_waiters: dict[int, queue.Queue] = {}    # msg_id → queue
        self._text_waiters: dict[int, queue.Queue] = {}  # chat_id → queue

        # Unclaimed text messages go here for the bridge to consume
        self.listener_queue: queue.Queue = queue.Queue()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="tg-session-bus"
            )
            self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                updates = get_updates(offset=self._offset, timeout=30)
                for upd in updates:
                    self._offset = upd["update_id"] + 1
                    self._dispatch(upd)
            except Exception:
                time.sleep(5)

    def _dispatch(self, upd: dict) -> None:
        cq = upd.get("callback_query")
        msg = upd.get("message") or upd.get("edited_message")

        if cq:
            mid = cq.get("message", {}).get("message_id")
            with self._lock:
                q = self._cb_waiters.get(mid)
            if q:
                q.put(cq)
            else:
                # No tool is waiting for this button — route to listener (bridge)
                self.listener_queue.put({"_cq": cq})

        if msg:
            chat_id = msg.get("chat", {}).get("id")
            with self._lock:
                q = self._text_waiters.get(chat_id)
            if q:
                q.put(msg)
            else:
                self.listener_queue.put(msg)

    # ── Registration ──────────────────────────────────────────────────────────

    def register_cb(self, msg_id: int) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._cb_waiters[msg_id] = q
        return q

    def unregister_cb(self, msg_id: int) -> None:
        with self._lock:
            self._cb_waiters.pop(msg_id, None)

    def register_text(self, chat_id: int) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._text_waiters[chat_id] = q
        return q

    def unregister_text(self, chat_id: int) -> None:
        with self._lock:
            self._text_waiters.pop(chat_id, None)


# Singleton — imported by telegram_claude_bridge.py for client-side traffic
client_bus = _UpdateBus()
