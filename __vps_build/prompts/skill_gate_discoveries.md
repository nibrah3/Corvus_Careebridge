# Skill: Gate & Enrich New Discoveries (VPS)

**Trigger:** VPS cron `*/20 * * * *` OR Redis push on `corvus:raw_ready`.
**Mode:** Fully autonomous.

---

## What you are doing

The VPS Crawlee/Serper/Firecrawl system has deposited raw URLs into `raw_discoveries`.
Read each one, decide keep or block, classify, enrich, write clean records to `jobs`.

This is the VPS-side version. Uses postgres_mcp (localhost:8801) directly.

---

## Step 1 — Check connection

Call postgres `get_system_status` equivalent — ping the MCP: `curl -s localhost:8801/health`
If unreachable: log error to /opt/corvus/logs/gate_errors.log and exit.

## Step 2 — Fetch raw batch

Call postgres_mcp `get_raw_discoveries(limit=50)` equivalent.
If count = 0: exit silently.

## Step 3 — Gate each discovery

**BLOCK immediately if:**
- Aggregator search page (indeed.com/q-, ziprecruiter search, simplyhired search)
- Blog post, news article (/blog/, /news/, /article/, /post/)
- Social media post (Reddit post URL, Facebook, Instagram, LinkedIn post — not company page)
- School enrollment page
- Professional job (engineering, medical, legal, accounting — requires degree/license)
- Raw content is empty or < 50 chars of real text

**KEEP if:** gig/task/remote work accessible without professional licensing.

## Step 4 — For KEEP items

1. Classify job_type from taxonomy
2. Extract requirements summary (2-3 sentences from raw_content)
3. Identify official employer URL if different from discovery URL
4. Write to jobs table via upsert_job
5. Mark raw as processed

## Step 5 — Update search term performance

For each kept discovery, update gig_rate for the source_query term.
For each blocked discovery, update hit_count only.

## Step 6 — Summary

Write to /opt/corvus/logs/gate.log: kept N, blocked N.
If gate produced > 10 new jobs: signal corvus:jobs_ready to Redis.

---

## On Error

**postgres_mcp unreachable:**
→ Check: `systemctl status postgres-mcp` or `ps aux | grep postgres_mcp`
→ If dead: restart with `/opt/corvus/start_mcps.sh`
→ Wait 5s, retry once.

**Redis unreachable:**
→ Check: `redis-cli ping`
→ If dead: `systemctl restart redis` (or docker restart corvus-redis)
→ This is non-fatal — gate can run without Redis signal.
