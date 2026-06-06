# CareerBridge Gating & Enrichment Plan

**Updated:** 2026-06-03  
**Status:** Database cleaned, pipeline ready

---

## Claude Code's Actual Role: Scout Gating Intelligence

### NOT What Claude Does
- ✗ Score jobs against user profiles
- ✗ Enrich jobs with user-specific data
- ✗ Apply user preferences or filters
- ✗ Create personalized job lists

### YES: What Claude Does — GATING
Claude Code acts as **Scout**, an autonomous intelligence that filters raw scraped data into valid opportunities using strict gate rules.

```
raw_discoveries (unfiltered scrapes from platforms)
  |
  | (Scout applies gate rules)
  |
jobs table (valid gigs/tasks/remote work only)
```

---

## Gate Rules: What to KEEP vs BLOCK

### BLOCK (Filter Out)
- **Aggregators**: Job boards listing 100+ jobs (noise, low signal)
- **Blog posts & articles**: Not actual job postings
- **Social media posts**: Tweets, LinkedIn status updates
- **School enrollment pages**: Professional education (out of scope)
- **Professional/licensed jobs**: Require credentials (lawyer, doctor, engineer)

### KEEP (Accept)
- **Gig work**: Freelance tasks, one-off projects, bounties
- **Remote work**: Full-time, part-time, contract positions
- **Task platforms**: Appen, Toloka, RWS, Clickworker, etc.
- **Content work**: Writing, tutoring, design, music
- **Microtasks**: Anything accessible globally without licensing

---

## Data Flow: Discovery -> Gating -> Jobs

### Input: raw_discoveries
```
id | url | title | company | raw_content | source | processed | blocked
906 items total
897 unprocessed (need gating)
9 already processed
```

**Example unprocessed:**
```
[21281] hn_hiring    "Remote (USA) Trace Labs"
[20298] rws          "Brazilian Portuguese - Literature"
[20364] hackernews   "Roles at Skyvern"
```

### Process: Claude Code (Scout) Gates Each Item
For each discovery:
1. **Read** title, company, raw_content
2. **Evaluate** using gate rules: Is this a gig/task/remote opportunity?
3. **Decide**:
   - YES -> `upsert_job()` + `mark_discovery_processed(id, job_id)`
   - NO -> `mark_discovery_blocked(id, reason)`
4. **Expand** graph (find new platforms, categories)
5. **Tune** search performance (update_term_performance)
6. **Remember** (write Scout memory)

### Output: jobs table
```
Only valid gigs accepted
897 raw items -> ~X valid jobs (X = number that pass gate)
Discovery graph expanded with new platforms/categories
Search term performance updated
```

---

## Step-by-Step: How Claude Code Processes Each Discovery

```python
batch = get_raw_discoveries(limit=25)  # Get 25 unprocessed

for discovery in batch:
    url = discovery['url']
    title = discovery['title']
    company = discovery['company']
    content = discovery['raw_content']
    source = discovery['source']
    
    # STEP 1: Apply Gate Rules
    if is_aggregator(content):
        mark_discovery_blocked(discovery['id'], 'aggregator')
        continue
    
    if is_blog_post(content):
        mark_discovery_blocked(discovery['id'], 'blog_post')
        continue
    
    if is_professional_job(title, company):
        mark_discovery_blocked(discovery['id'], 'professional_job')
        continue
    
    # STEP 2: Looks valid! Create job
    job_id = upsert_job(
        url=url,
        title=title,
        company=company,
        source='discovered',
        status='pending'
    )
    
    # STEP 3: Mark processed
    mark_discovery_processed(discovery['id'], job_id)
    
    # STEP 4: Expand graph
    platforms = extract_platforms_from(content)
    categories = identify_job_type(title, content)
    for platform in platforms:
        add_to_discovered_platforms(platform)
    for category in categories:
        add_to_search_terms(category, source='scout_generated')
    
    # STEP 5: Update performance
    update_term_performance(source, hits=1, gig_hits=1 if valid else 0)

# STEP 6: Remember
write_scout_memory({
    'cycle': 'gate',
    'items_processed': len(batch),
    'valid_gigs': count_accepted,
    'blocked': count_rejected,
    'new_platforms': platforms_added,
    'new_categories': categories_identified
})
```

---

## Database Cleanup Summary

### What Was Done
- **Deleted**: 3 profiles (James Okafor, and 2 others)
- **Cleared**: 452 jobs that referenced deleted profiles (set profile_id = NULL)
- **Result**: Clean database ready for gating pipeline

### Why
- Profiles represented user preferences/profile matching
- New system is Scout-based (gating, not filtering by profile)
- Clean slate = no confusion between old and new logic

### Current State
```
raw_discoveries: 906 total
  - 897 unprocessed (need gating)
  - 9 processed (already gated)
  - 0 blocked

jobs: 3,146 total
  - All jobs now have profile_id = NULL
  - Top sources: enrichment (950), us_schools (556), greenhouse (462)
  - Status distribution: pending (1,792), blocked (1,350)

profiles: 0 (deleted)
```

---

## What Comes Next: The Gating Pipeline

### Batch Processing Loop
```
WHILE raw_discoveries.unprocessed > 0:
  batch = get_raw_discoveries(limit=25)
  
  for discovery in batch:
    gate(discovery)  # Apply rules above
  
  REPEAT until queue empty
```

### Expected Results After Full Gating
- All 897 unprocessed discoveries evaluated
- ~X items accepted as valid gigs (depends on gate rules)
- ~Y items blocked (aggregators, blogs, professional jobs)
- Discovery graph expanded (new platforms, categories)
- Search term performance tuned
- Scout memory updated

### Timeline
- **Batch size**: 25 items
- **Batches needed**: 897 / 25 = ~36 batches
- **Time per batch**: ~2-3 minutes (includes firecrawl for ambiguous)
- **Total**: ~1.5-2 hours

---

## Key Differences: Old vs New System

| Aspect | Old (Profile-based) | New (Scout-based) |
|--------|-------------------|------------------|
| **Logic** | Filter by user profile | Filter by gate rules |
| **Profiles** | Required (3 profiles) | Not needed (deleted) |
| **Job scoring** | Score vs. profile match | Accept/reject by rules |
| **Expansion** | Static | Dynamic (finds new platforms) |
| **Performance tracking** | Per-user | Per-source (search terms) |
| **Output** | Jobs for specific user | Universal valid gigs |

---

## Scout Values (Guiding Principles)

1. **Signal over volume** — One well-gated job > ten ambiguous ones
2. **Quality before quantity** — Only valid opportunities reach user
3. **Expansion mindset** — Every discovery is a lead for more platforms
4. **Geographic equity** — Find work accessible in Kenya, Nigeria, India, Philippines
5. **Honest skepticism** — Retire sources that produce noise

---

## End Result

A system where:
- Raw scraped data enters `raw_discoveries`
- Scout gates each item using clear, verifiable rules
- Only legitimate gigs/tasks/remote work reach `jobs` table
- Discovery graph continuously expands (new platforms, categories)
- Search performance is optimized (high-signal sources promoted)
- No user profiles needed (clean separation of concerns)
