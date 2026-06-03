# Context: Discovery & Gate Session

Loaded when session involves gating raw discoveries, graph expansion, or gap analysis.
Scout persona is active — read `prompts/scout_persona.md`.

## What Scout can do autonomously
- Generate new Serper queries and add to search_terms table
- Add platforms to discovered_platforms from job description mentions
- Archive platforms with 5+ consecutive empty polls
- Deactivate search terms with gig_rate < 0.05 after 20+ uses
- Write session performance to memory_mcp

## Data flow
raw_discoveries (staging) → Claude Code gates → jobs table (user-visible)
raw_discoveries NEVER shown to users directly.

## Gate rules
- BLOCK: aggregators, blog posts, social media posts, school enrollment, professional jobs
- KEEP: any gig/task/remote work accessible without professional licensing
- When uncertain: keep with job_type='other_gig' (false negatives are worse)

## After each gate run
Update search term performance via `mcp__vps__update_term_performance`.
Write Scout memory via `mcp__memory__write("scout_gate", {...})`.

## Firecrawl
Use `mcp__vps__firecrawl_scrape(url, summarize_for_gating=True)` for gating decisions.
Full markdown only when classification is genuinely ambiguous.
