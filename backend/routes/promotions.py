"""Promotions (synthetic, writable) and their before/during sales comparison (real sales)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (ApiError, Body, Paging, Where, check_enum, jsonable, page, resolve_code, rows_json,
                            source)
from backend.config import sim_now
from backend.db import ro
from backend.writes import Changes, WriteOpts, run_write

router = APIRouter()

STATUS_SQL = """CASE WHEN p.canceled THEN 'canceled' WHEN p.end_date < %(today)s THEN 'ended'
                     WHEN p.start_date > %(today)s THEN 'scheduled' ELSE 'active' END"""
PROMO_COLS = f"""p.code, p.name, p.product_category, p.customer_state, p.discount_pct, p.start_date, p.end_date,
  {STATUS_SQL} AS status, p.created_at, p.created_by"""


class PromotionsQuery(Paging):
    status: str | None = Field(None, description="scheduled, active, ended, canceled (relative to today)")
    product_category: str | None = Field(None, description="Only promotions for this category")
    customer_state: str | None = Field(None, description="Only promotions for this state")


@endpoint(router, "GET", "/api/promotions", api_id="promotions.search", entity="promotion",
          title="List promotions",
          description="Discount campaigns with category, state, discount, dates and status "
                      "(scheduled, active, ended, canceled).",
          data_layers=["synthetic"], returns="items[] of promotions; total",
          examples=["which promotions are running now", "past promotions on health_beauty"])
def promotions_search(q: Annotated[PromotionsQuery, Query()]):
    today = sim_now().date()
    w = Where()
    if q.status:
        w.add(f"({STATUS_SQL}) = %(st)s", st=check_enum("promotion_status", q.status, "status"))
    if q.product_category:
        w.add("p.product_category = %(cat)s", cat=check_enum("product_category", q.product_category))
    if q.customer_state:
        w.add("p.customer_state = %(state)s", state=check_enum("customer_state", q.customer_state))
    params = {**w.params, "today": today}
    with ro() as conn:
        total = conn.execute(f"SELECT count(*) AS n FROM ops_promotions p{w.sql()}", params).fetchone()["n"]
        rows = conn.execute(f"SELECT {PROMO_COLS} FROM ops_promotions p{w.sql()} ORDER BY p.start_date DESC "
                            f"LIMIT %(limit)s OFFSET %(offset)s",
                            {**params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset, source(["ops_promotions"], ["synthetic"]))


@endpoint(router, "GET", "/api/promotions/{code}/performance", api_id="promotions.performance", entity="promotion",
          title="Promotion sales comparison",
          description="Real revenue, orders and items in the promotion's category/state during the promotion versus "
                      "the same number of days just before it. Promotions are simulated, so this shows what sales "
                      "did in that window, not what the promotion caused.",
          data_layers=["synthetic", "derived"], returns="promotion, during{}, before{}, change_pct{}",
          examples=["how did Black Friday 2017 do", "did the Mother's Day promotion lift sales"])
def promotions_performance(code: str):
    with ro() as conn:
        p = resolve_code(conn, "ops_promotions", code, "promotion", "PRM")
        days = (p["end_date"] - p["start_date"]).days + 1
        before = (p["start_date"] - timedelta(days=days), p["start_date"] - timedelta(days=1))
        w = Where()
        if p["product_category"]:
            w.add("product_category = %(cat)s", cat=p["product_category"])
        if p["customer_state"]:
            w.add("customer_state = %(st)s", st=p["customer_state"])
        extra = (" AND " + " AND ".join(w.parts)) if w.parts else ""

        def window(f: date, t: date) -> dict:
            return conn.execute(f"""
                SELECT round((sum(price) FILTER (WHERE is_valid_sale))::numeric, 2) AS revenue,
                       count(DISTINCT order_id) FILTER (WHERE is_valid_sale) AS orders,
                       count(*) FILTER (WHERE is_valid_sale) AS items
                FROM fact_order_items WHERE order_date BETWEEN %(f)s AND %(t)s{extra}""",
                                {**w.params, "f": f, "t": t}).fetchone()

        during, prior = window(p["start_date"], p["end_date"]), window(*before)
    change = {k: (round(100 * (float(during[k] or 0) - float(prior[k] or 0)) / float(prior[k]), 1)
                  if prior[k] else None) for k in ("revenue", "orders", "items")}
    return {"promotion": jsonable({k: p[k] for k in ("code", "name", "product_category", "customer_state",
                                                     "discount_pct", "start_date", "end_date")}),
            "during": {"from": p["start_date"].isoformat(), "to": p["end_date"].isoformat(), **jsonable(during)},
            "before": {"from": before[0].isoformat(), "to": before[1].isoformat(), **jsonable(prior)},
            "change_pct": change,
            "source": source(["ops_promotions", "fact_order_items"], ["synthetic", "derived"],
                             "sales are real Olist data; the promotion is simulated and did not cause them")}


class PromotionCreate(Body):
    name: str = Field(..., min_length=3, max_length=100)
    product_category: str | None = Field(None, description="Category (English name); empty = all categories")
    customer_state: str | None = Field(None, description="State code; empty = all states")
    discount_pct: float = Field(..., gt=0, le=70, description="Discount in percent (max 70)")
    start_date: date = Field(..., description="First day (today or later)")
    end_date: date = Field(..., description="Last day (on or after start_date)")
    allow_overlap: bool = Field(False, description="Allow overlapping an existing promotion for the same scope")


@endpoint(router, "POST", "/api/promotions", api_id="promotions.create", entity="promotion",
          title="Create a promotion",
          description="Schedule a discount campaign for a category and/or state. Start must be today or later; "
                      "refused if it overlaps another promotion for the same category and state.",
          data_layers=["synthetic"], returns="the new promotion (code PRM-...)",
          examples=["create a 15% promotion for toys next week", "run a promotion for the weakest category"])
def promotions_create(body: PromotionCreate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        today = sim_now().date()
        cat = check_enum("product_category", body.product_category, "product_category")
        st = check_enum("customer_state", body.customer_state, "customer_state")
        if body.start_date < today:
            raise ApiError(422, "start_in_past", f"start_date {body.start_date} is before today ({today}).",
                           today=today.isoformat())
        if body.end_date < body.start_date:
            raise ApiError(422, "invalid_date_range", "end_date is before start_date.")
        if not body.allow_overlap:
            clash = conn.execute("""
                SELECT code, name FROM ops_promotions WHERE NOT canceled
                  AND product_category IS NOT DISTINCT FROM %(c)s AND customer_state IS NOT DISTINCT FROM %(s)s
                  AND start_date <= %(e)s AND end_date >= %(b)s LIMIT 1""",
                                 {"c": cat, "s": st, "b": body.start_date, "e": body.end_date}).fetchone()
            if clash:
                raise ApiError(409, "overlapping_promotion", f"Overlaps {clash['code']} ({clash['name']}).",
                               existing=clash["code"], hint="change the dates or set allow_overlap=true")
        return ch.insert("ops_promotions", {
            "name": body.name, "product_category": cat, "customer_state": st, "discount_pct": body.discount_pct,
            "start_date": body.start_date, "end_date": body.end_date, "canceled": False,
            "created_at": sim_now(), "created_by": opts.actor})

    return run_write("promotions.create", opts, body.model_dump(), apply)


class PromotionUpdate(Body):
    end_date: date | None = Field(None, description="New last day")
    discount_pct: float | None = Field(None, gt=0, le=70)
    cancel: bool | None = Field(None, description="true = cancel the promotion")


@endpoint(router, "PATCH", "/api/promotions/{code}", api_id="promotions.update", entity="promotion",
          title="Change or cancel a promotion",
          description="Extend/shorten, change the discount of, or cancel a scheduled or active promotion. "
                      "Ended promotions cannot be changed.",
          data_layers=["synthetic"], returns="the promotion before/after",
          examples=["cancel PRM-0013", "extend the furniture clearance to 15 September"])
def promotions_update(code: str, body: PromotionUpdate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        p = resolve_code(conn, "ops_promotions", code, "promotion", "PRM")
        today = sim_now().date()
        if p["canceled"] or p["end_date"] < today:
            raise ApiError(409, "promotion_closed", f"{p['code']} has ended or was canceled; it cannot be changed.")
        values = {}
        if body.end_date:
            if body.end_date < max(p["start_date"], today):
                raise ApiError(422, "invalid_end_date", "end_date must be on/after the start date and today.")
            values["end_date"] = body.end_date
        if body.discount_pct:
            values["discount_pct"] = body.discount_pct
        if body.cancel:
            values["canceled"] = True
        if not values:
            raise ApiError(409, "nothing_to_change", "No field would change.")
        return ch.update("ops_promotions", p["id"], values)

    return run_write("promotions.update", opts, {"code": code, **body.model_dump()}, apply)
