"""Test video extraction on W3Schools HTML5 video demo."""
import sys, time
sys.path.insert(0, r"E:\Corvus_Careebridge")
from playwright.sync_api import sync_playwright
from careerbridge.cdp_executor import CDPExecutor

URL = "https://www.w3schools.com/html/html5_video.asp"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=[
        "--remote-debugging-port=9222", "--no-first-run",
        "--disable-extensions",
    ])
    page = browser.new_page()
    print(f"Navigating: {URL}")
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    cdp = CDPExecutor()
    cdp.connect(port=9222)

    vids = cdp.eval_js("""
        (function(){
            var results = [];
            document.querySelectorAll('video').forEach(function(v){
                var srcs = [];
                document.querySelectorAll('source[src]').forEach(function(s){ srcs.push(s.src); });
                results.push({videoSrc: v.src || '', currentSrc: v.currentSrc || '', sources: srcs});
            });
            return results;
        })()
    """) or []
    print("Video elements:", vids)

    # Also look for <source> elements with video types
    sources = cdp.eval_js("""
        Array.from(document.querySelectorAll('source[src]')).map(function(s){
            return {src: s.src, type: s.type || ''};
        })
    """) or []
    print("Source elements:", sources)

    title = cdp.eval_js("document.title") or ""
    print("Page title:", title)

    cdp.disconnect()
    time.sleep(1)
    browser.close()
