"""
One-shot school visitor + analyzer using Claude + residential proxies.
Visits each blocked school once via IPRoyal residential IP.
Extracts enrollment info and scores against 6 criteria in Claude context.
No fingerprint rotation needed — single visit per site from "different users" (subagents).
"""
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Residential proxy from IPRoyal
PROXY = "http://8EDYlP4dRd06CJAc:OcY1ARnMZVwkNTGV_country-us@geo.iproyal.com:12321"

# Headers that look like normal browser (not bot)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

TIMEOUT = 15  # seconds
MAX_RETRIES = 1  # One visit only


def visit_school(url: str) -> str | None:
    """
    Visit school URL once via residential proxy.
    Returns: HTML content (up to 50KB) or None on failure.
    """
    session = requests.Session()

    # Configure proxies
    session.proxies = {
        "http": PROXY,
        "https": PROXY,
    }

    # Add retry logic for transient network issues (but only 1 visit per site)
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()

        # Return first 50KB of content (enough for enrollment info)
        return response.text[:50000]

    except Exception as e:
        print(f"Failed to visit {url}: {e}")
        return None
    finally:
        session.close()


def extract_enrollment_info(html: str) -> str:
    """
    Extract human-readable enrollment information from HTML.
    Claude will interpret this in context.
    """
    if not html:
        return "(No content)"

    # Remove scripts and styles
    import re
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

    # Extract text
    from html.parser import HTMLParser
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
        def handle_data(self, data):
            text = data.strip()
            if text and len(text) > 3:  # Skip tiny fragments
                self.text.append(text)

    parser = TextExtractor()
    parser.feed(html)
    text = "\n".join(parser.text)[:10000]  # First 10KB of text

    return text if text.strip() else "(Empty page)"


if __name__ == "__main__":
    # Test
    url = "https://www.example.com"
    html = visit_school(url)
    if html:
        info = extract_enrollment_info(html)
        print(json.dumps({"url": url, "content": info[:500]}))
    else:
        print(json.dumps({"url": url, "error": "Failed to fetch"}))
