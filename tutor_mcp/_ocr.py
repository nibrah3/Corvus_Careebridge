"""
OCR wrapper — tries easyocr, falls back gracefully to empty string.

Initialization is done in a background thread at import time so it never
blocks the server's orchestration loop or tool handlers.

extract_text(image_np)          → str  (from BGR numpy array)
extract_text_from_bytes(bytes)  → str
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

_lock = threading.Lock()
_reader = None          # easyocr reader or None
_backend: str = "none"
_init_done = False


def _init_reader_bg():
    """Initialize easyocr in a background thread — never blocks callers."""
    global _reader, _backend, _init_done
    with _lock:
        if _init_done:
            return
        try:
            import easyocr
            _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            _backend = "easyocr"
        except Exception:
            _reader = None
            _backend = "none"
        _init_done = True


# Kick off initialization immediately so it's ready as soon as possible
_bg_thread = threading.Thread(target=_init_reader_bg, daemon=True)
_bg_thread.start()


def extract_text(image_bgr: np.ndarray) -> str:
    """Extract text from a BGR numpy array. Returns '' if OCR not ready yet."""
    if not _init_done:
        return ""   # still initializing — don't block
    with _lock:
        reader = _reader
    if reader is None:
        return ""
    try:
        import cv2
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        result = reader.readtext(rgb, detail=1, paragraph=False)
        lines = [text for (_, text, conf) in result if conf > 0.5]
        return "\n".join(lines)
    except Exception:
        return ""


def extract_text_from_bytes(jpeg_bytes: bytes) -> str:
    """Extract text from JPEG/PNG bytes."""
    import cv2
    buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        return ""
    return extract_text(img)
