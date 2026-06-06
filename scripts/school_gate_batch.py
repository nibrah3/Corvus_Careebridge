"""
Phase 2 helper — reads a batch of unanalyzed schools from raw_schools.
Outputs JSON to stdout for Claude Code subagent to analyze.
Usage: python school_gate_batch.py [batch_size]
"""
import sys, os, json, hashlib

sys.path.insert(0, r"E:\Corvus_Careebridge")
os.chdir(r"E:\Corvus_Careebridge")

for line in open(".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

import psycopg2, psycopg2.extras

DSN        = "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge"
batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 25

import time as _time

# Retry up to 3 times if SKIP LOCKED returns 0 but rows exist — handles race conditions
for _attempt in range(3):
    try:
        conn = psycopg2.connect(DSN, connect_timeout=10)
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, url, city, state, is_community_college, raw_text
                    FROM raw_schools
                    WHERE analyzed = FALSE
                      AND raw_text IS NOT NULL
                      AND length(raw_text) > 50
                    ORDER BY id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                """, (batch_size,))
                rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        if rows or _attempt == 2:
            break
        # Brief wait before retry — lets other agents' transactions commit
        _time.sleep(2)

    except Exception as e:
        print(json.dumps({"count": 0, "schools": [], "error": str(e)}))
        sys.exit(1)

# Truncate raw_text for output (keep first 6000 chars per school)
for r in rows:
    r["raw_text"] = (r["raw_text"] or "")[:6000]

result = {"count": len(rows), "schools": rows}
print(json.dumps(result))
