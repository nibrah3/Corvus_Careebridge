"""
corvus_discovery.py — Dumb collector for Track A gig discovery.

Responsibility: collect raw URLs and content → write to raw_discoveries table.
NO classification. NO LLM calls. NO extraction.
Claude Code (skill_gate_discoveries.md / skill_clean_scraped_data.md) does all intelligence.

Sources:
  1. Serper API — queries read from search_terms table (Claude-managed)
  2. Reddit via crawlee_mcp
  3. Greenhouse ATS boards (keyword-filtered client-side, no LLM)
  4. Firecrawl direct page scrape of known platforms → raw content pushed

Run: python3 corvus_discovery.py [--source serper|reddit|ats|pages|all]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
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

POSTGRES_MCP  = "http://localhost:8801"
CRAWLEE_MCP   = "http://localhost:8802"
TELEGRAM_MCP  = "http://localhost:8803"
REDIS_HOST    = "127.0.0.1"
REDIS_PORT    = 6379
SERPER_KEY    = os.environ.get("SERPER_API_KEY", "")

# Fallback queries used only when search_terms table is empty or unreachable
FALLBACK_QUERIES = [
    "AI trainer data annotator remote work from home",
    "data annotation labeling freelance contract remote",
    "content moderation remote gig work",
    "RLHF AI evaluator freelance",
    "search quality rater remote evaluation",
    "transcription captioning remote freelance",
    "online tutoring per session remote",
    "microtask crowd work online",
]

# Reddit sources
REDDIT_SOURCES = [
    ("reddit", "AI training annotation remote work"),
    ("reddit", "data labeling remote job work from home"),
    ("reddit", "beermoney AI annotator task evaluator"),
    ("reddit", "freelance writing remote agency"),
    ("reddit", "online work remote gig jobs"),
]

# Greenhouse ATS boards — keyword-matched client-side only
GREENHOUSE_BOARDS = [
    "prolific",
    "remotasks",
]

# Known gig platform pages — scraped raw, content pushed to staging
TRACK_A_PAGES = [
    {"url": "https://www.appen.com/jobs/",              "company": "Appen"},
    {"url": "https://dataannotation.tech/hire",          "company": "DataAnnotation"},
    {"url": "https://www.outlier.ai/careers",            "company": "Outlier AI"},
    {"url": "https://remotasks.com/en/jobs",             "company": "Remotasks"},
    {"url": "https://www.clickworker.com/clickworker/",  "company": "Clickworker"},
    {"url": "https://toloka.ai/jobs/",                   "company": "Toloka"},
    {"url": "https://surgehq.ai/careers",                "company": "Surge HQ"},
    {"url": "https://www.telusinternational.com/solutions/ai-data/ai-training",
                                                         "company": "TELUS International"},
    {"url": "https://www.lionbridge.com/ai-training-data-services/",
                                                         "company": "Lionbridge"},
    {"url": "https://sama.com/careers/",                 "company": "Sama"},
    {"url": "https://hive.com/about/jobs",               "company": "Hive"},
]

# Keyword filter for Greenhouse (no LLM — simple substring match)
TRACK_A_KEYWORDS = [
    "ai trainer", "ai training", "data annotation", "data annotator",
    "data labeling", "data labelling", "content moderator", "content moderation",
    "rlhf", "ai evaluator", "ai feedback", "ai reviewer",
    "annotation specialist", "labeling specialist", "image annotation",
    "video annotation", "text annotation", "speech annotation", "audio annotation",
    "machine learning trainer", "human feedback", "search quality", "quality rater",
    "micro task", "micro-task", "crowdsource", "crowd work",
    "transcriptionist", "transcription", "captioner",
]


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _post(url: str, body: dict, headers: dict | None = None, timeout: int = 30) -> dict:
    import urllib.request
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


_mcp_seq = 0

def mcp_call(base: str, tool: str, **kwargs) -> dict:
    global _mcp_seq
    _mcp_seq += 1
    resp = _post(f"{base}/mcp", {
        "jsonrpc": "2.0", "id": _mcp_seq,
        "method": "tools/call",
        "params": {"name": tool, "arguments": kwargs},
    })
    if "error" in resp:
        return resp
    try:
        return json.loads(resp["result"]["content"][0]["text"])
    except Exception:
        return resp


def _tg(text: str) -> None:
    try:
        mcp_call(TELEGRAM_MCP, "notify", message=text)
    except Exception:
        pass


# ── Redis signal ───────────────────────────────────────────────────────────────

def redis_signal_raw_ready() -> None:
    """Signal to Desktop that new raw_discoveries are waiting for Claude Code gate."""
    try:
        with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=3) as sock:
            for cmd_str in [
                "*3\r\n$7\r\nPUBLISH\r\n$20\r\ncorvus:raw_ready\r\n$3\r\nnew\r\n",
                "*3\r\n$5\r\nRPUSH\r\n$24\r\ncorvus:raw_ready_queue\r\n$3\r\nnew\r\n",
            ]:
                sock.sendall(cmd_str.encode())
                sock.recv(64)
    except Exception as e:
        print(f"[redis] signal failed: {e}", file=sys.stderr)


# ── Staging push ───────────────────────────────────────────────────────────────

def push_raw(url: str, title: str, company: str, raw_content: str,
             source: str, source_query: str = "") -> bool:
    """Push a single raw discovery to the staging table. Returns True on success."""
    if not url:
        return False
    result = mcp_call(POSTGRES_MCP, "push_raw_discovery",
                      url=url,
                      title=title[:300] if title else "",
                      company=company[:200] if company else "",
                      raw_content=raw_content[:2000] if raw_content else "",
                      source=source,
                      source_query=source_query[:500] if source_query else "")
    return "error" not in result


# ── Search terms ───────────────────────────────────────────────────────────────

def get_queries() -> list[dict]:
    """
    Read active search terms from postgres (Claude-managed).
    Falls back to FALLBACK_QUERIES if table empty or unreachable.
    Returns list of {term, category, priority}.
    """
    resp = mcp_call(POSTGRES_MCP, "get_active_search_terms", limit=100)
    terms = resp.get("terms", [])
    if terms:
        # High priority first, then normal
        terms.sort(key=lambda t: (0 if t.get("priority") == "high" else 1))
        return terms
    # Fallback: wrap hardcoded list in same structure
    print("  [search_terms] table empty or unreachable — using fallback queries", file=sys.stderr)
    return [{"term": q, "category": "general", "priority": "normal"} for q in FALLBACK_QUERIES]


# ── Serper search ──────────────────────────────────────────────────────────────

_NOISE_DOMAINS = {
    "youtube.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "wikipedia.org", "amazon.com", "tiktok.com", "pinterest.com", "quora.com",
}

def _is_noise(url: str) -> bool:
    return any(d in url for d in _NOISE_DOMAINS)


def _company_from_url(url: str) -> str:
    try:
        return url.split("/")[2].replace("www.", "")
    except Exception:
        return ""


def run_serper(queries: list[dict], seen: set, limit_per_query: int = 10) -> int:
    """Run Serper searches and push results to raw_discoveries. Returns count pushed."""
    if not SERPER_KEY:
        print("  [Serper] SERPER_KEY not set — skipping", file=sys.stderr)
        return 0

    import urllib.request
    pushed = 0
    for item in queries:
        query = item["term"]
        resp = _post(
            "https://google.serper.dev/search",
            {"q": query, "num": limit_per_query, "gl": "gb", "hl": "en"},
            headers={"X-API-KEY": SERPER_KEY},
            timeout=15,
        )
        for r in resp.get("organic", []):
            url = r.get("link", "")
            if not url or _is_noise(url) or url in seen:
                continue
            seen.add(url)
            ok = push_raw(
                url=url,
                title=r.get("title", ""),
                company=_company_from_url(url),
                raw_content=r.get("snippet", ""),
                source="serper",
                source_query=query,
            )
            if ok:
                pushed += 1
        time.sleep(0.4)

    print(f"  [Serper] pushed {pushed} raw discoveries from {len(queries)} queries")
    return pushed


# ── Reddit via crawlee ─────────────────────────────────────────────────────────

def run_reddit(seen: set) -> int:
    pushed = 0
    for source, keywords in REDDIT_SOURCES:
        resp = mcp_call(CRAWLEE_MCP, "trigger_scrape", source=source,
                        keywords=keywords, limit=25)
        for raw in resp.get("data", []):
            url = raw.get("website_url") or raw.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            ok = push_raw(
                url=url,
                title=raw.get("job_title") or raw.get("title") or "",
                company=raw.get("company_name") or _company_from_url(url),
                raw_content=(raw.get("description") or "")[:2000],
                source="reddit",
                source_query=keywords,
            )
            if ok:
                pushed += 1
        time.sleep(0.5)
    print(f"  [Reddit] pushed {pushed} raw discoveries")
    return pushed


# ── Greenhouse ATS ─────────────────────────────────────────────────────────────

def _is_track_a(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in TRACK_A_KEYWORDS)


def run_greenhouse(seen: set) -> int:
    import urllib.request
    pushed = 0
    for slug in GREENHOUSE_BOARDS:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            print(f"  [greenhouse/{slug}] error: {e}", file=sys.stderr)
            continue

        for job in data.get("jobs", []):
            title   = job.get("title", "")
            content = job.get("content", "")
            apply_url = job.get("absolute_url", "")
            if not apply_url or apply_url in seen:
                continue
            combined = f"{title} {content[:500]}"
            if not _is_track_a(combined):
                continue
            seen.add(apply_url)
            location = " ".join(o.get("name","") for o in job.get("offices",[]))
            ok = push_raw(
                url=apply_url,
                title=title,
                company=slug.capitalize(),
                raw_content=f"{location} | {content[:1500]}".strip(" |"),
                source="greenhouse",
                source_query=slug,
            )
            if ok:
                pushed += 1
        time.sleep(0.3)
    print(f"  [Greenhouse] pushed {pushed} raw discoveries from {len(GREENHOUSE_BOARDS)} boards")
    return pushed


# ── Direct platform pages ──────────────────────────────────────────────────────

def run_pages(seen: set) -> int:
    """Scrape known platform pages via Firecrawl → push raw content to staging."""
    pushed = 0
    for page in TRACK_A_PAGES:
        url = page["url"]
        if url in seen:
            continue
        resp = mcp_call(CRAWLEE_MCP, "scrape_url", url=url)
        content = (resp.get("content") or resp.get("text") or
                   resp.get("markdown") or "")
        if not content or len(content) < 100:
            time.sleep(0.5)
            continue
        seen.add(url)
        ok = push_raw(
            url=url,
            title=f"Jobs at {page['company']}",
            company=page["company"],
            raw_content=content[:2000],
            source="platform_page",
            source_query=url,
        )
        if ok:
            pushed += 1
        time.sleep(1.0)
    print(f"  [Pages] pushed {pushed} raw platform pages")
    return pushed


# ── Main ───────────────────────────────────────────────────────────────────────

def run_discovery(source: str = "all") -> None:
    start = datetime.now(timezone.utc)
    print(f"[{start.isoformat()}] Discovery starting — source={source}")

    seen: set[str] = set()
    total = 0

    if source in ("serper", "all"):
        queries = get_queries()
        total += run_serper(queries, seen)

    if source in ("reddit", "all"):
        total += run_reddit(seen)

    if source in ("ats", "all"):
        total += run_greenhouse(seen)

    if source in ("pages", "all"):
        total += run_pages(seen)

    print(f"[discovery] Total raw discoveries pushed: {total}")
    if total > 0:
        redis_signal_raw_ready()
        _tg(f"Discovery complete ({source}): {total} raw URLs pushed to staging.")
    else:
        print("[discovery] Nothing new — no signal sent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="all",
                        choices=["serper", "reddit", "ats", "pages", "all"],
                        help="Which source to run (default: all)")
    args = parser.parse_args()

    # Legacy positional arg support
    if len(sys.argv) > 1 and sys.argv[1] in ("discovery", "monitor"):
        args.source = "all"

    run_discovery(args.source)
