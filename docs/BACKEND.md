# Backend reference

Run: `uvicorn backend.main:app --port 8000` (docs: `/docs`). Rebuild data: see `docs/SYNTHETIC_DATA.md`.
Tests: `python -m pytest -q` (needs the database loaded and `python -m generator.synthesize` run).

## Layers (bottom to top)

| Layer | Files | Job |
|---|---|---|
| Data | `sql/`, `generator/` | original / derived / synthetic tables, roles `app_ro` (read) and `app_rw` (writes `ops_*` only) |
| Semantic layer | `generator/semantic_layer.py` | governed metrics (SQL defined once), dimensions with closed value sets |
| Metadata | `contracts/metadata.py`, `generator/build_application.py`, `generator/ops_pages.py` | `data/olist/application.json`: pages (P-codes), widgets (R-codes), filters, actions, `agent_context` |
| Domain APIs | `backend/routes/*.py` | 41 business APIs, each registered with a catalogue entry (`backend/catalog.py`) |
| Writes | `backend/writes.py` | `dry_run` preview, audit log `ops_action_log`, exact undo, guardrails |
| View engine | `backend/views.py` | (widget, UI state) -> rows; the single definition of what a widget shows |
| Session | `backend/sessions.py`, `routes/session.py` | authoritative `UiState`, WebSocket `apply_state`/`render_ack`, history/undo, events, confirmations |
| Verifier | `backend/verifier.py` | expected ack (from the view engine) vs the browser's ack: route, filters, row count, series hash |
| Validator | `backend/validator.py` | checks a `Plan` against metadata + catalogue; resolves synonyms/presets; suggestions on error |
| Executor | `backend/executor.py` | runs a validated plan: UI steps (verified), reads, analysis, writes (always confirmed), canvas |
| Retrieval | `backend/retrieval.py` | BM25 + synonym expansion over pages, APIs, entities (`GET /api/search`) |

## Summary APIs, trends and decompositions

- `*/summary` APIs (orders, tickets, reviews, inventory, restock orders, seller flags, sellers) return counts, totals and
  distributions with the same filters as the entity's search API plus `group_by`; they feed the KPI strips and charts of the
  operations pages and answer "how many ... by ..." questions.
- `POST /api/metrics/trend`: direction, slope, peak/trough, MoM, YoY, volatility and gaps, computed in code.
- `POST /api/metrics/compare`: period comparison; for additive metrics a contribution split, for ratio metrics
  (late rate, review score, AOV, delivery days) an exact mix/rate decomposition (`ratio_decomposition`).
- Deep links: `backend/deeplinks.py` (twin: `web/src/lib/url.ts`, vectors in `contracts/deeplink_vectors.json`).
- Per-session measurements for the KPIs: `GET /api/session/{sid}/stats`, `POST /api/session/{sid}/feedback`
  (see `docs/KPI_PLAN.md`).

## The agent contract (what the LLM agent will call)

1. `GET /api/session/{sid}/context` - what the user sees (page, filters, widget rows, available actions).
2. `GET /api/search?q=` - candidate pages / APIs.
3. `POST /api/session/{sid}/plan {plan, config}` - validate then execute; events stream over the WebSocket / `GET .../events`.
4. `POST /api/session/{sid}/confirm {action_id, approve}` - answer a `confirm_request` (writes always ask).
5. Evidence from `GET .../evidence/{id}`; canvases from `GET .../canvases`.

Tools in a `Plan` (`contracts/actions.py`): `search_pages, search_apis, navigate, set_filter, clear_filter,
set_date_range, set_sort, read_view, run_metric_query, compare_periods, explain_change, open_deep_link,
call_api, write_api, render_canvas`.

## Shared semantics

- S1 `navigate` resets filters / date / sort to the page defaults unless `keep_state` (carries compatible filters and date).
- S5 a filter applies to a metric widget iff its field is in the widget's dataset; S10 to an API widget iff its
  `filter_id` is in `source.filter_params`. Non-applying filters are not reported as applied.
- S9 `explain_change` only for additive metrics.
- Writes: always dry-run -> confirmation -> apply -> audit log; undo refuses if the rows changed since.
- Series hash (`contracts/ui_state.py`): metric widgets hash dimensions then metrics; API widgets hash every declared
  non-`list` column. The server never sends its hash to the browser.
- Without a browser attached, a simulated browser answers (`verify.source = "simulated_browser"`); faults can be
  injected (`POST /api/session/{sid}/fault`: drop_filter, empty_widget, wrong_route) to prove the verifier catches them.

## Errors

`{"error": {"code", "message", ...}}`. Common codes: `invalid_value` (+`did_you_mean`, `allowed`), `invalid_request`,
`ambiguous_reference` (+`candidates`), `*_not_found`, `restock_already_open`, `price_change_too_large`,
`undo_conflict`, `already_undone`, `agent_not_configured`.
