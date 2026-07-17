"""
school_scraper.py — Dumb school collector for CareerBridge VPS.

Responsibility: collect raw school data → write to raw_schools table.
NO criteria analysis. NO LLM calls.
Claude Code (skill_analyze_schools.md) does all intelligence.

Pipeline:
  Phase 1 — College Scorecard API: open-admission + financial-aid-eligible institutions.
             Filter: aid.federal_loan_rate > 0 (Title IV eligible = accredited schools only).
  Phase 2 — Firecrawl: visit enrollment page, capture raw text → raw_schools table.
  Phase 3 — Serper supplementary: catch schools not in Scorecard.

Run on VPS:
  python3 school_scraper.py                         # all sources
  python3 school_scraper.py --source scorecard       # Scorecard only
  python3 school_scraper.py --source serper          # Serper only
  python3 school_scraper.py --limit 500              # initial seeding run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ── Load .env ──────────────────────────────────────────────────────────────────

def _load_env(path: str = "/opt/corvus/.env") -> None:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass

_load_env()


# ── Config ─────────────────────────────────────────────────────────────────────

POSTGRES_MCP   = "http://localhost:8801"
CRAWLEE_MCP    = "http://localhost:8802"
SCORECARD_KEY  = os.environ.get("SCORECARD_API_KEY", "")
SERPER_KEY     = os.environ.get("SERPER_API_KEY", "")
DB_DSN         = os.environ.get("VPS_PG_DSN",
                     "postgresql://corvus:corvus-local-password@localhost:5432/careerbridge")

SCORECARD_BASE   = "https://api.data.gov/ed/collegescorecard/v1/schools"
SCORECARD_FIELDS = ",".join([
    "id",
    "school.name",
    "school.school_url",
    "school.city",
    "school.state",
    "school.online_only",
    "school.predominant_degree",
    "school.open_admissions_policy",
    "school.ownership",
    "aid.federal_loan_rate",    # Key filter: Title IV eligibility proxy
    "aid.pell_grant_rate",      # Need-based aid signal
])

# Serper queries for schools not in Scorecard
SERPER_SCHOOL_QUERIES = [
    "online community college rolling admission no transcript required",
    "online school instant acceptance monthly enrollment",
    "community college open enrollment no ID verification remote",
    "online college monthly start date no prior transcripts",
    "accredited online school federal financial aid eligible",
    "online community college Pell grant eligible instant admission",
    "distance learning college no transcript open enrollment",
    "online school same day acceptance federal loans available",
]


# ── HTTP helpers ───────────────────────────────────────────────────────────────

_mcp_seq = 0

def _post(url: str, body: dict, headers: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}


def mcp_call(base: str, tool: str, **kwargs) -> dict:
    global _mcp_seq
    _mcp_seq += 1
    resp = _post(f"{base}/mcp", {
        "jsonrpc": "2.0", "id": _mcp_seq,
        "method":  "tools/call",
        "params":  {"name": tool, "arguments": kwargs},
    })
    if "error" in resp:
        return resp
    try:
        return json.loads(resp["result"]["content"][0]["text"])
    except Exception:
        return resp


# ── Already-in-DB check ────────────────────────────────────────────────────────

def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def already_collected(url: str) -> bool:
    """Return True if this URL is already in raw_schools OR schools table."""
    try:
        import psycopg2
        conn = psycopg2.connect(DB_DSN, connect_timeout=5)
        with conn.cursor() as cur:
            h = _url_hash(url)
            cur.execute(
                "SELECT 1 FROM raw_schools WHERE url=%s "
                "UNION ALL SELECT 1 FROM schools WHERE url_hash=%s LIMIT 1",
                (url, h)
            )
            return cur.fetchone() is not None
        conn.close()
    except Exception:
        return False


# ── College Scorecard API ──────────────────────────────────────────────────────

def _scorecard_page(params: dict, page: int, per_page: int = 100) -> list[dict]:
    if not SCORECARD_KEY:
        return []
    full_params = {
        **params,
        "fields":   SCORECARD_FIELDS,
        "per_page": per_page,
        "page":     page,
        "api_key":  SCORECARD_KEY,
    }
    qs = urllib.parse.urlencode(full_params)
    req = urllib.request.Request(f"{SCORECARD_BASE}?{qs}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode()).get("results", [])
    except Exception as e:
        print(f"[scorecard] page {page} error: {e}", file=sys.stderr)
        return []


def run_scorecard(fetch_limit: int = 200) -> int:
    """
    Pull open-admission, loan-eligible US schools from Scorecard.
    Pushes raw records (no criteria analysis) to raw_schools table.
    Returns count pushed.
    """
    if not SCORECARD_KEY:
        print("[scorecard] SCORECARD_API_KEY not set — skipping", file=sys.stderr)
        return 0

    # Base queries: online schools AND open-admission schools WITH loan activity
    query_sets = [
        {"school.online_only": 1, "school.operating": 1,
         "aid.federal_loan_rate__range": "0.01..1.0"},
        {"school.open_admissions_policy": 1, "school.operating": 1,
         "aid.federal_loan_rate__range": "0.01..1.0"},
    ]

    seen_ids: set = set()
    pushed = 0

    for params in query_sets:
        page = 0
        while pushed < fetch_limit:
            results = _scorecard_page(params, page)
            if not results:
                break

            for r in results:
                sid = r.get("id")
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)

                url = (r.get("school.school_url") or "").strip()
                if not url:
                    continue
                if not url.startswith("http"):
                    url = "https://" + url
                name = (r.get("school.name") or "").strip()
                if not name:
                    continue

                if already_collected(url):
                    continue

                # Fetch enrollment page content via Firecrawl
                raw_content = ""
                try:
                    resp = mcp_call(CRAWLEE_MCP, "scrape_url", url=url)
                    raw_content = (resp.get("content") or resp.get("markdown") or "")[:3000]
                except Exception:
                    pass

                loan_rate = float(r.get("aid.federal_loan_rate") or 0)
                pell_rate = float(r.get("aid.pell_grant_rate") or 0)
                degree    = r.get("school.predominant_degree")
                online    = bool(r.get("school.online_only"))

                result = mcp_call(POSTGRES_MCP, "push_raw_school",
                    url=url,
                    name=name,
                    raw_content=raw_content,
                    scorecard_id=str(sid),
                    source="scorecard",
                    federal_loan_rate=loan_rate,
                    pell_grant_rate=pell_rate,
                    is_cc=(degree == 2),
                    online_only=online,
                    state=r.get("school.state", ""),
                    city=r.get("school.city", ""),
                )
                if result.get("new"):
                    pushed += 1
                    if pushed % 25 == 0:
                        print(f"  [scorecard] pushed {pushed}...", flush=True)

                time.sleep(0.5)

            if len(results) < 100:
                break
            page += 1
            time.sleep(0.3)

    print(f"[scorecard] done — pushed {pushed} raw schools")
    return pushed


# ── Serper supplementary ───────────────────────────────────────────────────────

def run_serper(limit: int = 50) -> int:
    """Find schools via Serper that aren't in Scorecard. Pushes raw to staging."""
    if not SERPER_KEY:
        print("[serper_schools] SERPER_KEY not set — skipping", file=sys.stderr)
        return 0

    pushed = 0
    for query in SERPER_SCHOOL_QUERIES:
        if pushed >= limit:
            break
        resp = _post(
            "https://google.serper.dev/search",
            {"q": query, "num": 10, "gl": "us", "hl": "en"},
            headers={"X-API-KEY": SERPER_KEY},
            timeout=15,
        )
        for item in resp.get("organic", []):
            url = item.get("link", "")
            if not url or already_collected(url):
                continue

            # Fetch page content
            raw_content = ""
            try:
                resp2 = mcp_call(CRAWLEE_MCP, "scrape_url", url=url)
                raw_content = (resp2.get("content") or resp2.get("markdown") or "")[:3000]
            except Exception:
                raw_content = item.get("snippet", "")[:500]

            result = mcp_call(POSTGRES_MCP, "push_raw_school",
                url=url,
                name=item.get("title", ""),
                raw_content=raw_content,
                source="serper",
                federal_loan_rate=0.0,   # Unknown until analyzed
            )
            if result.get("new"):
                pushed += 1
        time.sleep(0.5)

    print(f"[serper_schools] pushed {pushed} raw schools")
    return pushed


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="all",
                        choices=["scorecard", "serper", "all"])
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    start = datetime.now(timezone.utc)
    print(f"[{start.isoformat()}] school_scraper starting — source={args.source}")

    total = 0
    if args.source in ("scorecard", "all"):
        total += run_scorecard(args.limit)
    if args.source in ("serper", "all"):
        total += run_serper(min(args.limit, 50))

    print(f"[school_scraper] total raw schools pushed: {total}")
