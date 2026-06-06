# skill_gate_schools — Analyze raw_schools and gate into schools table

You are analyzing US schools from the raw_schools staging table.
Read batches of unanalyzed schools, apply 6-criteria scoring, save qualifying ones.

## Criteria (must pass ≥ 1 to qualify)
1. **community_college** — explicitly a community/two-year/junior college, or predominant degree is associate's
2. **no_id_verification** — explicitly states enrollment WITHOUT government-issued ID
3. **no_transcript_required** — explicitly states no prior transcripts needed
4. **monthly_enrollment** — rolling/monthly start dates explicitly stated
5. **instant_acceptance** — immediate/same-day/instant acceptance explicitly stated
6. **monthly_refund** — monthly or pro-rated refund policy explicitly described

## Rules
- Only return TRUE when explicitly stated or clearly implied by the text
- Absence of mention = FALSE (be conservative)
- For community_college: also return TRUE if the school name contains "Community College", "CC", "Junior College"

## Process
1. Call `sqlite_mcp` or `postgres_mcp` to read a batch: `SELECT id, name, url, city, state, is_community_college, raw_text FROM raw_schools WHERE analyzed=FALSE AND raw_text!='' ORDER BY id LIMIT 20`
2. For each school, analyze raw_text against 6 criteria
3. If criteria_score >= 1: upsert into schools table
4. Mark raw_schools.analyzed=TRUE for each processed row
5. Repeat until no unanalyzed rows remain
6. Generate HTML report when done using generate_html_report tool

## SQL for upsert into schools
```sql
INSERT INTO schools (name, url, enrollment_url, city, state, type,
    community_college, no_id_verification, no_transcript_required,
    monthly_enrollment, instant_acceptance, monthly_refund,
    filters, criteria_score, source_url, url_hash, updated_at)
VALUES (...)
ON CONFLICT (url_hash) DO UPDATE SET
    community_college=EXCLUDED.community_college,
    no_id_verification=EXCLUDED.no_id_verification,
    no_transcript_required=EXCLUDED.no_transcript_required,
    monthly_enrollment=EXCLUDED.monthly_enrollment,
    instant_acceptance=EXCLUDED.instant_acceptance,
    monthly_refund=EXCLUDED.monthly_refund,
    filters=EXCLUDED.filters,
    criteria_score=EXCLUDED.criteria_score,
    updated_at=NOW()
```

## After all batches done
1. Report total: analyzed, qualified (score>=1), excluded (score=0)
2. Call generate_html_report MCP tool to regenerate schools HTML
3. Confirm HTML saved to Desktop/schools/
