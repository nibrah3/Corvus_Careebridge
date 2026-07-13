"""
worker.py — Autonomous DXGI event loop for CorvusClient sessions.

Runs at login on each CorvusClient Windows account (via All Users Startup).
Skips execution on Mike's/Administrator's account.

Ports (all localhost — TCP crosses Windows sessions):
  capture_server : 8703  (started by this account's startup script, DXGI-local)
  gemini_mcp     : 8705  (Mike's session, reachable via loopback)
  master         : 9200  (Mike's session, reachable via loopback)
"""
from __future__ import annotations

import os
import random
import socket
import sys
import time

import requests

_ROOT = Path(__file__).parent.parent


def _load_env() -> None:
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

CAPTURE_PORT = int(os.environ.get("CORVUS_CAPTURE_PORT", "8703"))
CAPTURE  = f"http://localhost:{CAPTURE_PORT}"
GEMINI   = "http://localhost:8705"
MASTER   = "http://localhost:9200"

ACCOUNT = os.environ.get("CORVUS_ACCOUNT", os.environ.get("USERNAME", socket.gethostname()))

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


def _wait_for_capture(timeout: int = 60) -> bool:
    """Poll /health until capture_server responds — reliable alternative to blind sleep."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{CAPTURE}/health", timeout=2)
            if r.json().get("status") == "ok":
                return True
        except Exception:
            pass
        print("[worker] Waiting for capture server...")
        time.sleep(2)
    return False


def _ensure_capture(session_id: str) -> bool:
    if not _wait_for_capture():
        print("[worker] Capture server not responding after 60s — screen events will be missed")
        return False
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
    """Returns False if the master has ended this session."""
    try:
        r = requests.post(
            f"{MASTER}/worker/heartbeat",
            json={"session_id": session_id, "account": ACCOUNT},
            timeout=5,
        )
        return r.json().get("active", True)
    except Exception:
        return True  # assume still active on transient network error


def _register() -> str | None:
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
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            if not _send_heartbeat(session_id):
                print(f"[worker] Session {session_id} ended by master.")
                break
            last_heartbeat = now

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
                try:
                    os.unlink(frame_path)  # free disk immediately after Gemini is done
                except Exception:
                    pass
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
        try:
            session_id = _register()
            if session_id:
                _event_loop(session_id)
                print("[worker] Session ended. Waiting for next assignment.")
            else:
                time.sleep(15 + random.uniform(0, 3))  # jitter prevents sync'd polling
        except Exception as e:
            print(f"[worker] Unexpected crash: {e} — restarting in 15s")
            time.sleep(15)


if __name__ == "__main__":
    main()
