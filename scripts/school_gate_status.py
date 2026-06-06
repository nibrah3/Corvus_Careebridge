"""Reports Phase 2 progress: pending, analyzed, qualified."""
import sys, os, json
sys.path.insert(0, r"E:\Corvus_Careebridge")
for line in open(r"E:\Corvus_Careebridge\.env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k]=v
import psycopg2
conn = psycopg2.connect("postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge", connect_timeout=10)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM raw_schools WHERE analyzed=FALSE AND length(raw_text)>50")
pending = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM raw_schools WHERE analyzed=TRUE")
done = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM schools WHERE source_query='claude_code'")
qualified = cur.fetchone()[0]
conn.close()
print(json.dumps({"pending": pending, "analyzed": done, "qualified_by_claude": qualified}))
