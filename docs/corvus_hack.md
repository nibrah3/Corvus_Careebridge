# Corvus Hack — Multi-User Commercial Architecture

## What This Is

A paid remote AI-assistance service. Users pay $20/session (1hr cap, first session free)
to get live, intelligent guidance through online assessments (Outlier, HireVue, Scale, etc.).
Claude watches the user's screen passively and advises via Telegram. The user does all
clicking and typing — the AI does all thinking, reading, and answering.

---

## Infrastructure

### Windows VPS per active session
- Provider: Vultr High Frequency or Hetzner Cloud (both confirmed WDDM-compatible)
- OS: Windows Server 2022 Desktop Experience (GUI required — no Core)
- Spec: 4GB RAM minimum per active session
- Pre-installed image: GoLogin + UltraViewer + daemon + capture_mcp + gemini_mcp
- Spin new VMs from a saved snapshot — no manual setup per user

### Why VPS, not bare VM
A Windows VPS is a VM hosted by a provider. Same thing, already managed.
DXGI hooks into the WDDM GPU compositor (virtual display driver), not physical GPU.
Works identically on VPS as on physical hardware — confirmed on Vultr and Hetzner.

### Remote access: UltraViewer (not TeamViewer)
- Free, no session time limits, lightweight
- Does not interfere with DXGI capture
- User connects to the VPS desktop and operates it like their own machine

### GoLogin — browser fingerprint continuity
- User already has GoLogin profiles on their own machine
- They log into their GoLogin account on the VPS → profiles sync via cloud automatically
- Fingerprint, cookies, and proxies all come with the profile
- Platform sees a real human on a real browser — zero synthetic events

---

## Session Flow

```
1. User texts bot: "I want outlier assessment"
        ↓
2. Bot allocates a free VPS from the pool
   → Sends user: UltraViewer ID + password
   → Daemon on that VPS enters IDLE mode → starts DXGI capture immediately
        ↓
3. User connects via UltraViewer
   → Opens GoLogin, loads their profile
   → Navigates to platform (Outlier, etc.)
   → Logs in, gets to the assessment page
        ↓
4. User texts bot: "start"  (or states intent in plain English)
   → Daemon switches to ACTIVE mode
   → Pixel-diff event loop begins (already warmed up — no lag)
        ↓
5. ACTIVE loop:
   while session_active:
       event = capture_get_event(timeout=30)   # blocks until screen changes
       if event.type == "navigation":
           description = gemini.analyse_image(event.frame_path)
           guidance = claude_api.reason(description, session_context)
           telegram.send(user_chat_id, guidance)
       elif event.type == "none":
           # nothing changed — stay idle, check billing timer
        ↓
6. User navigates, clicks, types — Claude watches and advises via Telegram
   User can ask anything: "answer the question on screen", "what does this video say?"
        ↓
7. Session ends at 1hr or user says "done"
   → Billing event fired → Stripe charge
   → VPS returns to pool (session data wiped)
```

---

## Intelligence Layer

### Gemini (visual perception)
- "What is on this screen? What UI elements are visible? What does this form ask for?"
- Fast, cheap, already integrated
- No vision tokens billed to Claude API
- Used for: screen reading, OCR, element identification, video analysis

### Claude API (reasoning + writing)
- "Given what Gemini described, what should I tell the user to do?"
- "Write the cover letter / answer this essay question"
- Used for: decision-making, advice, long-form writing, complex reasoning
- Cost per session is low because vision is offloaded to Gemini

### Division of labor
```
Screen change detected
        ↓
Gemini Flash → "What is on screen?" → text description
        ↓
Claude Sonnet → "What should user do?" → Telegram message to user
```

---

## Pre-Capture (Zero Lag Design)

DXGI capture starts the moment the VPS is allocated — before the user even connects.

EasyOCR has a ~60s warmup on first start. By the time the user:
1. Gets the UltraViewer ID
2. Connects to the VPS
3. Opens GoLogin and loads their profile
4. Navigates to the platform
5. Says "start"

...the OCR engine has been warm for several minutes. Zero lag from "start".

Daemon states:
- **IDLE**: 1 fps capture, OCR warming, watching for UltraViewer connection
- **ACTIVE**: full event-driven pixel-diff loop, Claude advising on each navigation

---

## Download Detection

The daemon watches the VPS Downloads folder with a filesystem watcher (Python `watchdog`).

When a new file lands:
1. Send to Gemini for analysis (handles PDF, images, video)
2. Add extracted content to session context
3. Notify user via Telegram: "Downloaded: outlier_task_instructions.pdf — read and ready"

User downloads manually (right-click → Save), daemon picks it up automatically. No clicks needed from the AI side.

---

## No-Automation Advantage

The system provides intelligence, not automation. The user does all navigation.

Why this is better:
- Platforms detect synthetic mouse events via timing patterns and JS event listeners
- DXGI capture is completely invisible (hooks at GPU compositor, not browser layer)
- GoLogin profile = real fingerprint, real cookies, real proxy IP
- Real human hand on the mouse — zero bot detection risk
- Cleaner ToS position: AI advice is not the same as AI automation

---

## Billing

### Timer
- Wall-clock tracker starts when daemon enters ACTIVE mode
- Checks `time.monotonic() - session_start` at every loop iteration
- At 3600s: pause loop, send billing extension prompt via Telegram
- User confirms extension → Stripe charge → loop resumes
- User declines → session ends cleanly

### Pricing
- First session: free
- Each subsequent session: $20 flat, 1hr cap
- Extension: $20/hr additional

### Stripe integration
- Telegram bot collects payment method on signup
- Session start/extend/end fires Stripe charge events
- No manual payment during session

---

## Supervisor / Watchdog (Claude Code on Master)

The master machine (your desktop with Claude Code CLI via Telegram bridge) acts as the
intelligent ops brain for all worker VPS daemons.

Each worker daemon emits a structured heartbeat every 30s:
```json
{
  "session_id": "...",
  "vm": "vm-04",
  "status": "active",
  "last_event": "navigation",
  "stuck_since": null,
  "ts": 1752400000
}
```

Monitor process on master:
- Checks heartbeats
- If heartbeat late OR `stuck_since` > 60s:
  1. Writes worker's recent log to file
  2. Fires `claude --print` with: "Worker vm-04 is stuck. Here is its log. Diagnose and fix."
  3. Claude reads log, identifies issue, takes corrective action
  4. If unfixable: Telegram alert to Mike for manual intervention

Workers are dumb loops. The master Claude Code IS the diagnosis engine — same intelligence
as when you use it interactively via Telegram, just triggered automatically.

---

## VM Pool Management

```
pool DB (SQLite on master):
  vm_id | status   | user_id | session_start | session_end
  vm-01 | active   | u_123   | 2026-07-13T10:00 | null
  vm-02 | idle     | null    | null          | null
  vm-03 | cooldown | u_456   | 2026-07-13T09:00 | 2026-07-13T10:05
  vm-04 | active   | u_789   | 2026-07-13T10:15 | null
```

- cooldown: 5-10min after session ends for cleanup/wipe before returning to pool
- Telegram bot checks pool before allocating — if all active, user joins a queue
- Scale: start with 5 VMs, add more as demand grows

---

## Stack Summary

| Component        | Technology                              |
|------------------|-----------------------------------------|
| Remote desktop   | UltraViewer (free)                      |
| Browser profiles | GoLogin (user's own account, cloud sync)|
| Screen capture   | DXGI via capture_mcp                    |
| Visual analysis  | Gemini Flash 2.5 via gemini_mcp         |
| Intelligence     | Claude API (Sonnet) — stateful daemon   |
| User interface   | Telegram bot                            |
| Payments         | Stripe                                  |
| VM pool          | SQLite on master + PowerShell/API mgmt  |
| Watchdog         | Claude Code CLI on master machine       |
| Downloads        | Python watchdog filesystem watcher      |

---

## First Build Priority

1. `worker_daemon.py` — stateful agent loop (idle → active → billing → end)
2. VM pool allocator in Telegram bot (assign VM, send UltraViewer ID)
3. Billing timer + Stripe charge events
4. Download watcher
5. Heartbeat emitter + master watchdog
