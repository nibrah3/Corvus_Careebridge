# SOP: Browser Launch and CDP Connection

**Purpose:** Establish a verified, active browser session before any assessment or application work begins.

---

## Steps

### 1. Launch and connect profile

Call `mcp__ixbrowser__connect_profile(profile_id, email)`.

- `profile_id`: the numeric IXBrowser profile ID (or Postgres profile ID — the MCP maps it)
- `email`: the profile's email address (used to determine paid vs free path)

Expected response: `{ cdp_url, tab_list, active_tab_url, path }`

If response contains `error`:
- If error mentions IXBrowser not running: wait 10s, retry once.
- If error persists: call `mcp__telegram__notify("❌ IXBrowser failed to start for profile X")` and stop.

### 2. Check for existing assessment tab

Examine `tab_list` from the connect response:
- If any tab URL matches the target assessment URL (exact or domain match): note its `tab_id`.
- If a matching tab is found: use it directly — do not open a new tab.
- If no matching tab: proceed to Step 3.

### 3. Navigate to assessment URL

If no matching tab was found:
- Call `mcp__cdp__cdp_eval('window.open("")')` or use browser_mcp keyboard shortcut to open new tab.
- Navigate to the assessment URL via `mcp__cdp__cdp_page_info()` and CDP navigation.

### 4. Verify page loaded

Call `mcp__dom__get_accessibility_tree()` (or `mcp__cdp__cdp_get_axtree()`).
- If tree has interactive elements: page is ready. Proceed.
- If tree is empty: wait 3s, retry once.
- If still empty after retry: take a debug screenshot (`purpose="debug"`), check if page is blank or shows login wall.
  - Login wall: report to user and stop.
  - Blank/loading: wait 5s more, retry.

### 5. Confirm identity context

Verify the active tab URL matches the expected assessment URL.
If the browser redirected to a login page or error page: report and stop — do not proceed.

---

## Notes

- Never open Chrome directly — always go through `ixbrowser_mcp`. IXBrowser manages profile isolation and stealth fingerprinting.
- If `cdp_url` is already known (passed in from a prior step), skip Step 1 and go directly to Step 2.
- The `path` field in the connect response ("api" or "psutil") is informational — do not branch on it.
