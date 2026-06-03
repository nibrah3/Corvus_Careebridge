"""
hook_session_context.py — UserPromptSubmit hook

Detects the session type from the first user message and injects only the
relevant context file. Replaces loading the full 40KB CLAUDE.md for every session.

Session types and their context files:
  assessment  → context/context_assessment.md
  discovery   → context/context_discovery.md
  health      → context/context_health.md
  schools     → context/context_schools.md
  (default)   → no injection (CLAUDE.md alone is sufficient)

Token saving: ~70% reduction on baseline context for automated sessions.
"""
import io
import json
import os
import sys
from pathlib import Path

# Force UTF-8 stdout so context files with arrows/unicode don't crash on cp1252 Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

CB_DIR = Path(os.environ.get("CB_DIR",
    Path(__file__).resolve().parent.parent))

# Session type detection: keywords in the user message → context file
_RULES = [
    # Assessment / application / CV
    (["assessment", "apply", "application", "ixbrowser", "cdp", "skill_prepare",
      "skill_assess", "cv generation", "generate cv", "fill form", "annotation task"],
     "context_assessment.md"),

    # Discovery / gate / graph expand
    (["gate", "discovery", "raw_discoveries", "skill_gate", "skill_clean",
      "graph expand", "skill_graph", "search terms", "seed platforms",
      "skill_seed", "catalogue poll", "gap analysis"],
     "context_discovery.md"),

    # Health / maintenance
    (["health", "sweep", "mcp down", "restart", "tunnel", "port", "daemon",
      "skill_health", "health sweep", "system status", "mcp status"],
     "context_health.md"),

    # Schools
    (["school", "college", "enrollment", "raw_schools", "skill_analyze_schools",
      "community college", "financial aid", "scorecard"],
     "context_schools.md"),
]

_AUTONOMOUS_TRIGGERS = [
    "skill_gate_discoveries",
    "skill_clean_scraped_data",
    "skill_health_sweep",
    "skill_catalogue_poll",
    "skill_gap_analysis",
    "skill_analyze_schools",
    "skill_graph_expand",
    "skill_seed_platforms",
]


def _detect_session_type(message: str) -> str | None:
    low = message.lower()
    for keywords, ctx_file in _RULES:
        if any(kw in low for kw in keywords):
            return ctx_file
    return None


def _load_context(filename: str) -> str:
    path = CB_DIR / "context" / filename
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        data = json.loads(raw)
    except Exception:
        sys.exit(0)

    message = data.get("prompt") or data.get("message") or ""

    # Detect autonomous skill invocations — inject Scout persona too
    is_autonomous = any(t in message for t in _AUTONOMOUS_TRIGGERS)

    ctx_file = _detect_session_type(message)
    if not ctx_file and not is_autonomous:
        sys.exit(0)  # No injection needed — CLAUDE.md is sufficient

    parts = []

    # Inject Scout persona for discovery autonomous runs
    if is_autonomous and "discovery" in (ctx_file or "") or any(
        t in message for t in ["skill_gate", "skill_clean", "skill_graph",
                                "skill_seed", "skill_catalogue", "skill_gap"]
    ):
        scout_path = CB_DIR / "prompts" / "scout_persona.md"
        try:
            parts.append(scout_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Inject session-specific context
    if ctx_file:
        content = _load_context(ctx_file)
        if content:
            parts.append(content)

    if parts:
        print("\n\n---\n\n".join(parts))

    sys.exit(0)


if __name__ == "__main__":
    main()
