"""
Phase 1c — Anti-bot retry for 722 blocked schools.
Uses IXBrowser profiles + IPRoyal proxies + headful Playwright for evasion.
Retries schools where Firecrawl got <50 chars (JS-heavy, anti-bot blocked).
"""
import json, logging, os, sys, time, hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Logging
LOG_DIR = Path("/opt/corvus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(str(LOG_DIR / "crawl_antibot.log"), encoding="utf-8")],
)
log = logging.getLogger(__name__)

sys.path.insert(0, "/opt/corvus")
os.chdir("/opt/corvus")

# Load env
for line in open(".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

import psycopg2, psycopg2.extras
from ixbrowser_local_api import IXBrowserClient, entities

# ── Anti-Bot Evasion Config ────────────────────────────────────────────────────

COUNTRIES = ["US", "GB", "CA", "AU", "DE"]  # Rotate across proxies
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

MAX_WORKERS = 4  # Conservative — don't hammer VPS
REQUEST_TIMEOUT = 30  # seconds
RETRY_DELAY = 3  # seconds between retries

DSN = "postgresql://corvus:corvus-local-password@127.0.0.1:5432/careerbridge"

# ── Helper: IXBrowser Profile + Proxy ──────────────────────────────────────────

def _create_ixbrowser_profile(country: str, school_name: str) -> int | None:
    """Create or reuse an IXBrowser profile with the given country proxy."""
    try:
        client = IXBrowserClient()

        # Create profile with unique name
        profile = entities.Profile()
        profile.name = f"school_{country}_{int(time.time())}"
        result = client.create_profile(profile)

        if result is None:
            log.warning(f"IXBrowser profile creation failed: {client.message}")
            return None

        profile_id = int(result)

        # Attach country-specific proxy (IPRoyal)
        # proxy_manager.py handles the credentials
        from proxy_manager import iproyal_proxy_config
        cfg = iproyal_proxy_config(country, proxy_type="socks5")

        r = client.update_profile_to_custom_proxy_mode(
            profile_id=profile_id,
            proxy_type=cfg["proxy_type"],
            proxy_ip=cfg["proxy_host"],
            proxy_port=cfg["proxy_port"],
            proxy_user=cfg["proxy_user"],
            proxy_password=cfg["proxy_password"],
        )

        log.info(f"IXBrowser profile {profile_id} created with {country} proxy")
        return profile_id
    except Exception as e:
        log.error(f"IXBrowser profile creation failed: {e}")
        return None


def _crawl_with_ixbrowser(profile_id: int, url: str, timeout: int = 30) -> str:
    """
    Use IXBrowser's Playwright integration to crawl via the profile.
    Returns raw HTML or empty string on failure.
    """
    try:
        from ixbrowser_local_api import IXBrowserClient
        client = IXBrowserClient()

        # Get browser context via IXBrowser
        # This is pseudocode — adapt to actual IXBrowser Playwright API
        browser = client.start_browser(profile_id=profile_id, headless=False)
        page = browser.new_page()

        page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
        content = page.content()

        page.close()
        browser.close()

        return content
    except Exception as e:
        log.warning(f"IXBrowser crawl failed for {url}: {e}")
        return ""


def _crawl_school(school: dict) -> dict:
    """
    Attempt to crawl a school with anti-bot evasion.
    Returns: {"id": id, "name": name, "url": url, "raw_text": text, "crawled": bool, "error": str}
    """
    school_id = school["id"]
    name = school["name"]
    url = school["url"]

    log.info(f"[{school_id}] Attempting anti-bot crawl: {name}")

    # Try 2 profiles (different countries/proxies) per school
    for attempt in range(2):
        country = COUNTRIES[attempt % len(COUNTRIES)]

        # Create fresh profile with country proxy
        profile_id = _create_ixbrowser_profile(country, name)
        if not profile_id:
            continue

        try:
            # Crawl via IXBrowser's real browser
            html = _crawl_with_ixbrowser(profile_id, url, timeout=REQUEST_TIMEOUT)

            if html and len(html.strip()) > 100:
                # Extract text from HTML
                from html.parser import HTMLParser
                class TextExtractor(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.text = []
                    def handle_data(self, data):
                        self.text.append(data)

                parser = TextExtractor()
                parser.feed(html)
                text = " ".join(parser.text).strip()[:50000]

                if len(text) > 50:
                    log.info(f"[{school_id}] SUCCESS — {len(text)} chars via {country} proxy")
                    return {
                        "id": school_id,
                        "name": name,
                        "url": url,
                        "raw_text": text,
                        "crawled": True,
                        "error": None,
                    }

            log.debug(f"[{school_id}] Attempt {attempt+1}: Got {len(html)} chars, retrying")

        except Exception as e:
            log.debug(f"[{school_id}] Attempt {attempt+1} error: {e}")

        time.sleep(RETRY_DELAY)

    log.warning(f"[{school_id}] FAILED after 2 attempts")
    return {
        "id": school_id,
        "name": name,
        "url": url,
        "raw_text": "",
        "crawled": False,
        "error": "Anti-bot evasion failed — site too aggressive",
    }


def _fetch_blocked_schools() -> list[dict]:
    """Get all schools where Firecrawl failed (raw_text NULL or <50 chars)."""
    try:
        conn = psycopg2.connect(DSN, connect_timeout=10)
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, url
                    FROM raw_schools
                    WHERE (raw_text IS NULL OR length(raw_text) <= 50)
                    ORDER BY id
                """)
                rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Failed to fetch blocked schools: {e}")
        return []


def _save_crawl(school: dict) -> bool:
    """Save crawl result back to raw_schools."""
    if not school.get("raw_text"):
        return False

    try:
        conn = psycopg2.connect(DSN, connect_timeout=10)
        with conn:
            with conn.cursor() as cur:
                url_hash = hashlib.md5((school["url"] or "").encode()).hexdigest()
                cur.execute("""
                    UPDATE raw_schools SET raw_text=%s, crawled_at=NOW()
                    WHERE id=%s
                """, (school["raw_text"][:50000], school["id"]))
        conn.close()
        return True
    except Exception as e:
        log.error(f"Save failed for {school['id']}: {e}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log.info("Anti-bot retry crawler starting...")

    schools = _fetch_blocked_schools()
    log.info(f"Found {len(schools)} blocked schools to retry")

    crawled = 0
    recovered = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_crawl_school, s): s for s in schools}

        for future in as_completed(futures):
            try:
                result = future.result()
                crawled += 1

                if result.get("crawled"):
                    if _save_crawl(result):
                        recovered += 1
                else:
                    errors += 1

                if crawled % 25 == 0:
                    log.info(f"Progress: {crawled}/{len(schools)} | Recovered: {recovered}")

            except Exception as e:
                log.error(f"Worker error: {e}")
                errors += 1

    log.info(f"Anti-bot crawl complete: {crawled} attempted, {recovered} recovered, {errors} failed")
    log.info(f"Next: Re-run Phase 2 analysis on newly recovered schools")


if __name__ == "__main__":
    main()
