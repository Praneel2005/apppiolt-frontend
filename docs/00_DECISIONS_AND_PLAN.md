# AppPilot — Decisions, Shared Semantics and Implementation Plan

Read this first. Then read your personal file: `PRANEEL.md`, `SHASHANK.md`, `ADITYA.md`, `SHREENIKETH.md`.
Background and full research: `../../5A_Context_Aware_Application_Agent_PreImplementation_Plan.md`.

Source tags: **[PUB]** confirmed in a web search or paper on 6–8 Oct 2026 · **[SEC]** secondary source · **[MEM]** from memory, **verify before relying on it** · **[INF]** our decision.

---

## 1. Papers or GitHub repos — which do we build on?

**Neither is a thing to "fork". No public repo implements 5A.** The rule we follow [INF]:

> **Papers decide what we claim and how we measure. Repos supply parts we run.**

| Component | Paper gives us (the "why" and the method) | Repo / library we actually use (the "what") |
|---|---|---|
| Evaluation method | τ-bench: score by comparing **final state** to a goal state; **pass^k** reliability [PUB, arXiv 2406.12045] | Read `sierra-research/tau-bench` (and its tau2 successor) for harness structure; **do not fork**, our domain differs [PUB: repo exists] |
| Why not a raw browser agent | WorkArena Table 2: GPT-4o agent 0.0% on list-filter, 10.0% on list-sort, 42.7% overall [PUB, checked in the paper PDF]; SeeAct 51.1% only with manually grounded plans [PUB, abstract] | `ServiceNow/BrowserGym` is the environment behind WorkArena. **Read, do not run** [PUB: repo exists] |
| Verification with an external signal | CRITIC (ICLR 2024): verify → correct using tool feedback [PUB] | Our own verifier (ack vs expectation) |
| Tool use / retrieval of tools | Gorilla, ToolLLM: retrieve tool/API docs at run time [PUB] | `sentence-transformers`, `faiss-cpu`, `rank_bm25` [MEM] |
| Text-to-SQL | BIRD: external-knowledge evidence lifts GPT-4 from 34.88% to 54.89% [PUB] → give the model metric definitions | `sqlglot` for parsing/validation [PUB: `tobymao/sqlglot` exists] |
| Foreign metadata format | — | Apache Superset: Apache-2.0 [SEC], `superset load_examples` [PUB docs], documented YAML export ZIP [PUB docs] |

**Decision on trust:** when a paper's number is used on a slide, it must be checked in the paper (WorkArena 42.7 and SeeAct 51.1 are now checked). Repos are only used after you open the README and confirm the license.

## 2. The "no-code platform" question

Rulebook: metadata "exported from the no-code platform" [RB]. We looked at the real open-source options:

| Platform | License | What its export gives us | Fit |
|---|---|---|---|
| **Apache Superset** | Apache-2.0 [SEC] | ZIP of YAML: databases, datasets, charts, dashboards linked by UUID; `load_examples` ships sample dashboards [PUB docs] | Clean, documented export. It is a BI tool: dashboards/charts/filters, **no page hierarchy** |
| **Appsmith** | Apache-2.0, ~40k stars [SEC] | JSON with pages and a widget tree [SEC, third-party tool descriptions; **actual schema unverified**] | Closest to "pages + widgets + navigation", but a UI-layout tree, huge, unverified |
| NocoBase | Apache-2.0 [SEC] | not checked | not evaluated |
| ToolJet | AGPL-3.0 [SEC] | — | avoid (copyleft) |
| Budibase | GPLv3 [SEC] | — | avoid (copyleft) |

### Decision [INF]
1. **Our app is our own metadata-driven renderer** (Aditya) over the **canonical schema** (`contracts/metadata.py`). We do not drive the real Appsmith/Superset UI.
   *Why:* (a) the papers above show raw-UI control is the weak path; (b) our verifier needs a controlled **render acknowledgement** channel that a third-party UI doesn't provide; (c) we have days, not weeks.
2. **Foreign-export proof = a Superset importer** (Praneel, stretch task P4) that converts a real Superset export into the canonical `Application`. It proves the agent is not tied to our own format.
   *What it does NOT prove:* operating the real Superset UI. Say so to the mentors. This resolves the weakness "tenant B only shows different content".
3. **Appsmith** stays a reference only. If a mentor says a real no-code export is provided, we write an importer for that format instead (the architecture already supports importers).
4. **Tell the mentors honestly** that our "no-code app" is a metadata-driven renderer, and ask whether that is acceptable (Question 1 in `MENTOR_REVIEW_PREP.md`).

## 3. Dataset decision

Options: public business data vs fully synthetic.

| Option | Pros | Cons |
|---|---|---|
| **Olist Brazilian e-commerce** (Kaggle; ~100k orders 2016–2018, 9 CSV tables [PUB, via search]) | Real data; multi-table; real seasonality, so "why did it change" questions have real answers; gold answers come from SQL | **License not verified** (I recall CC BY-NC-SA, **check the Kaggle page**); Kaggle needs a login; no inventory/finance-ledger tables; ends in 2018 |
| Northwind (Postgres port exists on GitHub [PUB: `pthom/northwind_psql`]) | Tiny, trivial to load | Too small (hundreds of orders) for convincing analytics; license unverified |
| Fully synthetic generator | Total control, can inject anomalies | Looks fake; extra work to make it realistic |

### Decision [INF]: **Olist is the business data.**
- A synthetic generator is **not** needed for tenant A. We keep a *very small* synthetic tenant only for unit tests and fixtures.
- Because Olist is historical, **`as_of_date` = end of the last complete month in the data** (decide after checking monthly counts; the data thins out at the end [MEM]). "Last quarter" always resolves against it (`contracts/dates.py`).
- Modules (differ from the research doc, because Olist has no inventory): **Sales, Customers, Sellers, Logistics, Payments, Reviews, Catalog**.
- If Olist cannot be downloaded tonight, Praneel falls back to Northwind + a small generator for extra fact tables, and tells everyone immediately.

## 4. Final stack [INF]

| Layer | Choice |
|---|---|
| Frontend | React + Vite + TypeScript, Tailwind, Zustand, Recharts, TanStack Table, React Router |
| Backend | Python 3.12, FastAPI, uvicorn, Pydantic v2, psycopg 3 |
| DB | PostgreSQL 16 (docker compose) |
| Retrieval | `sentence-transformers` embeddings + `rank_bm25` + reciprocal rank fusion + `networkx` graph expansion; vector index in memory with FAISS. *(Changed from pgvector for speed and fewer moving parts; the interface stays the same, pgvector can replace it later.)* |
| SQL safety | sqlglot |
| LLM | **Gemini via the free API** (team choice), behind a provider-agnostic `agent/llm.py` (Anthropic and local Ollama as fallbacks). Free-tier limits are tight (see `SHASHANK.md` S1): cache every call, rate-limit, use Flash-Lite for bulk runs |
| Agent orchestration | Plain Python, no LangChain/LangGraph |
| E2E tests | Playwright |
| Deploy | `docker compose up` locally |

## 5. Contracts (already written and tested — `python3 -m pytest` in `apppilot/`)

| File | What |
|---|---|
| `contracts/metadata.py` | Canonical `Application` schema; rejects inconsistent exports |
| `contracts/ui_state.py` | `UiState`, WebSocket messages, `canonical_series_hash` |
| `contracts/hash_vectors.json` | **Frozen test vectors** the TypeScript hash must reproduce |
| `contracts/actions.py` | `Plan`, `ToolCall`, `Evidence`, `AgentConfig` (incl. ablation flags) |
| `contracts/query.py` | `QuerySpec`, `QueryResult`, `ValidationResult`, `VerifyResult` |
| `contracts/events.py` | `AgentEvent` stream to the chat UI |
| `contracts/interfaces.py` | Protocols: `Validator`, `QueryEngine`, `Executor`, `Verifier`, `Agent` |
| `contracts/dates.py` | `resolve(preset, as_of)`, `previous_period(...)` |

**Change rule:** nobody edits `contracts/` alone. Post in the group chat, agree, bump `SCHEMA_VERSION`, run the tests.

## 6. Shared semantics — do not diverge (these decide whether gold labels, validator and UI agree)

| # | Rule |
|---|---|
| S1 | `navigate(page_id)` **resets** state to the page's `default_state`, unless `args.keep_state = true`, which carries over the date range and any filter whose `filter_id` exists on the new page with a valid value. |
| S2 | `set_filter` **replaces** the value of that `filter_id` (it does not add). Values are stored as a list of **canonical** allowed values (synonyms already resolved). |
| S3 | `set_date_range` replaces the date range. Presets are resolved by `contracts/dates.py` against `as_of_date` and stored as explicit `from`/`to` plus the `preset` label. |
| S4 | `set_sort` replaces the sort. The field must be a column of some widget on the page that lists `sort` in `supports`. |
| S5 | A page filter applies to a widget **iff** `filter.field` is a field of the widget's dataset. The `date_range` filter applies through the dataset's `time_field`. Widgets whose dataset lacks the field ignore the filter. |
| S6 | `UiState.version` increments by 1 on every `ApplyState`. The nonce is unique per message. |
| S7 | Widget series columns for hashing = widget `dimensions` then `metrics`, in metadata order. Sorting of rows for the hash is done by the hash function, so display sort does not matter. |
| S8 | Gold final UI state = state after applying all gold steps to `start_state` under S1–S5. |
| S9 | Non-additive metrics (`additive=false`, e.g. average order value, late-delivery rate) are **not** decomposed into contribution percentages; only additive metrics are. |

## 7. Interfaces (who exposes what)

REST (JSON) and one WebSocket. Owner of each endpoint in brackets.

| Endpoint | Purpose |
|---|---|
| `GET /api/application` | Full canonical `Application` JSON [Shreeniketh] |
| `POST /api/session` → `{session_id}` | Create a session [Shreeniketh] |
| `POST /api/widget-data` `{widget_id, filters, date_range, sort}` → `{columns, rows, row_count}` | Data for a widget, via the query engine [Shreeniketh] |
| `POST /api/session/{id}/message` `{text, config?}` → 202 | Start an agent run; events arrive on the WebSocket [Shreeniketh wires, Shashank's agent runs] |
| `POST /api/session/{id}/confirm` `{action_id, approve}` | Answer a confirm card [Shreeniketh] |
| `POST /api/session/{id}/undo` | Revert the last UI action [Shreeniketh] |
| `GET /api/session/{id}/trace` | JSONL trace of the run [Shreeniketh] |
| `POST /api/debug/fault` `{kind}` (dev only) | Inject a fault (e.g. drop a filter in widget-data) [Shreeniketh] |
| `WS /ws/{session_id}` | Carries: `apply_state`, `render_ack`, `user_state_change`, `agent_event` (envelope) |

## 8. Clean implementation steps (the order everyone follows)

Each block ends with an **integration checkpoint**. Do not start the next block's integration work until the checkpoint passes. Hour counts are rough; if a block overruns, say so in the group chat immediately.

| Block | What happens | Owner(s) | Checkpoint (Definition of Done) |
|---|---|---|---|
| **0 — Setup (~1 h)** | Everyone pulls the repo, runs `python3 -m pytest` (31 pass), installs tools (Node 20+ for Aditya, Docker for all). Praneel starts downloading Olist; Shreeniketh starts the compose file | all | Everyone reports "tests pass" in the chat |
| **1 — Foundations in parallel (~4–6 h)** | Praneel: Olist in Postgres, semantic layer, first `application.json` (even 10 pages). Shreeniketh: FastAPI skeleton, Application loader, query engine, WS hub. Aditya: Vite app, renderer shell, WS client, hash port. Shashank: LLM adapter, retrieval over the sample tenant, fakes | all | **I1:** frontend loads `GET /api/application`, draws a page, backend pushes one `apply_state`, browser replies with a `render_ack`, and `canonical_series_hash` matches the Python vectors |
| **2 — Deterministic spine (~4 h)** | Validator + executor + verifier (Shreeniketh); widget-data + real ack (Aditya); first 60 pages (Praneel); planner prompt on fakes (Shashank) | all | **I2:** a **hand-written** plan (no LLM) navigates + sets filters, the browser acks, the verifier says OK; a fault-injected wrong ack is caught |
| **3 — LLM end to end (~4 h)** | Shashank plugs the real planner into the real validator/executor. Praneel delivers 100+ pages and the dev task set | Shashank + Shreeniketh | **I3:** ≥ 70% of dev L1–L3 tasks end in the correct UI state (a rough bar, not the final metric) |
| **4 — Analytics (~4 h)** | `run_metric_query`, `compare_periods`, `explain_change`, evidence, narrator with placeholders (Shreeniketh + Shashank); chat UI with action log, confirm card, citations (Aditya) | Shreeniketh, Shashank, Aditya | **I4:** "compare West last quarter with the previous one" returns a cited answer whose numbers match gold SQL |
| **5 — Evaluation (~4–6 h)** | Task sets frozen; harness; baselines B0–B2; ablations; fault injection; results table | Praneel + Shashank | **I5:** `results/table.md` with KPIs, CIs, baselines |
| **6 — Polish and demo** | UI polish, Superset importer result, rehearsals, backup recording, slides | all | **I6:** the demo script runs three times in a row |

Praneel's Superset importer (P4) runs whenever Block 1–2 work is waiting on others, and must be finished before Block 6.

## 9. Working agreements

- Git: one repo, short-lived branches, merge to `main` at least daily, `main` must always start with `docker compose up` and pass the tests.
- Each person commits only inside their own folder (listed in their file); contract changes follow section 5.
- Daily 10-minute sync: what finished, what blocks, what changes in a contract.
- If you are stuck for more than 45 minutes, ask. Do not stay silent.
- Never hard-code ids, routes, filter values or SQL strings that belong in metadata.

## 10. Honest open items

| Item | Status |
|---|---|
| Olist license and exact table columns/row counts | Not verified. Praneel checks and records it |
| Superset export details | Documentation summaries only. Praneel inspects a real export first |
| Appsmith export schema | Unverified; not used |
| TS hash port | Frozen vectors exist; the JS implementation has **not** been run (Node isn't on the machine that wrote them) |
| Gemini free-tier limits | Reported ~10 RPM / 250 RPD (Flash) and ~30 RPM / 1,000 RPD (Flash-Lite) [SEC]; check AI Studio. Full evaluation needs far more calls: caching, fewer repeats for ablations, small paid or local-model fallback |
| Gemini structured-output schema subset | Test `Plan` against it on day 1 |
| Calendar | Mentor review with slides 9 Oct; **offline live demo on Praneel's local desktop 11 Oct**. Blocks 1–2 on 8–9 Oct, 3–4 on 10 Oct, feature freeze and a fresh-clone dry run on the demo desktop the evening of 10 Oct, Blocks 5–6 on 11 Oct morning. Tight: use the demo ladder in `5A_Simple_Explainer_and_Slide_Content.md` A12 |

## 11. Development location (updated 2026-10-08)
Build and verify on the **workstation** (it has Docker, Node via conda, 48 cores). The **demo runs on the human's local PC on 11 Oct**, so: (1) everything must start from a clean clone with the README steps only; (2) do a **first fresh-clone run on the demo PC as soon as the first vertical slice works (target: 9–10 Oct), not on the last evening**; (3) pin dependencies (`requirements.lock.txt`, `package-lock.json`); (4) keep Olist CSVs and keys out of git; (5) warm the LLM response cache so the demo works offline.
