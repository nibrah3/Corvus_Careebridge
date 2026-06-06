"""
raw_schools staging table helpers.
Phase 1 (crawl): write raw_text here, analyzed=False.
Phase 2 (Claude Code): read batches, analyze, mark analyzed=True, upsert into schools.
"""
from __future__ import annotations
import logging, os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DSN = os.environ.get(
    "VPS_PG_DSN",
    "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge",
)

try:
    import psycopg2, psycopg2.extras
    _HAS_DB = True
except ImportError:
    _HAS_DB = False


def _connect():
    return psycopg2.connect(_DSN, connect_timeout=10)


def already_crawled(url_hash: str) -> bool:
    if not _HAS_DB:
        return False
    try:
        with _connect() as c:
            with c.cursor() as cur:
                cur.execute("SELECT 1 FROM raw_schools WHERE url_hash=%s", (url_hash,))
                return cur.fetchone() is not None
    except Exception as e:
        log.debug("already_crawled check failed: %s", e)
        return False


def save_raw(school: dict, raw_text: str) -> bool:
    """Save crawled school text to raw_schools for later Claude Code analysis."""
    if not _HAS_DB:
        return False
    from schools_mcp._db import url_hash
    h = url_hash(school["url"])
    try:
        with _connect() as c:
            with c.cursor() as cur:
                # Strip NUL bytes — Postgres TEXT cannot contain \x00
                clean_text = (raw_text or "").replace("\x00", "")[:50000]
                cur.execute("""
                    INSERT INTO raw_schools
                        (scorecard_id, name, url, city, state,
                         is_community_college, online_only, open_admissions,
                         raw_text, url_hash, analyzed)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE)
                    ON CONFLICT (url_hash) DO UPDATE SET
                        raw_text   = EXCLUDED.raw_text,
                        crawled_at = NOW(),
                        analyzed   = FALSE
                """, (
                    school.get("scorecard_id"),
                    school.get("name", ""),
                    school.get("url", ""),
                    school.get("city", ""),
                    school.get("state", ""),
                    bool(school.get("is_community_college")),
                    bool(school.get("online_only")),
                    bool(school.get("open_admissions")),
                    clean_text,
                    h,
                ))
            c.commit()
        return True
    except Exception as e:
        log.warning("save_raw failed for %s: %s", school.get("name", "?")[:40], e)
        return False


def get_unanalyzed_batch(batch_size: int = 20) -> list[dict]:
    """Fetch a batch of unanalyzed raw schools for Claude Code to process."""
    if not _HAS_DB:
        return []
    try:
        with _connect() as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, scorecard_id, name, url, city, state,
                           is_community_college, raw_text
                    FROM raw_schools
                    WHERE analyzed = FALSE AND raw_text IS NOT NULL AND raw_text != ''
                    ORDER BY id
                    LIMIT %s
                """, (batch_size,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.warning("get_unanalyzed_batch failed: %s", e)
        return []


def mark_analyzed(raw_id: int, error: str = None) -> None:
    if not _HAS_DB:
        return
    try:
        with _connect() as c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE raw_schools SET analyzed=TRUE, analysis_error=%s WHERE id=%s",
                    (error, raw_id),
                )
            c.commit()
    except Exception as e:
        log.warning("mark_analyzed failed: %s", e)


def pending_count() -> int:
    if not _HAS_DB:
        return 0
    try:
        with _connect() as c:
            with c.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM raw_schools WHERE analyzed=FALSE AND raw_text!=''")
                return cur.fetchone()[0]
    except Exception:
        return -1
