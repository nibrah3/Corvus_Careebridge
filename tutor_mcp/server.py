"""
Tutor MCP — passive annotation training pipeline.

Port: 8716
Orchestrates: _capture → _ocr → _session → _assembler → _uploader → Gemini

Tools:
  tutor_start_session()         — begin passive monitoring
  tutor_stop_session()          — stop monitoring, save session.md
  tutor_get_context()           — return current session.md content
  tutor_ask_gemini(question)    — ask Gemini about buffered video/session context
  tutor_capture_snapshot()      — one-shot screenshot + OCR for immediate analysis
  tutor_status()                — return current session state
"""
from __future__ import annotations

import concurrent.futures
import io
import os
import sys
import threading
import time
from typing import Optional

_GEMINI_TIMEOUT_S = 20.0

sys.path.insert(0, "E:\\Corvus_Careebridge")

from _minmcp import MinMCP

from ._audio import start as _audio_start, stop as _audio_stop, get_rms, get_window
from ._capture import start as _cap_start, stop as _cap_stop, get_event, get_snapshot, FrameEvent
from ._ocr import extract_text
from ._session import SessionDoc
from ._assembler import assemble_clip
from ._uploader import BackgroundUploader

mcp = MinMCP("tutor")

_BASE_DIR = "E:/Corvus_Careebridge/tutor_mcp"
_CLIPS_DIR = f"{_BASE_DIR}/clips"
_FRAMES_DIR = f"{_BASE_DIR}/frames"
_SESSION_DIR = f"{_BASE_DIR}/sessions"

os.makedirs(_CLIPS_DIR, exist_ok=True)
os.makedirs(_FRAMES_DIR, exist_ok=True)
os.makedirs(_SESSION_DIR, exist_ok=True)

# ── Session state ─────────────────────────────────────────────────────────────

_session: Optional[SessionDoc] = None
_session_lock = threading.Lock()
_orch_thread: Optional[threading.Thread] = None
_running = False
_uploader = BackgroundUploader()

# Video clip buffering
_video_frames: list = []        # [(bgr_array, ts), ...]
_video_start_ts: float = 0.0
_video_last_ts: float = 0.0
_VIDEO_GAP_S = 1.5              # silence gap → end of video event


def _save_frame_jpeg(bgr, label: str) -> str:
    import cv2
    ts_str = time.strftime("%H%M%S")
    path = f"{_FRAMES_DIR}/{label}_{ts_str}.jpg"
    cv2.imwrite(path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return path


def _gemini_analyse_image(image_path: str, prompt: str) -> str:
    try:
        sys.path.insert(0, "E:\\Corvus_Careebridge\\gemini_mcp")
        from _gemini import analyse_image
    except Exception as exc:
        return f"[gemini error: {exc}]"
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(analyse_image, image_path, prompt)
    ex.shutdown(wait=False)
    try:
        result = fut.result(timeout=_GEMINI_TIMEOUT_S)
        return result.get("text", "")
    except concurrent.futures.TimeoutError:
        return "[gemini: timed out]"
    except Exception as exc:
        return f"[gemini error: {exc}]"


def _gemini_analyse_video(uri: str, prompt: str) -> str:
    try:
        sys.path.insert(0, "E:\\Corvus_Careebridge\\gemini_mcp")
        from _gemini import analyse_video
    except Exception as exc:
        return f"[gemini error: {exc}]"
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(analyse_video, uri, prompt)
    ex.shutdown(wait=False)
    try:
        result = fut.result(timeout=_GEMINI_TIMEOUT_S)
        return result.get("text", "")
    except concurrent.futures.TimeoutError:
        return "[gemini: timed out]"
    except Exception as exc:
        return f"[gemini error: {exc}]"


def _flush_video_clip(session: SessionDoc):
    """Assemble buffered video frames + audio into a clip and upload."""
    global _video_frames, _video_start_ts, _video_last_ts

    frames = list(_video_frames)
    _video_frames = []

    if len(frames) < 5:
        return

    clip_ts = _video_start_ts
    clip_name = f"clip_{time.strftime('%H%M%S', time.localtime(clip_ts))}.mp4"
    clip_path = f"{_CLIPS_DIR}/{clip_name}"

    audio_chunks = get_window(_video_start_ts - 0.5, _video_last_ts + 0.5)

    try:
        assemble_clip(frames, audio_chunks, clip_path)
        _uploader.enqueue(clip_path)
        session.add_video_ref(clip_ts, clip_path)
        session.add_note(time.time(), f"Video clip assembled ({len(frames)} frames) — uploading to Gemini")
    except Exception as exc:
        session.add_note(time.time(), f"Clip assembly failed: {exc}")


def _orchestrate():
    """Main event loop — consumes _capture events and builds session.md."""
    global _video_frames, _video_start_ts, _video_last_ts, _running

    with _session_lock:
        session = _session

    if session is None:
        return

    last_video_event_ts = 0.0

    while _running:
        event: Optional[FrameEvent] = get_event(timeout=0.5)

        # Check if a video event timed out (gap > threshold)
        if _video_frames:
            gap = time.time() - _video_last_ts
            if gap > _VIDEO_GAP_S:
                _flush_video_clip(session)

        if event is None:
            continue

        if event.kind == "navigation":
            if event.frame_bgr is not None:
                img_path = _save_frame_jpeg(event.frame_bgr, "nav")
                ocr_text = extract_text(event.frame_bgr)
                analysis = _gemini_analyse_image(
                    img_path,
                    "Extract all visible text on this screen. Identify: "
                    "page type (question/instructions/video/other), "
                    "any question text and answer options, "
                    "any instructions visible. Be concise.",
                )
                session.add_navigation(event.timestamp, ocr_text, img_path, analysis)

        elif event.kind == "scroll":
            if event.scroll_strip is not None:
                ocr_text = extract_text(event.scroll_strip)
                if ocr_text.strip():
                    session.add_scroll_content(event.timestamp, ocr_text)

        elif event.kind == "video_playing":
            if event.frame_bgr is not None:
                _video_frames.append((event.frame_bgr.copy(), event.timestamp))
                if not _video_frames or len(_video_frames) == 1:
                    _video_start_ts = event.timestamp
                _video_last_ts = event.timestamp

        # ui_interaction and static are discarded (correct behaviour)


@mcp.tool()
def tutor_start_session(session_id: str = "") -> dict:
    """
    Start passive screen + audio monitoring session.

    Begins WASAPI loopback audio capture and DXGI frame capture.
    All captured content accumulates in session.md in real time.

    Args:
        session_id: Optional label for this session (default: timestamp).

    Returns status dict with session_id.
    """
    global _session, _orch_thread, _running

    with _session_lock:
        if _running:
            return {"status": "already_running", "session_id": _session._id if _session else ""}

        sid = session_id or time.strftime("%Y%m%d_%H%M%S")
        _session = SessionDoc(sid)
        _running = True

    audio_ok = _audio_start()
    _uploader.start()
    _cap_start(get_rms)

    _orch_thread = threading.Thread(target=_orchestrate, daemon=True)
    _orch_thread.start()

    return {
        "status": "started",
        "session_id": sid,
        "audio_capture": audio_ok,
        "session_dir": _SESSION_DIR,
    }


@mcp.tool()
def tutor_stop_session() -> dict:
    """
    Stop passive monitoring and save session.md.

    Returns path to the saved session document and summary stats.
    """
    global _running

    if not _running:
        return {"status": "not_running"}

    _running = False

    _cap_stop()
    _audio_stop()
    _uploader.stop()

    with _session_lock:
        session = _session

    if session is None:
        return {"status": "stopped", "entries": 0}

    if _video_frames:
        _flush_video_clip(session)

    session_path = f"{_SESSION_DIR}/{session._id}.md"
    session.save(session_path)

    return {
        "status": "stopped",
        "session_id": session._id,
        "entries": session.entry_count(),
        "size_kb": round(session.size_kb(), 1),
        "session_path": session_path,
    }


@mcp.tool()
def tutor_get_context() -> dict:
    """
    Return the current session.md content for Claude to reason over.

    Use this to get full context before answering a worker's question.
    Content includes OCR text from all navigated pages, Gemini analysis
    of screenshots, and transcriptions of any video clips processed so far.
    """
    with _session_lock:
        session = _session

    if session is None:
        return {"status": "no_session", "content": ""}

    content = session.to_markdown()
    return {
        "status": "ok",
        "session_id": session._id,
        "entries": session.entry_count(),
        "size_kb": round(len(content.encode()) / 1024, 1),
        "content": content,
    }


@mcp.tool()
def tutor_ask_gemini(
    question: str,
    use_latest_clip: bool = True,
) -> dict:
    """
    Ask Gemini a question about the current session context.

    If use_latest_clip=True and a recently uploaded clip is available,
    Gemini receives the video + question together (multimodal).
    Otherwise uses the session.md text context.

    Args:
        question: The question to ask Gemini.
        use_latest_clip: Whether to include the latest video clip (default True).

    Returns: Gemini's response text.
    """
    with _session_lock:
        session = _session

    if session is None:
        return {"status": "no_session", "answer": "No active session."}

    # Try to find the latest uploaded clip URI
    clip_uri = None
    if use_latest_clip:
        for path, info in reversed(list(_uploader._cache.items())):
            if info.get("state") == "ACTIVE":
                clip_uri = info.get("uri")
                break

    try:
        sys.path.insert(0, "E:\\Corvus_Careebridge\\gemini_mcp")
        from _gemini import analyse_video, analyse_image

        if clip_uri:
            result = analyse_video(clip_uri, question)
        else:
            # Fallback: snapshot + question
            snap = get_snapshot()
            if snap is not None:
                snap_path = _save_frame_jpeg(snap, "query")
                result = analyse_image(snap_path, question)
            else:
                return {"status": "no_content", "answer": "No video or snapshot available."}

        return {"status": "ok", "answer": result.get("text", ""), "model": result.get("model", "")}

    except Exception as exc:
        return {"status": "error", "answer": str(exc)}


@mcp.tool()
def tutor_capture_snapshot(
    prompt: str = "Describe what is on screen. Extract any question text, instructions, or answer options.",
) -> dict:
    """
    Take an immediate screenshot, run OCR, and ask Gemini to describe it.

    Use this when the worker asks about what's currently on screen or
    needs instant analysis of the current page state.

    Args:
        prompt: Question or instruction for Gemini analysis.

    Returns: dict with ocr_text, gemini_analysis, image_path.
    """
    snap = get_snapshot()
    if snap is None:
        # dxcam may not be available; use mss as fallback
        try:
            import mss as _mss
            import numpy as _np
            with _mss.MSS() as sct:
                mon = sct.monitors[1]
                sct_img = sct.grab(mon)
                snap = _np.array(sct_img)[:, :, :3]
        except Exception:
            snap = None

    if snap is None:
        return {"status": "error", "reason": "Could not capture frame"}

    img_path = _save_frame_jpeg(snap, "snapshot")
    ocr_text = extract_text(snap)
    analysis = _gemini_analyse_image(img_path, prompt)

    with _session_lock:
        session = _session
    if session:
        session.add_image_region(time.time(), img_path, analysis)

    return {
        "status": "ok",
        "image_path": img_path,
        "ocr_text": ocr_text,
        "gemini_analysis": analysis,
    }


@mcp.tool()
def tutor_status() -> dict:
    """
    Return current tutor session state.

    Shows whether session is active, entry count, audio/capture status,
    pending uploads, and current audio RMS level.
    """
    with _session_lock:
        session = _session

    pending_uploads = sum(
        1 for info in _uploader._cache.values()
        if info.get("state") in ("queued", "uploading")
    )
    completed_uploads = sum(
        1 for info in _uploader._cache.values()
        if info.get("state") == "ACTIVE"
    )

    return {
        "running": _running,
        "session_id": session._id if session else None,
        "entries": session.entry_count() if session else 0,
        "size_kb": round(session.size_kb(), 1) if session else 0,
        "audio_rms": round(get_rms(), 4),
        "video_buffer_frames": len(_video_frames),
        "uploads_pending": pending_uploads,
        "uploads_completed": completed_uploads,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8716)
    args = parser.parse_args()
    mcp.run_http(args.port)
