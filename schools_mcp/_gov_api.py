"""
College Scorecard API — fetch all accredited US institution candidates.

Fetches ALL operating, Title-IV-eligible schools — community colleges,
4-year universities, online schools — with no pre-filtering by school type.
The 6-criteria web-crawl + Claude analysis determines who qualifies:
  community_college, no_id_verification, no_transcript_required,
  monthly_enrollment, instant_acceptance, monthly_refund.

Any institution (community college OR university) that passes at least one
criterion is included in the CareerBridge schools report.

API docs: https://collegescorecard.ed.gov/data/documentation/
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

_BASE = "https://api.data.gov/ed/collegescorecard/v1/schools"
_KEY  = (os.environ.get("DATA_GOV_API_KEY") or
         os.environ.get("SCORECARD_API_KEY") or
         os.environ.get("COLLEGE_SCORECARD_API_KEY", ""))

_FIELDS = ",".join([
    "id",
    "school.name",
    "school.school_url",
    "school.city",
    "school.state",
    "school.online_only",
    "school.predominant_degree",
    "school.open_admissions_policy",
    "school.ownership",
    "aid.federal_loan_rate",
    "aid.pell_grant_rate",
])

# Fetch ALL accredited, operating US institutions — no pre-filtering by school type.
# Community colleges, 4-year universities, online schools, open-admissions schools —
# all are candidates. The 6-criteria web-crawl analysis does the actual filtering.
# "Online" in CareerBridge means "offers online/remote study", not a Scorecard flag.
_QUERIES = [
    {
        "school.operating": 1,
        # Note: aid.federal_loan_rate__range is not supported on api.data.gov new endpoint
        # school.operating=1 already filters to active institutions (6,197 total)
    },
]

_PER_PAGE = 100


def _page(params: dict, page: int) -> list[dict]:
    if not _KEY:
        log.warning("COLLEGE_SCORECARD_API_KEY not set")
        return []
    try:
        r = requests.get(
            _BASE,
            params={**params, "fields": _FIELDS, "per_page": _PER_PAGE,
                    "page": page, "api_key": _KEY},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        log.warning("Scorecard API page %d failed: %s", page, e)
        return []


def _norm(raw: dict) -> Optional[dict]:
    url = (raw.get("school.school_url") or "").strip().lower()
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    name = (raw.get("school.name") or "").strip()
    if not name:
        return None
    return {
        "scorecard_id":        raw.get("id"),
        "name":                name,
        "url":                 url,
        "city":                raw.get("school.city", ""),
        "state":               raw.get("school.state", ""),
        "online_only":         bool(raw.get("school.online_only")),
        "open_admissions":     bool(raw.get("school.open_admissions_policy")),
        "is_community_college": raw.get("school.predominant_degree") == 2,
        "ownership":           raw.get("school.ownership"),
        "federal_loan_rate":   float(raw.get("aid.federal_loan_rate") or 0),
        "pell_grant_rate":     float(raw.get("aid.pell_grant_rate") or 0),
    }


_LOCAL_JSON = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "scorecard_candidates.json"
)


def _load_local_json() -> list[dict]:
    """Load pre-parsed candidates from the local CSV-derived JSON snapshot."""
    import json
    try:
        with open(_LOCAL_JSON, encoding="utf-8") as f:
            data = json.load(f)
        log.info("[scorecard] Loaded %d candidates from local snapshot (%s)", len(data), _LOCAL_JSON)
        return data
    except Exception as e:
        log.warning("[scorecard] Could not load local snapshot: %s", e)
        return []


def fetch_candidates(filters: list = None, limit: int = 99999) -> list[dict]:
    """
    Return ALL accredited US institutions — community colleges AND universities.
    No pre-filtering by school type; criteria analysis does the actual gating.

    Strategy:
      1. Live Scorecard API (all operating, Title-IV schools).
      2. Fallback: local JSON at data/scorecard_candidates.json (built from
         bulk CSV download when API domain is unreachable).

    `filters` and `limit` accepted for backward compat, ignored.
    """
    # ── Try live API ──────────────────────────────────────────────────────────
    if _KEY:
        seen_ids: set = set()
        results: list[dict] = []

        for params in _QUERIES:
            page = 0
            while True:
                batch = _page(params, page)
                if not batch:
                    break
                for raw in batch:
                    sid = raw.get("id")
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    normed = _norm(raw)
                    if normed:
                        results.append(normed)
                if len(batch) < _PER_PAGE:
                    break
                page += 1
                time.sleep(0.2)

        if results:
            log.info("[scorecard] API: %d unique candidates", len(results))
            return results

        log.warning("[scorecard] API returned 0 results — using local JSON snapshot")

    # ── Fallback: local JSON from bulk CSV ────────────────────────────────────
    return _load_local_json()
