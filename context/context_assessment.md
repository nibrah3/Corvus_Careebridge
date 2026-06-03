# Context: Assessment & Application Session

Loaded when session involves running an assessment, application, or CV generation.

## Mandatory procedures
- Read `sops/sop_fallback_rules.md` before any browser action
- Follow `sops/sop_browser_launch.md` for every session start
- Route to correct skill: text→`skill_assess_text.md`, image→`skill_assess_image.md`, video→`skill_assess_video.md`

## Tool hierarchy
dom_mcp → cdp_mcp → uia_mcp → gemini_files (annotation only) → capture+gemini (GATE REQUIRED)

## Gate rules
- Supervised mode: gate all free-text answers before submission
- MCQ: auto-submit if confidence ≥ 0.85
- Screenshot fallback: always gates ALL subsequent answers

## Browser setup
Always use `mcp__ixbrowser__connect_profile` — never open Chrome directly.
If CDP stale: call `mcp__ixbrowser__reconnect` before retrying.

## CV generation
Follow `sops/sop_cv_application.md`. Firecrawl fetches job → Claude writes CV content → cv_format_tool produces PDF.
Never fabricate skills not in the profile. Always confirm with user before submitting.
