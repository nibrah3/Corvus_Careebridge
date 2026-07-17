"""
Alternative: Pure Playwright + proxy rotation + anti-bot flags.
Works if IXBrowser integration is complex. Falls back to browser-level evasion.
"""
import asyncio, json, logging, os, sys, time, hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

LOG_DIR = Path("/opt/corvus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(str(LOG_DIR / "crawl_playwright.log"), encoding="utf-8")],
)
log = logging.getLogger(__name__)

sys.path.insert(0, "/opt/corvus")
os.chdir("/opt/corvus")

for line in open(".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

import psycopg2, psycopg2.extras
from playwright.async_api import async_playwright

DSN = "postgresql://corvus:corvus-local-password@127.0.0.1:5432/careerbridge"

# ── Anti-Bot Evasion Flags ────────────────────────────────────────────────────

PROXY_LIST = [
    # IPRoyal SOCKS5 proxies (country rotation via password)
    "socks5://8EDYlP4dRd06CJAc:OcY1ARnMZVwkNTGV_country-us@geo.iproyal.com:12323",
    "socks5://8EDYlP4dRd06CJAc:OcY1ARnMZVwkNTGV_country-gb@geo.iproyal.com:12323",
    "socks5://8EDYlP4dRd06CJAc:OcY1ARnMZVwkNTGV_country-ca@geo.iproyal.com:12323",
    "socks5://8EDYlP4dRd06CJAc:OcY1ARnMZVwkNTGV_country-de@geo.iproyal.com:12323",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

REQUEST_TIMEOUT = 30000  # ms


async def _crawl_with_playwright(school: dict, proxy: str, user_agent: str) -> dict:
    """
    Use Playwright with:
    - Real headful browser (Chrome, not headless)
    - Rotating proxy (residential IPs via IPRoyal)
    - Rotating user-agent
    - Anti-bot headers (Referer, Accept-Language, etc.)
    """
    async with async_playwright() as p:
        try:
            # Launch browser with headful mode (real browser, harder to detect)
            # chrome_headless=False would be ideal but requires display
            # Instead use stealth mode flags
            browser = await p.chromium.launch(
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
                proxy={"server": proxy} if proxy else None,
            )

            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
            )

            # Inject anti-detection script
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
            """)

            page = await context.new_page()

            # Set realistic headers
            await page.set_extra_http_headers({
                "Referer": "https://www.google.com/",
                "Accept-Language": "en-US,en;q=0.9",
                "DNT": "1",
            })

            # Navigate and wait for network idle
            await page.goto(
                school["url"],
                timeout=REQUEST_TIMEOUT,
                wait_until="networkidle",
            )

            # Get text content
            content = await page.content()

            # Clean up
            await context.close()
            await browser.close()

            # Extract text
            from html.parser import HTMLParser
            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, data):
                    self.text.append(data)

            parser = TextExtractor()
            parser.feed(content)
            text = " ".join(parser.text).strip()[:50000]

            if len(text) > 50:
                log.info(f"[{school['id']}] Recovered {len(text)} chars")
                return {
                    "id": school["id"],
                    "name": school["name"],
                    "url": school["url"],
                    "raw_text": text,
                    "crawled": True,
                }
            else:
                log.debug(f"[{school['id']}] Content too short")
                return {"id": school["id"], "crawled": False}

        except Exception as e:
            log.debug(f"[{school['id']}] Playwright error: {e}")
            return {"id": school["id"], "crawled": False, "error": str(e)}


async def _retry_school(school: dict) -> dict:
    """Try 2 proxies and 2 user-agents per school."""
    for attempt in range(2):
        proxy = PROXY_LIST[attempt % len(PROXY_LIST)]
        ua = USER_AGENTS[attempt % len(USER_AGENTS)]

        result = await _crawl_with_playwright(school, proxy, ua)
        if result.get("crawled"):
            return result
        await asyncio.sleep(3)

    return {"id": school["id"], "crawled": False}


def _fetch_blocked_schools() -> list[dict]:
    """Get all schools where Firecrawl failed."""
    try:
        conn = psycopg2.connect(DSN, connect_timeout=10)
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, url FROM raw_schools
                    WHERE (raw_text IS NULL OR length(raw_text) <= 50)
                """)
                rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Fetch error: {e}")
        return []


def _save_crawl(school: dict) -> bool:
    """Save to raw_schools."""
    if not school.get("raw_text"):
        return False
    try:
        conn = psycopg2.connect(DSN, connect_timeout=10)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE raw_schools SET raw_text=%s, crawled_at=NOW() WHERE id=%s",
                    (school["raw_text"][:50000], school["id"]),
                )
        conn.close()
        return True
    except Exception as e:
        log.error(f"Save error for {school['id']}: {e}")
        return False


async def main():
    log.info("Playwright anti-bot crawler starting...")
    schools = _fetch_blocked_schools()
    log.info(f"Retrying {len(schools)} blocked schools")

    recovered = 0
    # Process in batches to avoid overwhelming resources
    batch_size = 4
    for i in range(0, len(schools), batch_size):
        batch = schools[i : i + batch_size]
        results = await asyncio.gather(*[_retry_school(s) for s in batch])

        for result in results:
            if result.get("crawled"):
                if _save_crawl(result):
                    recovered += 1

        log.info(f"Batch {i//batch_size + 1}: {recovered} recovered so far")

    log.info(f"Complete: {recovered}/{len(schools)} schools recovered")


if __name__ == "__main__":
    asyncio.run(main())
