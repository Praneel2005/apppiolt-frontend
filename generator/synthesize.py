"""Synthetic business data on top of the real Olist data (layer: synthetic).

    python -m generator.synthesize          # (re)creates every syn_* and ops_* table

Olist has no targets, SLAs, forecasts, inventory, tickets, replies, promotions or seller flags.
This script simulates them so the application has realistic operations to show and change.
Rules:
  * raw_* (original) and core_*/fact_* (derived) tables are only READ, never written.
  * Every random draw comes from numpy generators seeded with SEED and a per-table number, in a
    fixed row order, so the output is identical on every run and one table never shifts another.
  * Wherever possible the simulation is driven by REAL Olist quantities (monthly revenue per state,
    unit sales per product, late deliveries, low review scores); the formulas are in each function
    docstring and in docs/SYNTHETIC_DATA.md.
  * Nothing produced here may be presented as an Olist fact; the API labels it "synthetic".
"""
from __future__ import annotations

import math
import os
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import psycopg

from generator.load_olist import SQL, load_env, run_sql

SEED = 20261011
AS_OF = date(2018, 8, 31)               # same as Application.as_of_date
SIM_NOW = datetime(2018, 8, 31, 18, 0)  # simulated "now" for the operations data


def rng_for(table_no: int) -> np.random.Generator:
    return np.random.default_rng([SEED, table_no])


def frame(conn: psycopg.Connection, sql: str) -> pd.DataFrame:
    cur = conn.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def _py(v):
    """numpy / pandas scalars -> plain Python values psycopg can send."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if np.isnan(v) else float(v)
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def copy_rows(conn: psycopg.Connection, table: str, cols: list[str], rows) -> int:
    n = 0
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([_py(v) for v in r])
                n += 1
    return n


def month_start(p: pd.Period) -> date:
    return p.start_time.date()


def month_end(p: pd.Period) -> date:
    return p.end_time.date()


# --------------------------------------------------------------------------- actuals (real data)
def monthly_actuals(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Real valid-sale revenue and order counts per month x customer state (2016-09 .. as_of)."""
    df = frame(conn, f"""
        SELECT order_month, customer_state,
               SUM(price) FILTER (WHERE is_valid_sale) AS revenue,
               COUNT(DISTINCT order_id) FILTER (WHERE is_valid_sale) AS orders
        FROM fact_order_items WHERE order_month <= '{AS_OF}' GROUP BY 1, 2""")
    df["month"] = pd.PeriodIndex(pd.to_datetime(df["order_month"]), freq="M")
    idx = pd.period_range("2016-09", pd.Period(AS_OF, "M"), freq="M")
    states = sorted(frame(conn, "SELECT DISTINCT customer_state FROM raw_customers")["customer_state"])
    rev = df.pivot_table(index="month", columns="customer_state", values="revenue", aggfunc="sum")
    orders = df.pivot_table(index="month", columns="customer_state", values="orders", aggfunc="sum")
    rev = rev.reindex(index=idx, columns=states).fillna(0.0).astype(float)
    orders = orders.reindex(index=idx, columns=states).fillna(0).astype(float)
    return rev, orders


# --------------------------------------------------------------------------- 1. targets
def build_targets(conn, rev: pd.DataFrame, orders: pd.DataFrame) -> int:
    """syn_monthly_sales_targets, one row per month x state, months 2017-04 .. as_of+1.

    revenue_target = round_100( mean(actual revenue, 3 months before) x growth x noise )
    order_target   = round( mean(actual orders, 3 months before) x growth x noise' )
    growth = 1.08 (planned growth), x1.25 in November (Black Friday plan); noise ~ LogNormal(0, 0.06).
    Targets are a plan made from the recent run-rate, so some states beat them and some miss.
    """
    rng = rng_for(1)
    months = pd.period_range("2017-04", pd.Period(AS_OF, "M") + 1, freq="M")
    states = list(rev.columns)
    noise = rng.lognormal(0.0, 0.06, size=(len(months), len(states), 2))
    rows = []
    for i, m in enumerate(months):
        window = pd.period_range(m - 3, m - 1, freq="M")
        base_rev = rev.loc[window].mean()
        base_ord = orders.loc[window].mean()
        growth = 1.08 * (1.25 if m.month == 11 else 1.0)
        for j, s in enumerate(states):
            if base_rev[s] <= 0:
                continue
            rows.append((month_start(m), s,
                         max(100.0, round(base_rev[s] * growth * noise[i, j, 0], -2)),
                         max(1, int(round(base_ord[s] * growth * noise[i, j, 1]))),
                         "baseline", "trailing_3m_mean x 1.08 growth (x1.25 Nov) x lognormal(0,0.06) noise"))
    return copy_rows(conn, "syn_monthly_sales_targets",
                     ["target_month", "customer_state", "revenue_target", "order_target", "scenario_name",
                      "method"], rows)


# --------------------------------------------------------------------------- 2. SLA rules
SLA_V2_FROM = date(2018, 1, 1)
SLA_MIN_ORDERS = 30  # states with fewer delivered 2016-17 orders use their region's percentiles


def build_sla(conn) -> int:
    """syn_delivery_sla_rules, two versions per destination state (deterministic, no randomness).

    Calibrated on REAL delivery times of orders purchased up to 2017-12-31, like a carrier contract
    set from history: expected_delivery_days = ceil(median), sla_threshold_days = ceil(80th percentile)
    for the state (the region's percentiles if the state has < 30 delivered orders).
      v1 2016-09-01 .. 2017-12-31: those values.
      v2 2018-01-01 .. open      : one day tighter on both (the simulated company raised its commitment).
    A delivered order breaches when delivery_days > sla_threshold_days of the rule in force on its
    purchase date. These are NOT Olist's per-order estimated delivery dates.
    """
    d = frame(conn, """SELECT customer_state AS s, customer_region AS r, delivery_days::float8 AS days
                       FROM core_order_summary
                       WHERE delivered_at IS NOT NULL AND order_date <= DATE '2017-12-31'""")

    def pct(group: pd.DataFrame) -> tuple[int, int]:
        return int(math.ceil(group["days"].quantile(0.5))), int(math.ceil(group["days"].quantile(0.8)))

    by_region = {r: pct(g) for r, g in d.groupby("r")}
    by_state = {s: (pct(g) if len(g) >= SLA_MIN_ORDERS else None) for s, g in d.groupby("s")}
    states = frame(conn, "SELECT DISTINCT customer_state, br_region(customer_state) AS region "
                         "FROM raw_customers ORDER BY 1")
    rows = []
    for s, region in states.itertuples(index=False):
        exp, thr = by_state.get(s) or by_region[region]
        thr = max(thr, exp)
        rows.append((s, region, date(2016, 9, 1), SLA_V2_FROM - timedelta(days=1), exp, thr, "baseline"))
        rows.append((s, region, SLA_V2_FROM, None, max(1, exp - 1), max(max(1, exp - 1), thr - 1), "baseline"))
    return copy_rows(conn, "syn_delivery_sla_rules",
                     ["destination_state", "destination_region", "effective_from", "effective_to",
                      "expected_delivery_days", "sla_threshold_days", "scenario_name"], rows)


# --------------------------------------------------------------------------- 3. forecasts
FORECAST_MODEL = "linear_trend_6m_v1"
SCENARIOS = {"baseline": 1.0, "optimistic": 1.10, "conservative": 0.90}


def build_forecasts(conn, rev: pd.DataFrame) -> int:
    """syn_monthly_sales_forecasts per state x month for 2018-01 .. 2018-12 (no randomness).

    Model (on real monthly revenue up to a cutoff month c):
        level = mean(rev[c-2..c]),  prev = mean(rev[c-5..c-3]),  slope = (level - prev) / 3
        forecast(c + h) = max(0, level + slope * (h + 1))      # level is centred on c-1
    Months up to as_of are one-step-ahead backtests (c = month - 1, h = 1), so "forecast vs
    actual" can be checked; later months use c = as_of month. Scenarios scale the baseline x1.1 / x0.9.
    """
    last = pd.Period(AS_OF, "M")
    rows = []
    for m in pd.period_range("2018-01", "2018-12", freq="M"):
        c = m - 1 if m <= last else last
        h = (m - c).n
        level = rev.loc[pd.period_range(c - 2, c, freq="M")].mean()
        prev = rev.loc[pd.period_range(c - 5, c - 3, freq="M")].mean()
        base = (level + (level - prev) / 3.0 * (h + 1)).clip(lower=0.0)
        for s in rev.columns:
            for scen, k in SCENARIOS.items():
                rows.append((month_start(m), s, round(float(base[s]) * k, 2), scen,
                             "linear trend on trailing 6 months", FORECAST_MODEL, month_start(c), h,
                             month_end(c) + timedelta(days=1)))
    return copy_rows(conn, "syn_monthly_sales_forecasts",
                     ["forecast_month", "customer_state", "predicted_revenue", "scenario_name",
                      "forecast_method", "model_version", "data_cutoff", "horizon_months", "generated_at"],
                     rows)


# --------------------------------------------------------------------------- 4. listings, inventory, restocks
def build_inventory(conn) -> dict[str, int]:
    """ops_product_listings, syn_inventory_snapshots, ops_restock_orders.

    Listed products = products with a valid sale in the 6 months up to as_of (real data).
      list_price     = price of the product's most recent real sale
      unit_cost      = list_price x category margin, margin ~ U(0.50, 0.72) per category
      demand/day d   = max(units sold in last 90 days / 90, units sold in 6 months / 184)   (real)
      lead_time_days ~ U{5..15};  reorder_point = max(1, ceil(d x lead x 1.5))
      order_up_to    = reorder_point + max(2, ceil(d x 30))
    Monthly simulation Mar..Aug 2018, starting stock = order_up_to + U{0..2}:
      receive restock orders that arrived this month -> subtract REAL units sold this month
      (floored at 0) -> month-end snapshot -> review: if stock <= reorder_point and nothing is on
      order, place an order for order_up_to - stock with probability 0.85 (15% of reviews are
      missed, which leaves some products below their reorder point with nothing on order).
    Orders arriving after as_of stay 'in_transit'.
    """
    rng = rng_for(4)
    start = date(2018, 3, 1)
    sales = frame(conn, f"""
        SELECT product_id, product_category, order_date, price, order_id, order_item_id
        FROM fact_order_items
        WHERE is_valid_sale AND order_date >= '{start}' AND order_date <= '{AS_OF}'
        ORDER BY product_id, order_date, order_id, order_item_id""")
    sales["order_date"] = pd.to_datetime(sales["order_date"])
    sales["price"] = sales["price"].astype(float)
    products = sorted(sales["product_id"].unique())
    n = len(products)
    pidx = {p: i for i, p in enumerate(products)}
    last_sale = sales.groupby("product_id").tail(1).set_index("product_id")
    list_price = np.array([round(float(last_sale.loc[p, "price"]), 2) for p in products])
    category = [last_sale.loc[p, "product_category"] for p in products]
    cats = sorted(set(category))
    margin = dict(zip(cats, rng.uniform(0.50, 0.72, size=len(cats))))
    unit_cost = np.array([max(0.01, round(list_price[i] * margin[category[i]], 2)) for i in range(n)])
    lead = rng.integers(5, 16, size=n)

    months = pd.period_range("2018-03", pd.Period(AS_OF, "M"), freq="M")
    sales["m"] = sales["order_date"].dt.to_period("M")
    units = np.zeros((n, len(months)), dtype=int)
    for (p, m), k in sales.groupby(["product_id", "m"]).size().items():
        units[pidx[p], months.get_loc(m)] = k
    d90_cut = pd.Timestamp(AS_OF - timedelta(days=90))
    u90 = sales[sales["order_date"] > d90_cut].groupby("product_id").size()
    d90 = np.array([u90.get(p, 0) for p in products]) / 90.0
    demand = np.maximum(d90, units.sum(axis=1) / 184.0)
    rop = np.maximum(1, np.ceil(demand * lead * 1.5)).astype(int)
    up_to = rop + np.maximum(2, np.ceil(demand * 30)).astype(int)

    stock = up_to + rng.integers(0, 3, size=n)
    pending = np.full(n, -1)  # index into orders list, -1 = nothing on order
    orders: list[dict] = []
    snapshots = []
    for mi, m in enumerate(months):
        m_end = month_end(m)
        for i in np.nonzero(pending >= 0)[0]:
            o = orders[pending[i]]
            if o["expected_arrival"] <= m_end:
                stock[i] += o["quantity"]
                o["status"], o["received_at"] = "received", datetime.combine(o["expected_arrival"],
                                                                              datetime.min.time()) + timedelta(hours=10)
                pending[i] = -1
        stock = np.maximum(0, stock - units[:, mi])
        snapshots.append((m_end, stock.copy()))
        review_ok = rng.random(n) < 0.85
        days_before = rng.integers(0, 3, size=n)
        for i in np.nonzero((stock <= rop) & (pending < 0) & review_ok)[0]:
            created = datetime.combine(m_end - timedelta(days=int(days_before[i])), datetime.min.time()) \
                + timedelta(hours=9)
            orders.append({"product_id": products[i], "quantity": int(up_to[i] - stock[i]),
                           "status": "in_transit", "created_at": created,
                           "expected_arrival": created.date() + timedelta(days=int(lead[i])),
                           "received_at": None})
            pending[i] = len(orders) - 1

    counts = {}
    counts["ops_product_listings"] = copy_rows(
        conn, "ops_product_listings",
        ["product_id", "list_price", "unit_cost", "stock_quantity", "reorder_point", "order_up_to",
         "lead_time_days", "status", "updated_at"],
        ((products[i], list_price[i], unit_cost[i], stock[i], rop[i], up_to[i], lead[i], "active", SIM_NOW)
         for i in range(n)))
    counts["syn_inventory_snapshots"] = copy_rows(
        conn, "syn_inventory_snapshots",
        ["snapshot_date", "product_id", "stock_quantity", "reorder_point", "unit_cost", "scenario_name"],
        ((d, products[i], s[i], rop[i], unit_cost[i], "baseline") for d, s in snapshots for i in range(n)))
    orders.sort(key=lambda o: (o["created_at"], o["product_id"]))
    counts["ops_restock_orders"] = copy_rows(
        conn, "ops_restock_orders",
        ["product_id", "quantity", "status", "created_at", "expected_arrival", "received_at", "created_by"],
        ((o["product_id"], o["quantity"], o["status"], o["created_at"], o["expected_arrival"],
          o["received_at"], "ops_planner") for o in orders))
    return counts


# --------------------------------------------------------------------------- 5. support tickets
ASSIGNEES = ["ana", "bruno", "carla", "diego", "elisa", "felipe"]  # fictional support agents
SUBJECTS = {
    "late_delivery": "Order {ref} arrived after the promised date",
    "not_received": "Order {ref} not received yet",
    "damaged_item": "Item in order {ref} arrived damaged",
    "wrong_item": "Wrong item received in order {ref}",
    "product_quality": "Quality complaint about order {ref}",
    "refund_request": "Refund requested for order {ref}",
}
OTHER_CATS = ["damaged_item", "wrong_item", "product_quality", "refund_request"]
OTHER_CUM = np.cumsum([0.25, 0.20, 0.35, 0.20])


def build_tickets(conn) -> int:
    """ops_support_tickets, raised on REAL problem orders (purchase <= as_of).

    Candidates and probabilities:
      review score <= 2                    p = 0.35  (category: late_delivery if the order was
                                                      late, else one of damaged/wrong/quality/refund
                                                      with p = .25/.20/.35/.20)
      delivered late, no low review        p = 0.15  (late_delivery)
      not delivered and estimate passed    p = 0.30  (not_received)
    created_at: low review -> review date + U(0..2) days; late / not received -> estimated
    delivery date + U(1..5) days; random hour 08..19. Tickets after SIM_NOW are dropped.
    priority: urgent if >20 days late or not received; high if score 1 or >10 days late; else medium.
    resolution time ~ LogNormal(log 72 h, 0.8) (x3 for not_received); resolved if it ends before
    SIM_NOW (closed if also 7 days before), otherwise in_progress (open if < 1 day old).
    """
    rng = rng_for(5)
    df = frame(conn, f"""
        SELECT o.order_id, c.customer_unique_id, o.order_status,
               o.order_delivered_customer_date AS delivered, o.order_estimated_delivery_date AS estimated,
               r.review_score, r.review_creation_date,
               (SELECT i.seller_id FROM raw_order_items i WHERE i.order_id = o.order_id
                ORDER BY i.order_item_id LIMIT 1) AS seller_id
        FROM raw_orders o JOIN raw_customers c USING (customer_id)
        LEFT JOIN LATERAL (SELECT review_score, review_creation_date FROM raw_reviews rv
                           WHERE rv.order_id = o.order_id
                           ORDER BY review_creation_date DESC, review_id LIMIT 1) r ON true
        WHERE o.order_purchase_timestamp <= '{SIM_NOW}'
        ORDER BY o.order_id""")
    n = len(df)
    u, cat_u, day_u, hour_u = rng.random(n), rng.random(n), rng.random(n), rng.integers(8, 20, size=n)
    res_h = rng.lognormal(math.log(72.0), 0.8, size=n)
    assignee_i = rng.integers(0, len(ASSIGNEES), size=n)
    rows = []
    for k, r in enumerate(df.itertuples(index=False)):
        delivered = r.delivered if not pd.isna(r.delivered) else None
        late_days = (delivered - r.estimated).days if delivered is not None and delivered > r.estimated else 0
        low = r.review_score is not None and not pd.isna(r.review_score) and r.review_score <= 2
        not_received = (delivered is None and r.order_status not in ("canceled", "unavailable")
                        and r.estimated is not None and r.estimated < SIM_NOW - timedelta(days=1))
        if low:
            p = 0.35
        elif late_days > 0:
            p = 0.15
        elif not_received:
            p = 0.30
        else:
            continue
        if u[k] >= p:
            continue
        if low and late_days == 0:
            cat = OTHER_CATS[min(3, int(np.searchsorted(OTHER_CUM, cat_u[k], side="right")))]
            created = r.review_creation_date + timedelta(days=int(day_u[k] * 3))
        else:
            cat = "late_delivery" if late_days > 0 else "not_received"
            created = r.estimated + timedelta(days=1 + int(day_u[k] * 5))
        created = created.replace(hour=int(hour_u[k]), minute=0, second=0)
        if created > SIM_NOW:
            continue
        score = int(r.review_score) if low else None
        if late_days > 20 or cat == "not_received":
            priority = "urgent" if (late_days > 20 or (SIM_NOW - created).days > 14) else "high"
        elif score == 1 or late_days > 10:
            priority = "high"
        else:
            priority = "medium"
        resolved = created + timedelta(hours=float(res_h[k]) * (3 if cat == "not_received" else 1))
        if resolved <= SIM_NOW:
            status = "closed" if resolved + timedelta(days=7) <= SIM_NOW else "resolved"
        else:
            resolved = None
            status = "open" if created > SIM_NOW - timedelta(days=1) else "in_progress"
        rows.append((created, r.order_id, r.customer_unique_id, r.seller_id, cat, priority, status,
                     SUBJECTS[cat].format(ref=r.order_id[:8].upper()),
                     None if status == "open" else ASSIGNEES[assignee_i[k]], resolved, "customer"))
    rows.sort(key=lambda t: (t[0], t[1]))
    return copy_rows(conn, "ops_support_tickets",
                     ["created_at", "order_id", "customer_unique_id", "seller_id", "category", "priority",
                      "status", "subject", "assignee", "resolved_at", "created_by"], rows)


# --------------------------------------------------------------------------- 6. review replies
REPLIES = [
    "We are sorry about your experience. Our team has contacted the seller and will follow up by email.",
    "Thank you for the feedback. We have opened a case and will make this right as soon as possible.",
    "We apologise for the inconvenience. Please reply to our email so we can arrange an exchange or refund.",
    "Sorry that the order did not meet your expectations. A support agent will contact you within 48 hours.",
    "Thank you for telling us. We have shared your comments with the seller to improve their service.",
]


def build_replies(conn) -> int:
    """ops_review_replies: replies to REAL low reviews (score <= 2) that have a comment.
    p(reply) = 0.40; created = review date + U(1..4) days at 10:00; text from 5 templates."""
    rng = rng_for(6)
    df = frame(conn, f"""
        SELECT review_id, order_id, review_creation_date FROM raw_reviews
        WHERE review_score <= 2 AND review_comment_message IS NOT NULL
          AND review_creation_date <= '{SIM_NOW}'
        ORDER BY review_id, order_id""")
    n = len(df)
    u, days, tpl = rng.random(n), rng.integers(1, 5, size=n), rng.integers(0, len(REPLIES), size=n)
    rows = []
    for k, r in enumerate(df.itertuples(index=False)):
        created = (r.review_creation_date + timedelta(days=int(days[k]))).replace(hour=10, minute=0, second=0)
        if u[k] < 0.40 and created <= SIM_NOW:
            rows.append((created, r.review_id, r.order_id, REPLIES[tpl[k]], "seller_support"))
    rows.sort(key=lambda t: (t[0], t[1]))
    return copy_rows(conn, "ops_review_replies",
                     ["created_at", "review_id", "order_id", "reply_text", "created_by"], rows)


# --------------------------------------------------------------------------- 7. promotions
# (name, category or None, state or None, discount %, start, end, canceled). Hand-written calendar of
# Brazilian retail moments. They are synthetic: they did NOT cause the observed Olist sales.
PROMOTIONS = [
    ("Carnaval sale", None, None, 10, date(2017, 2, 20), date(2017, 3, 1), False),
    ("Mother's Day beauty week", "health_beauty", None, 15, date(2017, 5, 1), date(2017, 5, 14), False),
    ("Winter bedding sale", "bed_bath_table", None, 12, date(2017, 7, 1), date(2017, 7, 15), False),
    ("Father's Day watches", "watches_gifts", None, 15, date(2017, 8, 1), date(2017, 8, 13), False),
    ("Black Friday 2017", None, None, 20, date(2017, 11, 20), date(2017, 11, 26), False),
    ("Christmas toys", "toys", None, 15, date(2017, 12, 1), date(2017, 12, 24), False),
    ("Back to school stationery", "stationery", None, 10, date(2018, 1, 15), date(2018, 2, 10), False),
    ("Cool stuff flash sale", "cool_stuff", None, 25, date(2018, 3, 5), date(2018, 3, 7), True),
    ("Mother's Day beauty week 2018", "health_beauty", None, 15, date(2018, 4, 30), date(2018, 5, 13), False),
    ("World Cup electronics", "electronics", None, 10, date(2018, 6, 10), date(2018, 7, 15), False),
    ("Father's Day watches 2018", "watches_gifts", None, 15, date(2018, 7, 30), date(2018, 8, 12), False),
    ("Furniture clearance", "furniture_decor", None, 18, date(2018, 8, 20), date(2018, 9, 10), False),
    ("Sao Paulo week", None, "SP", 8, date(2018, 8, 27), date(2018, 9, 2), False),
    ("Spring sports", "sports_leisure", None, 12, date(2018, 9, 15), date(2018, 9, 30), False),
]


def build_promotions(conn) -> int:
    known = set(frame(conn, "SELECT product_category_name_english FROM core_category_map")
                ["product_category_name_english"])
    bad = [p[1] for p in PROMOTIONS if p[1] and p[1] not in known]
    if bad:
        raise ValueError(f"promotion categories not in core_category_map: {bad}")
    rows = [(name, cat, st, pct, s, e, canceled,
             datetime.combine(s - timedelta(days=10), datetime.min.time()) + timedelta(hours=10), "marketing")
            for name, cat, st, pct, s, e, canceled in PROMOTIONS]
    return copy_rows(conn, "ops_promotions",
                     ["name", "product_category", "customer_state", "discount_pct", "start_date", "end_date",
                      "canceled", "created_at", "created_by"], rows)


# --------------------------------------------------------------------------- 8. seller flags
def build_seller_flags(conn) -> int:
    """ops_seller_flags from a rule over REAL seller performance, 12 months up to as_of (no randomness):
      high_late_rate   : >= 30 delivered items and late rate > 20 %   (severity high if > 30 %)
      low_review_score : >= 30 reviewed orders and average score < 3.0 (severity high if < 2.5)
    created 2018-08-01 09:00 by the (simulated) monthly ops monitor; status open."""
    df = frame(conn, f"""
        WITH win AS (SELECT * FROM fact_order_items
                     WHERE is_valid_sale AND order_date > '{AS_OF - timedelta(days=365)}' AND order_date <= '{AS_OF}'),
        late AS (SELECT seller_id, COUNT(is_late) AS delivered, AVG(is_late) AS late_rate FROM win GROUP BY 1),
        rv AS (SELECT s.seller_id, COUNT(*) AS n_reviews, AVG(r.review_score) AS avg_score
               FROM (SELECT DISTINCT order_id, seller_id FROM win) s JOIN fact_reviews r USING (order_id)
               GROUP BY 1)
        SELECT seller_id, delivered, late_rate, n_reviews, avg_score
        FROM late FULL JOIN rv USING (seller_id) ORDER BY seller_id""")
    created = datetime(2018, 8, 1, 9, 0)
    rows = []
    for r in df.itertuples(index=False):
        if r.delivered and r.delivered >= 30 and r.late_rate is not None and float(r.late_rate) > 0.20:
            lr = float(r.late_rate)
            rows.append((r.seller_id, "high_late_rate", "high" if lr > 0.30 else "medium", "open",
                         f"Late rate {lr:.1%} on {int(r.delivered)} delivered items (Sep 2017 - Aug 2018)",
                         created, "ops_monitor"))
        if r.n_reviews and r.n_reviews >= 30 and r.avg_score is not None and float(r.avg_score) < 3.0:
            sc = float(r.avg_score)
            rows.append((r.seller_id, "low_review_score", "high" if sc < 2.5 else "medium", "open",
                         f"Average review score {sc:.2f} on {int(r.n_reviews)} reviewed orders (Sep 2017 - Aug 2018)",
                         created, "ops_monitor"))
    return copy_rows(conn, "ops_seller_flags",
                     ["seller_id", "reason", "severity", "status", "note", "created_at", "created_by"], rows)


# --------------------------------------------------------------------------- main
def main() -> None:
    load_env()
    url = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
    t0 = time.time()
    with psycopg.connect(url) as conn:
        run_sql(conn, SQL / "20_synthetic_schema.sql")
        rev, orders = monthly_actuals(conn)
        counts = {
            "syn_monthly_sales_targets": build_targets(conn, rev, orders),
            "syn_delivery_sla_rules": build_sla(conn),
            "syn_monthly_sales_forecasts": build_forecasts(conn, rev),
            **build_inventory(conn),
            "ops_support_tickets": build_tickets(conn),
            "ops_review_replies": build_replies(conn),
            "ops_promotions": build_promotions(conn),
            "ops_seller_flags": build_seller_flags(conn),
        }
        run_sql(conn, SQL / "30_roles_and_grants.sql")
        conn.commit()
    for t, k in counts.items():
        print(f"{t:30s} {k:>9,}")
    print(f"done in {time.time() - t0:.1f}s (seed {SEED})")


if __name__ == "__main__":
    main()
