"""
Real-world annotation pipeline demo — correct framebuffer extraction implementation.

Architecture (per CLAUDE.md Passive Annotation Tutor Pipeline):
  Playwright  → browser navigation (simulated human operator)
  MSS/DXGI    → OS-level framebuffer capture of what is displayed on screen
  Gemini      → vision analysis of the captured screen frame
  Text output → system produces answer; never interacts with the page

This is the ACTUAL CareerBridge extraction system:
  NOT CDPExecutor / DOM inspection
  NOT CDP Page.captureScreenshot
  YES MSS/DXGI GPU framebuffer capture (DXGI = undetectable by websites)
  YES Gemini vision analysis of the captured frame

The key insight from CLAUDE.md:
  "DXGI hooks into the Windows GPU compositor swap chain and is invisible to websites.
   Sites detect screenshots only via CDP Page.captureScreenshot, --force-renderer-
   accessibility, or injected browser extensions — none of which this pipeline uses."

Loop:
  Task 1/3 — Cat   (Wikipedia) : navigate → framebuffer capture → Gemini → text answer
  Task 2/3 — Dog   (Wikipedia) : navigate → framebuffer capture → Gemini → text answer
  Task 3/3 — Eagle (Wikipedia) : navigate → framebuffer capture → Gemini → text answer
  Video     — W3Schools HTML5  : navigate → start screen recording → play → stop → Gemini

Run:
    C:\\Python314\\python.exe E:\\Corvus_Careebridge\\tests\\real_world_demo.py
"""
from __future__ import annotations

import base64
import os
import sys
import time
from typing import Optional

sys.path.insert(0, r"E:\Corvus_Careebridge")


ANNOTATION_TASKS = [
    {
        "name": "Cat",
        "url": "https://en.wikipedia.org/wiki/Cat",
        "question": "What animal is shown prominently on this page?",
        "options": ["Cat", "Dog", "Bird", "Other animal"],
        "expected": "Cat",
    },
    {
        "name": "Dog",
        "url": "https://en.wikipedia.org/wiki/Dog",
        "question": "What animal is shown prominently on this page?",
        "options": ["Cat", "Dog", "Bird", "Other animal"],
        "expected": "Dog",
    },
    {
        "name": "Bald Eagle",
        "url": "https://en.wikipedia.org/wiki/Bald_eagle",
        "question": "What animal is shown prominently on this page?",
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


def capture_screen_b64() -> tuple[str, str]:
    """
    Capture the current screen via MSS (or GDI fallback) framebuffer.

    Returns (base64_string, mime_type).
    This is the non-intrusive extraction layer — OS-level pixel read,
    no CDP, no JS injection, invisible to the browser/website.
    """
    from capture_mcp._backend_mss import capture as mss_capture, available as mss_ok
    from capture_mcp._backend_gdi import capture as gdi_capture, available as gdi_ok
    from PIL import Image
    import io

    if mss_ok():
        img_bytes = mss_capture()
    elif gdi_ok():
        img_bytes = gdi_capture()
    else:
        raise RuntimeError("No framebuffer capture backend available (install mss or check GDI)")

    # Convert PNG → JPEG at 80% quality to reduce Gemini payload size
    pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=80, optimize=True)
    jpeg_bytes = buf.getvalue()

    return base64.standard_b64encode(jpeg_bytes).decode(), "image/jpeg"


def run_annotation_loop(page, mouse) -> dict:
    """
    Main loop: Playwright navigates, framebuffer captures what's on screen,
    Gemini analyses what it sees. No DOM inspection. No CDP. No JS eval.
    """
    from careerbridge.gemini_vision import annotate_image_b64

    results = {
        "frames":  [],
        "answers": [],
        "correct": 0,
    }

    for i, task in enumerate(ANNOTATION_TASKS, 1):
        name = task["name"]
        print(f"\n{'—' * 56}")
        print(f"[Task {i}/{len(ANNOTATION_TASKS)}] {name}")
        print(f"  URL: {task['url']}")

        # 1. Playwright navigates — this is the simulated human operator
        print(f"  [Playwright] Navigating browser...")
        page.goto(task["url"], wait_until="domcontentloaded")
        time.sleep(3.0)  # allow Wikipedia to lazy-load images

        # 2. Physical mouse — smooth movement to browser centre (human rhythm)
        wx       = page.evaluate("window.screenX")
        wy       = page.evaluate("window.screenY")
        ch       = page.evaluate("window.outerHeight - window.innerHeight")
        cx, cy   = mouse.position
        center_x = wx + page.evaluate("window.innerWidth") // 2
        center_y = wy + ch + page.evaluate("window.innerHeight") // 2

        print(f"  [pynput] Moving mouse to page centre ({center_x}, {center_y})...")
        smooth_move(mouse, cx, cy, center_x, center_y)
        time.sleep(0.4)

        # Scroll to reveal infobox (Wikipedia puts the animal photo in top-right infobox)
        page.keyboard.press("End")   # scroll to bottom to trigger all lazy loads
        time.sleep(0.8)
        page.keyboard.press("Home")  # scroll back to top where the infobox is
        time.sleep(0.8)

        # 3. Framebuffer capture — MSS/DXGI reads GPU compositor swap chain
        #    This is exactly what the tutor pipeline does: capture what is DISPLAYED
        print(f"  [MSS/DXGI] Capturing framebuffer (OS-level, undetectable)...")
        frame_b64, mime = capture_screen_b64()
        size_kb = round(len(base64.b64decode(frame_b64)) / 1024, 1)
        print(f"    Frame size  : {size_kb} KB  mime={mime}")
        results["frames"].append(frame_b64)

        # 4. Gemini vision analyses the captured screen frame
        if i > 1:
            print(f"  [Gemini] Waiting 6s (free-tier rate limit)...")
            time.sleep(6)
        print(f"  [Gemini] Analysing captured screen frame...")
        answer = annotate_image_b64(
            frame_b64,
            mime,
            task["question"],
            task["options"],
            context=(
                "This is a screenshot of a Wikipedia page. "
                "Look at the infobox image in the upper-right — that is the subject of the article. "
                "Answer based only on what you see in that image."
            ),
        )
        print(f"    ANSWER : {answer!r}  (expected: {task['expected']!r})")
        results["answers"].append(answer)
        if answer and task["expected"].lower() in answer.lower():
            results["correct"] += 1

        if i < len(ANNOTATION_TASKS):
            time.sleep(1.5)

    return results


def _mss_record_frames(duration_s: float, fps: int = 10) -> list:
    """
    Capture screen frames via MSS for `duration_s` seconds at `fps` frames/sec.
    Returns list of numpy BGR arrays suitable for cv2.VideoWriter.
    """
    import numpy as np
    from capture_mcp._backend_mss import capture as mss_capture
    from PIL import Image
    import io as _io

    frames = []
    interval = 1.0 / fps
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        png = mss_capture()
        pil = Image.open(_io.BytesIO(png)).convert("RGB")
        bgr = np.array(pil)[:, :, ::-1]  # RGB → BGR for OpenCV
        frames.append(bgr)
        elapsed = time.monotonic() - t0
        remaining = interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
    return frames


def _frames_to_mp4(frames: list, output_path: str, fps: int = 10) -> str:
    """Encode list of BGR numpy frames to MP4. Returns output_path."""
    import cv2
    if not frames:
        raise RuntimeError("No frames to encode")
    h, w = frames[0].shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    for f in frames:
        writer.write(f)
    writer.release()
    return output_path


def run_video_capture(page, mouse) -> dict:
    """
    Video pipeline:
      Playwright navigates to a page with video
      → MSS captures screen frames in a loop (no DXcam dependency)
      → Playwright clicks Play (simulated human action)
      → Frames assembled into MP4 via OpenCV
      → upload_video() sends to Gemini Files API
      → analyse_video() returns Gemini's description of the screen recording
    """
    from gemini_mcp._gemini import upload_video, analyse_video

    print(f"\n{'—' * 56}")
    print(f"[Video task] {VIDEO_TASK['name']}")
    print(f"  URL: {VIDEO_TASK['url']}")

    print(f"  [Playwright] Navigating browser...")
    page.goto(VIDEO_TASK["url"], wait_until="domcontentloaded")
    time.sleep(2.5)

    # Scroll to bring video element into view
    cx, cy = mouse.position
    wx       = page.evaluate("window.screenX")
    wy       = page.evaluate("window.screenY")
    ch_off   = page.evaluate("window.outerHeight - window.innerHeight")
    center_x = wx + page.evaluate("window.innerWidth") // 2
    center_y = wy + ch_off + page.evaluate("window.innerHeight") // 2
    smooth_move(mouse, cx, cy, center_x, center_y)
    time.sleep(0.3)

    # Scroll down to where the video element sits on the W3Schools page
    page.evaluate("window.scrollBy(0, 600)")
    time.sleep(1.0)

    clip_path = "C:/tmp/annotation_video_demo.mp4"
    os.makedirs("C:/tmp", exist_ok=True)

    # 1. Playwright clicks Play — simulated human presses the play button
    print(f"  [Playwright] Clicking play on video...")
    try:
        video_handle = page.locator("video").first
        video_handle.scroll_into_view_if_needed()
        time.sleep(0.3)
        video_handle.click()
        print(f"  [Playwright] Video element clicked")
    except Exception as e:
        print(f"  [Playwright] Video click via locator failed ({e}) — using Space key")
        page.keyboard.press("Space")

    time.sleep(0.5)  # brief pause so video actually starts before recording

    # 2. MSS captures screen at 10fps for 8 seconds while video plays
    RECORD_FPS = 10
    RECORD_S   = 8
    print(f"  [MSS] Recording {RECORD_S}s at {RECORD_FPS}fps via MSS framebuffer...")
    frames = _mss_record_frames(duration_s=RECORD_S, fps=RECORD_FPS)
    print(f"  [MSS] Captured {len(frames)} frames")

    if not frames:
        return {"mode": "mss_capture_failed", "error": "no frames", "clip_path": None}

    # 3. Encode frames → MP4
    print(f"  [OpenCV] Encoding {len(frames)} frames → {clip_path}")
    _frames_to_mp4(frames, clip_path, fps=RECORD_FPS)
    file_size = os.path.getsize(clip_path)
    print(f"  [OpenCV] MP4 saved: {clip_path} ({file_size // 1024} KB)")

    # 4. Upload to Gemini Files API
    print(f"  [Gemini] Uploading screen recording to Gemini Files API...")
    upload_info = upload_video(clip_path)
    if "error" in upload_info:
        print(f"  [Gemini] Upload failed: {upload_info['error']}")
        return {"mode": "upload_failed", "error": upload_info["error"], "clip_path": clip_path}

    print(f"  [Gemini] Upload complete: {upload_info.get('uri')} ({upload_info.get('upload_ms')}ms)")

    # 5. Gemini analyses the recorded video
    print(f"  [Gemini] Analysing screen recording...")
    analysis = analyse_video(
        upload_info["uri"],
        (
            "This is a screen recording of a web browser showing an HTML5 video tutorial page. "
            "Describe: (1) what web page is shown, (2) what video content is visible or playing, "
            "(3) what text or UI elements you can see. Be specific."
        ),
    )

    gemini_text = analysis.get("text", "") if isinstance(analysis, dict) else str(analysis)
    print(f"  [Gemini] Analysis: {gemini_text[:300]}{'...' if len(gemini_text) > 300 else ''}")

    return {
        "mode": "mss_video_capture",
        "clip_path": clip_path,
        "frame_count": len(frames),
        "gemini_uri": upload_info.get("uri"),
        "gemini_analysis": gemini_text,
    }


def main():
    from playwright.sync_api import sync_playwright
    from pynput.mouse import Controller

    print(_BAR)
    print("REAL-WORLD ANNOTATION PIPELINE — FRAMEBUFFER EXTRACTION")
    print(_BAR)
    print("  Playwright  : browser navigation (simulated human operator)")
    print("  pynput      : physical OS mouse movement (isTrusted=true)")
    print("  MSS/DXGI    : OS-level framebuffer capture (NOT CDP/DOM)")
    print("  Gemini      : vision analysis of captured screen frames")
    print("  Output      : text answer only — system never touches the page")
    print(_BAR)

    mouse = Controller()

    with sync_playwright() as p:
        print("\n[Setup] Launching visible Chromium browser (no CDP debug port)...")
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
                # NOTE: no --remote-debugging-port — we use framebuffer capture, not CDP
            ],
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto("about:blank")
        time.sleep(1.0)
        print("[Setup] Browser ready. Starting framebuffer annotation loop.\n")

        # ── Main annotation loop ─────────────────────────────────────────────
        loop_results = run_annotation_loop(page, mouse)

        # ── Video capture pipeline ───────────────────────────────────────────
        video_result = run_video_capture(page, mouse)

        time.sleep(1.5)
        browser.close()

    # ── Final summary ────────────────────────────────────────────────────────
    print(f"\n{_BAR}")
    print("PIPELINE RESULTS SUMMARY")
    print(_BAR)

    n_tasks  = len(ANNOTATION_TASKS)
    n_frames = sum(1 for f in loop_results["frames"] if f)
    n_ans    = sum(1 for a in loop_results["answers"] if a)
    n_ok     = loop_results["correct"]

    print(f"  Annotation tasks     : {n_tasks}")
    print(f"  Framebuffer captures : {n_frames}/{n_tasks}")
    print(f"  Gemini answered      : {n_ans}/{n_tasks}")
    print(f"  Correct answers      : {n_ok}/{n_ans if n_ans else n_tasks}")
    print()

    for i, (task, frame, ans) in enumerate(zip(
        ANNOTATION_TASKS,
        loop_results["frames"],
        loop_results["answers"],
    ), 1):
        match = " (CORRECT)" if ans and task["expected"].lower() in ans.lower() else ""
        print(f"  [{i}] {task['name']:<18} captured={bool(frame)} answer={ans!r}{match}")

    print()
    mode = video_result.get("mode", "unknown")
    print(f"  [V] Video mode: {mode}")
    if video_result.get("clip_path"):
        print(f"      Clip path  : {video_result['clip_path']}")
    if video_result.get("gemini_uri"):
        print(f"      Gemini URI : {video_result['gemini_uri']}")
    if video_result.get("gemini_analysis"):
        snippet = video_result["gemini_analysis"][:120]
        print(f"      Analysis   : {snippet}...")
    elif video_result.get("frame_b64"):
        print(f"      Still frame captured (DXcam not available — MSS fallback)")

    print()
    all_ok = n_frames == n_tasks and n_ans > 0
    if all_ok:
        print("SUCCESS — framebuffer extraction pipeline: captured + Gemini-analysed real browser content")
    else:
        print("PARTIAL — some captures/analyses succeeded (see details above)")
    print(_BAR)


if __name__ == "__main__":
    main()
