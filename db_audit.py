import sys, os
sys.path.insert(0, '.')
os.environ['POSTGRES_DSN'] = 'postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge'

import psycopg2, psycopg2.extras

def _conn():
    c = psycopg2.connect(os.environ['POSTGRES_DSN'])
    c.autocommit = True
    return c

print("="*80)
print("DATABASE AUDIT: Understanding Backlog & Enrichment Needs")
print("="*80)

with _conn() as conn:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ========================================================================
    print("\n[SECTION 1: RAW DISCOVERIES ANALYSIS]")
    print("-" * 80)

    print("\nRaw Discoveries Status Breakdown:")
    cur.execute("""
        SELECT
            SUM(CASE WHEN processed=FALSE AND blocked=FALSE THEN 1 ELSE 0 END) as unprocessed,
            SUM(CASE WHEN processed=TRUE THEN 1 ELSE 0 END) as processed,
            SUM(CASE WHEN blocked=TRUE THEN 1 ELSE 0 END) as blocked,
            COUNT(*) as total
        FROM raw_discoveries
    """)
    row = cur.fetchone()
    print(f"  Total raw_discoveries:       {row['total']:>6}")
    print(f"    - Unprocessed (queue):     {row['unprocessed']:>6}  (needs enrichment)")
    print(f"    - Processed (done):        {row['processed']:>6}  (enriched)")
    print(f"    - Blocked (skip):          {row['blocked']:>6}  (already filtered)")

    print("\nRaw Discoveries by Source:")
    cur.execute("""
        SELECT source,
               COUNT(*) as total,
               SUM(CASE WHEN processed=FALSE THEN 1 ELSE 0 END) as unproc
        FROM raw_discoveries
        GROUP BY source
        ORDER BY total DESC
    """)
    for rec in cur.fetchall():
        pct = (rec['unproc'] / rec['total'] * 100) if rec['total'] > 0 else 0
        print(f"  {rec['source']:<20} {rec['total']:>6} total | {rec['unproc']:>6} unproc ({pct:.0f}%)")

    print("\nSample Unprocessed (what needs enrichment):")
    cur.execute("""
        SELECT id, source, title, company, url
        FROM raw_discoveries
        WHERE processed=FALSE AND blocked=FALSE
        ORDER BY discovered_at DESC
        LIMIT 3
    """)
    for i, rec in enumerate(cur.fetchall(), 1):
        t = rec['title'][:30] if rec['title'] else 'NULL'
        c = rec['company'] or 'NULL'
        print(f"  {i}. [{rec['id']:5}] {rec['source']:<12} title={t:30} company={c}")

    # ========================================================================
    print("\n[SECTION 2: JOBS TABLE ANALYSIS]")
    print("-" * 80)

    print("\nJobs by Status:")
    cur.execute("""
        SELECT status, COUNT(*) as cnt
        FROM jobs
        GROUP BY status
        ORDER BY cnt DESC
    """)
    for rec in cur.fetchall():
        print(f"  {rec['status']:<20} {rec['cnt']:>6}")

    print("\nJobs by Source (top 10):")
    cur.execute("""
        SELECT source, COUNT(*) as cnt
        FROM jobs
        GROUP BY source
        ORDER BY cnt DESC
        LIMIT 10
    """)
    for rec in cur.fetchall():
        print(f"  {rec['source']:<30} {rec['cnt']:>6}")

    print("\nURL Overlap: raw_discoveries vs jobs")
    cur.execute("""
        SELECT
            COUNT(DISTINCT rd.url) as raw_urls,
            COUNT(DISTINCT j.url) as job_urls,
            COUNT(DISTINCT rd.url) FILTER (WHERE j.url IS NOT NULL) as overlapping
        FROM raw_discoveries rd
        LEFT JOIN jobs j ON rd.url = j.url
    """)
    rec = cur.fetchone()
    overlap_pct = (rec['overlapping'] / rec['raw_urls'] * 100) if rec['raw_urls'] > 0 else 0
    gap = rec['raw_urls'] - rec['overlapping']
    print(f"  Raw discovery URLs:   {rec['raw_urls']:>6}")
    print(f"  Job URLs:             {rec['job_urls']:>6}")
    print(f"  Overlapping:          {rec['overlapping']:>6}  ({overlap_pct:.1f}%)")
    print(f"  Gap:                  {gap:>6}  (discoveries NOT yet as jobs)")

    # ========================================================================
    print("\n[SECTION 3: ENRICHMENT COMPLETENESS]")
    print("-" * 80)

    print("\nJob Enrichment Status:")
    cur.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN title IS NOT NULL THEN 1 ELSE 0 END) as has_title,
            SUM(CASE WHEN company IS NOT NULL THEN 1 ELSE 0 END) as has_company,
            SUM(CASE WHEN description IS NOT NULL THEN 1 ELSE 0 END) as has_desc,
            SUM(CASE WHEN score > 0 THEN 1 ELSE 0 END) as has_score
        FROM jobs
    """)
    rec = cur.fetchone()
    title_pct = (rec['has_title'] / rec['total'] * 100) if rec['total'] > 0 else 0
    company_pct = (rec['has_company'] / rec['total'] * 100) if rec['total'] > 0 else 0
    desc_pct = (rec['has_desc'] / rec['total'] * 100) if rec['total'] > 0 else 0
    score_pct = (rec['has_score'] / rec['total'] * 100) if rec['total'] > 0 else 0
    print(f"  Total jobs:                 {rec['total']:>6}")
    print(f"    - With title:             {rec['has_title']:>6}  ({title_pct:.1f}%)")
    print(f"    - With company:           {rec['has_company']:>6}  ({company_pct:.1f}%)")
    print(f"    - With description:       {rec['has_desc']:>6}  ({desc_pct:.1f}%)")
    print(f"    - With score:             {rec['has_score']:>6}  ({score_pct:.1f}%)")

    print("\nRaw Discovery Enrichment Status:")
    cur.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN title IS NOT NULL THEN 1 ELSE 0 END) as has_title,
            SUM(CASE WHEN company IS NOT NULL THEN 1 ELSE 0 END) as has_company,
            SUM(CASE WHEN raw_content IS NOT NULL THEN 1 ELSE 0 END) as has_content
        FROM raw_discoveries
    """)
    rec = cur.fetchone()
    print(f"  Total raw discoveries:      {rec['total']:>6}")
    print(f"    - With title:             {rec['has_title']:>6}  ({rec['has_title']/rec['total']*100:.1f}%)")
    print(f"    - With company:           {rec['has_company']:>6}  ({rec['has_company']/rec['total']*100:.1f}%)")
    print(f"    - With raw_content:       {rec['has_content']:>6}  ({rec['has_content']/rec['total']*100:.1f}%)")

    # ========================================================================
    print("\n[SECTION 4: CLEANUP TASKS (WHAT CLAUDE NEEDS TO DO)]")
    print("-" * 80)

    # Get the exact gaps
    cur.execute("""
        SELECT COUNT(DISTINCT rd.id) as raw_not_in_jobs
        FROM raw_discoveries rd
        LEFT JOIN jobs j ON rd.url = j.url
        WHERE j.id IS NULL AND rd.processed=TRUE
    """)
    processed_not_jobs = cur.fetchone()['raw_not_in_jobs']

    cur.execute("""
        SELECT COUNT(*) as unproc
        FROM raw_discoveries
        WHERE processed=FALSE AND blocked=FALSE
    """)
    unproc = cur.fetchone()['unproc']

    cur.execute("""
        SELECT COUNT(*) as incomplete
        FROM jobs
        WHERE description IS NULL OR company IS NULL OR title IS NULL
    """)
    incomplete = cur.fetchone()['incomplete']

    print(f"\nTASK 1: Process Unprocessed Queue ({unproc} items)")
    print(f"  Status: Raw discoveries awaiting enrichment")
    print(f"  Action Needed: PIPELINE ENRICHMENT")
    print(f"  Claude should:")
    print(f"    a) get_raw_discoveries(limit=25)")
    print(f"    b) For each discovery:")
    print(f"       - Extract/classify: title, company, job_type")
    print(f"       - Score against user profile")
    print(f"       - Fetch full page content if needed")
    print(f"    c) upsert_job(url, title, company, ...)")
    print(f"    d) mark_discovery_processed(id, job_id)")
    print(f"  Batch Size: Process in batches of 25")
    print(f"  Est. Time: {unproc/25 * 2} mins at 2min/batch")

    print(f"\nTASK 2: Retroactively Create Jobs from Processed Discoveries ({processed_not_jobs} items)")
    print(f"  Status: Discoveries marked processed but no job record created")
    print(f"  Action Needed: BACKFILL JOBS")
    print(f"  Claude should:")
    print(f"    a) Query: raw_discoveries WHERE processed=TRUE AND url NOT IN (SELECT url FROM jobs)")
    print(f"    b) For each matched discovery:")
    print(f"       - Extract title, company, source from raw_discovery")
    print(f"       - Create job record")
    print(f"    c) Note: These are already 'processed' so just create jobs, no enrichment needed")
    print(f"  Batch Size: Can do all at once (bulk insert)")
    print(f"  Est. Time: < 1 min")

    print(f"\nTASK 3: Backfill Missing Enrichment ({incomplete} jobs)")
    print(f"  Status: Existing jobs missing title/company/description")
    print(f"  Action Needed: ENRICHMENT BACKFILL")
    print(f"  Claude should:")
    print(f"    a) Query: jobs WHERE description IS NULL OR company IS NULL OR title IS NULL")
    print(f"    b) For each incomplete job:")
    print(f"       - Check raw_discoveries for matching URL")
    print(f"       - Extract missing fields from raw_content/title/company")
    print(f"       - If still incomplete, fetch page content")
    print(f"       - update_job(id, title, company, description)")
    print(f"  Batch Size: Process in batches of 50")
    print(f"  Est. Time: {incomplete/50 * 3} mins at 3min/batch")

    print(f"\nRESUMMARY OF CLEANUP WORK:")
    print(f"  Total items to process:  {unproc + processed_not_jobs + incomplete}")
    print(f"    - Unprocessed queue:         {unproc}")
    print(f"    - Processed but no jobs:     {processed_not_jobs}")
    print(f"    - Jobs needing enrichment:   {incomplete}")
    print(f"  Recommended Order:")
    print(f"    1. Task 2 (fastest, no enrichment needed)")
    print(f"    2. Task 1 (main pipeline work)")
    print(f"    3. Task 3 (backfill any remaining gaps)")

print("\n" + "="*80)
print("END AUDIT")
print("="*80 + "\n")
