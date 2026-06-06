# Visit & Analyze Blocked Schools

You are visiting 722 schools that Firecrawl blocked, using residential proxies (one visit per school), then analyzing them for CareerBridge criteria.

## Your Loop

### Step 1: Get batch of blocked schools
```bash
cd E:\Corvus_Careebridge
python -c "
import sys,os,psycopg2,psycopg2.extras,json
sys.path.insert(0,'.')
for line in open('.env').read().splitlines():
    if '=' in line and not line.startswith('#'): k,_,v=line.partition('='); os.environ[k.strip()]=v.strip()
conn=psycopg2.connect('postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge',connect_timeout=10)
cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute('SELECT id,name,url FROM raw_schools WHERE (raw_text IS NULL OR length(raw_text)<=50) LIMIT 15')
rows=[dict(r) for r in cur.fetchall()]
conn.close()
print(json.dumps({'count':len(rows),'schools':rows}))
"
```

Parse the JSON output. If count==0, exit loop — you're done.

### Step 2: Visit each school via residential proxy
For each school in the batch, run:
```bash
python E:\Corvus_Careebridge\scripts\school_visit_and_analyze.py << 'EOF'
URL_HERE
EOF
```

This visits the school once via IPRoyal residential proxy (one-shot, no fingerprint needed).

### Step 3: Extract + analyze in Claude context
For each school you successfully visited:
- Read the returned HTML/text content
- Analyze for 6 criteria (same as Phase 2):
  1. **community_college** — community/two-year college OR name has "Community College"
  2. **no_id_verification** — explicitly says no government ID needed
  3. **no_transcript_required** — explicitly says no transcripts/records needed
  4. **monthly_enrollment** — rolling enrollment, monthly start dates
  5. **instant_acceptance** — same-day or 24-hour acceptance decision
  6. **monthly_refund** — monthly or pro-rated refund policy

Be conservative: only TRUE if explicitly stated.

### Step 4: Build results JSON
Create array of: `[{"id":N,"name":"...","url":"...","city":"...","state":"...","is_community_college":bool,"community_college":bool,"no_id_verification":bool,"no_transcript_required":bool,"monthly_enrollment":bool,"instant_acceptance":bool,"monthly_refund":bool}...]`

Write to `C:\tmp\blocked_results_[yourname].json`

### Step 5: Save to database
```bash
python E:\Corvus_Careebridge\scripts\school_gate_save.py < C:\tmp\blocked_results_[yourname].json
```

### Step 6: Loop back to Step 1
Continue until count==0.

## Key Points
- One visit per site (residential proxy is enough — no fingerprints needed)
- Timeout is 15s per visit (anti-DoS)
- Extract enrollment language from the page
- Analyze what you actually see (don't guess)
- If site is completely blank/broken, mark all criteria FALSE
- Report final tally: total visited, total analyzed, total qualified

## When Done
Run: `python E:\Corvus_Careebridge\scripts\school_gate_status.py` and report final counts.
