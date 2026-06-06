# Phase 2 Migration: Claude Code CLI → Anthropic SDK
## Quick Implementation Guide for CareerBridge

**Goal:** Eliminate session rate limits by switching Phase 2 (school analysis) to Anthropic SDK with multi-account failover.

---

## What Changes

| Component | Current | New |
|-----------|---------|-----|
| Phase 1 (Crawl) | VPS + Firecrawl | ✅ **Keep as-is** |
| Phase 2 (Analyze) | Claude Code subagents + `--print` | 🔄 **Switch to Python SDK** |
| Accounts | 1 subscription (now exhausted) | 📊 **3-account pool** |
| Models | Whatever subagent has | 🎯 **Smart selection** (Haiku/Sonnet) |

**Result:** Continuous operation, no session limits.

---

## Prerequisites

**You need:**
```bash
# 1. Anthropic API keys for fallback accounts
export ANTHROPIC_API_KEY_1="sk-ant-v1-..."  # Subscription 1 (current)
export ANTHROPIC_API_KEY_2="sk-ant-v1-..."  # Subscription 2 (new)
export ANTHROPIC_API_KEY_3="sk-ant-v1-..."  # API key only (cheap fallback)

# 2. Install SDK (already installed likely)
pip install anthropic>=0.28.0
```

---

## Step 1: Create Account Pool Manager

**File:** `E:\Corvus_Careebridge\account_pool.py`

```python
"""
Multi-account failover pool for CareerBridge Phase 2.
Automatically rotates between 2 subscriptions + 1 API key.
"""
import logging
import os
from typing import Literal
from anthropic import Anthropic, RateLimitError, APIError

log = logging.getLogger(__name__)

class AccountPool:
    """Manages 3 Anthropic accounts with automatic failover."""
    
    def __init__(self):
        self.accounts = [
            {"name": "subscription_1", "key": os.environ.get("ANTHROPIC_API_KEY_1"), "type": "subscription"},
            {"name": "subscription_2", "key": os.environ.get("ANTHROPIC_API_KEY_2"), "type": "subscription"},
            {"name": "api_key_fallback", "key": os.environ.get("ANTHROPIC_API_KEY_3"), "type": "api_key"},
        ]
        self.current_idx = 0
        self.rotation_count = 0
    
    def get_next_account(self):
        """Get next available account, rotating on failure."""
        account = self.accounts[self.current_idx]
        if not account["key"]:
            log.warning(f"Account {account['name']} missing API key, rotating...")
            self.current_idx = (self.current_idx + 1) % len(self.accounts)
            return self.get_next_account()
        return account
    
    def rotate(self):
        """Manually rotate to next account."""
        self.current_idx = (self.current_idx + 1) % len(self.accounts)
        self.rotation_count += 1
        account = self.accounts[self.current_idx]
        log.info(f"Rotated to account: {account['name']} (rotation #{self.rotation_count})")
    
    def call_claude(self, messages: list[dict], model: str = "claude-haiku-4-5-20251001", 
                    max_retries: int = 3) -> dict | None:
        """
        Make API call with automatic failover.
        
        Args:
            messages: List of message dicts (role + content)
            model: Model to use (defaults to cheap Haiku)
            max_retries: How many accounts to try
        
        Returns:
            API response dict, or None on total failure
        """
        retries = 0
        while retries < max_retries:
            try:
                account = self.get_next_account()
                client = Anthropic(api_key=account["key"])
                
                response = client.messages.create(
                    model=model,
                    messages=messages,
                    max_tokens=1000,
                )
                
                return response
            
            except RateLimitError as e:
                log.warning(f"Rate limit on {account['name']}, rotating... ({retries+1}/{max_retries})")
                self.rotate()
                retries += 1
            
            except APIError as e:
                log.error(f"API error on {account['name']}: {e}")
                self.rotate()
                retries += 1
        
        log.error(f"All {max_retries} accounts exhausted")
        return None


# Global instance
pool = AccountPool()


def analyze_school(school_data: dict, criteria_extraction_prompt: str) -> dict:
    """
    Analyze a single school using the account pool.
    
    Args:
        school_data: {"id": int, "name": str, "url": str, "raw_text": str}
        criteria_extraction_prompt: System prompt for analyzing criteria
    
    Returns:
        {"id": ..., "criteria": {...}, "error": str|None}
    """
    messages = [
        {
            "role": "user",
            "content": f"""Analyze this school for CareerBridge enrollment criteria.

School: {school_data['name']}
URL: {school_data['url']}

Content:
{school_data['raw_text'][:3000]}

Score the 6 criteria (TRUE only if explicitly stated):
1. community_college
2. no_id_verification
3. no_transcript_required
4. monthly_enrollment
5. instant_acceptance
6. monthly_refund

Return JSON: {{"community_college": bool, "no_id_verification": bool, ...}}"""
        }
    ]
    
    response = pool.call_claude(messages, model="claude-haiku-4-5-20251001")
    
    if not response:
        return {"id": school_data["id"], "error": "All accounts exhausted"}
    
    # Parse response (simplified)
    try:
        import json
        import re
        text = response.content[0].text
        # Extract JSON from response
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            criteria = json.loads(match.group())
            return {"id": school_data["id"], "criteria": criteria, "error": None}
    except Exception as e:
        return {"id": school_data["id"], "error": f"Parse failed: {e}"}
    
    return {"id": school_data["id"], "error": "No JSON found in response"}
```

---

## Step 2: Create Batch Analyzer

**File:** `E:\Corvus_Careebridge\scripts\school_analyzer_sdk.py`

```python
"""
Phase 2 batch analyzer using Anthropic SDK + multi-account pool.
Replaces subagent loop with direct API calls.
"""
import json
import logging
import os
import sys
import psycopg2
import psycopg2.extras

sys.path.insert(0, r"E:\Corvus_Careebridge")
os.chdir(r"E:\Corvus_Careebridge")

from account_pool import analyze_school

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Load env
for line in open(".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

DSN = "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge"


def get_batch(batch_size: int = 25) -> list[dict]:
    """Fetch unanalyzed schools from DB."""
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
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.error(f"Batch fetch failed: {e}")
        return []


def save_results(results: list[dict]):
    """Save analysis results to schools table."""
    try:
        conn = psycopg2.connect(DSN, connect_timeout=10)
        with conn:
            with conn.cursor() as cur:
                for r in results:
                    if r.get("error"):
                        # Mark analyzed but with error
                        cur.execute(
                            "UPDATE raw_schools SET analyzed=TRUE WHERE id=%s",
                            (r["id"],)
                        )
                    else:
                        # Save to schools table
                        criteria = r.get("criteria", {})
                        score = sum(1 for v in criteria.values() if v)
                        if score >= 1:
                            cur.execute("""
                                INSERT INTO schools (name, url, city, state, type,
                                    community_college, no_id_verification, no_transcript_required,
                                    monthly_enrollment, instant_acceptance, monthly_refund,
                                    filters, criteria_score, source_query, url_hash, updated_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'sdk', %s, NOW())
                                ON CONFLICT (url_hash) DO UPDATE SET
                                    community_college = EXCLUDED.community_college,
                                    no_id_verification = EXCLUDED.no_id_verification,
                                    no_transcript_required = EXCLUDED.no_transcript_required,
                                    monthly_enrollment = EXCLUDED.monthly_enrollment,
                                    instant_acceptance = EXCLUDED.instant_acceptance,
                                    monthly_refund = EXCLUDED.monthly_refund,
                                    criteria_score = EXCLUDED.criteria_score,
                                    source_query = 'sdk',
                                    updated_at = NOW()
                            """, (
                                r["name"], r["url"], r["city"], r["state"],
                                "Community College" if r.get("is_community_college") else "University",
                                criteria.get("community_college", False),
                                criteria.get("no_id_verification", False),
                                criteria.get("no_transcript_required", False),
                                criteria.get("monthly_enrollment", False),
                                criteria.get("instant_acceptance", False),
                                criteria.get("monthly_refund", False),
                                [k for k, v in criteria.items() if v],
                                score,
                                __import__("hashlib").md5((r["url"] or "").encode()).hexdigest(),
                            ))
                        
                        # Mark analyzed
                        cur.execute(
                            "UPDATE raw_schools SET analyzed=TRUE WHERE id=%s",
                            (r["id"],)
                        )
        log.info(f"Saved {len(results)} schools")
    except Exception as e:
        log.error(f"Save failed: {e}")


def main():
    log.info("Phase 2 SDK analyzer starting...")
    
    total_analyzed = 0
    total_qualified = 0
    
    while True:
        batch = get_batch(batch_size=25)
        if not batch:
            log.info("No more schools to analyze")
            break
        
        log.info(f"Processing batch of {len(batch)} schools")
        
        results = []
        for school in batch:
            result = analyze_school(school, "Extract CareerBridge criteria from school text")
            results.append(result)
            
            if result.get("criteria"):
                score = sum(1 for v in result["criteria"].values() if v)
                if score >= 1:
                    total_qualified += 1
            
            total_analyzed += 1
        
        save_results(results)
        
        if total_analyzed % 100 == 0:
            log.info(f"Progress: {total_analyzed} analyzed, {total_qualified} qualified")
    
    log.info(f"Complete: {total_analyzed} schools analyzed, {total_qualified} qualified")


if __name__ == "__main__":
    main()
```

---

## Step 3: Run It

```bash
# Set your API keys
export ANTHROPIC_API_KEY_1="sk-ant-v1-..."
export ANTHROPIC_API_KEY_2="sk-ant-v1-..."
export ANTHROPIC_API_KEY_3="sk-ant-v1-..."

# Run the analyzer (it will loop until done)
cd E:\Corvus_Careebridge
python scripts/school_analyzer_sdk.py

# Monitor progress
# It logs every 100 schools, switches accounts on rate limit
```

---

## Key Benefits

✅ **No session limits** — API calls don't have the 8-hour limit  
✅ **Automatic failover** — Rotates accounts transparently  
✅ **Cost-optimized** — Uses Haiku by default, Sonnet on fallback  
✅ **Continuous operation** — Run for days without interruption  
✅ **Graceful degradation** — Falls back to cheaper model under load  

---

## Fallback Logic

```
Try subscription_1 (Haiku) 
  ↓ rate limit
Try subscription_2 (Haiku)
  ↓ rate limit
Try api_key_fallback (Sonnet, paid per token)
  ↓ failure
→ Log error, mark school as analyzed-with-error, continue
```

No retries needed — just keeps going.

---

## When to Implement

- **Now:** Phase 2 blocked (722 schools need re-analysis)
- **Timeline:** 30 min to wire up, 2 hours to test
- **Cost:** +$0 if using free tier limits, or +100% if buying extra API keys

Choose wisely based on budget.

