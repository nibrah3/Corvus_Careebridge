"""
ffmpeg clip assembler — takes buffered DXGI frames + WASAPI audio chunks,
produces a 720p CRF28 MP4 with synced audio using variable frame rate (VFR).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from typing import List, Optional, Tuple

import numpy as np

from ._audio import save_wav


def assemble_clip(
    frames: List[Tuple[np.ndarray, float]],   # [(bgr_array, timestamp), ...]
    audio_chunks: List[dict],                  # from _audio.get_window()
    output_path: str,
    target_height: int = 720,
    crf: int = 28,
) -> str:
    """
    Assemble frames + audio into an MP4 using ffmpeg VFR muxing.

    frames: list of (bgr_ndarray, wall_clock_time) sorted by time
    audio_chunks: list of WASAPI chunk dicts
    output_path: destination .mp4
    Returns: output_path
    """
    if not frames:
        raise ValueError("No frames to assemble")

    import cv2

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # ── Sort frames by timestamp ──────────────────────────────────────────────
    frames = sorted(frames, key=lambda x: x[1])
    t0 = frames[0][1]

    # ── Scale frames to target height ────────────────────────────────────────
    h0, w0 = frames[0][0].shape[:2]
    if h0 != target_height:
        scale = target_height / h0
        new_w = int(w0 * scale)
        new_w = new_w if new_w % 2 == 0 else new_w + 1
        new_h = target_height
    else:
        new_w, new_h = w0, h0
    if new_w % 2 != 0:
        new_w += 1

    with tempfile.TemporaryDirectory() as tmp:
        # ── Write frame PNGs + timing file ───────────────────────────────────
        timing_file = os.path.join(tmp, "frames.txt")
        entries = []
        for i, (bgr, ts) in enumerate(frames):
            scaled = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            frame_path = os.path.join(tmp, f"f{i:06d}.jpg")
            cv2.imwrite(frame_path, scaled, [cv2.IMWRITE_JPEG_QUALITY, 85])
            offset_s = ts - t0
            entries.append((frame_path, offset_s))

        with open(timing_file, "w") as f:
            for i, (fpath, offset_s) in enumerate(entries):
                f.write(f"file '{fpath}'\n")
                if i + 1 < len(entries):
                    duration = entries[i + 1][1] - offset_s
                else:
                    duration = 1.0 / 30
                f.write(f"duration {duration:.6f}\n")
            # repeat last frame (ffmpeg concat demuxer requirement)
            f.write(f"file '{entries[-1][0]}'\n")

        # ── Write audio WAV ───────────────────────────────────────────────────
        has_audio = bool(audio_chunks)
        audio_path = ""
        if has_audio:
            audio_path = os.path.join(tmp, "audio.wav")
            save_wav(audio_chunks, audio_path)

        # ── Run ffmpeg ────────────────────────────────────────────────────────
        video_only = os.path.join(tmp, "video_only.mp4")

        cmd_video = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", timing_file,
            "-vf", f"scale={new_w}:{new_h}",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-vsync", "vfr",
            video_only,
        ]
        subprocess.run(cmd_video, check=True, capture_output=True)

        if has_audio:
            cmd_mux = [
                "ffmpeg", "-y",
                "-i", video_only,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                output_path,
            ]
            subprocess.run(cmd_mux, check=True, capture_output=True)
        else:
            import shutil
            shutil.copy(video_only, output_path)

    return output_path
