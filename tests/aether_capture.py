"""
Aether Capture CLI — command-driven video+audio observer
=========================================================
You navigate the browser manually; this tool captures whatever
is playing on screen + system audio, then asks Gemini to analyse it.

  watch [LABEL]          record screen+audio until you press ENTER
                         label auto-increments A→B→C if not specified
  analyse LABEL          (re-)send a capture to Gemini for analysis
  compare LABEL1 LABEL2  side-by-side Gemini comparison (both videos in one request)
  criteria [TEXT]        show or replace the evaluation criteria sent to Gemini
  list                   show all captures this session
  clear                  wipe captures and reset label counter
  help                   this help
  quit / exit / q        stop WASAPI and exit
"""
from __future__ import annotations
import os
import sys
import time
import threading
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"E:\Corvus_Careebridge")
sys.path.insert(0, r"E:\ai-tutor")

_OUT_DIR = Path("C:/tmp/aether")
_OUT_DIR.mkdir(parents=True, exist_ok=True)

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-001",
]

RETRY_MARKERS = (
    "quota", "Quota", "RESOURCE_EXHAUSTED", "429", "exhausted", "FreeTier",
    "PerDay", "503", "UNAVAILABLE", "unavailable", "high demand", "Service Unavailable",
)

DEFAULT_CRITERIA = (
    "Evaluate this screen recording on the following criteria:\n"
    "1. MOTION QUALITY — is movement natural with physical weight, or robotic/artificial?\n"
    "2. VISUAL ACCURACY — correct geometry, objects interact properly, no clipping or pass-through?\n"
    "3. AUDIO SYNC — does audio match visuals frame-by-frame? lip sync? sound effects timed to action?\n"
    "4. AUDIO QUALITY — clarity of speech, background noise, interference, sound artifacts?\n"
    "5. AI ARTIFACTS — flickering, distortion, impossible geometry, hallucinated elements?\n"
    "6. INSTRUCTION FOLLOWING — describe exactly what is shown and heard; note any mismatches.\n"
    "7. SUMMARY — one paragraph conclusion.\n\n"
    "Be specific. Reference timestamps and frame-level details where possible."
)

# ── Mutable session state (dict avoids 'global' keyword) ─────────────────────
_state: dict = {
    "captures": {},           # label -> capture info dict
    "criteria": DEFAULT_CRITERIA,
    "label_iter": iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
}


# ── Gemini key+model rotation ─────────────────────────────────────────────────

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
                print(f"  [{label}] {model}: empty — next")
    finally:
        set_key_override(None)
    return "", "none"


# ── Screen + audio capture ────────────────────────────────────────────────────

def _dxcam_record_until_stopped(fps: int, stop_event: threading.Event,
                                  result_list: list, max_s: float = 600) -> None:
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


def _encode_muxed_mp4(frames_ts: list, audio_chunks: list, output_path: str) -> str:
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
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        vf = av.VideoFrame.from_ndarray(rgb, format="rgb24")
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
            samp = pcm_f[i: i + CHUNK]
            if len(samp) < CHUNK:
                samp = np.pad(samp, ((0, CHUNK - len(samp)), (0, 0)))
            af = av.AudioFrame.from_ndarray(np.ascontiguousarray(samp.T), format="fltp", layout=layout)
            af.sample_rate = sample_rate
            af.pts = pts
            pts += CHUNK
            for pkt in a.encode(af):
                out.mux(pkt)
        for pkt in a.encode(None):
            out.mux(pkt)

    out.close()
    return output_path


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_watch(label: str | None, audio_mod) -> None:
    if not label:
        try:
            label = next(_state["label_iter"])
        except StopIteration:
            print("  [Error] Label sequence exhausted — provide a label manually (e.g. 'watch X')")
            return

    label = label.upper()
    mp4_path = str(_OUT_DIR / f"capture_{label}.mp4")

    print()
    print(f"  {'─' * 54}")
    print(f"  RECORDING  [{label}]  ·  press ENTER when done")
    print(f"  {'─' * 54}")
    print("  → Play the video in your browser now")
    print("  → Switch back here and press ENTER to stop")
    print()

    FPS = 10
    MAX_S = 600  # 10-minute hard cap
    stop_event = threading.Event()
    frames_result: list = []

    rec_thread = threading.Thread(
        target=_dxcam_record_until_stopped,
        args=(FPS, stop_event, frames_result, MAX_S),
        daemon=True,
    )
    audio_start_t = time.time()
    rec_thread.start()

    input()  # blocks until user presses ENTER

    stop_event.set()
    rec_thread.join(timeout=5)
    audio_end_t = time.time()

    duration = audio_end_t - audio_start_t
    frame_count = len(frames_result)
    print(f"  [Capture] {frame_count} frames over {duration:.1f}s")

    if frame_count == 0:
        print("  [Error] No frames captured — is dxcam working?")
        return

    chunks = audio_mod.get_window(audio_start_t, audio_end_t)
    has_audio = bool(chunks)
    print(f"  [Audio]   {len(chunks)} chunks | {'audio present' if has_audio else 'NO AUDIO — check WASAPI'}")

    print(f"  [Mux]     Encoding → {mp4_path} ...")
    try:
        _encode_muxed_mp4(frames_result, chunks, mp4_path)
        kb = os.path.getsize(mp4_path) // 1024
        print(f"  [Mux]     Done: {kb}KB")
    except Exception as e:
        print(f"  [Mux]     FAILED: {e}")
        traceback.print_exc()
        return

    _state["captures"][label] = {
        "mp4_path": mp4_path,
        "kb": kb,
        "frames": frame_count,
        "audio_chunks": len(chunks),
        "has_audio": has_audio,
        "duration_s": duration,
        "analysis": None,
        "upload_uri": None,
    }
    print(f"\n  [OK] [{label}] saved ({kb}KB, {duration:.0f}s).")
    print(f"       Run: analyse {label}   or   compare A B")
    print()


def _ensure_uploaded(label: str) -> str | None:
    from gemini_mcp._gemini import upload_file
    cap = _state["captures"][label]
    if cap.get("upload_uri"):
        return cap["upload_uri"]
    print(f"  [Upload] Uploading [{label}] ({cap['kb']}KB) ...")
    info = upload_file(cap["mp4_path"], mime_type="video/mp4")
    if "error" in info:
        print(f"  [Upload] FAILED: {info['error']}")
        return None
    cap["upload_uri"] = info["uri"]
    print(f"  [Upload] Done ({info['upload_ms']}ms)")
    return info["uri"]


def cmd_analyse(label: str) -> str:
    from gemini_mcp._gemini import analyse_files

    label = label.upper()
    if label not in _state["captures"]:
        print(f"  [Error] No capture for '{label}'. Run 'watch {label}' first.")
        return ""

    cap = _state["captures"][label]
    uri = _ensure_uploaded(label)
    if not uri:
        return ""

    has_audio = cap["has_audio"]
    mode = "screen recording with embedded system audio" if has_audio else "screen recording (no audio)"
    prompt = (
        f"This is a {mode} captured from a browser window. "
        f"Recording duration: {cap['duration_s']:.0f}s.\n\n"
        + _state["criteria"]
    )

    print(f"  [Gemini] Analysing [{label}] ...")
    analysis, model = _gemini_with_fallback(
        lambda m: analyse_files([{"uri": uri, "mime_type": "video/mp4"}], prompt, model=m),
        f"analyse/{label}",
    )

    if analysis:
        cap["analysis"] = analysis
        print(f"\n  {'─' * 54}")
        print(f"  [{label}] ANALYSIS via {model}")
        print(f"  {'─' * 54}")
        print(analysis)
        print(f"  {'─' * 54}\n")
    else:
        print(f"  [Gemini] All models exhausted for [{label}]")

    return analysis


def cmd_compare(label1: str, label2: str) -> None:
    from gemini_mcp._gemini import analyse_files

    label1, label2 = label1.upper(), label2.upper()
    for lbl in (label1, label2):
        if lbl not in _state["captures"]:
            print(f"  [Error] No capture for '{lbl}'. Run 'watch {lbl}' first.")
            return

    # Ensure both uploaded
    uri1 = _ensure_uploaded(label1)
    uri2 = _ensure_uploaded(label2)
    if not uri1 or not uri2:
        return

    # Run individual analyses if missing (gives Gemini prior context)
    for lbl in (label1, label2):
        if not _state["captures"][lbl].get("analysis"):
            print(f"  [Info] [{lbl}] not yet analysed — running now...")
            cmd_analyse(lbl)

    compare_prompt = (
        f"You are comparing two screen recordings.\n"
        f"The FIRST video is Response [{label1}].\n"
        f"The SECOND video is Response [{label2}].\n\n"
        + _state["criteria"]
        + f"\n\nFor each criterion above:\n"
        f"  - State which recording ({label1} or {label2}) is better\n"
        f"  - Explain why with specific observations\n\n"
        f"End with:\n"
        f"  OVERALL WINNER: [{label1} or {label2}] — one-sentence reason."
    )

    print(f"  [Gemini] Comparing [{label1}] vs [{label2}] (both videos in one request) ...")
    result, model = _gemini_with_fallback(
        lambda m: analyse_files(
            [
                {"uri": uri1, "mime_type": "video/mp4"},
                {"uri": uri2, "mime_type": "video/mp4"},
            ],
            compare_prompt,
            model=m,
        ),
        f"compare/{label1}v{label2}",
    )

    if result:
        print(f"\n  {'=' * 60}")
        print(f"  COMPARISON  [{label1}] vs [{label2}]  via {model}")
        print(f"  {'=' * 60}")
        print(result)
        print(f"  {'=' * 60}\n")
    else:
        print("  [Gemini] All models exhausted for comparison")


def cmd_list() -> None:
    captures = _state["captures"]
    if not captures:
        print("  No captures yet. Use: watch [LABEL]")
        return
    print()
    for lbl, cap in captures.items():
        analysed = "analysed" if cap.get("analysis") else "not analysed"
        uploaded = "uploaded" if cap.get("upload_uri") else "local only"
        audio = "audio+video" if cap["has_audio"] else "video only"
        print(f"  [{lbl}]  {cap['kb']}KB  {cap['frames']}fr  {cap['duration_s']:.0f}s  "
              f"{audio}  {analysed}  {uploaded}")
        print(f"        {cap['mp4_path']}")
    print()


def cmd_criteria(text: str | None) -> None:
    if not text:
        print(f"\n  Current criteria:\n{_state['criteria']}\n")
        return
    _state["criteria"] = text
    print("  [OK] Criteria updated.")


def cmd_help() -> None:
    print("""
  Commands:
    watch [LABEL]          record screen+audio; press ENTER to stop
                           label auto-increments A→B→C if omitted
    analyse LABEL          send capture to Gemini; print full analysis
    compare LABEL1 LABEL2  Gemini compares both videos in one request
    criteria [TEXT]        show current criteria, or replace with TEXT
    list                   show all captures this session
    clear                  wipe captures and reset label counter
    help                   this help
    quit / exit / q        stop WASAPI and exit

  Typical YouTube workflow:
    1. Open YouTube, find video A
    2. Type: watch A
    3. Click play in browser, watch the video
    4. When done: switch here, press ENTER
    5. Repeat for video B: watch B
    6. Type: compare A B
""")


# ── REPL ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import tutor_mcp._audio as _audio

    print()
    print("=" * 60)
    print("  AETHER CAPTURE CLI — passive video+audio observer")
    print("=" * 60)
    print("  Starting WASAPI loopback...")
    ok = _audio.start()
    print(f"  WASAPI: {'started' if ok else 'FAILED — audio capture will be empty'}")
    print()
    cmd_help()

    try:
        while True:
            try:
                line = input("aether> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not line:
                continue

            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "watch":
                cmd_watch(arg or None, _audio)
            elif cmd == "analyse":
                if not arg:
                    print("  Usage: analyse LABEL")
                else:
                    cmd_analyse(arg)
            elif cmd == "compare":
                labels = arg.split()
                if len(labels) < 2:
                    print("  Usage: compare LABEL1 LABEL2")
                else:
                    cmd_compare(labels[0], labels[1])
            elif cmd == "criteria":
                cmd_criteria(arg or None)
            elif cmd == "list":
                cmd_list()
            elif cmd in ("clear", "reset"):
                _state["captures"].clear()
                _state["label_iter"] = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                print("  [OK] Captures cleared, labels reset to A.")
            elif cmd == "help":
                cmd_help()
            else:
                print(f"  Unknown: '{cmd}'. Type 'help'.")

    finally:
        print("\n  Stopping WASAPI...")
        _audio.stop()
        print("  Bye.")


if __name__ == "__main__":
    # Direct mode: python aether_capture.py watch [LABEL]
    # e.g.  python aether_capture.py watch A
    if len(sys.argv) > 1 and sys.argv[1].lower() == "watch":
        import tutor_mcp._audio as _audio
        label = sys.argv[2].upper() if len(sys.argv) > 2 else "A"
        print(f"\n  Starting WASAPI ...", end=" ", flush=True)
        _audio.start()
        print("ready.")
        cmd_watch(label, _audio)
        analysis = cmd_analyse(label)
        _audio.stop()
        sys.exit(0)
    main()
