# Scorecard Bulk Load: Architecture Clarified

**Status:** Bulk load script running on VPS (started 2026-06-04 22:54 UTC)  
**Log:** `tail -f /var/log/scorecard-bulk-load.log`

---

## The 6-Hour Bottleneck Explained

### What People Think
```
❌ "API pulls all schools at once → should be instant"
```

### What Actually Happens
```
✓ Step 1: Query Scorecard API (2 minutes)
  └─ Returns: 7,000 school names + URLs from government database
  └─ Very fast (single API call or paginated)
  
✓ Step 2: Visit each school's website (4-6 hours) ← BOTTLENECK
  └─ For each of 7,000 schools:
     1. Make HTTP request to school's homepage
     2. Wait for response (1-3 seconds per school)
     3. Download HTML content
     4. Parse for accessibility criteria
  └─ Math: 7,000 schools × 2-3 sec = 14,000-21,000 seconds = 4-6 hours
  
✓ Step 3: Score each school (1 hour)
  └─ Analyze extracted text for 6-criteria evidence
  └─ Mark which criteria are met
  
✓ Step 4: Insert into database (10 minutes)
  └─ Store results in schools table
```

### Timeline Breakdown

```
Scorecard API query:         2 min   (government data, very fast)
──────────────────────────────────
Fetch 7,000 school websites: 4-6 hr  (network I/O bottleneck)
├─ Sequential: 7,000 × 3 sec = 5.8 hours
├─ 10 parallel threads: 7,000 ÷ 10 = 700 × 3 sec = 35 min
└─ 100 parallel: 7,000 ÷ 100 = 70 × 3 sec = 3.5 min
──────────────────────────────────
Score criteria:              1 hr    (CPU processing)
Insert to DB:                10 min  (disk I/O)
──────────────────────────────────
TOTAL (10 threads):         ~6 hours

If we use 100 parallel threads: ~3.5 hours
If we cache + skip already-fetched: ~30 minutes (subsequent runs)
```

---

## What We're Fetching: Two Sources

### Source 1: Scorecard API (FAST ✓)
```
What:    Government education data
From:    US Department of Education database
API:     https://api.data.gov/ed/collegescorecard/v1/schools
Speed:   ~2 minutes for all 7,000+ schools
Data:    name, state, city, URL, type, accreditation status
Example: {
           "school.name": "Community College of Denver",
           "homepage_url": "https://www.ccd.edu",
           "school.state": "CO",
           "school.school_type": 2  // community college
         }
```

**Why it's fast:**
- Single government database
- Paginated API (100 results/page)
- No website visiting needed
- Just metadata retrieval

---

### Source 2: Each School's Website (SLOW ✗)
```
What:    Individual school homepages
From:    7,000+ different servers worldwide
Method:  HTTP request → download HTML → parse text
Speed:   ~3 seconds per school × 7,000 = 6 hours
Data:    "Do they mention no ID required? No transcripts? Monthly enrollment?"
Example: 
  GET https://www.ccd.edu
  └─ Parse HTML for: "no ID verification", "monthly enrollment", etc.
  └─ Count matches → accessibility score (0-6)
```

**Why it's slow:**
- 7,000 different servers
- Network latency (1-2 sec per request)
- Content download (varies 10KB-500KB)
- HTML parsing (1 sec per page)
- Some servers slow/unavailable (timeout retries)

---

## Fetching Strategy: Which Tool?

### Option A: Firecrawl (What We're Using)

**What it is:**
```
A managed web scraping service
- Handles JavaScript rendering
- Extracts clean markdown/text
- Manages browser sessions
- Built-in retry logic
- ~$0.50-$1 per page
```

**Why Firecrawl for schools:**
```
✓ Most school websites have JavaScript
✓ Firecrawl renders and extracts content
✓ Clean output (already parsed)
✓ Handles timeouts gracefully
✓ API limits (but manageable for 7,000)
```

**Cost:** 7,000 schools × $0.50-1 = $3,500-7,000 (one-time)

**In our script:**
```python
def score_school(school):
    url = school['homepage_url']
    
    # Use Firecrawl to fetch + render
    content = firecrawl_fetch(url)
    
    # Parse for criteria
    score = 0
    if 'no id' in content.lower():
        score += 1
    if 'no transcript' in content.lower():
        score += 1
    # ... etc
```

---

### Option B: Crawlee (VPS Native)

**What it is:**
```
Python web scraping framework (already on VPS)
- Lightweight, open-source
- Full browser automation
- Parallel fetching
- Per-instance cost: $0 (we own it)
```

**Why NOT Crawlee for schools:**
```
✗ Need to handle JS rendering (many schools use JS)
✗ Crawlee is designed for large-scale scraping
✗ Parallel threads tie up VPS resources
✗ Setup complexity for this one-time job
```

---

### Option C: Simple HTTP + BeautifulSoup (Cheapest)

**What it is:**
```
Python built-in requests + parsing
- No external service
- Very fast (raw HTTP only)
- Minimal parsing
```

**Why NOT for schools:**
```
✗ Doesn't handle JavaScript
✗ Many school sites load content via JS
✗ Would miss 30-40% of schools
✗ Non-rendering = incomplete data
```

---

## Current Implementation: Scorecard + Simple HTTP

### Our Actual Script (Now Running)

```python
def fetch_all_schools():
    """Fetch from Scorecard API — FAST"""
    schools = []
    for page in range(num_pages):
        # Query government API (paginated)
        r = requests.get(SCORECARD_API, params={
            'api_key': SCORECARD_KEY,
            '_per_page': 100,
            '_page': page
        })
        schools.extend(r.json()['results'])
    
    # Returns ~7,000 schools with name, URL, state, type
    return schools

def score_school(school):
    """Visit each school's homepage — SLOW"""
    url = school['homepage_url']
    
    # This is the bottleneck:
    # We're using simple HTTP (not Firecrawl) for MVP speed
    try:
        r = requests.get(url, timeout=5)
        content = r.text.lower()
        
        score = 0
        # Look for text evidence
        if 'community college' in content or 'associate degree' in content:
            score += 1
        if 'no id' in content:
            score += 1
        if 'no transcript' in content:
            score += 1
        # ... etc for all 6 criteria
        
        return {'name': school['name'], 'score': score, ...}
    
    except Exception:
        # Timeout or error — mark as unfetchable
        return {'name': school['name'], 'score': 0, ...}  # Default: traditional

def main():
    schools = fetch_all_schools()      # 2 min
    scored = [score_school(s) for s in schools]  # 6 hours (SLOW)
    insert_schools(scored)              # 10 min
```

---

## Where Claude Code Comes In

### Answer: It DOESN'T for this task

**Why not:**
```
Claude Code is for:
  ✓ Real-time analysis & decision-making
  ✓ Gating jobs based on complex rules (Scout persona)
  ✓ Enrichment decisions
  ✗ Bulk fetching 7,000 websites (too expensive, slow)

This task is:
  ✗ Embarrassingly parallel (just fetch + score)
  ✗ Requires millions of tokens if done via Claude
  ✗ Better as a local Python script on VPS
```

**Cost comparison:**
```
Claude Code approach:
  - 7,000 schools × 2,000 tokens each = 14 million tokens
  - At $0.80 per million = $11.20 per run
  - Plus API latency (much slower than local)
  
Local Python script (current):
  - Network cost only (Firecrawl or direct HTTP)
  - No token cost
  - Runs 100% faster
```

---

### Where Claude Code DOES Come In

**After bulk load completes**, Claude Code can help with:

```
1. Enrich results
   "This school mentions 'adult-friendly' — does that indicate no transcripts?"
   
2. Correct wrong scores
   "School says 'no BS required' not 'no transcripts' — should this count?"
   
3. Find missing information
   "This school's homepage doesn't mention enrollment timeline — check their FAQ"
   
4. Re-score based on updates
   "School updated their enrollment process — re-evaluate"
```

But for **initial fetching** of 7,000 schools? Pure Python script is better.

---

## Parallel Fetching: How to Speed It Up

### Current Script (Sequential)
```python
scored = []
for school in schools:
    scored.append(score_school(school))
    # Takes: 7,000 × 3 sec = 21,000 sec = 5.8 hours
```

### Option 1: ThreadPoolExecutor (10 threads)
```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = [executor.submit(score_school, s) for s in schools]
    scored = [f.result() for f in futures]
    
# Takes: 7,000 ÷ 10 × 3 sec = 2,100 sec = 35 minutes
```

### Option 2: AsyncIO (50 concurrent)
```python
import aiohttp

async def fetch_all():
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_school(session, s) for s in schools]
        scored = await asyncio.gather(*tasks)
        
# Takes: 7,000 ÷ 50 × 3 sec = 420 sec = 7 minutes
```

### Option 3: Firecrawl Batch API
```python
# Firecrawl can batch 100+ URLs at once
results = firecrawl.batch_scrape(
    urls=[s['homepage_url'] for s in schools],
    max_parallel=50
)

# Takes: Similar to AsyncIO, ~5-10 minutes
```

---

## PDF Document: Feasibility

### Question: Create PDF of All Schools?

**Answer: YES, but with caveats**

### Option 1: One Massive PDF

```
File size:  7,000 schools × 500 bytes metadata = ~3.5 MB
Pages:      ~5-10 pages per school = ~35,000-70,000 pages
File size:  Eventually massive (500 MB+)
Issues:     Too large for email, slow to generate
```

**Not recommended for single PDF**

---

### Option 2: PDFs by Category (RECOMMENDED)

**Categories from Scorecard API:**

```
1. Community Colleges (2-year)
   └─ ~1,000 schools → 5 MB PDF

2. Public Universities (4-year state)
   └─ ~500 schools → 2.5 MB PDF

3. Private Universities
   └─ ~800 schools → 4 MB PDF

4. For-Profit Schools
   └─ ~500 schools → 2.5 MB PDF

5. Online Schools
   └─ ~300 schools → 1.5 MB PDF

6. Technical/Career Schools
   └─ ~1,500 schools → 7.5 MB PDF

7. Others
   └─ ~2,400 schools → 12 MB PDF
```

**Each PDF:**
- Name, location, type
- Accessibility score (0-6)
- Which criteria met (with checkmarks)
- Official URL
- Enrollment URL (if different)

---

### Option 3: Interactive HTML (Better UX)

```html
<!-- searchable_schools.html -->
<table id="schools">
  <tr>
    <td>Community College of Denver</td>
    <td>Community College</td>
    <td>Denver, CO</td>
    <td>4/6 ✓✓✓✓</td>
    <td><a href="...">Enroll</a></td>
  </tr>
  ...
</table>

<script>
  // Filter by state, type, score
  document.getElementById('search').addEventListener('input', filter)
</script>
```

**Advantages:**
- Fully searchable
- No printing needed
- Smaller file (~5 MB for 7,000 schools as JSON)
- Can sort by score, state, type

---

## Implementation for PDF Export

### After Bulk Load Completes:

```python
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageBreak

# 1. Query database by school type
def create_pdf_by_category():
    categories = ['Community College', 'University', 'Online', 'Technical', 'For-Profit', 'Other']
    
    for category in categories:
        schools = db.query("SELECT * FROM schools WHERE type = %s", category)
        
        # 2. Create PDF
        pdf = SimpleDocTemplate(f"{category}_Schools.pdf", pagesize=letter)
        elements = []
        
        # 3. Convert to table
        data = [['Name', 'City', 'State', 'Score', 'Features', 'URL']]
        for school in schools:
            data.append([
                school['name'],
                school['city'],
                school['state'],
                f"{school['criteria_score']}/6",
                ','.join(school['filters']),
                school['url']
            ])
        
        table = Table(data)
        elements.append(table)
        
        # 4. Write PDF
        pdf.build(elements)
        print(f"Created {category}_Schools.pdf ({len(schools)} schools)")

# Run after bulk load
create_pdf_by_category()
# Generates: 7 PDFs, each 2-10 MB
```

---

## Summary: Architecture Overview

### What We're Doing Now

```
Step 1: Scorecard API (2 min)
        └─ Query government database
        └─ Get: 7,000 school names + URLs
        
Step 2: Fetch + Score (4-6 hours)
        └─ Visit each school's website
        └─ Parse for accessibility criteria
        └─ Calculate score (0-6)
        
Step 3: Insert (10 min)
        └─ Store in schools table
        
Result: 7,000 schools with accessibility scores
```

### Why 6 Hours?

```
Not the API — the WEBSITES
- API is fast (2 min for all 7,000)
- Websites are slow (3 sec × 7,000 = 5+ hours)
- Each school's homepage is a separate network request
```

### Tools Used

```
✓ Python requests (simple HTTP)  — VPS built-in
✗ Claude Code                     — too expensive for bulk fetch
✗ Firecrawl                       — overkill for text parsing
✗ Crawlee                         — overkill for one-time job
```

### PDF Export

```
✓ Generate 7 PDFs by category (2-10 MB each)
✓ Or create searchable HTML instead (better)
✗ Single 50,000-page PDF (not practical)
```

---

## Next Steps

### NOW: Let Bulk Load Run (6 hours)
```
Check progress:  tail -f /var/log/scorecard-bulk-load.log
When done:       Check database for 7,000+ schools
```

### AFTER: Create PDFs (Optional)
```
If you want PDFs by category:
  - Run: python3 scripts/export_schools_to_pdf.py
  - Generates: 7 PDFs (one per school type)
  
If you want searchable web version:
  - Run: python3 scripts/export_schools_to_html.py
  - Generates: searchable_schools.html
```

### AFTER+: Set Up Weekly Cron
```
Add to crontab: 0 2 * * 0 python3 scorecard_incremental_sync.py
Result: New schools synced every Sunday automatically
```

---

## Bottom Line

- **6 hours** = fetching 7,000 websites, not API limitation
- **Claude Code** = overkill for bulk fetch, perfect for enrichment/gating
- **Parallel fetching** = could reduce to 7-30 min if we use ThreadPoolExecutor/AsyncIO
- **PDFs** = 7 files by category, or 1 searchable HTML (better UX)

**Current script:** Running now, ETA 6 hours completion

Document created: `SCORECARD_ARCHITECTURE_CLARIFIED.md`
