# Task cards for the local implementer (integration machine / Praneel's track)

These cards cover the **data and evaluation side**: Olist in Postgres, the semantic layer, the application metadata, the benchmark tasks and the evaluation metrics. Teammates' agents use the same `AGENTS.md` with their own file (`SHASHANK.md`, `ADITYA.md`, `SHREENIKETH.md`) as their task list.

Run **one card at a time**, in order. After each card the agent ends with the report block from `AGENTS.md`; paste it to Claude for review before starting the next card.

---

## Starter prompt
See `docs/CLI_PROMPT.md` (the single prompt to paste into the local agent). This file holds the detailed cards **T0–T8**; the full ordered list of all cards and gates is `docs/MASTER_BUILD_PLAN.md`.

---

## T0 — Environment and baseline checks
**Goal:** prove the toolchain and the shared contracts work on this machine.
**Do:** follow Steps 2–6 of `docs/LOCAL_SETUP_AND_HANDOFF.md` exactly (check_env, venv + pip install, pytest, `node --test contracts/ts/`, `docker compose up -d db`, check_gemini if a key exists).
**Acceptance:** `pytest` = 33 passed; every `node --test` case passes; `docker compose ps` shows db healthy; `check_gemini.py` output pasted (or "no key yet").
**Do not:** change any file except creating `.env` (not committed) and `requirements.lock.txt`.
**Why first:** the JS hash has never been run; if it fails, the whole verification design needs a fix, and we must know now.

## T1 — Initialise git and commit the baseline
**Do:** if the folder is not a git repo, `git init`, check `git status` shows no secrets/CSVs/.env, commit. Do not push unless the human asks.
**Acceptance:** `git log --oneline` shows the commit; `git status` clean.

## T2 — Get and inspect the Olist data
**Goal:** know the real data before building on it.
**Do:**
1. The human downloads the dataset from Kaggle (*Brazilian E-Commerce Public Dataset by Olist*, `https://www.kaggle.com/olistbr/brazilian-ecommerce`) into `data/olist/` (gitignored). The agent must **not** try to bypass Kaggle login.
2. Create `docs/DATA_NOTES.md` containing: **the license text exactly as shown on the Kaggle page**, the list of CSV files, and for each: header line, row count (`wc -l` minus 1), and the min/max of every date column. Record whether any header differs from `sql/05_raw_schema.sql` (known quirk to look for: `product_name_lenght` spelling).
3. Compute month-by-month order counts from `olist_orders_dataset.csv` and propose `as_of_date` = last day of the last **complete** month (the data thins out at the end). Put the proposal and the monthly table in `DATA_NOTES.md`.
**Acceptance:** `docs/DATA_NOTES.md` exists with real numbers (no guesses).
**Report:** include the license sentence, the as_of_date proposal and any header mismatches.

## T3 — Load raw tables
**Files:** `generator/load_olist.py`, fix `sql/05_raw_schema.sql` if T2 found differences.
**Spec:**
- CLI: `python generator/load_olist.py [--reset]`; reads `DATABASE_URL` (from `.env`); runs `sql/05_raw_schema.sql` (drops and recreates), then loads each CSV with `COPY ... FROM STDIN WITH (FORMAT csv, HEADER true)` via `psycopg`, naming columns explicitly in the CSV header order.
- Empty strings → NULL.
- Idempotent; prints row counts per table.
**Acceptance:** `tests/test_olist_db.py::test_raw_counts` compares each table's `count(*)` with the CSV row counts from `DATA_NOTES.md`. Tests must **skip** (not fail) when the database is not reachable.
**Command:** `python generator/load_olist.py --reset && python -m pytest tests/test_olist_db.py`

## T4 — Fact tables
**Files:** `sql/10_facts.sql` (draft exists and passes a syntax check on empty tables; verify on real data), `generator/build_facts.py` (just runs the SQL file).
**Spec / checks to add to `tests/test_olist_db.py`:**
- `SUM(price)` in `fact_order_items` equals `SUM(price)` in `raw_order_items`.
- Row count of `fact_order_items` equals `raw_order_items` (no join fan-out/loss; if not, find the cause: missing customers/orders).
- No `customer_region = 'Unknown'` rows (list offending states if any).
- `is_late` and `delivery_days` are NULL exactly when the order was not delivered.
- `app_ro` can `SELECT` from every fact table and cannot `INSERT`.
**Decision recorded in the metric descriptions:** revenue-type metrics exclude canceled/unavailable orders (`is_valid_sale`).
**Acceptance:** all checks pass; paste the numbers (row counts, total revenue).

## T5 — Semantic layer
**Files:** `generator/semantic_layer.py`, `tests/test_semantic_layer.py`.
**Spec:** `build_datasets(conn) -> list[contracts.metadata.Dataset]` producing:
| dataset_id | table | time_field | metrics (id: SQL, additive) |
|---|---|---|---|
| `ds_order_items` | `fact_order_items` | `order_date` | `revenue: SUM(price) FILTER (WHERE is_valid_sale)` (T), `freight: SUM(freight_value) FILTER (WHERE is_valid_sale)` (T), `items_sold: COUNT(*) FILTER (WHERE is_valid_sale)` (T), `orders: COUNT(DISTINCT order_id) FILTER (WHERE is_valid_sale)` (T), `aov: SUM(price) FILTER (WHERE is_valid_sale) / NULLIF(COUNT(DISTINCT order_id) FILTER (WHERE is_valid_sale),0)` (F), `avg_delivery_days: AVG(delivery_days)` (F), `late_rate: AVG(is_late)` (F) |
| `ds_orders` | `fact_orders` | `order_date` | `order_count: COUNT(*)` (T), `cancel_rate: AVG(CASE WHEN order_status='canceled' THEN 1.0 ELSE 0 END)` (F) |
| `ds_payments` | `fact_payments` | `order_date` | `payment_value: SUM(payment_value)` (T), `avg_installments: AVG(payment_installments)` (F) |
| `ds_reviews` | `fact_reviews` | `order_date` | `review_count: COUNT(*)` (T), `avg_review_score: AVG(review_score)` (F) |

Fields: every column of the fact table with `role` (time / dimension / metric / id) and a one-line description. **Enum `values` are read from the database** (`SELECT DISTINCT`), never typed: `customer_state`, `customer_region`, `product_category`, `seller_state`, `payment_type`, `order_status`. Each metric has a clear `title` and `description` (one sentence a business user would understand) and `additive` set as in the table (T = true, F = false).
**Acceptance:** a test executes `SELECT <metric.sql> FROM <table>` for every metric and asserts a non-null numeric result; a second test asserts `revenue` equals an independent hand-written SQL result.

## T6 — Application generator v1 (≥ 15 pages) and early drop
**Files:** `generator/build_application.py`, output `data/olist/application.json` (commit it), `tests/test_application.py`.
**Spec:**
- Builds `contracts.metadata.Application` (`source_format="native"`, `as_of_date` from T2).
- Modules: `sales, customers, sellers, logistics, payments, reviews, catalog`; directories like `sales/revenue`, `logistics/delivery`.
- Templates (v1 produces ≥ 15 pages; T9 grows it to 100–120):
  - **metric by dimension** → page_id `{module}.{metric}_by_{dimension}`, route `/{module}/{metric}-by-{dimension}` (underscores → hyphens), widgets `{page_id}.chart_main` (bar) and `{page_id}.grid`.
  - **metric trend** → `{module}.{metric}_trend`, one line chart over `order_month`.
  - **dataset detail grid** with sort.
- Filters per page: a `date_range` filter on the dataset time field (`allowed_ops: ["between"]`) plus up to 3 dimension filters (`multiselect`, `allowed_values` from the DB, `synonyms` for state codes ↔ names and regions); `default_state` with `{"date_range": {"preset": "last_12_months"}}`.
- Every widget has a `render` contract (`RenderContract`) listing its metrics and dimensions.
- Descriptions v1 are template-built but **must differ from page to page** and name the question the page answers; T9 improves them.
**Acceptance:** `Application.model_validate` passes; ≥ 15 pages; unique ids and routes; every filter value exists in the DB; every widget metric exists in its dataset; `as_of_date` is set. Commit `data/olist/application.json` so the others can integrate (this is the **early drop**: tell the team it exists).

## T7 — State simulator and task generator v1 (levels L1–L3)
**Files:** `eval/state_sim.py`, `generator/build_tasks.py`, `tasks/dev.jsonl`, `tests/test_state_sim.py`.
**Spec:**
- `eval/state_sim.py::apply_step(app, state, step) -> UiState` implements the shared semantics S1–S5 from `docs/00_DECISIONS_AND_PLAN.md` for `navigate` (with `keep_state`), `set_filter`, `set_date_range`, `set_sort`; raises `ValueError` on invalid references/values. Date presets resolve with `contracts.dates.resolve` against `app.as_of_date` and are stored as explicit `from`/`to` plus the preset label.
- `generator/build_tasks.py` writes one JSON object per line (format in `docs/PRANEEL.md` P3) for L1 (navigate), L2 (navigate + one setting), L3 (multi-slot). Gold final state = applying the gold steps with `apply_step` (S8). Target ≥ 60 dev tasks; vary phrasing templates; keep `gold_pages`.
**Acceptance:** tests apply each task's gold steps to its `start_state` and assert equality with `gold_final_state`; tests also cover the invalid-value and keep_state cases.

## T8 — Evaluation metrics module
**Files:** `eval/metrics.py`, `tests/test_metrics.py`.
**Functions (exact):**
| Function | Definition |
|---|---|
| `precision_at_k(retrieved, gold, k)` | `|top-k ∩ gold| / k` |
| `recall_at_k(retrieved, gold, k)` | `|top-k ∩ gold| / |gold|` |
| `mrr(ranks)` | mean of `1/rank` (rank 1-based; `None` counts 0) |
| `state_exact_match(pred, gold)` | equality of route, page_id, filters, date_range (from/to), sort |
| `slot_f1(pred_slots, gold_slots)` | F1 over (filter_id, op, sorted values) triples plus date range and sort |
| `pass_hat_k(successes, n, k)` | mean over tasks of `C(c,k)/C(n,k)` where `c` = successes of that task in `n` runs; **this unbiased form only** |
| `bootstrap_ci(values, iters=2000, seed=0)` | 95% percentile bootstrap CI of the mean |
**Required test values:** `pass_hat_k([3,3,0], n=3, k=3) = 2/3`; `pass_hat_k([2], n=3, k=2) = 1/3`; `mrr([1,2,None]) = 0.5`; `precision_at_k(['a','b','c'], {'a','c'}, 2) = 0.5`; `recall_at_k(['a','b','c'], {'a','c'}, 2) = 0.5`; `bootstrap_ci` is deterministic for a fixed seed and contains the mean.
**Acceptance:** all tests pass.

## Later cards
See `docs/MASTER_BUILD_PLAN.md` (T9 onward: backend, frontend, safety spine, agent, evaluation, importer, demo).

---

## Notes for the human
- If the agent proposes something that conflicts with `contracts/` or with `docs/00_DECISIONS_AND_PLAN.md`, do **not** accept it silently; send it to Claude.
- If a card is too big for the agent in one go, tell it to split the card into sub-steps itself and report after each sub-step.
- The first message to Claude after T0 is the checklist at the end of `docs/LOCAL_SETUP_AND_HANDOFF.md`.
