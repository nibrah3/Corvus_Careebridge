"""
Phase 2 helper — saves Claude's analysis results back to DB.
Reads JSON from stdin: list of {id, community_college, no_id_verification,
  no_transcript_required, monthly_enrollment, instant_acceptance, monthly_refund,
  name, url, city, state, is_community_college}
"""
import sys, os, json

sys.path.insert(0, r"E:\Corvus_Careebridge")
os.chdir(r"E:\Corvus_Careebridge")

for line in open(".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

import psycopg2

DSN = "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge"

def url_hash(url):
    import hashlib
    return hashlib.md5((url or "").encode()).hexdigest()

results = json.load(sys.stdin)

conn = psycopg2.connect(DSN, connect_timeout=10)
saved = skipped = errors = 0

try:
    with conn:
        with conn.cursor() as cur:
            for r in results:
                raw_id = r.get("id")
                criteria = {
                    "community_college":      bool(r.get("community_college")),
                    "no_id_verification":     bool(r.get("no_id_verification")),
                    "no_transcript_required": bool(r.get("no_transcript_required")),
                    "monthly_enrollment":     bool(r.get("monthly_enrollment")),
                    "instant_acceptance":     bool(r.get("instant_acceptance")),
                    "monthly_refund":         bool(r.get("monthly_refund")),
                }
                score   = sum(criteria.values())
                filters = [k for k, v in criteria.items() if v]
                url     = r.get("url", "")
                name    = r.get("name", "")

                # Mark analyzed in raw_schools regardless of score
                cur.execute(
                    "UPDATE raw_schools SET analyzed=TRUE WHERE id=%s",
                    (raw_id,)
                )

                if score >= 1:
                    # Upsert into schools table (overwrites heuristic results)
                    cur.execute("""
                        INSERT INTO schools (
                            name, url, city, state, type,
                            community_college, no_id_verification, no_transcript_required,
                            monthly_enrollment, instant_acceptance, monthly_refund,
                            filters, criteria_score, source_query, url_hash, updated_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'claude_code',%s,NOW())
                        ON CONFLICT (url_hash) DO UPDATE SET
                            community_college      = EXCLUDED.community_college,
                            no_id_verification     = EXCLUDED.no_id_verification,
                            no_transcript_required = EXCLUDED.no_transcript_required,
                            monthly_enrollment     = EXCLUDED.monthly_enrollment,
                            instant_acceptance     = EXCLUDED.instant_acceptance,
                            monthly_refund         = EXCLUDED.monthly_refund,
                            filters                = EXCLUDED.filters,
                            criteria_score         = EXCLUDED.criteria_score,
                            source_query           = 'claude_code',
                            updated_at             = NOW()
                    """, (
                        name, url,
                        r.get("city",""), r.get("state",""),
                        "Community College" if r.get("is_community_college") else "University/College",
                        criteria["community_college"],
                        criteria["no_id_verification"],
                        criteria["no_transcript_required"],
                        criteria["monthly_enrollment"],
                        criteria["instant_acceptance"],
                        criteria["monthly_refund"],
                        filters, score, url_hash(url),
                    ))
                    saved += 1
                else:
                    skipped += 1

    print(json.dumps({"saved": saved, "score_zero": skipped, "errors": errors}))

except Exception as e:
    print(json.dumps({"saved": saved, "score_zero": skipped, "errors": 1, "error": str(e)}))
finally:
    conn.close()
