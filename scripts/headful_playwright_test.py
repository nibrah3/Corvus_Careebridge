"""
Headful Chrome + Playwright + Stealth evasion → Test WAF bypass.
Claude Code drives the browser.

Usage:
  python headful_playwright_test.py https://www.example.edu
"""
import asyncio
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def test_site(url: str):
    """Visit blocked site with Playwright headful + stealth."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed. Install with: pip install playwright")
        print("Then run: playwright install chromium")
        return False

    async with async_playwright() as p:
        try:
            log.info(f"Launching headful Chromium...")
            # Launch headful (visible window)
            browser = await p.chromium.launch(
                headless=False,  # HEADFUL - show the window
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )

            page = await context.new_page()

            # Inject stealth
            log.info("Injecting stealth evasion...")
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                window.chrome = { runtime: {} };
            """)

            # Navigate
            log.info(f"Navigating to {url}...")
            try:
                await page.goto(url, timeout=30000, wait_until="networkidle")
            except:
                log.warning("Timeout, proceeding with content...")
                pass

            # Get content
            content = await page.content()

            # Extract text
            from html.parser import HTMLParser
            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, data):
                    text = data.strip()
                    if text and len(text) > 3:
                        self.text.append(text)

            parser = TextExtractor()
            parser.feed(content)
            text = "\n".join(parser.text)[:5000]

            # Results
            await page.close()
            await context.close()
            await browser.close()

            if len(text.strip()) > 100:
                log.info(f"✅ SUCCESS: Retrieved {len(text)} chars of content")
                print("\n" + "="*60)
                print("RESULT: SUCCESS - Headful Chrome bypassed WAF")
                print("="*60)
                print(f"\nFirst 800 chars of content:\n")
                print(text[:800])
                print(f"\n[... total {len(text)} chars retrieved ...]")
                return True
            else:
                log.warning(f"❌ Minimal content returned ({len(text)} chars)")
                print("\n" + "="*60)
                print("RESULT: FAILED - Got minimal/blank content")
                print("="*60)
                return False

        except Exception as e:
            log.error(f"❌ Error: {e}")
            print("\n" + "="*60)
            print(f"RESULT: ERROR - {e}")
            print("="*60)
            return False


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python headful_playwright_test.py <URL>")
        print("Example: python headful_playwright_test.py https://www.example.edu")
        sys.exit(1)

    url = sys.argv[1]
    success = await test_site(url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
