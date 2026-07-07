"""
Real-world annotation pipeline demo.

Demonstrates CDPExecutor extraction + Gemini vision on real public websites,
in a continuous loop — matching the production annotation workflow.

Architecture:
  Playwright  -> open browser, navigate to real sites (human simulation layer)
  CDPExecutor -> extract text, image URLs, video URLs (our system under test)
  pynput      -> physical OS mouse movement (visible on screen, isTrusted=true)
  Gemini      -> classify/describe extracted content

Loop:
  Task 1/3 — Cat     (Wikipedia) : text + image -> Gemini
  Task 2/3 — Dog     (Wikipedia) : text + image -> Gemini
  Task 3/3 — Eagle   (Wikipedia) : text + image -> Gemini
  Video     — Animation (Wikipedia) : video URL extraction

Run:
    C:\\Python314\\python.exe E:\\Corvus_Careebridge\\tests\\real_world_demo.py
"""
from __future__ import annotations

import sys
import time
from typing import Optional

sys.path.insert(0, r"E:\Corvus_Careebridge")


ANNOTATION_TASKS = [
    {
        "name": "Cat",
        "url": "https://en.wikipedia.org/wiki/Cat",
        "question": "What animal is shown in this image?",
        "options": ["Cat", "Dog", "Bird", "Other animal"],
        "expected": "Cat",
    },
    {
        "name": "Dog",
        "url": "https://en.wikipedia.org/wiki/Dog",
        "question": "What animal is shown in this image?",
        "options": ["Cat", "Dog", "Bird", "Other animal"],
        "expected": "Dog",
    },
    {
        "name": "Bald Eagle",
        "url": "https://en.wikipedia.org/wiki/Bald_eagle",
        "question": "What animal is shown in this image?",
        "options": ["Cat", "Dog", "Bird", "Other animal"],
        "expected": "Bird",
    },
]

VIDEO_TASK = {
    "name": "W3Schools HTML5 Video tutorial",
    "url": "https://www.w3schools.com/html/html5_video.asp",
}

_BAR = "=" * 64


def smooth_move(mouse, x1: int, y1: int, x2: int, y2: int, steps: int = 35, delay: float = 0.010):
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # smoothstep ease-in-out
        mouse.position = (int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
        time.sleep(delay)


def extract_main_image(cdp) -> str:
    """Find the largest non-icon, non-logo image on the page."""
    return cdp.eval_js("""
        (function() {
            // Wikipedia infobox image (most reliable for animal articles)
            var infobox = document.querySelector(
                '.infobox img, .mw-parser-output .infobox img, ' +
                'table.infobox img, .biota img'
            );
            if (infobox && infobox.naturalWidth > 80) return infobox.src;

            // Fallback: largest image on page (skip tiny icons)
            var imgs = Array.from(document.querySelectorAll('img'))
                .filter(function(i) {
                    return i.naturalWidth  > 150
                        && i.naturalHeight > 150
                        && !/logo|icon|avatar|badge|thumb\\.php/i.test(i.src);
                })
                .sort(function(a, b) {
                    return (b.naturalWidth * b.naturalHeight)
                         - (a.naturalWidth * a.naturalHeight);
                });
            return imgs.length ? imgs[0].src : '';
        })()
    """) or ""


def extract_page_text(cdp) -> str:
    """Extract the first meaningful paragraph of text."""
    return cdp.eval_js("""
        (function() {
            var title = document.querySelector('h1#firstHeading, h1.firstHeading, h1');
            var t = title ? title.innerText.trim() : '';
            var p = document.querySelector('p');
            var body = p ? p.innerText.trim().slice(0, 200) : '';
            return t + ' — ' + body;
        })()
    """) or ""


def extract_video_urls(cdp) -> list:
    """Extract all video source URLs from the page."""
    return cdp.eval_js("""
        (function() {
            var urls = [];
            document.querySelectorAll('video').forEach(function(v) {
                if (v.src)         urls.push(v.src);
                if (v.currentSrc)  urls.push(v.currentSrc);
            });
            document.querySelectorAll('video source[src]').forEach(function(s) {
                if (s.src) urls.push(s.src);
            });
            document.querySelectorAll('video[data-src], source[data-src]').forEach(function(el) {
                var u = el.getAttribute('data-src');
                if (u) urls.push(u.startsWith('http') ? u : location.origin + u);
            });
            return urls.filter(function(u, i, a) { return u && a.indexOf(u) === i; });
        })()
    """) or []


def run_annotation_loop(page, mouse, cdp) -> dict:
    """
    Main loop: process each annotation task in sequence.
    Returns summary dict of what was extracted.
    """
    from careerbridge.gemini_vision import annotate_image

    results = {
        "text":    [],
        "images":  [],
        "answers": [],
        "correct": 0,
    }

    for i, task in enumerate(ANNOTATION_TASKS, 1):
        name = task["name"]
        print(f"\n{'—' * 56}")
        print(f"[Task {i}/{len(ANNOTATION_TASKS)}] {name}")
        print(f"  URL: {task['url']}")

        # Playwright navigates (human simulation — address bar, loading)
        print(f"  [Playwright] Navigating browser...")
        page.goto(task["url"], wait_until="domcontentloaded")
        time.sleep(2.5)  # let Wikipedia lazy-load images

        # Physical mouse: simulate reading by scrolling down slightly
        wx  = page.evaluate("window.screenX")
        wy  = page.evaluate("window.screenY")
        ch  = page.evaluate("window.outerHeight - window.innerHeight")
        cx, cy = mouse.position
        center_x = wx + page.evaluate("window.innerWidth") // 2
        center_y = wy + ch + page.evaluate("window.innerHeight") // 2

        print(f"  [pynput ] Moving mouse to page center ({center_x}, {center_y})...")
        smooth_move(mouse, cx, cy, center_x, center_y)
        time.sleep(0.3)

        # Scroll down to trigger lazy image loads
        page.evaluate("window.scrollBy(0, 300)")
        time.sleep(0.8)
        page.evaluate("window.scrollBy(0, -200)")
        time.sleep(0.5)

        # CDPExecutor extracts text + image (OUR SYSTEM)
        print(f"  [CDPExecutor] Extracting text...")
        text = extract_page_text(cdp)
        print(f"    TEXT  : {text[:120]}{'...' if len(text) > 120 else ''}")
        results["text"].append(text)

        print(f"  [CDPExecutor] Extracting main image URL...")
        image_url = extract_main_image(cdp)
        if image_url:
            print(f"    IMAGE : {image_url[:90]}{'...' if len(image_url) > 90 else ''}")
        else:
            print(f"    IMAGE : (none found — Wikipedia may have changed layout)")
        results["images"].append(image_url)

        # Gemini vision classification (free tier: 10 RPM — wait 6s between calls)
        if image_url:
            if i > 1:
                print(f"  [Gemini ] Waiting 6s (free-tier rate limit)...")
                time.sleep(6)
            print(f"  [Gemini ] Classifying image...")
            answer = annotate_image(image_url, task["question"], task["options"])
            print(f"    ANSWER: {answer!r}  (expected: {task['expected']!r})")
            results["answers"].append(answer)
            if answer and task["expected"].lower() in answer.lower():
                results["correct"] += 1
        else:
            print(f"  [Gemini ] Skipped (no image URL extracted)")
            results["answers"].append(None)

        # Brief pause between tasks (human rhythm)
        if i < len(ANNOTATION_TASKS):
            time.sleep(1.5)

    return results


def run_video_extraction(page, mouse, cdp) -> list:
    """Navigate to a video-rich page and extract video URLs via CDPExecutor."""
    print(f"\n{'—' * 56}")
    print(f"[Video extraction] {VIDEO_TASK['name']}")
    print(f"  URL: {VIDEO_TASK['url']}")

    print(f"  [Playwright] Navigating browser...")
    page.goto(VIDEO_TASK["url"], wait_until="domcontentloaded")
    time.sleep(3.0)

    # Scroll down to trigger video lazy-loading
    wx  = page.evaluate("window.screenX")
    wy  = page.evaluate("window.screenY")
    ch  = page.evaluate("window.outerHeight - window.innerHeight")
    cx, cy = mouse.position
    center_x = wx + page.evaluate("window.innerWidth") // 2
    center_y = wy + ch + page.evaluate("window.innerHeight") // 2
    smooth_move(mouse, cx, cy, center_x, center_y)
    time.sleep(0.3)

    # Scroll through the page to trigger lazy video loads
    for delta in [400, 600, 800, 600]:
        page.evaluate(f"window.scrollBy(0, {delta})")
        time.sleep(0.6)

    print(f"  [CDPExecutor] Extracting video URLs...")
    video_urls = extract_video_urls(cdp)

    if video_urls:
        print(f"    Found {len(video_urls)} video URL(s):")
        for u in video_urls[:5]:
            print(f"      VIDEO : {u[:100]}{'...' if len(u) > 100 else ''}")
    else:
        print(f"    No <video> elements found on this page.")
        print(f"    (Wikipedia Animation page may not have inline video — checking for iframes...)")
        # Try extracting iframe sources that might contain video
        iframes = cdp.eval_js("""
            Array.from(document.querySelectorAll('iframe[src]'))
                .map(function(f){ return f.src; })
                .filter(function(s){ return /youtube|vimeo|video/i.test(s); })
                .slice(0,3)
        """) or []
        if iframes:
            print(f"    Video iframes found:")
            for u in iframes:
                print(f"      IFRAME: {u[:100]}")
            video_urls = iframes

    return video_urls


def main():
    from playwright.sync_api import sync_playwright
    from pynput.mouse import Controller
    from careerbridge.cdp_executor import CDPExecutor

    print(_BAR)
    print("REAL-WORLD ANNOTATION PIPELINE DEMO")
    print("  Playwright  : browser navigation (visible window)")
    print("  pynput      : physical OS mouse movement")
    print("  CDPExecutor : text + image + video extraction (our system)")
    print("  Gemini      : vision classification")
    print(_BAR)

    mouse = Controller()

    with sync_playwright() as p:
        print("\n[Setup] Launching visible Chromium browser...")
        browser = p.chromium.launch(
            headless=False,
            args=[
                f"--remote-debugging-port=9222",
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
            ],
        )
        context = browser.new_context(no_viewport=True)
        page    = context.new_page()
        page.goto("about:blank")
        time.sleep(1.0)

        # CDPExecutor connects to the same browser Playwright opened
        print("[Setup] CDPExecutor connecting to CDP port 9222...")
        cdp = CDPExecutor()
        cdp.connect(port=9222)
        print("[Setup] Connected. Starting annotation loop.\n")

        # ── Main annotation loop ─────────────────────────────────────────────
        loop_results = run_annotation_loop(page, mouse, cdp)

        # ── Video URL extraction ─────────────────────────────────────────────
        video_urls = run_video_extraction(page, mouse, cdp)

        cdp.disconnect()
        time.sleep(2.0)
        browser.close()

    # ── Final summary ────────────────────────────────────────────────────────
    print(f"\n{_BAR}")
    print("PIPELINE RESULTS SUMMARY")
    print(_BAR)

    n_tasks = len(ANNOTATION_TASKS)
    n_text  = sum(1 for t in loop_results["text"]   if t)
    n_img   = sum(1 for u in loop_results["images"]  if u)
    n_ans   = sum(1 for a in loop_results["answers"] if a)
    n_ok    = loop_results["correct"]

    print(f"  Annotation tasks  : {n_tasks}")
    print(f"  Text extracted    : {n_text}/{n_tasks}")
    print(f"  Images extracted  : {n_img}/{n_tasks}")
    print(f"  Gemini answered   : {n_ans}/{n_tasks}")
    print(f"  Correct answers   : {n_ok}/{n_ans if n_ans else n_tasks}")
    print(f"  Video URLs found  : {len(video_urls)}")
    print()

    for i, (task, text, img, ans) in enumerate(zip(
        ANNOTATION_TASKS,
        loop_results["text"],
        loop_results["images"],
        loop_results["answers"],
    ), 1):
        status = "OK" if ans else "SKIP"
        match  = " (CORRECT)" if ans and task["expected"].lower() in ans.lower() else ""
        print(f"  [{i}] {task['name']:<18} text={bool(text)} img={bool(img)} "
              f"answer={ans!r}{match}")

    if video_urls:
        print(f"\n  [V] Video URLs:")
        for u in video_urls[:3]:
            print(f"      {u[:90]}")

    print()
    all_ok = n_text == n_tasks and n_img > 0 and n_ans > 0
    if all_ok:
        print("SUCCESS — annotation pipeline extracted text + images + video from real sites")
    else:
        print("PARTIAL — some extractions succeeded (see details above)")
    print(_BAR)


if __name__ == "__main__":
    main()
