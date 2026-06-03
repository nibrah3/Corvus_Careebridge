# Skill: Graph Expansion

**Trigger:** `CareerBridge_GraphExpand` Task Scheduler task (every 4 hours).
**Mode:** Fully autonomous. Run to completion.

---

## What you are doing

For every company name that appeared in recently gated jobs but has no career page
in `discovered_platforms`, find their careers URL using Serper and add them.

Also mine recent job descriptions for mentions of OTHER platforms — "similar platforms
include X", "you can also work at Y" — these are high-quality leads that only Claude
Code can extract by reading the text.

This is the compounding mechanism: every gated job seeds more platforms, which
produce more jobs, which seed more platforms.

---

## Step 1 — Read Scout memory

Call `mcp__memory__read("scout_graph_expand")`.
Note any companies flagged from the last cycle for priority processing.

## Step 2 — Get unexplored companies

Call this SQL via vps_mcp (or use get_gap_report to see company counts):
```
SELECT DISTINCT company FROM jobs
WHERE company IS NOT NULL AND company != ''
  AND company NOT IN (SELECT company FROM probe_log)
  AND discovered_at > NOW() - INTERVAL '7 days'
ORDER BY company
LIMIT 30
```

If you can't run raw SQL, call `mcp__vps__list_jobs(limit=200)` and extract
distinct company names not in discovered_platforms yourself.

## Step 3 — Find careers pages via Serper

For each company (max 30 per run):
  1. Search: `"[company name]" careers jobs apply remote`
  2. Read the results in context — YOU decide which result is the actual careers page.
     Do NOT use a regex. Read the title, snippet, and URL and reason about it.
     A careers page typically:
       - Has the company domain in the URL
       - Contains words: careers, jobs, work-with-us, opportunities, hiring
       - Title mentions: "Work at X", "Jobs at X", "Careers — X"
  3. If a strong careers URL found:
     - Add to probe_log: `mcp__vps__record_probe(company, url)` (if tool exists, else note it)
     - Add to discovered_platforms via SQL or available MCP tool
     - Telegram: do NOT notify for individual platforms — batch at end
  4. If no careers URL found: record in probe_log as null (skip next cycle)

  Rate limit: 0.5s between Serper calls.

## Step 4 — Mine job descriptions for platform mentions

Call `mcp__vps__list_jobs(status="pending", limit=50)`.
For each job, read the description field in context.
Look for patterns like:
  - "similar to [Platform], [Platform]"
  - "you can also find work at [Platform]"
  - "check out [Platform] for similar opportunities"
  - Company names you don't recognize in the `discovered_platforms` table

For each mentioned platform NOT already in discovered_platforms:
  - Add to search_terms: `mcp__vps__upsert_search_term(term="[platform name] remote work", source="job_description_mention")`
  - Note it in Scout memory for next cycle's Serper expansion

## Step 5 — Write to memory

Call `mcp__memory__write("scout_graph_expand", {
  "cycle_ts": "[ISO timestamp]",
  "companies_explored": N,
  "platforms_found": N,
  "mentions_extracted": N,
  "top_finds": ["company1 → url1", "company2 → url2"]
})`

## Step 6 — Summary

`mcp__telegram__notify("Graph expand: explored N companies, found N new platforms, N mentions queued")`

---

## On Error

**Serper quota exceeded:**
→ Stop Serper calls. Continue with Step 4 (description mining) — no quota needed.
→ Write "serper_quota_exceeded" to memory so next cycle skips Serper gracefully.

**VPS unreachable:**
→ Check tunnel: `Test-NetConnection localhost 5433`
→ If down: run `scripts/vps_tunnel.ps1`, wait 10s, retry once.
→ If still down: skip this cycle, notify Telegram.

**MCP tools missing (upsert_discovered_platform not yet implemented):**
→ Use Bash to insert directly via psql or Python script.
→ This is expected on first run before all MCP tools are deployed.
