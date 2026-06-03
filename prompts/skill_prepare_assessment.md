# Skill: Prepare Assessment Session

**Trigger:** Called before any assessment or application run. Always run this first.
**Mode:** Interactive — user may be present.

---

## What you are doing

You are setting up the browser session for an assessment or application. This skill:
1. Connects to IXBrowser for the given profile
2. Detects whether the assessment tab is already open
3. Identifies the assessment type on the page
4. Routes to the appropriate assessment skill

---

## Required inputs

You need before starting:
- `profile_id` — the candidate's Postgres profile ID
- `job_url` — the assessment or application URL
- `job_type` — if known from the job record (text / image_annotation / video_annotation / essay)

If any are missing, ask the user before proceeding.

---

## Step 1 — Read mandatory rules

Read and internalize `E:\Corvus_Careebridge\sops\sop_fallback_rules.md` before taking any action.

## Step 2 — Connect browser profile

Follow `E:\Corvus_Careebridge\sops\sop_browser_launch.md` exactly.

Call `mcp__ixbrowser__connect_profile(profile_id, email)`.

On success: note the `cdp_url` and `tab_list`.

## Step 3 — Navigate to assessment

Check `tab_list` for a tab matching `job_url` (exact or domain match).
- If found: focus that tab via `mcp__cdp__cdp_eval('window.focus()')` or browser_mcp shortcut.
- If not found: navigate to `job_url`.

Verify the page loaded by calling `mcp__dom__get_accessibility_tree()`.
If tree is empty after 5s: wait and retry once per `sop_fallback_rules.md`.

## Step 4 — Detect assessment type

From the DOM, determine what type of content the page shows:
- **Video player + annotation controls** → `video_annotation`
- **Image + annotation controls / label boxes** → `image_annotation`
- **Form with radio/checkbox/text inputs** → `text_assessment`
- **Essay textarea only** → `essay`
- **Application form with file upload** → `application`
- **Unknown** → call `mcp__capture__screenshot(purpose="debug")` and describe what you see

## Step 5 — Route to the appropriate skill

Based on detected type:

| Detected type | Next skill |
|---|---|
| `video_annotation` | Follow `sops/sop_video_annotation.md` |
| `image_annotation` | Follow `sops/sop_image_annotation.md` |
| `text_assessment` or `essay` | Follow `sops/sop_text_assessment.md` |
| `application` | Follow `sops/sop_cv_application.md` (Steps 6 onward) |

Pass the `cdp_url`, `profile`, and `job_url` into the next step.

---

## Rules

- Never skip this skill and go directly to an assessment skill.
- Never take any assessment action before completing Steps 1-3.
- If browser connection fails twice: stop and notify user via Telegram.
