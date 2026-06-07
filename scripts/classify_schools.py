"""
classify_schools.py — Schools classification engine (v2).

GATE: school offers courses completable fully online — students never need
to physically report to campus for those specific online programs.
"Distance Learning," "online courses," hybrid schools all qualify because
their online PROGRAMS meet the criteria. We are NOT requiring the school
to be 100% online-only.

6 filters (applied to ALL schools passing the gate):
  online_available        — confirmed offers online/distance learning courses
  no_transcript_required  — no transcript needed to enroll (open admissions,
                            or explicitly stated)
  monthly_enrollment      — rolling/monthly starts, not fixed semester
  instant_acceptance      — open admissions, no application wait
  community_college       — community or junior college
  no_id_verification      — no identity verification required to enroll

Scorecard API fields used to auto-classify (reduces manual screening):
  school.online_only              -> online_available (strong signal)
  school.open_admissions_policy   -> no_transcript_required + instant_acceptance
  latest.admissions.admission_rate.overall (null = open)
  school.carnegie_undergrad       -> community_college type detection
  school.ownership                -> public/private (CC context)
  school.degrees_awarded.predominant -> degree type
  latest.student.size             -> enrollment size

Usage:
    python scripts/classify_schools.py --machine primary   --batch 100
    python scripts/classify_schools.py --machine secondary --batch 100
"""
from __future__ import annotations

import argparse, json, logging, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2, psycopg2.extras, requests

CB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CB_DIR))

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k = k.strip(); v = v.strip()
        if k and k not in os.environ: os.environ[k] = v

DSN           = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
SCORECARD_KEY = os.environ.get("COLLEGE_SCORECARD_API_KEY") or os.environ.get("SCORECARD_API_KEY", "")
SERPER_KEY    = os.environ.get("SERPER_API_KEY", "")
SC_BASE       = "https://api.data.gov/ed/collegescorecard/v1/schools"

LOG_FILE = CB_DIR / "logs" / "classify_schools.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("classify")

# ── GATE: online course availability ─────────────────────────────────────────
# Schools that mention ANY of these offer courses that can be taken online
# without physical campus visits. Broad — includes hybrids and distance schools.

_ONLINE_SIGNAL = re.compile(
    r"\b(online|distance\s+learning|distance\s+education|e[\s-]?learning|"
    r"virtual\s+class|remote\s+learn|fully\s+online|100\s*%\s*online|"
    r"online\s+only|online[\s-]?only|no\s+campus|web[\s-]?based\s+course|"
    r"online\s+program|online\s+degree|online\s+course|"
    r"entirely\s+online|completely\s+online|exclusively\s+online|"
    r"asynchronous|self[\s-]?paced\s+online|internet[\s-]?based)\b",
    re.IGNORECASE,
)

# ── 6 Filter patterns ─────────────────────────────────────────────────────────

_NO_TRANSCRIPT = re.compile(
    r"\b(no\s+transcript|without\s+transcript|transcript\s+not\s+required|"
    r"no\s+academic\s+record|self[\s-]?reported\s+grades|no\s+prior\s+record|"
    r"transcript\s+waived|high\s+school\s+diploma\s+not\s+required|"
    r"open\s+enrollment(?!\s+date)|open\s+admission|no\s+application\s+required)\b",
    re.IGNORECASE,
)
_MONTHLY_ENROLL = re.compile(
    r"\b(rolling\s+enrollment|rolling\s+admission|monthly\s+start|"
    r"start\s+anytime|multiple\s+start\s+date|flexible\s+start|"
    r"no\s+semester|continuous\s+enrollment|enroll\s+any\s+time|"
    r"open\s+entry|year[\s-]?round\s+enrollment|start\s+every\s+month)\b",
    re.IGNORECASE,
)
_INSTANT_ACCEPT = re.compile(
    r"\b(instant\s+accept|immediate\s+accept|open\s+admission|"
    r"open\s+enrollment|no\s+application\s+required|guaranteed\s+admission|"
    r"automatic\s+admit|no\s+waitlist|accept\s+all\s+applicants|"
    r"100\s*%\s+acceptance|no\s+application\s+fee\s+required)\b",
    re.IGNORECASE,
)
_NO_ID = re.compile(
    r"\b(no\s+id\s+verification|no\s+identity\s+check|no\s+id\s+required|"
    r"no\s+identification\s+required|anonymous\s+enrollment)\b",
    re.IGNORECASE,
)
_COMMUNITY_COLLEGE_TEXT = re.compile(
    r"\b(community\s+college|junior\s+college|technical\s+college|"
    r"two[\s-]?year\s+college|2[\s-]?year\s+college|associates?\s+degree\s+only)\b",
    re.IGNORECASE,
)

# Carnegie undergrad classification codes -> community college
# 1=Associates, 2=Primarily Associates, 6=Mixed bac/associates
_CC_CARNEGIE = {1, 2, 6}

# ── Scorecard API ─────────────────────────────────────────────────────────────

_SC_FIELDS = ",".join([
    "id",
    "school.name",
    "school.online_only",
    "school.open_admissions_policy",
    "school.ownership",
    "school.carnegie_undergrad",
    "school.degrees_awarded.predominant",
    "latest.admissions.admission_rate.overall",
    "latest.student.size",
])


def scorecard_fetch(scorecard_ids: list[str]) -> dict[str, dict]:
    if not scorecard_ids or not SCORECARD_KEY:
        return {}
    try:
        r = requests.get(
            SC_BASE,
            params={"id": ",".join(scorecard_ids), "fields": _SC_FIELDS,
                    "api_key": SCORECARD_KEY, "per_page": len(scorecard_ids)},
            timeout=20,
        )
        r.raise_for_status()
        return {str(s.get("id", "")): s for s in r.json().get("results", [])}
    except Exception as e:
        log.warning("Scorecard API error: %s", e)
        return {}


# ── Serper fallback ───────────────────────────────────────────────────────────

def serper_check_online(name: str, city: str, state: str) -> tuple:
    """Return (has_online_courses, evidence_snippet)."""
    if not SERPER_KEY:
        return False, "no_serper_key"
    query = f'"{name}" {city} {state} online courses distance learning programs'
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 5},
            timeout=10,
        )
        r.raise_for_status()
        snippets = " ".join(item.get("snippet", "") for item in r.json().get("organic", []))
        if _ONLINE_SIGNAL.search(snippets):
            m = _ONLINE_SIGNAL.search(snippets)
            return True, f"serper:{m.group(0)[:40]}"
        return False, "serper:no_online_signal"
    except Exception as e:
        log.warning("Serper error for %s: %s", name, e)
        return False, f"serper_error:{str(e)[:40]}"


# ── Classify one school ───────────────────────────────────────────────────────

def classify(row: dict, sc: dict) -> dict:
    raw_text = row.get("raw_text") or ""
    name     = row.get("name") or ""
    city     = row.get("city") or ""
    state    = row.get("state") or ""

    sc_online_only  = sc.get("school.online_only")
    sc_open_admit   = sc.get("school.open_admissions_policy")
    sc_carnegie     = sc.get("school.carnegie_undergrad")
    sc_admit_rate   = sc.get("latest.admissions.admission_rate.overall")
    sc_ownership    = sc.get("school.ownership")
    sc_predom_deg   = sc.get("school.degrees_awarded.predominant")

    # ── GATE: has online courses ──────────────────────────────────────────────
    online_available = False
    online_evidence  = ""
    classified_by    = "raw_text"

    if sc_online_only == 1:
        online_available = True
        online_evidence  = "scorecard:online_only=1"
        classified_by    = "scorecard"
    elif row.get("online_only"):
        online_available = True
        online_evidence  = "db:online_only=true"
        classified_by    = "db_flag"
    elif _ONLINE_SIGNAL.search(raw_text):
        m = _ONLINE_SIGNAL.search(raw_text)
        online_available = True
        online_evidence  = f"raw_text:{m.group(0)[:50]}"
        classified_by    = "raw_text"
    else:
        found, evidence = serper_check_online(name, city, state)
        if found:
            online_available = True
            online_evidence  = evidence
            classified_by    = "serper"
        else:
            online_evidence = evidence
            classified_by   = "serper_negative"

    # ── 6 Filters ─────────────────────────────────────────────────────────────

    no_transcript = (
        sc_open_admit == 1
        or sc_admit_rate is None
        or bool(_NO_TRANSCRIPT.search(raw_text))
    )

    monthly_enroll = (
        bool(_MONTHLY_ENROLL.search(raw_text))
        or (sc_open_admit == 1 and bool(_COMMUNITY_COLLEGE_TEXT.search(raw_text)))
    )

    instant_accept = (
        sc_open_admit == 1
        or sc_admit_rate is None
        or bool(_INSTANT_ACCEPT.search(raw_text))
    )

    is_cc = (
        bool(row.get("is_community_college"))
        or (sc_carnegie in _CC_CARNEGIE if sc_carnegie is not None else False)
        or (sc_predom_deg == 2 and sc_ownership == 1)
        or bool(_COMMUNITY_COLLEGE_TEXT.search(raw_text))
    )

    no_id = bool(_NO_ID.search(raw_text))

    return {
        "fully_online":             online_available,
        "online_evidence":          online_evidence,
        "classified_by":            classified_by,
        "scorecard_distance_only":  sc_online_only == 1 if sc_online_only is not None else None,
        "online_available":         online_available,
        "no_transcript_required":   no_transcript,
        "monthly_enrollment":       monthly_enroll,
        "instant_acceptance":       instant_accept,
        "community_college":        is_cc,
        "no_id_verification":       no_id,
    }


# ── Processing loop ───────────────────────────────────────────────────────────

def run(machine: str, batch_size: int) -> None:
    log.info("Starting v2 classification — machine=%s batch=%d", machine, batch_size)
    log.info("Gate: any online/distance signal in raw_text OR scorecard")

    conn = psycopg2.connect(DSN, connect_timeout=10)
    conn.autocommit = False
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    total_done = total_online = total_not_online = 0
    t0 = time.monotonic()

    while True:
        cur.execute("""
            UPDATE school_classification_work
            SET status = 'processing'
            WHERE id IN (
                SELECT w.id FROM school_classification_work w
                WHERE w.assigned_to = %s AND w.status = 'pending'
                ORDER BY w.id LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, raw_school_id
        """, (machine, batch_size))
        batch = [dict(r) for r in cur.fetchall()]
        conn.commit()

        if not batch:
            log.info("No more pending rows for %s.", machine)
            break

        raw_ids = [w["raw_school_id"] for w in batch]
        cur.execute("""
            SELECT id, name, url, city, state, scorecard_id,
                   online_only, is_community_college, open_admissions, raw_text
            FROM raw_schools WHERE id = ANY(%s)
        """, (raw_ids,))
        raw_map = {r["id"]: dict(r) for r in cur.fetchall()}

        sc_ids  = [raw_map[w["raw_school_id"]].get("scorecard_id", "") for w in batch
                   if raw_map.get(w["raw_school_id"], {}).get("scorecard_id")]
        sc_data = scorecard_fetch([s for s in sc_ids if s]) if sc_ids else {}

        updates = []
        for w in batch:
            rrow = raw_map.get(w["raw_school_id"], {})
            sc_id = rrow.get("scorecard_id", "")
            sc    = sc_data.get(str(sc_id), {}) if sc_id else {}
            try:
                res = classify(rrow, sc)
                res["status"]       = "done" if res["fully_online"] else "not_online"
                res["processed_at"] = datetime.now(timezone.utc)
                res["work_id"]      = w["id"]
                res["error_msg"]    = None
                if res["fully_online"]: total_online    += 1
                else:                   total_not_online += 1
            except Exception as e:
                res = {"work_id": w["id"], "status": "error", "error_msg": str(e)[:200],
                       "processed_at": datetime.now(timezone.utc), "fully_online": None,
                       "online_evidence": "", "classified_by": "error",
                       "scorecard_distance_only": None, "online_available": None,
                       "no_transcript_required": None, "monthly_enrollment": None,
                       "instant_acceptance": None, "community_college": None,
                       "no_id_verification": None}
            updates.append(res)

        for u in updates:
            cur.execute("""
                UPDATE school_classification_work SET
                    status=%s, error_msg=%s, processed_at=%s,
                    fully_online=%s, online_evidence=%s, classified_by=%s,
                    scorecard_distance_only=%s, online_available=%s,
                    no_transcript_required=%s, monthly_enrollment=%s,
                    instant_acceptance=%s, community_college=%s, no_id_verification=%s
                WHERE id=%s
            """, (u["status"], u.get("error_msg"), u["processed_at"],
                  u.get("fully_online"), u.get("online_evidence", ""), u.get("classified_by", ""),
                  u.get("scorecard_distance_only"), u.get("online_available"),
                  u.get("no_transcript_required"), u.get("monthly_enrollment"),
                  u.get("instant_acceptance"), u.get("community_college"),
                  u.get("no_id_verification"), u["work_id"]))
        conn.commit()

        total_done += len(batch)
        elapsed = time.monotonic() - t0
        rate = total_done / elapsed * 60
        log.info("Progress: %d done | %d online | %d not_online | %.0f/min",
                 total_done, total_online, total_not_online, rate)
        time.sleep(0.3)

    elapsed = time.monotonic() - t0
    log.info("=" * 60)
    log.info("COMPLETE machine=%s | online=%d not_online=%d | %.1f min",
             machine, total_online, total_not_online, elapsed / 60)
    log.info("=" * 60)
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default=os.environ.get("CB_SYNC_ROLE", "primary"))
    ap.add_argument("--batch",   type=int, default=100)
    args = ap.parse_args()
    run(args.machine, args.batch)
