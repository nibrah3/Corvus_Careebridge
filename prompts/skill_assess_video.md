# Skill: Video Annotation Assessment

**Trigger:** Called by `skill_prepare_assessment.md` when page type is video_annotation.
**Mode:** Supervised — always gates before final submission.

---

## What you are doing

Completing a video annotation task using the Gemini Files API to analyze the actual source video. This is not a screen recording of the browser — you are uploading the video file itself so Gemini can analyze its content at full quality.

---

## Procedure

Follow `E:\Corvus_Careebridge\sops\sop_video_annotation.md` exactly.

Key tool sequence:
1. `mcp__dom__get_accessibility_tree()` — extract annotation requirements, label taxonomy, output format
2. Identify video source URL from DOM (direct download preferred over screen recording)
3. Download video to `C:\tmp\` via Bash (or record screen as fallback)
4. `mcp__gemini__upload_video(video_path)` — upload to Gemini Files API (handles up to 2GB)
5. `mcp__gemini__analyse_video(file_uri, prompt)` — structured annotation request
6. Map output to platform format (COCO / YOLO / custom JSON / timeline segments)
7. Gate if any segment confidence < 0.80
8. `mcp__humanizer__*` — submit
9. `mcp__gemini__delete_file(file_name)` — cleanup

---

## Output format guide

| Platform shows | Format to use |
|---|---|
| JSON schema / template | Follow the schema exactly |
| "COCO format" | `{ "annotations": [{ "bbox": [x,y,w,h], "category_id": N, "score": F }] }` |
| "YOLO format" | One line per object: `class_id cx cy w h` (normalized 0-1) |
| Timeline / segments | `[{ "start": N, "end": N, "label": "X", "confidence": F }]` |
| Free text description | Natural language description with timestamps |

---

## Fallback rule

If video cannot be downloaded AND screen recording fails: stop and notify.
Screen recording (fallback) always triggers fallback mode and requires gate.

---

## After completion

`mcp__vps__update_job_status(job_id, status="applied")`.
`mcp__telegram__notify("✅ Video annotation complete: {job_title}")`.

## On Error

For Gemini/tunnel failures: see sops/sop_on_error_shared.md.

**Video skill specific:**
- Video too large (>2GB) → split into segments; upload each; combine annotations
- Download fails → try screen recording fallback (fallback mode activates automatically)
- Gemini stuck PROCESSING >90s → delete file; re-upload; if fails again: report and stop
