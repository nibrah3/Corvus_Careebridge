# Skill: Apply Flow

**Trigger:** User picks "Apply" from the main menu.
**Mode:** Interactive start, then autonomous with confirmation pauses.

---

## Step 1 — What are you applying to?

Call AskUserQuestion:
```
question: "What do you want to apply to?"
header:   "Apply"
options:
  - label: "Jobs"     description: "N jobs ready in your queue"
  - label: "Schools"  description: "N schools found — online enrollment"
```

Get the count for each from VPS:
- Jobs: `mcp__vps__count_jobs(status="pending")`
- Schools: `mcp__vps__count_schools(criteria_score_min=1, online_available=true)`

---

## Step 2 — Show the queue (jobs or schools)

### Jobs
Call `mcp__vps__get_pending_jobs(limit=10)`.

Present as AskUserQuestion cards — ONE at a time:
```
question: "[Job Title] at [Company]"
header:   "[job_type] · [sector]"
options:
  - label: "Apply"      description: "Start application with browser automation"
  - label: "Skip"       description: "Remove from queue"
  - label: "More Info"  description: "See full description and URL"
  - label: "Back"
```

Show quality issues inline if present. Use official_url as primary link.

### Schools
Call `mcp__schools__list_confirmed_schools(min_score=1, online_available=true, limit=10)`.

Present one at a time:
```
question: "[School Name]"
header:   "[State] · Score [N]/6"
options:
  - label: "Enroll"     description: "Start enrollment with browser automation"
  - label: "Skip"
  - label: "More Info"  description: "See criteria met and enrollment URL"
  - label: "Back"
```

---

## Step 3 — Browser and profile selection

When user picks Apply or Enroll:
Follow `prompts/skill_browser_select.md` exactly.
Get back: `cdp_url`, `browser_name`, `profile_name`, `profile_id`.

---

## Step 4 — Run the pipeline (autonomous)

Pass to the appropriate skill:
- **Job / text form** → `sops/sop_text_assessment.md`
- **Job / image annotation** → `sops/sop_image_annotation.md`
- **Job / video annotation** → `sops/sop_video_annotation.md`
- **School enrollment** → same as text form — `sops/sop_text_assessment.md`

The pipeline runs autonomously and pauses only when:

**Confirmation pause 1 — Personal data before filling:**
Before typing any personal data (name, email, phone, address), confirm with user:
```
question: "Confirm your details for this application:"
header:   "Your Info"
options:
  - label: "Correct, continue"
  - label: "Edit before filling"
```
Show: name, email, phone as found in the profile. Let user edit if needed.

**Confirmation pause 2 — Essay / free-text answer:**
`hook_gate_monitor` handles this automatically (gate_type: essay_gate).
Draft → Approve / Change it / Leave blank.

**Confirmation pause 3 — Final submit review:**
`hook_gate_monitor` handles this automatically (gate_type: submit_review).
Full answer summary → Submit / Wait, go back.

---

## After completion

- `mcp__vps__update_job_status(job_id, status="applied")`  (or school equivalent)
- `mcp__telegram__notify("Applied: [Title] / [Profile Name]")`
- Show next item in queue or return to Apply menu.

## On Error

See `sops/sop_on_error_shared.md` for browser/CDP/tunnel failures.
