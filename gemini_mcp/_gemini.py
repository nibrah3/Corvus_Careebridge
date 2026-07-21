"""
Gemini File API + Video understanding client (google.genai SDK).

Upload flow for large files (>20MB):
  1. client.files.upload(file=path)       → FileObject (uri, state, name)
  2. Poll until file.state.name == "ACTIVE"
  3. client.models.generate_content(...)  → structured response

API key: GEMINI_API_KEY environment variable.
"""
from __future__ import annotations

import concurrent.futures
import os
import time
from pathlib import Path

_UPLOAD_TIMEOUT = 90  # seconds — SDK upload call can block indefinitely without this

_KEY_OVERRIDE: str | None = None


def set_key_override(key: str | None) -> None:
    """Set a temporary API key override for the next _client() call."""
    global _KEY_OVERRIDE
    _KEY_OVERRIDE = key


def get_all_keys() -> list[str]:
    """Read GEMINI_API_KEY, GEMINI_API_KEY_2, GEMINI_API_KEY_3 from .env."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return [k for k in [os.environ.get("GEMINI_API_KEY", "")] if k]
    text = env_file.read_text(encoding="utf-8")
    keys = []
    for suffix in ("", "_2", "_3"):
        name = f"GEMINI_API_KEY{suffix}"
        for line in text.splitlines():
            if line.startswith(f"{name}="):
                v = line.split("=", 1)[1].strip()
                if v:
                    keys.append(v)
                break
    return keys


def _client():
    from google import genai
    if _KEY_OVERRIDE:
        return genai.Client(api_key=_KEY_OVERRIDE)
    # Always read from .env so key rotations take effect without restarting the server.
    api_key = ""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
                break
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not set")
    return genai.Client(api_key=api_key)


def upload_video(video_path: str) -> dict:
    """
    Upload an MP4 to Gemini File API. Polls until ACTIVE.

    Returns {uri, name, size_mb, state, upload_ms} or {error: str}.
    """
    try:
        client = _client()
        path   = Path(video_path)
        if not path.exists():
            return {"error": f"File not found: {video_path}"}

        t0 = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(client.files.upload, file=str(path))
            try:
                uploaded = fut.result(timeout=_UPLOAD_TIMEOUT)
            except concurrent.futures.TimeoutError:
                return {"error": f"Gemini upload timed out after {_UPLOAD_TIMEOUT}s"}

        deadline = time.monotonic() + 120
        while uploaded.state.name != "ACTIVE":
            if time.monotonic() > deadline:
                return {"error": "Gemini file processing timed out after 120s"}
            time.sleep(3)
            uploaded = client.files.get(name=uploaded.name)

        upload_ms = int((time.monotonic() - t0) * 1000)
        return {
            "uri":       uploaded.uri,
            "name":      uploaded.name,
            "size_mb":   round(path.stat().st_size / (1024 * 1024), 2),
            "state":     uploaded.state.name,
            "upload_ms": upload_ms,
        }
    except Exception as exc:
        return {"error": str(exc)}


def analyse_video(file_uri: str, prompt: str, model: str = "gemini-2.5-flash",
                   mime_type: str = "video/mp4") -> dict:
    """
    Send a previously uploaded video (or audio) file to Gemini with a prompt.

    mime_type must match what the file was uploaded as (upload_video() doesn't
    fix the MIME type — it's whatever the caller uploaded). Defaults to
    "video/mp4"; pass e.g. "audio/mp4"/"audio/mpeg"/"audio/wav" for audio files.

    Returns {text, model, elapsed_ms} or {error: str}.
    """
    try:
        from google.genai import types
        client = _client()
        t0     = time.monotonic()
        resp   = client.models.generate_content(
            model=model,
            contents=[types.Part.from_uri(file_uri=file_uri, mime_type=mime_type), prompt],
        )
        return {
            "text":       resp.text,
            "model":      model,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as exc:
        return {"error": str(exc)}


def analyse_image(image_path: str, prompt: str, model: str = "gemini-2.5-flash") -> dict:
    """
    Send a local image file to Gemini inline (no upload needed for images).

    Returns {text, model, elapsed_ms} or {error: str}.
    """
    try:
        from google.genai import types
        import mimetypes
        client = _client()
        path   = Path(image_path)
        mime   = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        t0     = time.monotonic()
        img_bytes = path.read_bytes()
        resp   = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=img_bytes, mime_type=mime), prompt],
        )
        return {
            "text":       resp.text,
            "model":      model,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as exc:
        return {"error": str(exc)}


def upload_file(file_path: str, mime_type: str | None = None) -> dict:
    """
    Upload any file (audio/video/image) to Gemini File API. Polls until ACTIVE.

    Returns {uri, name, size_mb, state, upload_ms, mime_type} or {error: str}.
    """
    try:
        import mimetypes
        client = _client()
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        mime = mime_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        t0 = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(client.files.upload, file=str(path), config={"mime_type": mime})
            try:
                uploaded = fut.result(timeout=_UPLOAD_TIMEOUT)
            except concurrent.futures.TimeoutError:
                return {"error": f"Gemini upload timed out after {_UPLOAD_TIMEOUT}s"}

        deadline = time.monotonic() + 120
        while uploaded.state.name != "ACTIVE":
            if time.monotonic() > deadline:
                return {"error": "Gemini file processing timed out after 120s"}
            time.sleep(3)
            uploaded = client.files.get(name=uploaded.name)

        upload_ms = int((time.monotonic() - t0) * 1000)
        return {
            "uri":       uploaded.uri,
            "name":      uploaded.name,
            "size_mb":   round(path.stat().st_size / (1024 * 1024), 4),
            "state":     uploaded.state.name,
            "upload_ms": upload_ms,
            "mime_type": mime,
        }
    except Exception as exc:
        return {"error": str(exc)}


def analyse_files(
    files: list[dict],  # [{uri, mime_type}, ...]
    prompt: str,
    model: str = "gemini-2.5-flash",
) -> dict:
    """
    Send one or more uploaded files + a text prompt to Gemini in a single call.

    files: list of dicts with keys 'uri' and 'mime_type'.
    Returns {text, model, elapsed_ms} or {error: str}.
    """
    try:
        from google.genai import types
        client = _client()
        t0 = time.monotonic()
        parts = [types.Part.from_uri(file_uri=f["uri"], mime_type=f["mime_type"]) for f in files]
        parts.append(prompt)
        resp = client.models.generate_content(model=model, contents=parts)
        return {
            "text":       resp.text,
            "model":      model,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as exc:
        return {"error": str(exc)}


def analyse_text(context: str, prompt: str, model: str = "gemini-2.5-flash") -> dict:
    """Send a text context + question prompt to Gemini. Returns {text, model, elapsed_ms}."""
    try:
        client = _client()
        t0 = time.monotonic()
        resp = client.models.generate_content(
            model=model,
            contents=f"{context}\n\n---\n\n{prompt}",
        )
        return {
            "text":       resp.text,
            "model":      model,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as exc:
        return {"error": str(exc)}


def delete_file(file_name: str) -> dict:
    """Delete an uploaded file from Gemini File API to free quota."""
    try:
        client = _client()
        client.files.delete(name=file_name)
        return {"status": "deleted", "name": file_name}
    except Exception as exc:
        return {"error": str(exc)}
