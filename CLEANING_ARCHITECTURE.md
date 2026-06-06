# CareerBridge Cleaning Architecture

**The System Works Both Ways: New Data AND Retroactively**

---

## Architecture Overview

```
RAW INPUT (dirty, unfiltered)
    |
    | Claude Code cleaning logic (gate rules, validate, enrich)
    |
DATABASE OUTPUT (clean, verified)
```

This applies to **everything** — both new discoveries and existing data.

---

## Three Cleaning Pipelines

### 1. JOBS CLEANING (New Discoveries)

**Raw:** `raw_discoveries` table (897 unprocessed items)  
**Logic:** `sop_data_cleaning.md` + `skill_clean_scraped_data.md`  
**Output:** `jobs` table

**What gets BLOCKED:**
- Aggregators (Indeed, ZipRecruiter, simplyhired searches)
- Blog posts, social media, Reddit/HN posts
- School enrollment pages (us_schools source)
- **Professional jobs requiring degrees/licenses** (engineering, law, accounting)
- Empty/duplicate content

**What gets KEPT:**
- Gig work (freelance, tasks, bounties)
- Remote work (ft/pt/contract)
- Content work (writing, tutoring, translation)
- Task platforms (Appen, Toloka, RWS)
- Anything accessible without professional licensing

**Classification:** Each kept job gets a `job_type` from taxonomy:
```
ai_training, data_annotation, search_rating, transcription, translation,
content_writing, tutoring, testing, moderation, gpt, other_gig, ...
```

**Enrichment:** Firecrawl fetches full page when raw_content is thin

---

### 2. JOBS RE-CLEANING (Existing 3,146 Jobs) — RETROACTIVE

**Current:** `jobs` table (3,146 existing jobs)  
**Logic:** Same as above — `sop_data_cleaning.md` gate rules  
**Action:** RE-VALIDATE existing jobs

**What you said:** Even the existing jobs must be revisited to remove professional jobs like engineering and search.

**This means:**
- Scan all 3,146 existing jobs
- Apply gate rules retroactively
- **REMOVE** or **MARK** jobs that are professional (engineering, search, etc.)
- Clean up titles/companies/descriptions using Firecrawl

**Implementation:**
```
for job in jobs table:
  if is_professional_job(job.title, job.description):
    mark_for_removal or set status='filtered'
  
  if description IS NULL or company IS NULL:
    firecrawl_scrape(job.url) -> enrich
```

---

### 3. SCHOOLS CLEANING

**Raw:** `raw_schools` table (on VPS, separate)  
**Logic:** `skill_analyze_schools.md` (6 criteria scoring)  
**Output:** `schools` table

**What makes a "clean" school:**
Applies 6 criteria against official website text:
1. Community college (explicitly stated)
2. No ID verification required
3. No transcript required
4. Monthly enrollment (rolling start dates)
5. Instant acceptance
6. Monthly refund option

**Score:** 0-6 criteria met → criteria_score field

**Enrichment:** Firecrawl fetches fresh content if raw_content is empty  

**Graph Expansion:** Mine each school page for mentions of partner/affiliated schools → add back to raw_schools queue

---

## The System Design

```
DISCOVERY SIDE                          SCHOOLS SIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Crawlee scrapes                         Scorecard API + Serper scrape
    ↓                                             ↓
raw_discoveries (dirty)                 raw_schools (dirty)
    |                                             |
    | Claude Code gates                  | Claude Code scores
    | Firecrawl enriches                 | Firecrawl enriches
    |                                             |
    ↓                                             ↓
jobs table (clean)                      schools table (clean)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KEY: This logic applies RETROACTIVELY
     Existing data can be re-cleaned
     Same rules, same enrichment, same validation
```

---

## Current State vs. After Cleaning

### BEFORE (Current Messy State)
```
raw_discoveries: 897 unprocessed (mixed junk + valid gigs)
jobs: 3,146 jobs (includes professional jobs, aggregators, blogs)
raw_schools: ? unprocessed (raw scores, unvalidated)
profiles: 0 (deleted ✓)
```

### AFTER (Fully Cleaned)
```
raw_discoveries: 897 processed (all gated, marked kept/blocked)
jobs: ~1,500-2,000 (valid gigs only, professional jobs removed)
schools: ~200-300 (scored 0-6 criteria, verified official sources)
raw_schools: all processed
profiles: 0 (stays deleted)
```

---

## Cleanup Tasks (3 Steps)

### Step 1: Gate New Discoveries (897 items)
**Status:** Not yet in jobs table  
**Action:** Apply `sop_data_cleaning.md` gate rules  
**Tool:** `skill_clean_scraped_data.md` (triggered every 15 min on VPS)  
**Time:** ~1.5-2 hours (36 batches × 50 items)  
**Result:** 897 discoveries → ~X valid jobs + Y blocked

### Step 2: RE-Gate Existing Jobs (3,146 items) — RETROACTIVE
**Status:** Already in jobs table  
**Action:** Apply same gate rules retroactively  
**Find & Remove:**
- Professional jobs (engineering, search, accounting, law, medical)
- Aggregator duplicates
- Blog post listings
- School enrollment pages
**Enrich:**
- Get full descriptions from Firecrawl for incomplete jobs
- Clean up titles/companies
**Result:** 3,146 jobs → ~1,500-2,000 valid gigs

### Step 3: Clean Raw Schools
**Status:** raw_schools on VPS (unprocessed)  
**Action:** Apply `skill_analyze_schools.md` (6 criteria)  
**Tool:** Firecrawl for fresh content when needed  
**Result:** raw_schools → schools table with criteria_score (0-6)

---

## The Key Architecture Insight

> **The same cleaning rules that work on RAW data work retroactively on EXISTING data**

Examples:
- Raw discovery marked "blog_post"? → Existing job with `/blog/` in URL also gets marked
- Raw discovery marked "professional_job"? → Existing job with "Senior Engineer" in title also gets marked
- Raw discovery needs Firecrawl enrichment? → Existing job with NULL description also needs it

This is why Claude Code can "clean the database": it's not deleting data, it's applying the same validated gate rules to everything.

---

## Files Defining This System

- **`sop_data_cleaning.md`** — Gate rules (block/keep criteria, classification)
- **`skill_clean_scraped_data.md`** — Automation for new discoveries + opportunistic re-enrichment of existing jobs
- **`skill_analyze_schools.md`** — School scoring and criteria validation
- **`context_discovery.md`** — Scout persona (gate expansion rules)
- **`context_schools.md`** — School analysis rules

---

## Ready for Cleanup?

The system is designed and documented. The architecture supports:

1. ✓ Gating new 897 discoveries
2. ✓ Re-gating 3,146 existing jobs (remove professional, validate)
3. ✓ Cleaning raw_schools (score against official sources)

Ready to execute the full cleanup?
