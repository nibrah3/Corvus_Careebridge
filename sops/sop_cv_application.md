# SOP: Custom Application — CV Generation and Form Completion

**Purpose:** Handle the full flow when a user provides a custom job URL and wants to apply with a specific profile: scrape requirements, generate a tailored CV, then complete the application.

---

## Steps

### 1. Fetch job requirements

Call `mcp__vps__firecrawl_scrape(url)` with the job posting URL.

You will receive markdown of the job page. Read it carefully — extract:
- Job title
- Company name
- Required skills (exact phrases used in the posting)
- Preferred/nice-to-have skills
- Tech stack mentioned
- Years of experience required
- Education or certifications required
- Any exact phrases that should appear in the CV ("experience with X", "proficiency in Y")

If Firecrawl returns empty (JS-heavy page, auth wall):
- Fall back to `mcp__cdp__cdp_eval('document.body.innerText')` if browser session is active.
- If still empty: request user provide the job description text manually.

### 2. Fetch candidate profile

Call `mcp__vps__get_profile(profile_id)`.

Load: name, email, phone, location, bio, skills, experience, education.
Also note the profile's persona (writing voice) — used for humanization.

### 3. Assess fit and write CV sections

Read the job requirements and profile together. In your context:

**CV Summary** — mirror the job title and top 3 required skills. Draw from the candidate's bio. Use the JD's exact phrasing where truthful.

**Skills section** — ordered by JD priority: required tech first, then profile skills. Use the exact terms from the JD (e.g., if JD says "Apache Kafka" don't write "Kafka").

**Experience bullets** — for each role in the candidate's experience, rewrite 2-3 bullets that incorporate the JD's exact language. Keep facts accurate; only adjust phrasing to mirror the JD. Do not invent experience.

**Relevant Qualifications section** — echo the top 5 required bullets back in first-person: "I have X years of experience with Y."

**Gaps** — list any required skills not in the candidate's profile. These will be reported to the user.

### 4. Generate PDF

Call `Bash` to run:
```
python E:\Corvus_Careebridge\scripts\format_cv.py --profile-id {id} --job-id {id} --sections-json '{json}'
```

This produces `cvs/cv_{profile}_{job}.txt` and `cvs/cv_{profile}_{job}.pdf`.

### 5. Report to user

Before proceeding to the application, tell the user:
- ATS match score (your assessment, 0-100)
- Top matched skills
- Gaps found (required but not in profile)
- Path to CV files

Ask: "Should I proceed with the application?"

Do not proceed until user confirms.

### 6. Launch browser and apply

Follow `sop_browser_launch.md` to connect the profile.
Navigate to the application URL.
If a CV/resume upload field is found: upload the PDF via `mcp__humanizer__*`.
Complete the remaining form fields following `sop_text_assessment.md`.

---

## Rules

- Never invent skills or experience not in the candidate's profile.
- Never generate a CV for a job the candidate is clearly unqualified for without flagging the gaps.
- The CV summary and skills must use the JD's exact phrasing — ATS keyword matching depends on this.
- Always confirm with the user before submitting the application.

## On Error

For tunnel/MCP/CDP/IXBrowser failures: see sops/sop_on_error_shared.md.

**CV application specific:**
- Firecrawl returns empty for job URL → try cdp_eval(document.body.innerText) if browser open; else ask user to paste JD
- cv_format_tool fails → save CV as plain text; note PDF generation error in report
- File upload field not found → ask user to manually upload PDF; continue with form
- Application already submitted (duplicate detected) → report to user; mark job applied
