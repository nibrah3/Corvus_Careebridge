# SOP: Text Assessment and Form Filling

**Purpose:** Complete text-based assessments — MCQ, personality tests, skills evaluations, short-answer, and essay forms.

---

## Steps

### 1. Extract all questions

Call `mcp__dom__get_accessibility_tree()` (preferred) or `mcp__cdp__cdp_get_axtree()`.

Identify:
- Radio buttons / checkboxes (MCQ, Likert scale)
- Text inputs / textareas (short answer, essay)
- Dropdowns / selects
- Submit / Next button

If the page has no interactive elements: scroll down via `mcp__humanizer__scroll` and re-read the tree. Some platforms load questions lazily.

### 2. Load profile context

Ensure you have the candidate's profile in context:
- name, background, bio, skills, experience
- persona_prompt (writing voice) — load from `mcp__answer__*` if available

### 3. Answer MCQ / radio / checkbox questions

For each radio/checkbox group:
- Read all options and the question text
- Select the best answer for the candidate's profile and stated persona
- For personality assessments: answer consistently with the candidate's Big Five profile (O, C, E, A, N) if known
- For skills assessments: answer honestly relative to the candidate's actual skill level
- Auto-submit MCQ after selection (confidence ≥ 0.85) — no gate required

Click via `mcp__humanizer__click` — never via cdp_eval.

### 4. Answer text input questions

For short answers and essays:
1. Generate a canonical answer in context (you are the LLM — reason directly)
2. Pass through `mcp__answer__humanize` to apply the candidate's writing voice
3. Click the field via `mcp__humanizer__click`
4. Type via `mcp__humanizer__type` — chunked if > 200 chars

Gate rule: free-text answers MUST go to supervised gate before submission in supervised mode.

### 5. Handle multi-page assessments

After answering all questions on the current page:
- If in supervised mode: call `mcp__vps__get_gate_answer` and wait for approval
- Click Next/Continue via `mcp__humanizer__click`
- Wait for page transition (poll DOM for new questions)
- Repeat from Step 1

### 6. Submission

When all pages are complete and Submit button is present:
- In supervised mode: show all answers on the final page for gate approval
- Click Submit via `mcp__humanizer__click`
- Wait for confirmation page / success message
- Report completion via `mcp__telegram__notify`

---

## Gate Rules

- Supervised mode: gate all free-text answers, gate the final Submit click on the last page
- Throughput mode: auto-submit everything (used only when explicitly set by operator)
- If the assessment asks for sensitive personal information not in the profile: gate and ask user

---

## Error Handling

- If a required field is found but cannot be answered (missing profile data): leave blank and note in gate message
- If a CAPTCHA appears: stop and call `mcp__telegram__notify("CAPTCHA — manual intervention needed")`
- If session expires / login wall appears: reconnect via `mcp__ixbrowser__reconnect()` and navigate back

## On Error

For tunnel/MCP/CDP/IXBrowser failures: see sops/sop_on_error_shared.md.

**Text assessment specific:**
- Question DOM not found → scroll down and re-read accessibility tree
- OTP field appears → check Redis for verification code via verify_code_injector
- CAPTCHA detected → stop; notify Telegram manual intervention needed
- Session expires mid-assessment → reconnect via ixbrowser_mcp.reconnect, navigate back
