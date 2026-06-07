"""
crawl_schools.py — Phase 2: crawl school homepages via Firecrawl.

For every school that passed Phase 1 with status='not_online', fetch their
actual website homepage and re-run the keyword gate. Schools where the homepage
confirms online courses get promoted to fully_online=true. Schools whose page
loaded but had no signal go to crawl_status='uncertain' for subagent review.
Schools whose page failed get crawl_status='error'.

Firecrawl runs on VPS, tunneled to localhost:7788.

Usage:
    python scripts/crawl_schools.py --machine primary  --workers 4
    python scripts/crawl_schools.py --machine secondary --workers 4
"""
from __future__ import annotations

import argparse, json, logging, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import psycopg2, psycopg2.extras, requests

CB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CB_DIR))

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k = k.strip(); v = v.strip()
        if k and k not in os.environ: os.environ[k] = v

DSN           = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
FIRECRAWL_URL = os.environ.get("FIRECRAWL_URL", "http://localhost:7788")

LOG_FILE = CB_DIR / "logs" / "crawl_schools.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("crawl")

# ── Online signal (same gate as Phase 1, applied to crawled HTML) ─────────────
_ONLINE_SIGNAL = re.compile(
    r"\b(online\s+(program|course|degree|class|learning|education|enrollment|study)|"
    r"distance\s+(learning|education|program)|"
    r"fully\s+online|100\s*%\s*online|online[\s-]?only|"
    r"e[\s-]?learning|virtual\s+(class|course|program)|"
    r"remote\s+(learn|study|program|course)|"
    r"asynchronous|self[\s-]?paced\s+online|"
    r"web[\s-]?based\s+(course|program|class)|"
    r"enroll\s+online|apply\s+online\s+now|"
    r"online\s+admissions|study\s+from\s+(home|anywhere))\b",
    re.IGNORECASE,
)

# Stronger signals — even a single match means confident online
_STRONG_SIGNAL = re.compile(
    r"\b(fully\s+online|100\s*%\s*online|online[\s-]?only|distance\s+education\s+program|"
    r"entirely\s+online|exclusively\s+online|no\s+campus\s+visit|"
    r"online\s+degree\s+program|online\s+bachelor|online\s+associate|"
    r"complete\s+(your\s+)?(degree|program)\s+online)\b",
    re.IGNORECASE,
)


# ── Firecrawl ─────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def _direct_fetch(url: str, timeout: int = 15) -> str:
    """Plain HTTP GET + strip tags. Fallback when Firecrawl returns empty."""
    try:
        import html, re as _re
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        text = r.text
        # Strip script/style blocks
        text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
        # Strip all tags
        text = _re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = _re.sub(r"\s+", " ", text)
        return text[:8000]
    except Exception:
        return ""


def firecrawl_scrape(url: str, timeout: int = 20) -> dict:
    """Firecrawl first, direct HTTP fallback if empty. Returns {text, success, error}."""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        r = requests.post(
            f"{FIRECRAWL_URL}/v1/scrape",
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True,
                  "timeout": timeout * 1000},
            timeout=timeout + 5,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            content = data.get("data", {})
            text = content.get("markdown") or content.get("content") or ""
            if len(text) > 200:
                return {"text": text, "success": True, "error": None, "source": "firecrawl"}
            # Firecrawl returned too little — fall through to direct
    except Exception:
        pass

    # Direct HTTP fallback
    text = _direct_fetch(url, timeout=15)
    if text:
        return {"text": text, "success": True, "error": None, "source": "direct"}
    return {"text": "", "success": False, "error": "empty_response", "source": "failed"}


# ── Classify crawled page ─────────────────────────────────────────────────────

def classify_page(text: str) -> tuple[bool, str, str]:
    """Return (is_online, confidence, signal_found)."""
    if not text:
        return False, "no_content", ""
    if _STRONG_SIGNAL.search(text):
        m = _STRONG_SIGNAL.search(text)
        return True, "strong", m.group(0)[:60]
    count = len(_ONLINE_SIGNAL.findall(text))
    if count >= 2:
        m = _ONLINE_SIGNAL.search(text)
        return True, "moderate", f"{count}x: {m.group(0)[:40]}"
    if count == 1:
        m = _ONLINE_SIGNAL.search(text)
        return False, "weak", f"1x: {m.group(0)[:40]}"  # uncertain
    return False, "none", ""


# ── Process one school ────────────────────────────────────────────────────────

def process_school(work_id: int, school_url: str, school_name: str) -> dict:
    result = firecrawl_scrape(school_url)
    if not result["success"]:
        return {
            "work_id": work_id,
            "crawl_status": "error",
            "crawl_url": school_url,
            "crawl_text_len": 0,
            "crawl_signal": result["error"],
            "promote_online": False,
        }

    text = result["text"]
    is_online, confidence, signal = classify_page(text)

    if is_online:
        status = "confirmed_online"
    elif confidence == "weak":
        status = "uncertain"
    else:
        status = "confirmed_offline"

    return {
        "work_id": work_id,
        "crawl_status": status,
        "crawl_url": school_url,
        "crawl_text_len": len(text),
        "crawl_signal": signal or "none",
        "promote_online": is_online,
        "confidence": confidence,
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(machine: str, workers: int, batch_size: int) -> None:
    log.info("Phase 2 crawl starting — machine=%s workers=%d", machine, workers)

    conn_main = psycopg2.connect(DSN, connect_timeout=10)
    conn_main.autocommit = False
    cur = conn_main.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    total = confirmed = uncertain = errors = offline = 0
    t0 = time.monotonic()

    while True:
        # Claim next batch
        cur.execute("""
            UPDATE school_classification_work
            SET crawl_status = 'crawling'
            WHERE id IN (
                SELECT w.id FROM school_classification_work w
                WHERE w.assigned_to = %s AND w.crawl_status = 'pending'
                ORDER BY w.id LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, raw_school_id
        """, (machine, batch_size))
        batch = [dict(r) for r in cur.fetchall()]
        conn_main.commit()

        if not batch:
            log.info("No more crawl_pending rows for %s.", machine)
            break

        # Fetch school URLs
        raw_ids = [w["raw_school_id"] for w in batch]
        cur.execute("SELECT id, name, url FROM raw_schools WHERE id = ANY(%s)", (raw_ids,))
        schools = {r["id"]: dict(r) for r in cur.fetchall()}

        work_map = {w["raw_school_id"]: w["id"] for w in batch}

        # Crawl in parallel
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for raw_id, wid in work_map.items():
                school = schools.get(raw_id, {})
                url  = school.get("url", "") or ""
                name = school.get("name", "") or ""
                if url:
                    f = pool.submit(process_school, wid, url, name)
                    futures[f] = wid
                else:
                    # No URL — mark error
                    cur.execute("""
                        UPDATE school_classification_work SET
                            crawl_status='error', crawl_signal='no_url'
                        WHERE id=%s
                    """, (wid,))

            for f in as_completed(futures):
                res = f.result()
                wid = res["work_id"]
                cur.execute("""
                    UPDATE school_classification_work SET
                        crawl_status   = %(crawl_status)s,
                        crawl_url      = %(crawl_url)s,
                        crawl_text_len = %(crawl_text_len)s,
                        crawl_signal   = %(crawl_signal)s,
                        fully_online   = CASE WHEN %(promote)s THEN true ELSE fully_online END,
                        online_available = CASE WHEN %(promote)s THEN true ELSE online_available END,
                        online_evidence  = CASE WHEN %(promote)s THEN %(signal)s ELSE online_evidence END,
                        classified_by    = CASE WHEN %(promote)s THEN 'crawl' ELSE classified_by END,
                        status           = CASE WHEN %(promote)s THEN 'done' ELSE status END
                    WHERE id = %(work_id)s
                """, {
                    "crawl_status": res["crawl_status"],
                    "crawl_url": res.get("crawl_url", ""),
                    "crawl_text_len": res.get("crawl_text_len", 0),
                    "crawl_signal": res.get("crawl_signal", ""),
                    "promote": res.get("promote_online", False),
                    "signal": f"crawl:{res.get('crawl_signal','')[:80]}",
                    "work_id": wid,
                })

                if res["crawl_status"] == "confirmed_online":   confirmed += 1
                elif res["crawl_status"] == "uncertain":         uncertain += 1
                elif res["crawl_status"] == "error":             errors    += 1
                else:                                            offline   += 1

        conn_main.commit()
        total += len(batch)
        elapsed = time.monotonic() - t0
        rate = total / elapsed * 60
        log.info("Crawled: %d | +online=%d uncertain=%d offline=%d err=%d | %.0f/min",
                 total, confirmed, uncertain, offline, errors, rate)

    elapsed = time.monotonic() - t0
    log.info("=" * 60)
    log.info("CRAWL COMPLETE machine=%s | confirmed=%d uncertain=%d offline=%d errors=%d | %.1f min",
             machine, confirmed, uncertain, offline, errors, elapsed / 60)
    log.info("=" * 60)

    # Summary of uncertain schools (candidates for subagent review)
    cur.execute("""
        SELECT s.name, s.url, w.crawl_signal
        FROM school_classification_work w
        JOIN raw_schools s ON s.id = w.raw_school_id
        WHERE w.assigned_to = %s AND w.crawl_status = 'uncertain'
        ORDER BY s.name LIMIT 20
    """, (machine,))
    rows = cur.fetchall()
    if rows:
        log.info("Uncertain schools needing subagent review (%d total, first 20):", uncertain)
        for r in rows:
            log.info("  %s | %s | signal: %s", r["name"][:40], r["url"][:40], r["crawl_signal"])

    conn_main.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine",  default=os.environ.get("CB_SYNC_ROLE", "primary"))
    ap.add_argument("--workers",  type=int, default=4)
    ap.add_argument("--batch",    type=int, default=20)
    args = ap.parse_args()
    run(args.machine, args.workers, args.batch)
