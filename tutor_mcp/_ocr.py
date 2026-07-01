"""
OCR wrapper — tries easyocr, falls back gracefully to empty string.

extract_text(image_np)          → str  (from BGR numpy array)
extract_text_from_bytes(bytes)  → str
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

_lock = threading.Lock()
_reader = None          # easyocr reader or None
_backend: str = ""      # "easyocr" | "none"
_init_done = False


def _get_reader():
    global _reader, _backend, _init_done
    if _init_done:
        return _reader
    with _lock:
        if _init_done:
            return _reader
        try:
            import easyocr
            _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            _backend = "easyocr"
        except Exception:
            _reader = None
            _backend = "none"
        _init_done = True
    return _reader


def extract_text(image_bgr: np.ndarray) -> str:
    """Extract text from a BGR numpy array. Returns concatenated text."""
    reader = _get_reader()
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
