"""
vps_mcp — Desktop bridge to VPS pipeline.
Runs on Desktop at port 8713.
Connects to VPS via SSH tunnels:
  Redis:    localhost:6380 -> VPS:6379
  Postgres: localhost:5433 -> VPS:5432
  Crawlee:  localhost:3101 -> VPS:3100
Start tunnels first: powershell E:\\Corvus_Careebridge\\scripts\\vps_tunnel.ps1
"""
import sys
import os
import json
import urllib.request
import urllib.error
import socket

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _minmcp import MinMCP

# Tunnel endpoints (SSH forward from Desktop)
REDIS_HOST  = os.environ.get("VPS_REDIS_HOST",     "127.0.0.1")
REDIS_PORT  = int(os.environ.get("VPS_REDIS_PORT", "6380"))
PG_DSN      = os.environ.get("VPS_PG_DSN",         "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
CRAWLEE     = os.environ.get("VPS_CRAWLEE_URL",     "http://127.0.0.1:3101")
FIRECRAWL   = os.environ.get("VPS_FIRECRAWL_URL",   "http://127.0.0.1:7788")

mcp = MinMCP("vps_mcp")


# ── Redis helpers ──────────────────────────────────────────────────────────────

def _redis_cmd(*parts: str) -> str:
    """Send a raw RESP command to Redis and return the reply as a string."""
    with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=5) as sock:
        cmd = f"*{len(parts)}\r\n" + "".join(f"${len(p)}\r\n{p}\r\n" for p in parts)
        sock.sendall(cmd.encode())
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if data.endswith(b"\r\n"):
                break
        return data.decode(errors="replace").strip()


def _redis_lrange(key: str, start: int = 0, end: int = -1) -> list[str]:
    try:
        reply = _redis_cmd("LRANGE", key, str(start), str(end))
        # Parse RESP array: *N\r\n$len\r\nval\r\n...
        lines = reply.split("\r\n")
        results = []
        i = 0
        if lines[i].startswith("*"):
            count = int(lines[i][1:])
            i += 1
            for _ in range(count):
                if lines[i].startswith("$"):
                    length = int(lines[i][1:])
                    i += 1
                    if length >= 0:
                        results.append(lines[i])
                    i += 1
        return results
    except Exception as e:
        return []


def _redis_rpush(key: str, value: str) -> bool:
    try:
        _redis_cmd("RPUSH", key, value)
        return True
    except Exception:
        return False


def _redis_lrem(key: str, value: str) -> bool:
    try:
        _redis_cmd("LREM", key, "1", value)
        return True
    except Exception:
        return False


def _redis_get(key: str) -> str | None:
    try:
        reply = _redis_cmd("GET", key)
        if reply.startswith("$-1"):
            return None
        lines = reply.split("\r\n")
        if lines[0].startswith("$"):
            return lines[1] if len(lines) > 1 else None
        return None
    except Exception:
        return None


def _redis_setex(key: str, seconds: int, value: str) -> bool:
    try:
        _redis_cmd("SETEX", key, str(seconds), value)
        return True
    except Exception:
        return False


def _redis_keys(pattern: str) -> list[str]:
    try:
        reply = _redis_cmd("KEYS", pattern)
        lines = reply.split("\r\n")
        results = []
        i = 0
        if not lines or not lines[i].startswith("*"):
            return results
        count = int(lines[i][1:])
        i += 1
        for _ in range(count):
            if i >= len(lines):
                break
            if lines[i].startswith("$"):
                i += 1
                if i < len(lines):
                    results.append(lines[i])
                i += 1
        return results
    except Exception:
        return []


# ── Postgres helpers ───────────────────────────────────────────────────────────

def _pg():
    import psycopg2
    import psycopg2.extras
    c = psycopg2.connect(PG_DSN)
    c.autocommit = True
    return c


def _ts(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row_to_dict(row) -> dict:
    return {k: _ts(v) for k, v in dict(row).items()}


# ── Job tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_pending_approvals(limit: int = 10) -> dict:
    """Get jobs waiting for Mike's approval from Redis pending queue."""
    items = _redis_lrange("corvus:pending_approvals", 0, limit - 1)
    parsed = []
    for item in items:
        try:
            parsed.append(json.loads(item))
        except Exception:
            parsed.append({"raw": item})
    return {"count": len(parsed), "jobs": parsed}


@mcp.tool()
def approve_job(job_id: int) -> dict:
    """Approve a job: mark postgres status='approved' and push to Redis corvus:approved_jobs."""
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute(
            "UPDATE jobs SET status='approved', approved_at=NOW() WHERE id=%s "
            "RETURNING id, url, title, company, profile_id",
            (job_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": f"Job {job_id} not found"}
        payload = json.dumps({
            "job_id":     job_id,
            "url":        row["url"],
            "title":      row.get("title") or "",
            "company":    row.get("company") or "",
            "profile_id": row.get("profile_id") or "",
        })
        _redis_rpush("corvus:approved_jobs", payload)
        # Remove from pending approvals if present
        pending = _redis_lrange("corvus:pending_approvals", 0, -1)
        for item in pending:
            try:
                if json.loads(item).get("job_id") == job_id:
                    _redis_lrem("corvus:pending_approvals", item)
            except Exception:
                pass
        return {"ok": True, "job_id": job_id, "status": "approved"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def skip_job(job_id: int) -> dict:
    """Skip a job: mark postgres status='skipped' and push to Redis corvus:skipped_jobs."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("UPDATE jobs SET status='skipped' WHERE id=%s", (job_id,))
        if cur.rowcount == 0:
            return {"error": f"Job {job_id} not found"}
        _redis_rpush("corvus:skipped_jobs", json.dumps({"job_id": job_id}))
        # Remove from pending approvals
        pending = _redis_lrange("corvus:pending_approvals", 0, -1)
        for item in pending:
            try:
                if json.loads(item).get("job_id") == job_id:
                    _redis_lrem("corvus:pending_approvals", item)
            except Exception:
                pass
        return {"ok": True, "job_id": job_id, "status": "skipped"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_jobs(status: str = "", limit: int = 20, fields: str = "") -> dict:
    """
    List jobs from VPS postgres, optionally filtered by status.
    fields: comma-separated column names to return (default: id,url,title,company,job_type,status).
    Pass fields='*' for all columns. Keeping fields minimal reduces token usage significantly.
    """
    default_fields = "id, url, title, company, job_type, status, discovered_at"
    select = "*" if fields == "*" else (fields.replace(",", ", ") if fields else default_fields)
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        if status:
            cur.execute(f"SELECT {select} FROM jobs WHERE status=%s ORDER BY discovered_at DESC LIMIT %s", (status, limit))
        else:
            cur.execute(f"SELECT {select} FROM jobs ORDER BY discovered_at DESC LIMIT %s", (limit,))
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        return {"jobs": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_job(job_id: int) -> dict:
    """Get full job record from VPS postgres."""
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute("SELECT * FROM jobs WHERE id=%s", (job_id,))
        row = cur.fetchone()
        if not row:
            return {"error": f"Job {job_id} not found"}
        return _row_to_dict(row)
    except Exception as e:
        return {"error": str(e)}


def _ensure_enrichment_columns() -> None:
    """Add enrichment columns to jobs table if they don't already exist."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE jobs
                ADD COLUMN IF NOT EXISTS official_url         TEXT,
                ADD COLUMN IF NOT EXISTS official_description TEXT,
                ADD COLUMN IF NOT EXISTS enriched             BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS quality_issue        TEXT,
                ADD COLUMN IF NOT EXISTS source_url           TEXT,
                ADD COLUMN IF NOT EXISTS job_type             TEXT
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS jobs_enriched_idx ON jobs (enriched) WHERE enriched IS FALSE"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS jobs_type_idx ON jobs (job_type)"
        )
    except Exception as e:
        pass  # columns may already exist


@mcp.tool()
def upsert_job(
    url: str,
    title: str = "",
    company: str = "",
    description: str = "",
    score: float = 0.0,
    source: str = "manual",
    profile_id: str = "",
    official_url: str = "",
    source_url: str = "",
    job_type: str = "",
) -> dict:
    """
    Insert or update a job in VPS postgres.

    url          — canonical employer career/ATS URL (NOT a platform/aggregator link)
    source_url   — discovery URL where the job was found (platform, board, blog)
    job_type     — category: ai_training, data_annotation, search_rating, transcription,
                   translation, content_writing, social_media, virtual_assistant,
                   customer_support, microtask, tutoring, testing, moderation, gpt, other_gig
    official_url — legacy alias for url (kept for backward compat with VPS discovery)
    """
    try:
        _ensure_enrichment_columns()
        conn = _pg()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO jobs (url, title, company, description, score, source, profile_id,
                              official_url, enriched, source_url, job_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (url) DO UPDATE SET
                title        = EXCLUDED.title,
                company      = EXCLUDED.company,
                description  = EXCLUDED.description,
                score        = EXCLUDED.score,
                source       = EXCLUDED.source,
                profile_id   = EXCLUDED.profile_id,
                official_url = COALESCE(NULLIF(EXCLUDED.official_url,''), jobs.official_url),
                source_url   = COALESCE(NULLIF(EXCLUDED.source_url,''),   jobs.source_url),
                job_type     = COALESCE(NULLIF(EXCLUDED.job_type,''),     jobs.job_type),
                enriched     = CASE WHEN EXCLUDED.official_url != '' THEN TRUE ELSE jobs.enriched END
            RETURNING id
            """,
            (url, title, company, description, score, source, profile_id or None,
             official_url or None, bool(official_url),
             source_url or None, job_type or None)
        )
        job_id = cur.fetchone()[0]
        return {"job_id": job_id, "url": url}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_unenriched_jobs(limit: int = 50) -> dict:
    """Return jobs that have not yet been gate-evaluated and need classification.

    Excludes: blocked, completed, skipped, failed, error, partial, applied
    — and also excludes jobs that already have a job_type set (classified).
    """
    try:
        _ensure_enrichment_columns()
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute(
            """
            SELECT id, url, title, company, source, discovered_at
            FROM jobs
            WHERE (enriched IS FALSE OR enriched IS NULL)
              AND job_type IS NULL
              AND status NOT IN ('blocked', 'skipped', 'completed',
                                 'failed', 'error', 'partial', 'applied')
            ORDER BY discovered_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        return {"jobs": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e), "jobs": []}


@mcp.tool()
def update_job_enrichment(
    job_id: int,
    official_url: str,
    official_description: str = "",
    quality_issue: str = "",
    source_url: str = "",
    job_type: str = "",
) -> dict:
    """
    Store the official employer URL, job type, and enriched description for a job.
    Set quality_issue to 'no_official_url' when extraction failed.
    source_url — the discovery platform URL where this job was originally found.
    job_type   — category from the job type taxonomy.
    """
    try:
        _ensure_enrichment_columns()
        conn = _pg()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE jobs
            SET official_url         = %s,
                official_description = %s,
                enriched             = TRUE,
                quality_issue        = NULLIF(%s, ''),
                source_url           = COALESCE(NULLIF(%s, ''), source_url),
                job_type             = COALESCE(NULLIF(%s, ''), job_type)
            WHERE id = %s
            """,
            (official_url or None, official_description or None, quality_issue,
             source_url or None, job_type or None, job_id),
        )
        if cur.rowcount == 0:
            return {"error": f"Job {job_id} not found"}
        return {"ok": True, "job_id": job_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_job_status(job_id: int, status: str, result: str = "") -> dict:
    """Update a job's status in VPS postgres (called after assessment completes)."""
    try:
        conn = _pg()
        cur = conn.cursor()
        if result:
            cur.execute("UPDATE jobs SET status=%s, result=%s WHERE id=%s", (status, result, job_id))
        else:
            cur.execute("UPDATE jobs SET status=%s WHERE id=%s", (status, job_id))
        if cur.rowcount == 0:
            return {"error": f"Job {job_id} not found"}
        return {"ok": True, "job_id": job_id, "status": status}
    except Exception as e:
        return {"error": str(e)}


# ── Profile tools ──────────────────────────────────────────────────────────────

@mcp.tool()
def list_profiles() -> dict:
    """List all candidate profiles stored on VPS."""
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute("SELECT id, name, email FROM profiles ORDER BY name")
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        return {"profiles": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_profile(profile_id: str) -> dict:
    """Get a full candidate profile from VPS postgres."""
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute("SELECT * FROM profiles WHERE id=%s", (profile_id,))
        row = cur.fetchone()
        if not row:
            return {"error": f"Profile {profile_id} not found"}
        return _row_to_dict(row)
    except Exception as e:
        return {"error": str(e)}


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
    response_bias: str = "",
    imap_password: str = "",
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993
) -> dict:
    """Create or update a candidate profile on VPS postgres (including IMAP credentials)."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO profiles
                (id, name, email, phone, location, bio, skills, experience,
                 education, big_five, response_bias,
                 imap_password, imap_server, imap_port, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name, email=EXCLUDED.email, phone=EXCLUDED.phone,
                location=EXCLUDED.location, bio=EXCLUDED.bio, skills=EXCLUDED.skills,
                experience=EXCLUDED.experience, education=EXCLUDED.education,
                big_five=EXCLUDED.big_five, response_bias=EXCLUDED.response_bias,
                imap_password=COALESCE(NULLIF(EXCLUDED.imap_password,''), profiles.imap_password),
                imap_server=COALESCE(NULLIF(EXCLUDED.imap_server,''), profiles.imap_server),
                imap_port=EXCLUDED.imap_port,
                updated_at=NOW()
            """,
            (id, name, email, phone, location, bio, skills, experience,
             education, big_five, response_bias, imap_password, imap_server, imap_port)
        )
        return {"ok": True, "profile_id": id}
    except Exception as e:
        return {"error": str(e)}


# ── Discovery tools ────────────────────────────────────────────────────────────

@mcp.tool()
def trigger_discovery(source: str = "all", keywords: str = "") -> dict:
    """Trigger a VPS discovery scrape via the Crawlee API (via SSH tunnel on port 3101)."""
    url = f"{CRAWLEE}/scrape/{source}"
    body = json.dumps({"keywords": keywords, "limit": 50}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e), "url": url}


@mcp.tool()
def get_system_status() -> dict:
    """Get a quick status snapshot: pending approvals count, approved jobs count, Redis ping."""
    status = {}
    try:
        pending = _redis_lrange("corvus:pending_approvals", 0, -1)
        approved = _redis_lrange("corvus:approved_jobs", 0, -1)
        status["pending_approvals"] = len(pending)
        status["approved_jobs"] = len(approved)
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"

    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("SELECT status, count(*) FROM jobs GROUP BY status")
        status["jobs_by_status"] = {row[0]: row[1] for row in cur.fetchall()}
        status["postgres"] = "ok"
    except Exception as e:
        status["postgres"] = f"error: {e}"

    return status


# ── Node tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def list_nodes() -> dict:
    """List all execution nodes that sent a heartbeat in the last 90 seconds."""
    import time
    keys = [k for k in _redis_keys("corvus:node:*") if k.count(":") == 2]
    nodes = []
    now = int(time.time())
    for key in keys:
        raw = _redis_get(key)
        if raw:
            try:
                info = json.loads(raw)
                info["seconds_ago"] = now - info.get("last_seen", now)
                nodes.append(info)
            except Exception:
                nodes.append({"node_id": key.split(":")[-1], "raw": raw})
    return {"nodes": nodes, "count": len(nodes)}


@mcp.tool()
def register_node(node_id: str, hostname: str, capabilities: str = "full", ttl: int = 90) -> dict:
    """Register or refresh a node heartbeat. Called by node_agent.py every 30 s."""
    import time
    payload = json.dumps({
        "node_id":      node_id,
        "hostname":     hostname,
        "capabilities": capabilities,
        "last_seen":    int(time.time()),
    })
    ok = _redis_setex(f"corvus:node:{node_id}", ttl, payload)
    return {"ok": ok, "node_id": node_id, "ttl": ttl}


@mcp.tool()
def dispatch_job_to_node(job_id: int, node_id: str) -> dict:
    """Approve a job and send it to a specific remote execution node's task queue."""
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute(
            "UPDATE jobs SET status='approved', approved_at=NOW() "
            "WHERE id=%s RETURNING id, url, title, company, profile_id",
            (job_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": f"Job {job_id} not found"}
        payload = json.dumps({
            "job_id":     job_id,
            "url":        row["url"],
            "title":      row.get("title") or "",
            "company":    row.get("company") or "",
            "profile_id": row.get("profile_id") or "",
            "node_id":    node_id,
        })
        queue_key = f"corvus:node:{node_id}:tasks"
        _redis_rpush(queue_key, payload)
        # Remove from pending approvals
        pending = _redis_lrange("corvus:pending_approvals", 0, -1)
        for item in pending:
            try:
                if json.loads(item).get("job_id") == job_id:
                    _redis_lrem("corvus:pending_approvals", item)
            except Exception:
                pass
        return {"ok": True, "job_id": job_id, "node_id": node_id, "queue": queue_key}
    except Exception as e:
        return {"error": str(e)}


# ── Raw Discovery tools (Claude Code gate pipeline) ──────────────────────────

@mcp.tool()
def get_raw_discoveries(limit: int = 50) -> dict:
    """
    Return unprocessed raw discoveries for Claude Code to gate.
    raw_content is capped at 500 chars — sufficient for keep/block decisions.
    For ambiguous items, call firecrawl_scrape(url) to get full content.
    """
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute(
            """
            SELECT id, url, title, company, source, source_query,
                   LEFT(raw_content, 500) AS raw_content, discovered_at
            FROM raw_discoveries
            WHERE processed = FALSE AND blocked = FALSE
            ORDER BY discovered_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        return {"discoveries": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e), "discoveries": []}


@mcp.tool()
def mark_raw_blocked(discovery_id: int, reason: str) -> dict:
    """Mark a raw discovery as blocked — not a gig job. Removes it from the gate queue."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute(
            "UPDATE raw_discoveries SET processed=TRUE, blocked=TRUE, block_reason=%s WHERE id=%s",
            (reason, discovery_id),
        )
        return {"ok": True, "id": discovery_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def mark_raw_processed(discovery_id: int) -> dict:
    """Mark a raw discovery as processed (kept as a job). Call after upsert_job()."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("UPDATE raw_discoveries SET processed=TRUE WHERE id=%s", (discovery_id,))
        return {"ok": True, "id": discovery_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_due_catalogue_companies(limit: int = 20) -> dict:
    """
    Return discovered_platforms companies whose check_interval has elapsed.
    Claude Code calls Firecrawl on each, reads for new listings, then
    calls upsert_job() for any new ones found and update_catalogue_tier() to adjust.
    """
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute(
            """
            SELECT id, company, careers_url, category, tier,
                   check_interval_hours, last_checked_at, last_found_jobs,
                   consecutive_empty, jobs_found_30d
            FROM discovered_platforms
            WHERE is_active = TRUE
              AND tier IS NOT NULL
              AND (
                last_checked_at IS NULL
                OR last_checked_at < NOW() - (check_interval_hours || ' hours')::INTERVAL
              )
            ORDER BY tier ASC, last_checked_at ASC NULLS FIRST
            LIMIT %s
            """,
            (limit,),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        return {"companies": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e), "companies": []}


@mcp.tool()
def update_catalogue_tier(
    platform_id: int,
    tier: int,
    reason: str = "",
    jobs_found: int = 0,
) -> dict:
    """
    Update a platform's monitoring tier after Claude Code polls it.
    tier 1 = check every 12h (high signal)
    tier 2 = check every 48h (moderate)
    tier 3 = check weekly (low signal)
    tier 0 = archive (no longer active)
    """
    try:
        conn = _pg()
        cur = conn.cursor()
        interval_map = {0: 8760, 1: 12, 2: 48, 3: 168}
        new_interval = interval_map.get(tier, 48)
        cur.execute(
            """
            UPDATE discovered_platforms
            SET tier = %s,
                tier_reason = %s,
                check_interval_hours = %s,
                last_found_jobs = %s,
                last_checked_at = NOW(),
                last_promoted_at = CASE WHEN tier != %s THEN NOW() ELSE last_promoted_at END
            WHERE id = %s
            """,
            (tier, reason, new_interval, jobs_found, tier, platform_id),
        )
        return {"ok": True, "platform_id": platform_id, "tier": tier, "interval_h": new_interval}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_gap_report() -> dict:
    """
    Return a gap analysis report for Claude Code's weekly strategy skill.
    Shows job_type distribution, top sources, empty categories, and
    what's underrepresented so Claude Code can direct the next discovery cycle.
    """
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)

        cur.execute("""
            SELECT job_type, COUNT(*) n
            FROM jobs WHERE job_type IS NOT NULL AND status != 'blocked'
            GROUP BY job_type ORDER BY n DESC
        """)
        by_type = {r["job_type"]: r["n"] for r in cur.fetchall()}

        cur.execute("""
            SELECT source, COUNT(*) n FROM jobs
            WHERE status = 'pending' AND job_type IS NOT NULL
            GROUP BY source ORDER BY n DESC LIMIT 15
        """)
        by_source = {r["source"]: r["n"] for r in cur.fetchall()}

        cur.execute("SELECT COUNT(*) n FROM jobs WHERE status='blocked'")
        blocked = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) n FROM raw_discoveries WHERE processed=FALSE AND blocked=FALSE")
        raw_pending = cur.fetchone()["n"]

        cur.execute("""
            SELECT tier, COUNT(*) n FROM discovered_platforms
            WHERE is_active=TRUE GROUP BY tier ORDER BY tier
        """)
        catalogue_tiers = {str(r["tier"] or "null"): r["n"] for r in cur.fetchall()}

        cur.execute("""
            SELECT COUNT(*) n FROM discovered_platforms
            WHERE is_active=TRUE AND last_found_jobs=0 AND consecutive_empty >= 3
        """)
        dead_platforms = cur.fetchone()["n"]

        all_types = {
            "ai_training", "data_annotation", "search_rating", "transcription",
            "translation", "content_writing", "social_media", "virtual_assistant",
            "customer_support", "microtask", "tutoring", "testing", "moderation", "gpt",
        }
        missing = sorted(all_types - set(by_type.keys()))

        return {
            "job_type_distribution": by_type,
            "missing_types": missing,
            "top_sources": by_source,
            "blocked_total": blocked,
            "raw_pending_gate": raw_pending,
            "catalogue_tiers": catalogue_tiers,
            "dead_platforms_count": dead_platforms,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Firecrawl tools (VPS Firecrawl via SSH tunnel localhost:7788) ─────────────

def _firecrawl_post(endpoint: str, payload: dict, timeout: int = 60) -> dict:
    """POST to VPS Firecrawl through the SSH tunnel."""
    url = f"{FIRECRAWL}/v1/{endpoint}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
        except Exception:
            detail = {"http_status": e.code}
        return {"error": str(e), **detail}
    except Exception as e:
        # Fallback: raw urllib fetch with basic HTML strip
        return {"error": str(e), "fallback": True}


def _raw_fetch_fallback(url: str) -> str:
    """Last-resort plain HTTP fetch when Firecrawl is unavailable."""
    import re as _re
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="replace")
        text = _re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=_re.I)
        text = _re.sub(r"<style[^>]*>[\s\S]*?</style>",  " ", text,  flags=_re.I)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"&[a-z]+;", " ", text)
        return _re.sub(r"\s+", " ", text).strip()[:8000]
    except Exception as e:
        return f"[fetch failed: {e}]"


@mcp.tool()
def firecrawl_scrape(url: str, formats: list = None,
                     summarize_for_gating: bool = False) -> dict:
    """
    Scrape a URL via VPS Firecrawl (SSH tunnel → localhost:7788).
    Returns clean markdown + metadata.

    summarize_for_gating=True: returns a compact ~500-char summary optimized for
    keep/block gating decisions. Use for bulk discovery processing to save tokens.
    summarize_for_gating=False (default): returns up to 4000 chars of markdown.

    Falls back to raw urllib fetch if Firecrawl tunnel is down.
    """
    payload = {
        "url":     url,
        "formats": formats or ["markdown"],
    }
    result = _firecrawl_post("scrape", payload)

    if "error" in result or result.get("fallback"):
        markdown = _raw_fetch_fallback(url)
        return {
            "markdown": markdown,
            "metadata": {"url": url},
            "source":   "fallback_fetch",
            "warning":  result.get("error", "Firecrawl unavailable"),
        }

    data     = result.get("data") or result
    markdown = data.get("markdown") or data.get("content") or ""

    if summarize_for_gating and markdown:
        # Extract key lines: title, headings, lines with job-related terms
        import re as _re
        job_terms = {"apply", "join", "hire", "remote", "freelance", "contractor",
                     "task", "annotation", "work from home", "salary", "rate", "pay"}
        lines = markdown.split("\n")
        kept = []
        for line in lines[:80]:  # scan first 80 lines
            ll = line.lower()
            if line.startswith("#") or any(t in ll for t in job_terms):
                kept.append(line.strip())
        markdown = " | ".join(kept)[:500] if kept else markdown[:500]

    return {
        "markdown": markdown[:4000] if not summarize_for_gating else markdown,
        "metadata": data.get("metadata") or {"url": url},
        "source":   "firecrawl",
    }


@mcp.tool()
def firecrawl_batch(urls: list, formats: list = None) -> dict:
    """
    Scrape multiple URLs via VPS Firecrawl in one batch request.

    Args:
        urls:    List of URLs to scrape (max 25 per call).
        formats: Output formats. Default: ["markdown"].

    Returns:
        { results: [{ url, markdown, metadata }], count, source }
    """
    if not urls:
        return {"results": [], "count": 0}

    payload = {
        "urls":    urls[:25],
        "formats": formats or ["markdown"],
    }
    result = _firecrawl_post("batch/scrape", payload, timeout=120)

    if "error" in result:
        # Fall back to individual raw fetches
        results = []
        for url in urls[:25]:
            results.append({
                "url":      url,
                "markdown": _raw_fetch_fallback(url),
                "metadata": {"url": url},
            })
        return {"results": results, "count": len(results), "source": "fallback_fetch"}

    data = result.get("data") or []
    results = [
        {
            "url":      item.get("metadata", {}).get("url") or item.get("url", ""),
            "markdown": item.get("markdown") or item.get("content") or "",
            "metadata": item.get("metadata") or {},
        }
        for item in data
    ]
    return {"results": results, "count": len(results), "source": "firecrawl"}


# ── Search terms (proxied from postgres) ─────────────────────────────────────

@mcp.tool()
def upsert_search_term(term: str, category: str = "", priority: str = "normal",
                       source: str = "seed") -> dict:
    """Add or update a discovery search term in the search_terms table."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_terms (
                id SERIAL PRIMARY KEY, term TEXT NOT NULL UNIQUE,
                category TEXT, priority TEXT DEFAULT 'normal',
                source TEXT DEFAULT 'seed', active BOOLEAN DEFAULT TRUE,
                last_used_at TIMESTAMPTZ, hit_count INT DEFAULT 0,
                gig_count INT DEFAULT 0, gig_rate FLOAT DEFAULT 0.0,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            INSERT INTO search_terms (term, category, priority, source)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (term) DO UPDATE SET
                category=COALESCE(NULLIF(EXCLUDED.category,''), search_terms.category),
                priority=EXCLUDED.priority, active=TRUE
        """, (term, category or None, priority, source))
        return {"ok": True, "term": term}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_active_search_terms(limit: int = 100) -> dict:
    """Return active search terms ordered by priority then gig_rate."""
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute("""
            SELECT term, category, priority, gig_rate, hit_count
            FROM search_terms WHERE active=TRUE
            ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                     gig_rate DESC NULLS LAST
            LIMIT %s
        """, (limit,))
        return {"terms": [_row_to_dict(r) for r in cur.fetchall()]}
    except Exception as e:
        return {"error": str(e), "terms": []}


@mcp.tool()
def update_term_performance(term: str, hits: int = 0, gig_hits: int = 0) -> dict:
    """Update gig_rate for a search term after a gate run. Auto-tunes priority."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("""
            UPDATE search_terms
            SET hit_count=hit_count+%s, gig_count=gig_count+%s, last_used_at=NOW(),
                gig_rate=CASE WHEN (hit_count+%s)>0
                         THEN (gig_count+%s)::float/(hit_count+%s) ELSE 0 END
            WHERE term=%s RETURNING hit_count, gig_count, gig_rate
        """, (hits, gig_hits, hits, gig_hits, hits, term))
        row = cur.fetchone()
        if not row:
            return {"error": f"Term not found: {term}"}
        hit_count, gig_count, gig_rate = row
        action = "updated"
        if hit_count >= 20 and gig_rate < 0.05:
            cur.execute("UPDATE search_terms SET active=FALSE WHERE term=%s", (term,))
            action = "deactivated"
        elif gig_rate > 0.40:
            cur.execute("UPDATE search_terms SET priority='high' WHERE term=%s", (term,))
            action = "promoted"
        return {"ok": True, "term": term, "hit_count": hit_count,
                "gig_rate": round(gig_rate, 3), "action": action}
    except Exception as e:
        return {"error": str(e)}


# ── Raw schools (staging) ─────────────────────────────────────────────────────

@mcp.tool()
def push_raw_school(url: str, name: str = "", raw_content: str = "",
                    scorecard_id: str = "", source: str = "scorecard",
                    federal_loan_rate: float = 0.0, pell_grant_rate: float = 0.0,
                    is_cc: bool = False, online_only: bool = False,
                    state: str = "", city: str = "") -> dict:
    """Push a raw school to staging table. Claude Code analyzes it later."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw_schools (
                id SERIAL PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                name TEXT, raw_content TEXT, scorecard_id TEXT, source TEXT,
                federal_loan_rate FLOAT, pell_grant_rate FLOAT,
                is_cc BOOLEAN DEFAULT FALSE, online_only BOOLEAN DEFAULT FALSE,
                state TEXT, city TEXT, processed BOOLEAN DEFAULT FALSE,
                discovered_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            INSERT INTO raw_schools
                (url,name,raw_content,scorecard_id,source,federal_loan_rate,
                 pell_grant_rate,is_cc,online_only,state,city)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (url) DO NOTHING RETURNING id
        """, (url, name or None, (raw_content or "")[:3000], scorecard_id or None,
              source, federal_loan_rate, pell_grant_rate, is_cc, online_only,
              state or None, city or None))
        row = cur.fetchone()
        return {"ok": True, "new": bool(row)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_raw_schools(limit: int = 25) -> dict:
    """Return unprocessed raw schools for Claude Code analysis."""
    try:
        conn = _pg()
        cur = conn.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["RealDictCursor"]).RealDictCursor)
        cur.execute("""
            SELECT id, url, name, LEFT(raw_content,2000) AS raw_content,
                   scorecard_id, source, federal_loan_rate, pell_grant_rate,
                   is_cc, online_only, state, city
            FROM raw_schools WHERE processed=FALSE
            ORDER BY federal_loan_rate DESC NULLS LAST, discovered_at ASC
            LIMIT %s
        """, (limit,))
        return {"schools": [_row_to_dict(r) for r in cur.fetchall()], "count": 0}
    except Exception as e:
        return {"error": str(e), "schools": []}


@mcp.tool()
def mark_school_processed(school_id: int) -> dict:
    """Mark a raw school as processed after Claude Code analyzed it."""
    try:
        conn = _pg()
        conn.cursor().execute(
            "UPDATE raw_schools SET processed=TRUE WHERE id=%s", (school_id,))
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


# ── Raw discoveries push (from corvus_discovery.py) ──────────────────────────

@mcp.tool()
def push_raw_discovery(url: str, title: str = "", company: str = "",
                       raw_content: str = "", source: str = "",
                       source_query: str = "") -> dict:
    """Push one raw discovery URL to staging. Skips duplicates silently."""
    try:
        conn = _pg()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw_discoveries (
                id SERIAL PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                title TEXT, company TEXT, raw_content TEXT,
                source TEXT, source_query TEXT,
                processed BOOLEAN DEFAULT FALSE, blocked BOOLEAN DEFAULT FALSE,
                block_reason TEXT, discovered_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            INSERT INTO raw_discoveries (url,title,company,raw_content,source,source_query)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (url) DO NOTHING RETURNING id
        """, (url, title or None, company or None, (raw_content or "")[:2000],
              source or None, source_query or None))
        row = cur.fetchone()
        return {"ok": True, "new": bool(row)}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run()
