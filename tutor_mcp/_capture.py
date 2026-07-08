"""
DXGI continuous capture with pHash dedup and frame classification.

Frame types: static / navigation / scroll / video_playing / ui_interaction

FrameEvent fields:
  kind       : str  — one of the above
  timestamp  : float — time.time() when captured
  frame_bgr  : np.ndarray — raw BGR frame (may be None for static/ui_interaction)
  scroll_strip: np.ndarray | None — newly revealed strip for scroll events
  motion_rect: tuple | None — (x,y,w,h) bounding box of localised motion
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import numpy as np

_PHASH_THRESHOLD = 5        # Hamming distance < 5 → near-duplicate → discard
_NAVIGATION_THRESHOLD = 0.40  # >40% pixel change → navigation
_SCROLL_SHIFT_TOLERANCE = 8   # ±8 px matching for scroll detection
_VIDEO_MOTION_RATIO = 0.05    # >5% of pixels changed in motion region → video
_SCROLL_MIN_SHIFT = 20        # minimum pixel shift to count as scroll
_CAPTURE_FPS = 8              # target frames per second for classification

_event_queue: queue.Queue = queue.Queue(maxsize=50)
_snapshot: Optional[np.ndarray] = None
_snapshot_lock = threading.Lock()
_running = False
_thread: Optional[threading.Thread] = None
_get_audio_rms: Callable[[], float] = lambda: 0.0


@dataclass
class FrameEvent:
    kind: str
    timestamp: float
    frame_bgr: Optional[np.ndarray] = field(default=None, repr=False)
    scroll_strip: Optional[np.ndarray] = field(default=None, repr=False)
    motion_rect: Optional[Tuple[int, int, int, int]] = None


def _phash(img_gray: np.ndarray) -> int:
    """Compute pHash as a 64-bit integer."""
    import cv2
    resized = cv2.resize(img_gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = np.fft.fft2(resized.astype(np.float32))
    dct_low = dct[:8, :8].real
    med = np.median(dct_low)
    bits = (dct_low > med).flatten()
    val = 0
    for b in bits:
        val = (val << 1) | int(b)
    return val


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _changed_ratio(prev: np.ndarray, curr: np.ndarray) -> float:
    diff = np.abs(prev.astype(np.int16) - curr.astype(np.int16))
    return float(np.mean(diff > 15))


def _detect_scroll(
    prev: np.ndarray, curr: np.ndarray
) -> Optional[int]:
    """
    Try shifts of prev by N pixels vertically. If a shift produces a close match
    return N (positive = scroll down). Returns None if no scroll detected.
    """
    h = prev.shape[0]
    for shift in range(_SCROLL_MIN_SHIFT, min(h // 2, 400), 4):
        # scroll down: bottom of curr matches top of prev-shifted
        prev_top = prev[shift:, :]
        curr_top = curr[: h - shift, :]
        if prev_top.shape == curr_top.shape:
            diff = np.abs(prev_top.astype(np.int16) - curr_top.astype(np.int16))
            if float(np.mean(diff > 20)) < 0.08:
                return shift
        # scroll up
        prev_bot = prev[: h - shift, :]
        curr_bot = curr[shift:, :]
        if prev_bot.shape == curr_bot.shape:
            diff = np.abs(prev_bot.astype(np.int16) - curr_bot.astype(np.int16))
            if float(np.mean(diff > 20)) < 0.08:
                return -shift
    return None


def _motion_bbox(
    prev_gray: np.ndarray, curr_gray: np.ndarray
) -> Optional[Tuple[int, int, int, int]]:
    """Return bounding box of changed region, or None if change is too diffuse."""
    import cv2
    diff = cv2.absdiff(prev_gray, curr_gray)
    _, thresh = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    x, y, w, h = cv2.boundingRect(np.vstack(contours))
    area_ratio = (w * h) / (curr_gray.shape[0] * curr_gray.shape[1])
    if area_ratio < 0.01 or area_ratio > 0.95:
        return None
    return (x, y, w, h)


def _classify_frame(
    prev_bgr: np.ndarray,
    curr_bgr: np.ndarray,
    prev_hash: int,
    curr_hash: int,
    audio_rms: float,
) -> FrameEvent:
    import cv2

    ts = time.time()

    # 1. Duplicate check
    if _hamming(prev_hash, curr_hash) < _PHASH_THRESHOLD:
        return FrameEvent(kind="static", timestamp=ts)

    prev_gray = cv2.cvtColor(prev_bgr, cv2.COLOR_BGR2GRAY)
    curr_gray = cv2.cvtColor(curr_bgr, cv2.COLOR_BGR2GRAY)
    ratio = _changed_ratio(prev_gray, curr_gray)

    # 2. Navigation — large layout change
    if ratio > _NAVIGATION_THRESHOLD:
        return FrameEvent(kind="navigation", timestamp=ts, frame_bgr=curr_bgr)

    # 3. Scroll detection
    scroll_shift = _detect_scroll(prev_gray, curr_gray)
    if scroll_shift is not None:
        if scroll_shift > 0:  # scroll down → reveal bottom strip
            strip = curr_bgr[curr_bgr.shape[0] - scroll_shift:, :]
        else:  # scroll up → reveal top strip
            strip = curr_bgr[: abs(scroll_shift), :]
        return FrameEvent(
            kind="scroll",
            timestamp=ts,
            frame_bgr=curr_bgr,
            scroll_strip=strip,
        )

    # 4. Localised motion
    bbox = _motion_bbox(prev_gray, curr_gray)
    if bbox:
        x, y, w, h = bbox
        region_prev = prev_gray[y : y + h, x : x + w]
        region_curr = curr_gray[y : y + h, x : x + w]
        local_ratio = _changed_ratio(region_prev, region_curr)
        if local_ratio > _VIDEO_MOTION_RATIO and audio_rms > 0.01:
            return FrameEvent(
                kind="video_playing",
                timestamp=ts,
                frame_bgr=curr_bgr,
                motion_rect=bbox,
            )

    return FrameEvent(kind="ui_interaction", timestamp=ts)


def _grab_mss() -> Optional[np.ndarray]:
    """Single-shot screen capture via mss (GDI fallback)."""
    try:
        import mss as _mss
        with _mss.MSS() as sct:
            mon = sct.monitors[1]
            sct_img = sct.grab(mon)
            frame = np.array(sct_img)[:, :, :3]  # BGRA → BGR
            return frame
    except Exception:
        return None


def _capture_loop():
    global _snapshot, _running

    import cv2

    # Try DXGI first; probe with grab() (non-blocking) before committing to continuous mode
    cam = None
    use_dxgi = False
    try:
        import dxcam
        cam = dxcam.create(output_color="BGR")
        probe = cam.grab()  # returns None if DX surface unavailable
        if probe is not None:
            use_dxgi = True
            cam.start(target_fps=_CAPTURE_FPS)
        else:
            cam = None
    except Exception:
        cam = None

    prev_bgr: Optional[np.ndarray] = None
    prev_hash: Optional[int] = None
    interval = 1.0 / _CAPTURE_FPS

    while _running:
        t0 = time.perf_counter()

        if use_dxgi and cam is not None:
            frame = cam.get_latest_frame()
            if frame is None:
                time.sleep(0.02)
                continue
            curr_bgr = frame
        else:
            curr_bgr = _grab_mss()
            if curr_bgr is None:
                time.sleep(0.1)
                continue

        with _snapshot_lock:
            _snapshot = curr_bgr.copy()

        if prev_bgr is None:
            prev_bgr = curr_bgr
            curr_gray = cv2.cvtColor(curr_bgr, cv2.COLOR_BGR2GRAY)
            prev_hash = _phash(curr_gray)
            elapsed = time.perf_counter() - t0
            time.sleep(max(0, interval - elapsed))
            continue

        curr_gray = cv2.cvtColor(curr_bgr, cv2.COLOR_BGR2GRAY)
        curr_hash = _phash(curr_gray)
        audio_rms = _get_audio_rms()

        event = _classify_frame(prev_bgr, curr_bgr, prev_hash, curr_hash, audio_rms)

        if event.kind != "static":
            try:
                _event_queue.put_nowait(event)
            except queue.Full:
                pass  # drop oldest not newest — consumer is slow

        prev_bgr = curr_bgr
        prev_hash = curr_hash

        elapsed = time.perf_counter() - t0
        time.sleep(max(0, interval - elapsed))

    if use_dxgi and cam is not None:
        try:
            cam.stop()
        except Exception:
            pass


def start(get_audio_rms_fn: Callable[[], float] = lambda: 0.0):
    global _running, _thread, _get_audio_rms
    if _running:
        return
    _get_audio_rms = get_audio_rms_fn
    _running = True
    _thread = threading.Thread(target=_capture_loop, daemon=True)
    _thread.start()


def stop():
    global _running
    _running = False
    if _thread:
        _thread.join(timeout=3)


def get_event(timeout: float = 1.0) -> Optional[FrameEvent]:
    try:
        return _event_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def get_snapshot() -> Optional[np.ndarray]:
    with _snapshot_lock:
        return _snapshot.copy() if _snapshot is not None else None
