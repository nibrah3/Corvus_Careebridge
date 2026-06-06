"""
Blocked schools crawler using CDPExecutor + Claude + humanizer.
Same pattern as assessment_pipeline — no puppeteer needed.

Claude Code drives:
  1. Launch headful Chrome (port 9222)
  2. CDPExecutor connects to CDP
  3. Claude analyzes page content
  4. Extract enrollment text
"""
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

sys.path.insert(0, r"E:\Corvus_Careebridge")
os.chdir(r"E:\Corvus_Careebridge")

# Load .env
for line in open(".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

from careerbridge.cdp_executor import CDPExecutor, CDPError


def launch_chrome_debug() -> subprocess.Popen:
    """Launch headful Chrome with debug port."""
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)

    chrome_paths = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path(f"{Path.home()}/AppData/Local/Google/Chrome/Application/chrome.exe"),
    ]
    chrome_exe = next((p for p in chrome_paths if p.exists()), None)

    if not chrome_exe:
        raise RuntimeError("Chrome not found")

    args = [
        str(chrome_exe),
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    log.info("Launching headful Chrome on port 9222...")
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def visit_school_cdp(school: dict) -> dict:
    """
    Visit school using CDPExecutor (assessment pipeline pattern).
    Returns: {"id": int, "name": str, "url": str, "raw_text": str, "success": bool}
    """
    school_id = school["id"]
    name = school["name"]
    url = school["url"]

    log.info(f"[{school_id}] Visiting {name}")

    try:
        # Connect to Chrome via CDP
        executor = CDPExecutor()
        executor.connect(port=9222)  # auto-discover or use 9222

        # Inject stealth (same as assessment pipeline)
        executor.inject_stealth()

        # Navigate
        executor.navigate(url, timeout=15)
        log.info(f"[{school_id}] Page loaded")

        # Get accessibility tree (semantic DOM)
        axtree = executor.get_axtree()

        # Extract visible text from tree
        def extract_text_from_tree(tree):
            """Recursively extract text from accessibility tree."""
            text = []
            for node in tree:
                if node.get("name"):
                    text.append(node["name"])
                if "childIds" in node:
                    # Fetch children (if needed)
                    pass
            return " ".join(text)

        # Also get raw content as fallback
        content = executor.eval_js("document.documentElement.outerHTML")
        if isinstance(content, str) and len(content) > 100:
            # Extract text from HTML
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
            try:
                parser.feed(content)
                text = "\n".join(parser.text)[:5000]
            except:
                text = ""
        else:
            text = ""

        executor.disconnect()

        if len(text.strip()) > 100:
            log.info(f"[{school_id}] ✅ SUCCESS: {len(text)} chars")
            return {
                "id": school_id,
                "name": name,
                "url": url,
                "raw_text": text,
                "success": True,
            }
        else:
            log.warning(f"[{school_id}] ⚠️ Minimal content")
            return {
                "id": school_id,
                "name": name,
                "url": url,
                "raw_text": "",
                "success": False,
            }

    except CDPError as e:
        log.error(f"[{school_id}] CDP error: {e}")
        return {"id": school_id, "name": name, "url": url, "raw_text": "", "success": False}
    except Exception as e:
        log.error(f"[{school_id}] Error: {e}")
        return {"id": school_id, "name": name, "url": url, "raw_text": "", "success": False}


def main():
    """Test on one blocked school."""
    # Get a test school
    import psycopg2
    conn = psycopg2.connect(
        "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge",
        connect_timeout=10,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, name, url FROM raw_schools
        WHERE (raw_text IS NULL OR length(raw_text)<=50)
        AND url LIKE 'https://%'
        ORDER BY RANDOM() LIMIT 1
    """)
    school = dict(cur.fetchone() or {})
    conn.close()

    if not school:
        log.error("No test school found")
        return

    log.info(f"=== TESTING CDPExecutor ON BLOCKED SCHOOL ===\n")
    log.info(f"School: {school['name']}")
    log.info(f"URL: {school['url']}\n")

    # Launch Chrome
    chrome_proc = launch_chrome_debug()

    try:
        # Wait longer for Chrome to fully start CDP server
        log.info("Waiting for Chrome CDP to be ready...")
        for i in range(15):
            try:
                import urllib.request
                urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=1)
                log.info("Chrome CDP ready!")
                break
            except:
                time.sleep(0.5)

        # Visit school
        result = visit_school_cdp(school)

        # Report
        print("\n" + "="*60)
        if result["success"]:
            print("[SUCCESS] CDPExecutor bypassed WAF!")
            print("="*60)
            print(f"School: {result['name']}")
            print(f"URL: {result['url']}")
            print(f"Content retrieved: {len(result['raw_text'])} chars")
            print(f"\nFirst 500 chars:\n{result['raw_text'][:500]}")
        else:
            print("[FAILED] No content retrieved")
            print("="*60)

    finally:
        chrome_proc.terminate()
        chrome_proc.wait(timeout=5)


if __name__ == "__main__":
    import psycopg2
    import psycopg2.extras
    main()
