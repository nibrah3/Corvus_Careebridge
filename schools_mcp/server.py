"""
schools_mcp — School discovery MCP server.
Port: 8714

Tools:
  discover_schools(state)               — run full pipeline (parallel, HTML output)
  get_discovery_status(job_id)          — poll job progress
  generate_html_report()                — regenerate HTML from existing DB records
  send_school_reports(filters, min_score, limit)  — send PDFs to Telegram
  list_confirmed_schools(filters, min_score, limit)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path).read().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            if _k.strip() not in os.environ:
                os.environ[_k.strip()] = _v.strip()

from _minmcp import MinMCP
from schools_mcp._gov_api  import fetch_candidates
from schools_mcp._crawler  import crawl_school
from schools_mcp._analyzer import analyze, CRITERIA, CRITERIA_LABELS
from schools_mcp._html     import generate as generate_html
from schools_mcp._pdf      import generate as generate_pdf
from schools_mcp._notify   import send_school_pdf, notify_text
from schools_mcp._db       import ensure_table, save, was_recently_processed, url_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("schools_mcp")

mcp = MinMCP("schools")

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

CONCURRENCY       = 10    # parallel crawl+analyze workers
PROGRESS_INTERVAL = 900   # 15 minutes between Telegram progress updates
REPORTS_DIR       = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop", "schools")

# ── Gate ─────────────────────────────────────────────────────────────────────
# School must pass at least ONE of the 6 criteria to be included.
# "online" is the API baseline (school.online_only / open_admissions_policy).
MIN_CRITERIA_SCORE = 1


# ── Worker ────────────────────────────────────────────────────────────────────

def _process_one(school: dict) -> dict:
    """Crawl + analyze one school. Returns enriched dict (always, even on error)."""
    try:
        crawled  = crawl_school(school)
        analyzed = analyze(crawled)
        scorecard_id = school.get("scorecard_id")
        analyzed["source_url"] = (
            f"https://collegescorecard.ed.gov/school/?{scorecard_id}"
            if scorecard_id else "college_scorecard_api"
        )
        return analyzed
    except Exception as e:
        log.warning("process_one failed for %s: %s", school.get("name", "?")[:40], e)
        school["criteria_score"] = 0
        school["filters"] = []
        return school


# ── HTML from DB ──────────────────────────────────────────────────────────────

def _build_html_from_db() -> str:
    """Pull all qualifying schools from DB and write HTML report. Returns file path."""
    try:
        import psycopg2
        import psycopg2.extras
        from schools_mcp._db import _DSN

        with psycopg2.connect(_DSN, connect_timeout=10) as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT name, url, enrollment_url, city, state,
                           community_college, no_id_verification, no_transcript_required,
                           monthly_enrollment, instant_acceptance, monthly_refund,
                           filters, criteria_score
                    FROM schools
                    WHERE criteria_score >= %s
                    ORDER BY criteria_score DESC, name
                """, (MIN_CRITERIA_SCORE,))
                rows = [dict(r) for r in cur.fetchall()]

        ts  = time.strftime("%Y%m%d_%H%M")
        out = os.path.join(REPORTS_DIR, f"schools_{ts}.html")
        path = generate_html(rows, out)
        log.info("HTML report written: %s (%d schools)", path, len(rows))
        return path
    except Exception as e:
        log.error("HTML build failed: %s", e)
        return ""


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _run_pipeline(job_id: str, state: str) -> None:
    def _upd(**kw):
        with _jobs_lock:
            _jobs[job_id].update(kw)

    try:
        notify_text(
            f"🔍 *School discovery started* (job `{job_id}`)\n"
            f"Baseline: online schools from College Scorecard\n"
            f"Gate: ≥1 criterion required · {CONCURRENCY} parallel workers\n"
            f"Progress updates every 15 minutes via Telegram."
        )

        ensure_table()

        # ── Round 1: fetch candidates ─────────────────────────────────────────
        _upd(phase="scorecard_api")
        log.info("[%s] Fetching candidates from College Scorecard…", job_id)
        all_candidates = fetch_candidates()
        log.info("[%s] %d candidates from Scorecard", job_id, len(all_candidates))
        _upd(candidates=len(all_candidates))

        if not all_candidates:
            _upd(status="done", error="No candidates — check SCORECARD_API_KEY")
            notify_text(f"⚠️ Job `{job_id}`: No candidates from Scorecard. Check API key.")
            return

        # ── Bulk dedup (one query instead of N) ──────────────────────────────
        _upd(phase="dedup")
        import psycopg2 as _pg
        from schools_mcp._db import _DSN

        # Build hash map for all candidates
        hmap: dict[str, dict] = {}
        for school in all_candidates:
            if state and school.get("state", "").upper() != state.upper():
                continue
            h = url_hash(school["url"])
            if h not in hmap:          # session dedup
                hmap[h] = school

        all_hashes = list(hmap.keys())
        try:
            with _pg.connect(_DSN, connect_timeout=10) as _c:
                with _c.cursor() as _cur:
                    _cur.execute(
                        "SELECT url_hash FROM schools WHERE url_hash = ANY(%s)",
                        (all_hashes,),
                    )
                    in_db: set[str] = {r[0] for r in _cur.fetchall()}
        except Exception as _e:
            log.warning("Bulk dedup query failed (%s) — processing all candidates", _e)
            in_db = set()

        to_process  = [hmap[h] for h in all_hashes if h not in in_db]
        skipped_db  = len(all_hashes) - len(to_process)
        skipped_session = len(all_candidates) - len(all_hashes)

        log.info("[%s] %d to process (%d already in DB, %d session dups)",
                 job_id, len(to_process), skipped_db, skipped_session)
        _upd(phase="crawling", total=len(to_process), skipped_db=skipped_db)

        notify_text(
            f"📋 Job `{job_id}`: {len(all_candidates)} total candidates\n"
            f"Already in DB: {skipped_db} · New to process: {len(to_process)}"
        )

        if not to_process:
            notify_text(f"✅ Job `{job_id}`: All candidates already processed. Generating HTML…")
        else:
            # ── Round 2: parallel crawl + analyze ─────────────────────────────
            stored = gated_out = errors = 0
            processed = 0
            last_report_at = time.monotonic()

            with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                futures = {pool.submit(_process_one, s): s for s in to_process}

                for future in as_completed(futures):
                    processed += 1
                    _upd(processed=processed)

                    try:
                        result = future.result()
                    except Exception as e:
                        log.warning("[%s] future error: %s", job_id, e)
                        errors += 1
                        continue

                    score = result.get("criteria_score", 0)
                    name  = result.get("name", "?")[:40]

                    if score >= MIN_CRITERIA_SCORE:
                        if save(result):
                            stored += 1
                            _upd(stored=stored)
                            log.info("[%s] [%d/%d] ✓ %s — %d/6 — %s",
                                     job_id, processed, len(to_process), name,
                                     score, result.get("filters") or [])
                        else:
                            log.warning("[%s] save failed: %s", job_id, name)
                    else:
                        gated_out += 1
                        log.debug("[%s] [%d/%d] ✗ %s — score 0, excluded",
                                  job_id, processed, len(to_process), name)

                    # 15-minute progress report
                    if time.monotonic() - last_report_at >= PROGRESS_INTERVAL:
                        last_report_at = time.monotonic()
                        pct = int(processed / len(to_process) * 100) if to_process else 100
                        notify_text(
                            f"⏱ *Progress update* — job `{job_id}`\n"
                            f"Processed: {processed}/{len(to_process)} ({pct}%)\n"
                            f"Saved (≥1 criterion): {stored}\n"
                            f"Excluded (score 0): {gated_out}\n"
                            f"Errors: {errors}"
                        )

            _upd(stored=stored, gated_out=gated_out, errors=errors)
            notify_text(
                f"✅ *Crawl complete* — job `{job_id}`\n"
                f"Processed: {processed} · Saved: {stored} · "
                f"Excluded (score 0): {gated_out} · Errors: {errors}\n"
                f"Generating HTML report…"
            )

        # ── Round 3: build HTML from all qualifying records in DB ─────────────
        _upd(phase="html")
        html_path = _build_html_from_db()

        if html_path:
            size_kb = os.path.getsize(html_path) // 1024
            notify_text(
                f"📄 *HTML report ready* — job `{job_id}`\n"
                f"Path: `{html_path}`\n"
                f"Size: {size_kb} KB\n"
                f"Open in any browser — filterable by criterion."
            )
            _upd(html_path=html_path)
        else:
            notify_text(f"⚠️ Job `{job_id}`: HTML generation failed — check logs.")

        _upd(status="done")
        log.info("[%s] Pipeline complete.", job_id)

    except Exception as e:
        log.exception("[%s] Pipeline crashed: %s", job_id, e)
        _upd(status="error", error=str(e))
        notify_text(f"❌ Job `{job_id}` crashed: {str(e)[:300]}")


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def discover_schools(state: str = "") -> dict:
    """
    Run the full school discovery pipeline:
    1. Fetch all online-baseline candidates from College Scorecard API.
    2. Skip schools already in DB (resumable).
    3. Crawl + Claude-analyze remaining schools in parallel (10 workers).
    4. Save only schools that pass ≥1 criterion (gate).
    5. Generate an HTML report of all qualifying schools.

    Args:
        state: Optional two-letter US state filter (e.g. 'CA'). Empty = all states.
    """
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "status":     "running",
            "phase":      "starting",
            "candidates": 0,
            "total":      0,
            "processed":  0,
            "stored":     0,
            "skipped_db": 0,
            "gated_out":  0,
            "errors":     0,
            "state":      state,
            "html_path":  "",
        }

    threading.Thread(
        target=_run_pipeline,
        args=(job_id, state),
        daemon=True,
    ).start()

    return {
        "job_id":  job_id,
        "status":  "started",
        "message": (
            f"Pipeline {job_id} started. Baseline: online schools from College Scorecard. "
            f"Gate: ≥1 criterion. Workers: {CONCURRENCY}. "
            "Progress reported to Telegram every 15 minutes. "
            "HTML report generated at completion."
        ),
    }


@mcp.tool()
def get_discovery_status(job_id: str) -> dict:
    """Check status of a running or completed discovery job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"error": f"Job '{job_id}' not found."}
    return dict(job)


@mcp.tool()
def generate_html_report() -> dict:
    """
    Regenerate the HTML report from all qualifying schools currently in the DB.
    Use this to refresh the report without re-running discovery.
    """
    path = _build_html_from_db()
    if not path:
        return {"ok": False, "error": "HTML generation failed — check logs."}
    size_kb = os.path.getsize(path) // 1024
    notify_text(
        f"📄 *HTML report regenerated*\n"
        f"Path: `{path}`\n"
        f"Size: {size_kb} KB"
    )
    return {"ok": True, "path": path, "size_kb": size_kb}


@mcp.tool()
def send_school_reports(
    filters: list = None,
    min_score: int = 1,
    limit: int = 20,
) -> dict:
    """
    Query confirmed schools from DB, generate a PDF for each, send to Telegram.

    Args:
        filters:   Criteria keys to filter on. Empty = all qualifying schools.
        min_score: Minimum criteria score (1–6). Default 1.
        limit:     Max schools to send. Default 20.
    """
    try:
        import psycopg2
        import psycopg2.extras
        from schools_mcp._db import _DSN

        if filters:
            sql = """
                SELECT name, url, enrollment_url, type, evidence,
                       community_college, no_id_verification, no_transcript_required,
                       monthly_enrollment, instant_acceptance, monthly_refund,
                       filters, criteria_score, city, state
                FROM schools
                WHERE criteria_score >= %s AND filters && %s::text[]
                ORDER BY criteria_score DESC, name
                LIMIT %s
            """
            params = (min_score, filters, limit)
        else:
            sql = """
                SELECT name, url, enrollment_url, type, evidence,
                       community_college, no_id_verification, no_transcript_required,
                       monthly_enrollment, instant_acceptance, monthly_refund,
                       filters, criteria_score, city, state
                FROM schools
                WHERE criteria_score >= %s
                ORDER BY criteria_score DESC, name
                LIMIT %s
            """
            params = (min_score, limit)

        with psycopg2.connect(_DSN, connect_timeout=10) as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]

    except Exception as e:
        return {"error": f"DB query failed: {e}", "sent": 0}

    if not rows:
        notify_text("🔍 No schools found matching your filters. Run discover_schools first.")
        return {"sent": 0, "message": "No matching schools."}

    sent = failed = 0
    for school in rows:
        if not isinstance(school.get("evidence"), dict):
            school["evidence"] = {}
        try:
            pdf_bytes = generate_pdf(school)
            if send_school_pdf(school, pdf_bytes):
                sent += 1
            else:
                failed += 1
        except Exception as e:
            log.warning("PDF failed for %s: %s", school.get("name", "?")[:40], e)
            failed += 1
        time.sleep(0.2)

    notify_text(
        f"📚 *School reports sent*\n"
        f"Filters: {', '.join(filters) if filters else 'all'} · "
        f"Sent: {sent} · Failed: {failed} · Matched: {len(rows)}"
    )
    return {"sent": sent, "failed": failed, "matched": len(rows)}


@mcp.tool()
def list_confirmed_schools(
    filters: list = None,
    min_score: int = 1,
    limit: int = 50,
) -> dict:
    """List qualifying schools from DB without sending to Telegram."""
    try:
        import psycopg2
        import psycopg2.extras
        from schools_mcp._db import _DSN

        if filters:
            sql = """
                SELECT name, url, enrollment_url, type, filters, criteria_score, city, state, created_at
                FROM schools WHERE criteria_score >= %s AND filters && %s::text[]
                ORDER BY criteria_score DESC, name LIMIT %s
            """
            params = (min_score, filters, limit)
        else:
            sql = """
                SELECT name, url, enrollment_url, type, filters, criteria_score, city, state, created_at
                FROM schools WHERE criteria_score >= %s
                ORDER BY criteria_score DESC, name LIMIT %s
            """
            params = (min_score, limit)

        with psycopg2.connect(_DSN, connect_timeout=10) as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]

        return {"count": len(rows), "schools": rows}
    except Exception as e:
        return {"error": str(e), "schools": []}


if __name__ == "__main__":
    mcp.run()
