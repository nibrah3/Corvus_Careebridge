"""Quick video extraction check on Wikimedia Commons."""
import sys, time
sys.path.insert(0, r"E:\Corvus_Careebridge")
from playwright.sync_api import sync_playwright
from careerbridge.cdp_executor import CDPExecutor

URL = "https://commons.wikimedia.org/wiki/File:Big_Buck_Bunny_4_seconds_bird_clip.ogv"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=[
        "--remote-debugging-port=9222", "--no-first-run",
        "--disable-extensions", "--disable-background-networking",
    ])
    page = browser.new_page()
    print(f"Navigating to: {URL}")
    page.goto(URL, wait_until="domcontentloaded")
    time.sleep(3)

    cdp = CDPExecutor()
    cdp.connect(port=9222)

    videos = cdp.eval_js("""
        (function(){
            var urls = [];
            document.querySelectorAll('video[src]').forEach(function(v){ if(v.src) urls.push(v.src); });
            document.querySelectorAll('video source[src]').forEach(function(s){ if(s.src) urls.push(s.src); });
            document.querySelectorAll('video[data-src], source[data-src]').forEach(function(el){
                var u = el.getAttribute('data-src');
                if(u) urls.push(u.startsWith('http') ? u : location.origin + u);
            });
            return urls.filter(function(u,i,a){ return a.indexOf(u)===i; });
        })()
    """) or []

    print(f"Found {len(videos)} video URL(s):")
    for u in videos:
        print(f"  {u}")

    if not videos:
        # Check all video/source tags (even without src)
        tags = cdp.eval_js("""
            Array.from(document.querySelectorAll('video, source')).map(function(el){
                return el.tagName + ' src=' + (el.src || el.getAttribute('src') || el.getAttribute('data-src') || '(none)');
            })
        """) or []
        print("All video/source tags:", tags[:10])

    cdp.disconnect()
    time.sleep(1)
    browser.close()
