"""Test video extraction on YouTube (uses HTML5 <video> element)."""
import sys, time
sys.path.insert(0, r"E:\Corvus_Careebridge")
from playwright.sync_api import sync_playwright
from careerbridge.cdp_executor import CDPExecutor

# First YouTube video ever uploaded — short, public, no age restriction
URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=[
        "--remote-debugging-port=9222", "--no-first-run",
        "--disable-extensions",
        "--autoplay-policy=no-user-gesture-required",
    ])
    page = browser.new_page()
    print(f"Navigating: {URL}")
    page.goto(URL, wait_until="domcontentloaded")
    time.sleep(5)  # YouTube JS player needs time to initialize

    cdp = CDPExecutor()
    cdp.connect(port=9222)

    # Extract video title
    title = cdp.eval_js("""
        (function(){
            var el = document.querySelector('h1.ytd-watch-metadata yt-formatted-string, h1.title, h1');
            return el ? el.innerText.trim() : '';
        })()
    """) or ""
    print(f"Title : {title!r}")

    # Extract video element
    vstate = cdp.eval_js("""
        (function(){
            var v = document.querySelector('video');
            if (!v) return {found: false};
            return {
                found: true,
                src: v.src || '',
                currentSrc: v.currentSrc || '',
                readyState: v.readyState,
                duration: v.duration
            };
        })()
    """) or {}
    print(f"Video : {vstate}")

    # Extract OG metadata (thumbnail, title)
    og = cdp.eval_js("""
        (function(){
            var img = document.querySelector('meta[property=\"og:image\"]');
            var ttl = document.querySelector('meta[property=\"og:title\"]');
            return {
                thumbnail: img ? img.content : '',
                title: ttl ? ttl.content : ''
            };
        })()
    """) or {}
    print(f"OG    : {og}")

    cdp.disconnect()
    time.sleep(1)
    browser.close()
