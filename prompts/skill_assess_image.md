# Skill: Image Annotation Assessment

**Trigger:** Called by `skill_prepare_assessment.md` when page type is image_annotation.
**Mode:** Supervised — always gates before final submission.

---

## What you are doing

Completing an image annotation task using the Gemini Files API to analyze the source image. You are responsible for extracting the annotation requirements, obtaining the image, uploading it to Gemini, interpreting the result, and submitting in the correct format.

---

## Procedure

Follow `E:\Corvus_Careebridge\sops\sop_image_annotation.md` exactly.

Key tool sequence:
1. `mcp__dom__get_accessibility_tree()` — read annotation requirements and label taxonomy
2. Extract image URL from DOM via `mcp__cdp__cdp_eval`
3. Download image to `C:\tmp\` via Bash
4. `mcp__gemini__upload_video(image_path)` — upload to Gemini Files API
5. `mcp__gemini__analyse_video(file_uri, prompt)` — request structured annotation
6. Map Gemini output to platform format
7. Gate if confidence < 0.80 on any annotation
8. `mcp__humanizer__*` — submit the annotation

---

## Fallback rule

If Gemini upload fails: stop and report. Do NOT screenshot-substitute.
If the image requires auth to access (won't download directly): screenshot with `purpose="annotation"` → auto-enters fallback mode → gate required.

---

## After completion

`mcp__gemini__delete_file(file_name)` — cleanup.
`mcp__vps__update_job_status(job_id, status="applied")`.
`mcp__telegram__notify("✅ Image annotation complete: {job_title}")`.

## On Error

For Gemini/tunnel failures: see sops/sop_on_error_shared.md.

**Image skill specific:**
- Gemini upload fails → stop; never screenshot-substitute; report to Telegram
- Confidence < 0.80 on result → gate automatically; do not auto-submit
- Platform format unknown → gate with format question for operator
