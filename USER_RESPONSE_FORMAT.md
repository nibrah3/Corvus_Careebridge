# What Users See: Jobs & Schools Response Format

**Date:** 2026-06-04  
**Context:** Current user interface for job/school listings

---

## Current User Flow

```
User: "Show me available jobs"
          ↓
Claude Code checks jobs table
          ↓
hook_present_jobs.py formats response
          ↓
User sees: Jobs as interactive cards (one at a time)
```

---

## JOBS Response Format

### What User Sees

```
[JOBS READY — showing 8 of 1573]

Present these jobs as interactive cards ONE AT A TIME:

1. job_id=58445 | "AI Training Data Annotator" | company="Scale AI"
   | sector="AI/ML" | 92% match
   official_url='https://scale.com/careers/ai-training-data-annotator'
   [!] Official employer URL not confirmed — verify before applying
   preview='Create training data for AI models. Remote, flexible hours. $15-25/hour.'

2. job_id=58446 | "Search Quality Rater" | company="Google"
   | sector="Search" | 87% match
   official_url='https://careers.google.com/jobs/results/search-quality-rater'
   source_url='https://www.indeed.com/viewjob?jk=xxx'
   preview='Evaluate search result quality. Work from home. 20-40 hours/week. Contract.'

3. job_id=58447 | "Portuguese Translator" | company="RWS"
   | sector="Translation"
   official_url='https://rws.com/careers/translator-portuguese'
   [enriching...] Official URL pending — check back shortly
   preview='Translate English content to Brazilian Portuguese. Freelance. Per-word rate.'

[Each job presented as ONE card at a time]
[Card buttons: Apply | Skip | More Info | Back to Jobs]
```

### Actual Card Appearance

```
┌─────────────────────────────────────────┐
│ AI Training Data Annotator at Scale AI  │
├─────────────────────────────────────────┤
│ AI/ML · 92% match                       │
├─────────────────────────────────────────┤
│ Job #1 of 8 in queue                    │
│                                          │
│ Official: scale.com/careers/...         │
│ Preview: "Create training data for AI   │
│ models. Remote, flexible hours.         │
│ $15-25/hour."                           │
│                                          │
│ [Apply] [Skip] [More Info] [Back]       │
└─────────────────────────────────────────┘
```

### URL Structure

```
official_url = The canonical employer/platform URL (primary)
  Example: https://scale.com/careers/ai-annotator

source_url = Where the job was originally discovered (fallback)
  Example: https://www.indeed.com/viewjob?jk=abc123

[Claude uses official_url first, source_url as backup]
```

### Job Data Included

| Field | Example | Purpose |
|-------|---------|---------|
| job_id | 58445 | Database reference |
| title | "AI Training Data Annotator" | Role title |
| company | "Scale AI" | Employer/platform |
| sector | "AI/ML" | Category |
| score | 92% | Match relevance (if user profile matched) |
| official_url | scale.com/careers/... | Primary application link |
| source_url | indeed.com/viewjob?... | Where discovered |
| preview | "Create training data..." | 150-char job description |
| quality_note | "[!] Not confirmed..." | If enrichment incomplete |

### Quality Indicators

```
[!] Official employer URL not confirmed
    → Means: Scraped from source, official site not verified yet
    → User should verify before applying

[!] Could not retrieve full job details
    → Means: Firecrawl scrape failed, incomplete data
    → User sees source_url only

[enriching...] Official URL pending
    → Means: Still fetching official site, not ready yet
    → User can apply now or wait
```

### User Actions on Each Card

```
[Apply]      → Use official_url for application, load assessment flow
[Skip]       → Mark job_id as skipped in DB, show next job
[More Info]  → Show official_url + full preview, re-show card
[Back]       → Return to Jobs & Assessments menu
```

---

## SCHOOLS Response Format

### What User Sees

```
[SCHOOLS READY - 10 of 74 confirmed]

Present these schools ONE AT A TIME as cards:

1. "Community College of Denver" (Community College) — Denver, CO
   Score: 4/6  |  Community College | No ID required | No transcripts needed | Monthly start dates
   url='https://www.ccd.edu'
   enrollment_url='https://www.ccd.edu/enrollment/apply'

2. "Riverside City College" (Community College) — Riverside, CA
   Score: 3/6  |  Community College | No transcripts needed | Monthly start dates
   url='https://www.rcc.edu'

3. "California Virtual Campus" (Online) — Statewide, CA
   Score: 4/6  |  Community College | No transcripts needed | Monthly start dates | Instant acceptance
   url='https://cvc.edu'

[Each school presented as ONE card at a time]
[Card buttons: Send to my Phone | Next School | Back to Schools Menu]
```

### Actual Card Appearance

```
┌──────────────────────────────────────┐
│ Community College of Denver          │
│ Denver, CO                            │
├──────────────────────────────────────┤
│ Community College - Score 4/6         │
├──────────────────────────────────────┤
│ School #1 of 10                       │
│                                        │
│ Accessible Features:                 │
│ ✓ Community College                  │
│ ✓ No ID required                     │
│ ✓ No transcripts needed              │
│ ✓ Monthly start dates                │
│                                        │
│ Official: ccd.edu                     │
│ Enroll: ccd.edu/enrollment/apply      │
│                                        │
│ [Send to Phone] [Next] [Back]        │
└──────────────────────────────────────┘
```

### School Data Included

| Field | Example | Purpose |
|-------|---------|---------|
| name | "Community College of Denver" | School name |
| type | "Community College" | Category (CC, university, online, etc.) |
| city | "Denver" | Location |
| state | "CO" | State |
| score | 4 | Accessibility score (0-6) |
| filters | ["community_college", "no_id_verification", ...] | Which criteria met |
| url | ccd.edu | Official website |
| enrollment_url | ccd.edu/enrollment/apply | Enrollment page (if different) |

### Criteria Translation for Users

```
Backend field → User sees:
community_college       → "Community College"
no_id_verification      → "No ID required"
no_transcript_required  → "No transcripts needed"
monthly_enrollment      → "Monthly start dates"
instant_acceptance      → "Instant acceptance"
monthly_refund          → "Monthly refund policy"
```

### User Actions on Each Card

```
[Send to my Phone] → Send school info + criteria filters via Telegram
[Next School]      → Show next school card
[Back to Schools]  → Return to Schools menu
```

### Score Display

```
Score: 0/6 - Traditional school (no special accessibility features)
Score: 1/6 - Limited accessibility (1 feature)
Score: 2/6 - Moderate accessibility (2 features)
Score: 3/6 - Good accessibility (3+ features)
Score: 4/6 - Excellent accessibility (4+ features)
```

---

## Comparison: Current vs. What's Missing

### Currently Shown

```
Jobs:    1,073 jobs (shown to user)
         ├─ 58K pending
         ├─ 756K blocked (internal filtering)
         └─ All with official_url or source_url

Schools: 74 schools (score >= 3, quality filtered)
         └─ All with 3-6 accessibility criteria
```

### Not Currently Shown

```
Jobs:    Not shown (filtered out earlier)
         ├─ 831 aggregator jobs (Indeed, ZipRecruiter, etc.)
         ├─ 760 professional jobs (engineers, doctors, lawyers)
         ├─ 466 school enrollment pages
         └─ Various blogs, Reddit posts, etc.

Schools: Not shown (score < 3, low accessibility)
         ├─ 238 traditional schools (score 0/6)
         ├─ 38 schools with 1 criterion
         ├─ 129 schools with 2 criteria
         └─ Could include 6,500+ additional schools via Scorecard API
```

---

## Response Cycle

### Current Flow

```
User: "Show me jobs"
      ↓
get_pending_approvals() query
      ↓
Returns: list of ~1,073 jobs (pending status)
      ↓
hook_present_jobs.py formats
      ↓
Shows: First 8 jobs as interactive cards
      ↓
User: Apply/Skip/More Info
      ↓
Repeat for each job in queue
```

### Empty State

```
If no jobs/schools:
[JOBS RESULT - EMPTY]
"Nothing new at the moment - I'll keep an eye out for you."
[Show Jobs & Assessments menu]
```

---

## URL Hierarchy

### For Jobs

```
Primary URL:    official_url (employer/platform official site)
Fallback URL:   source_url (where discovered, if official unavailable)

Example:
  official_url: https://scale.com/careers/ai-annotator
  source_url:   https://www.indeed.com/viewjob?jk=abc123

[Claude always uses official first, tells user if only source available]
```

### For Schools

```
Primary URL:    url (school official website)
Secondary URL:  enrollment_url (specific enrollment/application page)

Example:
  url:           https://www.ccd.edu
  enrollment_url: https://www.ccd.edu/enrollment/apply

[If enrollment_url exists and differs from url, both are shown]
```

---

## Data Quality Notes

### Jobs Quality Indicators

```
[!] Official employer URL not confirmed
    → Scraped job, official site not yet verified
    → Still valid, but verify before applying

[!] Could not retrieve full job details
    → Firecrawl scrape failed
    → Show source_url as fallback

[enriching...] Official URL pending
    → System still fetching official site
    → User can apply now or wait ~30 sec
```

### Schools Quality

```
Score 0/6 = Traditional school (meets no special accessibility criteria)
Score 1/6 = Limited (1 barrier removed)
Score 3/6 = Good (meets 3+ barriers removed)
Score 4/6 = Excellent (4+ barriers removed)
```

---

## Pagination

### Jobs
```
Show: 8 jobs at a time (MAX_JOBS = 8)
Queue: Up to 1,073 jobs available
Navigation: [Next] advances one job, [Skip] marks as skipped
Total display: "showing 8 of 1,073"
```

### Schools
```
Show: 10 schools at a time (MAX_SCHOOLS = 10)
Queue: Up to 74 schools available (score >= 3)
Navigation: [Next School] advances to next
Total display: "showing 10 of 74 confirmed"
```

---

## Summary: What Users Get

### Jobs Response
```
Format: Interactive cards, one at a time
Fields: Title, Company, Sector, Score, Official URL, Source URL, Preview
Quality: Filtered (valid gigs only, no aggregators/blogs/professional jobs)
Total available: 1,073 jobs
Shown at once: 8 cards
```

### Schools Response
```
Format: Interactive cards, one at a time
Fields: Name, Type, Location, Score, Accessibility Criteria, URLs
Quality: Filtered (score >= 3 accessibility criteria)
Total available: 74 schools
Shown at once: 10 cards
```

---

*Current system delivers clean, filtered data with quality indicators and primary URLs.*
*Future: Can expand schools from 74 to 7,000+ via Scorecard API while maintaining same format.*
