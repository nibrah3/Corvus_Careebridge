# Schools Criteria Explained: Traditional vs. Accessible

**Date:** 2026-06-04  
**Context:** Understanding the 6-criteria scoring system and how to access ALL US schools

---

## What "Traditional Schools" Means

Traditional schools are **accredited educational institutions** that require standard prerequisites and formal admission processes:

- Universities (4-year)
- Traditional colleges (4-year)
- Community colleges (2-year with formal admission)
- For-profit schools with standard requirements
- Career/technical schools with prerequisites

**Key characteristic:** They require:
- Official transcripts from previous schools
- Government-issued ID verification
- Formal application process
- Standard admission timeline
- Professional staff

**Why they "don't meet criteria":** The 6-criteria system looks for schools that are **exceptionally accessible** — schools that remove barriers to entry.

### Example: Traditional vs. Accessible

**Traditional Community College:**
```
- Requires high school diploma/GED
- Needs official transcripts
- 3-6 month admission process
- Specific enrollment periods (Fall/Spring)
- Regular refund policies (usually semester-based)

Score: 0/6 criteria (meets none of the "extra accessible" standards)
```

**Accessible Community College (hypothetical):**
```
- Community college = YES (1 point)
- No ID verification = YES (2 points)
- No transcript required = YES (3 points)
- Monthly enrollment = YES (4 points)
- Instant acceptance = YES (5 points)
- Monthly refund = YES (6 points)

Score: 6/6 criteria (meets ALL accessibility standards)
```

---

## The 6-Criteria System Explained

### **1. Community College** ✓ (169/479 schools)

**What It Means:**
- Explicitly a "community college" or "two-year college"
- Offers associate degrees
- Designed for open access, not selective

**Evidence Required:**
- Page explicitly states "community college"
- OR URL/title contains "community college"
- OR mentions "associate degree" program

**Why It Matters:**
- Community colleges historically have lower barriers
- More likely to accept varied academic backgrounds

**Examples:**
- Community College of Denver ✓
- Riverside City College ✓
- Harvard University ✗ (university, not CC)

---

### **2. No ID Verification** ✓ (59/479 schools)

**What It Means:**
- School explicitly states you DON'T need government-issued ID to enroll
- No requirement for Social Security Number verification
- Accepts alternative identity documentation

**Evidence Required:**
- Page explicitly states: "no ID required", "without government ID", "accepts alternative identification"
- Must be explicit — absence of mention = NO

**Why It Matters:**
- Removes barrier for undocumented students
- Removes barrier for people without ID
- Indicates non-traditional enrollment path

**Examples:**
- "Enrollment without government-issued ID" ✓
- "We accept passport or national ID" — doesn't count ✗ (still requires government ID)
- School doesn't mention ID requirements ✗ (doesn't count as NO)

---

### **3. No Transcript Required** ✓ (428/479 schools)

**What It Means:**
- School explicitly states you DON'T need prior academic transcripts
- No requirement to provide high school records
- No requirement to provide college transcripts from other schools

**Evidence Required:**
- Page explicitly states: "no transcripts required", "no prior academic records needed", "open enrollment"
- Must be explicit — assumption doesn't count

**Why It Matters:**
- Biggest barrier removal for non-traditional students
- Allows people to enroll without academic history
- Indicates adult/non-traditional focus

**Examples:**
- "No transcripts or prior education required" ✓
- "Open enrollment — anyone can start" ✓
- "We don't require transcripts" ✓
- School has no admission requirements section ✗ (doesn't count as NO)

**Current Status:** This is the MOST common criteria met (428 schools), suggesting many schools have adopted open enrollment.

---

### **4. Monthly Enrollment** ✓ (379/479 schools)

**What It Means:**
- School accepts students ANY month of the year
- Rolling start dates (not just Fall/Spring semesters)
- Can begin courses at flexible times
- "Open enrollment" throughout the year

**Evidence Required:**
- Page explicitly states: "rolling admissions", "monthly start dates", "start any time", "begin whenever"
- OR mentions specific months: "Start in January, February, March..."

**Why It Matters:**
- Traditional schools lock enrollment to specific semesters
- Monthly enrollment = immediate access, no waiting
- Allows people to start immediately, not wait months

**Examples:**
- "Rolling enrollment — start any month" ✓
- "New cohorts start monthly" ✓
- "Classes begin the first of each month" ✓
- "Fall and Spring semesters only" ✗

**Current Status:** 379 schools (79%) support monthly enrollment — this is increasingly common.

---

### **5. Instant Acceptance** ✓ (3/479 schools)

**What It Means:**
- School immediately accepts applications
- Same-day or instant enrollment decision
- No waiting period, no review process
- Instant gratification — apply and start

**Evidence Required:**
- Page explicitly states: "instant acceptance", "same-day approval", "accept immediately", "begin today"
- Must be explicit

**Why It Matters:**
- Removes decision anxiety
- Allows immediate access
- Indicates highly automated/non-selective process

**Examples:**
- "Accepted instantly upon application" ✓
- "Same-day enrollment" ✓
- "Traditional 4-6 week decision timeline" ✗

**Current Status:** VERY RARE (only 3 schools) — most schools need time to process, even if brief.

---

### **6. Monthly Refund** ✓ (1/479 schools)

**What It Means:**
- School refunds tuition on monthly basis
- Pro-rated by the month (not semester or year)
- Can drop out and get prorated refund
- Financial flexibility for student dropouts

**Evidence Required:**
- Page explicitly states: "monthly refund", "pro-rated refund", "refund by month"
- Must specify monthly intervals

**Why It Matters:**
- Reduces financial risk for students
- Allows people to exit with money-back option
- Indicates flexible financial terms

**Examples:**
- "Monthly pro-rated refund upon withdrawal" ✓
- "Refund policy calculated monthly" ✓
- "Semester-based refund schedule" ✗
- "Refund policy available upon request" ✗ (not explicit)

**Current Status:** EXTREMELY RARE (only 1 school) — most schools use semester or term-based refunds.

---

## Current Distribution (479 schools on VPS)

### By Number of Criteria Met

```
4/6: 3 schools     (excellent accessibility)
3/6: 71 schools    (good accessibility)
2/6: 129 schools   (moderate accessibility)
1/6: 38 schools    (limited accessibility)
0/6: 238 schools   (traditional schools, no special accessibility)
```

### By Individual Criteria

```
No Transcript Required:  428/479 (89.4%) ← MOST COMMON
Monthly Enrollment:      379/479 (79.1%)
Community College:       169/479 (35.3%)
No ID Verification:       59/479 (12.3%)
Instant Acceptance:        3/479 (0.6%)  ← EXTREMELY RARE
Monthly Refund:            1/479 (0.2%)  ← UNIQUE (only 1 school)
```

---

## US Scorecard API Integration

### The API You Have Access To

**API:** US Department of Education College Scorecard API  
**Key:** `SCORECARD_API_KEY=XhIoK9TkwMek34tfJDWdux6aUDtgu3yFUS4UuizN`  
**URL:** https://api.data.gov/ed/collegescorecard/v1/schools  
**Coverage:** ALL accredited US educational institutions (~7,000+ schools)

### What Data It Provides

```
Per Institution:
- name, location (city, state, zip)
- type (university, college, community college)
- SAT/ACT requirements
- Acceptance rate
- Federal loan rates (Title IV eligibility)
- Tuition & fees
- Financial aid averages
- Graduation rates
- Employment data
- ... and 100+ more data points
```

### How to Access ALL Schools + Score Them

**Current System (Correct Approach):**

```
1. Use Scorecard API to GET all US schools
   - query: ?school.state=XX (by state)
   - returns: name, URL, type, loan rates
   
2. Store in raw_schools (staging)
   
3. For each school:
   - Fetch official website
   - Scan for 6 criteria evidence
   - Score (0-6)
   - Store in schools table
   
4. Present schools by score:
   - >= 3: Show to users (good accessibility)
   - < 3: Archive/research only
```

---

## Strategy to Include ALL US Schools

### Current Gap

```
You have: 479 schools (manually curated/discovered)
You could have: 7,000+ schools (all accredited US institutions)
```

### Implementation Plan

**Step 1: Scorecard API Integration**
```python
# Query Scorecard API for all accredited schools
GET https://api.data.gov/ed/collegescorecard/v1/schools?school.type__code=1,2,3&api_key=...
# Returns all universities, colleges, community colleges
```

**Step 2: Batch Load into raw_schools**
```sql
INSERT INTO raw_schools (name, url, scorecard_id, source)
SELECT name, homepage_url, school_id, 'scorecard' 
FROM scorecard_api_response
WHERE accreditation_status = 'Accredited'
```

**Step 3: Score for Accessibility**
```
For each school in raw_schools:
  - Fetch homepage
  - Run through 6-criteria classifier
  - Score (0-6)
  - Store results
```

**Step 4: Present All + Accessibility Metadata**
```
Users see ALL schools but sorted by accessibility:
- Tier 1: 3-4/6 criteria (most accessible)
- Tier 2: 2/6 criteria (moderate accessibility)
- Tier 3: 1/6 criteria (traditional + 1 feature)
- Tier 4: 0/6 criteria (purely traditional)
```

---

## Why This Matters

### Current System Misses

```
You're showing: ~479 schools (mostly discovered via scraping)
Reality: 7,000+ accredited US institutions exist

Missing:
- Regional obscure schools
- New schools
- Rural schools
- Historical schools with no web presence
- Small private schools
- Specialty technical schools
```

### Solution: Scorecard API First

```
1. Start with Scorecard API (complete list)
2. Supplement with web scraping (enrichment)
3. Score ALL for accessibility (6-criteria)
4. Present with transparency: "Traditional school + 1 accessibility feature"
```

---

## Recommendation

**To include ALL US schools:**

1. ✓ You have the Scorecard API key
2. ✓ You have the 6-criteria scoring system
3. ✓ You have storage (raw_schools table)

**Next Steps:**
1. Run Scorecard API query for all ~7,000 accredited US schools
2. Store in raw_schools (source='scorecard')
3. Score each for accessibility (0-6 criteria)
4. Present ALL schools to users with accessibility tiers
5. Use as base + add discovered schools on top

**Benefit:** Users see ALL options, understand accessibility tier, can choose even "traditional" schools if they want.

---

## Current vs. Proposed

### Current Setup
```
479 schools (discovered)
├─ 74 with score >= 3 (shown to users)
└─ 405 with score < 3 (archived)
```

### Proposed Setup
```
7,000+ schools (all US accredited via Scorecard API)
├─ Tier 1: Score 4+ (most accessible, ~100 schools)
├─ Tier 2: Score 3 (good accessibility, ~200 schools)
├─ Tier 3: Score 2 (moderate, ~1,000 schools)
├─ Tier 4: Score 1 (limited, ~2,000 schools)
└─ Tier 5: Score 0 (traditional, ~3,700 schools)
```

**Users get complete transparency:** Choose by accessibility level, not guessing.

---

## Summary

- **Traditional schools** = Accredited institutions with standard prerequisites (requires transcripts, ID, formal admission)
- **6-criteria scoring** = Measures HOW accessible each school is (0-6 scale)
- **You have access** = Scorecard API + 7,000+ schools available
- **Current gap** = Only 479 schools scored vs. 7,000+ possible
- **Solution** = Load all schools via API, score all for accessibility, present with tiers

**Next action:** Integrate Scorecard API to load all ~7,000 US accredited schools and score for accessibility.
