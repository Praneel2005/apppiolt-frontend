"""Summary APIs: counts, totals and distributions per entity, optionally grouped. They feed the KPI strips and charts
of the operations pages, and answer "how many ... by ..." questions for the agent.

Every endpoint takes the same filters as its entity's search API (so a page's filter bar drives both the table and
its charts) plus `group_by`, and returns {items: [...], group_by, source}. With group_by=none there is one row of totals.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (Params, Where, check_enum, check_enum_list, date_range, order_by, resolve_ref,
                            rows_json, source)
from backend.config import as_of_date
from backend.db import ro
from backend.routes.products import PRODUCT_SQL, _window
from backend.routes.sellers import SELLER_SQL, _period

router = APIRouter()


def _run(conn, select: str, frm: str, w: Where, group: tuple[str, str] | None, params: dict | None = None) -> list[dict]:
    """SELECT [group expr AS alias,] metrics FROM frm [WHERE] [GROUP BY]."""
    sel = f"{group[0]} AS {group[1]}, " if group else ""
    grp = " GROUP BY 1" if group else ""  # by position: valid for any expression, including CASE and subqueries
    return conn.execute(f"SELECT {sel}{select} FROM {frm}{w.sql()}{grp}", {**w.params, **(params or {})}).fetchall()


def _sorted(rows: list[dict], key: str | None, order: list[str] | None = None, desc_by: str | None = None) -> list[dict]:
    if not key:
        return rows
    if order:
        pos = {v: i for i, v in enumerate(order)}
        return sorted(rows, key=lambda r: pos.get(r[key], 99))
    if desc_by:
        return sorted(rows, key=lambda r: -(float(r[desc_by] or 0)))
    return sorted(rows, key=lambda r: (r[key] is None, r[key]))


def _out(rows, group_by, tables, layers, note=None, **extra):
    return {"items": rows_json(rows), "group_by": group_by, "source": source(tables, layers, note), **extra}


# ------------------------------------------------------------------------------------ orders
BUCKET = ("CASE WHEN s.delivered_at IS NULL THEN 'not delivered' WHEN NOT s.is_late THEN 'on time' "
          "WHEN s.days_late <= 3 THEN '1-3 days' WHEN s.days_late <= 7 THEN '4-7 days' "
          "WHEN s.days_late <= 14 THEN '8-14 days' ELSE '15+ days' END")
BUCKETS = ["on time", "1-3 days", "4-7 days", "8-14 days", "15+ days", "not delivered"]
ORDER_GROUPS = {"status": "s.order_status", "customer_state": "s.customer_state", "customer_region": "s.customer_region",
                "month": "date_trunc('month', s.order_date)::date", "payment_type": "COALESCE(s.payment_type, 'unknown')",
                "review_score": "s.review_score", "days_late_bucket": BUCKET}


class OrdersSummaryQuery(Params):
    status: str | None = Field(None, description="Order status")
    customer_state: str | None = Field(None, description="Customer state codes, comma-separated")
    customer_region: str | None = Field(None, description="Customer macro-region")
    product_category: str | None = Field(None, description="Orders containing this product category")
    seller_id: str | None = Field(None, description="Orders containing items from this seller")
    date_from: date | None = Field(None, description="Purchased on or after")
    date_to: date | None = Field(None, description="Purchased on or before")
    preset: str | None = Field(None, description="Relative purchase-date range, e.g. last_month")
    late: bool | None = Field(None, description="true = delivered late, false = on time")
    overdue: bool | None = Field(None, description="true = undelivered and past the estimated date")
    has_open_ticket: bool | None = Field(None, description="true = has an open or in-progress ticket")
    group_by: Literal["none", "status", "customer_state", "customer_region", "month", "payment_type", "review_score",
                      "days_late_bucket"] = Field("none", description="How to group the totals")


@endpoint(router, "GET", "/api/orders/summary", api_id="orders.summary", entity="order",
          title="Order totals and distributions",
          description="Number of orders, order value, freight, late orders, late rate, average days late, overdue orders "
                      "and average review score, for the filtered orders, optionally grouped by status, state, region, "
                      "month, payment type, review score or how many days late. Use it for 'how many orders ...' and "
                      "'what share of orders ...' questions.",
          data_layers=["original", "derived", "synthetic"],
          returns="items[] of {<group>, orders, items_value, freight, late_orders, late_rate, avg_days_late, "
                  "overdue_orders, avg_review_score}",
          examples=["how many orders were late last month", "orders by status", "late orders by state",
                    "how late are late orders"])
def orders_summary(q: Annotated[OrdersSummaryQuery, Query()]):
    w = Where()
    with ro() as conn:
        if q.status:
            w.add("s.order_status = %(status)s", status=check_enum("order_status", q.status, "status"))
        if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
            w.add("s.customer_state = ANY(%(states)s)", states=states)
        if q.customer_region:
            w.add("s.customer_region = %(region)s", region=check_enum("customer_region", q.customer_region))
        if q.product_category:
            w.add("%(cat)s = ANY(s.categories)", cat=check_enum("product_category", q.product_category))
        if q.seller_id:
            w.add("%(seller)s = ANY(s.seller_ids)", seller=resolve_ref(conn, "raw_sellers", "seller_id", q.seller_id, "seller"))
        f, t = date_range(q.date_from, q.date_to, q.preset)
        if f:
            w.add("s.order_date >= %(f)s", f=f)
        if t:
            w.add("s.order_date <= %(t)s", t=t)
        if q.late is not None:
            w.add("s.is_late = %(late)s", late=q.late)
        if q.overdue is not None:
            w.add("(s.days_overdue > 0) = %(od)s" if q.overdue else "COALESCE(s.days_overdue, 0) = 0", od=q.overdue)
        if q.has_open_ticket is not None:
            w.add(("" if q.has_open_ticket else "NOT ") + "EXISTS (SELECT 1 FROM ops_support_tickets t WHERE "
                  "t.order_id = s.order_id AND t.status IN ('open', 'in_progress'))")
        grp = (ORDER_GROUPS[q.group_by], q.group_by) if q.group_by != "none" else None
        rows = _run(conn, """count(*) AS orders, round(sum(s.items_value)::numeric, 2) AS items_value,
            round(sum(s.freight)::numeric, 2) AS freight, count(*) FILTER (WHERE s.is_late) AS late_orders,
            round(avg(s.is_late::int)::numeric, 4) AS late_rate,
            round((avg(s.days_late) FILTER (WHERE s.is_late))::numeric, 2) AS avg_days_late,
            count(*) FILTER (WHERE s.days_overdue > 0) AS overdue_orders,
            round(avg(s.review_score)::numeric, 2) AS avg_review_score""", "core_order_summary s", w, grp)
    rows = (_sorted(rows, "days_late_bucket", BUCKETS) if q.group_by == "days_late_bucket"
            else _sorted(rows, q.group_by) if q.group_by in ("month", "review_score")
            else _sorted(rows, q.group_by, desc_by="orders") if q.group_by != "none" else rows)
    return _out(rows, q.group_by, ["core_order_summary", "ops_support_tickets"], ["original", "derived", "synthetic"],
                "order data is real Olist data; ticket-based filters use simulated tickets")


# ------------------------------------------------------------------------------------ tickets
TICKET_GROUPS = {"status": "t.status", "category": "t.category", "priority": "t.priority",
                 "day": "date_trunc('day', t.created_at)::date", "customer_state": "s.customer_state",
                 "assignee": "COALESCE(t.assignee, 'unassigned')"}


class TicketsSummaryQuery(Params):
    status: str | None = Field(None, description="Comma-separated ticket statuses")
    open_only: bool = Field(False, description="Only open or in-progress tickets")
    category: str | None = Field(None, description="Ticket category")
    priority: str | None = Field(None, description="Ticket priority")
    customer_state: str | None = Field(None, description="Customer state codes, comma-separated")
    date_from: date | None = Field(None, description="Created on or after")
    date_to: date | None = Field(None, description="Created on or before")
    preset: str | None = Field(None, description="Relative created-date range, e.g. last_30_days")
    group_by: Literal["none", "status", "category", "priority", "day", "customer_state", "assignee"] = Field("none")


@endpoint(router, "GET", "/api/tickets/summary", api_id="tickets.summary", entity="ticket",
          title="Ticket totals and distributions",
          description="Number of support tickets, open, urgent and unassigned tickets, resolved tickets and average "
                      "resolution time, optionally grouped by status, category, priority, day, state or assignee. "
                      "Use it for 'how many tickets ...' questions and ticket trends.",
          data_layers=["synthetic", "original"],
          returns="items[] of {<group>, tickets, open_tickets, urgent_tickets, unassigned, resolved_tickets, avg_resolution_hours}",
          examples=["how many tickets are open", "tickets by category", "tickets created per day last month",
                    "average resolution time"])
def tickets_summary(q: Annotated[TicketsSummaryQuery, Query()]):
    w = Where()
    if statuses := check_enum_list("ticket_status", q.status, "status"):
        w.add("t.status = ANY(%(st)s)", st=statuses)
    if q.open_only:
        w.add("t.status IN ('open', 'in_progress')")
    if q.category:
        w.add("t.category = %(cat)s", cat=check_enum("ticket_category", q.category, "category"))
    if q.priority:
        w.add("t.priority = %(pr)s", pr=check_enum("ticket_priority", q.priority, "priority"))
    if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
        w.add("s.customer_state = ANY(%(states)s)", states=states)
    f, t = date_range(q.date_from, q.date_to, q.preset)
    if f:
        w.add("t.created_at >= %(f)s", f=f)
    if t:
        w.add("t.created_at < %(t)s::date + 1", t=t)
    grp = (TICKET_GROUPS[q.group_by], q.group_by) if q.group_by != "none" else None
    with ro() as conn:
        rows = _run(conn, """count(*) AS tickets, count(*) FILTER (WHERE t.status IN ('open', 'in_progress')) AS open_tickets,
            count(*) FILTER (WHERE t.priority = 'urgent') AS urgent_tickets,
            count(*) FILTER (WHERE t.assignee IS NULL AND t.status IN ('open', 'in_progress')) AS unassigned,
            count(*) FILTER (WHERE t.status IN ('resolved', 'closed')) AS resolved_tickets,
            round((avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600.0)
                   FILTER (WHERE t.resolved_at IS NOT NULL))::numeric, 1) AS avg_resolution_hours""",
                    "ops_support_tickets t JOIN core_order_summary s USING (order_id)", w, grp)
    order = {"status": ["open", "in_progress", "resolved", "closed"], "priority": ["urgent", "high", "medium", "low"]}.get(q.group_by)
    rows = (_sorted(rows, q.group_by, order) if order else _sorted(rows, q.group_by) if q.group_by == "day"
            else _sorted(rows, q.group_by, desc_by="tickets") if q.group_by != "none" else rows)
    return _out(rows, q.group_by, ["ops_support_tickets", "core_order_summary"], ["synthetic", "original"])


# ------------------------------------------------------------------------------------ reviews
REVIEW_REPLIED = "EXISTS (SELECT 1 FROM ops_review_replies x WHERE x.review_id = r.review_id AND x.order_id = r.order_id)"
REVIEW_GROUPS = {"score": "r.review_score", "month": "date_trunc('month', r.review_creation_date)::date",
                 "customer_state": "s.customer_state", "has_reply": f"CASE WHEN {REVIEW_REPLIED} THEN 'replied' ELSE 'no reply' END"}


class ReviewsSummaryQuery(Params):
    score_max: int | None = Field(None, ge=1, le=5, description="Score at most")
    score_min: int | None = Field(None, ge=1, le=5, description="Score at least")
    customer_state: str | None = Field(None, description="Customer state codes, comma-separated")
    has_reply: bool | None = Field(None, description="true = the seller replied")
    date_from: date | None = Field(None, description="Review created on or after")
    date_to: date | None = Field(None, description="Review created on or before")
    preset: str | None = Field(None, description="Relative review-date range")
    group_by: Literal["none", "score", "month", "customer_state", "has_reply"] = Field("none")


@endpoint(router, "GET", "/api/reviews/summary", api_id="reviews.summary", entity="review",
          title="Review totals and distributions",
          description="Number of reviews, average score, negative reviews (score 1-2) and their share, reviews with "
                      "comments and replied reviews, optionally grouped by score, month, state or reply status. Use it "
                      "for 'how are customers rating us', score distribution and review trends.",
          data_layers=["original", "synthetic"],
          returns="items[] of {<group>, reviews, avg_score, negative_reviews, negative_share, with_comment, replied}",
          examples=["average review score", "review score distribution", "negative review share by state",
                    "how many negative reviews have no reply"])
def reviews_summary(q: Annotated[ReviewsSummaryQuery, Query()]):
    w = Where()
    if q.score_max:
        w.add("r.review_score <= %(smax)s", smax=q.score_max)
    if q.score_min:
        w.add("r.review_score >= %(smin)s", smin=q.score_min)
    if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
        w.add("s.customer_state = ANY(%(states)s)", states=states)
    if q.has_reply is not None:
        w.add(("" if q.has_reply else "NOT ") + REVIEW_REPLIED)
    f, t = date_range(q.date_from, q.date_to, q.preset)
    if f:
        w.add("r.review_creation_date >= %(f)s", f=f)
    if t:
        w.add("r.review_creation_date < %(t)s::date + 1", t=t)
    grp = (REVIEW_GROUPS[q.group_by], q.group_by) if q.group_by != "none" else None
    with ro() as conn:
        rows = _run(conn, f"""count(*) AS reviews, round(avg(r.review_score)::numeric, 2) AS avg_score,
            count(*) FILTER (WHERE r.review_score <= 2) AS negative_reviews,
            round(avg((r.review_score <= 2)::int)::numeric, 4) AS negative_share,
            count(*) FILTER (WHERE r.review_comment_message IS NOT NULL) AS with_comment,
            count(*) FILTER (WHERE {REVIEW_REPLIED}) AS replied""",
                    "raw_reviews r JOIN core_order_summary s USING (order_id)", w, grp)
    rows = (_sorted(rows, q.group_by) if q.group_by in ("score", "month") else
            _sorted(rows, q.group_by, desc_by="reviews") if q.group_by != "none" else rows)
    return _out(rows, q.group_by, ["raw_reviews", "core_order_summary", "ops_review_replies"], ["original", "synthetic"],
                "reviews are real Olist data; replies are simulated")


# ------------------------------------------------------------------------------------ stock
class StockSummaryQuery(Params):
    product_category: str | None = Field(None, description="Only this product category")
    below_reorder_point: bool | None = Field(None, description="true = only products at or below their reorder point")
    group_by: Literal["none", "category"] = Field("none")
    sort: str | None = Field(None, description="products, stock_units, inventory_value, below_reorder, needs_restock, "
                                                "units_sold_90d; '-' = descending (default -inventory_value)")
    limit: int = Field(30, ge=1, le=100, description="Groups to return")


@endpoint(router, "GET", "/api/inventory/summary", api_id="inventory.summary", entity="inventory",
          title="Stock totals and distributions",
          description="Number of listed products, units in stock, inventory value at cost, products below their reorder "
                      "point and needing a restock, units and revenue sold in 90 days and average days of cover, "
                      "optionally per product category. Use it for 'how much stock / inventory value / how many "
                      "products are low' questions.",
          data_layers=["synthetic", "original", "derived"],
          returns="items[] of {<category>, products, stock_units, inventory_value, below_reorder, needs_restock, "
                  "units_sold_90d, revenue_90d, avg_days_of_cover}",
          examples=["total inventory value", "which categories have the most low-stock products", "stock by category"])
def inventory_summary(q: Annotated[StockSummaryQuery, Query()]):
    w = Where().add("x.listed")
    if q.product_category:
        w.add("x.product_category = %(cat)s", cat=check_enum("product_category", q.product_category))
    if q.below_reorder_point is not None:
        w.add("COALESCE(x.below_reorder_point, false) = %(below)s", below=q.below_reorder_point)
    sorts = {k: k for k in ("products", "stock_units", "inventory_value", "below_reorder", "needs_restock", "units_sold_90d")}
    grp = ("x.product_category", "category") if q.group_by == "category" else None
    sel = """count(*) AS products, COALESCE(sum(x.stock_quantity), 0) AS stock_units,
        round(COALESCE(sum(x.inventory_value), 0)::numeric, 2) AS inventory_value,
        count(*) FILTER (WHERE x.below_reorder_point) AS below_reorder, count(*) FILTER (WHERE x.needs_restock) AS needs_restock,
        COALESCE(sum(x.units_sold_90d), 0) AS units_sold_90d, round(COALESCE(sum(x.revenue_90d), 0)::numeric, 2) AS revenue_90d,
        round(avg(x.days_of_cover)::numeric, 1) AS avg_days_of_cover"""
    with ro() as conn:
        rows = _run(conn, sel, f"({PRODUCT_SQL}) x", w, grp, _window())
    if q.group_by == "category":
        key = (q.sort or "-inventory_value")
        name = key.lstrip("-")
        if name not in sorts:
            from backend.common import ApiError
            raise ApiError(422, "invalid_sort", f"Cannot sort by '{name}'.", allowed=sorted(sorts))
        rows = sorted(rows, key=lambda r: float(r[name] or 0), reverse=key.startswith("-"))[:q.limit]
    return _out(rows, q.group_by, ["ops_product_listings", "fact_order_items", "ops_restock_orders"],
                ["synthetic", "original", "derived"], "stock, cost and price are simulated; units sold are real Olist data")


# ------------------------------------------------------------------------------------ restock orders and flags
class RestockSummaryQuery(Params):
    status: str | None = Field(None, description="placed, in_transit, received, canceled")
    group_by: Literal["none", "status", "month", "category"] = Field("none")


@endpoint(router, "GET", "/api/restock-orders/summary", api_id="restock_orders.summary", entity="restock_order",
          title="Restock order totals and distributions",
          description="Number of restock orders and units, open orders and open units, units received, optionally "
                      "grouped by status, month placed or product category.",
          data_layers=["synthetic"], returns="items[] of {<group>, orders, units, open_orders, open_units, received_units}",
          examples=["how many restock orders are open", "units on order", "restock orders per month"])
def restock_summary(q: Annotated[RestockSummaryQuery, Query()]):
    w = Where()
    if q.status:
        w.add("r.status = %(s)s", s=check_enum("restock_status", q.status, "status"))
    groups = {"status": "r.status", "month": "date_trunc('month', r.created_at)::date",
              "category": "COALESCE(m.product_category_name_english, p.product_category_name, 'unknown')"}
    grp = (groups[q.group_by], q.group_by) if q.group_by != "none" else None
    with ro() as conn:
        rows = _run(conn, """count(*) AS orders, COALESCE(sum(r.quantity), 0) AS units,
            count(*) FILTER (WHERE r.status IN ('placed', 'in_transit')) AS open_orders,
            COALESCE(sum(r.quantity) FILTER (WHERE r.status IN ('placed', 'in_transit')), 0) AS open_units,
            COALESCE(sum(r.quantity) FILTER (WHERE r.status = 'received'), 0) AS received_units""",
                    "ops_restock_orders r JOIN raw_products p USING (product_id) "
                    "LEFT JOIN core_category_map m ON m.product_category_name = p.product_category_name", w, grp)
    rows = (_sorted(rows, q.group_by, ["placed", "in_transit", "received", "canceled"]) if q.group_by == "status"
            else _sorted(rows, q.group_by) if q.group_by == "month"
            else _sorted(rows, q.group_by, desc_by="orders") if q.group_by != "none" else rows)
    return _out(rows, q.group_by, ["ops_restock_orders"], ["synthetic"])


class FlagsSummaryQuery(Params):
    status: str | None = Field(None, description="open, resolved, dismissed")
    severity: str | None = Field(None, description="low, medium, high")
    reason: str | None = Field(None, description="Flag reason")
    group_by: Literal["none", "reason", "severity", "status"] = Field("none")


@endpoint(router, "GET", "/api/seller-flags/summary", api_id="seller_flags.summary", entity="seller_flag",
          title="Seller flag totals and distributions",
          description="Number of seller performance flags, open flags and high-severity flags, optionally grouped by "
                      "reason, severity or status.",
          data_layers=["synthetic"], returns="items[] of {<group>, flags, open_flags, high_severity}",
          examples=["how many sellers are flagged", "flags by reason"])
def flags_summary(q: Annotated[FlagsSummaryQuery, Query()]):
    w = Where()
    if q.status:
        w.add("f.status = %(s)s", s=check_enum("flag_status", q.status, "status"))
    if q.severity:
        w.add("f.severity = %(v)s", v=check_enum("flag_severity", q.severity, "severity"))
    if q.reason:
        w.add("f.reason = %(r)s", r=check_enum("flag_reason", q.reason, "reason"))
    grp = (f"f.{q.group_by}", q.group_by) if q.group_by != "none" else None
    with ro() as conn:
        rows = _run(conn, """count(*) AS flags, count(*) FILTER (WHERE f.status = 'open') AS open_flags,
            count(*) FILTER (WHERE f.severity = 'high') AS high_severity""", "ops_seller_flags f", w, grp)
    order = {"severity": ["high", "medium", "low"], "status": ["open", "resolved", "dismissed"]}.get(q.group_by)
    rows = _sorted(rows, q.group_by, order) if order else _sorted(rows, q.group_by, desc_by="flags") if q.group_by != "none" else rows
    return _out(rows, q.group_by, ["ops_seller_flags"], ["synthetic"])


# ------------------------------------------------------------------------------------ sellers
class SellersSummaryQuery(Params):
    seller_state: str | None = Field(None, description="Seller state codes, comma-separated")
    flagged: bool | None = Field(None, description="true = sellers with an open flag")
    date_from: date | None = Field(None, description="Performance period start (default 12 months to as_of)")
    date_to: date | None = Field(None, description="Performance period end")
    preset: str | None = Field(None, description="Relative period")
    group_by: Literal["none", "seller_state"] = Field("none")


@endpoint(router, "GET", "/api/sellers/summary", api_id="sellers.summary", entity="seller",
          title="Seller totals and distributions",
          description="Number of selling sellers and flagged sellers, total revenue, items sold, item-weighted late "
                      "rate and review-weighted average score for a period, optionally per seller state.",
          data_layers=["original", "derived", "synthetic"],
          returns="items[] of {<group>, active_sellers, flagged_sellers, revenue, items_sold, avg_late_rate, avg_review_score}",
          examples=["how many sellers are active", "revenue by seller state", "what share of sellers are flagged"])
def sellers_summary(q: Annotated[SellersSummaryQuery, Query()]):
    f, t = _period(q.date_from, q.date_to, q.preset)
    w = Where()
    if states := check_enum_list("seller_state", q.seller_state, "seller_state"):
        w.add("x.seller_state = ANY(%(states)s)", states=states)
    if q.flagged is not None:
        w.add("(x.open_flags > 0) = %(fl)s", fl=q.flagged)
    grp = ("x.seller_state", "seller_state") if q.group_by == "seller_state" else None
    sel = """count(*) FILTER (WHERE x.items_sold > 0) AS active_sellers, count(*) FILTER (WHERE x.open_flags > 0) AS flagged_sellers,
        round(COALESCE(sum(x.revenue), 0)::numeric, 2) AS revenue, COALESCE(sum(x.items_sold), 0) AS items_sold,
        round((sum(x.late_rate * x.items_sold) FILTER (WHERE x.late_rate IS NOT NULL)
               / NULLIF(sum(x.items_sold) FILTER (WHERE x.late_rate IS NOT NULL), 0))::numeric, 4) AS avg_late_rate,
        round((sum(x.avg_review_score * x.reviews) FILTER (WHERE x.avg_review_score IS NOT NULL)
               / NULLIF(sum(x.reviews) FILTER (WHERE x.avg_review_score IS NOT NULL), 0))::numeric, 2) AS avg_review_score"""
    with ro() as conn:
        rows = _run(conn, sel, f"({SELLER_SQL}) x", w, grp, {"f": f, "t": t})
    if grp:
        rows = _sorted(rows, "seller_state", desc_by="revenue")
    return _out(rows, q.group_by, ["raw_sellers", "fact_order_items", "fact_reviews", "ops_seller_flags"],
                ["original", "derived", "synthetic"], "flags are simulated; performance is real Olist data",
                period={"from": f.isoformat(), "to": t.isoformat()})
