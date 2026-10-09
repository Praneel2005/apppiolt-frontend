"""Sellers (real Olist data) with performance, flags (synthetic) and funnel origin (real)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (ApiError, Body, Paging, Where, check_enum, check_enum_list, date_range, jsonable,
                            order_by, page, resolve_code, resolve_ref, rows_json, source)
from backend.config import as_of_date, sim_now
from backend.db import ro
from backend.writes import Changes, WriteOpts, run_write

router = APIRouter()

SELLER_SQL = """
WITH win AS (
  SELECT * FROM fact_order_items WHERE order_date >= %(f)s AND order_date <= %(t)s),
perf AS (
  SELECT seller_id, sum(price) FILTER (WHERE is_valid_sale) AS revenue,
         count(*) FILTER (WHERE is_valid_sale) AS items_sold,
         count(DISTINCT order_id) FILTER (WHERE is_valid_sale) AS orders,
         avg(is_late) AS late_rate, avg(delivery_days) AS avg_delivery_days,
         count(*) FILTER (WHERE order_status = 'canceled') AS canceled_items
  FROM win GROUP BY seller_id),
rv AS (
  SELECT s.seller_id, avg(r.review_score) AS avg_review_score, count(*) AS reviews
  FROM (SELECT DISTINCT order_id, seller_id FROM win) s JOIN fact_reviews r USING (order_id) GROUP BY 1),
flags AS (
  SELECT seller_id, count(*) AS open_flags, array_agg(reason ORDER BY reason) AS open_flag_reasons
  FROM ops_seller_flags WHERE status = 'open' GROUP BY 1)
SELECT se.seller_id, upper(left(se.seller_id, 8)) AS seller_ref, se.seller_city, se.seller_state,
       COALESCE(p.revenue, 0) AS revenue, COALESCE(p.items_sold, 0) AS items_sold, COALESCE(p.orders, 0) AS orders,
       round(p.late_rate::numeric, 4) AS late_rate, round(p.avg_delivery_days::numeric, 2) AS avg_delivery_days,
       round(rv.avg_review_score::numeric, 2) AS avg_review_score, COALESCE(rv.reviews, 0) AS reviews,
       COALESCE(f.open_flags, 0) AS open_flags, f.open_flag_reasons,
       (cd.mql_id IS NOT NULL) AS acquired_via_funnel
FROM raw_sellers se
LEFT JOIN perf p USING (seller_id)
LEFT JOIN rv USING (seller_id)
LEFT JOIN flags f USING (seller_id)
LEFT JOIN raw_closed_deals cd USING (seller_id)
"""
SORTS = {"revenue": "revenue", "items_sold": "items_sold", "orders": "orders", "late_rate": "late_rate",
         "avg_review_score": "avg_review_score", "avg_delivery_days": "avg_delivery_days", "open_flags": "open_flags"}


def _period(date_from, date_to, preset) -> tuple[date, date]:
    f, t = date_range(date_from, date_to, preset)
    a = as_of_date()
    return f or (a - timedelta(days=364)), t or a


class SellersQuery(Paging):
    seller_state: str | None = Field(None, description="Seller state codes, comma-separated")
    flagged: bool | None = Field(None, description="true = has an open performance flag")
    min_items_sold: int | None = Field(None, ge=0, description="Sold at least this many items in the period")
    acquired_via_funnel: bool | None = Field(None, description="true = joined through the marketing funnel")
    date_from: date | None = Field(None, description="Performance period start (default: 12 months to as_of)")
    date_to: date | None = Field(None, description="Performance period end")
    preset: str | None = Field(None, description="Relative period, e.g. last_quarter")
    sort: str | None = Field(None, description="revenue (default -revenue), items_sold, orders, late_rate, "
                                                "avg_review_score, avg_delivery_days, open_flags; '-' = descending")


@endpoint(router, "GET", "/api/sellers", api_id="sellers.search", entity="seller",
          title="Search sellers and their performance",
          description="Sellers with revenue, items and orders sold, late-delivery rate, average delivery days, "
                      "average review score and open performance flags for a period (default last 12 months). "
                      "Rank or filter by state, flags, volume or funnel origin.",
          data_layers=["original", "derived", "synthetic"],
          returns="items[] of sellers with performance figures; total; period",
          examples=["top sellers by revenue last quarter", "sellers with the worst late delivery rate",
                    "flagged sellers in SP", "sellers with at least 50 items and low reviews"])
def sellers_search(q: Annotated[SellersQuery, Query()]):
    f, t = _period(q.date_from, q.date_to, q.preset)
    w = Where()
    if states := check_enum_list("seller_state", q.seller_state, "seller_state"):
        w.add("x.seller_state = ANY(%(states)s)", states=states)
    if q.flagged is not None:
        w.add("(x.open_flags > 0) = %(fl)s", fl=q.flagged)
    if q.min_items_sold is not None:
        w.add("x.items_sold >= %(mi)s", mi=q.min_items_sold)
    if q.acquired_via_funnel is not None:
        w.add("x.acquired_via_funnel = %(af)s", af=q.acquired_via_funnel)
    base = f"SELECT * FROM ({SELLER_SQL}) x{w.sql()}"
    params = {"f": f, "t": t, **w.params}
    with ro() as conn:
        total = conn.execute(f"SELECT count(*) AS n FROM ({base}) c", params).fetchone()["n"]
        rows = conn.execute(f"{base}{order_by(q.sort, SORTS, '-revenue')} LIMIT %(limit)s OFFSET %(offset)s",
                            {**params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset,
                source(["raw_sellers", "fact_order_items", "fact_reviews", "ops_seller_flags", "raw_closed_deals"],
                       ["original", "derived", "synthetic"], "flags are synthetic; performance is real Olist data"),
                period={"from": f.isoformat(), "to": t.isoformat()})


@endpoint(router, "GET", "/api/sellers/{seller_ref}", api_id="sellers.get", entity="seller",
          title="Get seller details",
          description="One seller: location, 12-month performance, monthly revenue, top categories, all flags and, "
                      "if acquired through the marketing funnel, the lead origin and deal details.",
          data_layers=["original", "derived", "synthetic"],
          returns="seller, monthly[], top_categories[], flags[], funnel",
          examples=["how is seller 3442F8959 doing", "why was this seller flagged"])
def sellers_get(seller_ref: str):
    f, t = _period(None, None, None)
    with ro() as conn:
        sid = resolve_ref(conn, "raw_sellers", "seller_id", seller_ref, "seller")
        seller = conn.execute(f"SELECT * FROM ({SELLER_SQL}) x WHERE x.seller_id = %(s)s",
                              {"f": f, "t": t, "s": sid}).fetchone()
        monthly = conn.execute("""
            SELECT order_month, round((sum(price) FILTER (WHERE is_valid_sale))::numeric, 2) AS revenue,
                   count(*) FILTER (WHERE is_valid_sale) AS items_sold, round(avg(is_late)::numeric, 4) AS late_rate
            FROM fact_order_items WHERE seller_id = %(s)s AND order_date <= %(t)s GROUP BY 1 ORDER BY 1""",
                               {"s": sid, "t": t}).fetchall()
        cats = conn.execute("""
            SELECT product_category, round((sum(price) FILTER (WHERE is_valid_sale))::numeric, 2) AS revenue,
                   count(*) AS items FROM fact_order_items WHERE seller_id = %(s)s
            GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 5""", {"s": sid}).fetchall()
        flags = conn.execute("SELECT code, reason, severity, status, note, created_at, created_by "
                             "FROM ops_seller_flags WHERE seller_id = %(s)s ORDER BY id DESC", {"s": sid}).fetchall()
        funnel = conn.execute("""
            SELECT d.mql_id, l.origin, l.first_contact_date, d.won_date, d.business_segment, d.lead_type,
                   d.business_type, d.declared_monthly_revenue
            FROM raw_closed_deals d LEFT JOIN raw_marketing_qualified_leads l USING (mql_id)
            WHERE d.seller_id = %(s)s""", {"s": sid}).fetchone()
    return {"seller": jsonable(seller), "period": {"from": f.isoformat(), "to": t.isoformat()},
            "monthly": rows_json(monthly), "top_categories": rows_json(cats), "flags": rows_json(flags),
            "funnel": jsonable(funnel),
            "source": source(["raw_sellers", "fact_order_items", "ops_seller_flags", "raw_closed_deals"],
                             ["original", "derived", "synthetic"], "flags are synthetic")}


class FlagCreate(Body):
    reason: Literal["high_late_rate", "low_review_score", "high_cancellation", "policy_violation", "other"]
    severity: Literal["low", "medium", "high"] = "medium"
    note: str | None = Field(None, max_length=500, description="Evidence for the flag")


@endpoint(router, "POST", "/api/sellers/{seller_ref}/flags", api_id="sellers.flag", entity="seller",
          title="Flag a seller",
          description="Raise a performance flag on a seller (late deliveries, low reviews, cancellations, policy). "
                      "Refused if the seller already has an open flag for the same reason.",
          data_layers=["synthetic"], returns="the new flag (code FLG-...)",
          examples=["flag seller 3442F895 for late deliveries", "flag the worst-rated seller in RJ"])
def sellers_flag(seller_ref: str, body: FlagCreate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        sid = resolve_ref(conn, "raw_sellers", "seller_id", seller_ref, "seller")
        dup = conn.execute("SELECT code FROM ops_seller_flags WHERE seller_id = %(s)s AND reason = %(r)s "
                           "AND status = 'open'", {"s": sid, "r": body.reason}).fetchone()
        if dup:
            raise ApiError(409, "duplicate_flag", f"{dup['code']} is already open for this reason.", existing=dup["code"])
        return ch.insert("ops_seller_flags", {"seller_id": sid, "reason": body.reason, "severity": body.severity,
                                              "status": "open", "note": body.note, "created_at": sim_now(),
                                              "created_by": opts.actor})

    return run_write("sellers.flag", opts, {"seller_ref": seller_ref, **body.model_dump()}, apply)


class FlagUpdate(Body):
    status: Literal["open", "resolved", "dismissed"]
    note: str | None = Field(None, max_length=500)


@endpoint(router, "PATCH", "/api/seller-flags/{code}", api_id="seller_flags.update", entity="seller_flag",
          title="Resolve or dismiss a seller flag",
          description="Change a seller flag's status to resolved, dismissed or open again.",
          data_layers=["synthetic"], returns="the flag before/after",
          examples=["resolve FLG-00012", "dismiss this flag"])
def seller_flags_update(code: str, body: FlagUpdate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        f = resolve_code(conn, "ops_seller_flags", code, "seller flag", "FLG")
        values = {"status": body.status}
        if body.note:
            values["note"] = body.note
        return ch.update("ops_seller_flags", f["id"], values)

    return run_write("seller_flags.update", opts, {"code": code, **body.model_dump()}, apply)


class FlagsQuery(Paging):
    status: str | None = Field(None, description="open, resolved, dismissed")
    reason: str | None = Field(None, description="high_late_rate, low_review_score, ...")
    severity: str | None = Field(None, description="low, medium, high")


@endpoint(router, "GET", "/api/seller-flags", api_id="seller_flags.search", entity="seller_flag",
          title="List seller flags",
          description="Performance flags on sellers with reason, severity, status and evidence note.",
          data_layers=["synthetic"], returns="items[] of flags with seller state; total",
          examples=["open seller flags", "high severity flags"])
def seller_flags_search(q: Annotated[FlagsQuery, Query()]):
    w = Where()
    if q.status:
        w.add("f.status = %(s)s", s=check_enum("flag_status", q.status, "status"))
    if q.reason:
        w.add("f.reason = %(r)s", r=check_enum("flag_reason", q.reason, "reason"))
    if q.severity:
        w.add("f.severity = %(v)s", v=check_enum("flag_severity", q.severity, "severity"))
    with ro() as conn:
        total = conn.execute(f"SELECT count(*) AS n FROM ops_seller_flags f{w.sql()}", w.params).fetchone()["n"]
        rows = conn.execute(f"""
            SELECT f.code, f.seller_id, upper(left(f.seller_id, 8)) AS seller_ref, s.seller_state, f.reason,
                   f.severity, f.status, f.note, f.created_at, f.created_by
            FROM ops_seller_flags f JOIN raw_sellers s USING (seller_id){w.sql()}
            ORDER BY f.id DESC LIMIT %(limit)s OFFSET %(offset)s""",
                            {**w.params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset, source(["ops_seller_flags", "raw_sellers"], ["synthetic", "original"]))
