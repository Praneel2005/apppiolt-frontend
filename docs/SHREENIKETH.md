# SHREENIKETH — Backend, Safety Layer and Analytics Lead

**Mission:** own the deterministic spine: sessions and the WebSocket, the **validator**, the **query engine**, the **executor**, the **verifier**, and the analytics. This is what makes the agent safe and trustworthy. Shashank's LLM proposes; your code decides what is allowed and checks the result.

Read first: `00_DECISIONS_AND_PLAN.md` (sections 5, 6, 7, 8). Then all of `contracts/` (especially `interfaces.py`, `query.py`, `ui_state.py`, `dates.py`).

You own: `backend/` and `docker-compose.yml`. Do not edit other folders. Contract changes follow the change rule.

---

## What we want from you (deliverables)

| # | Deliverable | File | Implements |
|---|---|---|---|
| B0 | docker-compose (Postgres 16 + backend), config, `make up` | `docker-compose.yml`, `backend/` | — |
| B1 | Application loader and lookups | `backend/app_store.py` | loads `data/olist/application.json` (fall back to `examples/tenant_a_sample.json`) |
| B2 | Session manager + WebSocket hub | `backend/sessions.py`, `backend/ws.py` | authoritative `UiState`, versions, nonces, ack waiting |
| B3 | Query engine | `backend/query_engine.py` | `QueryEngine` |
| B4 | Validator | `backend/validator.py` | `Validator` |
| B5 | Executor (tools, undo, confirmation) | `backend/executor.py` | `Executor` |
| B6 | Verifier | `backend/verifier.py` | `Verifier` |
| B7 | Analytics | `backend/analytics.py` | `compare_periods`, `explain_change`, trends, `Evidence` |
| B8 | REST + wiring to the agent + tracing | `backend/main.py`, `backend/trace.py` | endpoint table in the decisions file |
| B9 | Fault injection | `backend/faults.py` | `POST /api/debug/fault` |
| B10 | Tests | `backend/tests/` | all of the above |

Order: B0 → B1 → B3 → B2 → B4 → B5 → B6 → B8 → B7 → B9 → B10 (write tests as you go).

---

## B3 — Query engine (~4 h)
**Input:** `QuerySpec` (dataset, metric ids, dimensions, filters, date range, order, limit). **Output:** `QueryResult` (columns = dimensions then metrics, rows, SQL, row_count, `series_hash` via `canonical_series_hash`).
1. Compile from the semantic layer: take each metric's `sql` from metadata, `GROUP BY` the dimensions, `WHERE` from filters and the date range on the dataset's `time_field`, `ORDER BY`, `LIMIT`.
2. **Never string-concatenate user values.** Field names must be in the dataset's field list (whitelist); values go in as bound parameters.
3. Validate the final SQL with `sqlglot` [PUB: `tobymao/sqlglot`]: exactly one statement, `SELECT` only, tables/columns in the allowlist, a row limit.
4. Run on a **read-only DB role** (`default_transaction_read_only`) with a statement timeout (e.g. 5 s).
5. `POST /api/widget-data` uses the **same** engine to serve the browser.
6. **Honest limitation to remember:** the verifier's expectation and the browser's data both come from this compiler, so a compiler bug is invisible to the ack check. Praneel's gold answers (with independent hand SQL) are the safety net; keep this compiler small and well-tested.

## B2 — Sessions and WebSocket (~3 h)
- `POST /api/session` creates a session with an authoritative `UiState` (version 0, default page).
- `WS /ws/{session_id}`: you send `apply_state` and `agent_event` envelopes; you receive `render_ack` and `user_state_change` (models in `contracts/ui_state.py`, `contracts/events.py`).
- `apply(state, cause)`: increment `version` (S6), generate a unique `nonce`, send, and **await the matching ack** (same version and nonce) with a timeout (~5–8 s). Return the ack or a timeout error.
- Keep an **undo stack** of previous states. `POST /api/session/{id}/undo` pops and re-applies.
- `user_state_change` from the browser updates the authoritative state (version still increments).

## B4 — Validator (~4 h)
`validate(app, plan, state) -> ValidationResult` with a normalized plan and typed issues (`contracts/query.py`). Checks per step:
- tool is in the allowed list; `page_id`, `filter_id`, `widget_id`, `metric`, `dimension` exist (`invalid_reference`);
- filter op is in the filter's `allowed_ops`; values are in `allowed_values`, **after synonym resolution** ("western" → "West") (`invalid_value`); on failure fill `suggestion` with the nearest allowed value (edit distance) so the planner can fix it;
- date presets resolve through `contracts/dates.py` against `app.as_of_date` (`invalid_date`); store explicit `from`/`to` plus the preset label (S3);
- sort field is valid for the page (S4);
- `unsafe` for anything mutating or outside the allowlist.
- Apply the shared semantics S1–S5 when simulating the plan to compute the **expected final `UiState`** (the verifier reuses this).
Write many small unit tests: this component is fully deterministic and is where the "near-zero action hallucination" KPI is earned.

## B5 — Executor (~3 h)
Tools (names from `contracts/actions.py`):
- `navigate` (S1, with `keep_state`), `set_filter` (S2), `set_date_range` (S3), `set_sort` (S4), `open_deep_link`: change the state via `apply(...)`, wait for the ack.
- `read_view(widget_id)`: run the widget's query through the engine; return rows as `Evidence(kind="widget_read")`.
- `run_metric_query`, `compare_periods`, `explain_change`: via B3/B7; return `Evidence`.
- `search_pages`: delegates to Shashank's retrieval (or returns candidates passed in).
- Confirmation: if `AgentConfig.confirm_mode` is on, or the policy marks the step as needing it, emit a `confirm_request` and wait for `POST /api/session/{id}/confirm`. Default policy: reversible UI-state steps auto-run with undo; anything mutating is **disabled**. (We implement both readings of the rulebook's "confirmation for state changes" through the toggle.)
- Return `StepResult` with an `error_code` from the taxonomy.

## B6 — Verifier (~2 h)
`verify(app, expected_state, ack) -> VerifyResult`:
1. Route in the ack equals the expected route.
2. For each widget on the page: expected `applied_filters` = the page filters that apply to that widget under rule S5, with the expected values; compare to `ack.applied_filters` (and date range, sort).
3. Independently run the widget's expected `QuerySpec` through the **query engine** and compare `row_count` and `series_hash` with the ack. Mismatch → a `mismatches` entry `{widget_id, field, expected, actual}`.
This is **not** a re-read of the store your executor wrote. It compares what the browser really did with what the engine says it should have shown.

## B7 — Analytics (~4 h)
Deterministic functions that return `Evidence` (with the validated SQL, source, and machine-readable values):
- `compare_periods(metric, period_a, period_b, dims)`: current vs previous (use `previous_period` from `dates.py`), absolute and % change, per-dimension if requested.
- Trend: monthly series, change over the window, direction.
- `explain_change(metric, period_a, period_b)`: for **additive** metrics only (S9), decompose the change by each candidate dimension and rank members by **contribution to the total change** (member delta ÷ total delta), showing the top contributors per dimension and the dimension with the most concentrated change. Phrase results as "largest contributors", never as causes. For non-additive metrics return per-dimension deltas without contribution percentages.
- Handle empty results and zero baselines explicitly (return a typed "no data" evidence so the agent says so).
Every number must be traceable to the SQL that produced it.

## B8 — Wiring and tracing (~2 h)
- Implement the endpoint table in the decisions file §7.
- `POST /api/session/{id}/message` starts `agent.handle(...)` as a background task and streams each `AgentEvent` over the WebSocket as an `AgentEventEnvelope`. Shashank implements `agent.handle` (signature in `contracts/interfaces.py`); until then plug in a **scripted agent** that runs a fixed plan, so Aditya can build the UI.
- Write a JSONL **trace** per run (every event, tool call, SQL, ack, timings). Praneel's evaluation and our debugging depend on it. Expose `GET /api/session/{id}/trace`.

## B9 — Fault injection (~1 h)
`POST /api/debug/fault {kind}` with kinds like `drop_filter` (widget-data ignores one filter), `empty_widget`, `stale_render`, `slow_widget`, `rename_page` (metadata changes mid-session). Used by the demo (to show the verifier catching a wrong render) and by evaluation (error-recovery KPI). Dev-only.

---

## What to read and explore (time-boxed)
| Item | Time | What to extract |
|---|---|---|
| `contracts/` end to end | 45 min | Every type you will produce/consume |
| BIRD paper (NeurIPS 2023) | 20 min | Why metric definitions matter in text-to-SQL; don't let the model write free SQL |
| CRITIC (arXiv 2305.11738) | 20 min | Verification must use an **external** signal; that is your verifier |
| ToolSafe (arXiv 2601.10156) and the "Closed-World Resolution" paper (arXiv 2609.19425), abstracts | 20 min | Related guardrail work; we apply the same idea to app metadata and must say so |
| τ-bench paper and `sierra-research/tau-bench` repo | 30 min | State-based scoring and how tool errors are typed |
| Docs: FastAPI WebSockets, psycopg 3, sqlglot (`tobymao/sqlglot`) | 20 min each | APIs |
| Postgres: read-only roles, `statement_timeout` | 15 min | Safe execution |

## Interfaces
- **You consume:** `application.json` and the Postgres data (Praneel); `Agent.handle` (Shashank); `render_ack` (Aditya).
- **You provide:** the endpoints and WebSocket; implementations of `Validator`, `QueryEngine`, `Executor`, `Verifier`; analytics evidence.
- Post your endpoint list with example payloads in the chat as soon as the skeleton runs, so Aditya can build against it.

## Done when
- Checkpoint **I1**: backend serves `GET /api/application`, pushes an `apply_state`, receives an ack, and verifies it.
- Checkpoint **I2**: a hand-written plan (no LLM) passes validator → executor → verifier; a fault-injected wrong ack is detected with the right `mismatches`.
- Query engine rejects non-SELECT, unknown columns and oversized queries; unit tests cover it.
- Analytics results match Praneel's gold answers on the dev analytic tasks.

## Pitfalls
- String-building SQL from user text.
- Using the store as the verifier's source of truth (circular).
- Forgetting that dates resolve against `as_of_date`, not today.
- Decomposing non-additive metrics.
- Letting a missing ack hang a session (always time out).
- Different filter semantics between your verifier and Aditya's renderer (S5).

## Messages you owe others
- To Aditya: the endpoint list and sample payloads; the mock/scripted agent.
- To Shashank: confirmation that your `Validator`/`Executor` match the interfaces, plus real `ValidationIssue` examples to feed his retry prompt.
- To Praneel: the semantic-layer compiler entry point so he can compute gold answers.
