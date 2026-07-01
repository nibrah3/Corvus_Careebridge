"""
WASAPI loopback audio capture with rolling timestamped buffer.

Rolling deque of chunks: {data: bytes, system_time: float, duration_ms: float,
                          sample_rate: int, channels: int}
"""
from __future__ import annotations

import io
import struct
import threading
import time
import wave
from collections import deque
from typing import List, Optional

_BUFFER_SECONDS = 600  # 10-minute rolling window
_CHUNK_FRAMES = 1024

_lock = threading.Lock()
_buffer: deque = deque()
_stream = None
_pa = None
_thread: Optional[threading.Thread] = None
_running = False
_sample_rate = 44100
_channels = 2


def _trim_buffer():
    """Drop chunks older than _BUFFER_SECONDS."""
    cutoff = time.time() - _BUFFER_SECONDS
    while _buffer and _buffer[0]["system_time"] < cutoff:
        _buffer.popleft()


def _record_loop(stream, sample_rate: int, channels: int):
    global _running
    while _running:
        try:
            raw = stream.read(_CHUNK_FRAMES, exception_on_overflow=False)
        except Exception:
            time.sleep(0.01)
            continue

        duration_ms = (_CHUNK_FRAMES / sample_rate) * 1000
        chunk = {
            "data": raw,
            "system_time": time.time(),
            "duration_ms": duration_ms,
            "sample_rate": sample_rate,
            "channels": channels,
        }
        with _lock:
            _buffer.append(chunk)
            _trim_buffer()


def start() -> bool:
    """Start WASAPI loopback capture. Returns True on success."""
    global _stream, _pa, _thread, _running, _sample_rate, _channels

    if _running:
        return True

    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return False

    try:
        pa = pyaudio.PyAudio()
        wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = pa.get_device_info_by_index(
            wasapi_info["defaultOutputDevice"]
        )

        if not default_speakers.get("isLoopbackDevice", False):
            # Find the loopback sibling
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                if (
                    dev.get("isLoopbackDevice", False)
                    and default_speakers["name"] in dev["name"]
                ):
                    default_speakers = dev
                    break

        sr = int(default_speakers["defaultSampleRate"])
        ch = int(default_speakers["maxInputChannels"])
        ch = max(1, ch)

        stream = pa.open(
            format=pyaudio.paInt16,
            channels=ch,
            rate=sr,
            input=True,
            frames_per_buffer=_CHUNK_FRAMES,
            input_device_index=default_speakers["index"],
        )

        _pa = pa
        _stream = stream
        _sample_rate = sr
        _channels = ch
        _running = True

        _thread = threading.Thread(
            target=_record_loop, args=(stream, sr, ch), daemon=True
        )
        _thread.start()
        return True

    except Exception:
        return False


def stop():
    global _running, _stream, _pa
    _running = False
    if _thread:
        _thread.join(timeout=2)
    if _stream:
        try:
            _stream.stop_stream()
            _stream.close()
        except Exception:
            pass
    if _pa:
        try:
            _pa.terminate()
        except Exception:
            pass
    _stream = None
    _pa = None


def get_rms() -> float:
    """RMS of the last 0.5s of audio (0.0–1.0 normalised for int16)."""
    with _lock:
        if not _buffer:
            return 0.0
        cutoff = time.time() - 0.5
        recent = [c for c in _buffer if c["system_time"] >= cutoff]

    if not recent:
        return 0.0

    samples = []
    for chunk in recent:
        sr = chunk["sample_rate"]
        ch = chunk["channels"]
        raw = chunk["data"]
        n = len(raw) // 2
        unpacked = struct.unpack(f"<{n}h", raw)
        samples.extend(unpacked)

    if not samples:
        return 0.0

    sq_sum = sum(s * s for s in samples)
    rms = (sq_sum / len(samples)) ** 0.5
    return min(1.0, rms / 32768.0)


def get_window(start_time: float, end_time: float) -> List[dict]:
    """Return chunks whose system_time falls within [start_time, end_time]."""
    with _lock:
        return [
            c for c in _buffer
            if start_time <= c["system_time"] <= end_time
        ]


def get_recent(seconds: float = 30.0) -> List[dict]:
    """Return the last N seconds of audio chunks."""
    cutoff = time.time() - seconds
    with _lock:
        return [c for c in _buffer if c["system_time"] >= cutoff]


def save_wav(chunks: List[dict], path: str) -> str:
    """Write a list of chunks to a WAV file. Returns path."""
    if not chunks:
        raise ValueError("No audio chunks to save")

    sr = chunks[0]["sample_rate"]
    ch = chunks[0]["channels"]

    with wave.open(path, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sr)
        for chunk in chunks:
            wf.writeframes(chunk["data"])

    return path
