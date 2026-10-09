"""Plan vs actual (all plan tables are synthetic; actuals are real): targets, delivery SLA, forecasts."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (ApiError, Params, Where, check_enum, check_enum_list, date_range, order_by, rows_json,
                            source)
from backend.config import as_of_date
from backend.db import ro

router = APIRouter()


def _period(f: date | None, t: date | None, default_from: date, default_to: date) -> tuple[date, date]:
    return f or default_from, t or default_to


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _group_clause(group_by: str, exprs: dict[str, str]) -> tuple[str, str, str]:
    """-> (select list prefix, GROUP BY clause, order prefix) for group_by in exprs ('none' = no grouping)."""
    if group_by == "none":
        return "", "", ""
    return f"{exprs[group_by]} AS {group_by}, ", f" GROUP BY {exprs[group_by]}", group_by


# ------------------------------------------------------------------------------------ targets
class TargetsQuery(Params):
    date_from: date | None = Field(None, description="First month in range (any day of the month)")
    date_to: date | None = Field(None, description="Last day of range")
    preset: str | None = Field(None, description="Relative range, e.g. last_quarter, month:2018-07 (default last_quarter)")
    customer_state: str | None = Field(None, description="State codes, comma-separated")
    group_by: Literal["customer_state", "month", "none"] = Field("customer_state", description="How to group")
    below_target: bool | None = Field(None, description="true = only groups under their revenue target")
    sort: str | None = Field(None, description="attainment_pct (default, worst first), variance, revenue_actual, "
                                                "revenue_target; '-' = descending")
    limit: int = Field(50, ge=1, le=200)


@endpoint(router, "GET", "/api/targets/performance", api_id="targets.performance", entity="target",
          title="Sales vs target",
          description="Actual revenue and orders versus the simulated monthly targets, per state or month, with "
                      "attainment percent and variance. Find states that missed or beat their target. Targets are "
                      "simulated plans, actuals are real Olist sales.",
          data_layers=["synthetic", "derived"],
          returns="rows[] of {group, revenue_actual, revenue_target, attainment_pct, variance, orders_actual, order_target}",
          examples=["which states missed their sales target last quarter", "actual vs target revenue for SP in July",
                    "which state beat its target by the most"])
def targets_performance(q: Annotated[TargetsQuery, Query()]):
    f, t = date_range(q.date_from, q.date_to, q.preset or ("last_quarter" if not (q.date_from or q.date_to) else None))
    a = as_of_date()
    f, t = _period(f, t, a - timedelta(days=90), a)
    t = min(t, a)  # no actuals after the dataset's as_of date
    mf, mt = _month_start(f), _month_start(t)
    w = Where()
    if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
        w.add("t.customer_state = ANY(%(states)s)", states=states)
    sel, grp, _ = _group_clause(q.group_by, {"customer_state": "t.customer_state", "month": "t.target_month"})
    where = (" AND " + " AND ".join(w.parts)) if w.parts else ""
    having = ""
    if q.below_target is not None:
        having = f" HAVING (COALESCE(sum(a.rev), 0) < sum(t.revenue_target)) = {'true' if q.below_target else 'false'}"
    inner = f"""
        WITH a AS (SELECT order_month AS m, customer_state AS s,
                          sum(price) FILTER (WHERE is_valid_sale) AS rev,
                          count(DISTINCT order_id) FILTER (WHERE is_valid_sale) AS ord
                   FROM fact_order_items WHERE order_month BETWEEN %(mf)s AND %(mt)s GROUP BY 1, 2)
        SELECT {sel}round(COALESCE(sum(a.rev), 0)::numeric, 2) AS revenue_actual,
               round(sum(t.revenue_target), 2) AS revenue_target,
               round((100 * COALESCE(sum(a.rev), 0) / sum(t.revenue_target))::numeric, 1) AS attainment_pct,
               round((COALESCE(sum(a.rev), 0) - sum(t.revenue_target))::numeric, 2) AS variance,
               COALESCE(sum(a.ord), 0)::bigint AS orders_actual, sum(t.order_target)::bigint AS order_target,
               count(*) AS state_months
        FROM syn_monthly_sales_targets t LEFT JOIN a ON a.m = t.target_month AND a.s = t.customer_state
        WHERE t.scenario_name = 'baseline' AND t.target_month BETWEEN %(mf)s AND %(mt)s{where}{grp}{having}"""
    sorts = {"attainment_pct": "attainment_pct", "variance": "variance", "revenue_actual": "revenue_actual",
             "revenue_target": "revenue_target"}
    with ro() as conn:
        rows = conn.execute(f"SELECT * FROM ({inner}) g{order_by(q.sort, sorts, 'attainment_pct')} LIMIT %(limit)s",
                            {**w.params, "mf": mf, "mt": mt, "limit": q.limit}).fetchall()
    return {"items": rows_json(rows), "group_by": q.group_by,
            "period": {"from": mf.isoformat(), "to": t.isoformat()},
            "source": source(["syn_monthly_sales_targets", "fact_order_items"], ["synthetic", "derived"],
                             "targets are simulated (baseline scenario, no real company targets); "
                             "actuals are real valid-sale revenue")}


# ------------------------------------------------------------------------------------ SLA
class SlaQuery(Params):
    date_from: date | None = Field(None, description="Orders purchased on or after")
    date_to: date | None = Field(None, description="Orders purchased on or before")
    preset: str | None = Field(None, description="Relative range (default last_12_months)")
    customer_state: str | None = Field(None, description="Destination states, comma-separated")
    customer_region: str | None = Field(None, description="Destination region")
    group_by: Literal["customer_state", "customer_region", "month", "none"] = Field("customer_state")
    min_orders: int = Field(30, ge=1, description="Ignore groups with fewer delivered orders")
    sort: str | None = Field(None, description="breach_rate_pct (default, worst first), avg_delivery_days, "
                                                "delivered_orders; '-' = ascending/descending")
    limit: int = Field(50, ge=1, le=200)


@endpoint(router, "GET", "/api/sla/performance", api_id="sla.performance", entity="sla",
          title="Delivery SLA breaches",
          description="Delivered orders whose delivery time exceeded the simulated delivery SLA of their "
                      "destination state (rule in force on the purchase date), by state, region or month. Also shows "
                      "Olist's own late rate (delivered after the estimated date) for contrast; the two differ.",
          data_layers=["synthetic", "original", "derived"],
          returns="rows[] of {group, delivered_orders, breaches, breach_rate_pct, avg_delivery_days, "
                  "sla_threshold_days, olist_late_rate_pct}",
          examples=["which states have the highest SLA breach rate", "how many deliveries breached the SLA last quarter"])
def sla_performance(q: Annotated[SlaQuery, Query()]):
    f, t = date_range(q.date_from, q.date_to, q.preset or ("last_12_months" if not (q.date_from or q.date_to) else None))
    w = Where().add("s.delivered_at IS NOT NULL")
    if f:
        w.add("s.order_date >= %(f)s", f=f)
    if t:
        w.add("s.order_date <= %(t)s", t=t)
    if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
        w.add("s.customer_state = ANY(%(states)s)", states=states)
    if q.customer_region:
        w.add("s.customer_region = %(region)s", region=check_enum("customer_region", q.customer_region))
    sel, grp, _ = _group_clause(q.group_by, {"customer_state": "s.customer_state", "customer_region": "s.customer_region",
                                             "month": "date_trunc('month', s.order_date)::date"})
    sorts = {"breach_rate_pct": "breach_rate_pct", "avg_delivery_days": "avg_delivery_days",
             "delivered_orders": "delivered_orders", "olist_late_rate_pct": "olist_late_rate_pct"}
    inner = f"""
        SELECT {sel}count(*) AS delivered_orders,
               count(*) FILTER (WHERE s.delivery_days > r.sla_threshold_days) AS breaches,
               round((100.0 * count(*) FILTER (WHERE s.delivery_days > r.sla_threshold_days) / count(*))::numeric, 2)
                 AS breach_rate_pct,
               round(avg(s.delivery_days)::numeric, 2) AS avg_delivery_days,
               round(avg(r.expected_delivery_days), 1) AS expected_delivery_days,
               round(avg(r.sla_threshold_days), 1) AS sla_threshold_days,
               round((100.0 * avg(s.is_late::int))::numeric, 2) AS olist_late_rate_pct
        FROM core_order_summary s JOIN syn_delivery_sla_rules r
          ON r.destination_state = s.customer_state AND r.scenario_name = 'baseline'
         AND s.order_date BETWEEN r.effective_from AND COALESCE(r.effective_to, DATE '9999-12-31')
        {w.sql()}{grp} HAVING count(*) >= %(min)s"""
    with ro() as conn:
        rows = conn.execute(f"SELECT * FROM ({inner}) g{order_by(q.sort, sorts, '-breach_rate_pct')} LIMIT %(limit)s",
                            {**w.params, "min": q.min_orders, "limit": q.limit}).fetchall()
    return {"items": rows_json(rows), "group_by": q.group_by,
            "period": {"from": f.isoformat() if f else None, "to": t.isoformat() if t else None},
            "source": source(["syn_delivery_sla_rules", "core_order_summary"], ["synthetic", "original", "derived"],
                             "SLA rules are simulated; delivery times and olist_late_rate_pct are real Olist data")}


# ------------------------------------------------------------------------------------ forecasts
class ForecastQuery(Params):
    date_from: date | None = Field(None, description="First month in range")
    date_to: date | None = Field(None, description="Last day of range")
    preset: str | None = Field(None, description="Relative range (default year:2018)")
    scenario: str = Field("baseline", description="baseline, optimistic or conservative")
    customer_state: str | None = Field(None, description="State codes, comma-separated")
    group_by: Literal["month", "customer_state", "none"] = Field("month")
    limit: int = Field(60, ge=1, le=300)


@endpoint(router, "GET", "/api/forecasts/comparison", api_id="forecasts.comparison", entity="forecast",
          title="Forecast vs actual revenue",
          description="Model-generated revenue forecasts compared with actual revenue, by month or state, for the "
                      "baseline, optimistic or conservative scenario. Months after the data ends (Sep-Dec 2018) have "
                      "a forecast but no actual. Forecasts are estimates, not sales.",
          data_layers=["synthetic", "derived"],
          returns="rows[] of {group, predicted_revenue, actual_revenue, error_pct, has_actual}",
          examples=["how accurate was the forecast for 2018", "what is the revenue forecast for the rest of 2018",
                    "which states are forecast to grow", "optimistic vs conservative forecast"])
def forecasts_comparison(q: Annotated[ForecastQuery, Query()]):
    scenario = check_enum("forecast_scenario", q.scenario, "scenario")
    f, t = date_range(q.date_from, q.date_to, q.preset or ("year:2018" if not (q.date_from or q.date_to) else None))
    a = as_of_date()
    f, t = _period(f, t, date(2018, 1, 1), date(2018, 12, 31))
    mf, mt = _month_start(f), _month_start(t)
    last_actual = _month_start(a)
    w = Where()
    if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
        w.add("fc.customer_state = ANY(%(states)s)", states=states)
    sel, grp, _ = _group_clause(q.group_by, {"month": "fc.forecast_month", "customer_state": "fc.customer_state"})
    where = (" AND " + " AND ".join(w.parts)) if w.parts else ""
    inner = f"""
        WITH act AS (SELECT order_month AS m, customer_state AS s, sum(price) FILTER (WHERE is_valid_sale) AS rev
                     FROM fact_order_items WHERE order_month BETWEEN %(mf)s AND %(last)s GROUP BY 1, 2)
        SELECT {sel}round(sum(fc.predicted_revenue), 2) AS predicted_revenue,
               round(sum(act.rev) FILTER (WHERE fc.forecast_month <= %(last)s)::numeric, 2) AS actual_revenue,
               round(sum(fc.predicted_revenue) FILTER (WHERE fc.forecast_month <= %(last)s), 2) AS predicted_for_actual_months,
               bool_or(fc.forecast_month <= %(last)s) AS has_actual,
               min(fc.model_version) AS model_version, min(fc.forecast_method) AS forecast_method
        FROM syn_monthly_sales_forecasts fc LEFT JOIN act ON act.m = fc.forecast_month AND act.s = fc.customer_state
        WHERE fc.scenario_name = %(sc)s AND fc.forecast_month BETWEEN %(mf)s AND %(mt)s{where}{grp}"""
    with ro() as conn:
        rows = conn.execute(f"""SELECT g.*, round((100 * (g.predicted_for_actual_months - g.actual_revenue)
                                    / NULLIF(g.actual_revenue, 0))::numeric, 1) AS error_pct
                                FROM ({inner}) g ORDER BY 1 LIMIT %(limit)s""",
                            {**w.params, "sc": scenario, "mf": mf, "mt": mt, "last": last_actual,
                             "limit": q.limit}).fetchall()
    return {"items": rows_json(rows), "group_by": q.group_by, "scenario": scenario,
            "period": {"from": mf.isoformat(), "to": t.isoformat()},
            "note": "error_pct = (forecast - actual) / actual over the months that have actuals; "
                    "positive = the forecast was too high",
            "source": source(["syn_monthly_sales_forecasts", "fact_order_items"], ["synthetic", "derived"],
                             "forecasts are model estimates; actuals are real valid-sale revenue")}
