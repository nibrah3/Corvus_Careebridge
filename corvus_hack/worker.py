"""
worker.py — Autonomous DXGI event loop for CorvusClient sessions.

Runs at login on each CorvusClient Windows account (via All Users Startup).
Skips execution on Mike's/Administrator's account.

Ports (all localhost — TCP crosses Windows sessions):
  capture_mcp : 8703  (started by this account's startup script, DXGI-local)
  gemini_mcp  : 8705  (Mike's session, reachable via loopback)
  master      : 9200  (Mike's session, reachable via loopback)
"""
from __future__ import annotations

import os
import sys
import socket
import time
import requests

CAPTURE_PORT = int(os.environ.get("CORVUS_CAPTURE_PORT", "8703"))
CAPTURE  = f"http://localhost:{CAPTURE_PORT}"
GEMINI   = "http://localhost:8705"
MASTER   = "http://localhost:9200"

ACCOUNT = os.environ.get("CORVUS_ACCOUNT", os.environ.get("USERNAME", socket.gethostname()))

# Skip if running on Mike's or admin's account
_SKIP = {"mike", "administrator"}
if ACCOUNT.lower() in _SKIP:
    print(f"[worker] Skipping — not a Corvus client account (USERNAME={ACCOUNT})")
    sys.exit(0)


def _wait_for_master(timeout: int = 180) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"{MASTER}/health", timeout=3)
            return True
        except Exception:
            print(f"[worker] Waiting for master dispatcher on {MASTER}...")
            time.sleep(8)
    print(f"[worker] Master not reachable after {timeout}s. Exiting.")
    return False


def _ensure_capture(session_id: str) -> bool:
    try:
        requests.post(f"{CAPTURE}/start", json={"session_id": session_id}, timeout=10)
        return True
    except Exception as e:
        print(f"[worker] Capture start failed: {e}")
        return False


def _analyse_frame(frame_path: str) -> str:
    try:
        r = requests.post(
            f"{GEMINI}/analyse_image",
            json={
                "image_path": frame_path,
                "prompt": (
                    "Describe everything visible on screen in detail: "
                    "all text, questions, answer choices, form fields, buttons, "
                    "instructions, videos, timers. Be thorough and literal."
                ),
            },
            timeout=35,
        )
        return r.json().get("text", "")
    except Exception as e:
        print(f"[worker] Gemini error: {e}")
        return ""


def _send_event(session_id: str, screen_text: str, event_type: str) -> None:
    try:
        requests.post(
            f"{MASTER}/event",
            json={
                "session_id": session_id,
                "account": ACCOUNT,
                "screen": screen_text,
                "event_type": event_type,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[worker] Send event error: {e}")


def _send_heartbeat(session_id: str) -> bool:
    """Returns False if session has ended."""
    try:
        r = requests.post(
            f"{MASTER}/worker/heartbeat",
            json={"session_id": session_id, "account": ACCOUNT},
            timeout=5,
        )
        return r.json().get("active", True)
    except Exception:
        return True  # assume still active on network error


def _register() -> str | None:
    """Ask master if there's a pending session for this account."""
    try:
        r = requests.post(
            f"{MASTER}/worker/register",
            json={"account": ACCOUNT},
            timeout=10,
        )
        return r.json().get("session_id")
    except Exception as e:
        print(f"[worker] Register failed: {e}")
        return None


def _event_loop(session_id: str) -> None:
    print(f"[worker] Event loop started — session={session_id}")
    _ensure_capture(session_id)

    heartbeat_interval = 30
    last_heartbeat = time.time()

    while True:
        # Periodic heartbeat / session-end check
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            if not _send_heartbeat(session_id):
                print(f"[worker] Session {session_id} ended by master.")
                break
            last_heartbeat = now

        # Poll for next event
        try:
            r = requests.post(
                f"{CAPTURE}/get_event", json={"timeout": 30}, timeout=40
            )
            event = r.json()
        except Exception as e:
            print(f"[worker] Event poll error: {e}")
            time.sleep(5)
            continue

        etype = event.get("type", "none")

        if etype in ("navigation", "scroll"):
            frame_path = event.get("frame_path", "")
            if frame_path:
                desc = _analyse_frame(frame_path)
                if desc:
                    _send_event(session_id, desc, etype)

        elif etype == "video_end":
            clip_path = event.get("clip_path", "")
            if clip_path:
                try:
                    requests.post(
                        f"{MASTER}/video_event",
                        json={
                            "session_id": session_id,
                            "account": ACCOUNT,
                            "clip_path": clip_path,
                        },
                        timeout=10,
                    )
                except Exception as e:
                    print(f"[worker] Video event error: {e}")


def main() -> None:
    print(f"[worker] Starting on account={ACCOUNT}, capture={CAPTURE}")

    if not _wait_for_master():
        sys.exit(1)

    print("[worker] Polling for session assignment...")
    while True:
        session_id = _register()
        if session_id:
            _event_loop(session_id)
            print("[worker] Session ended. Will wait for next.")
        else:
            time.sleep(15)


if __name__ == "__main__":
    main()
