# Skill: Queue

**Trigger:** User picks "Queue" from the main menu.
**Mode:** Read-only view. No browser automation here.

---

## Step 1 — What do you want to see?

Call AskUserQuestion:
```
question: "What do you want to look at?"
header:   "Queue"
options:
  - label: "Ready Jobs"      description: "Jobs cleaned and ready to apply — N waiting"
  - label: "Applied Jobs"    description: "History of what you've applied to"
  - label: "Blocked Jobs"    description: "What Claude filtered out and why"
  - label: "Schools Found"   description: "Schools from Scorecard discovery"
  - label: "Back"
```

---

## Ready Jobs
`mcp__vps__get_pending_jobs(limit=20)` — show as a plain list.
Format: `[Title] at [Company] · [job_type]`
No Apply button here — user goes to Apply menu to act on them.

## Applied Jobs
`mcp__vps__get_jobs(status="applied", limit=20)` — show as list with date.
Format: `[Title] at [Company] · Applied [date]`

## Blocked Jobs
`mcp__vps__get_jobs(status="blocked", limit=20)` — show with block reason.
Format: `[URL] · Blocked: [reason]`
Tell user: "These were filtered because they matched block rules (professional jobs, blog posts, school enrollment pages, aggregators). No action needed."

## Schools Found
`mcp__schools__list_confirmed_schools(limit=20)` — show with score.
Format: `[School Name] · [State] · Score [N]/6`
If no schools yet: "No schools discovered yet. Schools discovery runs once — ask me to run it when you're ready."

---

## Rules

- Never start browser automation from the Queue view.
- Never modify job status from the Queue view (read-only).
- After viewing, offer: "Back to Queue" or "Go to Apply".
