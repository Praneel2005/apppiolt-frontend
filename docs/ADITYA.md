# ADITYA — Frontend: app shell, pages, widgets, live state

Hi Aditya. You and Shreeniketh are building the **AppPilot web app** (the screen) for HackFest 2026, problem 5A. Praneel's side builds the data, the backend server and the AI agent. This file tells you exactly what to build, how it must look, which backend API to use, and how to push your work.

**Your part:** the app shell, navigation, page renderer, filter bar, charts and tables, URL state, the live connection to the backend, and the **render acknowledgement** (the browser's report of what it actually showed).
**Shreeniketh's part:** the design kit, the chat panel, the action log, confirm/undo and the debug drawer. You two share one React project.

---

## 1. What AppPilot is (read once)
AppPilot is an analytics app over real Brazilian e-commerce data (Olist, 2016–2018) with an AI assistant inside it. A user types "Show revenue by customer state last quarter", and the assistant opens the right page, sets the filters, and answers with numbers. **The app moves on its own, and the server checks that the screen really shows the right data.** That check is your render acknowledgement (§6), which is the most important thing you build.

All pages are **generated from one file**, `data/olist/application.json` (100 pages, 7 modules). You never hand-code a page; you write one renderer that draws any page from that file.

## 2. Setup
1. Accept the GitHub invite, then: `git clone https://github.com/Praneel2005/apppiolt-frontend.git` and `cd apppiolt-frontend`
2. Install **Node 20 LTS** and **Python 3.11+**.
3. Read: `contracts/ui_state.py` (state and WebSocket messages), `contracts/metadata.py` (shape of `application.json`), `contracts/ts/hash.mjs` and `contracts/hash_vectors.json` (the hash).
4. **Run the backend locally.** You are not on our network, so use the **mock backend** in `mock/`: same API as the real server, real Olist numbers (SQLite), scripted fake assistant.
   ```
   python -m venv .venv
   # Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
   pip install -r mock/requirements.txt
   python -m uvicorn mock.server:app --port 8000 --reload      # run from the repo root
   ```
   Check: open http://localhost:8000/api/application, which should return JSON with 100 pages. If `mock/` is not in the repo yet, Praneel will push it; until then build against `data/olist/application.json` directly.
5. **Create the frontend project in the first hour and push it**, so Shreeniketh can start:
   ```
   npm create vite@latest frontend -- --template react-ts
   cd frontend
   npm i zustand recharts @tanstack/react-table react-router-dom
   npm i -D tailwindcss postcss autoprefixer vitest @playwright/test
   ```
   In `vite.config.ts`, proxy the backend so the browser only talks to `localhost:5173`:
   ```ts
   server: { proxy: { "/api": "http://localhost:8000", "/ws": { target: "ws://localhost:8000", ws: true } } }
   ```

## 3. Design (shared with Shreeniketh; he builds the components, you use them)
**Feel:** clean, light, professional analytics tool. Plenty of whitespace, no gradients, no decorative stripes.

| Token | Value |
|---|---|
| Page background | `#F8FAFC` |
| Card / surface | `#FFFFFF`, border `#E2E8F0`, radius 12px, shadow `0 1px 2px rgba(15,23,42,.06)` |
| Text / muted text | `#0F172A` / `#64748B` |
| Primary (buttons, chart bars, links) | `#4F46E5` |
| Success / warning / danger | `#16A34A` / `#D97706` / `#DC2626` |
| Font | Inter (fallback: system-ui), 14px body, 20px page title, 28px KPI numbers |
| Spacing | 8px grid (8 / 16 / 24 / 32) |

**Number formatting (use everywhere, by the metric's `unit` in `application.json`):**
| unit | format | example |
|---|---|---|
| `BRL` | `R$` + thousands separators; no decimals at ≥ 1,000, 2 decimals below | `R$ 2,849,730` · `R$ 143.22` |
| `ratio` | percent, 1 decimal | `0.0483` → `4.8%` |
| `days` | 1 decimal + " days" | `10.6 days` |
| `count` | integer with separators | `19,897` |
| `score` | 2 decimals | `4.09` |

## 4. Layout (desktop first; must still work at 1280px)
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Top bar (56px): AppPilot · Olist Marketplace Analytics   [Data as of 31 Aug 2018] [Debug] │
├──────────────┬──────────────────────────────────────────────┬────────────────┤
│ Left nav     │ Breadcrumb: Sales › Breakdowns               │ Chat panel     │
│ (260px)      │ Page title (20px) + one-line description     │ (400px,        │
│ Sales        │ ┌ Filter bar ─────────────────────────────┐  │ collapsible to │
│  Overview    │ │ Date range ▾  Region ▾  State ▾  [Reset] │  │ a 56px rail)   │
│  Breakdowns ▸│ └──────────────────────────────────────────┘  │                │
│  Trends     ▸│ Widgets (layout depends on page type, below) │ Shreeniketh    │
│ Customers…   │                                              │ builds this    │
└──────────────┴──────────────────────────────────────────────┴────────────────┘
```
- **Left nav:** modules from `application.json` → under each module "Overview", then the `breakdowns` and `trends` directories as collapsible groups listing their pages. Add a search box at the top that filters page titles. Highlight the current page.
- **Filter bar:** one control per entry in `page.filters`.
  - `date_range` → a dropdown of presets (Last 12 months, Last quarter, Last month, This year, Year to date, Last year, Last 90 days) plus custom from/to dates. Get preset dates from `GET /api/date-presets`; never compute them yourself.
  - `multiselect` → a searchable multi-select with `allowed_values`. For states, show "SP — São Paulo" (names come from `synonyms`).
  - [Reset] returns to `page.default_state`.

**Page types** (detect from the widgets):
| Page type | How to detect | Layout |
|---|---|---|
| Overview (7 pages) | has `kpi_card` widgets | row of up to 4 KPI cards, then a full-width line chart |
| Breakdown, 1 dimension (61) | `bar_chart` + `grid` with one dimension | bar chart left (60%) + table right (40%); stacked under 1400px |
| Breakdown, 2 dimensions (18) | `grid` with two dimensions | bar chart full width on top, table full width below |
| Trend (14) | first widget is `line_chart` over `order_month` | line chart full width, table below |

## 5. Widgets (one React component per `widget.type`)
For every widget, call `POST /api/widget-data` and render **exactly the rows returned**. Columns are `widget.dimensions` followed by `widget.metrics`.
- **`kpi_card`:** title, one big formatted number, small muted unit/description. One row, no dimensions.
- **`bar_chart`:** horizontal bars, category on the y-axis, metric on the x-axis, primary colour, value labels on the bars. If there are more than 20 categories, draw the top 20 by the current sort and show "showing 20 of N, see table". Clicking a bar may set that value as a filter (nice to have).
- **`line_chart`:** x = `order_month` ascending (label "Jan 2018"), y = metric, dots on points, tooltip with formatted values.
- **`grid`:** TanStack Table; sortable column headers (a sort change becomes UI state, §6); formatted numbers right-aligned; sticky header; 25 rows per page.
- Every widget card has a title, a loading skeleton, an error state, and an **"empty" state** ("No data for this selection").
- Give each widget card `data-widget-id={widget_id}` and export a function `highlightWidget(widgetId)` (2-second primary-colour outline plus scroll into view). Shreeniketh's citation chips call it.

## 6. State, URL and the render acknowledgement (the core of your job)
**UI state** (exact shape in `contracts/ui_state.py`):
```json
{"route": "/sales/revenue-by-customer-state", "page_id": "sales.revenue_by_customer_state",
 "filters": {"customer_region": {"op": "in", "value": ["Southeast"]}},
 "date_range": {"from": "2018-04-01", "to": "2018-06-30", "preset": "last_quarter"},
 "sort": {"field": "revenue", "dir": "desc"}, "selected_widget": null, "version": 7}
```
- **The backend is the boss of the state.** Keep it in a Zustand store; the URL mirrors it.
- **URL format (must be exact; deep links and the assistant depend on it):**
  `/sales/revenue-by-customer-state?from=2018-04-01&to=2018-06-30&preset=last_quarter&f.customer_region=Southeast&f.customer_state=SP,RJ&sort=revenue:desc`
  (route = `page.route`; each filter is `f.<filter_id>` with comma-separated values; `sort=<field>:<asc|desc>`.)

**Connection:** on load, `POST /api/session` returns `{session_id, state}`; then open `WebSocket /ws/{session_id}`. Messages:

| Direction | Message | You must |
|---|---|---|
| server → you | `{"type": "apply_state", "version", "nonce", "state", "cause"}` | set store + URL to `state`, let every widget on the page refetch and render, then send a `render_ack` |
| you → server | `{"type": "render_ack", "version", "nonce", "route", "widgets": [WidgetAck...]}` | see the rules below |
| you → server | `{"type": "user_state_change", "state"}` | send whenever the **user** changes page, filters, dates or sort by hand |
| server → you | `{"type": "agent_event", "event": {...}}` | put it in a store slice Shreeniketh's chat panel reads (`useAgentEvents`) |

**Render acknowledgement rules (follow exactly; the server verifies them):**
1. Send it only after **all widgets on the page have finished** (data received and rendered). If a widget is not done within 5 s, send the ack anyway with `error` set for that widget.
2. One `WidgetAck` per widget on the page:
   ```json
   {"widget_id": "...", "applied_filters": {...}, "applied_date_range": {...}, "applied_sort": null,
    "row_count": 27, "series_hash": "ab12…", "render_ms": 140, "error": null}
   ```
3. `applied_filters` = the filters **this widget actually put in its `/api/widget-data` request** (copy them from the request body you sent, not from the store). A widget only gets the page filters whose `field` exists in its dataset's `fields` (look up `widget.dataset_id` in `datasets`). Filters with an empty value list are left out.
4. `row_count` = number of rows the widget received and holds; `series_hash` = `canonicalSeriesHash(rows, columns)` over **all** received rows (columns = dimensions then metrics). Never drop or change rows before hashing; visual top-20 truncation is fine because the hash covers the data you hold.
5. Use the **same `version` and `nonce`** as the `apply_state` you are answering.

**The hash:** port `contracts/ts/hash.mjs` to `frontend/src/lib/hash.ts` using `globalThis.crypto.subtle`. It must pass **every** case in `contracts/hash_vectors.json` (write a vitest test that imports the JSON). Do not use `toFixed` or `localeCompare`; the reference file explains why.

**Test your ack against the mock** (no AI needed):
```
curl -X POST localhost:8000/api/debug/apply_state -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","state":{"route":"/sales/revenue-by-customer-state","page_id":"sales.revenue_by_customer_state","filters":{"customer_region":{"op":"in","value":["Southeast"]}},"date_range":{"from":"2018-04-01","to":"2018-06-30","preset":"last_quarter"}}}'
```
The answer contains `"verify": {"ok": true}` when your ack is correct; otherwise it lists each mismatch (`applied_filters`, `row_count` or `series_hash`) per widget. `GET /api/debug/expected/<id>` shows what the server expected.

## 7. Backend API you use (same on the mock and the real server)
| Call | Body → response |
|---|---|
| `GET /api/application` | → the full `application.json` |
| `GET /api/date-presets` | → `{"last_quarter": {"from": "2018-04-01", "to": "2018-06-30"}, ...}` |
| `POST /api/session` | → `{"session_id", "state"}` |
| `POST /api/widget-data` | `{"widget_id", "filters": {filter_id: {"op": "in", "value": [...]}}, "date_range": {"from", "to"}, "sort": {"field", "dir"} or null}` → `{"columns": [...], "rows": [{...}], "row_count": n}` |
| `WS /ws/{session_id}` | messages in §6 |
| `POST /api/debug/apply_state` | `{"session_id", "state"}` → `{"ack", "verify": {"ok", "mismatches"}}` (testing only) |
| `GET /api/debug/expected/{session_id}` | → expected acks for the current state (testing only) |
| `POST /api/debug/fault` | `{"kind": "none" | "drop_filter" | "empty_widget" | "slow_widget"}` (testing and demo only) |

Shreeniketh uses the chat endpoints (`/message`, `/confirm`, `/undo`). Never hard-code page ids, routes, filter values or numbers: everything comes from the API.

## 8. Folder ownership inside `frontend/`
| You (Aditya) | Shreeniketh |
|---|---|
| `src/app/` (shell, routing), `src/nav/`, `src/pages/`, `src/widgets/`, `src/state/` (store, session, URL sync), `src/net/` (api, ws), `src/lib/hash.ts`, `src/lib/format.ts` | `src/ui/` (design kit), `src/chat/`, `src/debug/` |

Shared contract between you: `src/state/store.ts` exposes `useUiState()`, `useSession()` (session id, ws send), `useAgentEvents()`; you export `highlightWidget(id)`. Agree on these names on day one and don't rename them later.

## 9. How to push your work
- Work on a branch: `git checkout -b aditya/<feature>` (for example `aditya/renderer`).
- Commit small and often: `git commit -m "renderer: bar chart widget"`. Never commit `node_modules`, `.env` or build output.
- Push and open a **Pull Request into `main`** on GitHub: `git push -u origin aditya/<feature>`. Praneel reviews and merges.
- Before starting work each day: `git checkout main && git pull`, then rebase or merge into your branch.
- **Only change files inside `frontend/`.** If something in `contracts/`, `mock/` or the API looks wrong, message Praneel; don't edit it.
- Commit `frontend/package-lock.json` so everyone gets the same versions.

## 10. Deadlines
| When | You must have |
|---|---|
| **8 Oct (tonight)** | Vite project pushed; nav renders all modules and pages from `/api/application` |
| **9 Oct, 23:00** | All 4 page types render with real data; filter bar and URL state work; WebSocket connected; **the debug `apply_state` returns `verify.ok = true`**; hash tests pass |
| **10 Oct, 18:00** | Sorting, empty/error states, `highlightWidget`, polish; Playwright test: push a state → visible filters change → ack verified |
| **10 Oct, 21:00** | Feature freeze; `npm run build` passes; demo rehearsal on Praneel's PC |

## 11. Definition of done
- [ ] Any of the 100 pages renders from metadata, with no hard-coded pages.
- [ ] Filters, dates and sort change the data and the URL; reloading a deep link restores the same view.
- [ ] `apply_state` from the server updates the screen and returns a correct `render_ack` (`verify.ok` true on the mock).
- [ ] With `POST /api/debug/fault {"kind":"drop_filter"}`, the verify result shows a mismatch (this proves the check works).
- [ ] All `hash_vectors.json` cases pass in vitest.
- [ ] Numbers formatted per §3; loading, empty and error states exist.

If you use an AI coding assistant, give it this file plus `AGENTS.md` and the `contracts/` folder. Questions → Praneel.
