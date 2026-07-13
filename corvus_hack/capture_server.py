"""
capture_server.py — Lightweight DXGI screen capture HTTP server for CorvusClient sessions.

Exposes simple REST endpoints that worker.py calls.
Runs in each CorvusClient Windows session (DXGI is session-local).

Port: 8703 (different from Mike's capture_mcp on 8702)

Endpoints:
  POST /start          {"session_id": "..."}            → start capture loop
  POST /get_event      {"timeout": 30}                  → block until event or timeout
  POST /stop           {}                               → stop capture loop
  GET  /health                                          → {"status": "ok"}

Events returned by /get_event:
  {"type": "navigation", "frame_path": "..."}  — >40% pixel change
  {"type": "scroll",     "frame_path": "..."}  — 5–40% pixel change
  {"type": "none"}                              — timeout, no significant change
"""
from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
from pathlib import Path

import dxcam
import numpy as np
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── State ─────────────────────────────────────────────────────────────────────

_camera = None
_camera_lock = threading.Lock()
_event_queue: queue.Queue = queue.Queue()
_session_id: str = ""
_running = False
_capture_thread: threading.Thread | None = None

FRAMES_DIR = Path(tempfile.gettempdir()) / "corvus_frames"
FRAMES_DIR.mkdir(exist_ok=True)

# Thresholds (fraction of pixels that changed)
NAVIGATE_THRESH = 0.40   # full page change
SCROLL_THRESH   = 0.05   # partial content change
CAPTURE_FPS     = 2      # how many frames per second to sample


# ── Pixel-diff helper ─────────────────────────────────────────────────────────

def _diff_ratio(a: np.ndarray, b: np.ndarray) -> float:
    """Fraction of pixels that changed between two frames (grayscale comparison)."""
    if a.shape != b.shape:
        return 1.0
    ag = a.mean(axis=2).astype(np.float32)
    bg = b.mean(axis=2).astype(np.float32)
    diff = np.abs(ag - bg)
    changed = (diff > 15).sum()   # >15/255 intensity change counts
    return float(changed) / diff.size


def _save_frame(frame: np.ndarray) -> str:
    ts = int(time.time() * 1000)
    path = str(FRAMES_DIR / f"frame_{ts}.png")
    from PIL import Image
    Image.fromarray(frame).save(path)
    return path


# ── Capture loop ─────────────────────────────────────────────────────────────

def _capture_loop() -> None:
    global _running, _camera
    prev = None
    interval = 1.0 / CAPTURE_FPS
    last_event_time = 0.0

    with _camera_lock:
        cam = dxcam.create(output_color="RGB")
        _camera = cam

    cam.start(target_fps=CAPTURE_FPS, video_mode=False)
    time.sleep(0.5)  # let dxcam warm up

    try:
        while _running:
            frame = cam.get_latest_frame()
            if frame is None:
                time.sleep(interval)
                continue

            if prev is not None:
                ratio = _diff_ratio(prev, frame)
                now = time.time()
                # Throttle events: at most one per 2 seconds
                if ratio >= NAVIGATE_THRESH and now - last_event_time >= 2.0:
                    fp = _save_frame(frame)
                    _event_queue.put({"type": "navigation", "frame_path": fp})
                    last_event_time = now
                elif ratio >= SCROLL_THRESH and now - last_event_time >= 2.0:
                    fp = _save_frame(frame)
                    _event_queue.put({"type": "scroll", "frame_path": fp})
                    last_event_time = now

            prev = frame
            time.sleep(interval)
    except Exception as e:
        print(f"[capture] Loop error: {e}")
    finally:
        try:
            cam.stop()
            cam.release()
        except Exception:
            pass
        with _camera_lock:
            _camera = None
        _running = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "running": _running, "session": _session_id})


@app.route("/start", methods=["POST"])
def start_capture():
    global _session_id, _running, _capture_thread
    data = request.json or {}
    _session_id = data.get("session_id", "")

    if _running:
        return jsonify({"status": "already_running"})

    # Drain stale events
    while not _event_queue.empty():
        try:
            _event_queue.get_nowait()
        except Exception:
            break

    _running = True
    _capture_thread = threading.Thread(target=_capture_loop, daemon=True)
    _capture_thread.start()
    return jsonify({"status": "started", "session_id": _session_id})


@app.route("/get_event", methods=["POST"])
def get_event():
    data = request.json or {}
    timeout = float(data.get("timeout", 30))
    try:
        event = _event_queue.get(timeout=timeout)
        return jsonify(event)
    except queue.Empty:
        return jsonify({"type": "none"})


@app.route("/stop", methods=["POST"])
def stop_capture():
    global _running
    _running = False
    return jsonify({"status": "stopped"})


def _frame_cleanup() -> None:
    """Safety net: delete frames older than 10 minutes that worker never claimed."""
    while True:
        time.sleep(300)
        cutoff = time.time() - 600
        for f in FRAMES_DIR.glob("frame_*.png"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass

threading.Thread(target=_frame_cleanup, daemon=True).start()

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8703
    print(f"[capture_server] Starting on port {port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
