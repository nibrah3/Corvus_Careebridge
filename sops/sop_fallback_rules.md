# SOP: Global Fallback Rules

**Injected at session start. These rules are mandatory — not guidelines.**

---

## Tool Hierarchy (enforce this order, never skip)

1. `mcp__dom__*` — accessibility tree (structured, semantic, zero cost)
2. `mcp__cdp__*` — raw DOM queries and CDP interaction (fallback from dom_mcp)
3. `mcp__uia__*` — Windows native controls (when no browser context)
4. `mcp__gemini__upload_file` + `mcp__gemini__analyse_file` — annotation content (video/image files only)
5. `mcp__capture__screenshot` + `mcp__gemini__analyse_image` — LAST RESORT only

Do not skip to a lower-priority tool because it seems faster or easier.

---

## Hard Rules

**DOM extraction:**
- If `dom_mcp` returns empty tree: try `cdp_mcp.cdp_get_axtree()` once.
- If both return empty: take screenshot → go to supervised gate. Do NOT proceed without DOM data.
- Never submit an answer if DOM extraction completely failed.

**CDP connection:**
- If any CDP tool returns a connection error: call `mcp__ixbrowser__reconnect()` first.
- Do not retry the CDP tool on the stale connection. Reconnect, then retry.
- Never fall back to UIA or screenshot because CDP is slow — only if CDP is genuinely unavailable.

**Screenshots:**
- `mcp__capture__screenshot` is allowed for debugging only (`purpose="debug"`).
- Taking a screenshot for task work (not debugging) puts the session in FALLBACK MODE.
- FALLBACK MODE: all answer submissions must go to supervised gate before executing.
- Never use a screenshot to answer an annotation task — use Gemini Files API instead.

**Gemini Files API (annotation tasks):**
- If `mcp__gemini__upload_file` fails: stop and report the error. Do NOT fall back to screenshot annotation.
- Screenshots cannot replace the annotation video/image — they show your screen, not the source file.

**All browser interactions:**
- All clicks, typing, and scrolling MUST go through `mcp__humanizer__*`.
- Never use `mcp__cdp__cdp_eval` for clicks, form submission, or event dispatch.
- `cdp_eval` is for reading state only: `document.querySelectorAll`, `window.location`, computed values.
- Allowed: `dispatchEvent(new Event('input'))` and `dispatchEvent(new Event('change'))` — React needs these after CDP insertText.
- Blocked: `.click()`, `.submit()`, `MouseEvent`, `PointerEvent`, `KeyboardEvent` dispatch.

**Answer submission:**
- All answers are submitted only through `mcp__humanizer__*` interactions (clicking the submit button as a human would).
- Never directly post form data via JS or CDP.
- In supervised mode: free-text answers always go to gate before submission.
- MCQ/radio: auto-submit is allowed if confidence ≥ 0.85.

---

## Fallback Mode Behaviour

When FALLBACK MODE is active (screenshot was taken for task work):
- All answer submissions require supervised gate approval.
- Notify via `mcp__telegram__notify("⚠️ Session in fallback mode — gate active")`.
- Fallback mode clears when the session ends — not mid-session.

---

## On Error — Fallback Rules Enforcement

**CDP tool returns "blocked: CDP available" but CDP is not working:**
→ The CDP ping in capture_mcp checks port 9222 specifically.
→ If IXBrowser uses a different port, set `CDP_PORT` env var on capture_mcp startup.
→ Temporary fix: call `mcp__capture__screenshot(purpose="debug")` which bypasses the check.

**hook_fallback_monitor fails to write to Redis:**
→ Non-fatal. The hook still injects the gate instruction to Claude.
→ The gate enforcement works even without Redis — it's belt-and-suspenders.

→ Shared patterns: see `sops/sop_on_error_shared.md`
