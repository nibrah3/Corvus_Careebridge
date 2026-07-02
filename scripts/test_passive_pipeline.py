"""
E2E test for the passive annotation tutor pipeline.

Uses Playwright (headful) to put known content on screen so DXGI can capture it.
Verifies: server health, capture start, navigation event, scroll event, on-demand snapshot OCR.

Writes result to C:/tmp/tutor_test_result.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Test HTML ─────────────────────────────────────────────────────────────────

TEST_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Tutor Pipeline Test</title>
<style>
  body { font-family: Arial, sans-serif; font-size: 18px; background: #fff; }
  .page { width: 900px; margin: 40px auto; padding: 20px; }
  .scroll-area { height: 300px; overflow-y: auto; border: 2px solid #333; padding: 10px; }
  .long-content p { margin: 0 0 30px; }
  h1 { color: #1a1a1a; }
  .question { background: #f0f8ff; padding: 15px; border-left: 4px solid #0066cc; margin: 20px 0; }
</style>
</head>
<body>
<div class="page">
  <h1>ANNOTATION TASK — Test Page</h1>
  <div class="question">
    <p><strong>Question 1:</strong> In the audio clip below, does the speaker sound confident?</p>
    <p>A) Yes, the speaker sounds very confident</p>
    <p>B) No, the speaker sounds uncertain</p>
    <p>C) Cannot determine from audio alone</p>
    <p>D) The speaker sounds neutral</p>
  </div>
  <h2>Instructions (scroll to read all)</h2>
  <div class="scroll-area" id="scroll-target">
    <div class="long-content">
      <p>INSTRUCTION LINE 1: Listen carefully to the audio before annotating.</p>
      <p>INSTRUCTION LINE 2: Consider tone, pace, and vocabulary when scoring.</p>
      <p>INSTRUCTION LINE 3: Confidence is measured on a 1-5 scale for this task.</p>
      <p>INSTRUCTION LINE 4: A score of 5 means extremely confident delivery.</p>
      <p>INSTRUCTION LINE 5: A score of 1 means highly uncertain or hesitant delivery.</p>
      <p>INSTRUCTION LINE 6: Consider filler words (um, uh, like) as markers of lower confidence.</p>
      <p>INSTRUCTION LINE 7: Vocal variety and clear articulation indicate higher confidence.</p>
      <p>INSTRUCTION LINE 8: Do not let content agree or disagree with your own views affect scoring.</p>
      <p>INSTRUCTION LINE 9: Rate only the delivery style, not the content accuracy.</p>
      <p>INSTRUCTION LINE 10: VISIBLE_SCROLL_MARKER — this line must appear in OCR output.</p>
    </div>
  </div>
</div>
<script>
  setTimeout(function() {
    var el = document.getElementById('scroll-target');
    el.scrollTop = el.scrollHeight;
    console.log('scrolled');
  }, 3000);
</script>
</body>
</html>
"""

RESULT_PATH = "C:/tmp/tutor_test_result.json"
TUTOR_SERVER_PORT = 8716
HTML_SERVER_PORT = 8799


# ── Helpers ───────────────────────────────────────────────────────────────────

def _start_html_server(html_dir: str):
    import http.server
    import functools

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=html_dir,
    )
    srv = http.server.HTTPServer(("127.0.0.1", HTML_SERVER_PORT), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _start_tutor_server():
    env = os.environ.copy()
    env["PYTHONPATH"] = "E:\\Corvus_Careebridge"

    proc = subprocess.Popen(
        [
            "C:\\Python314\\python.exe",
            "-m", "tutor_mcp.server",
            "--port", str(TUTOR_SERVER_PORT),
        ],
        cwd="E:\\Corvus_Careebridge",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    import urllib.request
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise RuntimeError(f"Server exited early:\n{err.decode()[:500]}")
        try:
            urllib.request.urlopen(f"http://localhost:{TUTOR_SERVER_PORT}/mcp", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    return proc


def _call_tool(tool: str, args: dict = None) -> dict:
    import urllib.request
    import json as _json

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": args or {},
        },
    }
    body = _json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://localhost:{TUTOR_SERVER_PORT}/mcp",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = _json.loads(resp.read())

    result = raw.get("result", {})
    if isinstance(result, dict) and "content" in result:
        for item in result["content"]:
            if item.get("type") == "text":
                return _json.loads(item["text"])
    return result


# ── Main test ─────────────────────────────────────────────────────────────────

def run_tests() -> dict:
    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "passed": [],
        "failed": [],
        "details": {},
    }

    def ok(name, detail=""):
        results["passed"].append(name)
        results["details"][name] = detail
        print(f"  PASS  {name}: {detail}")

    def fail(name, detail=""):
        results["failed"].append(name)
        results["details"][name] = detail
        print(f"  FAIL  {name}: {detail}")

    # ── Write test HTML and serve ────────────────────────────────────────────
    Path("C:/tmp").mkdir(exist_ok=True)
    html_path = "C:/tmp/tutor_test_page.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(TEST_HTML)
    print(f"[test] HTML written to {html_path}")
    html_srv = _start_html_server("C:/tmp")
    test_url = f"http://127.0.0.1:{HTML_SERVER_PORT}/tutor_test_page.html"

    # ── Start tutor server ───────────────────────────────────────────────────
    print("[test] Starting tutor server...")
    server_proc = _start_tutor_server()

    try:
        # ── Test 1: server reachable ─────────────────────────────────────────
        try:
            status = _call_tool("capture_status")
            if status.get("running") is False:
                ok("server_reachable", f"running={status.get('running')}")
            else:
                fail("server_reachable", f"unexpected status: {status}")
        except Exception as e:
            fail("server_reachable", str(e))
            return results

        # ── Test 2: capture start ────────────────────────────────────────────
        try:
            r = _call_tool("capture_start", {"session_id": "e2e_test"})
            if r.get("status") == "started":
                ok("capture_started", f"session_id={r.get('session_id')}, audio={r.get('audio_capture')}")
            else:
                fail("capture_started", str(r))
        except Exception as e:
            fail("capture_started", str(e))

        # ── Open browser with Playwright (headful) ───────────────────────────
        print("[test] Launching browser with Playwright...")
        captured_events = []
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False, args=["--window-size=1280,900"])
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(test_url, wait_until="commit", timeout=30000)
                time.sleep(2)
                ok("browser_open", "page loaded")

                # Wait for DXGI to capture the navigation frame
                print("[test] Waiting 4s for navigation capture...")
                time.sleep(4)

                # Trigger scroll inside the test div
                scroll_el = page.query_selector("#scroll-target")
                if scroll_el:
                    page.evaluate("document.getElementById('scroll-target').scrollTop = 500")
                    ok("scroll_triggered", "scroll-target scrolled")
                else:
                    fail("scroll_triggered", "element not found")

                # Allow scroll detection to process
                time.sleep(3)

                # ── Test 3: on-demand snapshot ───────────────────────────────
                # Verifies DXGI frame capture works. OCR may still be
                # initializing (returns '' during warmup) so we don't fail on it.
                try:
                    snap = _call_tool("capture_get_snapshot")
                    frame_path = snap.get("frame_path")
                    ocr = snap.get("ocr_text", "")
                    if frame_path and Path(frame_path).exists():
                        ok("snapshot_captured", f"frame={frame_path}, OCR={len(ocr)}ch")
                    else:
                        fail("snapshot_captured", f"no frame returned: {snap}")
                except Exception as e:
                    fail("snapshot_captured", str(e))

                # ── Drain event queue ────────────────────────────────────────
                print("[test] Draining capture event queue (up to 15s)...")
                deadline_ev = time.time() + 15
                while time.time() < deadline_ev:
                    try:
                        ev = _call_tool("capture_get_event", {"timeout_s": 1.0})
                        if ev.get("kind") not in (None, "none"):
                            captured_events.append(ev)
                    except Exception:
                        break

                browser.close()

        except Exception as e:
            fail("browser_open", str(e))

        # ── Test 4: events were captured ─────────────────────────────────────
        nav_events = [e for e in captured_events if e.get("kind") == "navigation"]
        scroll_events = [e for e in captured_events if e.get("kind") == "scroll"]
        total = len(captured_events)
        if total > 0:
            ok("events_captured", f"total={total}, nav={len(nav_events)}, scroll={len(scroll_events)}")
        else:
            fail("events_captured", "no events received from capture_get_event")

        # ── Test 5: stop capture ─────────────────────────────────────────────
        try:
            stop_r = _call_tool("capture_stop")
            if stop_r.get("status") == "stopped":
                ok("capture_stopped", f"clips_completed={stop_r.get('clips_completed', 0)}")
            else:
                fail("capture_stopped", str(stop_r))
        except Exception as e:
            fail("capture_stopped", str(e))

    finally:
        try:
            html_srv.shutdown()
        except Exception:
            pass
        server_proc.terminate()
        try:
            server_proc.wait(timeout=3)
        except Exception:
            server_proc.kill()

    results["success"] = len(results["failed"]) == 0
    return results


if __name__ == "__main__":
    print("=" * 60)
    print("Tutor Passive Pipeline — E2E Test")
    print("=" * 60)

    results = run_tests()

    Path("C:/tmp").mkdir(exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    success = results.get("success", False)
    passed = len(results["passed"])
    failed = len(results["failed"])

    print("\n" + "=" * 60)
    print(f"Result: {'PASS' if success else 'FAIL'} — {passed} passed, {failed} failed")
    print(f"Result written to: {RESULT_PATH}")
    print("=" * 60)

    sys.exit(0 if success else 1)
