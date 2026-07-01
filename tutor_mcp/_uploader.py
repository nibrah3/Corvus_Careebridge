"""
BackgroundUploader — queues MP4 paths and uploads them to Gemini File API
in a background thread. Caches URI by path so clips can be queried immediately
after upload completes.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Dict, Optional

_SENTINEL = None


class BackgroundUploader:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._cache: Dict[str, dict] = {}  # path → {uri, name, state, ...}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._queue.put(_SENTINEL)
        if self._thread:
            self._thread.join(timeout=5)

    def enqueue(self, path: str):
        """Queue a video path for background upload."""
        with self._lock:
            if path in self._cache:
                return  # already uploaded or queued
            self._cache[path] = {"state": "queued"}
        self._queue.put(path)

    def get_uri(self, path: str) -> Optional[str]:
        """Return the Gemini file URI once upload completes, or None if pending."""
        with self._lock:
            info = self._cache.get(path)
        if info and info.get("state") == "ACTIVE":
            return info.get("uri")
        return None

    def wait_for_uri(self, path: str, timeout: float = 120.0) -> Optional[str]:
        """Block until URI is available or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            uri = self.get_uri(path)
            if uri:
                return uri
            with self._lock:
                info = self._cache.get(path, {})
            if info.get("state") == "error":
                return None
            time.sleep(1.0)
        return None

    def get_info(self, path: str) -> dict:
        with self._lock:
            return dict(self._cache.get(path, {}))

    def _worker(self):
        # Resolve the gemini upload function without circular import
        sys.path.insert(0, "E:\\Corvus_Careebridge\\gemini_mcp")
        try:
            from _gemini import upload_video as _upload
        except ImportError:
            _upload = None

        while self._running:
            path = self._queue.get()
            if path is _SENTINEL:
                break

            if _upload is None:
                with self._lock:
                    self._cache[path] = {"state": "error", "reason": "gemini not importable"}
                continue

            try:
                with self._lock:
                    self._cache[path]["state"] = "uploading"

                result = _upload(path)  # blocks until ACTIVE
                with self._lock:
                    self._cache[path] = {
                        "state": result.get("state", "ACTIVE"),
                        "uri": result.get("uri"),
                        "name": result.get("name"),
                        "size_mb": result.get("size_mb"),
                        "upload_ms": result.get("upload_ms"),
                    }
            except Exception as exc:
                with self._lock:
                    self._cache[path] = {"state": "error", "reason": str(exc)}
