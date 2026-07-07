"""Debug: inspect video/source element attributes on Wikimedia Commons."""
import sys, time
sys.path.insert(0, r"E:\Corvus_Careebridge")
from playwright.sync_api import sync_playwright
from careerbridge.cdp_executor import CDPExecutor

URL = "https://commons.wikimedia.org/wiki/File:Big_Buck_Bunny_4_seconds_bird_clip.ogv"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=[
        "--remote-debugging-port=9222", "--no-first-run",
        "--disable-extensions",
    ])
    page = browser.new_page()
    print(f"Navigating to: {URL}")
    page.goto(URL, wait_until="networkidle")
    time.sleep(4)

    cdp = CDPExecutor()
    cdp.connect(port=9222)

    # Inspect all attributes on source elements
    attrs = cdp.eval_js("""
        Array.from(document.querySelectorAll('source')).map(function(s){
            var out = {};
            for (var i=0; i<s.attributes.length; i++) {
                out[s.attributes[i].name] = s.attributes[i].value;
            }
            return out;
        })
    """) or []
    print("SOURCE attributes:", attrs)

    # Also look for any text containing upload.wikimedia.org
    hrefs = cdp.eval_js("""
        Array.from(document.querySelectorAll('[href*="upload.wikimedia"], [src*="upload.wikimedia"]'))
            .map(function(el){ return el.href || el.src; })
            .filter(function(u){ return !!u; })
            .slice(0, 10)
    """) or []
    print("Wikimedia upload URLs:", hrefs)

    # Check page HTML for video URL patterns
    video_in_html = cdp.eval_js("""
        (function(){
            var html = document.body.innerHTML;
            var match = html.match(/https?:[^"'\\s]+\\.(?:webm|ogv|mp4|ogg)[^"'\\s]*/g);
            return match ? match.slice(0,5) : [];
        })()
    """) or []
    print("Video URL patterns in HTML:", video_in_html)

    cdp.disconnect()
    time.sleep(1)
    browser.close()
