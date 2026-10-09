# AppPilot: project handoff, status and agent design

*Context-Aware Application Agent for a metadata-driven business application.* KLE Tech HackFest 2026, Domain 5, problem
statement 5A. Mentor review done on 9 Oct 2026; **offline round with a live demo on 11 Oct 2026**.
Repository: `https://github.com/Praneel2005/apppiolt-frontend` (branch `main`; last commit at the time of writing: `56710c8`).

This document is the single source of truth. Part A moves the workspace to a new machine; Parts B to G say what exists;
Parts H to K say what remains and how the agent is designed; Part L is a briefing you can paste into a new coding-assistant session.

---

## 0. Status at a glance

| Area | State | Evidence |
|---|---|---|
| Data layers (original, derived, synthetic) | **Done, verified** | row counts and reconciliation tests (see §5) |
| Backend: ~49 catalogued APIs, writes with preview / confirm / undo, sessions, WebSocket protocol, validator, executor, verifier, deep links, trend and mix/rate analytics, KPI counters | **Done, verified** | 144 backend tests pass; numbers compared with independent SQL |
| Web app rendered from metadata (15 operations pages with KPI strips and charts, 100-page report library, actions, assistant panel, plan runner, canvas) | **Built; dashboard seen working; some browser paths not yet confirmed** | 30 frontend tests, clean type check, screenshot of the dashboard. **Not yet confirmed in a browser:** the live `render_ack` loop ("screen verified (browser)"), the write dialog with Undo, the generated canvas, the chart styling (see §H.0) |
| **LLM agent (Gemini)** | **Not started** (design in §J) | |
| **Evaluation harness (KPIs K1 to K7)** | **Not started** (plan in `docs/KPI_PLAN.md` and §K) | |
| Demo script and slides for 11 Oct | Not started (outline in §L) | older slide text in `5A_Simple_Explainer_and_Slide_Content.md` |

Today is 9 Oct; the demo is 11 Oct. The agent and a first evaluation run are the critical path.

---

# PART A: Move the workspace to your local machine

The code is in GitHub, so moving is a clone plus a rebuild. The database does **not** need to be copied: it is rebuilt from
the Olist CSVs in about 15 seconds, and the synthetic data is seeded so it comes out identical (checksums in step 9).

Part letters: A move, B project, C built, D structure, E verification, F decisions, G limits, H remaining work, **J agent
design**, K evaluation design, L demo and briefing (there is no Part I; the letters are kept because other sections refer to them).

## A.1 What is in git, and what is not

| In git | **Not** in git (carry or recreate) |
|---|---|
| all code (`backend/`, `web/`, `generator/`, `contracts/`, `sql/`, `tests/`, `docs/`, `scripts/`) | `.env` (your Gemini key; create it locally) |
| `data/olist/application.json` (regenerated anyway) | the Olist zips (not needed once the CSVs are in git) |
| the **Olist CSVs** in `data/olist/` and `data/olist_funnel/` (committed on purpose; if you cloned a version without them, step 4 below unzips them from your Downloads) | |
| `research/` (the plan documents and the paper PDFs), if you pushed it | |
| | `.venv/`, `web/node_modules/`, `logs/`, `.llm_cache/` (recreated) |
| | the Postgres volume (rebuilt) |
| | outside the repo on the workstation: `/home/jatin/fest/papers/` (16 PDFs and notes), `5A_Context_Aware_Application_Agent_PreImplementation_Plan.md`, `5A_Simple_Explainer_and_Slide_Content.md` (copy with `scp`, step 10) |

## A.2 Steps (Windows, PowerShell)

**1. Install the tools (once).** Docker Desktop is optional; see A.3 if the demo PC cannot run it.
```powershell
winget install -e --id Git.Git
winget install -e --id Python.Python.3.12
winget install -e --id OpenJS.NodeJS.LTS          # Node 22 or newer (the test runner wants >= 22.12)
winget install -e --id Docker.DockerDesktop       # restart Windows once after installing
```
Close and reopen PowerShell so the new tools are on `PATH`; check `git --version`, `py -3.12 --version`, `node -v`, `docker --version`.

**2. Get the code.** Enter your own credentials when asked. The GitHub token pasted earlier in the project chat must be
**revoked**; create a new fine-grained token limited to this repository if Git asks for one.
```powershell
cd $HOME\Documents          # or any folder without spaces
git clone https://github.com/Praneel2005/apppiolt-frontend.git apppilot
cd apppilot
git log --oneline -3        # the newest commit should be 56710c8 or later
```

**3. Python environment.**
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned, then retry
python -m pip install -U pip
pip install -r requirements.txt
```

**4. The Olist data** (the two zips from your Downloads; the CSVs are never committed).
```powershell
New-Item -ItemType Directory -Force data\olist, data\olist_funnel | Out-Null
Expand-Archive "$HOME\Downloads\archive.zip" data\olist -Force
Expand-Archive "$HOME\Downloads\marketing_funnel_olist.zip" data\olist_funnel -Force
dir data\olist\*.csv, data\olist_funnel\*.csv      # 9 + 2 files
```

**5. Settings.** Create `.env` from the template and put your Gemini key in it (never commit or paste it):
```powershell
Copy-Item .env.example .env
notepad .env        # fill GEMINI_API_KEY= and GEMINI_MODEL= (the model name exactly as Google AI Studio shows it)
```
The database defaults in `.env.example` match both options in A.3.

**6. Database (Docker option).**
```powershell
docker compose up -d db
docker compose ps          # wait until "healthy"
```

**7. Load everything (about 15 seconds).**
```powershell
python -m generator.load_olist          # original CSVs -> raw_*, derived tables, roles
python -m generator.synthesize          # simulated syn_* / ops_* tables
python -m generator.build_application   # data\olist\application.json
python -m pytest -q                     # expect 144 passed
```

**8. Run it** (two terminals, both in the repo folder).
```powershell
# terminal 1 (venv active)
uvicorn backend.main:app --port 8000
# terminal 2
cd web
npm ci
npm run dev
```
Open `http://localhost:5173`. With the backend up you should see the Operations dashboard with live numbers and a green "Live" dot.
Then `python scripts\ws_smoke.py` (venv active, backend running) should print three PASS lines.

**9. Checksums (prove the rebuild is identical).** `load_olist` prints these row counts: customers 99,441 · orders 99,441 ·
order items 112,650 · payments 103,886 · reviews 99,224 · products 32,951 · sellers 3,095 · leads 8,000 · closed deals 842.
`synthesize` prints: targets 486 · SLA rules 54 · forecasts 972 · listings 16,725 · inventory snapshots 100,350 ·
restock orders 5,928 · tickets 5,632 · review replies 4,320 · promotions 14 · seller flags 29. Sanity numbers: revenue of valid
sales R$ 13,494,400.74; late-delivery rate 8.11%; Q2-2018 revenue R$ 2,849,730.26.

**10. Copy the files that are outside the repo** (from the Windows PowerShell; the workstation is `10.9.0.104`):
```powershell
New-Item -ItemType Directory -Force ..\apppilot-extras | Out-Null
scp "jatin@10.9.0.104:/home/jatin/fest/5A_*.md" ..\apppilot-extras\
scp -r jatin@10.9.0.104:/home/jatin/fest/papers ..\apppilot-extras\papers
```

**If GitHub is unreachable**, make an archive on the workstation instead (this excludes the heavy and secret items):
```bash
cd /home/jatin/fest && tar --exclude=.venv --exclude=node_modules --exclude='*.csv' --exclude=.env --exclude=logs -czf apppilot.tgz apppilot
```
then `scp jatin@10.9.0.104:/home/jatin/fest/apppilot.tgz .` and extract it (`tar -xzf apppilot.tgz` works in current Windows).

## A.3 No Docker? Use a native PostgreSQL 16 (replaces step 6)
```powershell
winget install -e --id PostgreSQL.PostgreSQL.16      # remember the postgres superuser password you choose
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE ROLE app LOGIN SUPERUSER PASSWORD 'app';"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE DATABASE appdb OWNER app;"
```
Everything else is identical. The read-only (`app_ro`) and write (`app_rw`) roles are created by `sql/30_roles_and_grants.sql`
during the load. If another PostgreSQL already listens on port 5432, stop it or change the port in `.env` (`DATABASE_URL`,
`DATABASE_URL_RO`, `DATABASE_URL_RW`).

## A.4 Troubleshooting
| Symptom | Cause and fix |
|---|---|
| `Connection refused` on 5432 | database not running: `docker compose up -d db` (or start the PostgreSQL service) |
| tests show many "skipped" | the database is unreachable or not loaded (steps 6 and 7) |
| `ModuleNotFoundError: contracts` | run Python modules from the repo root, as `python -m generator.load_olist` |
| browser says "Can't reach the backend" | start `uvicorn` (step 8); Vite proxies `/api` and `/ws` to port 8000 |
| `npm ci` complains about the Node version | install Node 22 or newer (warnings only; tests may still run on Node 20) |
| port 8000 or 5173 already in use | `netstat -ano \| findstr :8000`, then stop that process |
| `UnicodeEncodeError: 'charmap' codec can't encode ...` on Windows | `write_text()` and `read_text()` default to `cp1252` on Windows; explicitly passed `encoding="utf-8"` in `generator/build_application.py`, `generator/load_olist.py`, `backend/config.py`, `scripts/check_gemini.py`, and test files |

---

# PART B: What the project is

**Problem (5A).** Build an assistant that understands a whole no-code business application (navigation hierarchy, pages,
widgets, filters, charts) and lets users **navigate, operate and analyse** it in natural language: intent, actions on UI
state, data retrieval, period comparisons and likely causes, answers shown in context, confirmation for state changes.

**Our answer.** One concrete product, **"Olist Seller Operations"**, a marketplace operations console on the real Olist
e-commerce data, plus an agent that works on it through the same APIs and metadata a person uses.

**Thesis.** *The LLM proposes; deterministic code validates before and verifies after; every number is cited.*

**What the mentor asked for (9 Oct review).** One specific business use case; **metadata for every page and file** so the
agent has context; the demo's assistant showed a visible reasoning trace (context, intent, retrieval over an API
catalogue, plan, tool calls), resolved "this" from page context, **generated a canvas** (chart, table or form) when no
page fit, and **performed write actions** through APIs. We covered all of these (§C, §J.14).

**Official KPIs.** K1 intent-to-destination accuracy; K2 UI-state correctness; K3 multi-step task success, steps and latency;
K4 analytical quality and traceable explanations; K5 retrieval quality at scale; K6 reliability and safety (near-zero action
hallucination, recovery, latency); K7 user experience. Measurement plan: `docs/KPI_PLAN.md`.

**Differentiators.** (1) Validation before every action, so invented pages, filters, values and APIs are caught.
(2) Verification after every action against an independently computed expectation (non-circular: the browser reports what it
rendered). (3) Cited numbers by construction (placeholders filled from evidence). (4) Writes are always previewed,
confirmed, audited and undoable, on a data layer physically separated from the original data. (5) A measured benchmark with
baselines and ablations (to build).

---

# PART C: What has been built

## C.1 Data (three honest layers)
| Layer | Prefix | Content |
|---|---|---|
| original | `raw_` | Olist marketplace (customers, orders, items, payments, reviews, products, sellers, category translation) and the Olist **Marketing Funnel** (8,000 leads, 842 closed deals), loaded 1:1, never modified |
| derived | `core_`, `fact_` | `fact_order_items` / `fact_orders` / `fact_payments` / `fact_reviews`, `core_order_summary` (one row per order with value, freight, sellers, categories, payment, review, days late, days overdue), `core_category_map` (Olist 71 + our 2 translations) |
| synthetic, read-only | `syn_` | monthly sales targets, delivery SLA rules (calibrated on real 2016-17 delivery times), revenue forecasts (linear trend, three scenarios), month-end inventory snapshots |
| synthetic, writable | `ops_` | product listings (price, cost, stock, reorder point), restock orders, support tickets, review replies, promotions, price changes, seller flags, and `ops_action_log` (audit with before/after images, used for undo) |

Every table has a comment starting with its layer; `GET /api/data-dictionary` returns them. Synthetic data is generated by
`generator/synthesize.py` with fixed seed `20261011`; formulas and known limits are in `docs/SYNTHETIC_DATA.md`.
Simulated "today" is **2018-08-31** (the dataset's `as_of_date`). Three roles: `app` (owner, loaders), `app_ro` (read-only, 5 s
timeout, every read), `app_rw` (can write only `ops_*`, cannot delete from the audit log).

## C.2 Semantic layer and metadata
- `generator/semantic_layer.py`: 4 datasets, 16 governed metrics (revenue, freight, items sold, orders, customers, average
  order value, delivery days, late rate, cancel rate, payments, review score ...), closed value sets for dimensions read from
  the database, and `weight_sql` for ratio metrics (enables exact mix/rate decomposition).
- `data/olist/application.json` (`contracts/metadata.py`, schema v0.2): 14 modules (7 operations + 7 reports), **115 pages =
  15 curated operations pages (P-100 to P-700) + 100 generated report pages (P-1001 ...)**, widget codes `R-0001 ...`.
  Pages carry `agent_context` (what "this" means there, what the user typically does next), layout, filters, page actions.
  Widgets are either **metric widgets** (dataset + metric + dimensions) or **API-bound widgets** (`source`: catalogue API,
  fixed params, filter and date mapping, columns, chart spec, row actions). Referential integrity is checked at load time.

Curated operations pages: Dashboard, Orders, Late and overdue deliveries, Support tickets, Customer reviews, Products and
stock, Low-stock alerts, Restock orders, Sellers, Seller flags, Seller acquisition funnel, Sales vs target, Delivery SLA,
Revenue forecast, Promotions. Each has KPI strips and charts (donuts, bars, lines) next to its table; filters drive all of them.

## C.3 Backend (FastAPI, `backend/`)
| Group | Examples | Notes |
|---|---|---|
| Entity search and detail | orders, products, inventory alerts and snapshots, restock orders, sellers, seller flags, reviews, tickets, promotions, funnel channels and deals | filters, sort, paging, `source` block with `data_layers`; short refs (8-char) for orders, products, sellers with "ambiguous" errors listing candidates |
| Summaries | `*/summary` for orders, tickets, reviews, inventory, restock orders, flags, sellers | counts, totals, distributions with `group_by`; feed charts and "how many ... by ..." answers |
| Governed metrics | `/api/metrics` (list), `/query`, `/compare`, `/trend` | compare gives contribution split (additive) or **mix/rate decomposition** (ratios); trend gives slope, peak, trough, MoM, YoY, volatility, gaps |
| Plan vs actual | targets performance, SLA performance, forecast vs actual | simulated plans vs real actuals, clearly labelled |
| Writes | tickets (create, bulk, update), restock orders (create, bulk, update), price change, review reply, seller flag and flag update, promotion create and update | all support `?dry_run=true`; `x-actor: user|agent`; guardrails (price change limit, duplicate checks, status transitions); **exact undo** via `/api/actions/{code}/undo`, refused if rows changed since |
| Catalogue and metadata | `/api/catalog`, `/api/entities`, `/api/data-dictionary`, `/api/application`, `/api/search` | the catalogue is built from the real routes (cannot drift): description, parameters with allowed values, body schema, read/write, data layers, examples |
| Session and runtime | `/api/session` (+ state, context, plan, confirm, undo, events, evidence, canvases, stats, feedback, fault), `/api/widget-data`, `/api/date-presets`, `/api/deeplink/build` and `/parse`, WebSocket `/ws/{sid}` | see below |

**Runtime pieces** (all tested):
- **View engine** (`backend/views.py`): the one definition of what a widget shows for a UI state; used by the browser, the
  verifier, the page-context endpoint and the agent's `read_view`.
- **Sessions** (`backend/sessions.py`): the backend owns `UiState`; `apply_state` goes to the browser, which answers with a
  `render_ack` (row count, series hash, applied filters per widget, computed from what it actually requested and displayed);
  a **simulated browser** answers when none is attached (tests, evaluation) and can inject faults.
- **Verifier** (`backend/verifier.py`): recomputes the expected screen independently and compares route, version, filters, row
  count and hash.
- **Validator** (`backend/validator.py`): checks a `Plan` against metadata and the catalogue: unknown pages, filter ids and
  values (synonyms resolved: "Rio de Janeiro" becomes `RJ`), date presets, metric and dimension names, API ids, parameter types,
  body fields, write limits (at most 5 write steps per plan); returns issues **with suggestions**.
- **Executor** (`backend/executor.py`): runs the normalised plan, emits events, makes evidence (E1, E2 ...), runs writes as
  dry-run, confirmation, apply, log; supports `render_canvas`. UI steps are verified.
- **Retrieval** (`backend/retrieval.py`): BM25 with business-synonym expansion over pages, APIs and entities (the lexical half
  of the agent's hybrid retrieval).
- **Deep links** (`backend/deeplinks.py`, twin in `web/src/lib/url.ts`): `route?from=&to=&preset=&f.<filter>=a,b&sort=field:dir`.
- **Untrusted text**: review comments, subjects and notes are flagged and shortened in evidence and page context.
- **KPI counters**: per session manual vs agent steps, validator stops, verification pass rate, confirmations, undos, latency
  p50/p90, thumbs feedback.

## C.4 Web app (`web/`: React, Vite, TypeScript, Tailwind, Zustand, Recharts)
Rendered entirely from metadata: left navigation (Operations first, then a collapsible Reports library with search by name or
code), filter bar (multiselect with search, selects, date presets and custom dates), KPI card and KPI strip, bar, line and
donut charts, sortable tables with row actions, page layout rows, copy-link, P-/R- codes shown, an API-call counter and a
verification badge in the top bar, undo of the last screen change.
Write actions open a form built from the API's schema, show the **dry-run preview** (what would change, before and after),
confirm, then show a toast with **Undo**. The **assistant panel** has four tabs: Assistant (live reasoning trace, confirm
cards, evidence chips with "Open view" links, thumbs), Plan runner (runs a plan with no LLM through the same validator,
executor and verifier; useful for demos and tests), Context ("what the assistant can see right now") and Metrics (live KPI
figures). Generated canvases (chart, table, form) appear at the top of the page. URL, WebSocket, hash and request rules are
the exact twins of the backend (tested against shared vectors).

## C.5 Shared semantics (do not change without updating both sides)
- **S1** `navigate` resets filters, date range and sort to the page defaults unless `keep_state` is set.
- **S5** a filter applies to a metric widget iff its field is in the widget's dataset; **S10** to an API widget iff its
  `filter_id` is in `source.filter_params`. Filters that do not apply are not reported as applied.
- **S9** contribution analysis only for additive metrics; ratio metrics use the mix/rate decomposition.
- Writes: dry-run, then confirmation, then apply, then audit log; undo restores the exact before-image.
- Series hash: language-neutral serialisation with micro-unit rounding (`floor(|x| * 1e6 + 0.5)`), sorted rows, SHA-256;
  metric widgets hash dimensions then metrics, API widgets hash every declared non-list column.
- Relative dates resolve against `as_of_date`, never today's date (`contracts/dates.py`).

---

# PART D: Repository structure

```
apppilot/
  AGENTS.md                      rules for any coding agent (read first)         README.md  docker-compose.yml  .env.example
  requirements.txt  requirements-ml.txt (heavy, later)  pytest.ini
  contracts/   metadata.py (v0.2)  ui_state.py  actions.py  query.py  events.py  canvas.py  interfaces.py  dates.py
               hash_vectors.json  deeplink_vectors.json  ts/ (JS hash twin and test)
  sql/         00_roles.sql (docker init)  05_raw_schema  06_category_fixes  10_facts  20_synthetic_schema  30_roles_and_grants
  generator/   load_olist.py  semantic_layer.py  synthesize.py  build_application.py  ops_pages.py  ops_visuals.py
  data/olist/  application.json (committed)   *.csv (NOT committed)      data/olist_funnel/ (CSVs, NOT committed)
  backend/     main.py  config.py  db.py  common.py  catalog.py  writes.py  views.py  sessions.py  verifier.py
               validator.py  executor.py  retrieval.py  deeplinks.py
               routes/  orders products sellers reviews tickets promotions metrics plan funnel summaries system session
  web/         src/ app/ nav/ pages/ widgets/ actions/ agent/ state/ net/ lib/ __tests__/     (package.json, vite.config.ts)
  agent/       (TO BUILD)  llm.py retrieval.py tools.py context.py planner.py agent.py narrator.py baselines.py
  eval/        (TO BUILD)  tasks generator, harness, scoring, reports
  tests/       test_contracts  test_dates  test_olist_db  test_semantic_layer  test_application  test_ops_pages
               test_backend_api  test_session_flow  test_analytics_links  test_summaries  (test_mock_server: legacy)
  scripts/     check_env.py  check_gemini.py  ws_smoke.py  activate_env.sh (Linux only)
  docs/        PROJECT_HANDOFF.md (this)  BACKEND.md  SYNTHETIC_DATA.md  KPI_PLAN.md  DATA_NOTES.md  + superseded plans
  mock/        retired mock backend (kept for its legacy test)
  examples/    sample tenant metadata
```
Documents that are **superseded** by this file: `MASTER_BUILD_PLAN.md`, `PRANEEL.md`, `SHASHANK.md` (still useful for adapter and
retrieval notes), `SHREENIKETH.md`, `ADITYA.md`, `TEAM_SPLIT.md`, `TASKS_FOR_LOCAL_AGENT.md`, `CLI_PROMPT.md`,
`LOCAL_SETUP_AND_HANDOFF.md`, `PROGRESS.md`, `MENTOR_REVIEW_PREP.md`.
Research material outside the repo: the 16 papers and `READING_NOTES.md` (`papers/`), and the 26-section plan
`5A_Context_Aware_Application_Agent_PreImplementation_Plan.md`.

---

# PART E: Verification state

- Backend: **144 tests pass** (`python -m pytest -q`): contracts, dates, data integrity, semantic layer, metadata, ops-page
  conformance with the live catalogue, API numbers against independent SQL, writes and undo, role permissions, session flow,
  validator, executor, verifier with injected faults, deep links, trend and decomposition reconciliation, summaries, KPI counters.
- Web: **30 tests pass** (`npm test`: hash vectors, deep-link vectors, request rules) and `npx tsc -b` is clean.
- Not yet run or seen: `python scripts/ws_smoke.py` (real WebSocket, honest and tampered browser); the three browser checks in §H.0.

---

# PART F: Decisions already made (so they are not reopened)

1. One business use case on real data (Olist), with simulated operational layers that are clearly labelled; not a synthetic telecom app.
2. Metadata first: the web app is a renderer of metadata; the agent acts on exactly what the user sees.
3. The backend is authoritative for UI state; the browser acknowledges what it rendered (non-circular verification).
4. All data changes go through catalogue write APIs with dry-run, confirmation, audit log and undo, only on `ops_*` tables.
5. Gemini through `google-genai` behind a provider-agnostic adapter; the model name lives in config.
6. One structured LLM call for intent and plan; the LLM never writes numbers (placeholders), never computes dates (presets), never
   writes SQL (governed metrics only).
7. Evaluation by **state-based scoring** with gold values computed by independent SQL; baselines and ablations are config switches.
8. No dependence on the teammates' earlier frontend or mock: the web app and backend in this repository are the product.

---

# PART G: Known limits (say them before a judge does)

- Targets, SLA, forecasts, inventory, tickets, replies, promotions and flags are **simulated**; findings about them describe the
  simulation. Real data covers 2016-09 to 2018-08 (September and October 2018 hold 20 stray orders).
- Forecasts use a deliberately simple linear-trend model (February 2018 is over-forecast by about 29%, a real backtest error).
- Targets for tiny states (RR, AP, AM) rest on a handful of orders.
- The Olist licence sentence is **not yet recorded** (`docs/DATA_NOTES.md`); copy it from the Kaggle page before showing the data publicly.
- `explain_change` on ratio metrics is a mix/rate split, not causal; "likely causes" always means "largest contributors".
- Gemini free-tier rate limits (reported: about 10 requests/min and 250/day for 2.5 Flash, higher for Flash-Lite; **verify in AI Studio**)
  cap how much evaluation can run; plan for disk caching and Flash-Lite for bulk runs.
- The demo PC may not run Docker Desktop; use the native PostgreSQL option (§A.3).

---

# PART H: What remains (ordered)

## H.0 Close the loop on what is built (about 30 minutes, do first)
1. Start the database, backend and web app (§A.2 steps 6 and 8).
2. `python scripts/ws_smoke.py` prints PASS for the honest browser, the extra-row browser and the dropped-filter browser.
3. In the browser: **Assistant, Plan runner, "Late orders in Rio, last month"**: the trace ends with "screen verified (browser)" and the top bar shows the green badge.
4. **Orders, Open ticket, Preview, Confirm, Undo** works end to end.
5. **Plan runner, "Revenue by state + chart"** shows a generated chart; look over the donuts, bar labels and KPI strips on the Dashboard, Orders and Tickets pages and fix styling.
6. Record the Olist licence sentence in `docs/DATA_NOTES.md`.

## H.1 Small backend additions the agent needs
- Expose `next_evidence_id` in `GET /api/session/{sid}/context`, and let `render_canvas` accept `evidence_id: "@last"`, so a plan can
  chart the result of an earlier step in the same plan without guessing ids.
- Attach the agent at startup (`routes/session.py::set_agent`) when `GEMINI_API_KEY` is configured; keep `/plan` working without it.
- Optional: an `/api/session/{sid}/answer` event type handled by the UI (the assistant tab already renders `answer`, `confirm_request`, `evidence`, `canvas`).

## H.2 The agent (`agent/`): build in this order, details in §J
| # | Piece | Done when |
|---|---|---|
| A1 | `llm.py`: Gemini adapter, structured output, disk cache, rate limiter, usage log | `check_gemini.py` passes; second identical call is served from cache |
| A2 | `tools.py`: tool schemas generated from metadata (enums for pages, filters, values, metrics, APIs) | schema size is bounded; a plan built from it always validates |
| A3 | `retrieval.py`: dense embeddings plus the existing BM25, reciprocal rank fusion, metadata graph expansion | K5 table: P@1/3/5, R@5, MRR for vector, BM25, hybrid, hybrid plus graph |
| A4 | `context.py`: page context, last turns, resolved references, last evidence | follow-ups like "same for SP" and "these orders" resolve |
| A5 | `planner.py`: one call returns intent and plan, with refusal and clarification | on a dev set, valid plan rate and destination accuracy reported |
| A6 | `agent.py`: loop with validation retries (max 2), bounded replan (max 2), recovery, confirmations; streams `AgentEvent`s | the four mentor-style scenarios (§L) run end to end |
| A7 | `narrator.py`: templates for navigation, placeholder prose for analytics, number check | no digit in an answer that is not from evidence |
| A8 | `baselines.py`: B0 LLM only, B1 prompt stuffing, B2 retrieval plus tools without gates | same model and prompt effort as the full system |

## H.3 Evaluation (`eval/`), details in §K and `docs/KPI_PLAN.md`
Gold-task generator (about 150 tasks) with expected state and values from independent SQL; harness that runs a task on a fresh
session and scores K1 to K7; ablations (validator off, verifier off, graph off, citation check off, other model); retrieval labels;
bootstrap confidence intervals and pass^k; a results table for the slides.

## H.4 Demo and submission (11 Oct)
Scripted demo (§L), fallback to the Plan runner if the LLM misbehaves, slides (`5A_Simple_Explainer_and_Slide_Content.md` has the
earlier text), a one-page limits list, and a dry run on the demo PC.

---

# PART J: Agent design

## J.1 Principles
1. **Propose, validate, execute, verify, narrate.** The model only proposes a typed plan; every reference is validated against
   metadata and the live catalogue before anything runs; every screen change is verified after; every number comes from evidence.
2. **One LLM call to understand and plan.** No separate intent classifier (latency is a KPI). A second call only for analytic
   narration or a bounded replan.
3. **Ground, never generate, identifiers.** The model chooses among candidates supplied by retrieval; it never invents page ids,
   filter ids, values, URLs, SQL or dates.
4. **Prefer refusing or asking to guessing.** Ambiguity gets a question with two or three options; missing data gets an honest "not available".
5. **Data is not instructions.** Tool results and free text sit in a delimited block marked untrusted.

## J.2 Architecture
```
user message
   |
   v
[Context builder] <- GET /api/session/{sid}/context   (page, filters, widget rows, actions, recent writes)
   |               <- last turns, resolved references, last evidence
   v
[Hybrid retrieval] -> top-8 pages, top-6 APIs (+ their schemas)    (dense + BM25 -> RRF -> graph expansion)
   |
   v
[Planner: ONE structured LLM call] -> Plan {intent, steps[tool,args,expect], clarifying_question, allow_replan}
   |
   v
[Validator] --issues + suggestions--> back to Planner (max 2 retries) --> else ask the user
   |
   v
[Executor]  navigate / set_filter / ... (verified against the browser's render_ack)
            read_view / run_metric_query / compare_periods / explain_change / analyze_trend / call_api
            write_api (dry-run -> CONFIRMATION -> apply -> undo available)      render_canvas
   |            events stream to the UI as they happen
   v
[Evidence store] E1, E2, ...   --(analytic read steps and allow_replan)--> Planner again (max 2 replans)
   |
   v
[Narrator] templates for navigation; placeholder prose for analytics -> number check -> answer + citations + deep links
```

## J.3 Components and files (all in `agent/`)
| File | Responsibility |
|---|---|
| `llm.py` | provider-agnostic `generate(schema, messages)`; Gemini via `google-genai`; Pydantic `response_schema`; retry once with the parse error; disk cache keyed by hash of (model, messages, schema); token bucket per model; per-call log (model, tokens, latency); a `FakeLLM` for tests |
| `tools.py` | `tool_schemas(app, catalog, page)` producing the JSON schema of every tool with enums from metadata |
| `retrieval.py` | cards for pages, widgets, filters and APIs; BM25 (reuse `backend/retrieval.py`), embeddings (`sentence-transformers`, `BAAI/bge-small-en-v1.5`, verify) in FAISS, RRF with k=60, `networkx` graph expansion (directory, page, widget, dataset, metric, API) |
| `context.py` | builds the prompt context, keeps last ~6 turns (older summarised), resolves "this", "that state", "these orders" |
| `planner.py` | prompt assembly, the one structured call, repair loop with validator issues |
| `agent.py` | `handle(session_id, message, config) -> AsyncIterator[AgentEvent]`; orchestration, recovery, replan |
| `narrator.py` | answer generation with placeholders and the number check |
| `baselines.py` | B0, B1, B2 |

## J.4 The planner call
**Input blocks, in order:** (1) system rules; (2) tool list with schemas; (3) *page context* (current page and code, `agent_context`,
active filters and dates, widget summaries with the first rows, selected row/widget, available actions); (4) *retrieved
candidates* with allowed filter values, columns and API schemas; (5) recent turns and last evidence digests; (6) 6 to 10 few-shot
examples chosen by similarity from the **dev** tasks only; (7) the user message. Free text from data goes in a block labelled
untrusted.

**Output** (`contracts.actions.Plan`): `intent` (closed set: navigate, set_state, read_view, analyze_trend, compare_periods,
explain_change, multi_step, clarify_needed, out_of_scope, unsafe_action), `steps` (typed tool calls, each with an `expect`
post-condition), `clarifying_question`, `allow_replan`.

**Schema caveat (day-1 check):** Gemini's structured output accepts only a subset of JSON schema, and `args` is a free-form
object. The adapter therefore sends a flattened schema (each step has `tool` as an enum plus `args_json` and `expect_json` as
strings), then parses and validates into `Plan`. `scripts/check_gemini.py` tests both the key and this compatibility. If the
flattening needs a contract change, version `contracts/actions.py` additively.

**Prompt rules:** use only ids that appear in the candidates; relative time as a **preset** string (`last_month`, `quarter:2018Q2`,
`year:2017`, `between:...`), never computed dates; no SQL; no URLs except via `open_deep_link` with a candidate route; a write needs
`write_api` with every required body field known, otherwise ask; at most 5 write steps, otherwise use a bulk API; if no page or
API fits, `out_of_scope`; deletion or anything that is not in the catalogue is `unsafe_action`; if the user's request depends on
"this" and nothing is selected, ask.

## J.5 Tools (15)
| Tool | Effect | Policy |
|---|---|---|
| `search_pages`, `search_apis` | read | free |
| `navigate`, `open_deep_link` | UI state | verified; confirm optional (`confirm_mode`) |
| `set_filter`, `clear_filter`, `set_date_range`, `set_sort` | UI state | verified; confirm optional |
| `read_view` | read the rows a widget shows | default widget = the page's main table |
| `run_metric_query`, `compare_periods`, `explain_change`, `analyze_trend` | analytics, evidence with supporting-view link | free |
| `call_api` | read any catalogue GET | free; parameters validated against the catalogue |
| `write_api` | change data | **always** dry-run, then user confirmation, then apply; undoable; actor `agent` |
| `render_canvas` | generated chart, table or form | chart and table need an evidence id; form needs a write API (fields come from its schema) |

## J.6 Validation and recovery
The validator returns issues with a code (`invalid_reference`, `invalid_value`, `invalid_op`, `invalid_date`, `disallowed_tool`,
`unsafe`) and a suggestion (nearest valid value). The loop feeds them back to the planner at most twice (`max_plan_retries`),
then asks the user. Execution failures are classified: stale state (re-apply once), invalid value (fuzzy-match), empty result
(say so; offer a wider period), API error (report the API's message), verification mismatch (re-apply once, then state honestly
what the screen shows). The agent never claims a state it did not verify.

## J.7 Bounded replan
After read-only analytic steps, if `plan.allow_replan` and replans < `max_replans` (2), the planner is called again with the
evidence so far (for example: first find the biggest contributing category, then filter to it and compare again). UI steps that
already verified are not repeated.

## J.8 Clarification, refusal, out of scope, unsafe
Ambiguous reference or missing required field: one question with 2 to 3 concrete options (no tool runs). Not in the application
(for example "show me Amazon returns"): say what the application does cover and offer the nearest pages. Unsafe or unsupported
changes (delete, change original data, anything outside the catalogue): refuse and say why. These cases are part of the gold set.

## J.9 Writes and confirmations
Writes ignore `confirm_mode`: the executor always runs the dry-run, emits `confirm_request` with the preview (before and after of
every affected record), and waits for approval (180 s timeout means declined). After applying it emits the action id; the user can
undo in the UI, or by saying "undo that", which maps to `POST /api/session/{sid}/undo {"what":"write"}`. Bulk operations use the bulk
APIs (for example restock every product in the low-stock alerts) so one confirmation covers the whole change.

## J.10 Narrator and citation enforcement
- **Navigation and filter tasks: no LLM call.** A template states what was opened, with which filters and period, and whether the
  screen verified.
- **Analytic tasks:** the LLM receives only the evidence objects (values, units, sources, data layers) and writes prose with
  placeholders such as `{E2.pct_change}`, `{E1.rows[0].value}`, `{E3.total}`. **Code** fills them with formatted values (units come from
  evidence). If the prose contains a digit that did not come from a placeholder (apart from numbers in the user's message), or a placeholder
  is unmatched, it is rejected and regenerated once, then replaced by a plain template.
- "Likely causes" are the largest contributors from `explain_change` evidence, never a causal claim. Simulated layers are named as
  simulated. Missing data is stated, never estimated.
- Every answer carries `citations` (evidence ids) and `deep_links` (the supporting view with the same filters and period, flagged
  approximate when a filter could not be carried over).

## J.11 Context and memory
Session-scoped: current `UiState`, last ~6 turns (older summarised), resolved references ("that region" is `West`), last evidence.
The selected row or widget in the UI (`UiState.selected_widget`) and the rows in the page context let "this order" or "these
tickets" resolve to concrete ids. Follow-ups ("now compare with last quarter", "same for SP") reuse the last plan's slots.

## J.12 Safety
- Invented references are stopped by the validator (K6 "action hallucination" = plans with invented references caught, and,
  with the validator off, executed wrongly).
- Prompt injection: untrusted fields are flagged and shortened; the planner prompt puts them in a delimited block; even if the
  model were fooled, the validator only permits catalogue APIs, writes need a human confirmation, and the action log records
  `actor = agent`.
- Guardrails already enforced in the APIs: price change within +/-50% per change, no duplicate open tickets or restock orders,
  legal status transitions, promotions cannot start in the past, at most 5 writes per plan.
- Every write is undoable; undo is refused if the data changed afterwards.

## J.13 LLM adapter and budget (Gemini)
Model name from `.env` (`GEMINI_MODEL`), provider chosen by `LLM_PROVIDER`; the same interface can wrap another provider. Disk
cache (`LLM_CACHE_DIR`) makes repeated runs free and evaluation reproducible. A token-bucket limiter with backoff respects the
free-tier limits (verify the real numbers in AI Studio). Budget plan: demo and development on the better model; bulk
evaluation on the lite model, recorded in every result; estimate the call count before an evaluation run (about one planner call
per task, plus narration for analytic tasks).

## J.14 Event stream (what the UI shows)
`understanding` (intent, slots) → `retrieval` (candidates with scores) → `plan` (steps) → `validation` (ok or issues) →
`confirm_request` (summary, preview, auto-confirmed flag) → `action` (running, done, failed) → `verify` (ok, mismatches, source
browser or simulated) → `evidence` → `canvas` → `answer_delta` / `answer` (text, citations, deep links) → `error` → `done` (steps,
replans, elapsed). Events travel over the WebSocket (`agent_event` envelope) and are also available at `GET /api/session/{sid}/events`.
This matches the mentor's trace (context, intent, retrieve, plan, tool, render).

## J.15 Configuration and ablations (`AgentConfig`)
`confirm_mode`, `max_plan_retries=2`, `max_replans=2`, `max_tool_retries=1`, `use_validator`, `use_verifier`, `use_graph_expansion`,
`use_citation_check`. Evaluation turns each off in turn (A1 validator, A2 verifier, A3 graph expansion, A6 citation check), plus a
different model (A9).

## J.16 Baselines
- **B0** LLM only, no metadata: shows what metadata buys.
- **B1** prompt stuffing: all page cards in the prompt, one navigate/set-state call, no validator or verifier.
- **B2** retrieval plus tools, no validator, no verifier, no replan.
Same LLM and the same prompt effort as the full system; prompts and versions are recorded.

## J.17 Latency budget (targets, to be measured)
Navigation with filters: one LLM call plus verification, p50 under about 4 s. Analytics: planner plus executor plus one narration
call, p50 under about 8 s. The Metrics tab and `GET /api/session/{sid}/stats` report the real values.

---

# PART K: Evaluation design

- **Gold tasks** (about 150, generated with a fixed seed from metadata and independent SQL): L1 navigate to the right page,
  L2 plus filters, L3 plus dates and sort, L4 analytics (numbers, comparisons, trends, contributions), L5 multi-step and writes
  (tickets, restocks, promotions), L6 clarification, refusal, out of scope, unsafe, injected instructions in review text.
  Each task has `expected`: page, state, required evidence values, forbidden changes, or `refuse`.
- **Scoring is state-based:** K1 final page equals gold; K2 filters, dates and sort equal gold after normalisation (per element and per
  task); K3 success = required state, required evidence and no forbidden write, plus steps and latency; K4 numeric correctness
  against SQL, faithfulness = share of answer numbers found in cited evidence and share of evidence linked to a view; K5 P@k, R@k,
  MRR on labelled queries also over a scaled application with several hundred generated pages; K6 hallucinated references per plan,
  verification pass rate, recovery rate, latency; K7 agent share of actions, tasks done without manual navigation, thumbs, and a
  small hand versus assistant user study (clicks and time).
- **Statistics:** repeated runs for pass^k, bootstrap confidence intervals, cached LLM calls for reproducibility.
- **Safety scenarios in the benchmark:** invented page, invalid value, unsafe write, duplicate write, injected review text,
  injected browser faults (dropped filter, empty widget, wrong route) caught by the verifier.

---

# PART L: Demo plan (11 Oct) and briefing for a new assistant session

## L.1 Demo scenarios (mirror the mentor's four traces, on our data)
1. **Page context:** on Orders, select a late order, ask "why was this order late?"; the agent reads the order, compares with the
   state's SLA and answers with cited numbers and a link to the supporting view.
2. **Generated canvas:** "compare my last 6 months revenue with the same months last year" (no page fits): `compare_periods`
   evidence, a generated grouped chart and table on the canvas.
3. **Write with confirmation and undo:** "restock everything below its reorder point": bulk restock preview, confirmation card
   with before and after, apply, undo.
4. **Form from an API schema:** "create a promotion for the weakest category": generated form from the API's schema, preview, confirm.
5. **Safety:** inject a browser fault in the Plan runner and show "mismatch caught"; ask for something unsafe or non-existent and
   show the refusal; a review containing instructions is treated as data.
6. Show the live Metrics tab and the benchmark table.

## L.2 Briefing to paste into a new session
> You are continuing the AppPilot project (KLE Tech HackFest 2026, problem 5A). Read `AGENTS.md`, then `docs/PROJECT_HANDOFF.md`
> (single source of truth), then `docs/BACKEND.md` and `docs/KPI_PLAN.md`. The backend, data layers and web app are built and tested
> (144 backend tests, 30 web tests). Your task is the LLM agent in `agent/` as designed in Part J, then the evaluation harness
> in `eval/` as designed in Part K, using the existing backend contract (`GET /api/session/{sid}/context`, `GET /api/search`,
> `POST /api/session/{sid}/plan`, `POST /api/session/{sid}/confirm`, evidence and canvas endpoints). Do not hard-code any page, filter,
> value or API; take them from metadata and the catalogue. Never modify original data; writes only through catalogue write APIs.
> Keep tests green, report real command output, never put a key in the repo.

## L.3 Command cheat sheet
```
python -m generator.load_olist && python -m generator.synthesize && python -m generator.build_application
python -m pytest -q                       uvicorn backend.main:app --port 8000
cd web && npm ci && npm run dev           npm test && npx tsc -b
python scripts/ws_smoke.py                python scripts/check_gemini.py
curl localhost:8000/api/catalog           curl localhost:8000/api/data-dictionary
```
