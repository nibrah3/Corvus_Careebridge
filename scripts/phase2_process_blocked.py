#!/usr/bin/env python3
"""
Phase 2: Process 722 blocked schools using residential proxies + Claude analysis.

One-shot visit per school via IPRoyal residential proxy. Analyze each for 6 criteria
in Claude context. Save results to database in batches.

Usage:
    python scripts/phase2_process_blocked.py [--batch-size 15] [--max-batches 999]
"""

import sys
import os
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

CB_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(CB_DIR))

# Load .env
for line in open(CB_DIR / ".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

import psycopg2
import psycopg2.extras
from anthropic import Anthropic

DSN = "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge"
PROXY = "http://8EDYlP4dRd06CJAc:OcY1ARnMZVwkNTGV_country-us@geo.iproyal.com:12321"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
}

def fetch_batch(limit=15):
    """Fetch batch of blocked schools from DB."""
    conn = psycopg2.connect(DSN, connect_timeout=10)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, name, url, city, state FROM raw_schools "
            "WHERE (raw_text IS NULL OR length(raw_text) <= 50) "
            "AND analyzed = FALSE "
            "ORDER BY id "
            "LIMIT %s",
            (limit,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        conn.close()

def visit_school(url: str) -> str | None:
    """Visit school URL once via residential proxy. Returns HTML content or None."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    session.proxies = {"http": PROXY, "https": PROXY}

    retry = Retry(total=1, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        response = session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        return response.text[:50000]
    except Exception as e:
        print(f"    ERROR: {e}", file=sys.stderr)
        return None
    finally:
        session.close()

def extract_text(html: str) -> str:
    """Extract plain text from HTML."""
    if not html:
        return "(No content)"

    import re
    from html.parser import HTMLParser

    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
        def handle_data(self, data):
            text = data.strip()
            if text and len(text) > 3:
                self.text.append(text)

    parser = TextExtractor()
    try:
        parser.feed(html)
        text = "\n".join(parser.text)[:10000]
        return text if text.strip() else "(Empty page)"
    except:
        return "(Parse error)"

def analyze_schools(batch):
    """Analyze schools using Claude API."""
    client = Anthropic()
    results = []

    for i, school in enumerate(batch, 1):
        school_id = school["id"]
        name = school["name"]
        url = school["url"]

        print(f"  [{i}/{len(batch)}] {name[:50]}...", end="", flush=True, file=sys.stderr)

        # Visit school
        html = visit_school(url)
        if not html:
            print(" SKIP (no content)", file=sys.stderr)
            continue

        text_content = extract_text(html)

        # Analyze with Claude
        prompt = f"""Analyze this school's website for enrollment criteria.

School: {name}
URL: {url}

Content (first 8000 chars):
{text_content[:8000]}

For each of these 6 criteria, determine if it's EXPLICITLY STATED or clearly implied:

1. community_college: community/two-year college? (or "Community College" in name?)
2. no_id_verification: no government ID required?
3. no_transcript_required: no transcripts/prior records needed?
4. monthly_enrollment: rolling/monthly enrollment (not just fixed terms)?
5. instant_acceptance: same-day or 24-hour acceptance?
6. monthly_refund: monthly/pro-rated refund policy?

Respond with ONLY this JSON (no other text):
{{"community_college":true/false,"no_id_verification":true/false,"no_transcript_required":true/false,"monthly_enrollment":true/false,"instant_acceptance":true/false,"monthly_refund":true/false}}"""

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text.strip()

            # Extract JSON
            import json as json_lib
            try:
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    criteria = json_lib.loads(response_text[json_start:json_end])
                else:
                    raise ValueError("No JSON found")
            except:
                print(f" ERROR (parsing)", file=sys.stderr)
                continue

            score = sum(1 for v in criteria.values() if v)

            result = {
                "id": school_id,
                "name": name,
                "url": url,
                "city": school.get("city", ""),
                "state": school.get("state", ""),
                "is_community_college": criteria.get("community_college", False),
                "community_college": criteria.get("community_college", False),
                "no_id_verification": criteria.get("no_id_verification", False),
                "no_transcript_required": criteria.get("no_transcript_required", False),
                "monthly_enrollment": criteria.get("monthly_enrollment", False),
                "instant_acceptance": criteria.get("instant_acceptance", False),
                "monthly_refund": criteria.get("monthly_refund", False),
            }

            results.append(result)
            print(f" OK ({score}/6)", file=sys.stderr)

        except Exception as e:
            print(f" ERROR ({e})", file=sys.stderr)
            continue

    return results

def save_results(results):
    """Save analysis results to database."""
    if not results:
        return {"saved": 0, "skipped": 0}

    save_script = CB_DIR / "scripts" / "school_gate_save.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(save_script)],
            input=json.dumps(results).encode(),
            capture_output=True,
            timeout=30,
            cwd=str(CB_DIR),
        )

        if proc.returncode == 0:
            save_result = json.loads(proc.stdout.decode())
            return save_result
        else:
            print(f"  Save error: {proc.stderr.decode()}", file=sys.stderr)
            return {"saved": 0, "skipped": 0}
    except Exception as e:
        print(f"  Save failed: {e}", file=sys.stderr)
        return {"saved": 0, "skipped": 0}

def main():
    parser = argparse.ArgumentParser(description="Phase 2: Process blocked schools")
    parser.add_argument("--batch-size", type=int, default=15, help="Schools per batch")
    parser.add_argument("--max-batches", type=int, default=999, help="Max batches to process")
    args = parser.parse_args()

    print(f"Phase 2: Processing blocked schools", file=sys.stderr)
    print(f"Start: {datetime.now().isoformat()}", file=sys.stderr)
    print(f"=" * 60, file=sys.stderr)

    total_saved = 0
    total_analyzed = 0
    batch_num = 0

    while batch_num < args.max_batches:
        batch = fetch_batch(limit=args.batch_size)

        if not batch:
            print(f"No more schools to process.", file=sys.stderr)
            break

        batch_num += 1
        print(f"\nBatch {batch_num}: {len(batch)} schools", file=sys.stderr)

        # Visit and analyze
        results = analyze_schools(batch)
        total_analyzed += len(results)

        if results:
            print(f"  Saving {len(results)} results...", end="", flush=True, file=sys.stderr)
            save_result = save_results(results)
            total_saved += save_result.get("saved", 0)
            print(f" Done (saved: {save_result.get('saved', 0)})", file=sys.stderr)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"Total analyzed: {total_analyzed}", file=sys.stderr)
    print(f"Total saved: {total_saved}", file=sys.stderr)
    print(f"End: {datetime.now().isoformat()}", file=sys.stderr)

if __name__ == "__main__":
    main()
