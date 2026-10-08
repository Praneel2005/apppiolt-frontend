# MASTER BUILD PLAN — one implementer, all components

You (the local agent) build everything in this order. Cards **T0–T8** have full detail in `docs/TASKS_FOR_LOCAL_AGENT.md`. For **T9 onward** the card below gives the files, the key spec decisions and the acceptance checks, and points to the component spec (`PRANEEL.md`, `SHASHANK.md`, `SHREENIKETH.md`, `ADITYA.md`) for the long description. Rules: `AGENTS.md`. Shared semantics S1–S9 and the endpoint table: `docs/00_DECISIONS_AND_PLAN.md`.

**Why this order:** it reaches a visible, end-to-end slice (the app moves when told to, and the browser acknowledges it) as early as possible, then adds the safety layer, then the AI, then evaluation. Every phase ends in a **GATE** where you stop, report, and the planner reviews.

```
Phase A  data + metadata + eval basics     T0–T8      GATE G0 (after T1), GATE G1 (after T8)
Phase B  backend core                      T9–T11
Phase C  frontend + first round trip       T12–T14    GATE G2  (I1: browser ack == server expectation)
Phase D  safety spine (no LLM yet)         T15–T17    GATE G3  (I2: scripted plan verified; injected fault caught)
Phase E  agent                             T18–T21    GATE G4  (I3/I4: LLM end-to-end incl. cited analytics)
Phase F  scale, UI, evaluation             T22–T26    GATE G5  (results table)
Phase G  demo readiness                    T27        GATE G6
```

---

## Phase A — data, metadata, evaluation basics (cards in `TASKS_FOR_LOCAL_AGENT.md`)
| Card | Title | Gate |
|---|---|---|
| T0 | Environment and baseline checks (incl. JS hash test, Gemini check) | |
| T1 | Initialise git, commit baseline | **G0** |
| T2 | Get and inspect Olist; license; as_of_date proposal | |
| T3 | Load raw tables | |
| T4 | Fact tables | |
| T5 | Semantic layer | |
| T6 | Application generator v1 (≥ 15 pages) | |
| T7 | State simulator + task generator v1 (L1–L3) | |
| T8 | Evaluation metrics module | **G1** |

**G0 rule:** if `node --test contracts/ts/` fails, stop. Do not continue until the planner has reviewed the failing vector.
**G1 report must include:** license sentence, real row counts, total revenue, the as_of_date, number of pages and tasks.

---

## Phase B — backend core
Component specs: `docs/SHREENIKETH.md` (B1, B2, B3).

### T9 — Backend skeleton
**Files:** `backend/__init__.py`, `backend/main.py`, `backend/app_store.py`, `backend/config.py`, `tests/test_backend_basic.py`.
**Spec:** FastAPI app. `app_store` loads `data/olist/application.json` (falls back to `examples/tenant_a_sample.json` if missing) into `contracts.metadata.Application`. Endpoints: `GET /health`, `GET /api/application`, `POST /api/session` → `{session_id}`. Settings from `.env`.
**Acceptance:** `pytest tests/test_backend_basic.py` using FastAPI `TestClient`; `uvicorn backend.main:app --port 8000` serves `/api/application` (show the first 200 chars).

### T10 — Query engine and widget data
**Files:** `backend/query_engine.py`, `tests/test_query_engine.py`; endpoint `POST /api/widget-data` in `backend/main.py`.
**Spec (from SHREENIKETH.md B3):**
- `QueryEngine.run(spec: QuerySpec) -> QueryResult` compiles metric SQL from the dataset metadata; `GROUP BY` dimensions; filters and the date range as **bound parameters**; field names whitelisted against the dataset; `ORDER BY` only on selected columns; `LIMIT`.
- Validate the final SQL with `sqlglot`: a single `SELECT`, tables/columns in the allowlist.
- Connect with `DATABASE_URL_RO` (read-only role, 5 s timeout).
- Convert `Decimal` to `float` and dates to ISO strings before returning; `series_hash` via `contracts.ui_state.canonical_series_hash` with columns = dimensions then metrics (S7).
- `POST /api/widget-data {widget_id, filters, date_range, sort}` → `{columns, rows, row_count}`. A page filter applies to a widget iff the filter field is in the widget's dataset (S5).
**Acceptance (tests skip if DB unreachable):** every widget in `application.json` returns rows; revenue by region equals an independent hand-written SQL; a malicious value (`'; DROP TABLE ...`) is bound as data and returns zero rows; a non-SELECT or unknown column is rejected; the hash of identical rows is identical.

### T11 — Sessions and the WebSocket hub
**Files:** `backend/sessions.py`, `backend/ws.py`, `tests/test_ws_roundtrip.py`.
**Spec (SHREENIKETH.md B2):** per-session authoritative `UiState`; `apply(state, cause)` increments `version` (S6), generates a `nonce`, sends `ApplyState` on `WS /ws/{session_id}`, awaits the matching `RenderAck` with a timeout (default 6 s), returns the ack or a typed timeout error; handles `UserStateChange`; keeps an undo stack. Add a **dev-only** endpoint `POST /api/debug/apply_state {session_id, state}` that calls `apply` and returns `{ack | error}` (used by the e2e tests and the demo; guard it with `DEBUG=1`).
**Acceptance:** a test opens the WebSocket with Starlette's test client acting as a fake browser: it receives `apply_state`, replies with a `render_ack`, and the REST call returns the ack; a second test never replies and gets the timeout error without hanging.

---

## Phase C — frontend and the first round trip
Component spec: `docs/ADITYA.md`.

### T12 — Frontend scaffold and the TypeScript hash
**Files:** `frontend/` (Vite React TS: `npm create vite@latest frontend -- --template react-ts`; add Tailwind, Zustand, Recharts, `@tanstack/react-table`, `react-router-dom`, `vitest`, `@playwright/test`), `frontend/src/hash.ts`, `frontend/src/contracts.ts` (TS mirrors of `UiState`, `ApplyState`, `RenderAck`, `WidgetAck`, `AgentEvent`, `Application`), `frontend/src/hash.test.ts`.
**Spec:** port `contracts/ts/hash.mjs` to TypeScript using `globalThis.crypto.subtle`; Vite dev server proxies `/api` and `/ws` to `localhost:8000`.
**Acceptance:** `npm run test` passes **every case in `contracts/hash_vectors.json`** (import the JSON); `npm run build` succeeds.

### T13 — Metadata-driven renderer
**Files:** `frontend/src/` app shell, `NavTree`, `PageView`, `FilterBar`, widgets (`BarChartWidget`, `LineChartWidget`, `GridWidget`, `KpiWidget`), `filterRule.ts`, `filterRule.test.ts`.
**Spec (ADITYA.md A3–A5):** load `GET /api/application`; nav tree from `directories`/`pages`; routes from `Page.route`; each widget calls `POST /api/widget-data` with the filters that apply to it under rule S5 (implemented once in `filterRule.ts` and unit-tested); render exactly the rows returned. No hard-coded pages.
**Acceptance:** `npm run test`; `npm run dev` shows the navigation tree and at least three different pages from `application.json` rendering real data (describe what you see and include the page ids).

### T14 — WebSocket client, render acknowledgement, first Playwright test
**Files:** `frontend/src/ws.ts`, `frontend/src/useSession.ts`, `frontend/e2e/roundtrip.spec.ts`, `playwright.config.ts`, a script `scripts/dev_up.py` (starts db, backend, frontend).
**Spec (ADITYA.md A6):** create a session, open `/ws/{id}`, on `apply_state` update store and router, wait until **all widgets have settled**, then send `render_ack` with `applied_filters`/date/sort copied **from the request each widget actually sent** (not from the store), `row_count` of the rows displayed, `series_hash` of the displayed rows; if a widget has not settled in 5 s send the ack with `error` set.
**Acceptance (this is GATE G2, "I1"):** the Playwright test starts the stack, creates a session, calls `POST /api/debug/apply_state` with a state (page, one filter, a date range), and asserts that the returned ack's `series_hash` **equals** the hash computed independently by the backend query engine for the same state, and that the visible filter controls show the state.
**GATE G2 report:** the Playwright output, a screenshot path, and the hash values compared.

---

## Phase D — the safety spine (no LLM yet)
Component spec: `docs/SHREENIKETH.md` (B4–B6, B9).

### T15 — Validator
**Files:** `backend/validator.py`, `tests/test_validator.py`.
**Spec (B4):** implements `contracts.interfaces.Validator`: tool allowlist; ids exist; filter op allowed; value in `allowed_values` after **synonym resolution**, otherwise a `suggestion` by edit distance; date presets via `contracts.dates.resolve` against `app.as_of_date` stored as explicit `from`/`to` plus the preset; sort field valid (S4); returns a **normalized plan**. Reuse `eval/state_sim.apply_step` (T7) for the expected final `UiState` so the validator and the gold labels cannot disagree.
**Acceptance:** ≥ 25 test cases (valid and invalid) pass, covering each `ValidationIssue.code`; synonym "western" → "West" on a page filter; unknown page and unknown value give useful suggestions.

### T16 — Executor, tools, undo and confirm mode
**Files:** `backend/executor.py`, `tests/test_executor.py`.
**Spec (B5):** implements `Executor`: `navigate` (S1, `keep_state`), `set_filter` (S2), `set_date_range` (S3), `set_sort` (S4), `read_view` and `run_metric_query` returning `Evidence`; `confirm_mode` makes state-changing steps wait for `POST /api/session/{id}/confirm`; `POST /api/session/{id}/undo`; typed `StepResult.error_code`.
**Acceptance:** with a fake browser (the T11 test helper) a hand-written plan (navigate + set_filter + set_date_range) runs and every ack is received; confirm mode blocks until approved and a declined step changes nothing; undo restores the previous state.

### T17 — Verifier and fault injection
**Files:** `backend/verifier.py`, `backend/faults.py`, `tests/test_verifier.py`, `frontend/e2e/fault.spec.ts`.
**Spec (B6, B9):** `verify(app, expected, ack)` recomputes each widget's expected `QuerySpec` through the query engine and compares `applied_filters`, `row_count` and `series_hash`; returns `mismatches` as `{widget_id, field, expected, actual}`. Dev-only `POST /api/debug/fault {kind}` with `drop_filter`, `empty_widget`, `slow_widget` affecting `/api/widget-data`.
**Acceptance (GATE G3, "I2"):** (1) unit tests: a correct ack verifies; an ack with a dropped filter, a wrong row count and a wrong hash each produce the right mismatch; (2) e2e: run a hand-written plan end to end with the real browser: verified; then enable `drop_filter` and run again: the verifier reports a mismatch on the right widget.
**GATE G3 report:** both outputs.

---

## Phase E — the agent
Component spec: `docs/SHASHANK.md`.

### T18 — LLM adapter
**Files:** `agent/llm.py`, `agent/fakes.py`, `tests/test_llm_cache.py`.
**Spec (S0, S1):** provider-agnostic interface (`Gemini` first via `google-genai`, a `Fake` for tests, others later); structured output into a Pydantic model; **disk cache** keyed by hash of (model, messages, schema); rate limiter with exponential backoff (respect the free-tier limits in `.env`/config); per-call logging of tokens and latency. **If `scripts/check_gemini.py` showed that `Plan` fails**, first agree the flattened schema with the planner (do not change `contracts/` alone).
**Acceptance:** tests with the Fake and a recorded Gemini response (no network): cache hit avoids a call; backoff retries; a real Gemini call works when a key is present (paste output).

### T19 — Retrieval
**Files:** `agent/retrieval.py`, `agent/eval_retrieval.py`, `tests/test_retrieval.py`.
**Spec (S2):** one card per page/widget/filter; embeddings + BM25 + reciprocal rank fusion; `networkx` graph expansion (switchable by `AgentConfig.use_graph_expansion`); returns top-k candidates with full schemas. Install `requirements-ml.txt` for this card.
**Acceptance:** `eval_retrieval.py` prints P@1/P@3/P@5, R@5 and MRR on the dev tasks for vector-only, BM25-only, hybrid, hybrid + graph (paste the table).

### T20 — Planner and agent loop (L1–L3)
**Files:** `agent/tools.py`, `agent/context.py`, `agent/planner.py`, `agent/agent.py`, `agent/narrator.py` (templated part only), `backend/agent_runner.py`, `tests/test_agent_scripted.py`.
**Spec (S3–S6):** tool schemas generated from metadata; **intent and plan in one LLM call**; validator retry loop (max 2) with `ValidationIssue`s fed back; execute + verify; templated confirmation text for navigation/filter tasks (no LLM call); stream `AgentEvent`s over the WebSocket; `POST /api/session/{id}/message` runs the agent in the background. Few-shot examples come from **dev** tasks only.
**Acceptance:** a headless runner (`eval/run_headless.py`, a fake browser that acks from the query engine) executes the dev L1–L3 tasks and prints: intent accuracy, destination accuracy, state exact match, mean latency, calls per task.

### T21 — Analytics and the narrator (L4–L5)
**Files:** `backend/analytics.py`, `agent/narrator.py` (full), `tests/test_analytics.py`, `tests/test_narrator.py`.
**Spec (B7, S7):** `compare_periods`, trend, `explain_change` (contributions only for additive metrics, S9; "largest contributors", never causal) returning `Evidence` with the SQL; the narrator receives only `Evidence`, writes prose with placeholders like `{e1.delta_pct}`, code fills numbers; any digit not from a placeholder → regenerate once, then fall back to a template; empty/zero-baseline cases produce a typed "no data" evidence.
**Acceptance (GATE G4, "I3/I4"):** the headless runner on dev: (a) L1–L3 state exact match ≥ 70% (a rough bar), (b) 5 hand-picked L4–L5 questions return answers whose numbers equal independent hand-written SQL, (c) an unknown page and a destructive request are refused or clarified. Paste per-level numbers.
**GATE G4 report:** the metrics, three example transcripts (one success, one clarification, one refusal) and measured latency.

---

## Phase F — scale, UI, evaluation
### T22 — Scale to 100–120 pages
**Files:** `generator/build_application.py` (extended), `generator/describe.py`.
**Spec:** more templates and combinations; LLM-drafted descriptions and synonyms (cached, so rerunning costs nothing), then a human-readable review file `data/olist/descriptions_review.md` for the human to skim; descriptions must be distinct and name the question they answer.
**Acceptance:** 100–120 pages, tests from T6 still pass, retrieval metrics (T19) re-run and compared with the 15-page numbers.

### T23 — Tasks L4–L6 and the frozen test split
**Files:** `generator/build_tasks.py` (extended), `tasks/dev.jsonl`, `tasks/test.jsonl`, `tasks/FROZEN.sha256`, `tests/test_tasks_gold.py`.
**Spec (PRANEEL.md P3):** analytical gold answers via the semantic-layer compiler **and** ≥ 30 independent hand-written SQL cross-checks that must agree; L6 (≥ 30 test tasks): ambiguous, out-of-scope, missing data, destructive, unknown value; paraphrases stored separately; dev ≈ 60, test ≈ 140; freeze the test split (write its SHA-256).
**Acceptance:** tests pass; the cross-check agreement is printed; the human is told to **read the whole test split**.

### T24 — Chat UI
**Files:** `frontend/src/chat/*`, `frontend/src/DebugDrawer.tsx`, `frontend/e2e/chat.spec.ts`.
**Spec (ADITYA.md A7–A8):** streaming action log from `agent_event`s; "✓ verified" badge from the `verify` event; answer with citation chips that highlight the source widget; deep links; confirm card; undo button; **confirm-mode toggle**; debug drawer with the state JSON, last ack and a fault-injection control.
**Acceptance:** Playwright: send a message, see the action log fill, see the verified badge and a citation chip; toggling confirm mode shows the confirm card.

### T25 — Evaluation harness, baselines, ablations
**Files:** `eval/run.py`, `agent/baselines.py`, `eval/report.py`, `results/`.
**Spec (PRANEEL.md P5, SHASHANK.md S8):** run the frozen test set through our system and B0/B1/B2 (same LLM, same prompt effort); ablations A1 (no validator), A2 (no verifier), A3 (no graph expansion), A6 (no citation check) through `AgentConfig` flags; 3 runs per task for the main comparison and 1 for ablations (LLM-quota reality); fault-injection run for the recovery KPI; bootstrap CIs and pass^k from `eval/metrics.py`; record the model and prompt versions.
**Acceptance (GATE G5):** `results/table.md` with every KPI for every system and the CIs; an honest limitations paragraph (shared query compiler, one dataset, which model produced the numbers).

### T26 — Superset importer
**Files:** `importers/superset.py`, `tests/test_superset_import.py`, `docs/IMPORTER_NOTES.md`.
**Spec (PRANEEL.md P4):** run Superset in Docker, `superset load_examples`, export a dashboard; **inspect the real YAML first**; map dashboard → Page, chart → Widget, dataset → Dataset/fields/metrics, native filters → FilterDef; `source_format="superset"`; the model's referential integrity must pass.
**Acceptance:** the imported `Application` validates; retrieval and validation run on it; unmappable parts are documented honestly.

---

## Phase G — demo readiness
### T27 — Demo
**Files:** `docs/DEMO_SCRIPT.md`, `scripts/dev_up.py` (one command start), `scripts/warm_cache.py`.
**Spec:** the scripted flow in `5A_Simple_Explainer_and_Slide_Content.md` (Part B, slides 5–9): a navigation + filter task, a cited period comparison, a "why" question, a clarification, a refusal, the forced fault being caught (debug drawer), confirm mode; `warm_cache.py` runs the whole script once so every LLM response is cached (the demo works offline); a **fresh-clone rehearsal**: clone the repo into a new folder, follow only the README, and run the script three times in a row.
**Acceptance (GATE G6):** three consecutive successful runs from a fresh clone; the cached run works with the network off; a backup screen recording exists.
**GATE G6 report:** timings, any flakiness observed, the README steps used.
