# ADITYA — Frontend Lead

**Mission:** build the application the agent operates and the chat companion inside it. Your most important job is the **render acknowledgement**: after any state change, the browser tells the backend what it *actually* queried and displayed. That is what makes our verification honest, and a judge will ask about it.

Read first: `00_DECISIONS_AND_PLAN.md` (sections 2, 5, 6, 7, 8). Then `contracts/ui_state.py`, `contracts/metadata.py`, `contracts/events.py`, `contracts/hash_vectors.json`.

You own: `frontend/`. Do not edit other folders. Contract changes follow the change rule.

**Requirement:** Node 20+ and npm (not installed on the machine that wrote the contracts; install it on yours).

---

## What we want from you (deliverables)

| # | Deliverable | Notes |
|---|---|---|
| A0 | Vite + React + TypeScript project with Tailwind, Zustand, Recharts, TanStack Table, React Router | `frontend/` |
| A1 | TypeScript types generated/mirrored from the Pydantic contracts | `frontend/src/contracts.ts` |
| A2 | **Canonical series hash in TS passing all `hash_vectors.json` cases** | `frontend/src/hash.ts` + a vitest test |
| A3 | Metadata-driven shell: nav tree from `directories`/`pages`, routes from `Page.route` | loads `GET /api/application` |
| A4 | Generic widgets: grid (sort), bar chart, line chart, KPI card, filter bar | one component per `Widget.type` |
| A5 | Widget data fetching with the shared filter rule | `POST /api/widget-data` |
| A6 | WebSocket client: apply state, **send render acks**, forward user changes | `frontend/src/ws.ts` |
| A7 | Chat panel: streaming action log, confirm card, undo, confirm-mode toggle, citations, deep links | uses `AgentEvent`s |
| A8 | Debug drawer: current `UiState`, last ack, fault-injection button | demo weapon |
| A9 | Playwright smoke tests | `frontend/e2e/` |

Order: A0 → A1 → A2 → A3 → A4 → A5 → A6 (→ checkpoint I1) → A7 → A8 → A9.

---

## A2 — The hash (do this early; it is the riskiest detail)
The backend and browser must produce **byte-identical** hashes. A reference JavaScript implementation and its test already exist: `contracts/ts/hash.mjs` and `contracts/ts/hash.test.mjs`. Run `node --test contracts/ts/` first; copy the logic into `frontend/src/hash.ts` (use `globalThis.crypto.subtle` in the browser; `localhost` counts as a secure context).
Rules (the authoritative text is the comment above `_cell` in `contracts/ui_state.py`):
- number: `x = Number(v)`, `n = Math.floor(Math.abs(x) * 1e6 + 0.5)`, text = (`"-"` if `x < 0 && n !== 0`) + `String(n)` (so 1.5 → `"1500000"`). **Do not use `toFixed`**: its tie-rounding differs from Python and caused a real mismatch on values like 0.0078125. NaN/±Infinity → `"nan"`/`"inf"`/`"-inf"`.
- `null`/`undefined` → `"null"`; boolean → `"true"`/`"false"`; strings as they are (dates arrive from the server as ISO strings).
- row = cells joined with `\x1f` in the order of `columns` (missing key = `null`); rows sorted with the plain default `.sort()` (not `localeCompare`) and joined with `\x1e`.
- text = `columns.join("\x1f") + "\x1d" + rows`; hash = SHA-256 of the UTF-8 bytes, lowercase hex.
- The vectors in `contracts/hash_vectors.json` were produced by the Python implementation; **every case must pass in JS**. If one fails, tell the team which one: it may reveal a contract bug.

## A3–A5 — Rendering from metadata
- `GET /api/application` returns the canonical `Application`. Build navigation from `directories` and `pages`. Build routes from `Page.route`. Never hard-code a page.
- Each page renders its `widgets` plus a filter bar from `Page.filters` (select/multiselect with `allowed_values`, date range).
- **The filter rule (S5):** a page filter applies to a widget iff `filter.field` is a field of the widget's dataset; the `date_range` filter applies through the dataset's `time_field`. Widgets that lack the field ignore that filter. Implement this once in a helper and unit-test it.
- Widget data: `POST /api/widget-data {widget_id, filters, date_range, sort}` returns `{columns, rows, row_count}`. Render exactly those rows.
- Chart/grid details: columns = widget `dimensions` then `metrics` (S7). Bar chart: first dimension on the axis. Line chart: time dimension on the axis. KPI card: a single metric value. Grid: sortable columns.

## A6 — State, WebSocket and the render ack (the core job)
Protocol (see `contracts/ui_state.py`):
1. The backend is authoritative. It sends `apply_state {version, nonce, state, cause}`.
2. Apply the state to the router and the Zustand store, then let all widgets of the page fetch and render.
3. When **all widgets have settled** (data loaded and committed to the DOM; use an effect after the render commit), send `render_ack {version, nonce, route, widgets:[WidgetAck]}` where for each widget:
   - `applied_filters`, `applied_date_range`, `applied_sort`: copied from **the request body the widget actually sent** to `/api/widget-data`, **not** from the store. This is the whole point.
   - `row_count`: number of rows you render.
   - `series_hash`: `canonicalSeriesHash` of the rows you **display**, using the widget's `columns`.
   - `render_ms`, and `error` if the fetch failed.
4. If a widget has not settled in ~5 s, send the ack anyway with `error` set (never hang the agent).
5. When the **user** changes a filter by hand, send `user_state_change` so the backend and agent stay in sync.
6. Reconnect with backoff; on reconnect, request the current state.

## A7 — Chat companion
- Single WebSocket carries `agent_event` envelopes (`AgentEventEnvelope`). Messages are sent by `POST /api/session/{id}/message`.
- **Action log:** one row per `understanding/retrieval/plan/validation/action/verify` event with status icons; fill it as events stream in (this hides latency).
- **Verified badge:** show "rendered state matches expectation ✓" (from the `verify` event), and a red state with the mismatch list when it fails.
- **Answer:** show text plus **citation chips** (evidence ids). Clicking a chip highlights the widget/page it came from. Show deep links as buttons.
- **Confirm card:** on `confirm_request`, show the summary with Approve/Decline → `POST /api/session/{id}/confirm`.
- **Confirm-mode toggle:** switch between "auto-run with undo" and "confirm every state change"; sent as `config.confirm_mode`.
- **Undo** button → `POST /api/session/{id}/undo`.
- Chart/summary cards inline for analytical answers (using `evidence` events).

## A8 — Debug drawer
Show current `UiState` JSON, the last `RenderAck`, round-trip time, and a **"Inject fault"** control calling `POST /api/debug/fault` (for example: drop the region filter in widget-data). In the demo this proves the verifier catches a wrong render.

## A9 — Playwright
Smoke tests: the app loads and renders a page from metadata; a pushed state changes the visible filters; the ack hash equals the expected one; the confirm card works. A subset is reused by Praneel's evaluation for real-browser runs.

---

## What to read and explore (time-boxed)
| Item | Time | What to extract |
|---|---|---|
| `contracts/ui_state.py`, `contracts/hash_vectors.json` | 30 min | Exact protocol and hash rules |
| WorkArena paper Table 2 and the SeeAct abstract (arXiv 2403.07718, 2401.01614) | 15 min | Why we *do not* build a screen-reading agent; why the ack matters |
| Docs: TanStack Table, Recharts, Zustand, React Router | 20 min each | APIs |
| Playwright docs (Test, WebSocket events) | 30 min | e2e |
| Optional: Appsmith/Superset UIs for UX inspiration | 15 min | Look only; we do not integrate them |

## Interfaces
- **You consume:** `GET /api/application`, `POST /api/widget-data`, WebSocket events, `agent_event`s.
- **You provide:** `render_ack`, `user_state_change`, REST calls for message/confirm/undo.
- Until Shreeniketh's backend works, run a **mock server** (even a tiny FastAPI/Node script) that serves `examples/tenant_a_sample.json`, a canned `/api/widget-data`, and a WS that sends one `apply_state`. Share it with the team.

## Done when
- Checkpoint **I1**: the page renders from metadata, the backend pushes a state, and your ack matches the backend's expectation (hashes equal).
- All hash vectors pass in TS.
- The chat panel streams the action log, confirm card works, citations link to widgets.
- Playwright smoke tests pass.

## Pitfalls
- Building the ack from the store instead of from the actual request/rows (this recreates the circular-verifier bug).
- `localeCompare` or different number formatting in the hash.
- Acking before widgets finish rendering.
- Hard-coding pages, filter values or routes.
- Applying a filter to widgets whose dataset lacks the field (S5).

## Messages you owe others
- To Shreeniketh: the exact `render_ack` you send; confirm the filter rule helper matches his verifier.
- To Shashank: confirm the `AgentEvent` types you render.
- To Praneel: which pages/widgets types you support so generated pages only use those.
