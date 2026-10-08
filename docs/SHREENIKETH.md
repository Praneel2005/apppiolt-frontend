# SHREENIKETH — Frontend: design kit, chat assistant panel, action log, debug drawer

Hi Shreeniketh. You and Aditya are building the **AppPilot web app** (the screen) for HackFest 2026, problem 5A. Praneel's side builds the data, the backend server and the AI agent. This file tells you exactly what to build, how it must look, which backend API to use, and how to push your work.

**Your part:** the shared design kit (components and theme), the **chat panel** where the user talks to the assistant, the live **action log** that shows every step the assistant takes, confirm and undo, citation chips, and the debug drawer.
**Aditya's part:** the app shell, navigation, pages, charts and tables, URL state and the live connection. You two share one React project.

---

## 1. What AppPilot is (read once)
AppPilot is an analytics app over real Brazilian e-commerce data (Olist, 2016–2018) with an AI assistant inside it. A user types "Show revenue by customer state last quarter", and the assistant opens the right page, sets the filters, checks that the screen really shows the right data, and answers with numbers that cite their source. **Your chat panel is where judges watch the assistant think and act.** Make every step visible, clear and honest.

## 2. Setup
1. Accept the GitHub invite, then: `git clone https://github.com/Praneel2005/apppiolt-frontend.git` and `cd apppiolt-frontend`
2. Install **Node 20 LTS** and **Python 3.11+**.
3. Read: `contracts/events.py` (the assistant's event types), `contracts/ui_state.py` (state and WebSocket messages), `contracts/actions.py` (`AgentConfig.confirm_mode`).
4. **Run the backend locally.** You are not on our network, so use the **mock backend** in `mock/`: same API as the real server, real Olist numbers, and a **scripted fake assistant** that streams realistic events (so you can build the chat before the real AI exists).
   ```
   python -m venv .venv
   # Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
   pip install -r mock/requirements.txt
   python -m uvicorn mock.server:app --port 8000 --reload      # run from the repo root
   ```
   If `mock/` is not in the repo yet, Praneel will push it; until then build against the sample events in §6.
5. **Aditya creates the Vite project in `frontend/`** in his first hour. Pull it, then `cd frontend && npm ci`. Start with the design kit (§3), which needs nothing else.

## 3. Design kit (you own this; Aditya uses it)
**Feel:** clean, light, professional analytics tool. Plenty of whitespace, no gradients, no decorative stripes.

| Token | Value |
|---|---|
| Page background | `#F8FAFC` |
| Card / surface | `#FFFFFF`, border `#E2E8F0`, radius 12px, shadow `0 1px 2px rgba(15,23,42,.06)` |
| Text / muted text | `#0F172A` / `#64748B` |
| Primary | `#4F46E5` (hover `#4338CA`) |
| Success / warning / danger | `#16A34A` / `#D97706` / `#DC2626` |
| Font | Inter (fallback: system-ui), 14px body, 20px titles |
| Spacing | 8px grid (8 / 16 / 24 / 32) |

Put the tokens in the Tailwind config, then build in `src/ui/`: `Card`, `Button` (primary / secondary / ghost / danger), `Badge` (neutral / success / warning / danger), `Toggle`, `Select`, `MultiSelect` (searchable), `Spinner`, `Skeleton`, `Drawer`, `Tooltip`, `EmptyState`. Keep them small and typed. **Push the kit early (by 9 Oct midday) so Aditya can use it.**

## 4. Layout and where your parts sit
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Top bar: AppPilot · Olist Marketplace Analytics   [Data as of 31 Aug 2018]  [Debug] │
├──────────────┬──────────────────────────────────────────────┬────────────────┤
│ Left nav     │ Page (Aditya)                                │ CHAT PANEL     │
│ (Aditya)     │                                              │ (you, 400px)   │
│              │                                              │                │
├──────────────┴──────────────────────────────────────────────┴────────────────┤
│ DEBUG DRAWER (you): slides up from the bottom when [Debug] is clicked          │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Chat panel (400px, right side, collapsible to a 56px icon rail)
```
┌ AppPilot assistant ───────────── [Confirm mode ○] [↶ Undo] ┐
│                                                             │
│  (empty state) Try asking:                                  │
│   [Show revenue by customer state last quarter]             │
│   [Compare orders in São Paulo last month]                  │
│   [Why did revenue change in November 2017?]                │
│   [Open the late delivery trend]                            │
│   [Delete all March orders]   ← shows a refusal             │
│                                                             │
│                      ┌─────────────────────────┐            │
│                      │ user message (right)    │            │
│                      └─────────────────────────┘            │
│  ┌ Steps (collapsible, live) ──────────────────────┐        │
│  │ 🔎 Understood: compare periods                  │        │
│  │ 📄 Pages found: Revenue by customer state (+2)  │        │
│  │ 🧭 Plan: navigate → set filter → set dates      │        │
│  │ ✅ Plan checked against the app                  │        │
│  │ ▶ navigate · set_filter · set_date_range        │        │
│  │ 🛡 Screen verified ✓   (or red: 1 mismatch ▾)   │        │
│  │ 📊 Evidence e1 read from widget                 │        │
│  └──────────────────────────────────────────────────┘        │
│  ┌ answer (left) ──────────────────────────────────┐        │
│  │ … top entry is SP with revenue R$ 1,234,567 [e1]│        │
│  │ [Open page]                                      │        │
│  └──────────────────────────────────────────────────┘        │
│ ┌──────────────────────────────────────────┐ [Send]          │
│ │ Ask about your data…                     │                 │
│ └──────────────────────────────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
```

## 5. Behaviour you must implement
1. **Sending:** `POST /api/session/{session_id}/message` with `{"text": "...", "config": {"confirm_mode": true|false}}` → 202. The reply arrives as a stream of `agent_event` messages on the WebSocket (Aditya's connection puts them into the store; read them with `useAgentEvents()`). Disable the input while a run is active (until the `done` event); show a spinner on the current step.
2. **Steps timeline:** one row per event, appended live in `seq` order (§6 table). Collapsed to a one-line summary once the answer arrives; click to expand.
3. **Verify row:** green badge "Screen verified ✓" when `data.ok` is true. When false, a red badge "Screen mismatch" that expands to list each mismatch (`widget_id`, `field`, expected vs actual). **Never hide a mismatch**; it is a feature the judges should see.
4. **Answer bubble:** render `data.text`, turning citation markers like `[e1]` into small chips. Clicking a chip calls Aditya's `highlightWidget(evidence.source)` (the `evidence` event with that `evidence_id` tells you the widget). Show `data.deep_links` as "Open page" buttons that navigate to the route.
5. **Confirm mode:** a toggle in the header (default **off**). When on, the assistant sends `confirm_request {action_id, summary}` before changing the screen: show an inline card with the summary and **Approve / Decline** → `POST /api/session/{id}/confirm` with `{"action_id", "approve": true|false}`.
6. **Undo:** header button → `POST /api/session/{id}/undo`. The server pushes the previous state to the screen; show a small toast "Reverted to the previous view".
7. **Errors:** an `error` event shows a red row with `message`; if `recoverable`, add a "Try again" button that resends the last message.
8. **Suggested prompts:** the five chips in the empty state send that text when clicked.
9. **Keyboard:** Enter sends, Shift+Enter makes a new line, Esc collapses the panel.

## 6. Assistant events (exact; from `contracts/events.py`)
Every WebSocket message of this kind looks like `{"type": "agent_event", "event": {"seq": 3, "type": "plan", "data": {...}}}`.

| `event.type` | `data` | Show as |
|---|---|---|
| `understanding` | `{intent, slots}` | 🔎 "Understood: <intent in words>" (`compare_periods` → "compare periods", `unsafe_action` → "not allowed") |
| `retrieval` | `{candidates: [{page_id, title}]}` | 📄 "Pages found: <first title> (+N)", expandable list |
| `plan` | `{steps: [{tool, args}]}` | 🧭 "Plan: navigate → set filter → …" (tool names in words) |
| `validation` | `{ok, issues}` | ✅ "Plan checked against the app", or ⚠ list of issues |
| `confirm_request` | `{action_id, summary}` | inline confirm card (§5.5) |
| `action` | `{tool, args, status}` | ▶ one line per tool call |
| `verify` | `{ok, mismatches}` | 🛡 green or red badge (§5.3) |
| `evidence` | `{evidence_id, kind, source, query, values}` | 📊 "Evidence e1 read from <source>"; keep it for the citation chips |
| `answer_delta` | `{text}` | append to the answer while streaming (may not be used yet) |
| `answer` | `{text, citations, deep_links}` | the answer bubble |
| `error` | `{code, message, recoverable}` | red row (§5.7) |
| `done` | `{steps, replans, elapsed_ms}` | end of run; re-enable input; small muted "done in 2.1 s" |

Sample stream to build against before the mock is ready:
```json
{"type":"agent_event","event":{"seq":1,"type":"understanding","data":{"intent":"set_state","slots":{}}}}
{"type":"agent_event","event":{"seq":2,"type":"retrieval","data":{"candidates":[{"page_id":"sales.revenue_by_customer_state","title":"Revenue by customer state"}]}}}
{"type":"agent_event","event":{"seq":3,"type":"plan","data":{"steps":[{"tool":"navigate","args":{"page_id":"sales.revenue_by_customer_state"}},{"tool":"set_date_range","args":{"preset":"last_quarter"}}]}}}
{"type":"agent_event","event":{"seq":4,"type":"validation","data":{"ok":true,"issues":[]}}}
{"type":"agent_event","event":{"seq":5,"type":"action","data":{"tool":"navigate","args":{"page_id":"sales.revenue_by_customer_state"},"status":"running"}}}
{"type":"agent_event","event":{"seq":6,"type":"verify","data":{"ok":true,"mismatches":[]}}}
{"type":"agent_event","event":{"seq":7,"type":"evidence","data":{"evidence_id":"e1","kind":"widget_read","source":"sales.revenue_by_customer_state.chart","query":null,"values":{}}}}
{"type":"agent_event","event":{"seq":8,"type":"answer","data":{"text":"SP has the highest revenue last quarter [e1].","citations":["e1"],"deep_links":["/sales/revenue-by-customer-state"]}}}
{"type":"agent_event","event":{"seq":9,"type":"done","data":{"steps":3,"replans":0,"elapsed_ms":2100}}}
```

## 7. Debug drawer (bottom, opened by the [Debug] button in the top bar)
Tabs:
- **State:** current UI state JSON from `useUiState()` (pretty-printed, copy button).
- **Last ack:** the last `render_ack` the browser sent (Aditya stores it in the store as `lastAck`).
- **Verify:** the last `verify` event, with mismatches in a table.
- **Faults:** a dropdown `none / drop_filter / empty_widget / slow_widget` → `POST /api/debug/fault {"kind": ...}`. In the demo we switch on `drop_filter` and show the verify row turning red. Label the tab "Developer / demo tools".

## 8. Backend API you use (same on the mock and the real server)
| Call | Body → response |
|---|---|
| `POST /api/session/{id}/message` | `{"text", "config": {"confirm_mode"}}` → 202; events arrive on the WebSocket |
| `POST /api/session/{id}/confirm` | `{"action_id", "approve"}` → `{"ok"}` |
| `POST /api/session/{id}/undo` | → `{"ack", "verify"}` or `{"error"}` |
| `POST /api/debug/fault` | `{"kind"}` → `{"kind"}` |
| `GET /api/application` | page titles, if you need them for display |

The session id and the WebSocket come from Aditya's `useSession()`; do not open a second connection.

## 9. Folder ownership inside `frontend/`
| You (Shreeniketh) | Aditya |
|---|---|
| `src/ui/` (design kit), `src/chat/`, `src/debug/`, Tailwind theme config | `src/app/`, `src/nav/`, `src/pages/`, `src/widgets/`, `src/state/`, `src/net/`, `src/lib/` |

Shared contract between you: Aditya's `src/state/store.ts` exposes `useUiState()`, `useSession()`, `useAgentEvents()`, `lastAck`, and he exports `highlightWidget(id)`. Agree on these names on day one and don't rename them later.

## 10. How to push your work
- Work on a branch: `git checkout -b shree/<feature>` (for example `shree/chat-panel`).
- Commit small and often: `git commit -m "chat: steps timeline"`. Never commit `node_modules`, `.env` or build output.
- Push and open a **Pull Request into `main`** on GitHub: `git push -u origin shree/<feature>`. Praneel reviews and merges.
- Before starting work each day: `git checkout main && git pull`, then rebase or merge into your branch.
- **Only change files inside `frontend/`.** If something in `contracts/`, `mock/` or the API looks wrong, message Praneel; don't edit it.

## 11. Deadlines
| When | You must have |
|---|---|
| **8 Oct (tonight)** | Read this file and the contracts; Tailwind theme and first components started |
| **9 Oct, 13:00** | Design kit pushed (Aditya needs it) |
| **9 Oct, 23:00** | Chat panel sends messages and renders the full event stream from the mock (steps timeline, answer, citations) |
| **10 Oct, 18:00** | Confirm card, undo, verify mismatch view, error rows, suggested prompts, debug drawer with faults; Playwright test: send a message → steps appear → answer with a citation chip |
| **10 Oct, 21:00** | Feature freeze; `npm run build` passes; demo rehearsal on Praneel's PC |

## 12. Definition of done
- [ ] Every event type in §6 renders correctly and live, in order.
- [ ] Verify mismatches are clearly visible (test with the `drop_filter` fault).
- [ ] Citation chips highlight the right widget; "Open page" navigates.
- [ ] Confirm mode works end to end (approve and decline); undo works.
- [ ] Suggested prompts, keyboard shortcuts, collapsed panel, error retry.
- [ ] Debug drawer shows state, last ack and verify, and switches faults.
- [ ] The design kit is used consistently across the app.

If you use an AI coding assistant, give it this file plus `AGENTS.md` and the `contracts/` folder. Questions → Praneel.
