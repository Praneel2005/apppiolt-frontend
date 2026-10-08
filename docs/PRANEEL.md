# PRANEEL — Metadata & Dataset Lead, then Evaluation

**Mission:** you own the foundation. Everything the agent knows comes from the metadata and data you produce, and every score we report comes from the tasks and harness you build. If your output is weak, the whole project is weak.

Read first: `00_DECISIONS_AND_PLAN.md` (sections 2, 3, 6, 8). Paper/repo rule: papers = what we measure, repos = parts we run.

You own these folders: `generator/`, `sql/`, `data/` (gitignored), `tasks/`, `eval/`, `importers/`, `results/`, `contracts/dates.py` and `contracts/hash_vectors.json`.
You do **not** edit the other folders. If you need a contract change, follow the change rule.

---

## What we want from you (deliverables)

| # | Deliverable | File(s) | Consumers |
|---|---|---|---|
| P0 | Olist data downloaded and understood | `data/olist/*.csv`, `docs/DATA_NOTES.md` | everyone |
| P1 | Olist in Postgres + denormalized fact tables + semantic layer | `generator/load_olist.py`, `sql/*.sql`, `generator/semantic_layer.py` | Shreeniketh (query engine), Shashank |
| P2 | `Application` metadata for ~100–120 pages | `generator/build_application.py` → `data/olist/application.json` | everyone |
| P3 | Labelled benchmark tasks with gold actions, gold UI states and gold answers | `generator/build_tasks.py` → `tasks/dev.jsonl`, `tasks/test.jsonl` | Shashank, evaluation |
| P4 | Superset importer (stretch, after P3 starts) | `importers/superset.py`, `tests/test_superset_import.py` | portability claim |
| P5 | Evaluation harness, baselines runner, ablations, results | `eval/*.py`, `results/` | the final report |

Order: P0 → P1 → P2 → P3 → P5; P4 fits into waiting time; **P4 must finish before Block 6**.

---

## P0 — Get and understand the data (~1 h)

1. Open the Kaggle page for *Brazilian E-Commerce Public Dataset by Olist* (`https://www.kaggle.com/olistbr/brazilian-ecommerce` [PUB, via search]). **Read the license on that page and write it in `docs/DATA_NOTES.md`.** I believe it is CC BY-NC-SA 4.0 [MEM] but this was not confirmed. Non-commercial hackathon use is expected to be fine, but write down what the page actually says.
2. Download the 9 CSVs (Kaggle login needed, or `kaggle datasets download -d olistbr/brazilian-ecommerce` with the Kaggle CLI [MEM: verify the slug]). GitHub mirrors exist (search found several), but prefer Kaggle for the license.
3. For each CSV record: columns, row count, null counts, date range. I expect roughly: orders ≈ 99k, order_items ≈ 112k, customers ≈ 99k, products ≈ 33k, sellers ≈ 3k, payments ≈ 104k, reviews ≈ 99k [MEM]. **Record the real numbers.**
4. Find the **last complete month** in `order_purchase_timestamp`. The data thins out near the end [MEM]; check monthly counts. That month's last day is `as_of_date`.
5. If you cannot get the data tonight: say so at once and switch to the Northwind fallback (decision file section 3).

## P1 — Postgres, fact tables, semantic layer (~2–3 h)

1. Shreeniketh provides `docker-compose.yml` with Postgres. Until then run your own: `docker run -e POSTGRES_PASSWORD=app -p 5432:5432 postgres:16`.
2. `generator/load_olist.py`: create raw tables and load with `COPY` (or pandas → `to_sql`). Make it idempotent (`--reset` flag).
3. `sql/10_facts.sql` creates denormalized tables the semantic layer points to. Suggested:
   - `fact_order_items` = order_items ⨝ orders ⨝ customers ⨝ products ⨝ category-translation. Add derived columns: `order_month` (date), `delivery_days`, `is_late` (delivered after estimated), `customer_region` (map the 27 Brazilian states to the 5 macro-regions: North, Northeast, Central-West, Southeast, South), `product_category_en`.
   - `fact_orders`, `fact_payments`, `fact_reviews`.
   Verify column names against the real CSV headers; the ones above are from memory.
4. `generator/semantic_layer.py`: build the `Dataset` and `Metric` objects (from `contracts/metadata.py`). Starting set:

| Metric id | SQL | additive |
|---|---|---|
| `revenue` | `SUM(price)` | true |
| `freight` | `SUM(freight_value)` | true |
| `items_sold` | `COUNT(*)` | true |
| `orders` | `COUNT(DISTINCT order_id)` | true |
| `aov` | `SUM(price) / NULLIF(COUNT(DISTINCT order_id),0)` | **false** |
| `avg_delivery_days` | `AVG(delivery_days)` | **false** |
| `late_rate` | `AVG(CASE WHEN is_late THEN 1 ELSE 0 END)` | **false** |
| `payment_value`, `avg_installments` | on `fact_payments` | true / false |
| `avg_review_score`, `review_count` | on `fact_reviews` | false / true |

   Dimensions: `customer_state`, `customer_region`, `product_category_en`, `seller_state`, `payment_type`, `order_status`, `order_month`. Every `enum` field must list its **complete** `values` taken from the database (not typed by hand).
5. **Test:** pytest that every metric SQL runs and returns non-null on the full data; revenue total equals a hand-written SQL.

## P2 — Generate the `Application` (~3 h)

Goal: 100–120 pages across 7 modules that retrieve well. This is the K5 (retrieval) KPI.

1. `generator/build_application.py` builds `contracts.metadata.Application`. Page templates:
   - **Metric by dimension** (bar chart + grid), e.g. "Revenue by customer state".
   - **Metric trend** (line by month).
   - **Dataset detail grid** with sort.
   - **Module overview** (KPI cards).
   - A few **comparison** pages.
   Combine metrics × dimensions sensibly (skip meaningless ones) until you reach 100–120 pages. Use a `directories` tree like `sales/revenue`, `logistics/delivery`.
2. **Descriptions decide retrieval quality.** Each page and widget description must be 2–3 sentences, state the question it answers, and use words a user would say (sales = revenue = turnover; late = delayed = overdue). Do **not** generate near-identical template text; near-duplicates will confuse retrieval. Use an LLM to draft descriptions from structured facts (cache the output), then **read every one** and fix the bad ones.
3. Filters: build `FilterDef`s with `allowed_values` from the database and `synonyms`, e.g. state code ↔ name ("SP", "São Paulo", "Sao Paulo"), region names, category translations.
4. Set `as_of_date` (from P0). Add a `default_state` per page.
5. Every widget gets a `render` contract (`RenderContract`).
6. Output `data/olist/application.json`. Validate with `Application.model_validate`. Add tests: ≥100 pages, unique routes, every filter value exists in the DB, every metric in widgets exists.
7. **Early drop:** after about 1 hour, publish a 10–15 page version so Aditya and Shreeniketh can integrate. Then grow it.

## P3 — Benchmark tasks (~3 h)

1. `contracts/dates.py` already exists with tests; read it and confirm the semantics are what you want (calendar-based, relative to `as_of_date`). If you change anything, follow the change rule, because the validator and gold labels depend on it.
2. `generator/build_tasks.py` produces JSONL, one task per line:
```json
{"task_id":"dev-0012","level":"L3","utterance":"…","start_state":{...},
 "gold_intent":"set_state",
 "gold_steps":[{"tool":"navigate","args":{"page_id":"…"}},{"tool":"set_filter","args":{"page_id":"…","filter_id":"customer_state","op":"in","value":["SP"]}}],
 "gold_final_state":{...},
 "gold_answer":{"metric":"revenue","values":{...}},
 "gold_pages":["…"],
 "expected_behavior":"execute|clarify|refuse|confirm"}
```
3. Levels (details in the research doc §7.4): L1 navigation · L2 navigation+one setting · L3 multi-slot · L4 analytical single view · L5 multi-step analytical (compare, why) · L6 edge (ambiguous, out of scope, missing data, destructive, unknown value). **Target:** dev ≈ 60, test ≈ 140, at least 30 L6.
4. Gold final state follows the shared semantics S1–S8 exactly.
5. Gold answers: compute with the semantic-layer compiler (Shreeniketh's) **and** write independent hand SQL for ~30 tasks, then assert the two agree. This cross-check is how we catch compiler bugs (the verifier shares the compiler and cannot).
6. Paraphrase utterances with an LLM into a *separate* field, keep `paraphrase_of`. **Never tune on the test split.** Freeze `tasks/test.jsonl` before Block 5 and commit its hash.
7. Hand-check at least the whole test split.

## P4 — Superset importer (stretch, ~3–4 h)

Purpose: show the agent works on a **foreign** export, not just ours.
1. Read the Superset export docs (`https://superset.apache.org/admin-docs/configuration/importing-exporting-datasources` [PUB]). Run Superset locally with Docker, run `superset load_examples` [PUB docs], and export a dashboard (UI, or `/api/v1/dashboard/export/`) [PUB docs].
2. **Unzip it and read the real YAML before writing code.** The documented layout is directories for databases, datasets, charts, dashboards, each asset a YAML file, cross-referenced by UUID, plus `metadata.yaml` [PUB docs]. Field names in my mapping below are guesses to be corrected:

| Superset | → canonical |
|---|---|
| dashboard | `Page` (and its title/description) |
| chart | `Widget` (viz type → widget type) |
| dataset (table, columns, metrics) | `Dataset`, `FieldDef`, `Metric` |
| dashboard native filters | `FilterDef` |
| (no hierarchy) | one `Directory` per dashboard group |

3. `importers/superset.py: load(zip_path) -> Application` with `source_format="superset"`. Referential integrity is enforced by the model, so a bad mapping fails loudly.
4. What to show: run retrieval and validation on the imported app (K1/K5), and render one or two pages if the data can be reached. **Do not claim** the agent operates the Superset UI.
5. If the export doesn't map cleanly, document the gaps honestly in `docs/IMPORTER_NOTES.md`; that is still a real result.

## P5 — Evaluation (Block 5, with Shashank)

1. `eval/metrics.py`: implement every formula in research doc §15 with unit tests. **pass^k uses the unbiased estimator `C(c,k)/C(n,k)`** (c successes in n runs), averaged over tasks; the research doc listed a second form, ignore it.
2. `eval/run.py`: load tasks, run the agent through the backend API with a **headless ack simulator** (a fake browser that returns acks computed from the query engine) for speed, plus a **fault mode** (drop a filter, return an empty widget, rename a page). Aditya provides a Playwright subset for real-browser runs.
3. Baselines (Shashank implements the agents, you run them): B0 embedding-search-only, B1 prompt-stuffing, B2 RAG+tools with no validator/verifier. Ablations by `AgentConfig` flags: A1 validator off, A2 verifier off, A3 graph expansion off, A6 citation check off, A9 other model.
4. Report mean with 95% bootstrap CIs, 3 runs per task, model and prompt version recorded. Output `results/table.md`.
5. Be honest in the write-up: the ack check shares the query compiler; Superset import proves metadata portability only; results are on one dataset.

---

## What to read and explore (time-boxed)

| Item | Time | What to extract |
|---|---|---|
| τ-bench paper (arXiv 2406.12045) and `sierra-research/tau-bench` repo | 40 min | How it scores by comparing final state to a goal; how pass^k is computed; how tasks are structured. **Do not fork.** |
| BIRD paper (NeurIPS 2023) | 20 min | Why evidence/definitions matter for text-to-SQL; informs your metric definitions |
| WorkArena paper, Table 2 and task list | 20 min | Task categories to mirror (list filter, sort, dashboard, menu) |
| Superset export docs | 30 min | The format you will import |
| Olist Kaggle page and discussion | 20 min | Column meanings, known data quirks |

## Done when
- `data/olist/application.json` validates, has ≥100 pages, and passes your tests.
- `tasks/dev.jsonl` and `tasks/test.jsonl` exist with gold labels and the cross-check passes.
- `eval/run.py` produces `results/table.md` with CIs.
- Superset import is demonstrated, or its gaps are documented.

## Pitfalls
- Near-duplicate page descriptions (retrieval collapses).
- Typing allowed values by hand instead of reading them from the DB.
- Forgetting that relative dates resolve against `as_of_date`, not today.
- Decomposing non-additive metrics (S9).
- Tuning anything on the test split.

## Messages you owe others
- To Shreeniketh: "Olist loaded, here are the table names and the semantic layer" (end of P1).
- To Aditya and Shreeniketh: the 10–15 page `application.json` (early drop in P2).
- To Shashank: `application.json` + dev tasks.
