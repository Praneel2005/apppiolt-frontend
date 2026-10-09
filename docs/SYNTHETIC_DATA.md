# Data layers and synthetic data

Rebuild everything, in order (all deterministic, seed `20261011`):

```
python -m generator.load_olist      # original CSVs -> raw_*, derived tables, roles
python -m generator.synthesize      # syn_* and ops_* tables (drops and recreates them, incl. the action log)
python -m generator.build_application
```

## The four layers

| Layer | Prefix | What it is | Who may change it |
|---|---|---|---|
| **original** | `raw_` | Olist CSVs loaded 1:1 (marketplace + marketing funnel). Never modified. | nobody |
| **derived** | `core_`, `fact_` | Rebuilt from `raw_` by `sql/06`, `sql/10`. Documented formulas (`generator/semantic_layer.py`). | the loader |
| **synthetic** | `syn_` | Simulated plans and history Olist does not have. Read-only for the API. | `generator/synthesize.py` |
| **synthetic, writable** | `ops_` | Operational records the application (and the agent, after confirmation) create and change. | API writes only (role `app_rw`) |
| system | `ops_action_log` | Audit log with before/after images, used for undo. Append-only for the API. | API |

Every table carries a `COMMENT` starting with its layer; `GET /api/data-dictionary` returns them, and every API
response lists the `data_layers` and tables behind its numbers. Synthetic values are never presented as Olist facts.
The database role `app_rw` physically cannot write `raw_`, `core_`, `fact_` or `syn_` tables (tested).

## Metric definitions (one place: `generator/semantic_layer.py`)

- **Revenue** = sum of order-item price, excluding freight, of valid sales (status not `canceled`/`unavailable`).
- **Freight** = sum of item freight on valid sales. **Payments received** includes freight and is not revenue.
- **Customers** = distinct `customer_unique_id` with a valid sale (not additive across groups).
- **Late delivery** = delivered, and `order_delivered_customer_date > order_estimated_delivery_date`.
- One-to-many tables (items, payments, reviews) are aggregated separately before being joined (`core_order_summary`).

## Synthetic tables

Simulated "today" is `2018-08-31` (the dataset's `as_of_date`); the API clock starts at 2018-08-31 18:00 and advances in real time.

| Table | Grain | Method (real inputs in **bold**) |
|---|---|---|
| `syn_monthly_sales_targets` | month x customer state (2017-04 .. 2018-09) | target = round(**mean actual revenue of the 3 previous months** x 1.08 growth (x1.25 in November) x lognormal(0, 0.06) noise); order target the same on **order counts** |
| `syn_delivery_sla_rules` | destination state x rule version | v1 (to 2017-12-31): expected = ceil(median), threshold = ceil(80th percentile) of **real 2016-17 delivery days** per state (region if < 30 orders); v2 (from 2018-01-01): one day tighter. Not Olist's per-order estimated date. |
| `syn_monthly_sales_forecasts` | month x state x scenario (2018-01 .. 2018-12) | linear trend on **trailing 6 months of actual revenue**; Jan-Aug are one-step backtests, Sep-Dec forecast from August; optimistic x1.10, conservative x0.90. `model_version = linear_trend_6m_v1` |
| `ops_product_listings` | listed product (**products with a valid sale in the last 6 months** = 16,725) | price = **last real sale price**; unit cost = price x category margin U(0.50, 0.72); reorder point = ceil(1.5 x lead time x **daily demand**); lead time U{5..15} days |
| `syn_inventory_snapshots` | month-end (Mar-Aug 2018) x product | stock simulated month by month, depleted by **real units sold**, replenished by restock orders |
| `ops_restock_orders` | one purchase order | placed when month-end stock <= reorder point (85% of reviews catch it, so some products stay low) |
| `ops_support_tickets` | one ticket | raised on **real problem orders**: p = 0.35 for review <= 2, 0.15 for late deliveries, 0.30 for overdue undelivered; created/resolved timestamps from lognormal resolution times |
| `ops_review_replies` | one reply | to **real low-score reviews with a comment**, p = 0.40, 5 templates |
| `ops_promotions` | one campaign | hand-written calendar of Brazilian retail moments (14); did not cause observed sales |
| `ops_seller_flags` | one flag | rule on **real seller performance** (12 months): late rate > 20% with >= 30 deliveries, or average review < 3.0 with >= 30 reviews |
| `ops_price_changes`, `ops_action_log` | - | empty at start; filled by API writes |

## Known limits (say them before the mentor does)

- Targets, SLA, forecasts, inventory, tickets, replies, promotions and flags are **simulated**; findings about them
  describe the simulation, not Olist. Real data is 2016-09 .. 2018-08 (September/October 2018 hold 20 stray orders).
- Forecasts use a deliberately simple method (no seasonality); February 2018 is over-forecast by ~29% because the model
  extrapolates the November-January peak. This is real backtest error and a useful "why was the forecast wrong" case.
- Targets for tiny states (RR, AP, AM) are based on a handful of orders, so their attainment is noisy.
- Marketing-funnel leads with a missing origin (1,159) are kept as `unknown`; they are real Olist data.
- Funnel revenue is attributed to a seller from the day their deal was won; Olist does not say which sales came from the deal.
