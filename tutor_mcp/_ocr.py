"""
PaddleOCR wrapper — lazy-initialised, thread-safe.

extract_text(image_np)    → str  (from BGR numpy array)
extract_text_from_bytes(jpeg_bytes) → str
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

_lock = threading.Lock()
_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        with _lock:
            if _ocr is None:
                from paddleocr import PaddleOCR
                _ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    return _ocr


def extract_text(image_bgr: np.ndarray) -> str:
    """Extract text from a BGR numpy array. Returns concatenated text."""
    ocr = _get_ocr()
    result = ocr.ocr(image_bgr, cls=True)
    if not result or not result[0]:
        return ""
    lines = []
    for line in result[0]:
        if line and len(line) >= 2 and line[1] and len(line[1]) >= 2:
            text, conf = line[1][0], line[1][1]
            if conf > 0.5:
                lines.append(text)
    return "\n".join(lines)


def extract_text_from_bytes(jpeg_bytes: bytes) -> str:
    """Extract text from JPEG/PNG bytes."""
    import cv2

    buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        return ""
    return extract_text(img)
