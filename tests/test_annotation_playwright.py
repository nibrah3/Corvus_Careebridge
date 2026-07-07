"""
End-to-end annotation pipeline test using a real Playwright browser.

Strategy:
  1. Serve mock_annotation.html via a local HTTP server
  2. Launch Playwright Chromium headful (visible) with --remote-debugging-port=9222
  3. Navigate to the mock page via Playwright
  4. Use CDPExecutor (connected to port 9222 via a NEW tab) to run task extraction
  5. Call annotate_image() with the extracted image URL and question
  6. Use Playwright to verify the correct button was clicked and "Done" was pressed

This tests the full annotation pipeline logic end-to-end in a real browser.

Run: C:/Python314/python.exe -m pytest E:/Corvus_Careebridge/tests/test_annotation_playwright.py -v -s
"""
from __future__ import annotations

import os
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, Page

sys.path.insert(0, r"E:\Corvus_Careebridge")

TESTS_DIR   = Path(__file__).parent
MOCK_PORT   = 18899
MOCK_URL    = f"http://127.0.0.1:{MOCK_PORT}/mock_annotation.html"
DEBUG_PORT  = 9222


# ── Local HTTP server fixture ──────────────────────────────────────────────────

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="session")
def http_server():
    """Serve the tests/ directory so mock_annotation.html is reachable."""
    os.chdir(TESTS_DIR)
    server = HTTPServer(("127.0.0.1", MOCK_PORT), _QuietHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()


# ── Playwright browser fixture ─────────────────────────────────────────────────

@pytest.fixture(scope="session")
def browser_page(http_server):
    """
    Launch a real Chromium browser via Playwright with CDP exposed on DEBUG_PORT.
    Returns the Playwright Page pointed at the mock annotation page.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                f"--remote-debugging-port={DEBUG_PORT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
            ],
        )
        page = browser.new_page()
        page.goto(MOCK_URL, wait_until="load")
        time.sleep(1.0)  # let SPA settle
        yield page
        browser.close()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_mock_page_loads(browser_page: Page):
    """Verify the mock annotation page loaded correctly."""
    title = browser_page.title()
    assert "Mock Annotation" in title or "annotation" in title.lower()

    question = browser_page.inner_text("h2.task-question")
    assert "animal" in question.lower(), f"Question text unexpected: {question!r}"

    options = browser_page.query_selector_all('[role="radio"]')
    assert len(options) == 4, f"Expected 4 option buttons, found {len(options)}"


def test_gemini_annotation_on_page(browser_page: Page):
    """
    Extract task info via Playwright JS eval, call Gemini, verify it returns 'Cat'.
    This tests the Gemini perception path end-to-end.
    """
    from careerbridge.gemini_vision import annotate_image

    # Extract the same data the pipeline would extract from DOM
    image_url = browser_page.evaluate("document.querySelector('.subject img').src")
    question  = browser_page.evaluate(
        "document.querySelector('h2.task-question').innerText.trim()"
    )
    options   = browser_page.evaluate(
        "Array.from(document.querySelectorAll('[role=\"radio\"]'))"
        ".map(b => b.textContent.trim())"
    )

    assert image_url, "Image URL not extracted"
    assert question,  "Question not extracted"
    assert len(options) >= 2, "Options not extracted"

    print(f"\n  Question : {question}")
    print(f"  Image URL: {image_url[:80]}...")
    print(f"  Options  : {options}")

    answer = annotate_image(image_url, question, options)
    print(f"  Gemini   : {answer!r}")

    assert answer is not None, "Gemini returned None — check GEMINI_API_KEY and quota"
    assert answer in options,  f"Gemini answer {answer!r} is not in options {options}"
    assert answer == "Cat",    f"Expected 'Cat', Gemini said {answer!r}"


def test_cdp_extracts_text_images_options(browser_page: Page):
    """
    Verify CDPExecutor (our system) correctly extracts all three element types
    from a live page: question text, image URL, and labelled option list with IDs.

    Playwright is used only to ensure the browser is open and navigated —
    the extraction itself is done by CDPExecutor, which is what production uses.
    """
    from careerbridge.cdp_executor import CDPExecutor

    cdp = CDPExecutor()
    cdp.connect(port=DEBUG_PORT)

    # --- TEXT extraction ---
    question = cdp.eval_js(
        "(function(){"
        "  var el = document.querySelector('h2.task-question');"
        "  return el ? el.innerText.trim() : '';"
        "})()"
    )
    assert question and "animal" in question.lower(), f"Text extraction failed: {question!r}"

    # --- IMAGE extraction ---
    image_url = cdp.eval_js(
        "(function(){"
        "  var img = document.querySelector('.subject img');"
        "  return img ? img.src : '';"
        "})()"
    )
    assert image_url and image_url.startswith("http"), f"Image URL extraction failed: {image_url!r}"
    assert "Cat03" in image_url, f"Wrong image URL: {image_url!r}"

    # --- OPTIONS extraction (text + clickable IDs) ---
    raw = cdp.eval_js(
        "Array.from(document.querySelectorAll('[role=\"radio\"]'))"
        ".map(function(b){ return {text: b.textContent.trim(), id: b.id}; })"
    ) or []
    options = [o for o in raw if isinstance(o, dict) and o.get("text")]
    labels  = [o["text"] for o in options]
    ids     = [o["id"]   for o in options]

    assert len(options) == 4,            f"Expected 4 options, got {len(options)}: {options}"
    assert "Cat"  in labels,             f"'Cat' not in extracted options: {labels}"
    assert "Dog"  in labels,             f"'Dog' not in extracted options: {labels}"
    assert all(i.startswith("opt-") for i in ids), f"Unexpected button IDs: {ids}"

    print(f"\n  [TEXT   ] {question!r}")
    print(f"  [IMAGE  ] {image_url[:72]}...")
    print(f"  [OPTIONS] {options}")

    cdp.disconnect()


def test_annotation_pipeline_cdp(http_server):
    """
    Test CDPExecutor task extraction against the mock page.

    Launches a separate Chromium process (not Playwright-managed) with
    --remote-debugging-port=9223 so CDPExecutor can attach without conflict.
    Uses a fresh temp profile dir each run to avoid Chrome's singleton lock
    blocking startup when another Chrome is already running.
    """
    import subprocess
    import tempfile
    import shutil
    import socket as _socket
    from careerbridge.cdp_executor import CDPExecutor, CDPError

    chromium = (
        r"C:\Users\Mike\AppData\Local\ms-playwright"
        r"\chromium-1200\chrome-win64\chrome.exe"
    )
    if not Path(chromium).exists():
        pytest.skip(f"Playwright Chromium not found at {chromium}")

    # Fresh profile dir each run — no stale SingletonLock from prior crashed runs
    tmp_profile = tempfile.mkdtemp(prefix="cdp-test-")
    proc = subprocess.Popen(
        [
            chromium,
            "--remote-debugging-port=9223",
            f"--user-data-dir={tmp_profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Poll until the CDP port accepts connections (up to 30s)
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                with _socket.create_connection(("127.0.0.1", 9223), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.3)
        else:
            pytest.fail("Chromium CDP port 9223 never became available (30s timeout)")

        cdp = CDPExecutor()
        cdp.connect(port=9223)
        cdp.navigate(MOCK_URL, timeout=20.0)
        time.sleep(1.0)

        # Build a mini-pipeline and extract the task
        question = cdp.eval_js(
            "(function() {"
            "  var el = document.querySelector('h2.task-question');"
            "  return el ? el.innerText.trim() : '';"
            "})()"
        )
        image_url = cdp.eval_js(
            "(function() {"
            "  var img = document.querySelector('.subject img');"
            "  return img ? img.src : '';"
            "})()"
        )
        raw_opts = cdp.eval_js(
            "Array.from(document.querySelectorAll('[role=\"radio\"]'))"
            ".map(function(b) { return {text: b.textContent.trim(), id: b.id}; })"
        ) or []
        options = [o["text"] for o in raw_opts if isinstance(o, dict) and o.get("text")]

        print(f"\n  CDPExecutor question : {question!r}")
        print(f"  CDPExecutor image_url: {image_url[:80]}...")
        print(f"  CDPExecutor options  : {options}")

        assert question and "animal" in question.lower(), f"Bad question: {question!r}"
        assert image_url and "Cat03" in image_url, f"Bad image_url: {image_url!r}"
        assert "Cat" in options, f"Cat not in options: {options}"

        # Test Gemini annotation via CDPExecutor path
        from careerbridge.gemini_vision import annotate_image
        answer = annotate_image(image_url, question, options)
        print(f"  Gemini answer        : {answer!r}")
        assert answer == "Cat", f"Expected 'Cat', got {answer!r}"

        cdp.disconnect()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        shutil.rmtree(tmp_profile, ignore_errors=True)
