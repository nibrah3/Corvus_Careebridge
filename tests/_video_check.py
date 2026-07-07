"""Find direct video download links on Wikimedia Commons."""
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
    print(f"Navigating: {URL}")
    page.goto(URL, wait_until="networkidle")
    time.sleep(5)

    cdp = CDPExecutor()
    cdp.connect(port=9222)

    # Look for all anchor hrefs that contain video file extensions
    video_links = cdp.eval_js("""
        Array.from(document.querySelectorAll('a[href]'))
            .map(function(a){ return a.href; })
            .filter(function(h){
                return /\\.(ogv|webm|mp4|ogg|avi|mov)(\\?|$)/i.test(h)
                    || /upload\\.wikimedia\\.org.*\\.(ogv|webm|mp4)/i.test(h);
            })
            .slice(0, 10)
    """) or []
    print("Direct video links:", video_links)

    # Also check the mw-filepage-filelinks section
    file_links = cdp.eval_js("""
        Array.from(document.querySelectorAll('.fullMedia a, .mw-filepage-other-resolutions a, #mw-filepage-content a'))
            .map(function(a){ return a.href; })
            .filter(function(h){ return h.includes('upload.wikimedia'); })
            .slice(0, 5)
    """) or []
    print("File section links:", file_links)

    # Scroll down and trigger video player
    page.evaluate("window.scrollTo(0, 300)")
    time.sleep(2)

    # Re-check video elements after scroll
    vids = cdp.eval_js("""
        (function(){
            var v = document.querySelector('video');
            if (!v) return {found: false};
            var sources = Array.from(v.querySelectorAll('source')).map(function(s){
                return {src: s.src || s.getAttribute('src'), type: s.type};
            });
            return {found: true, src: v.src, currentSrc: v.currentSrc, sources: sources};
        })()
    """)
    print("Video element state:", vids)

    cdp.disconnect()
    time.sleep(1)
    browser.close()
