"""
SessionDoc — real-time session.md builder.

Accumulates OCR text, image refs, video refs as the worker navigates.
Serialises to a Markdown document that fits inline into a Claude prompt (~50KB).
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class _Entry:
    kind: str   # navigation | scroll | video | image | note
    ts: float
    text: str = ""
    image_ref: Optional[str] = None
    video_ref: Optional[str] = None
    gemini_uri: Optional[str] = None
    gemini_analysis: Optional[str] = None


class SessionDoc:
    def __init__(self, session_id: Optional[str] = None):
        self._id = session_id or time.strftime("%Y%m%d_%H%M%S")
        self._entries: List[_Entry] = []
        self._lock = threading.Lock()
        self._page_counter = 0

    def _ts(self, ts: float) -> str:
        return time.strftime("%H:%M:%S", time.localtime(ts))

    def add_navigation(
        self,
        ts: float,
        ocr_text: str,
        image_ref: Optional[str] = None,
        gemini_analysis: Optional[str] = None,
    ):
        self._page_counter += 1
        with self._lock:
            self._entries.append(
                _Entry(
                    kind="navigation",
                    ts=ts,
                    text=ocr_text,
                    image_ref=image_ref,
                    gemini_analysis=gemini_analysis,
                )
            )

    def add_scroll_content(self, ts: float, ocr_text: str):
        with self._lock:
            self._entries.append(_Entry(kind="scroll", ts=ts, text=ocr_text))

    def add_video_ref(
        self,
        ts: float,
        clip_path: str,
        gemini_uri: Optional[str] = None,
        gemini_analysis: Optional[str] = None,
    ):
        with self._lock:
            self._entries.append(
                _Entry(
                    kind="video",
                    ts=ts,
                    video_ref=clip_path,
                    gemini_uri=gemini_uri,
                    gemini_analysis=gemini_analysis,
                )
            )

    def add_image_region(
        self,
        ts: float,
        image_path: str,
        gemini_analysis: Optional[str] = None,
    ):
        with self._lock:
            self._entries.append(
                _Entry(
                    kind="image",
                    ts=ts,
                    image_ref=image_path,
                    gemini_analysis=gemini_analysis,
                )
            )

    def add_note(self, ts: float, text: str):
        with self._lock:
            self._entries.append(_Entry(kind="note", ts=ts, text=text))

    def to_markdown(self) -> str:
        with self._lock:
            entries = list(self._entries)

        lines = [f"# Session {self._id}\n"]
        nav_num = 0

        for e in entries:
            t = self._ts(e.ts)
            if e.kind == "navigation":
                nav_num += 1
                lines.append(f"\n## Page {nav_num} — {t}\n")
                if e.image_ref:
                    lines.append(f"![screenshot]({e.image_ref})\n")
                if e.text:
                    lines.append("```\n" + e.text.strip() + "\n```\n")
                if e.gemini_analysis:
                    lines.append(f"> **Gemini:** {e.gemini_analysis}\n")

            elif e.kind == "scroll":
                if e.text.strip():
                    lines.append(f"\n### Scroll content — {t}\n")
                    lines.append("```\n" + e.text.strip() + "\n```\n")

            elif e.kind == "video":
                lines.append(f"\n### Video clip — {t}\n")
                if e.video_ref:
                    lines.append(f"- Local: `{e.video_ref}`\n")
                if e.gemini_uri:
                    lines.append(f"- Gemini URI: `{e.gemini_uri}`\n")
                if e.gemini_analysis:
                    lines.append(f"\n> **Gemini:** {e.gemini_analysis}\n")

            elif e.kind == "image":
                lines.append(f"\n### Image — {t}\n")
                if e.image_ref:
                    lines.append(f"![region]({e.image_ref})\n")
                if e.gemini_analysis:
                    lines.append(f"> **Gemini:** {e.gemini_analysis}\n")

            elif e.kind == "note":
                lines.append(f"\n> _{t}: {e.text}_\n")

        return "".join(lines)

    def save(self, path: str) -> str:
        content = self.to_markdown()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def size_kb(self) -> float:
        return len(self.to_markdown().encode()) / 1024

    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)
