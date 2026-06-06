#!/usr/bin/env python3
"""
hook_gate_monitor.py — UserPromptSubmit hook.
Checks Redis for pending pipeline gates. When the desktop pipeline is
paused waiting for user confirmation (name, phone, essay answer, final
submit review), this hook injects the gate so Claude surfaces it as
an AskUserQuestion immediately.

Gate types:
  field_confirm   — user confirms / edits a pre-filled value (name, phone, email)
  essay_gate      — user approves / edits / skips a drafted free-text answer
  submit_review   — user reviews all answers before final submit click
  captcha         — manual captcha solving required
"""
import io, json, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6380
GATE_KEY   = "corvus:pending_gates"


def _peek_gate():
    try:
        import redis
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT,
                        decode_responses=True, socket_connect_timeout=2)
        raw = r.lindex(GATE_KEY, 0)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _format_gate(gate: dict) -> str:
    gate_id   = gate.get("gate_id", "?")
    gate_type = gate.get("type", "essay_gate")
    field     = gate.get("field", "")
    draft     = (gate.get("draft") or "").strip()
    draft_str = f'"{draft[:140]}"' if draft else "(no draft)"

    answer_script = (
        f"C:\\Python314\\python.exe "
        f"E:\\Corvus_Careebridge\\hooks\\answer_gate.py {gate_id}"
    )

    if gate_type == "field_confirm":
        return (
            f"[PIPELINE PAUSED — CONFIRM FIELD]\n"
            f"The pipeline filled in a value and needs your OK before continuing.\n\n"
            f"Field: \"{field}\"\n"
            f"Value: {draft_str}\n\n"
            f"Call AskUserQuestion:\n"
            f"  question: \"Is this correct for '{field}'?\"\n"
            f"  header:   \"Confirm\"\n"
            f"  options:\n"
            f"    - label: \"Yes, looks good\"   → run: {answer_script} approve\n"
            f"    - label: \"No, change it\"     → ask user for correct value, "
            f"then: {answer_script} edit <value>\n"
        )

    if gate_type == "submit_review":
        return (
            f"[PIPELINE PAUSED — FINAL REVIEW BEFORE SUBMIT]\n"
            f"All fields are filled. Review before the pipeline clicks Submit.\n\n"
            f"{draft}\n\n"
            f"Call AskUserQuestion:\n"
            f"  question: \"Ready to submit?\"\n"
            f"  header:   \"Final Review\"\n"
            f"  options:\n"
            f"    - label: \"Submit\"         → run: {answer_script} approve\n"
            f"    - label: \"Wait, go back\"  → run: {answer_script} skip\n"
        )

    if gate_type == "captcha":
        return (
            f"[PIPELINE PAUSED — CAPTCHA]\n"
            f"A CAPTCHA appeared on the page. Solve it manually in the browser.\n"
            f"When done, call AskUserQuestion:\n"
            f"  question: \"Have you solved the CAPTCHA?\"\n"
            f"  header:   \"CAPTCHA\"\n"
            f"  options:\n"
            f"    - label: \"Done, continue\"  → run: {answer_script} approve\n"
            f"    - label: \"Skip this job\"   → run: {answer_script} skip\n"
        )

    # Default: essay_gate
    return (
        f"[PIPELINE PAUSED — ANSWER NEEDED]\n"
        f"The pipeline drafted an answer for a question and needs your decision.\n\n"
        f"Question: \"{field}\"\n"
        f"Draft answer: {draft_str}\n\n"
        f"Call AskUserQuestion:\n"
        f"  question: \"How should I answer: '{field[:80]}'?\"\n"
        f"  header:   \"Review Answer\"\n"
        f"  options:\n"
        f"    - label: \"Use this\"      → run: {answer_script} approve\n"
        f"    - label: \"Change it\"     → ask user for new text, "
        f"then: {answer_script} edit <text>\n"
        f"    - label: \"Leave blank\"   → run: {answer_script} skip\n"
    )


def main():
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass

    gate = _peek_gate()
    if gate:
        print(_format_gate(gate))

    sys.exit(0)


if __name__ == "__main__":
    main()
