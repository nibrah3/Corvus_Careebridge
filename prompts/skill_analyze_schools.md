# Skill: Analyze Raw Schools

**Trigger:** `CB_Schools_Gate` Task Scheduler task (every 4 hours) OR manual `/analyze-schools`.
**Mode:** Fully autonomous.

---

## What you are doing

Schools have been collected into `raw_schools` by the Scorecard/Serper collectors.
They are raw text — no criteria have been applied yet. You read each one, apply
the 6 criteria by reasoning over the page text, and write qualified schools to
the `schools` table. Users only see the schools table.

You also mine each page for mentions of affiliated or partner schools — these
become new leads added back to the raw_schools queue.

---

## Step 1 — Check connection

Call `mcp__vps__get_system_status`. If postgres is error: check SSH tunnel and retry once.
If still unreachable: notify Telegram and stop.

## Step 2 — Fetch raw batch

Call `mcp__vps__get_raw_schools(limit=20)`.
If count = 0: exit silently.

## Step 3 — For each raw school

Read the school's `raw_content` field in context. Reason about it.

### Financial aid pre-check
If `federal_loan_rate` = 0 AND source = "serper" (meaning we don't know the loan rate):
  Look for explicit mention in the text of: "financial aid", "federal loans", "Pell grant",
  "Title IV", "FAFSA". If none mentioned: lower priority but still analyze.

### Apply the 6 criteria

Answer each criterion YES only if the page text explicitly states or clearly implies it.
Do NOT assume YES because it is not mentioned — absence is NOT evidence of meeting criteria.

1. **community_college** — explicitly a community college, two-year college, or associates degree program
2. **no_id_verification** — explicitly states enrollment without government-issued ID
3. **no_transcript_required** — explicitly states no prior transcripts or academic records needed
4. **monthly_enrollment** — explicitly states rolling/monthly start dates
5. **instant_acceptance** — explicitly states same-day or immediate acceptance
6. **monthly_refund** — explicitly describes monthly or pro-rated tuition refund

For each YES: note the specific quote from the page text as evidence.
For each NO: note "not stated" or the contradicting phrase found.

### Score
`criteria_score` = count of YES criteria (0-6).

### Write to schools table
Call Bash to insert via Python:
```python
python -c "
import sys; sys.path.insert(0,'E:/Corvus_Careebridge')
from schools_mcp._db import save, ensure_table
ensure_table()
save({
  'name': '...',
  'url': '...',
  'community_college': True/False,
  'no_id_verification': True/False,
  'no_transcript_required': True/False,
  'monthly_enrollment': True/False,
  'instant_acceptance': True/False,
  'monthly_refund': True/False,
  'criteria_score': N,
  'filters': [...],
  'evidence': {...},
  'is_community_college': True/False,
  'federal_loan_rate': N,
})"
```
OR use `mcp__vps__firecrawl_scrape` to get fresher content if raw_content is empty.

### Mine for institution mentions
Read the page text for any mentions of other schools, colleges, or institutions.
Patterns: "transfer to", "partner institutions", "articulation agreement with",
"sister college", "credits accepted at", "our affiliated schools".

For each mentioned institution not already in raw_schools or schools:
  Call `mcp__vps__push_raw_school(url=mention_url, name=mention_name, source="mention")`
  (If you can't determine the URL, just note the name — skip the push.)

### Mark as processed
After writing (or deciding to skip), call `mcp__vps__mark_school_processed(school_id)`.

## Step 4 — Summary

`mcp__telegram__notify("Schools analyzed: kept N (score>=3: N), skipped N (score 0-1), mentions queued: N")`

---

## On Error

**raw_content is empty or too short (<200 chars):**
→ Try fetching the page fresh: `mcp__vps__firecrawl_scrape(url)`.
→ If still empty: mark as processed with score=0, note "empty_content" in evidence.

**DB save fails:**
→ Check: does schools table exist? Run `ensure_table()` via Bash.
→ If URL conflict: school already analyzed — mark raw as processed, skip.

**VPS connection lost mid-run:**
→ Save progress note to memory_mcp: how many processed so far.
→ Notify Telegram. Stop cleanly — next cron run picks up from where this left off.
