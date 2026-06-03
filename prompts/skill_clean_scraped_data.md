# Skill: Clean Scraped Data

**Trigger:** `CareerBridge_Gate` Task Scheduler task (every 15 min) OR Redis push from `raw_listener.py`.
**Mode:** Fully autonomous — no user present. Run to completion.

---

## What you are doing

VPS Crawlee has deposited raw URLs into `raw_discoveries`. These are unfiltered and include aggregator pages, blog posts, social media, and real gig listings all mixed together.

Your job: read each one, decide keep or block, enrich with Firecrawl if needed, classify the job type, and write clean records to the jobs table.

Follow `E:\Corvus_Careebridge\sops\sop_data_cleaning.md` for the full decision criteria.

---

## Step 1 — Check connection

Call `mcp__vps__get_system_status`.
If redis or postgres is error: `mcp__telegram__notify("Gate skipped: VPS connection down")` and stop.

## Step 2 — Fetch raw batch

Call `mcp__vps__get_raw_discoveries(limit=50)`.
If count = 0: exit silently.

## Step 3 — Process each discovery

For each item, follow `sop_data_cleaning.md` Steps 2-6.

Use Firecrawl (`mcp__vps__firecrawl_scrape`) only when `raw_content` is too thin to classify confidently (< 200 chars of real content, or ambiguous title). Each Firecrawl call costs — use judiciously.

## Step 4 — Enrich unenriched jobs (opportunistic)

After processing raw_discoveries, call `mcp__vps__get_unenriched_jobs(limit=20)`.
For each: call `mcp__vps__firecrawl_scrape(url)`, extract official employer URL, clean description, call `mcp__vps__update_job_enrichment(job_id, official_url, official_description, job_type)`.

Stop enrichment early if more than 5 Firecrawl calls have been made in this run (stay within rate limits).

## Step 5 — Summary

Call `mcp__telegram__notify("Gate: kept N, blocked N, enriched N | pending: X")`.

---

## Rules

- MCP tools only — no Python scripts, no shell commands.
- Never approve or submit jobs. Gate and classify only.
- False negatives (blocking real gigs) are worse than false positives. Doubt = keep.
- Complete all 50 in a single pass without pausing between items.
