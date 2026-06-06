# Task 3: Clean Schools Database

**Status:** Ready to execute  
**Location:** raw_schools on VPS (accessed via SSH tunnel)  
**Process:** Apply 6 criteria scoring per skill_analyze_schools.md  
**Output:** schools table with criteria_score (0-6)

---

## What Makes a "Clean" School

Each school is evaluated against **6 binary criteria**. Only YES answers based on explicit page text:

### The 6 Criteria

1. **community_college**
   - Explicit mention: "community college", "two-year college", "associates degree"
   - Evidence: Actual quote from official website

2. **no_id_verification**
   - Explicit statement: "enrollment without government-issued ID"
   - Evidence: Page text confirming this

3. **no_transcript_required**
   - Explicit statement: "no prior transcripts", "no academic records needed"
   - Evidence: Page text confirming this

4. **monthly_enrollment**
   - Explicit statement: "rolling start dates", "monthly enrollment", "start any time"
   - Evidence: Page text confirming this

5. **instant_acceptance**
   - Explicit statement: "same-day acceptance", "immediate approval", "instant enrollment"
   - Evidence: Page text confirming this

6. **monthly_refund**
   - Explicit statement: "monthly refund", "pro-rated tuition", "refund policy"
   - Evidence: Page text confirming this

### Scoring

`criteria_score = count of YES answers (0-6)`

**Decision rule:**
- Score 5-6: Very accessible school (high priority)
- Score 3-4: Moderately accessible
- Score 0-2: Low accessibility (likely professional/traditional)

---

## Workflow per skill_analyze_schools.md

```
1. Get batch: mcp__vps__get_raw_schools(limit=20)

2. For each raw school:
   a) Read raw_content
   b) Answer 6 criteria (YES only if explicit)
   c) Score = count of YES
   d) Mine for affiliated schools mentioned
   
3. Write to schools table with:
   - name, url, community_college, no_id_verification, ...
   - criteria_score (0-6)
   - evidence (quotes from page text)

4. Mark processed: mcp__vps__mark_school_processed(school_id)

5. Notify: Telegram with summary
```

---

## Key Rules

- **Only explicit YES**: If criteria not mentioned or contradicted → NO
- **Evidence required**: Save quote from page text for each YES answer
- **Firecrawl on empty**: If raw_content < 200 chars, fetch fresh page
- **False negatives worse**: When uncertain → mark as NO (not YES)
- **Mine for mentions**: Extract any partner/affiliated school names

---

## After Cleanup

Expected result:
- All raw_schools processed and scored (0-6)
- Only high-quality (score >= 3) shown to users
- Discovery graph expanded (new schools from mentions)
- Evidence trail maintained (why each school scored as it did)

---

## Execution

Run on VPS via scheduled task (every 4 hours) or manual trigger:
```
/analyze-schools
```

Or via CLI on VPS:
```
python -m schools_mcp.server --skill analyze_schools
```

---

## Current State

- raw_schools on VPS: ~500+ entries (estimate)
- schools table (local): EXISTS (0 entries currently)
- Last run: Unknown

Ready to execute after Task 1 & 2 complete.
