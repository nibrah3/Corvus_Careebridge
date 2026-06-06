"""
Headful Chrome + CDP + Stealth → Test if we can bypass WAF on blocked schools.
Claude Code drives the browser automation.

Usage:
  python headful_cdp_test.py https://www.example-school.edu
"""
import asyncio
import json
import logging
import sys
import subprocess
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Launch Chrome with Debug Port ────────────────────────────────────────────

def launch_chrome_debug(debug_port: int = 9222) -> subprocess.Popen:
    """Launch headful Chrome with CDP debug port."""
    # Kill any existing Chrome
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)

    # Find Chrome binary
    chrome_paths = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path(f"{Path.home()}/AppData/Local/Google/Chrome/Application/chrome.exe"),
    ]
    chrome_exe = next((p for p in chrome_paths if p.exists()), None)

    if not chrome_exe:
        raise RuntimeError("Chrome not found. Install Chrome or update paths.")

    args = [
        str(chrome_exe),
        f"--remote-debugging-port={debug_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-component-update",
        "--disable-sync",
    ]

    log.info(f"Launching Chrome with --remote-debugging-port={debug_port}")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)  # Wait for Chrome to start
    return proc


# ── Pyppeteer Stealth Evasion ────────────────────────────────────────────────

async def inject_stealth(page):
    """Inject stealth scripts to avoid bot detection."""
    stealth_scripts = [
        # Override navigator.webdriver
        """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        """,
        # Override navigator.plugins
        """
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        """,
        # Override navigator.languages
        """
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        """,
        # Chrome detection
        """
        window.chrome = { runtime: {} };
        """,
        # Playwright/Puppeteer detection
        """
        delete navigator.__proto__.webdriver;
        """,
    ]

    for script in stealth_scripts:
        try:
            await page.evaluate(script)
        except:
            pass  # Ignore evaluation errors


# ── Visit Site via CDP ───────────────────────────────────────────────────────

async def visit_site_headful(url: str, debug_port: int = 9222) -> dict:
    """
    Visit site using headful Chrome + CDP + stealth evasion.
    Returns: {"success": bool, "content": str, "error": str}
    """
    try:
        from pyppeteer import connect
    except ImportError:
        return {
            "success": False,
            "content": "",
            "error": "pyppeteer not installed. Install with: pip install pyppeteer"
        }

    try:
        log.info(f"Connecting to Chrome on port {debug_port}...")
        browser = await connect(browserWSEndpoint=f"http://127.0.0.1:{debug_port}")

        page = await browser.newPage()

        # Set user agent
        await page.setUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        # Inject stealth
        log.info("Injecting stealth evasion...")
        await inject_stealth(page)

        # Navigate
        log.info(f"Navigating to {url}")
        try:
            await page.goto(url, {'waitUntil': 'networkidle2', 'timeout': 30000})
        except:
            # If networkidle times out, just proceed
            log.warning("Navigation timeout, proceeding with partial content")
            pass

        # Get content
        log.info("Extracting content...")
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
        text = "\n".join(parser.text)[:5000]  # First 5KB of text

        await page.close()
        await browser.disconnect()

        if len(text.strip()) > 100:
            log.info(f"SUCCESS: Got {len(text)} chars of content")
            return {"success": True, "content": text, "error": None}
        else:
            log.warning(f"Got minimal content ({len(text)} chars)")
            return {"success": False, "content": "", "error": "Page returned minimal content"}

    except Exception as e:
        log.error(f"Failed to visit site: {e}")
        return {"success": False, "content": "", "error": str(e)}


# ── Main Test ────────────────────────────────────────────────────────────────

async def main(url: str):
    """Test headful Chrome on a blocked school."""
    log.info(f"=== HEADFUL CHROME + CDP TEST ===")
    log.info(f"Target: {url}\n")

    # Launch Chrome
    chrome_proc = launch_chrome_debug(debug_port=9222)

    try:
        # Visit site
        result = await visit_site_headful(url, debug_port=9222)

        # Report
        print("\n=== RESULT ===")
        print(f"Success: {result['success']}")
        if result['success']:
            print(f"Content length: {len(result['content'])} chars")
            print(f"\nFirst 500 chars:")
            print(result['content'][:500])
        else:
            print(f"Error: {result['error']}")

        return result

    finally:
        # Clean up
        chrome_proc.terminate()
        chrome_proc.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python headful_cdp_test.py <URL>")
        print("Example: python headful_cdp_test.py https://www.example.edu")
        sys.exit(1)

    url = sys.argv[1]

    # Run async
    result = asyncio.run(main(url))

    # Exit code based on result
    sys.exit(0 if result['success'] else 1)
