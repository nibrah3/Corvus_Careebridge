"""
hook_inject_sop.py — PostToolUse hook on mcp__vps__approve_job

Fires after a job is approved. Reads the job's job_type from the tool result,
loads the corresponding SOP file, and injects it as a mandatory instruction
block into Claude's context.

Claude does not "optionally read a document" — it receives the SOP as a
system-level instruction it must follow for this session.
"""
import json
import os
import sys

CB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOP_DIR = os.path.join(CB_DIR, "sops")

JOB_TYPE_TO_SOP = {
    "video_annotation":  "sop_video_annotation.md",
    "image_annotation":  "sop_image_annotation.md",
    "data_annotation":   "sop_image_annotation.md",
    "ai_training":       "sop_text_assessment.md",
    "search_rating":     "sop_text_assessment.md",
    "transcription":     "sop_text_assessment.md",
    "translation":       "sop_text_assessment.md",
    "content_writing":   "sop_text_assessment.md",
    "social_media":      "sop_text_assessment.md",
    "virtual_assistant": "sop_text_assessment.md",
    "customer_support":  "sop_text_assessment.md",
    "microtask":         "sop_text_assessment.md",
    "tutoring":          "sop_text_assessment.md",
    "testing":           "sop_text_assessment.md",
    "moderation":        "sop_text_assessment.md",
    "gpt":               "sop_text_assessment.md",
    "other_gig":         "sop_text_assessment.md",
}


def _load_sop(filename: str) -> str:
    path = os.path.join(SOP_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


_REDIS_HOST = os.environ.get("VPS_REDIS_HOST", "127.0.0.1")
_REDIS_PORT = int(os.environ.get("VPS_REDIS_PORT", "6380"))


def _redis_get(key: str) -> str | None:
    import socket
    try:
        with socket.create_connection((_REDIS_HOST, _REDIS_PORT), timeout=2) as sock:
            cmd = f"*2\r\n$3\r\nGET\r\n${len(key)}\r\n{key}\r\n"
            sock.sendall(cmd.encode())
            reply = sock.recv(256).decode(errors="replace").strip()
            if reply.startswith("$-1"):
                return None
            lines = reply.split("\r\n")
            return lines[1] if len(lines) > 1 else None
    except Exception:
        return None


def _redis_setex(key: str, seconds: int, value: str) -> None:
    import socket
    try:
        with socket.create_connection((_REDIS_HOST, _REDIS_PORT), timeout=2) as sock:
            cmd = f"*4\r\n$5\r\nSETEX\r\n${len(key)}\r\n{key}\r\n${len(str(seconds))}\r\n{seconds}\r\n${len(value)}\r\n{value}\r\n"
            sock.sendall(cmd.encode())
            sock.recv(64)
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        data = json.loads(raw)
    except Exception:
        sys.exit(0)

    # Extract job_type from the tool result
    tool_result = data.get("tool_result") or {}
    content = tool_result.get("content") or []
    job_type = ""
    job_id = ""

    for item in content:
        text = item.get("text") or ""
        try:
            result = json.loads(text)
            job_type = result.get("job_type") or ""
            job_id = str(result.get("job_id") or "")
        except Exception:
            pass

    # Session-aware injection: only inject full SOPs once per session (per process group)
    # After first injection, just acknowledge — saves 80% of SOP injection tokens
    session_key = f"corvus:sop_injected:{os.getpid()}"
    already_injected = _redis_get(session_key)

    if already_injected:
        # SOPs already active this session — brief acknowledgment only
        type_label = job_type or "text_assessment"
        print(
            f"SOPs acknowledged for this session (job_type: {type_label}). "
            f"Fallback rules, browser launch procedure, and {JOB_TYPE_TO_SOP.get(job_type, 'sop_text_assessment.md')} "
            f"remain active from initial injection. Proceed with skill_prepare_assessment.md."
        )
        sys.exit(0)

    # First injection this session — full SOPs
    fallback_sop = _load_sop("sop_fallback_rules.md")
    browser_sop  = _load_sop("sop_browser_launch.md")
    task_sop     = _load_sop(JOB_TYPE_TO_SOP.get(job_type, "sop_text_assessment.md"))

    if not fallback_sop:
        sys.exit(0)

    # Mark as injected for this session (TTL 2 hours)
    _redis_setex(session_key, 7200, "1")

    job_label  = f"Job {job_id}" if job_id else "this job"
    type_label = job_type or "text_assessment"

    injection = f"""
=== MANDATORY PROCEDURE FOR {job_label.upper()} (type: {type_label}) ===

The following rules and procedures MUST be followed. They are not suggestions.

--- FALLBACK RULES (always active) ---
{fallback_sop}

--- BROWSER LAUNCH PROCEDURE ---
{browser_sop}

--- ASSESSMENT PROCEDURE FOR THIS JOB TYPE ---
{task_sop}

=== END OF MANDATORY PROCEDURE ===

Start with skill_prepare_assessment.md. Do not take any assessment action before completing the browser launch procedure.
""".strip()

    print(injection)
    sys.exit(0)


if __name__ == "__main__":
    main()
