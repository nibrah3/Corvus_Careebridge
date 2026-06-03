# SOP: Scraped Data Cleaning

**Purpose:** Normalize raw Crawlee/Firecrawl discoveries into clean, classified job records before they enter the jobs table.

---

## What "clean" means

A clean job record has:
- `url`: canonical employer URL (not aggregator, not redirect, not tracking-param-laden)
- `title`: job/gig title (plain text, no HTML, no site suffixes)
- `company`: employer or platform name
- `description`: 1-3 sentence summary of what the task involves and what it pays
- `job_type`: one value from the taxonomy (see Step 3)
- `source_url`: the discovery URL where the item was found

---

## Steps

### 1. Fetch raw discoveries

Call `mcp__vps__get_raw_discoveries(limit=50)`.
If count = 0: nothing to do, exit silently.

### 2. For each discovery: decide keep or block

**Block immediately if:**
- URL is an aggregator search results page (indeed.com/q-, ziprecruiter.com/Jobs/, simplyhired.com/search, etc.)
- URL is a Reddit/HN/Facebook/Instagram/YouTube/Twitter post
- URL is a blog article (contains `/blog/`, `/news/`, `/article/`, `/post/`)
- URL leads to a professional full-time job requiring a degree or license
- URL leads to school/university enrollment
- `raw_content` is empty or just navigation text (< 100 chars of actual content)

→ Call `mcp__vps__mark_raw_blocked(id, reason)` where reason is one of:
  `aggregator_search | blog_post | social_media | school_enrollment | professional_job | empty_content | duplicate`

**Keep if:** the URL leads to a gig, task, or remote work opportunity that doesn't require professional licensing. When in doubt: keep with job_type='other_gig'.

### 3. For kept items: classify job_type

Pick exactly one from this taxonomy:
- `ai_training` — creating training data, RLHF, preference labeling for AI systems
- `data_annotation` — labeling images, video, audio, text for ML datasets
- `search_rating` — search quality evaluation, web relevance rating, UQRS
- `transcription` — audio/video to text, captioning
- `translation` — text or audio translation between languages
- `content_writing` — articles, product descriptions, social posts (human-written)
- `social_media` — managing accounts, engagement tasks
- `virtual_assistant` — admin, scheduling, research tasks (remote)
- `customer_support` — chat/email support, ticket handling (gig/task-based)
- `microtask` — Mechanical Turk style: classify, verify, short tasks
- `tutoring` — teaching/coaching (per-session, not faculty employment)
- `testing` — app/website QA testing, bug reporting, usability studies
- `moderation` — content review, trust and safety, policy enforcement
- `gpt` — Get Paid To: surveys, offers, cashback, rewards programs
- `other_gig` — gig-work that doesn't fit above categories

### 4. Extract canonical URL

From `raw_content`, identify the actual employer/platform signup or task URL.
- Strip tracking parameters (`utm_*`, `ref=`, `source=`, etc.)
- Resolve redirects mentally (e.g., `bit.ly/xyz` in content suggests the next URL is the real one — use Firecrawl if needed)
- The `url` field should be the page a user would bookmark to apply/sign up

If no cleaner URL can be determined: use the discovery URL as-is.

### 5. Write a requirements summary

Write 1-3 sentences describing:
- What the task involves
- Pay structure if mentioned (per hour, per task, per word)
- Any notable requirements (location, language, device)

### 6. Upsert to jobs table

Call `mcp__vps__upsert_job(url, title, company, description, job_type, source_url, source)`.
Then call `mcp__vps__mark_raw_processed(id)`.

### 7. Summary

After processing all items:
Call `mcp__telegram__notify("Clean: kept N, blocked N | {job_type breakdown}")`.

---

## Notes

- Run in a single pass — do not pause between items.
- Use Firecrawl (`mcp__vps__firecrawl_scrape`) only when the raw_content is insufficient to classify. It costs a scrape call.
- False negatives (blocking a real gig) are worse than false positives. When uncertain: keep.

## On Error

For tunnel/MCP failures: see sops/sop_on_error_shared.md.

**Data cleaning specific:**
- Firecrawl returns empty for an ambiguous URL → classify from URL structure alone; mark lower confidence
- search_terms table not found → run upsert_search_term once to trigger table creation
- Batch size too large (timeout) → reduce to 25 items; retry
