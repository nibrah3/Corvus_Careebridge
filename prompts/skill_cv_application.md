# Skill: CV Generation and Custom Application

**Trigger:** User provides a job URL and profile ID and requests a tailored CV + application.
**Mode:** Interactive — user confirms before application is submitted.

---

## What you are doing

Generating an ATS-optimised CV tailored to a specific job posting, then completing the application. You are the reasoning engine — Firecrawl fetches the content, you analyze and write.

Follow `E:\Corvus_Careebridge\sops\sop_cv_application.md` for the full procedure.

---

## Required inputs

Ask the user for (if not provided):
- `job_url` — the job posting URL
- `profile_id` — which candidate profile to use
- `cover_letter` — yes/no (default: no)

---

## Step 1 — Fetch job content

Call `mcp__vps__firecrawl_scrape(job_url)`.

Read the returned markdown in full. Extract:
- Job title, company name
- Required skills (exact phrases)
- Preferred skills
- Tech stack
- Years of experience required
- Education / certifications
- Exact phrases to mirror in the CV

## Step 2 — Fetch profile

Call `mcp__vps__get_profile(profile_id)`.

Read: name, email, phone, location, bio, skills (JSON array), experience (JSON array), education.

## Step 3 — Write CV sections in context

You are writing the CV content now. No external LLM call — you are the LLM.

**Summary (3-4 sentences):**
Open with the job title. Reference the top 2-3 required skills using the JD's exact phrasing. Draw from the candidate's bio. Close with a forward-looking sentence mentioning the company.

**Skills section (ordered list):**
- First: required tech terms from JD that match the profile (exact JD phrasing)
- Then: remaining profile skills
- Then: preferred/nice-to-have matches

**Experience bullets (for each role):**
Rewrite 2-3 bullets per role to incorporate the JD's exact verb/noun language where factually true.
Format: `[Action verb] [result/scope] using [JD-mirrored technology/skill]`
Do not invent experience.

**Relevant Qualifications section:**
Echo the top 5 required bullets back in first-person present tense:
`• Proficient in [required skill] with [N] years of hands-on experience`

**Gaps:**
List required skills not found in the candidate's profile. These are reported to the user.

## Step 4 — Generate PDF

Run via Bash:
```
python E:\Corvus_Careebridge\scripts\format_cv.py --profile-id {profile_id} --sections-json "{escaped_json}"
```

This returns paths to the .txt and .pdf files.

If cover letter requested: also generate it in context and save via the same script with `--cover-letter`.

## Step 5 — Report and confirm

Tell the user:
- Your ATS match score (0-100, based on required skills matched)
- Top matched skills (first 8)
- Gaps found
- CV file path

Ask: "Shall I proceed with the application? (yes/no)"

Stop and wait for confirmation.

## Step 6 — Apply (if confirmed)

Run `skill_prepare_assessment.md` with the job_url and profile_id.
Upload the PDF when a file upload field is detected.
Complete the form following `sop_text_assessment.md`.

---

## Rules

- Never fabricate skills or experience not in the profile.
- Always confirm with user before submitting.
- The CV content you write here IS the CV — format_cv.py only converts it to PDF.

## On Error

For Firecrawl/VPS/IXBrowser failures: see sops/sop_on_error_shared.md.

**CV skill specific:**
- Firecrawl returns empty → ask user to paste job description text directly
- format_cv.py fails → output plain text CV instead; note PDF generation failed
- User does not confirm within session → stop; job stays in pending state
