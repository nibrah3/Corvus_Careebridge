"""
cv_generator.py — ATS-safe CV formatter.

Accepts pre-written CV sections from Claude Code and produces a plain-text
+ PDF output. All content intelligence (requirement extraction, keyword
mapping, bullet rewriting) happens in Claude Code's context before this
file is called — this module is a formatter, not a reasoner.

ATS rules enforced:
  - No tables, columns, graphics, text boxes, headers/footers
  - Standard section headings recognised by Greenhouse, Lever, Workday, Taleo
  - PDF via reportlab (falls back to .txt only if unavailable)
"""
import os
import re
import logging

log = logging.getLogger(__name__)


# ── Profile field parsers ──────────────────────────────────────────────────────

def _parse_skills(raw) -> list[str]:
    if not raw:
        return []
    try:
        import json
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, list):
            return [str(s) for s in parsed]
    except Exception:
        pass
    return [s.strip() for s in re.split(r"[,;|]", str(raw)) if s.strip()]


def _parse_experience(raw) -> list[dict]:
    if not raw:
        return []
    try:
        import json
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return [{"role": "Freelancer", "company": "Independent", "years": "", "desc": str(raw)}]


# ── CV text builder ────────────────────────────────────────────────────────────

def _build_cv_text(sections: dict, profile: dict) -> str:
    """
    Build ATS-safe plain text from pre-written sections.

    sections keys (all optional, Claude Code provides these):
        summary              — professional summary paragraph
        skills               — list of skill strings (ordered by JD priority)
        experience_bullets   — list of { role, company, years, bullets: [str] }
        qualifications       — list of qualification bullet strings
        education            — raw education text

    Falls back to profile fields if section is missing.
    """
    name     = (profile.get("name") or "").upper()
    email    = profile.get("email") or ""
    phone    = profile.get("phone") or ""
    location = profile.get("location") or ""

    summary       = sections.get("summary") or profile.get("bio") or ""
    skills        = sections.get("skills")  or _parse_skills(profile.get("skills"))
    experience    = sections.get("experience_bullets") or _parse_experience(profile.get("experience"))
    qualifications = sections.get("qualifications") or []
    education_raw = sections.get("education") or profile.get("education") or ""

    L = []

    # Header
    L.append(name)
    L.append(" | ".join(p for p in [email, phone, location] if p))
    L.append("")

    # Summary
    if summary:
        L.append("PROFESSIONAL SUMMARY")
        L.append("-" * 50)
        L.append(summary[:600])
        L.append("")

    # Core skills
    if skills:
        L.append("CORE SKILLS")
        L.append("-" * 50)
        skill_list = skills if isinstance(skills, list) else _parse_skills(skills)
        for i in range(0, len(skill_list), 4):
            L.append("  " + " | ".join(skill_list[i:i + 4]))
        L.append("")

    # Experience
    if experience:
        L.append("PROFESSIONAL EXPERIENCE")
        L.append("-" * 50)
        for exp in experience:
            if isinstance(exp, dict):
                role    = exp.get("role") or ""
                company = exp.get("company") or ""
                years   = exp.get("years") or ""
                bullets = exp.get("bullets") or []
                desc    = exp.get("desc") or ""
                dur = f"{years} year{'s' if str(years) != '1' else ''}" if years else ""
                header = " | ".join(p for p in [role, company, dur] if p)
                L.append(header)
                if bullets:
                    for bullet in bullets:
                        if bullet:
                            L.append(f"  - {bullet}")
                elif desc:
                    for bullet in re.split(r"\.\s+|\n", desc):
                        bullet = bullet.strip()
                        if bullet:
                            L.append(f"  - {bullet}")
                L.append("")

    # Education
    if education_raw:
        L.append("EDUCATION")
        L.append("-" * 50)
        L.append(education_raw)
        L.append("")

    # Relevant qualifications (ATS keyword mirroring section)
    if qualifications:
        L.append("RELEVANT QUALIFICATIONS")
        L.append("-" * 50)
        for q in qualifications[:8]:
            L.append(f"  - {q}")
        L.append("")

    return "\n".join(L)


# ── PDF output ─────────────────────────────────────────────────────────────────

def _build_pdf(text: str, path: str) -> bool:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        styles = getSampleStyleSheet()
        body = ParagraphStyle("body", parent=styles["Normal"],
                              fontSize=10, leading=14, fontName="Helvetica")
        bold = ParagraphStyle("bold", parent=styles["Normal"],
                              fontSize=11, leading=14, fontName="Helvetica-Bold")

        doc = SimpleDocTemplate(path, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        story = []
        for line in text.split("\n"):
            stripped = line.strip()
            if re.match(r"^[A-Z ]{4,}$", stripped):
                story.append(Paragraph(stripped, bold))
            elif stripped.startswith("-" * 10):
                story.append(Spacer(1, 4))
            elif stripped:
                story.append(Paragraph(stripped.replace("&", "&amp;"), body))
            else:
                story.append(Spacer(1, 6))
        doc.build(story)
        return True
    except Exception as e:
        log.warning("PDF generation failed: %s", e)
        return False


# ── Humanization helper ───────────────────────────────────────────────────────

def _humanize_summary(summary: str, persona_prompt: str, profile_id: str) -> str:
    try:
        from answer_mcp._humanize import humanize
        return humanize(
            canonical_answer=summary,
            question="Write a professional summary for my CV",
            persona_prompt=persona_prompt,
            profile_id=profile_id,
            target_words=min(80, len(summary.split()) + 10),
        )
    except Exception as e:
        log.warning("CV summary humanization failed: %s — using as-is", e)
        return summary


def _load_persona(profile: dict) -> str:
    profile_id = profile.get("profile_id") or profile.get("id") or ""
    if not profile_id:
        return ""
    try:
        from answer_mcp._persona import get_persona_prompt, generate_persona
        prompt = get_persona_prompt(profile_id)
        if not prompt:
            prompt = generate_persona(profile_id, {
                "name":       profile.get("name", ""),
                "background": (profile.get("bio") or "")[:200],
            })["persona_prompt"]
        return prompt or ""
    except Exception as e:
        log.debug("Persona load failed (non-fatal): %s", e)
        return ""


# ── Public entry points ────────────────────────────────────────────────────────

def generate_cv(sections: dict, profile: dict, job_meta: dict,
                out_dir: str = "E:/Corvus_Careebridge/cvs") -> dict:
    """
    Format and save a CV from pre-written sections.

    Args:
        sections:  Dict with keys: summary, skills, experience_bullets,
                   qualifications, education.  Written by Claude Code.
        profile:   Full profile dict from VPS postgres.
        job_meta:  { id, title, company } — used for filename only.
        out_dir:   Output directory.

    Returns:
        { txt_path, pdf_path, persona_applied }
    """
    os.makedirs(out_dir, exist_ok=True)

    persona_prompt = _load_persona(profile)
    if persona_prompt and sections.get("summary"):
        pid = profile.get("profile_id") or profile.get("id") or ""
        sections = dict(sections)
        sections["summary"] = _humanize_summary(
            sections["summary"], persona_prompt, pid
        )

    cv_text = _build_cv_text(sections, profile)

    safe_p = re.sub(r"[^a-z0-9_-]", "_",
                    (profile.get("id") or "profile").lower())
    safe_j = re.sub(r"[^a-z0-9_-]", "_",
                    str(job_meta.get("id") or "job").lower())
    base = os.path.join(out_dir, f"cv_{safe_p}_{safe_j}")

    txt_path = base + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(cv_text)

    pdf_path = base + ".pdf"
    pdf_ok = _build_pdf(cv_text, pdf_path)

    return {
        "txt_path":        txt_path,
        "pdf_path":        pdf_path if pdf_ok else None,
        "persona_applied": bool(persona_prompt),
    }


def generate_cover_letter(profile: dict, job: dict,
                          cover_text: str = "",
                          out_dir: str = "E:/Corvus_Careebridge/cvs") -> dict:
    """
    Save a cover letter to disk.

    cover_text: The letter content written by Claude Code.
                If empty, generates a minimal canonical letter.
    """
    import json as _json

    name      = profile.get("name") or "Applicant"
    job_title = job.get("title") or "the position"
    company   = job.get("company") or "your company"

    if not cover_text:
        skills = _parse_skills(profile.get("skills"))[:3]
        bio    = (profile.get("bio") or "")[:200]
        cover_text = (
            f"Dear Hiring Manager,\n\n"
            f"I am writing to apply for the {job_title} role at {company}. "
            f"{bio + ' ' if bio else ''}"
            f"I bring hands-on experience with {', '.join(skills) if skills else 'relevant skills'}.\n\n"
            f"I look forward to the opportunity to contribute to {company}.\n\n"
            f"Sincerely,\n{name}"
        )

    persona_prompt = _load_persona(profile)
    if persona_prompt:
        profile_id = profile.get("profile_id") or profile.get("id") or ""
        try:
            from answer_mcp._humanize import humanize
            cover_text = humanize(
                canonical_answer=cover_text,
                question=f"Write a cover letter for {job_title} at {company}",
                persona_prompt=persona_prompt,
                profile_id=profile_id,
                target_words=220,
            )
        except Exception as e:
            log.warning("Cover letter humanization failed: %s", e)

    os.makedirs(out_dir, exist_ok=True)
    safe_p = re.sub(r"[^a-z0-9_-]", "_",
                    (profile.get("id") or "profile").lower())
    safe_j = re.sub(r"[^a-z0-9_-]", "_",
                    str(job.get("id") or "job").lower())
    txt_path = os.path.join(out_dir, f"cover_{safe_p}_{safe_j}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(cover_text)

    return {
        "txt_path":        txt_path,
        "persona_applied": bool(persona_prompt),
    }
