# Skill: Browser and Profile Selection

**Trigger:** Called at the start of ANY pipeline that needs a browser — jobs, schools, CV application.
**Mode:** Interactive — user must choose browser and profile before pipeline continues.

---

## What you are doing

Detecting which antidetect browsers are installed on this machine, asking the user to pick one, then reading that browser's profiles and asking the user which profile to use. After selection the pipeline is fully autonomous.

---

## Step 1 — Detect available browsers

Check which of these are installed (look for running processes AND installed exe paths):

| Browser | Process name | Default exe path |
|---|---|---|
| IXBrowser | IXBrowser.exe | `C:\Program Files\IXBrowser\IXBrowser.exe` |
| GoLogin | gologin.exe | `C:\Program Files\GoLogin\GoLogin.exe` |
| Camoufox | camoufox.exe | check `site-packages\camoufox\` |
| AdsPower | AdsPower.exe | `C:\Program Files\AdsPower\AdsPower.exe` |
| Multilogin | multiloginapp.exe | `C:\Program Files\Multilogin\` |

Use Bash: `Get-Process | Where-Object {$_.Name -match "IXBrowser|gologin|camoufox|AdsPower|multilogin"}`

Also check if APIs are reachable:
- IXBrowser: `http://127.0.0.1:53200/api/v1/profile/list`
- GoLogin: `http://127.0.0.1:36912/`

---

## Step 2 — Ask user which browser

Call AskUserQuestion with only the browsers that are available:

```
question: "Which browser profile should I use?"
header:   "Browser"
options:  [list only detected browsers]
  - label: "IXBrowser"    description: "N profiles available"
  - label: "GoLogin"      description: "N profiles available"
  - label: "Camoufox"     description: "available"
```

If only one browser is detected, skip this step and use it automatically.
If none are detected, stop and tell the user: "No antidetect browser found. Please start IXBrowser or GoLogin first."

---

## Step 3 — Read profiles from chosen browser

**IXBrowser:**
Call `mcp__ixbrowser__list_profiles()` → returns profile list with name, email, id.

**GoLogin:**
Call `mcp__vps__gologin_list_profiles()` if available, or read via API:
`curl http://127.0.0.1:36912/browser/list`

**Camoufox:**
No profile concept — runs with a generated fingerprint. Skip profile selection.

---

## Step 4 — Ask user which profile

Call AskUserQuestion with up to 6 profiles (show most recently used first if known):

```
question: "Which profile should I use for this application?"
header:   "Profile"
options:
  - label: "John Smith"    description: "john@email.com · last used 2 days ago"
  - label: "Sarah Connor"  description: "sarah@email.com"
  - label: "Profile 3"     description: "no email on file"
```

---

## Step 5 — Connect

**IXBrowser:**
`mcp__ixbrowser__connect_profile(profile_id, email)` → returns `cdp_url`

**GoLogin:**
Launch profile via GoLogin API → wait for CDP port → connect

**Camoufox:**
`camoufox.launch()` → returns playwright page object

Store: `cdp_url`, `browser_name`, `profile_name`, `profile_id` for use in the pipeline.

---

## Rules

- Never skip this skill when browser automation is needed.
- Always ask — never assume which profile to use.
- If browser crashes during connect: retry once, then stop and notify user via Telegram.
- Profile selection persists for the current job session only. Ask again for the next job.

## On Error

- Browser API not responding → ask user to open the browser application first
- Profile list empty → tell user to create a profile in the browser and try again
- CDP connect fails → see `sops/sop_browser_launch.md`
