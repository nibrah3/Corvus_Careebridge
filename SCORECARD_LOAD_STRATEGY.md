# Scorecard API Load Strategy: One-Time Bulk vs. Incremental Batches

**Question:** Load all 7,000+ schools at once, or batches via cron?  
**Answer:** Hybrid approach — do ONE bulk load now, then weekly cron for new schools

---

## One-Time Bulk Load (Recommended FIRST)

### Why Do It
```
Current state:  74 schools (manually discovered)
Available:      7,000+ schools (via Scorecard API)
Gap:            6,500+ schools missing

Goal: Get baseline dataset quickly, then maintain incrementally
```

### Bulk Load Approach

**Timeline:**
```
Scorecard API query for all schools:  ~2 min (API is fast)
Fetch each school's website:          ~4-6 hours (2-3 sec × 7,000)
Score for 6-criteria:                 ~1 hour (parallel processing)
Insert into database:                 ~10 min

Total: ~6 hours one-time setup
```

**Implementation:**
```python
# Script: /opt/corvus/scripts/scorecard_bulk_load.py

import requests
import concurrent.futures

# Step 1: Get all schools from Scorecard API (~100 results per page)
schools = fetch_all_schools_from_scorecard_api()  # ~2 min, 7,000+ schools

# Step 2: Fetch + score in parallel (fast)
with concurrent.futures.ThreadPoolExecutor(max_workers=10):
    for school in schools:
        fetch_official_site(school)  # 2-3 sec per school
        score_accessibility(school)  # Instant
        insert_into_schools_table(school)

# Step 3: Done
print(f"Loaded {len(schools)} schools in ~6 hours")
```

### Run it ONCE, NOW
```bash
# Run manually, goes in background
nohup python3 /opt/corvus/scripts/scorecard_bulk_load.py > /var/log/scorecard-bulk-load.log 2>&1 &

# Check progress
tail -f /var/log/scorecard-bulk-load.log

# When done: 7,000+ schools in schools table with accessibility scores
```

**Pros:**
- ✓ Get full baseline immediately (6 hours is acceptable one-time cost)
- ✓ Complete dataset ready for users
- ✓ Can parallelize fetching (10 threads = 24-30 min instead of 6 hours)
- ✓ Simple implementation
- ✓ Catches ALL accredited schools at once

**Cons:**
- ✗ Takes 6 hours (but only runs once)
- ✗ Ties up ~10 concurrent connections (negligible on modern VPS)
- ✗ If it fails halfway, need to handle resumption

---

## Weekly Incremental Cron (For NEW Schools)

### Why Do It After Bulk Load
```
New schools get accredited constantly
Scorecard API is updated weekly
Want system to stay current without manual intervention
```

### Incremental Cron Approach

**Timeline:**
```
Query only NEW schools since last run:  ~1 min
Fetch + score them:                    ~5 min (usually 20-50 new)
Insert/update:                          ~1 min

Total: ~7 minutes weekly
```

**Implementation:**
```python
# Script: /opt/corvus/scripts/scorecard_incremental_sync.py

import requests
from datetime import datetime, timedelta

# Only fetch schools updated in the last 7 days
last_sync = get_last_sync_time()  # From metadata table
new_schools = fetch_schools_since(last_sync)  # Usually 20-50 schools

for school in new_schools:
    fetch_official_site(school)
    score_accessibility(school)
    upsert_into_schools_table(school)  # Upsert, don't duplicate

update_last_sync_time(datetime.now())
print(f"Synced {len(new_schools)} new schools")
```

### Add to Crontab
```bash
# Every Sunday at 2 AM (off-peak)
0 2 * * 0  cd /opt/corvus && python3 scripts/scorecard_incremental_sync.py >> /var/log/schools-scorecard-weekly.log 2>&1
```

**Pros:**
- ✓ Stays current with new schools
- ✓ Only runs 7 minutes weekly
- ✓ Low overhead (catches ~30 new schools/week)
- ✓ Handles resumption gracefully
- ✓ Same pattern as job discovery

**Cons:**
- ✗ Doesn't help until initial bulk load done
- ✗ Needs metadata tracking (last_sync timestamp)

---

## Recommended Strategy

### Phase 1: Bulk Load (Do NOW - One Time)

```
Step 1: Run bulk load script
        Takes: 6 hours (but runs in background, you can ignore it)
        Result: 7,000+ schools in schools table

Step 2: Monitor progress
        Check log: tail -f /var/log/scorecard-bulk-load.log
        
Step 3: When done
        ~7,000 schools available to users
        Can still search/filter by accessibility score
```

### Phase 2: Weekly Incremental Cron (Do AFTER Bulk Loads)

```
Step 1: Add cron job
        0 2 * * 0  python3 /opt/corvus/scripts/scorecard_incremental_sync.py

Step 2: Runs automatically every Sunday 2 AM
        Catches new schools
        Takes ~7 minutes
```

---

## Cost-Benefit Analysis

### Option A: One-Time Bulk Load (Recommended)

| Metric | Cost | Benefit |
|--------|------|---------|
| **Time** | 6 hours (once) | 7,000+ schools immediately |
| **API calls** | ~7,000 | Complete baseline |
| **Bandwidth** | ~500MB (1-time) | Full coverage |
| **Complexity** | Low | Simple script |
| **Ongoing** | None needed | Data current |

**Best for:** Getting all schools quickly, then maintaining incrementally

---

### Option B: Weekly Cron Only (Not Recommended as First Step)

| Metric | Cost | Benefit |
|--------|------|---------|
| **Time** | 7 min/week | Incremental only |
| **API calls** | ~30/week | New schools only |
| **Bandwidth** | ~3MB/week | Minimal |
| **Complexity** | Medium | Needs tracking |
| **Result** | Takes 6+ months to get all 7,000 | Miss 6,500 schools for months |

**Best for:** Maintaining after bulk load, not for initial population

---

### Option C: Hybrid (RECOMMENDED)

**Step 1: Bulk load once** (6 hours, now)
```
Get all 7,000 schools
Set up as baseline
Users have comprehensive data immediately
```

**Step 2: Weekly cron** (7 min/week, ongoing)
```
Catches new schools
Keeps system current
Low maintenance cost
```

---

## Implementation Plan

### RIGHT NOW

**1. Create bulk load script:**
```bash
cat > /opt/corvus/scripts/scorecard_bulk_load.py << 'EOF'
#!/usr/bin/env python3
"""
Scorecard API Bulk Load — loads all 7,000+ accredited US schools
Runs once to populate schools table with accessibility scores
"""
import requests
import concurrent.futures
import time
from datetime import datetime

SCORECARD_API = "https://api.data.gov/ed/collegescorecard/v1/schools"
SCORECARD_KEY = os.getenv("SCORECARD_API_KEY")
MAX_WORKERS = 10  # Parallel fetches

def fetch_all_schools():
    """Get all accredited schools from Scorecard API"""
    schools = []
    page = 0
    while True:
        params = {
            "api_key": SCORECARD_KEY,
            "school.degrees_awarded.predominant__gte": 2,  # Accredited
            "_fields": "id,name,school.main_campus,school.state,school.city,school.type,homepage_url",
            "_per_page": 100,
            "_page": page
        }
        r = requests.get(SCORECARD_API, params=params)
        data = r.json()
        
        if not data.get("results"):
            break
        
        schools.extend(data["results"])
        print(f"[{datetime.now()}] Fetched page {page}: {len(schools)} total")
        page += 1
    
    return schools

def score_school(school):
    """Fetch website + score for accessibility"""
    try:
        # Fetch official site
        url = school.get("homepage_url") or ""
        content = firecrawl_scrape(url) if url else ""
        
        # Score accessibility
        score = 0
        filters = []
        
        if "community college" in content.lower() or school.get("type") == "2":
            score += 1
            filters.append("community_college")
        if "no id" in content.lower():
            score += 1
            filters.append("no_id_verification")
        if "no transcript" in content.lower():
            score += 1
            filters.append("no_transcript_required")
        if any(x in content.lower() for x in ["rolling", "monthly", "start any"]):
            score += 1
            filters.append("monthly_enrollment")
        if "instant" in content.lower() and "accept" in content.lower():
            score += 1
            filters.append("instant_acceptance")
        if "monthly refund" in content.lower():
            score += 1
            filters.append("monthly_refund")
        
        return {
            "name": school.get("name"),
            "url": url,
            "state": school.get("school.state"),
            "city": school.get("school.city"),
            "criteria_score": score,
            "filters": filters
        }
    except Exception as e:
        print(f"Error scoring {school.get('name')}: {e}")
        return None

def main():
    print(f"[{datetime.now()}] Starting Scorecard bulk load...")
    
    # Fetch all schools from API
    schools = fetch_all_schools()
    print(f"[{datetime.now()}] Got {len(schools)} schools from Scorecard")
    
    # Score in parallel
    scored = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(score_school, s): s for s in schools}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            result = future.result()
            if result:
                scored.append(result)
            if (i + 1) % 100 == 0:
                print(f"[{datetime.now()}] Scored {i+1}/{len(schools)}")
    
    # Insert into database
    print(f"[{datetime.now()}] Inserting {len(scored)} schools into database...")
    for school in scored:
        insert_school_into_db(school)
    
    print(f"[{datetime.now()}] COMPLETE: {len(scored)} schools loaded!")

if __name__ == "__main__":
    main()
EOF

chmod +x /opt/corvus/scripts/scorecard_bulk_load.py
```

**2. Run it in background:**
```bash
nohup python3 /opt/corvus/scripts/scorecard_bulk_load.py > /var/log/scorecard-bulk-load.log 2>&1 &

# Monitor
tail -f /var/log/scorecard-bulk-load.log
```

**3. When done (check log):**
```bash
# Should see: "COMPLETE: 7000+ schools loaded!"
# Check database:
docker exec corvus-postgres psql -U corvus -d careerbridge -c "SELECT COUNT(*) FROM schools;"
# Should show: ~7,000 rows
```

---

### AFTER Bulk Load Complete

**Add weekly incremental cron:**
```bash
# Same location, different script
cat > /opt/corvus/scripts/scorecard_incremental_sync.py << 'EOF'
# ... similar logic but only fetches new schools ...
EOF

# Add to crontab
crontab -e
# Add line:
# 0 2 * * 0  cd /opt/corvus && python3 scripts/scorecard_incremental_sync.py >> /var/log/schools-scorecard-weekly.log 2>&1
```

---

## Summary & Recommendation

| Approach | Timeline | Result | Recommended |
|----------|----------|--------|-------------|
| **One-time bulk load** | 6 hours now | All 7,000 schools immediately | ✓ YES |
| **Weekly cron only** | 6+ months | Slowly accumulate schools | ✗ No |
| **Hybrid** (bulk + cron) | 6 hours + 7 min/week | Complete + current | ✓ BEST |

**MY RECOMMENDATION:**

1. **Do the bulk load TODAY** (it runs in background)
   - Takes 6 hours
   - You don't need to wait
   - Just monitor the log
   - Result: 7,000 schools available immediately

2. **Add weekly cron AFTER bulk load finishes**
   - Catches new schools
   - Runs 7 minutes weekly
   - Keeps system current indefinitely

**Why not just cron weekly?**
- You'd be missing 6,500 schools for months
- Cron is best for *maintenance*, not initial population
- The bulk load is a one-time 6-hour cost vs. 6+ months of incompleteness

---

## Bottom Line

**Do one sweep NOW (bulk load in background), then maintain with weekly cron.**

Document created: `SCORECARD_LOAD_STRATEGY.md`
