"""
browser_crawl_schools.py — Phase 3: Playwright browser crawl for JS-heavy schools.

Targets schools where crawl_status='error' (empty_response from JS sites)
and crawl_status='uncertain' (single weak signal needing confirmation).

Uses headless Chromium to render JS, waits for network idle, then checks
for online course signals on the loaded page.

Usage:
    python scripts/browser_crawl_schools.py --workers 3 --batch 10
"""
from __future__ import annotations

import argparse, logging, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import psycopg2, psycopg2.extras

CB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CB_DIR))

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k = k.strip(); v = v.strip()
        if k and k not in os.environ: os.environ[k] = v

DSN = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")

LOG_FILE = CB_DIR / "logs" / "browser_crawl.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("browser_crawl")

# ── Signal patterns (same gate as classifier) ─────────────────────────────────
_ONLINE_SIGNAL = re.compile(
    r"\b(online\s+(program|course|degree|class|learning|education|enrollment|study)|"
    r"distance\s+(learning|education|program)|"
    r"fully\s+online|100\s*%\s*online|online[\s-]?only|"
    r"e[\s-]?learning|virtual\s+(class|course|program)|"
    r"remote\s+(learn|study|program|course)|"
    r"asynchronous|self[\s-]?paced\s+online|"
    r"web[\s-]?based\s+(course|program)|"
    r"enroll\s+online|study\s+from\s+(home|anywhere)|"
    r"online\s+admissions|online\s+degree\s+program|"
    r"distance\s+education\s+program)\b",
    re.IGNORECASE,
)
_STRONG_SIGNAL = re.compile(
    r"\b(fully\s+online|100\s*%\s*online|online[\s-]?only|"
    r"entirely\s+online|exclusively\s+online|no\s+campus|"
    r"online\s+degree\s+program|online\s+bachelor|online\s+associate|"
    r"complete\s+(your\s+)?(degree|program)\s+online|"
    r"distance\s+education\s+program)\b",
    re.IGNORECASE,
)


def browse_school(url: str, timeout_ms: int = 20000) -> dict:
    """Open URL in headless Chromium, return {text, success, error, signal}."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    if not url.startswith("http"):
        url = "https://" + url

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()
            page.set_default_timeout(timeout_ms)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Brief wait for JS to populate nav/hero text
                page.wait_for_timeout(2000)
            except PWTimeout:
                browser.close()
                return {"text": "", "success": False, "error": "timeout", "signal": ""}

            text = page.inner_text("body") or ""
            text = text[:6000]
            browser.close()

            if not text.strip():
                return {"text": "", "success": False, "error": "empty_after_js", "signal": ""}

            return {"text": text, "success": True, "error": None, "signal": ""}

    except Exception as e:
        return {"text": "", "success": False, "error": str(e)[:80], "signal": ""}


def classify_text(text: str) -> tuple[bool, str, str]:
    """Return (is_online, confidence, matched_signal)."""
    if not text:
        return False, "no_content", ""
    if _STRONG_SIGNAL.search(text):
        m = _STRONG_SIGNAL.search(text)
        return True, "strong", m.group(0)[:60]
    hits = _ONLINE_SIGNAL.findall(text)
    if len(hits) >= 2:
        m = _ONLINE_SIGNAL.search(text)
        return True, "moderate", f"{len(hits)}x: {m.group(0)[:40]}"
    if len(hits) == 1:
        m = _ONLINE_SIGNAL.search(text)
        return False, "weak", f"1x: {m.group(0)[:40]}"
    return False, "none", ""


def process_one(work_id: int, url: str, name: str) -> dict:
    result = browse_school(url)
    if not result["success"]:
        return {
            "work_id": work_id,
            "crawl_status": "browser_error",
            "crawl_signal": result["error"],
            "crawl_text_len": 0,
            "promote_online": False,
        }

    is_online, confidence, signal = classify_text(result["text"])

    if is_online:
        status = "confirmed_online"
    elif confidence == "weak":
        status = "browser_uncertain"
    else:
        status = "confirmed_offline"

    log.info("  %-45s | %-18s | %s", name[:45], status, signal[:50] if signal else "none")

    return {
        "work_id": work_id,
        "crawl_status": status,
        "crawl_signal": signal or "none",
        "crawl_text_len": len(result["text"]),
        "promote_online": is_online,
    }


def run(workers: int, batch_size: int) -> None:
    log.info("Browser crawl starting — workers=%d batch=%d", workers, batch_size)

    conn = psycopg2.connect(DSN, connect_timeout=10)
    conn.autocommit = False
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    total = confirmed = browser_uncertain = offline = errors = 0
    t0 = time.monotonic()

    while True:
        # Claim next batch from error + uncertain pool
        cur.execute("""
            UPDATE school_classification_work
            SET crawl_status = 'browser_crawling'
            WHERE id IN (
                SELECT w.id FROM school_classification_work w
                WHERE w.crawl_status IN ('error', 'uncertain')
                ORDER BY w.crawl_status DESC, w.id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, raw_school_id, crawl_status as prev_status
        """, (batch_size,))
        batch = [dict(r) for r in cur.fetchall()]
        conn.commit()

        if not batch:
            log.info("No more error/uncertain rows to process.")
            break

        raw_ids = [w["raw_school_id"] for w in batch]
        cur.execute("SELECT id, name, url FROM raw_schools WHERE id = ANY(%s)", (raw_ids,))
        schools = {r["id"]: dict(r) for r in cur.fetchall()}
        work_map = {w["raw_school_id"]: w["id"] for w in batch}

        # Browser crawl in parallel
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for raw_id, wid in work_map.items():
                s = schools.get(raw_id, {})
                url  = s.get("url", "") or ""
                name = s.get("name", "") or ""
                if url:
                    futures[pool.submit(process_one, wid, url, name)] = wid
                else:
                    cur.execute("UPDATE school_classification_work SET crawl_status='browser_error', crawl_signal='no_url' WHERE id=%s", (wid,))

            for f in as_completed(futures):
                res = f.result()
                wid = res["work_id"]
                cur.execute("""
                    UPDATE school_classification_work SET
                        crawl_status    = %(crawl_status)s,
                        crawl_signal    = %(crawl_signal)s,
                        crawl_text_len  = %(crawl_text_len)s,
                        fully_online    = CASE WHEN %(promote)s THEN true ELSE fully_online END,
                        online_available= CASE WHEN %(promote)s THEN true ELSE online_available END,
                        online_evidence = CASE WHEN %(promote)s THEN %(signal)s ELSE online_evidence END,
                        classified_by   = CASE WHEN %(promote)s THEN 'browser' ELSE classified_by END,
                        status          = CASE WHEN %(promote)s THEN 'done' ELSE status END
                    WHERE id = %(work_id)s
                """, {
                    "crawl_status": res["crawl_status"],
                    "crawl_signal": res["crawl_signal"],
                    "crawl_text_len": res["crawl_text_len"],
                    "promote": res["promote_online"],
                    "signal": f"browser:{res['crawl_signal'][:80]}",
                    "work_id": wid,
                })

                st = res["crawl_status"]
                if st == "confirmed_online":       confirmed        += 1
                elif st == "browser_uncertain":    browser_uncertain += 1
                elif st == "browser_error":        errors           += 1
                else:                              offline          += 1

        conn.commit()
        total += len(batch)
        elapsed = time.monotonic() - t0
        rate    = total / elapsed * 60 if elapsed > 0 else 0
        log.info("Progress: %d/%d done | +online=%d uncertain=%d offline=%d err=%d | %.0f/min",
                 total, total + (batch_size if batch else 0),
                 confirmed, browser_uncertain, offline, errors, rate)

    elapsed = time.monotonic() - t0
    log.info("=" * 60)
    log.info("BROWSER CRAWL COMPLETE | +online=%d uncertain=%d offline=%d errors=%d | %.1f min",
             confirmed, browser_uncertain, offline, errors, elapsed / 60)
    log.info("=" * 60)

    # Report any remaining browser_uncertain
    cur.execute("""
        SELECT s.name, s.state, w.crawl_signal, s.url
        FROM school_classification_work w
        JOIN raw_schools s ON s.id = w.raw_school_id
        WHERE w.crawl_status = 'browser_uncertain'
        ORDER BY s.name LIMIT 20
    """)
    rows = cur.fetchall()
    if rows:
        log.info("Remaining browser_uncertain (%d shown):", len(rows))
        for r in rows:
            log.info("  %-45s %s | %s", r["name"][:45], r["state"], r["crawl_signal"])

    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--batch",   type=int, default=9)
    args = ap.parse_args()
    run(args.workers, args.batch)
