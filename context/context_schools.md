# Context: Schools Session

Loaded when session involves school discovery, analysis, or presenting schools to user.

## Data flow
raw_schools (staging) → Claude Code analyzes → schools table (user-visible)
raw_schools are NEVER shown to users. Only analyzed records in schools table.

## 6 criteria (apply only with explicit evidence)
1. community_college — explicitly two-year/CC
2. no_id_verification — explicitly no government ID required
3. no_transcript_required — explicitly no prior transcripts needed
4. monthly_enrollment — explicitly rolling/monthly start dates
5. instant_acceptance — explicitly same-day/immediate acceptance
6. monthly_refund — explicitly monthly/pro-rated refund policy

## Financial aid filter
Only analyze schools with federal_loan_rate > 0 (Title IV eligible = accredited).
Schools with no loan activity are not worth user attention.

## School expansion
When analyzing a school page, mine for mentions of:
- Partner/affiliated institutions → add to raw_schools
- Transfer destination schools → add to raw_schools
- "Similar programs at X" → add to raw_schools

## User presentation
Use `hook_present_schools.py` format: one school card at a time.
Translate criteria to plain English — never show raw criterion names.
PDF reports sent to Telegram via `mcp__schools__send_school_reports`.
