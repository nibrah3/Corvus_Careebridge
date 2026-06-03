# Skill: Seed Platforms and Search Terms

**Trigger:** Manual — run once on first setup, or when discovery_seed.py is updated.
**Mode:** Fully autonomous. Run to completion.

---

## What you are doing

Bootstrapping the `discovered_platforms` and `search_terms` tables from the seed catalogue
in `E:\Corvus_Careebridge\discovery_seed.py`. This is a one-time initialization that
gives the discovery system its starting coverage across all 14+ gig categories.

After this runs, `corvus_discovery.py` reads queries from `search_terms` instead of
hardcoded lists, and `skill_catalogue_poll.md` has platforms to poll.

---

## Step 1 — Read the seed file

Use Bash to read the CATEGORIES list from discovery_seed.py:
```
python -c "
import sys; sys.path.insert(0,'E:/Corvus_Careebridge')
from discovery_seed import CATEGORIES
import json
print(json.dumps(CATEGORIES))
" 2>&1
```

Parse the JSON output. You now have the full seed catalogue.

## Step 2 — Seed search_terms table

For each category in CATEGORIES:
  For each keyword in category["keywords"]:
    Call `mcp__vps__upsert_search_term(
      term=keyword,
      category=category["id"],
      priority="high" if category["pay_tier"] == "high" else "normal",
      source="seed"
    )`

  For each reddit_sub in category.get("reddit_subs", []):
    Call `mcp__vps__upsert_search_term(
      term=f"site:{reddit_sub} gig work hiring",
      category=category["id"],
      priority="normal",
      source="seed_reddit"
    )`

Report: how many terms upserted, how many already existed.

## Step 3 — Seed discovered_platforms table

For each category in CATEGORIES:
  For each platform in category.get("platforms", []):
    Check if platform URL already in discovered_platforms:
      Call `mcp__vps__get_due_catalogue_companies(limit=500)` — check URL in results.
      If not present: Call `mcp__vps__upsert_discovered_platform(
        company=platform["name"],
        careers_url=platform["url"],
        category=category["id"],
        tier=2,
        source="seed"
      )` — Note: this tool may need adding; use upsert_job as workaround if needed.

If `upsert_discovered_platform` doesn't exist yet, use Bash to INSERT directly:
```sql
INSERT INTO discovered_platforms (company, careers_url, category, tier, source, is_active)
VALUES ('Name', 'url', 'category', 2, 'seed', TRUE)
ON CONFLICT (careers_url) DO NOTHING;
```

## Step 4 — Report

Call `mcp__telegram__notify` with:
- Total search terms seeded
- Total platforms seeded
- Categories covered
- Any errors

---

## On Error

**Bash fails to import discovery_seed.py:**
→ Check file path. Run `python -c "import discovery_seed"` from CB_DIR to confirm.
→ If import fails, read the file directly and parse CATEGORIES manually.

**VPS tools unreachable:**
→ Check `mcp__vps__get_system_status`. If postgres error: verify SSH tunnel.
→ Run `scripts/vps_tunnel.ps1` to rebuild tunnel, then retry.
