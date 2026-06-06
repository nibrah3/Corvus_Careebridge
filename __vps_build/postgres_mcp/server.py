import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _minmcp import MinMCP

import psycopg2
import psycopg2.extras

DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgresql://corvus:corvus-local-password@localhost:5432/careerbridge"
)

mcp = MinMCP("postgres_mcp")


def _conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


@mcp.tool()
def list_jobs(status: str = "", limit: int = 20) -> dict:
    """List jobs, optionally filtered by status."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if status:
            cur.execute(
                "SELECT * FROM jobs WHERE status=%s ORDER BY discovered_at DESC LIMIT %s",
                (status, limit)
            )
        else:
            cur.execute(
                "SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT %s",
                (limit,)
            )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
    return {"jobs": rows, "count": len(rows)}


@mcp.tool()
def get_job(job_id: int) -> dict:
    """Get a full job record by ID."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM jobs WHERE id=%s", (job_id,))
        row = cur.fetchone()
        if not row:
            return {"error": f"Job {job_id} not found"}
        r = dict(row)
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return r


@mcp.tool()
def upsert_job(
    url: str,
    title: str = "",
    company: str = "",
    description: str = "",
    score: float = 0.0,
    source: str = "discovered",
    profile_id: str = ""
) -> dict:
    """Insert or update a job record. Returns the job ID."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO jobs (url, title, company, description, score, source, profile_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                title = EXCLUDED.title,
                company = EXCLUDED.company,
                description = EXCLUDED.description,
                score = EXCLUDED.score,
                source = EXCLUDED.source,
                profile_id = EXCLUDED.profile_id
            RETURNING id
            """,
            (url, title, company, description, score, source, profile_id or None)
        )
        job_id = cur.fetchone()[0]
    return {"job_id": job_id, "url": url}


@mcp.tool()
def update_job_status(job_id: int, status: str, result: str = "") -> dict:
    """Update the status (and optional result JSON) of a job."""
    with _conn() as conn:
        cur = conn.cursor()
        if result:
            cur.execute(
                "UPDATE jobs SET status=%s, result=%s WHERE id=%s",
                (status, result, job_id)
            )
        else:
            cur.execute("UPDATE jobs SET status=%s WHERE id=%s", (status, job_id))
        if cur.rowcount == 0:
            return {"error": f"Job {job_id} not found"}
    return {"job_id": job_id, "status": status}


@mcp.tool()
def list_profiles() -> dict:
    """List all profiles (id and name only)."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, email FROM profiles ORDER BY name")
        rows = [dict(r) for r in cur.fetchall()]
    return {"profiles": rows, "count": len(rows)}


@mcp.tool()
def get_profile(profile_id: str) -> dict:
    """Get a full profile record."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM profiles WHERE id=%s", (profile_id,))
        row = cur.fetchone()
        if not row:
            return {"error": f"Profile {profile_id} not found"}
        r = dict(row)
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return r


@mcp.tool()
def upsert_profile(
    id: str,
    name: str,
    email: str = "",
    phone: str = "",
    location: str = "",
    bio: str = "",
    skills: str = "",
    experience: str = "",
    education: str = "",
    big_five: str = "",
    response_bias: str = ""
) -> dict:
    """Insert or update a candidate profile."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO profiles
                (id, name, email, phone, location, bio, skills, experience,
                 education, big_five, response_bias, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name, email=EXCLUDED.email, phone=EXCLUDED.phone,
                location=EXCLUDED.location, bio=EXCLUDED.bio, skills=EXCLUDED.skills,
                experience=EXCLUDED.experience, education=EXCLUDED.education,
                big_five=EXCLUDED.big_five, response_bias=EXCLUDED.response_bias,
                updated_at=NOW()
            """,
            (id, name, email, phone, location, bio, skills, experience,
             education, big_five, response_bias)
        )
    return {"profile_id": id}


@mcp.tool()
def create_application(job_id: int, profile_id: str) -> dict:
    """Create a new application record."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO applications (job_id, profile_id) VALUES (%s, %s) RETURNING id",
            (job_id, profile_id)
        )
        app_id = cur.fetchone()[0]
    return {"application_id": app_id, "job_id": job_id, "profile_id": profile_id}


@mcp.tool()
def update_application(app_id: int, status: str, result: str = "") -> dict:
    """Update the status and optional JSON result of an application."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE applications SET status=%s, assessment_result=%s, updated_at=NOW() WHERE id=%s",
            (status, result or None, app_id)
        )
        if cur.rowcount == 0:
            return {"error": f"Application {app_id} not found"}
    return {"application_id": app_id, "status": status}


@mcp.tool()
def log_event(type: str, payload: str = "") -> dict:
    """Write a system event log entry."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO events (type, payload) VALUES (%s, %s) RETURNING id",
            (type, payload or None)
        )
        event_id = cur.fetchone()[0]
    return {"event_id": event_id, "type": type}


# ── Search terms ──────────────────────────────────────────────────────────────

def _ensure_search_terms() -> None:
    with _conn() as conn:
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS search_terms (
                id           SERIAL PRIMARY KEY,
                term         TEXT NOT NULL UNIQUE,
                category     TEXT,
                priority     TEXT DEFAULT 'normal',
                source       TEXT DEFAULT 'seed',
                active       BOOLEAN DEFAULT TRUE,
                last_used_at TIMESTAMPTZ,
                hit_count    INT DEFAULT 0,
                gig_count    INT DEFAULT 0,
                gig_rate     FLOAT DEFAULT 0.0,
                added_at     TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS search_terms_active_idx ON search_terms (active, priority);
        """)


@mcp.tool()
def upsert_search_term(term: str, category: str = "", priority: str = "normal",
                       source: str = "seed") -> dict:
    """Add or update a search term. Source: seed | gap_analysis | scout_generated."""
    _ensure_search_terms()
    with _conn() as conn:
        conn.cursor().execute("""
            INSERT INTO search_terms (term, category, priority, source)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (term) DO UPDATE SET
                category = COALESCE(NULLIF(EXCLUDED.category,''), search_terms.category),
                priority = EXCLUDED.priority,
                active   = TRUE
        """, (term, category or None, priority, source))
    return {"ok": True, "term": term}


@mcp.tool()
def get_active_search_terms(limit: int = 100) -> dict:
    """Return active search terms ordered by priority then gig_rate DESC."""
    _ensure_search_terms()
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT term, category, priority, gig_rate, hit_count
            FROM search_terms
            WHERE active = TRUE
            ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                     gig_rate DESC NULLS LAST
            LIMIT %s
        """, (limit,))
        return {"terms": [dict(r) for r in cur.fetchall()]}


@mcp.tool()
def update_term_performance(term: str, hits: int = 0, gig_hits: int = 0) -> dict:
    """
    Record discovery performance for a search term.
    Auto-deactivates terms with gig_rate < 0.05 after 20+ uses.
    Auto-promotes terms with gig_rate > 0.40 to high priority.
    """
    _ensure_search_terms()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE search_terms
            SET hit_count    = hit_count + %s,
                gig_count    = gig_count + %s,
                last_used_at = NOW(),
                gig_rate     = CASE WHEN (hit_count + %s) > 0
                                    THEN (gig_count + %s)::float / (hit_count + %s)
                                    ELSE 0 END
            WHERE term = %s
            RETURNING hit_count, gig_count, gig_rate
        """, (hits, gig_hits, hits, gig_hits, hits, term))
        row = cur.fetchone()
        if not row:
            return {"error": f"Term not found: {term}"}
        hit_count, gig_count, gig_rate = row
        # Auto-tune
        if hit_count >= 20 and gig_rate < 0.05:
            cur.execute("UPDATE search_terms SET active=FALSE WHERE term=%s", (term,))
            action = "deactivated (low gig_rate)"
        elif gig_rate > 0.40:
            cur.execute("UPDATE search_terms SET priority='high' WHERE term=%s", (term,))
            action = "promoted to high priority"
        else:
            action = "updated"
    return {"ok": True, "term": term, "hit_count": hit_count,
            "gig_rate": round(gig_rate, 3), "action": action}


# ── Raw discoveries staging ────────────────────────────────────────────────────

def _ensure_raw_discoveries() -> None:
    with _conn() as conn:
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS raw_discoveries (
                id           SERIAL PRIMARY KEY,
                url          TEXT NOT NULL UNIQUE,
                title        TEXT,
                company      TEXT,
                raw_content  TEXT,
                source       TEXT,
                source_query TEXT,
                processed    BOOLEAN DEFAULT FALSE,
                blocked      BOOLEAN DEFAULT FALSE,
                block_reason TEXT,
                discovered_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS raw_disc_unprocessed ON raw_discoveries (processed, blocked)
                WHERE processed = FALSE AND blocked = FALSE;
        """)


@mcp.tool()
def push_raw_discovery(url: str, title: str = "", company: str = "",
                       raw_content: str = "", source: str = "",
                       source_query: str = "") -> dict:
    """Push one raw discovery to the staging table. Skips duplicates silently."""
    _ensure_raw_discoveries()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO raw_discoveries (url, title, company, raw_content, source, source_query)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
        """, (url, title or None, company or None, raw_content or None,
              source or None, source_query or None))
        row = cur.fetchone()
    return {"ok": True, "new": bool(row)}


@mcp.tool()
def get_raw_discoveries(limit: int = 25, blocked: bool = False) -> dict:
    """Return unprocessed raw discoveries for Claude Code to enrich and classify."""
    _ensure_raw_discoveries()
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, url, title, company, raw_content, source, discovered_at
            FROM raw_discoveries
            WHERE processed = FALSE AND blocked = %s
            ORDER BY discovered_at ASC
            LIMIT %s
        """, (blocked, limit))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if hasattr(r.get('discovered_at'), 'isoformat'):
                r['discovered_at'] = r['discovered_at'].isoformat()
    return {"discoveries": rows, "count": len(rows)}


@mcp.tool()
def mark_discovery_processed(discovery_id: int, job_id: int = 0) -> dict:
    """Mark a raw discovery as processed after Claude Code has analyzed it."""
    _ensure_raw_discoveries()
    with _conn() as conn:
        conn.cursor().execute(
            "UPDATE raw_discoveries SET processed=TRUE WHERE id=%s", (discovery_id,))
    return {"ok": True, "id": discovery_id, "job_id": job_id or None}


# ── Raw schools staging ────────────────────────────────────────────────────────

def _ensure_raw_schools() -> None:
    with _conn() as conn:
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS raw_schools (
                id            SERIAL PRIMARY KEY,
                url           TEXT NOT NULL UNIQUE,
                name          TEXT,
                raw_content   TEXT,
                scorecard_id  TEXT,
                source        TEXT,
                federal_loan_rate FLOAT,
                pell_grant_rate   FLOAT,
                is_cc         BOOLEAN DEFAULT FALSE,
                online_only   BOOLEAN DEFAULT FALSE,
                state         TEXT,
                city          TEXT,
                processed     BOOLEAN DEFAULT FALSE,
                discovered_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS raw_schools_unproc ON raw_schools (processed)
                WHERE processed = FALSE;
        """)


@mcp.tool()
def push_raw_school(url: str, name: str = "", raw_content: str = "",
                    scorecard_id: str = "", source: str = "scorecard",
                    federal_loan_rate: float = 0.0, pell_grant_rate: float = 0.0,
                    is_cc: bool = False, online_only: bool = False,
                    state: str = "", city: str = "") -> dict:
    """Push a school to raw staging. Claude Code analyzes it later."""
    _ensure_raw_schools()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO raw_schools
                (url, name, raw_content, scorecard_id, source,
                 federal_loan_rate, pell_grant_rate, is_cc, online_only, state, city)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
        """, (url, name or None, raw_content or None, scorecard_id or None, source,
              federal_loan_rate, pell_grant_rate, is_cc, online_only,
              state or None, city or None))
        row = cur.fetchone()
    return {"ok": True, "new": bool(row)}


@mcp.tool()
def get_raw_schools(limit: int = 25) -> dict:
    """Return unprocessed raw schools for Claude Code to analyze."""
    _ensure_raw_schools()
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, url, name, raw_content, scorecard_id, source,
                   federal_loan_rate, pell_grant_rate, is_cc, online_only, state, city
            FROM raw_schools
            WHERE processed = FALSE
            ORDER BY federal_loan_rate DESC NULLS LAST, discovered_at ASC
            LIMIT %s
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    return {"schools": rows, "count": len(rows)}


@mcp.tool()
def mark_school_processed(school_id: int) -> dict:
    """Mark a raw school as processed after Claude Code has analyzed it."""
    _ensure_raw_schools()
    with _conn() as conn:
        conn.cursor().execute(
            "UPDATE raw_schools SET processed=TRUE WHERE id=%s", (school_id,))
    return {"ok": True, "id": school_id}


if __name__ == "__main__":
    mcp.run()
