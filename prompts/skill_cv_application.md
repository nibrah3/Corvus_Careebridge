# Skill: CV and Personal Details Handling

**Trigger:** Called by skill_apply.md when a job or school form needs a CV upload
or personal details (name, phone, address, work history).
**Mode:** Interactive check first, then autonomous.

---

## Step 1 — Check what the profile already has

Call `mcp__vps__get_profile(profile_id)`.

Check which fields are present:
- name, email, phone, address → mark as complete or missing
- cv_file_path → does a saved CV file exist on disk?
- work_history, skills, bio → needed for CV generation

---

## Step 2 — Personal details confirmation

Before filling ANY personal field on a form, show the user what will be used:

Call AskUserQuestion:
```
question: "Here's what I'll use for your personal details — is this correct?"
header:   "Your Info"
options:
  - label: "Looks good, continue"
  - label: "Let me update something"
```

Show: name, email, phone, location (from profile).
If user picks "Let me update something" — ask which field and get the new value before continuing.
Missing fields: ask only for those, one at a time.

---

## Step 3 — CV handling (only if page has a file upload field)

If no CV upload field detected on the page: skip this step entirely.

If CV upload is needed, ask:

Call AskUserQuestion:
```
question: "This application needs a CV. Which would you like to use?"
header:   "CV"
options:
  - label: "My saved CV"         description: "Use the CV file already on your profile"
  - label: "Generate a new one"  description: "Claude writes a tailored CV for this job"
  - label: "I'll provide my own" description: "Give me a file path or paste your CV"
```

### Option A — My saved CV
Read `cv_file_path` from profile.
If file exists on disk: upload it directly. Done.
If file not found: fall through to Option C.

### Option B — Generate a tailored CV
1. Call `mcp__vps__firecrawl_scrape(job_url)` — get job description
2. Extract: title, company, required skills, preferred skills, tech stack
3. Read profile: name, bio, skills array, work_history array, education
4. Write CV sections in context (you are the LLM — no external call):
   - Summary: 3-4 sentences referencing top required skills using JD phrasing
   - Skills: required matches first (exact JD phrasing), then remaining profile skills
   - Experience: rewrite 2-3 bullets per role using JD verb/noun language where factually true
   - Qualifications: echo top 5 required skills in first-person present tense
5. List any gaps (required skills not in profile) — report these to user
6. Run via Bash: `python E:\Corvus_Careebridge\scripts\format_cv.py --profile-id {id} --sections-json "{json}"`
7. Show user: ATS match score, top matched skills, gaps, file path
8. Ask: "Use this CV for the application?" → Yes / Regenerate / Cancel

### Option C — I'll provide my own
Ask:
```
question: "How do you want to provide your CV?"
header:   "Your CV"
options:
  - label: "File path"   description: "Give me the path to your CV file"
  - label: "Paste text"  description: "Paste your CV content and I'll save it as PDF"
```
File path: read and verify it exists → use for upload.
Paste text: save to `C:\Users\<user>\Desktop\MyCV.pdf` via format_cv.py → upload.

---

## Rules

- Never fabricate skills or experience not in the profile.
- Always show personal details to the user before filling — no silent pre-fill.
- CV generation only happens when explicitly chosen — never auto-generate without asking.
- Saved CV takes priority if it exists and user picks it — no regeneration needed.

## On Error

- Firecrawl empty → ask user to paste the job description text directly
- format_cv.py fails → output plain text CV; note PDF generation failed
- File not found → offer to generate or let user provide path again
