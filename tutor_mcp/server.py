"""
Tutor Capture Service — pure sensor layer for Claude Code orchestration.

Port: 8716
Claude Code is the brain — this service has no decision logic.

Tools:
  capture_start(session_id)  — begin DXGI + WASAPI capture
  capture_stop()             — stop capture, flush pending clip
  capture_get_event(timeout) — pop next processed event for Claude to reason over
  capture_get_snapshot()     — on-demand frame + OCR
  capture_audio_rms()        — current WASAPI RMS
  capture_get_clip_uri()     — latest Gemini-uploaded clip URI
  capture_status()           — service health
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, "E:\\Corvus_Careebridge")

from _minmcp import MinMCP

from ._audio import start as _audio_start, stop as _audio_stop, get_rms, get_window
from ._capture import start as _cap_start, stop as _cap_stop, get_event as _cap_get_event, get_snapshot, FrameEvent
from ._ocr import extract_text
from ._assembler import assemble_clip
from ._uploader import BackgroundUploader

mcp = MinMCP("tutor")

_BASE_DIR = "E:/Corvus_Careebridge/tutor_mcp"
_CLIPS_DIR = f"{_BASE_DIR}/clips"
_FRAMES_DIR = f"{_BASE_DIR}/frames"

os.makedirs(_CLIPS_DIR, exist_ok=True)
os.makedirs(_FRAMES_DIR, exist_ok=True)

# ── Module state ──────────────────────────────────────────────────────────────

_running = False
_uploader = BackgroundUploader()

# Processed event queue — what Claude reads from via capture_get_event()
_out_queue: queue.Queue = queue.Queue(maxsize=200)

# Video clip buffering (written/read only by _event_processor thread except
# _latest_clip_path which is also read by capture_get_clip_uri HTTP handler)
_video_frames: list = []
_video_start_ts: float = 0.0
_video_last_ts: float = 0.0
_VIDEO_GAP_S = 1.5
_latest_clip_path: Optional[str] = None
_state_lock = threading.Lock()

_processor_thread: Optional[threading.Thread] = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _save_frame_jpeg(bgr, label: str) -> str:
    import cv2
    ts_str = time.strftime("%H%M%S")
    path = f"{_FRAMES_DIR}/{label}_{ts_str}.jpg"
    cv2.imwrite(path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return path


def _flush_video_clip():
    """Assemble buffered video frames into a clip and enqueue for Gemini upload."""
    global _video_frames, _video_start_ts, _latest_clip_path

    with _state_lock:
        frames = list(_video_frames)
        start_ts = _video_start_ts
        last_ts = _video_last_ts
        _video_frames = []

    if len(frames) < 5:
        return

    clip_name = f"clip_{time.strftime('%H%M%S', time.localtime(start_ts))}.mp4"
    clip_path = f"{_CLIPS_DIR}/{clip_name}"
    audio_chunks = get_window(start_ts - 0.5, last_ts + 0.5)

    try:
        assemble_clip(frames, audio_chunks, clip_path)
        _uploader.enqueue(clip_path)
        with _state_lock:
            _latest_clip_path = clip_path
        try:
            _out_queue.put_nowait({
                "kind": "video_end",
                "timestamp": last_ts,
                "clip_path": clip_path,
                "frame_count": len(frames),
                "duration_s": round(last_ts - start_ts, 1),
            })
        except queue.Full:
            pass
    except Exception as exc:
        try:
            _out_queue.put_nowait({
                "kind": "video_end",
                "timestamp": last_ts,
                "clip_path": None,
                "error": str(exc),
            })
        except queue.Full:
            pass


def _event_processor():
    """Read raw capture events, buffer video, emit processed events to _out_queue."""
    global _video_frames, _video_start_ts, _video_last_ts, _running

    while _running:
        # Flush video clip if silence gap exceeded
        with _state_lock:
            has_video = bool(_video_frames)
            last_ts = _video_last_ts
        if has_video and (time.time() - last_ts) > _VIDEO_GAP_S:
            _flush_video_clip()
            continue

        event: Optional[FrameEvent] = _cap_get_event(timeout=0.5)
        if event is None:
            continue

        if event.kind == "navigation" and event.frame_bgr is not None:
            frame_path = _save_frame_jpeg(event.frame_bgr, "nav")
            ocr_text = extract_text(event.frame_bgr)
            try:
                _out_queue.put_nowait({
                    "kind": "navigation",
                    "timestamp": event.timestamp,
                    "frame_path": frame_path,
                    "ocr_text": ocr_text,
                })
            except queue.Full:
                pass

        elif event.kind == "scroll" and event.scroll_strip is not None:
            ocr_text = extract_text(event.scroll_strip)
            if ocr_text.strip():
                try:
                    _out_queue.put_nowait({
                        "kind": "scroll",
                        "timestamp": event.timestamp,
                        "ocr_text": ocr_text,
                    })
                except queue.Full:
                    pass

        elif event.kind == "video_playing" and event.frame_bgr is not None:
            with _state_lock:
                _video_frames.append((event.frame_bgr.copy(), event.timestamp))
                if len(_video_frames) == 1:
                    _video_start_ts = event.timestamp
                _video_last_ts = event.timestamp

        # static and ui_interaction events are discarded


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def capture_start(session_id: str = "") -> dict:
    """
    Start DXGI screen capture and WASAPI audio capture.

    Call this once at the beginning of a session, before navigating to the
    annotation site. Claude Code then polls capture_get_event() in a loop
    to receive classified events (navigation, scroll, video_end).

    Returns: {status, session_id, audio_capture}
    """
    global _running, _processor_thread, _video_frames, _latest_clip_path, _out_queue

    if _running:
        return {"status": "already_running"}

    # Reset state for new session
    with _state_lock:
        _video_frames = []
        _latest_clip_path = None
    # Drain any leftover events from previous session
    _out_queue = queue.Queue(maxsize=200)

    sid = session_id or time.strftime("%Y%m%d_%H%M%S")
    _running = True

    audio_ok = _audio_start()
    _uploader.start()
    _cap_start(get_rms)

    _processor_thread = threading.Thread(target=_event_processor, daemon=True, name="tutor-processor")
    _processor_thread.start()

    return {"status": "started", "session_id": sid, "audio_capture": audio_ok}


@mcp.tool()
def capture_stop() -> dict:
    """
    Stop all capture. Flushes any buffered video clip before stopping.

    Returns: {status, clips_completed}
    """
    global _running

    if not _running:
        return {"status": "not_running"}

    _running = False
    _cap_stop()
    _audio_stop()

    if _processor_thread is not None:
        _processor_thread.join(timeout=3)

    # Flush any remaining video buffer
    with _state_lock:
        has_video = bool(_video_frames)
    if has_video:
        _flush_video_clip()

    _uploader.stop()

    clips_done = sum(
        1 for info in _uploader._cache.values()
        if info.get("state") == "ACTIVE"
    )
    return {"status": "stopped", "clips_completed": clips_done}


@mcp.tool()
def capture_get_event(timeout_s: float = 2.0) -> dict:
    """
    Pop the next processed capture event from the queue.

    Event kinds:
      navigation — major page change; frame_path (JPEG) and ocr_text provided.
                   Pass frame_path to mcp__gemini__analyse_image to get content analysis.
      scroll     — new content scrolled into view; ocr_text of the revealed strip.
      video_end  — a video clip was assembled; clip_path provided.
                   Use capture_get_clip_uri() to check when it's ACTIVE for Gemini.
      none       — queue was empty within timeout_s.

    Call this in a reasoning loop: get_event → reason → repeat.

    Returns: {kind, timestamp, ...kind-specific fields}
    """
    try:
        return _out_queue.get(timeout=timeout_s)
    except queue.Empty:
        return {"kind": "none"}


@mcp.tool()
def capture_get_snapshot() -> dict:
    """
    Capture the current screen on demand via DXGI (or mss fallback) and run OCR.

    Use when you need to inspect current screen state between events, or to
    verify what the screen shows before answering a question.

    To get Gemini's analysis of the snapshot, pass frame_path to
    mcp__gemini__analyse_image with your prompt.

    Returns: {frame_path, ocr_text} or {error, frame_path: null, ocr_text: ""}
    """
    snap = get_snapshot()
    if snap is None:
        try:
            import mss as _mss
            import numpy as _np
            with _mss.mss() as sct:
                mon = sct.monitors[1]
                sct_img = sct.grab(mon)
                snap = _np.array(sct_img)[:, :, :3]
        except Exception:
            snap = None

    if snap is None:
        return {"error": "capture_unavailable", "frame_path": None, "ocr_text": ""}

    frame_path = _save_frame_jpeg(snap, "snapshot")
    ocr_text = extract_text(snap)
    return {"frame_path": frame_path, "ocr_text": ocr_text}


@mcp.tool()
def capture_audio_rms() -> dict:
    """
    Return the current WASAPI loopback audio RMS level (0.0–1.0).

    Values above ~0.01 indicate audio is playing.
    Use to confirm whether a video is currently producing sound before
    deciding if capture_get_clip_uri() will have a useful clip.

    Returns: {rms}
    """
    return {"rms": round(get_rms(), 4)}


@mcp.tool()
def capture_get_clip_uri() -> dict:
    """
    Return the Gemini File API URI for the latest assembled video clip.

    State values:
      ACTIVE    — clip is ready; pass uri to mcp__gemini__analyse_video
      uploading — still uploading to Gemini; retry in a few seconds
      queued    — waiting to start upload
      none      — no clip assembled yet this session

    Returns: {uri, state, clip_path}
    """
    with _state_lock:
        clip_path = _latest_clip_path

    if clip_path is None:
        return {"uri": None, "state": "none", "clip_path": None}

    info = _uploader.get_info(clip_path)
    return {
        "uri": info.get("uri"),
        "state": info.get("state", "unknown"),
        "clip_path": clip_path,
    }


@mcp.tool()
def capture_status() -> dict:
    """
    Return current service health.

    Returns: {running, queue_depth, video_buffer_frames, clips_pending, clips_ready, audio_rms}
    """
    with _state_lock:
        buf_frames = len(_video_frames)

    pending = sum(1 for i in _uploader._cache.values() if i.get("state") in ("queued", "uploading"))
    ready = sum(1 for i in _uploader._cache.values() if i.get("state") == "ACTIVE")

    return {
        "running": _running,
        "queue_depth": _out_queue.qsize(),
        "video_buffer_frames": buf_frames,
        "clips_pending": pending,
        "clips_ready": ready,
        "audio_rms": round(get_rms(), 4),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8716)
    args = parser.parse_args()
    mcp.run_http(args.port)
