"""
ixbrowser_mcp — IXBrowser profile manager and CDP connection router.
Port 8715. Wraps careerbridge/ixbrowser_connector.py — do not duplicate logic.

Tools:
    connect_profile  — open/attach to a profile, return CDP URL + open tabs
    list_profiles    — list all IXBrowser profiles via local API
    get_open_tabs    — list open tabs via CDP /json endpoint
    close_profile    — close a profile cleanly
    reconnect        — close then reopen (fixes stale CDP connections)

Two paths, selected automatically:
    Path A (paid)  — IXBrowser local API port 53200 → CDP URL returned directly
    Path B (free)  — psutil scan for --remote-debugging-port flag on Chromium subprocess
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _minmcp import MinMCP
from careerbridge.ixbrowser_connector import (
    ensure_ixbrowser_running,
    get_cdp_url,
    is_paid_account,
    ix_close_profile,
    ix_open_profile,
)

mcp = MinMCP("ixbrowser")

_IX_API_BASE = "http://127.0.0.1:53200/api/v2"


def _ix_get(endpoint: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(f"{_IX_API_BASE}/{endpoint}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def _tabs_from_port(port: int) -> list[dict]:
    """Read open page tabs from CDP /json endpoint."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json",
            headers={"User-Agent": "CareerBridge-CDP/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            targets = json.loads(r.read())
        return [
            {
                "tab_id": t.get("id", ""),
                "url":    t.get("url", ""),
                "title":  t.get("title", ""),
            }
            for t in targets
            if t.get("type") == "page"
        ]
    except Exception:
        return []


def _port_from_cdp_url(cdp_url: str) -> int | None:
    m = re.search(r":(\d+)", cdp_url or "")
    return int(m.group(1)) if m else None


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def connect_profile(profile_id: str, email: str = "") -> dict:
    """
    Open or attach to an IXBrowser profile and return its CDP URL + open tabs.

    Path A (paid): email is in paid-account list → ix_open_profile() via API.
    Path B (free): psutil scans Chromium subprocess for --remote-debugging-port.

    Returns: { cdp_url, tab_list, active_tab_url, path }
    path is "api" or "psutil" — informational only, caller need not branch on it.
    """
    try:
        ensure_ixbrowser_running()
        try:
            ix_id = int(profile_id)
        except ValueError:
            ix_id = None

        if is_paid_account(email or "") and ix_id is not None:
            cdp_url = ix_open_profile(ix_id)
            path = "api"
        else:
            cdp_url = get_cdp_url(
                user_email=email or None,
                ix_profile_id=ix_id,
            )
            path = "api" if (ix_id and is_paid_account(email or "")) else "psutil"

        port = _port_from_cdp_url(cdp_url)
        tabs = _tabs_from_port(port) if port else []
        active = tabs[0]["url"] if tabs else ""

        return {
            "cdp_url":        cdp_url,
            "tab_list":       tabs,
            "active_tab_url": active,
            "path":           path,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_profiles() -> dict:
    """List all IXBrowser profiles via local API. Returns id, name, proxy, status."""
    try:
        ensure_ixbrowser_running()
        resp = _ix_get("profile-list")
        if "error" in resp:
            return resp
        profiles = []
        for p in resp.get("data") or resp.get("profiles") or []:
            profiles.append({
                "id":     p.get("profile_id") or p.get("id"),
                "name":   p.get("name") or p.get("profile_name", ""),
                "proxy":  p.get("proxy", ""),
                "status": p.get("status", ""),
            })
        return {"profiles": profiles, "count": len(profiles)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_open_tabs(profile_id: str = "", cdp_port: int = 0) -> dict:
    """
    List open page tabs in a running IXBrowser profile.
    Provide cdp_port if known. If omitted, scans common debug ports (9222-9225).
    """
    if cdp_port:
        tabs = _tabs_from_port(cdp_port)
        return {"tabs": tabs, "count": len(tabs)}
    for port in [9222, 9223, 9224, 9225]:
        tabs = _tabs_from_port(port)
        if tabs:
            return {"tabs": tabs, "count": len(tabs), "port": port}
    return {"tabs": [], "count": 0, "error": "No CDP port found on 9222-9225"}


@mcp.tool()
def close_profile(profile_id: str) -> dict:
    """Close an IXBrowser profile cleanly via local API."""
    try:
        ix_id = int(profile_id)
        ix_close_profile(ix_id)
        return {"ok": True, "profile_id": profile_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def reconnect(profile_id: str, email: str = "") -> dict:
    """
    Close then reopen a profile. Use when CDP connection is stale or dropped.
    Waits 2s between close and open to let the browser process fully exit.
    """
    close_profile(profile_id)
    time.sleep(2)
    return connect_profile(profile_id, email=email)


if __name__ == "__main__":
    mcp.run()
