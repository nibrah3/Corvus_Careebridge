# Discovery Automation Status: Jobs & Schools

**Date:** 2026-06-04 06:55 UTC  
**Status:** ✓ FULLY AUTOMATED - No manual triggers needed

---

## Current Discovery Pipeline State

### Raw Discoveries (Staging)
```
Total:        928 items
Unprocessed:  44 (waiting for gating)
Processed:    288 (gated and accepted as jobs)
Blocked:      596 (gated and rejected)
```

### Jobs (Production)
```
Total:        3,164 jobs
Pending:      830 (awaiting user action/approval)
Applied:      1 (user applied)
Blocked:      739 (duplicate URLs)
```

### Pipeline Flow (Automated)

```
1. DISCOVERY (Continuous)
   VPS Crawlee/Crawl APIs
        ↓ (Feeds new URLs)
   raw_discoveries table (44 unprocessed waiting)

2. GATING (Automated via cron)
   Claude Code gate skill (every 15 min trigger)
        ↓ (Applies Scout rules)
   Accepted → jobs table (288 created)
   Rejected → marked blocked (596)

3. ENRICHMENT (Continuous)
   career-watchdog service
        ↓ (Monitors job platforms continuously)
   Updates job metadata

4. PRESENTATION (On-demand)
   User asks "Show me jobs"
        ↓
   Displays pending jobs (830 available)
```

---

## Automation: What's Running Now?

### Continuous Services (Always Running)

**1. corvus-career-watchdog.service** ✓ RUNNING
```
Purpose:      Real-time job platform monitoring
Status:       Active for 15h+ (since 2026-06-03 06:55:32)
Memory:       25.7M
CPU:          1m 8s total
Function:     Monitors career pages for new postings
Triggers:     Continuous (not time-based)
```

**2. corvus-bot.service** ✓ RUNNING
```
Purpose:      Telegram bot interface
Status:       Active
Function:     User interaction, Telegram notifications
```

**3. corvus-queue-worker.service** ✓ RUNNING
```
Purpose:      Process discovery queue
Status:       Active
Function:     Bull/Redis job processor
```

### Scheduled Tasks (Cron Jobs)

| Time | Task | Frequency | Purpose |
|------|------|-----------|---------|
| 5 AM | discover_all.sh | Daily | Full discovery sweep + signal |
| 5 AM | raw_ready signal | Daily | Triggers Claude Code gating |
| 6 AM | corvus_discovery.py | Daily | Apify crawler |
| 12:00 AM, 6 AM, 12 PM, 6 PM | discover_tier1.sh | Every 6h | Tier-1 platforms |
| 7 AM & 7 PM | Reddit discovery | 2x daily | Reddit job threads |
| 8 AM | serper_graph_expand.py | Daily | Search term expansion |
| Every 30 min | run_sweep.sh | 30-min | Cleanup + status |

**Total cron jobs:** 7 main discovery tasks + 1 sweep task = **8 crons**

---

## Discovery Automation: How It Works

### Flow Diagram

```
┌─ DISCOVERY PHASE (VPS) ─────────────────────┐
│                                              │
│  Crawlee/APIs scrape platforms              │
│  → raw_discoveries table (new URLs)         │
│  → Currently: 44 unprocessed waiting        │
│                                              │
└──────────────────────────────────────────────┘
         ↓
┌─ GATING PHASE (Claude Code) ────────────────┐
│                                              │
│  Triggered by: cron publish "corvus:raw_ready"  │
│  Frequency: 5 AM daily + immediate on signal│
│  Process:                                    │
│    1. get_raw_discoveries(limit=50)         │
│    2. Apply Scout gate rules                │
│    3. Accept → upsert_job()                 │
│    4. Reject → mark_blocked()               │
│    5. Update search term performance        │
│                                              │
│  Result: 288 new jobs created, 596 blocked  │
│                                              │
└──────────────────────────────────────────────┘
         ↓
┌─ ENRICHMENT PHASE (Continuous) ─────────────┐
│                                              │
│  career-watchdog monitors job platforms     │
│  Updates job metadata (descriptions, URLs)  │
│  Firecrawl fetches official URLs            │
│                                              │
│  Result: 830 pending jobs ready for users   │
│                                              │
└──────────────────────────────────────────────┘
         ↓
┌─ PRESENTATION (On-Demand) ──────────────────┐
│                                              │
│  User asks "Show me jobs"                   │
│  Returns: 8 pending jobs as cards           │
│  User: Apply, Skip, More Info               │
│                                              │
└──────────────────────────────────────────────┘
```

---

## For Jobs: Is Discovery Continuous?

### YES - Fully Automated

**What's happening:**
```
✓ Crawlee scraping continuously on VPS
✓ New URLs pushed to raw_discoveries every 5-30 minutes
✓ Gating triggered automatically (5 AM daily + immediate signal)
✓ Career-watchdog monitoring platforms 24/7
✓ Every 30 min: Cleanup sweep checks queue depth
```

**What you need to do:** NOTHING - it's automated

**Next discovery runs:**
```
5 AM tomorrow  - Full discovery sweep
6 AM tomorrow  - Apify crawler
7 AM tomorrow  - Reddit discovery
Every 6 hours  - Tier-1 platforms
Every 30 min   - Status check (ongoing)
```

**Queue status:**
```
Raw discoveries waiting:    44 (will be gated at next trigger)
Jobs ready for users:       830 (available now)
Pending approvals:          830
```

---

## For Schools: Is Discovery Continuous?

### PARTIALLY - Currently Manual, Can Be Automated

**Current setup:**
```
✓ Scorecard API key available (XhIoK9TkwMek...)
✓ 479 schools already in database (scored for accessibility)
✗ Not pulling new schools continuously from Scorecard API
✗ No scheduled Scorecard API refresh
```

**What's NOT happening:**
```
- Scorecard API not queried regularly for new schools
- No incremental school discovery (one-time load only)
- No scheduled accessibility re-scoring
- No new platforms added via school enrichment
```

**What you CAN do to automate:**

**Option 1: Add Scorecard API cron (Automated)**
```bash
# Add to crontab:
0 2 * * 0  cd /opt/corvus && python3 scripts/scorecard_sync.py >> /var/log/schools-scorecard.log 2>&1
# Syncs all 7,000+ US schools weekly, scores for accessibility
```

**Option 2: Continuous monitoring (Like career-watchdog)**
```bash
# Create schools_watchdog.py service
# Monitors new school registrations
# Runs continuously like career-watchdog
```

**Option 3: One-time bulk load + manual refresh**
```bash
# Load all 7,000+ schools once via Scorecard API
# Manually re-run when needed (monthly/quarterly)
# Don't automate yet until stable
```

---

## What Needs to Be Triggered Manually vs. Automated

### Jobs - NO TRIGGERS NEEDED ✓ Fully Automatic

| Task | Automated? | How |
|------|-----------|-----|
| Scrape platforms | ✓ Yes | Crawlee 24/7 |
| Push raw data | ✓ Yes | Every 5-30 min |
| Gate discoveries | ✓ Yes | Cron 5 AM + signal |
| Enrich jobs | ✓ Yes | career-watchdog continuous |
| Show to users | ✓ Yes | On-demand query |

**Result:** 830 jobs available right now, new ones added daily automatically.

---

### Schools - PARTIALLY AUTOMATED ⚠️ Manual Setup Needed

| Task | Automated? | How |
|------|-----------|-----|
| Fetch from Scorecard | ✗ No | Manual trigger needed |
| Score accessibility | ✗ No | Manual trigger needed |
| Show to users | ✓ Yes | On-demand query |

**Current state:** 74 schools (score >= 3) available now.

**To automate:** Create `scorecard_sync.py` cron to weekly fetch + score new schools from Scorecard API.

---

## Should You Trigger School Discovery?

### RECOMMENDED: Add Scorecard API Automation

**Why:**
```
- You have the API key (already in .env)
- You have the schema (schools table exists)
- You have the scoring logic (6-criteria system works)
- You're missing 6,500+ schools (huge gap)
```

**Implementation (One-Time Setup):**

```python
# Create /opt/corvus/scripts/scorecard_sync.py
# Steps:
# 1. Query Scorecard API for all ~7,000 accredited US schools
# 2. For each new school:
#    a. Fetch official website
#    b. Score against 6-criteria
#    c. Insert into schools table
# 3. Log results
# 4. Send summary to Telegram

# Add to crontab:
0 2 * * 0  python3 /opt/corvus/scripts/scorecard_sync.py
# Runs: Every Sunday at 2 AM (off-peak)
```

**Benefits:**
```
✓ Continuous school discovery like jobs
✓ 7,000+ schools available vs. 74
✓ Automated accessibility scoring
✓ New schools added weekly
✓ Same pattern as job discovery
```

**Timeline:**
```
First run:   ~30 min (7,000+ schools × 2-3 sec each)
Subsequent:  ~5 min (incremental updates only)
```

---

## Summary: Current vs. Desired State

### JOBS - Ready to Go ✓

```
Current:
✓ Discovery automated (continuous crawling + daily cron)
✓ Gating automated (triggered on signal + scheduled)
✓ Enrichment automated (career-watchdog 24/7)
✓ Queue processing automated (every 30 min sweep)
✓ 830 jobs available now

Next run: Tomorrow 5 AM (automatic)
User action: None needed
```

### SCHOOLS - Mostly Ready, One Automation Gap ⚠️

```
Current:
✓ 74 schools available (score >= 3)
✓ Accessibility scoring working
✓ User interface ready
✗ Scorecard API not syncing continuously
✗ Missing 6,500+ potential schools

Next run: Manual (or set up cron)
User action: Optional — add Scorecard sync cron if you want 7,000+
```

---

## Recommendation

**For Jobs:** ✓ Nothing to do — fully automated

**For Schools:** 
- **If happy with 74 schools:** Do nothing
- **If want all 7,000+ US schools:** Set up Scorecard API sync cron (1-2 hours setup)

**To implement schools automation:**
```bash
# 1. Create scorecard_sync.py script
# 2. Add to crontab (Sunday 2 AM weekly)
# 3. Monitor first run in logs

# Result: Every Sunday, new schools auto-fetched + scored
```

---

**Current automation:** Jobs fully automated, schools partially (74 schools, could be expanded to 7,000+)

Document created: `DISCOVERY_AUTOMATION_STATUS.md`
