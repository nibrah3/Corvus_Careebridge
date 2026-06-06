"""
Phase 2: Process 722 blocked schools using residential proxies + Claude analysis.

One-shot visit per school via IPRoyal residential proxy. Analyze each for 6 criteria
in Claude context. Save results to database.

Loop:
  1. Fetch batch of 15 blocked schools (raw_text NULL or <50 chars)
  2. Visit each via residential proxy + extract enrollment info
  3. Analyze in Claude context for 6 criteria
  4. Save results via school_gate_save.py
  5. Repeat until batch count == 0

Usage:
    python scripts/phase2_blocked_loop.py
"""

import sys
import os
import json
import subprocess
from pathlib import Path

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

# IPRoyal residential proxy (single IP per batch, one-shot visits)
PROXY = "http://8EDYlP4dRd06CJAc:OcY1ARnMZVwkNTGV_country-us@geo.iproyal.com:12321"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

def fetch_batch(limit=15):
    """Fetch batch of blocked schools from DB."""
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id, name, url FROM raw_schools "
            "WHERE (raw_text IS NULL OR length(raw_text) <= 50) "
            "AND analyzed = FALSE "
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
        return response.text[:50000]  # First 50KB
    except Exception as e:
        print(f"  Failed to visit {url}: {e}", file=sys.stderr)
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
    """
    Analyze schools using Claude API.

    For each school, Claude evaluates:
      - community_college: community/two-year college OR name has "Community College"
      - no_id_verification: no government ID needed
      - no_transcript_required: no transcripts/records needed
      - monthly_enrollment: rolling or monthly start dates
      - instant_acceptance: same-day or 24h acceptance
      - monthly_refund: monthly or pro-rated refund
    """
    client = Anthropic()

    results = []

    for i, school in enumerate(batch, 1):
        school_id = school["id"]
        name = school["name"]
        url = school["url"]

        print(f"\n[{i}/{len(batch)}] Visiting {name}...", file=sys.stderr)

        # Visit school via residential proxy
        html = visit_school(url)
        if not html:
            print(f"  Skipped (no content)", file=sys.stderr)
            continue

        # Extract enrollment info
        text_content = extract_text(html)

        print(f"  Analyzing...", file=sys.stderr)

        # Ask Claude to evaluate the 6 criteria
        prompt = f"""Analyze this school's website content for enrollment criteria.

School: {name}
URL: {url}

Content:
{text_content[:8000]}

Evaluate these 6 criteria (TRUE = explicitly stated, FALSE = not found or explicitly stated as not required):

1. community_college: Is this a community college or two-year college? (Also TRUE if name contains "Community College")
2. no_id_verification: No government ID required for enrollment?
3. no_transcript_required: No transcripts or prior records required?
4. monthly_enrollment: Accepts students monthly or on rolling basis (not fixed terms)?
5. instant_acceptance: Same-day or within 24 hours acceptance decision?
6. monthly_refund: Monthly or pro-rated refund policy (not just full refund)?

IMPORTANT: Only mark TRUE if explicitly stated or clearly implied. If you can't find evidence, mark FALSE.

Respond with ONLY valid JSON (no other text):
{{
  "community_college": true/false,
  "no_id_verification": true/false,
  "no_transcript_required": true/false,
  "monthly_enrollment": true/false,
  "instant_acceptance": true/false,
  "monthly_refund": true/false
}}"""

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse Claude's response
            response_text = response.content[0].text.strip()

            # Extract JSON from response
            import json as json_lib
            json_match = response_text[response_text.find("{"):response_text.rfind("}")+1]
            criteria = json_lib.loads(json_match)

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

            score = sum([
                criteria.get("community_college", False),
                criteria.get("no_id_verification", False),
                criteria.get("no_transcript_required", False),
                criteria.get("monthly_enrollment", False),
                criteria.get("instant_acceptance", False),
                criteria.get("monthly_refund", False),
            ])

            print(f"  Score: {score}/6 {list(k for k,v in criteria.items() if v)}", file=sys.stderr)
            results.append(result)

        except Exception as e:
            print(f"  Claude analysis failed: {e}", file=sys.stderr)
            continue

    return results

def save_results(results):
    """Save analysis results to database via school_gate_save.py."""
    if not results:
        print("No results to save.", file=sys.stderr)
        return {"saved": 0, "skipped": 0}

    print(f"\nSaving {len(results)} results...", file=sys.stderr)

    # Call school_gate_save.py with results as stdin
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
            print(f"  Saved: {save_result.get('saved', 0)}, Skipped: {save_result.get('score_zero', 0)}", file=sys.stderr)
            return save_result
        else:
            print(f"  Save error: {proc.stderr.decode()}", file=sys.stderr)
            return {"saved": 0, "skipped": 0}
    except Exception as e:
        print(f"  Failed to call save script: {e}", file=sys.stderr)
        return {"saved": 0, "skipped": 0}

def main():
    print("Phase 2: Processing blocked schools", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    total_saved = 0
    batch_num = 1

    while True:
        batch = fetch_batch(limit=15)

        if not batch:
            print(f"\nNo more schools to process.", file=sys.stderr)
            break

        print(f"\nBatch {batch_num}: {len(batch)} schools", file=sys.stderr)

        # Visit and analyze
        results = analyze_schools(batch)

        if results:
            # Save to database
            save_result = save_results(results)
            total_saved += save_result.get("saved", 0)

        batch_num += 1

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"Total saved: {total_saved}", file=sys.stderr)

    # Final status
    status_script = CB_DIR / "scripts" / "school_gate_status.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(status_script)],
            capture_output=True,
            timeout=10,
            cwd=str(CB_DIR),
        )
        if proc.returncode == 0:
            status = json.loads(proc.stdout.decode())
            print(f"\nFinal status: {status}", file=sys.stderr)
    except:
        pass

if __name__ == "__main__":
    main()
