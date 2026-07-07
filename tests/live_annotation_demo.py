"""
Live annotation demo — physical mouse clicks via pynput.

1. Starts an HTTP server for the mock page
2. Launches a visible Playwright Chrome window
3. Gets exact screen coordinates of all buttons via JS getBoundingClientRect
4. Calls Gemini vision API to identify the correct answer
5. Physically moves the mouse to that button and clicks it (isTrusted-style)
6. Clicks Done and verifies the result

Run:
    C:\\Python314\\python.exe E:\\Corvus_Careebridge\\tests\\live_annotation_demo.py
"""
from __future__ import annotations

import os
import sys
import math
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, r"E:\Corvus_Careebridge")

TESTS_DIR = Path(__file__).parent
MOCK_PORT = 18899
MOCK_URL   = f"http://127.0.0.1:{MOCK_PORT}/mock_annotation.html"


# ── HTTP server ───────────────────────────────────────────────────────────────

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def start_server():
    os.chdir(TESTS_DIR)
    server = HTTPServer(("127.0.0.1", MOCK_PORT), _QuietHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ── Smooth mouse move ─────────────────────────────────────────────────────────

def smooth_move(mouse, x1: int, y1: int, x2: int, y2: int, steps: int = 40, delay: float = 0.012):
    """Ease-in-out interpolation from (x1,y1) to (x2,y2)."""
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)          # smoothstep
        nx = int(x1 + (x2 - x1) * t)
        ny = int(y1 + (y2 - y1) * t)
        mouse.position = (nx, ny)
        time.sleep(delay)


# ── Main demo ─────────────────────────────────────────────────────────────────

def main():
    from playwright.sync_api import sync_playwright
    from pynput.mouse import Controller, Button
    from careerbridge.gemini_vision import annotate_image

    print("=" * 60)
    print("LIVE ANNOTATION DEMO — physical mouse movement & clicks")
    print("=" * 60)

    # Start HTTP server
    print("\n[1/6] Starting HTTP server on port", MOCK_PORT)
    try:
        server = start_server()
        print("      OK — serving", TESTS_DIR)
    except OSError:
        print("      Already running (or port busy) — continuing")
        server = None

    mouse = Controller()

    with sync_playwright() as p:
        # Launch visible, maximized browser
        print("\n[2/6] Launching headful Chromium…")
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
            ],
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(MOCK_URL, wait_until="load")
        time.sleep(1.5)
        print(f"      OK — loaded {MOCK_URL}")

        # Get element positions & task data from DOM
        print("\n[3/6] Reading element positions from DOM…")
        info = page.evaluate(
            """() => {
                const ids = ['opt-cat', 'opt-dog', 'opt-bird', 'opt-fish', 'btn-done'];
                const buttons = {};
                ids.forEach(id => {
                    const el = document.getElementById(id);
                    if (el) {
                        const r = el.getBoundingClientRect();
                        buttons[id] = {
                            left: r.left, top: r.top,
                            right: r.right, bottom: r.bottom,
                            cx: r.left + r.width / 2,
                            cy: r.top  + r.height / 2
                        };
                    }
                });
                return {
                    windowX:     window.screenX,
                    windowY:     window.screenY,
                    chromeHeight: window.outerHeight - window.innerHeight,
                    question:    document.querySelector('h2.task-question').innerText.trim(),
                    imageUrl:    document.querySelector('.subject img').src,
                    options:     Array.from(document.querySelectorAll('[role="radio"]'))
                                     .map(b => b.textContent.trim()),
                    buttons:     buttons,
                };
            }"""
        )

        wx  = info["windowX"]
        wy  = info["windowY"]
        ch  = info["chromeHeight"]
        print(f"      Window origin : ({wx}, {wy})")
        print(f"      Chrome UI      : {ch} px")
        print(f"      Question       : {info['question']}")
        print(f"      Image URL      : {info['imageUrl'][:72]}…")
        print(f"      Options        : {info['options']}")

        for btn_id, rect in info["buttons"].items():
            sx = int(wx + rect["cx"])
            sy = int(wy + ch + rect["cy"])
            print(f"        {btn_id:<12} screen ({sx:>4}, {sy:>4})")

        # Call Gemini
        print("\n[4/6] Calling Gemini vision API…")
        answer = annotate_image(info["imageUrl"], info["question"], info["options"])
        print(f"      Gemini answer  : {answer!r}")
        assert answer is not None, "Gemini returned None — check API key/quota"
        assert answer in info["options"], f"{answer!r} not in {info['options']}"

        # Map answer to button id
        btn_map = {
            "Cat": "opt-cat", "Dog": "opt-dog",
            "Bird": "opt-bird", "Fish": "opt-fish",
        }
        btn_id = btn_map.get(answer)
        assert btn_id, f"No button ID mapped for answer {answer!r}"
        rect = info["buttons"][btn_id]
        target_x = int(wx + rect["cx"])
        target_y = int(wy + ch + rect["cy"])

        # Move mouse & click the answer button
        print(f"\n[5/6] Moving mouse → {answer!r} button at ({target_x}, {target_y})…")
        cx, cy = mouse.position
        smooth_move(mouse, cx, cy, target_x, target_y)
        time.sleep(0.25)
        mouse.click(Button.left)
        print("      Clicked!")
        time.sleep(0.8)

        result = page.inner_text("#result-display")
        print(f"      Page result    : {result!r}")
        assert answer in result, f"Expected {answer!r} in result display, got {result!r}"

        # Move mouse & click Done
        done_rect = info["buttons"]["btn-done"]
        done_x = int(wx + done_rect["cx"])
        done_y = int(wy + ch + done_rect["cy"])

        print(f"\n[6/6] Moving mouse → Done button at ({done_x}, {done_y})…")
        cx, cy = mouse.position
        smooth_move(mouse, cx, cy, done_x, done_y)
        time.sleep(0.25)
        mouse.click(Button.left)
        print("      Clicked!")
        time.sleep(0.8)

        final = page.inner_text("#result-display")
        print(f"      Final result   : {final!r}")
        assert "DONE" in final,  f"Expected 'DONE' in final text, got {final!r}"
        assert answer in final,  f"Expected {answer!r} in final text, got {final!r}"

        print("\n" + "=" * 60)
        print("SUCCESS — full annotation pipeline with physical mouse clicks")
        print(f"  Gemini vision   : identified {answer!r}")
        print(f"  Physical click  : registered on-screen")
        print(f"  Final page state: {final}")
        print("=" * 60)

        time.sleep(3.0)   # pause so user can see the result
        browser.close()

    if server:
        server.shutdown()


if __name__ == "__main__":
    main()
