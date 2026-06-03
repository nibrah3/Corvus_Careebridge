# Skill: Text Assessment and Form Filling

**Trigger:** Called by `skill_prepare_assessment.md` when page type is text_assessment, essay, or MCQ.
**Mode:** Supervised (default) or throughput.

---

## What you are doing

Completing a text-based assessment or application form on behalf of a candidate. You are the reasoning engine — you read the questions, determine the best answers for the candidate, and direct the humanizer to enter them.

---

## Procedure

Follow `E:\Corvus_Careebridge\sops\sop_text_assessment.md` exactly.

Key tool sequence:
1. `mcp__dom__get_accessibility_tree()` — read all questions
2. Reason in context about each answer using the candidate's profile
3. `mcp__answer__humanize(answer, question, profile_id)` — apply persona voice to prose answers
4. `mcp__humanizer__click` / `mcp__humanizer__type` — enter each answer
5. For supervised mode: `mcp__vps__push_gate_answer(answer, context)` before free-text submission

---

## Gate rules

- Supervised mode: gate all free-text answers and the final Submit click
- MCQ: auto-submit if confidence ≥ 0.85
- If any question cannot be answered from the profile: gate it with a note

---

## After completion

Call `mcp__vps__update_job_status(job_id, status="applied")`.
Call `mcp__telegram__notify("✅ Assessment complete: {job_title} / {profile_name}")`.
