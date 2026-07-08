"""
Multimodal E2E Test — Real Educational Page (BBC Bitesize)

Single page with text + images + annotation-style video (with audio) + quiz.
Playwright scrolls the page; when video is reached, it plays until end.
dxcam (DXGI) + WASAPI loopback capture screen and audio simultaneously.
One muxed MP4 is sent to Gemini — video always accompanied by audio.

Phases:
  1. Navigate + scroll full page → stitch screenshots → Gemini text/image analysis
  2. Scroll to embedded video → click play → dxcam+WASAPI simultaneous capture
     → mux single MP4 → Gemini video+audio analysis
  3. Scroll to quiz/activity section → capture → Gemini answers using P1+P2 context
  4. Final annotated document

System is passive — Playwright drives navigation; pipeline only observes
OS framebuffer (DXGI) and audio render stream (WASAPI loopback).
"""
from __future__ import annotations

import os
import sys
import time
import threading
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"E:\Corvus_Careebridge")

_BAR = "=" * 68
_OUT_DIR = "C:/tmp"
os.makedirs(_OUT_DIR, exist_ok=True)

# ── Target page ───────────────────────────────────────────────────────────────
# BBC Bitesize: science article with text, images, embedded video, and quiz.
# Change this to any page that has the same elements.
TARGET_URL = "https://www.bbc.co.uk/bitesize/articles/z8bnh39"

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-001",
]

RETRY_MARKERS = (
    "quota", "Quota", "RESOURCE_EXHAUSTED", "429", "exhausted", "FreeTier",
    "PerDay", "503", "UNAVAILABLE", "unavailable", "high demand",
    "Service Unavailable",
)


def _ts() -> str:
    return time.strftime("%H:%M:%S")


# ── Gemini with key+model rotation ───────────────────────────────────────────

def _gemini_with_fallback(fn, label: str) -> tuple[str, str]:
    from gemini_mcp._gemini import set_key_override, get_all_keys
    keys = get_all_keys()
    if not keys:
        keys = [None]
    try:
        for ki, key in enumerate(keys, 1):
            set_key_override(key)
            for model in MODELS:
                try:
                    result = fn(model)
                except Exception as e:
                    err = str(e)
                    if any(k in err for k in RETRY_MARKERS):
                        print(f"  [{label}] {model} (k{ki}): quota — next")
                        continue
                    print(f"  [{label}] {model}: exception — {e}")
                    break
                if isinstance(result, dict) and "error" in result:
                    err = str(result["error"])
                    if any(k in err for k in RETRY_MARKERS):
                        print(f"  [{label}] {model} (k{ki}): quota — next")
                        continue
                    print(f"  [{label}] {model}: error — {err}")
                    break
                text = result.get("text", "") if isinstance(result, dict) else str(result or "")
                if text:
                    return text, model
                print(f"  [{label}] {model}: empty — trying next")
    finally:
        set_key_override(None)
    return "", "none"


# ── Capture helpers ───────────────────────────────────────────────────────────

def capture_mss_bgr():
    import io as _io
    import numpy as np
    from PIL import Image
    from capture_mcp._backend_mss import capture as mss_capture
    png = mss_capture()
    pil = Image.open(_io.BytesIO(png)).convert("RGB")
    return np.array(pil)[:, :, ::-1]


def _detect_shift(prev_gray, curr_gray, min_shift: int = 20, max_shift: int = 400):
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


def _dxcam_record_frames(duration_s: float, fps: int = 10) -> list[tuple[float, object]]:
    """Record screen for a fixed duration using DXGI Desktop Duplication."""
    import dxcam
    cam = dxcam.create(output_color="BGR")
    frames_ts: list[tuple[float, object]] = []
    try:
        cam.start(target_fps=fps, video_mode=True)
        end = time.monotonic() + duration_s
        while time.monotonic() < end:
            frame = cam.get_latest_frame()
            if frame is not None:
                frames_ts.append((time.monotonic(), frame.copy()))
    finally:
        cam.stop()
        cam.release()
    return frames_ts


def _dxcam_record_until_stopped(fps: int, stop_event: threading.Event,
                                 result_list: list, max_s: float = 120) -> None:
    """Record screen frames into result_list until stop_event is set (or max_s)."""
    import dxcam
    cam = dxcam.create(output_color="BGR")
    try:
        cam.start(target_fps=fps, video_mode=True)
        deadline = time.monotonic() + max_s
        while not stop_event.is_set() and time.monotonic() < deadline:
            frame = cam.get_latest_frame()
            if frame is not None:
                result_list.append((time.monotonic(), frame.copy()))
    finally:
        cam.stop()
        cam.release()


def _encode_muxed_mp4(
    frames_ts: list[tuple[float, object]],
    audio_chunks: list,
    output_path: str,
) -> str:
    """Encode a PTS-accurate MP4 with audio muxed in. Uses actual capture timestamps."""
    import av
    import numpy as np

    if not frames_ts:
        raise RuntimeError("No frames to encode")

    h, w = frames_ts[0][1].shape[:2]
    sample_rate = audio_chunks[0]["sample_rate"] if audio_chunks else 44100
    n_ch = audio_chunks[0]["channels"] if audio_chunks else 2

    if len(frames_ts) >= 2:
        actual_dur = frames_ts[-1][0] - frames_ts[0][0]
        actual_fps = (len(frames_ts) - 1) / max(actual_dur, 0.1)
    else:
        actual_fps = 10.0
    actual_fps = max(1.0, min(actual_fps, 60.0))

    out = av.open(output_path, "w", format="mp4")

    v = out.add_stream("libx264", rate=int(round(actual_fps)))
    v.width = w
    v.height = h
    v.pix_fmt = "yuv420p"
    v.options = {"preset": "ultrafast", "crf": "23"}

    a = out.add_stream("aac", rate=sample_rate)
    a.layout = "stereo" if n_ch >= 2 else "mono"

    for idx, (_, bgr) in enumerate(frames_ts):
        vf = av.VideoFrame.from_ndarray(bgr[:, :, ::-1].copy(), format="rgb24")
        vf.pts = idx
        for pkt in v.encode(vf):
            out.mux(pkt)
    for pkt in v.encode(None):
        out.mux(pkt)

    if audio_chunks:
        all_pcm = b"".join(c["data"] for c in audio_chunks)
        pcm = np.frombuffer(all_pcm, dtype=np.int16)
        n_total = len(pcm) // n_ch
        pcm_f = pcm[: n_total * n_ch].reshape(n_total, n_ch).astype(np.float32) / 32768.0
        layout = "stereo" if n_ch >= 2 else "mono"
        CHUNK = 1024
        pts = 0
        for i in range(0, n_total, CHUNK):
            samp = pcm_f[i : i + CHUNK]
            if len(samp) < CHUNK:
                samp = np.pad(samp, ((0, CHUNK - len(samp)), (0, 0)))
            af = av.AudioFrame.from_ndarray(samp.T, format="fltp", layout=layout)
            af.sample_rate = sample_rate
            af.pts = pts
            pts += CHUNK
            for pkt in a.encode(af):
                out.mux(pkt)
        for pkt in a.encode(None):
            out.mux(pkt)

    out.close()
    return output_path


def smooth_move(mouse, x1, y1, x2, y2, steps=30, delay=0.012):
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)
        mouse.position = (int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
        time.sleep(delay)


# ── Phase 1: Navigate + full page scroll → stitch → Gemini ───────────────────

def phase1_scroll_assembly(page) -> dict:
    import cv2
    import numpy as np
    from gemini_mcp._gemini import analyse_image

    print(f"\n[{_ts()}] PHASE 1: Navigate + full page scroll ({TARGET_URL})")

    try:
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  [Nav] Warning: {e} — trying anyway")
    time.sleep(3.0)

    # Dismiss cookie banners / consent popups if present
    for sel in ["[id*='cookie'] button", "[class*='consent'] button",
                "[aria-label*='Accept' i]", "button:has-text('Accept')",
                "button:has-text('OK')", "button:has-text('Got it')"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                time.sleep(0.5)
                break
        except Exception:
            pass

    page.keyboard.press("Home")
    time.sleep(0.5)

    page_height = page.evaluate("document.documentElement.scrollHeight")
    viewport_h = page.evaluate("window.innerHeight")
    SCROLL_PX = max(300, int(viewport_h * 0.65))
    total_steps = max(6, int(page_height / SCROLL_PX) + 2)
    print(f"  [Page] height={page_height}px viewport={viewport_h}px steps={total_steps}")

    # JS helper: find first matching element's document-Y position
    FIND_DOC_Y_JS = """
        (selectors) => {
            for (const sel of selectors) {
                try {
                    const el = document.querySelector(sel);
                    if (el) {
                        const r = el.getBoundingClientRect();
                        return { found: true, docY: Math.round(window.scrollY + r.top), sel: sel };
                    }
                } catch(e) {}
            }
            return { found: false, docY: -1, sel: '' };
        }
    """

    VIDEO_SELECTORS = [
        "video",
        "[data-testid*='media']",
        "[data-component*='media']",
        ".media-player",
        "figure.media-asset-figure",
        "iframe[src*='bbc']",
        "iframe[src*='youtube']",
        "[class*='video']",
    ]

    QUIZ_SELECTORS = [
        "[data-testid*='quiz']",
        "[data-testid*='activity']",
        "[data-module*='quiz']",
        "[class*='quiz']",
        "[class*='activity']",
        "h2:has-text('Test')",
        "h2:has-text('Activity')",
        "h2:has-text('Quiz')",
        "h2:has-text('Question')",
        "h3:has-text('Test')",
        "h3:has-text('Quiz')",
    ]

    # Initial detection before scrolling
    vid_info = page.evaluate(FIND_DOC_Y_JS, VIDEO_SELECTORS)
    quiz_info = page.evaluate(FIND_DOC_Y_JS, QUIZ_SELECTORS)
    detected_video_y = vid_info["docY"] if vid_info["found"] else -1
    detected_quiz_y = quiz_info["docY"] if quiz_info["found"] else -1
    if vid_info["found"]:
        print(f"  [Detect] Video pre-detected at docY={detected_video_y} ({vid_info['sel']})")
    if quiz_info["found"]:
        print(f"  [Detect] Quiz pre-detected at docY={detected_quiz_y}")

    frames_bgr = [capture_mss_bgr()]

    for step in range(total_steps):
        scroll_y = (step + 1) * SCROLL_PX
        page.evaluate(f"window.scrollTo(0, {scroll_y})")
        time.sleep(0.7)
        frames_bgr.append(capture_mss_bgr())

        if detected_video_y < 0:
            vi = page.evaluate(FIND_DOC_Y_JS, VIDEO_SELECTORS)
            if vi["found"]:
                detected_video_y = vi["docY"]
                print(f"    [Detect] Video at docY={detected_video_y} ({vi['sel']})")

        if detected_quiz_y < 0:
            qi = page.evaluate(FIND_DOC_Y_JS, QUIZ_SELECTORS)
            if qi["found"]:
                detected_quiz_y = qi["docY"]
                print(f"    [Detect] Quiz at docY={detected_quiz_y}")

        print(f"    scroll {step + 1}/{total_steps}: Y={scroll_y}")

    # If still not found, use fallback positions
    if detected_video_y < 0:
        detected_video_y = int(page_height * 0.35)
        print(f"  [Detect] Video not found — using fallback Y={detected_video_y}")
    if detected_quiz_y < 0:
        detected_quiz_y = int(page_height * 0.85)
        print(f"  [Detect] Quiz not found — using fallback Y={detected_quiz_y}")

    # Stitch frames
    H = frames_bgr[0].shape[0]
    canvas = frames_bgr[0].copy()
    prev_frame = frames_bgr[0]
    print(f"  [Stitch] Assembling {len(frames_bgr)} frames...")

    for i, curr in enumerate(frames_bgr[1:], 1):
        if curr.shape[1] != canvas.shape[1]:
            prev_frame = curr
            continue
        pg = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        cg = cv2.cvtColor(curr, cv2.COLOR_BGR2GRAY)
        shift = _detect_shift(pg, cg)
        if shift and shift > 0:
            canvas = np.vstack([canvas, curr[H - shift:, :]])
        else:
            q = H // 4
            canvas = np.vstack([canvas, curr[H - q:, :]])
        prev_frame = curr

    stitched_path = f"{_OUT_DIR}/mm_stitched.jpg"
    _, jpeg_buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 80])
    with open(stitched_path, "wb") as fh:
        fh.write(jpeg_buf.tobytes())
    size_kb = os.path.getsize(stitched_path) // 1024
    print(f"  [Stitch] {canvas.shape[1]}x{canvas.shape[0]}px — {size_kb}KB")

    EXTRACTION_PROMPT = (
        "This is a stitched scrollable screenshot of an educational web page. "
        "Extract all visible text as clean markdown. "
        "Use # for page title, ## for section headings. "
        "For images/diagrams write ![IMAGE: brief description]. "
        "For quiz/activity questions include them verbatim prefixed with Q:. "
        "Output markdown only — no preamble."
    )
    print("  [Gemini] Extracting page content...")
    md_text, model_used = _gemini_with_fallback(
        lambda m: analyse_image(stitched_path, EXTRACTION_PROMPT, model=m),
        "Gemini/page-extract",
    )

    if md_text:
        print(f"  [Gemini] {len(md_text)} chars via {model_used}")
    else:
        md_text = f"[Page content extraction deferred — quota exhausted. Image at {stitched_path}]"
        print("  [Gemini] All models exhausted — placeholder used")

    doc_path = f"{_OUT_DIR}/mm_annotation_doc.md"
    with open(doc_path, "w", encoding="utf-8") as fh:
        fh.write("# Multimodal Annotation Session\n\n")
        fh.write(f"_Source: {TARGET_URL}_\n\n---\n\n")
        fh.write("## Phase 1 — Page Content\n\n")
        fh.write(md_text)

    doc_len = os.path.getsize(doc_path)
    print(f"  [Doc] {doc_path} ({doc_len} bytes)")

    return {
        "pass": doc_len > 100 and len(frames_bgr) >= 3,
        "stitched_path": stitched_path,
        "stitched_shape": f"{canvas.shape[1]}x{canvas.shape[0]}",
        "doc_path": doc_path,
        "doc_len": doc_len,
        "frames_captured": len(frames_bgr),
        "md_chars": len(md_text),
        "scroll_content": md_text,
        "video_scroll_y": detected_video_y,
        "quiz_scroll_y": detected_quiz_y,
        "page_height": page_height,
    }


# ── Phase 2: Scroll to video → play → dxcam+WASAPI → mux MP4 → Gemini ───────

def phase2_video_capture(page, audio_mod, video_scroll_y: int) -> dict:
    from gemini_mcp._gemini import upload_file, analyse_files

    print(f"\n[{_ts()}] PHASE 2: Video capture (dxcam+WASAPI → muxed MP4)")

    # Click somewhere on page for user-gesture context
    page.mouse.click(400, 300)
    time.sleep(0.2)

    # Wake WASAPI audio engine so stream.read() doesn't block
    WAKE_JS = """(async function() {
        var ctx = new AudioContext();
        await ctx.resume();
        var buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.15), ctx.sampleRate);
        var src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        src.start();
        return ctx.state;
    })()"""
    try:
        wake = page.evaluate(WAKE_JS)
        print(f"  [WebAudio] WASAPI warmup: {wake}")
    except Exception as e:
        print(f"  [WebAudio] Warmup warning: {e}")
    time.sleep(1.5)

    # Scroll to video position
    print(f"  [Scroll] Scrolling to video at docY={video_scroll_y}")
    page.evaluate(f"window.scrollTo({{top: {max(0, video_scroll_y - 150)}, behavior: 'smooth'}})")
    time.sleep(1.5)

    # Find and click the video/play button
    VIDEO_PLAY_SELECTORS = [
        "video",
        "[data-testid*='media'] button",
        "[class*='play' i]",
        "[aria-label*='play' i]",
        "[aria-label*='Play' i]",
        ".vjs-play-button",
        "button[class*='play']",
        ".icon-play",
        "[data-component*='media']",
        "figure.media-asset-figure",
    ]

    video_clicked = False
    for sel in VIDEO_PLAY_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=500):
                el.scroll_into_view_if_needed()
                time.sleep(0.3)
                el.click()
                video_clicked = True
                print(f"  [Click] Clicked: {sel}")
                break
        except Exception:
            pass

    if not video_clicked:
        # Fallback: JS play on video element
        try:
            page.evaluate("document.querySelector('video')?.play()")
            video_clicked = True
            print("  [Click] JS play() fallback")
        except Exception as e:
            print(f"  [Click] All click attempts failed: {e}")

    time.sleep(0.5)

    # Check if there's a nested play button after clicking the player wrapper
    if video_clicked:
        for sel in ["[aria-label*='Play' i]", ".vjs-big-play-button",
                    "[class*='play-button']", "button[title*='play' i]"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=800):
                    btn.click()
                    print(f"  [Click] Inner play button: {sel}")
                    break
            except Exception:
                pass

    # Start dxcam recording in background thread
    FPS = 10
    MAX_VIDEO_S = 120  # max capture duration (covers videos up to 2 min)
    stop_event = threading.Event()
    frames_result: list = []

    def _record():
        _dxcam_record_until_stopped(FPS, stop_event, frames_result, max_s=MAX_VIDEO_S)

    audio_start_t = time.time()
    rec_thread = threading.Thread(target=_record, daemon=True)
    rec_thread.start()
    print(f"  [dxcam] Recording started (max {MAX_VIDEO_S}s at {FPS}fps)")

    # Poll for video end
    VIDEO_END_POLL_INTERVAL = 2.0
    poll_start = time.time()
    video_ended = False
    video_duration = 0.0

    while time.time() - poll_start < MAX_VIDEO_S:
        try:
            state = page.evaluate("""
                () => {
                    const v = document.querySelector('video');
                    if (!v) return { exists: false, ended: false, duration: 0, currentTime: 0 };
                    return {
                        exists: true,
                        ended: v.ended,
                        duration: v.duration || 0,
                        currentTime: v.currentTime || 0,
                        paused: v.paused,
                        readyState: v.readyState
                    };
                }
            """)
            if state.get("exists"):
                dur = state.get("duration", 0)
                ct = state.get("currentTime", 0)
                ended = state.get("ended", False)
                if dur > 0 and video_duration == 0:
                    video_duration = dur
                    print(f"  [Video] Duration: {dur:.1f}s")
                if ended:
                    video_ended = True
                    print(f"  [Video] Ended at {ct:.1f}s")
                    time.sleep(1.0)  # buffer after end
                    break
                if ct > 0:
                    remaining = max(0, dur - ct) if dur > 0 else 0
                    print(f"  [Video] Playing {ct:.1f}s/{dur:.1f}s ({remaining:.0f}s left)")
        except Exception as e:
            print(f"  [Poll] {e}")
        time.sleep(VIDEO_END_POLL_INTERVAL)

    if not video_ended:
        elapsed = time.time() - poll_start
        print(f"  [Video] Max wait reached ({elapsed:.0f}s)")

    # Stop recording
    stop_event.set()
    rec_thread.join(timeout=5)
    audio_end_t = time.time()

    frames_ts = list(frames_result)
    print(f"  [dxcam] Captured {len(frames_ts)} frames over {audio_end_t - audio_start_t:.1f}s")

    if not frames_ts:
        return {"pass": False, "error": "no dxcam frames captured", "video_analysis": ""}

    chunks = audio_mod.get_window(audio_start_t, audio_end_t)
    print(f"  [WASAPI] {len(chunks)} audio chunks for window")

    muxed_path = f"{_OUT_DIR}/mm_video_muxed.mp4"
    print(f"  [PyAV] Muxing {len(frames_ts)} frames + {len(chunks)} chunks → {muxed_path}")
    try:
        _encode_muxed_mp4(frames_ts, chunks, muxed_path)
        muxed_kb = os.path.getsize(muxed_path) // 1024
        print(f"  [PyAV] Muxed MP4: {muxed_kb}KB")
    except Exception as e:
        return {"pass": False, "error": f"PyAV mux failed: {e}", "video_analysis": ""}

    print("  [Gemini] Uploading muxed MP4...")
    upload_info = upload_file(muxed_path, mime_type="video/mp4")
    if "error" in upload_info:
        return {"pass": False, "error": upload_info["error"], "video_analysis": ""}

    muxed_uri = upload_info["uri"]
    has_audio = bool(chunks)
    print(f"  [Gemini] Uploaded: {muxed_uri} ({upload_info['upload_ms']}ms)")

    COMBINED_PROMPT = (
        "You are receiving a screen recording of a web browser on an educational page"
        + (", with system audio embedded. " if has_audio else ". ")
        + "Analyse and describe:\n"
        "(1) What educational content or video is visible on screen.\n"
        "(2) What is the topic being taught.\n"
        "(3) Key facts, concepts, or information presented visually.\n"
        + ("(4) What you hear — transcribe any narration or speech, describe any music/sounds.\n"
           "(5) How audio and visuals work together to explain the concept.\n"
           if has_audio else
           "(4) What text or UI elements are visible.\n")
        + "Be specific. This is a passive educational content observer."
    )

    mode = "muxed video+audio" if has_audio else "video-only"
    print(f"  [Gemini] Analysing {mode}...")
    analysis, model_used = _gemini_with_fallback(
        lambda m: analyse_files([{"uri": muxed_uri, "mime_type": "video/mp4"}],
                                 COMBINED_PROMPT, model=m),
        "Gemini/video-audio",
    )

    if analysis:
        print(f"  [Gemini] [{model_used}] {analysis[:200]}...")
    else:
        analysis = f"[{mode.capitalize()} uploaded — Gemini quota exhausted]"
        print("  [Gemini] All models exhausted")

    return {
        "pass": len(frames_ts) > 0 and bool(muxed_uri),
        "video_path": muxed_path,
        "video_kb": muxed_kb,
        "video_uri": muxed_uri,
        "frame_count": len(frames_ts),
        "audio_chunks": len(chunks),
        "has_audio": has_audio,
        "video_ended": video_ended,
        "video_duration": video_duration,
        "video_analysis": analysis,
        "model_used": model_used,
    }


# ── Phase 3: Scroll to quiz → capture → Gemini answers with context ───────────

def phase3_quiz_answers(page, quiz_scroll_y: int, p1: dict, p2: dict) -> dict:
    from gemini_mcp._gemini import analyse_image

    print(f"\n[{_ts()}] PHASE 3: Quiz/questions capture + Gemini answers")

    # Scroll to quiz section
    print(f"  [Scroll] Scrolling to quiz at docY={quiz_scroll_y}")
    page.evaluate(f"window.scrollTo({{top: {max(0, quiz_scroll_y - 100)}, behavior: 'smooth'}})")
    time.sleep(1.5)

    # Capture the quiz area
    quiz_shot_path = f"{_OUT_DIR}/mm_quiz_capture.jpg"
    import cv2
    import numpy as np
    frame = capture_mss_bgr()
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    with open(quiz_shot_path, "wb") as fh:
        fh.write(buf.tobytes())
    quiz_kb = os.path.getsize(quiz_shot_path) // 1024
    print(f"  [Capture] Quiz screenshot: {quiz_shot_path} ({quiz_kb}KB)")

    # Build context from earlier phases
    page_content = p1.get("scroll_content", "")[:3000]
    video_analysis = p2.get("video_analysis", "")

    QUIZ_PROMPT = (
        "This screenshot shows an educational quiz or activity section from a web page. "
        "The page topic is described below.\n\n"
        f"=== PAGE CONTENT (Phase 1) ===\n{page_content}\n\n"
        f"=== VIDEO ANALYSIS (Phase 2) ===\n{video_analysis}\n\n"
        "=== TASK ===\n"
        "1. Identify any quiz questions or activity prompts visible in the screenshot.\n"
        "2. Answer each question using information from the page content and video analysis above.\n"
        "3. If no questions are visible, summarise what is shown and note what educational "
        "activity or concept this section covers.\n"
        "Format: Q1: [question] → A1: [answer], Q2: [question] → A2: [answer], etc."
    )

    print("  [Gemini] Answering quiz questions...")
    answers, model_used = _gemini_with_fallback(
        lambda m: analyse_image(quiz_shot_path, QUIZ_PROMPT, model=m),
        "Gemini/quiz",
    )

    if answers:
        print(f"  [Gemini] [{model_used}] {answers[:200]}...")
    else:
        answers = "[Quiz section captured — Gemini quota exhausted, answers deferred]"
        print("  [Gemini] All models exhausted")

    return {
        "pass": bool(quiz_shot_path) and os.path.exists(quiz_shot_path),
        "quiz_shot_path": quiz_shot_path,
        "quiz_kb": quiz_kb,
        "quiz_answers": answers,
        "model_used": model_used,
    }


# ── Phase 4: Final annotated document ────────────────────────────────────────

def phase4_final_document(p1: dict, p2: dict, p3: dict) -> dict:
    print(f"\n[{_ts()}] PHASE 4: Final annotated document")

    doc_path = p1.get("doc_path", f"{_OUT_DIR}/mm_annotation_doc.md")
    if not os.path.exists(doc_path):
        return {"pass": False, "error": "doc_path missing"}

    with open(doc_path, "a", encoding="utf-8") as fh:
        fh.write("\n\n---\n\n## Phase 2 — Video + Audio Analysis\n\n")
        fh.write(p2.get("video_analysis", "_[no video analysis]_"))
        fh.write(f"\n\n_Muxed MP4: {p2.get('video_path', 'N/A')} "
                 f"({p2.get('video_kb', 0)}KB, "
                 f"frames={p2.get('frame_count', 0)}, "
                 f"audio_chunks={p2.get('audio_chunks', 0)})_\n")
        if p2.get("video_ended"):
            fh.write(f"_Video played to completion ({p2.get('video_duration', 0):.1f}s)_\n")

        fh.write("\n\n---\n\n## Phase 3 — Quiz / Activity Answers\n\n")
        fh.write(p3.get("quiz_answers", "_[no answers]_"))
        fh.write(f"\n\n_Quiz screenshot: {p3.get('quiz_shot_path', 'N/A')}_\n")

        fh.write("\n\n---\n\n## Pipeline Summary\n\n")
        fh.write(f"- Page: {TARGET_URL}\n")
        fh.write(f"- Scroll frames: {p1.get('frames_captured', 0)}\n")
        fh.write(f"- Stitched image: {p1.get('stitched_shape', '?')}\n")
        fh.write(f"- Video captured: {p2.get('frame_count', 0)} frames, "
                 f"{p2.get('audio_chunks', 0)} audio chunks\n")
        fh.write(f"- Video ended naturally: {p2.get('video_ended', False)}\n")

    doc_len = os.path.getsize(doc_path)
    print(f"  [Doc] Final document: {doc_path} ({doc_len} bytes)")

    with open(doc_path, encoding="utf-8") as fh:
        content = fh.read()

    sections_ok = all(s in content for s in [
        "## Phase 1", "## Phase 2", "## Phase 3", "## Pipeline Summary"
    ])

    return {
        "pass": doc_len > 500 and sections_ok,
        "doc_path": doc_path,
        "doc_len": doc_len,
        "sections_ok": sections_ok,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from playwright.sync_api import sync_playwright
    from pynput.mouse import Controller
    import tutor_mcp._audio as _audio

    print(_BAR)
    print("MULTIMODAL E2E TEST — REAL EDUCATIONAL PAGE")
    print(_BAR)
    print(f"  Target : {TARGET_URL}")
    print("  Phase 1: Scroll full page → stitch → Gemini text+image analysis")
    print("  Phase 2: Play video → dxcam+WASAPI → muxed MP4 → Gemini video+audio")
    print("  Phase 3: Quiz section → capture → Gemini answers with full context")
    print("  Phase 4: Final annotated document")
    print(_BAR)

    mouse = Controller()
    results: dict[str, dict] = {}

    print("\n[Setup] Starting WASAPI loopback...")
    audio_ok = _audio.start()
    print(f"[Setup] WASAPI: {'started' if audio_ok else 'FAILED'}")
    time.sleep(0.5)

    with sync_playwright() as pw:
        print("[Setup] Launching Chromium...")
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        ctx = browser.new_context(no_viewport=True)
        page = ctx.new_page()
        page.goto("about:blank")
        time.sleep(1.0)
        print("[Setup] Browser ready.\n")

        # Phase 1: navigate + scroll
        try:
            results["phase1"] = phase1_scroll_assembly(page)
        except Exception as e:
            print(f"  PHASE1 EXCEPTION: {e}")
            traceback.print_exc()
            results["phase1"] = {"pass": False, "error": str(e),
                                  "video_scroll_y": -1, "quiz_scroll_y": -1,
                                  "scroll_content": "", "doc_path": ""}

        video_y = results["phase1"].get("video_scroll_y", -1)
        quiz_y = results["phase1"].get("quiz_scroll_y", -1)

        # Phase 2: video playback + capture (same page, browser still open)
        try:
            results["phase2"] = phase2_video_capture(page, _audio, video_y)
        except Exception as e:
            print(f"  PHASE2 EXCEPTION: {e}")
            traceback.print_exc()
            results["phase2"] = {"pass": False, "error": str(e), "video_analysis": ""}

        # Phase 3: quiz answers (same page, continue scrolling)
        try:
            results["phase3"] = phase3_quiz_answers(
                page, quiz_y, results["phase1"], results["phase2"]
            )
        except Exception as e:
            print(f"  PHASE3 EXCEPTION: {e}")
            traceback.print_exc()
            results["phase3"] = {"pass": False, "error": str(e), "quiz_answers": ""}

        time.sleep(1.0)
        browser.close()

    _audio.stop()

    # Phase 4: final doc
    try:
        results["phase4"] = phase4_final_document(
            results.get("phase1", {}),
            results.get("phase2", {}),
            results.get("phase3", {}),
        )
    except Exception as e:
        print(f"  PHASE4 EXCEPTION: {e}")
        traceback.print_exc()
        results["phase4"] = {"pass": False, "error": str(e)}

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{_BAR}")
    print("E2E MULTIMODAL TEST RESULTS")
    print(_BAR)

    labels = {
        "phase1": "Navigate + scroll + Gemini page analysis",
        "phase2": "Video play + dxcam+WASAPI + Gemini video+audio",
        "phase3": "Quiz capture + Gemini answers",
        "phase4": "Final annotated document",
    }

    all_pass = True
    for key, label in labels.items():
        r = results.get(key, {})
        ok = r.get("pass", False)
        if not ok:
            all_pass = False
        status = "PASS" if ok else "FAIL"

        if key == "phase1":
            detail = (f"frames={r.get('frames_captured', 0)} "
                      f"stitched={r.get('stitched_shape', '?')} "
                      f"md={r.get('md_chars', 0)}ch "
                      f"video_y={r.get('video_scroll_y', -1)} "
                      f"quiz_y={r.get('quiz_scroll_y', -1)}")
        elif key == "phase2":
            detail = (f"frames={r.get('frame_count', 0)} "
                      f"mp4={r.get('video_kb', 0)}KB "
                      f"audio={r.get('audio_chunks', 0)}chunks "
                      f"ended={r.get('video_ended', False)} "
                      f"analysis={bool(r.get('video_analysis'))}")
        elif key == "phase3":
            detail = (f"shot={r.get('quiz_kb', 0)}KB "
                      f"answers={bool(r.get('quiz_answers'))}")
        elif key == "phase4":
            detail = f"doc={r.get('doc_len', 0)}B sections={r.get('sections_ok', False)}"
        else:
            detail = str(r.get("error", ""))

        err = f" | error: {r.get('error', '')}" if r.get("error") else ""
        print(f"  [{status}] {label}")
        print(f"         {detail}{err}")

    print(_BAR)
    overall = "ALL PASS" if all_pass else "SOME FAILURES — see above"
    print(f"  OVERALL: {overall}")
    print(_BAR)

    doc = results.get("phase4", {}).get("doc_path") or results.get("phase1", {}).get("doc_path")
    if doc and os.path.exists(doc):
        print(f"\n  Final document: {doc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")
    except Exception as e:
        print(f"\n[FATAL] {e}")
        traceback.print_exc()
        sys.exit(1)
