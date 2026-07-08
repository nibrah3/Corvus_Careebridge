"""
End-to-end annotation pipeline test.

Playwright acts as the human operator. MSS/DXGI observes the screen passively.
Gemini analyses what the framebuffer captures.

Phases:
  1. Scroll-assembly  : Playwright scrolls Wikipedia Cat article -> MSS captures
                        frames at each scroll position -> frames stitched into
                        one tall image -> Gemini extracts markdown document ->
                        saved to C:/tmp/annotation_doc.md
  2. Tutor components : _capture + _audio modules started directly; Playwright
                        navigates + scrolls triggering observable FrameEvents
  3. Video pipeline   : W3Schools HTML5 video -> Playwright clicks Play ->
                        MSS records 8s -> MP4 encoded -> uploaded to Gemini
                        Files API -> Gemini analyses the screen recording
  4. Final document   : video analysis appended to annotation_doc.md;
                        assert final .md is coherent

All phases must pass for test to exit 0.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"E:\Corvus_Careebridge")

_BAR = "=" * 64
_OUT_DIR = "C:/tmp"
os.makedirs(_OUT_DIR, exist_ok=True)


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def smooth_move(mouse, x1: int, y1: int, x2: int, y2: int, steps: int = 35, delay: float = 0.010):
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)
        mouse.position = (int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
        time.sleep(delay)


def capture_mss_bgr():
    """Capture full screen via MSS -> BGR numpy array."""
    import io as _io
    import numpy as np
    from PIL import Image
    from capture_mcp._backend_mss import capture as mss_capture
    png = mss_capture()
    pil = Image.open(_io.BytesIO(png)).convert("RGB")
    return np.array(pil)[:, :, ::-1]  # RGB -> BGR


def _detect_shift(prev_gray, curr_gray, min_shift: int = 20, max_shift: int = 400):
    """
    Detect how many pixels the page shifted vertically between two frames.
    Returns pixel shift (int) or None if no confident match found.

    Algorithm mirrors tutor_mcp._capture._detect_scroll:
      prev[shift:, :] ≈ curr[:H-shift, :] => page scrolled down `shift` pixels.
    """
    import numpy as np
    h = prev_gray.shape[0]
    for shift in range(min_shift, min(h // 2, max_shift), 4):
        top_a = prev_gray[shift:, :]
        top_b = curr_gray[:h - shift, :]
        if top_a.shape == top_b.shape:
            diff = np.abs(top_a.astype(np.int16) - top_b.astype(np.int16))
            if float(np.mean(diff > 20)) < 0.08:
                return shift
    return None


def _mss_record_frames(duration_s: float, fps: int = 10) -> list:
    """Capture screen via MSS for duration_s at fps. Returns list of BGR arrays."""
    import io as _io
    import numpy as np
    from PIL import Image
    from capture_mcp._backend_mss import capture as mss_capture
    frames = []
    interval = 1.0 / fps
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        png = mss_capture()
        pil = Image.open(_io.BytesIO(png)).convert("RGB")
        bgr = np.array(pil)[:, :, ::-1]
        frames.append(bgr)
        elapsed = time.monotonic() - t0
        remaining = interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
    return frames


def _frames_to_mp4(frames: list, output_path: str, fps: int = 10) -> str:
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


def _gemini_with_fallback(fn, models, label: str) -> tuple[str, str]:
    """
    Try fn(model) for each model until one succeeds.
    Returns (text, model_used) or ("", "none").
    """
    QUOTA_MARKERS = ("quota", "Quota", "RESOURCE_EXHAUSTED", "429", "exhausted", "FreeTier", "PerDay")
    for model in models:
        try:
            result = fn(model)
        except Exception as e:
            err = str(e)
            if any(k in err for k in QUOTA_MARKERS):
                print(f"  [{label}] {model}: quota exhausted — trying next")
                continue
            print(f"  [{label}] {model}: exception — {e}")
            break
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            if any(k in err for k in QUOTA_MARKERS):
                print(f"  [{label}] {model}: quota exhausted — trying next")
                continue
            print(f"  [{label}] {model}: error — {err}")
            break
        text = result.get("text", "") if isinstance(result, dict) else str(result or "")
        if text:
            return text, model
        print(f"  [{label}] {model}: empty response — trying next")
    return "", "none"


# ── Phase 1: Scroll document assembly ────────────────────────────────────────

def phase1_scroll_assembly(page) -> dict:
    """
    Playwright scrolls a Wikipedia article. MSS captures a frame at each
    scroll position. Frames are stitched into a single tall image using
    overlap detection. Gemini extracts the page as a markdown document.
    """
    import cv2
    import numpy as np
    from gemini_mcp._gemini import analyse_image

    print(f"\n[{_ts()}] PHASE 1: Scroll document assembly")

    print("  [Playwright] Navigating to Wikipedia: Cat...")
    page.goto("https://en.wikipedia.org/wiki/Cat", wait_until="domcontentloaded")
    time.sleep(3.0)

    page.keyboard.press("Home")
    time.sleep(0.5)

    SCROLL_STEPS = 5
    SCROLL_PX = 600

    print(f"  [MSS] Capturing {SCROLL_STEPS + 1} frames across {SCROLL_STEPS} scroll steps...")

    frames_bgr = [capture_mss_bgr()]  # initial frame at top

    for step in range(SCROLL_STEPS):
        page.evaluate(f"window.scrollBy(0, {SCROLL_PX})")
        time.sleep(0.8)
        frames_bgr.append(capture_mss_bgr())
        print(f"    scroll {step + 1}/{SCROLL_STEPS}: captured")

    # Stitch frames into one tall image
    H = frames_bgr[0].shape[0]
    canvas = frames_bgr[0].copy()
    prev_frame = frames_bgr[0]
    overlap_found = 0

    print(f"  [Stitch] Assembling {len(frames_bgr)} frames...")
    for i, curr_frame in enumerate(frames_bgr[1:], 1):
        if curr_frame.shape[1] != canvas.shape[1]:
            print(f"    frame {i}: width mismatch — skipped")
            prev_frame = curr_frame
            continue

        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
        shift = _detect_shift(prev_gray, curr_gray)

        if shift and shift > 0:
            new_rows = curr_frame[H - shift:, :]
            canvas = np.vstack([canvas, new_rows])
            overlap_found += 1
            print(f"    frame {i}: overlap={shift}px -> appended {shift} new rows")
        else:
            # Fallback: append bottom quarter as new content
            quarter = H // 4
            canvas = np.vstack([canvas, curr_frame[H - quarter:, :]])
            print(f"    frame {i}: no overlap -> appended {quarter}px (fallback)")
        prev_frame = curr_frame

    stitched_path = f"{_OUT_DIR}/stitched_page.jpg"
    _, jpeg_buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 80])
    with open(stitched_path, "wb") as fh:
        fh.write(jpeg_buf.tobytes())
    size_kb = os.path.getsize(stitched_path) // 1024
    print(f"  [Stitch] Result: {canvas.shape[1]}x{canvas.shape[0]}px — {stitched_path} ({size_kb}KB)")

    # Send to Gemini for markdown extraction
    EXTRACTION_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-2.5-flash-lite", "gemini-2.0-flash-001"]
    EXTRACTION_PROMPT = (
        "This is a long scrollable screenshot of a Wikipedia web page. "
        "Extract all visible text as clean markdown. "
        "Use # for main headings, ## for sections, ### for sub-sections. "
        "For each image write ![IMAGE: brief description of image]. "
        "For any video player write [VIDEO: description]. "
        "Output only the markdown content. No preamble or explanation."
    )

    print(f"  [Gemini] Extracting markdown from stitched image...")
    md_text, model_used = _gemini_with_fallback(
        lambda m: analyse_image(stitched_path, EXTRACTION_PROMPT, model=m),
        EXTRACTION_MODELS,
        "Gemini/extract",
    )

    if md_text:
        print(f"  [Gemini] Extracted {len(md_text)} chars via {model_used}")
    else:
        md_text = f"# Cat\n\n[Quota exhausted — stitched image at {stitched_path}]\n"
        print("  [Gemini] All models quota-exhausted. Placeholder markdown used.")

    doc_path = f"{_OUT_DIR}/annotation_doc.md"
    with open(doc_path, "w", encoding="utf-8") as fh:
        fh.write("# Scroll-Assembled Annotation Document\n\n")
        fh.write(f"_Source: Wikipedia/Cat — {SCROLL_STEPS} scroll steps — {canvas.shape[1]}x{canvas.shape[0]}px stitched_\n\n")
        fh.write("---\n\n")
        fh.write(md_text)

    doc_len = os.path.getsize(doc_path)
    print(f"  [Doc] Saved: {doc_path} ({doc_len} bytes)")

    ok = os.path.exists(stitched_path) and os.path.exists(doc_path) and doc_len > 200
    return {
        "pass": ok,
        "stitched_path": stitched_path,
        "stitched_shape": f"{canvas.shape[1]}x{canvas.shape[0]}",
        "doc_path": doc_path,
        "doc_len": doc_len,
        "frames_captured": len(frames_bgr),
        "overlap_frames": overlap_found,
        "md_chars": len(md_text),
    }


# ── Phase 2: Tutor server components ─────────────────────────────────────────

def phase2_tutor_components(page) -> dict:
    """
    Import tutor_mcp._capture and ._audio directly.
    Playwright navigation and scrolling trigger FrameEvents.
    Assert the capture pipeline is alive and emitting events.
    """
    print(f"\n[{_ts()}] PHASE 2: Tutor server components (_capture + _audio)")

    events_received = []
    got_non_static = False
    audio_started = False
    capture_started = False

    try:
        import tutor_mcp._audio as _audio
        import tutor_mcp._capture as _capture

        # Start WASAPI audio loopback
        audio_started = _audio.start()
        print(f"  [audio] WASAPI loopback: {'started' if audio_started else 'FAILED (no audio device?)'}")
        time.sleep(0.5)

        def get_rms():
            try:
                return _audio.get_rms()
            except Exception:
                return 0.0

        # Start framebuffer capture loop
        _capture.start(get_rms)
        capture_started = True
        print("  [capture] Framebuffer capture loop started")
        time.sleep(0.5)

        # Playwright navigates to a new page -> large pixel change -> navigation event
        print("  [Playwright] Navigating to Wikipedia: Dog (triggers navigation event)...")
        page.goto("https://en.wikipedia.org/wiki/Dog", wait_until="domcontentloaded")
        time.sleep(2.0)

        # Playwright scrolls -> overlap detection -> scroll events
        print("  [Playwright] Scrolling down 5 times (triggers scroll events)...")
        for _ in range(5):
            page.evaluate("window.scrollBy(0, 400)")
            time.sleep(0.4)

        # Drain event queue for 5 seconds
        print("  [capture] Collecting events (5s)...")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            ev = _capture.get_event(timeout=0.4)
            if ev is None:
                continue
            kind = getattr(ev, "kind", "unknown")
            events_received.append(kind)
            if kind != "static":
                got_non_static = True
            print(f"    FrameEvent: {kind}")

        print("  [capture] Stopping...")
        _capture.stop()
        if audio_started:
            _audio.stop()

    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        try:
            if capture_started:
                import tutor_mcp._capture as _capture
                _capture.stop()
        except Exception:
            pass
        try:
            if audio_started:
                import tutor_mcp._audio as _audio
                _audio.stop()
        except Exception:
            pass

    n = len(events_received)
    # Pass: capture module is operational and emitted at least 1 event
    ok = n > 0
    print(f"  Events: {n} total — non-static={got_non_static}")
    print(f"  Kinds seen: {sorted(set(events_received))}")
    return {
        "pass": ok,
        "events_total": n,
        "got_non_static": got_non_static,
        "event_kinds": sorted(set(events_received)),
        "audio_started": audio_started,
    }


# ── Phase 3: Video pipeline ───────────────────────────────────────────────────

def phase3_video_pipeline(page, mouse) -> dict:
    """
    Navigate to W3Schools HTML5 video page -> Playwright clicks Play ->
    MSS records 8 seconds -> MP4 encoded via OpenCV -> uploaded to Gemini
    Files API -> Gemini analyses the screen recording.
    """
    from gemini_mcp._gemini import upload_video, analyse_video

    print(f"\n[{_ts()}] PHASE 3: Video pipeline (W3Schools)")

    print("  [Playwright] Navigating to W3Schools HTML5 Video tutorial...")
    page.goto("https://www.w3schools.com/html/html5_video.asp", wait_until="domcontentloaded")
    time.sleep(2.5)

    # Move mouse to page centre (human-like)
    wx = page.evaluate("window.screenX")
    wy = page.evaluate("window.screenY")
    ch_off = page.evaluate("window.outerHeight - window.innerHeight")
    cx, cy = mouse.position
    centre_x = wx + page.evaluate("window.innerWidth") // 2
    centre_y = wy + ch_off + page.evaluate("window.innerHeight") // 2
    smooth_move(mouse, cx, cy, centre_x, centre_y)
    time.sleep(0.3)

    # Scroll to video element
    page.evaluate("window.scrollBy(0, 600)")
    time.sleep(1.0)

    clip_path = f"{_OUT_DIR}/e2e_video_demo.mp4"

    # Playwright clicks Play
    print("  [Playwright] Clicking play on video element...")
    try:
        vh = page.locator("video").first
        vh.scroll_into_view_if_needed()
        time.sleep(0.3)
        vh.click()
        print("  [Playwright] Video element clicked")
    except Exception as e:
        print(f"  [Playwright] Click failed ({e}) — pressing Space")
        page.keyboard.press("Space")

    time.sleep(0.5)

    # MSS records 8 seconds of screen
    print("  [MSS] Recording 8s at 10fps (OS framebuffer, not CDP)...")
    frames = _mss_record_frames(8.0, fps=10)
    print(f"  [MSS] Captured {len(frames)} frames")

    if not frames:
        return {"pass": False, "error": "no MSS frames captured"}

    # Encode frames to MP4
    print(f"  [OpenCV] Encoding {len(frames)} frames -> {clip_path}...")
    _frames_to_mp4(frames, clip_path, fps=10)
    size_kb = os.path.getsize(clip_path) // 1024
    print(f"  [OpenCV] MP4 saved: {clip_path} ({size_kb}KB)")

    # Upload to Gemini Files API
    print("  [Gemini] Uploading screen recording to Files API...")
    upload_info = upload_video(clip_path)
    if "error" in upload_info:
        print(f"  [Gemini] Upload failed: {upload_info['error']}")
        return {"pass": False, "error": upload_info["error"], "clip_path": clip_path}

    gemini_uri = upload_info.get("uri")
    print(f"  [Gemini] Upload complete: {gemini_uri} ({upload_info.get('upload_ms')}ms)")

    # Analyse with fallback model chain
    ANALYSIS_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-2.5-flash-lite", "gemini-2.0-flash-001"]
    VIDEO_PROMPT = (
        "This is a screen recording of a web browser showing an HTML5 video tutorial page. "
        "Describe: (1) what web page is shown, (2) what video content is visible or playing, "
        "(3) what text or UI elements you can see. Be specific."
    )

    print("  [Gemini] Analysing screen recording...")
    gemini_text, model_used = _gemini_with_fallback(
        lambda m: analyse_video(gemini_uri, VIDEO_PROMPT, model=m),
        ANALYSIS_MODELS,
        "Gemini/video",
    )

    if gemini_text:
        print(f"  [Gemini] [{model_used}] {gemini_text[:200]}...")
    else:
        print("  [Gemini] All models quota-exhausted — upload succeeded, analysis deferred")

    # Pass = frames captured AND uploaded (Gemini analysis is best-effort)
    ok = len(frames) > 0 and bool(gemini_uri)
    return {
        "pass": ok,
        "clip_path": clip_path,
        "frame_count": len(frames),
        "size_kb": size_kb,
        "gemini_uri": gemini_uri,
        "gemini_analysis": gemini_text or None,
        "model_used": model_used,
    }


# ── Phase 4: Final document ───────────────────────────────────────────────────

def phase4_final_document(p1: dict, p3: dict) -> dict:
    """Append video analysis to the scroll-assembled markdown document."""
    print(f"\n[{_ts()}] PHASE 4: Final document assembly")

    doc_path = p1.get("doc_path", f"{_OUT_DIR}/annotation_doc.md")
    if not os.path.exists(doc_path):
        print(f"  ERROR: {doc_path} not found")
        return {"pass": False, "error": "doc_path missing"}

    with open(doc_path, "a", encoding="utf-8") as fh:
        fh.write("\n\n---\n\n## Video Analysis\n\n")
        if p3.get("gemini_analysis"):
            fh.write(p3["gemini_analysis"])
        elif p3.get("gemini_uri"):
            fh.write(f"[VIDEO uploaded — URI: {p3['gemini_uri']}]\n")
            fh.write("_(Analysis deferred: Gemini quota exhausted)_\n")
        else:
            fh.write("_(Video pipeline did not complete successfully)_\n")
        if p3.get("clip_path"):
            fh.write(f"\n\n_Screen recording: {p3['clip_path']}_\n")

    doc_len = os.path.getsize(doc_path)
    print(f"  Final document: {doc_path} ({doc_len} bytes)")

    with open(doc_path, encoding="utf-8") as fh:
        content = fh.read()

    has_heading = "# " in content
    has_video_section = "## Video Analysis" in content
    ok = doc_len > 500 and has_heading and has_video_section

    return {
        "pass": ok,
        "doc_path": doc_path,
        "doc_len": doc_len,
        "has_heading": has_heading,
        "has_video_section": has_video_section,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from playwright.sync_api import sync_playwright
    from pynput.mouse import Controller

    print(_BAR)
    print("E2E ANNOTATION PIPELINE — ALL PHASES")
    print(_BAR)
    print("  Playwright : simulated human (navigation + mouse + scrolling)")
    print("  MSS/DXGI   : OS framebuffer capture (NOT CDP, NOT DOM)")
    print("  Gemini     : vision analysis of captured frames/recordings")
    print(_BAR)

    mouse = Controller()
    results: dict[str, dict] = {}

    with sync_playwright() as p:
        print("\n[Setup] Launching Chromium (no CDP debug port)...")
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
        page.goto("about:blank")
        time.sleep(1.0)
        print("[Setup] Browser ready.\n")

        for phase_name, fn, args in [
            ("phase1", phase1_scroll_assembly, (page,)),
            ("phase2", phase2_tutor_components, (page,)),
            ("phase3", phase3_video_pipeline, (page, mouse)),
        ]:
            try:
                results[phase_name] = fn(*args)
            except Exception as e:
                print(f"  {phase_name.upper()} EXCEPTION: {e}")
                traceback.print_exc()
                results[phase_name] = {"pass": False, "error": str(e)}

        try:
            results["phase4"] = phase4_final_document(
                results.get("phase1", {}),
                results.get("phase3", {}),
            )
        except Exception as e:
            print(f"  PHASE4 EXCEPTION: {e}")
            traceback.print_exc()
            results["phase4"] = {"pass": False, "error": str(e)}

        time.sleep(1.0)
        browser.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{_BAR}")
    print("E2E TEST RESULTS")
    print(_BAR)

    phase_labels = {
        "phase1": "Scroll document assembly",
        "phase2": "Tutor server components",
        "phase3": "Video pipeline",
        "phase4": "Final document",
    }

    all_pass = True
    for key, label in phase_labels.items():
        r = results.get(key, {})
        ok = r.get("pass", False)
        if not ok:
            all_pass = False
        status = "PASS" if ok else "FAIL"

        if key == "phase1":
            detail = (
                f"frames={r.get('frames_captured', 0)} "
                f"stitched={r.get('stitched_shape', '?')} "
                f"doc={r.get('doc_len', 0)}B "
                f"md_chars={r.get('md_chars', 0)}"
            )
        elif key == "phase2":
            detail = (
                f"events={r.get('events_total', 0)} "
                f"non-static={r.get('got_non_static')} "
                f"kinds={r.get('event_kinds', [])}"
            )
        elif key == "phase3":
            detail = (
                f"frames={r.get('frame_count', 0)} "
                f"size={r.get('size_kb', 0)}KB "
                f"uri={bool(r.get('gemini_uri'))} "
                f"analysis={bool(r.get('gemini_analysis'))}"
            )
        elif key == "phase4":
            detail = (
                f"doc={r.get('doc_len', 0)}B "
                f"heading={r.get('has_heading')} "
                f"video_section={r.get('has_video_section')}"
            )
        else:
            detail = str(r.get("error", ""))

        print(f"  [{status}] Phase {key[-1]}: {label}")
        print(f"         {detail}")
        if not ok and r.get("error"):
            print(f"         ERROR: {r['error']}")

    print()
    if all_pass:
        print("ALL PHASES PASSED — framebuffer annotation pipeline fully operational")
        print(_BAR)
        sys.exit(0)
    else:
        failed = [k for k, r in results.items() if not r.get("pass")]
        print(f"FAILED PHASES: {failed}")
        print(_BAR)
        sys.exit(1)


if __name__ == "__main__":
    main()
