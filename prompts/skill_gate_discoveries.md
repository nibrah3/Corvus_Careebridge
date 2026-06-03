# Skill: Gate & Enrich New Discoveries

**Scout persona active for this skill. Read `prompts/scout_persona.md` before Step 1.**



**Trigger:** Called by `CareerBridge_Gate` (every 15 min) OR `raw_listener.py` (immediate push when VPS signals new data).
**Mode:** Fully autonomous — no user present. Run to completion.

---

## What you are doing

The VPS Crawlee system has deposited raw URLs into the `raw_discoveries` table.
These are unfiltered — they include blog posts, school pages, professional jobs,
aggregator search URLs, Reddit threads, and real gig listings all mixed together.

Your job: read each one, decide keep or block, classify the keepers, write clean
records to the `jobs` table, and update the company catalogue.

---

## Step 0 — Read Scout memory

Call `mcp__memory__read("scout_gate")`.
Note any sources that performed well/poorly last cycle.
Note any regional signals or anomalies to watch for.

## Step 1 — Check connection

Call `mcp__vps__get_system_status`.
If redis or postgres is "error": send `mcp__telegram__notify("Gate skipped: VPS connection down")` and stop.

## Step 2 — Process raw discoveries

Call `mcp__vps__get_raw_discoveries(limit=50)`.
If count = 0: nothing to do, exit silently.

For each discovery:

**BLOCK immediately (no further analysis needed) if:**
- URL is an aggregator search page: `indeed.com/q-`, `ziprecruiter.com/Jobs/`, `simplyhired.com/search`
- URL is a Reddit/HN/Facebook/Instagram/YouTube post
- URL is a blog article (contains `/blog/`, `/news/`, `/article/`, `/post/`)
- URL domain is a general-purpose site with no gig relevance (insurance, healthcare system, university enrollment)
- Title explicitly says "Apply at [School Name]" or "Open Enrollment"

→ Call `mcp__vps__mark_raw_blocked(id, reason)` where reason is one of:
  `aggregator_search_page | blog_post | social_media_post | school_enrollment_page | professional_job | vague`

**KEEP and classify if:**
The URL leads to a gig/task/remote work opportunity that doesn't require a
professional degree or license. This includes:
- AI/ML training, data annotation, labeling tasks
- Search quality rating / web evaluation
- Transcription, captioning, translation
- Content moderation, trust & safety review
- Virtual assistant, data entry, customer support (task-based, not FTE)
- Software/app/website usability testing
- GPT / Get Paid To: surveys, offers, cashback, microtasks
- Online tutoring (per-session, not faculty employment)

For KEEP items:
1. Determine `job_type` (ai_training | data_annotation | search_rating | transcription |
   translation | content_writing | social_media | virtual_assistant | customer_support |
   microtask | tutoring | testing | moderation | gpt | other_gig)
2. Extract the canonical company/platform URL (not the aggregator that listed it)
3. Write a 1-2 sentence requirements summary from the raw_content
4. Call `mcp__vps__upsert_job(url=canonical_url, title=..., company=...,
   description=requirements, job_type=job_type, source_url=discovery_url, source=source)`
5. Call `mcp__vps__mark_raw_processed(id)`
6. If this is a new platform not yet in the catalogue:
   → Call `mcp__vps__update_catalogue_tier(platform_id, tier=2, reason="newly discovered via gate")`

## Step 3 — Update search term performance (gig_rate feedback)

Group the processed discoveries by `source_query`.
For each query term that appeared in processed discoveries:
  - `hits` = count of discoveries from that query
  - `gig_hits` = count of kept (not blocked) discoveries from that query
  Call `mcp__vps__update_term_performance(term=source_query, hits=hits, gig_hits=gig_hits)`

This keeps the gig_rate field accurate so high-signal queries get promoted
and noise queries get deactivated automatically.

## Step 4 — Write Scout memory

Call `mcp__memory__write("scout_gate", {
  "cycle_ts": "[ISO timestamp]",
  "kept": N,
  "blocked": N,
  "sources_that_performed": [queries with gig_rate > 0.3 this cycle],
  "sources_that_flopped": [queries with gig_rate = 0 and > 3 hits],
  "new_platforms_seen": [company names in kept jobs not yet in discovered_platforms]
})`

## Step 5 — Summary

`mcp__telegram__notify("Gate complete: kept N, blocked N | queue depth X")`

---

## Rules

- Load `prompts/scout_persona.md` at session start for Scout decision authority.
- Never call any Python script or shell command. Use MCP tools only.
- Never submit/approve jobs. Gate only.
- If a URL is ambiguous: keep it with job_type='other_gig' rather than blocking.
- Speed matters: process all 50 in a single pass without pausing.

---

## On Error

**VPS connection fails:**
→ Run: `Test-NetConnection localhost 6380` (Redis) and `localhost 5433` (Postgres)
→ If either down: run `scripts/vps_tunnel.ps1`, wait 10s, retry once.
→ If still down: `mcp__telegram__notify("Gate skipped: VPS unreachable")` and stop.

**Empty raw_discoveries even though signal was received:**
→ Check: did corvus_discovery.py actually push anything? Check /opt/corvus/logs/discovery_*.log via SSH.
→ This can happen if Serper key expired or Crawlee is down. Note in Scout memory.

**memory_mcp unreachable:**
→ Non-fatal. Skip Steps 0 and 4. Gate runs without memory — just less optimized.

## On Error (already present — see end of file)
