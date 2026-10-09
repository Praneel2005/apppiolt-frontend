"""Governed metrics: any metric by any dimension, and period-over-period comparison with drivers.

Metric SQL comes from the semantic layer (data/olist/application.json), never from the caller.
Callers choose a metric id, dimensions and filters; all three are validated against the metadata
(closed enum sets, additivity), so the agent cannot invent a metric or a filter value.
"""
from __future__ import annotations

import difflib
from datetime import date
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from psycopg import sql
from pydantic import Field

from backend.catalog import endpoint
from backend.common import ApiError, Body, date_range, jsonable, source
from backend.config import application, as_of_date
from backend.db import ro
from contracts.dates import previous_period

router = APIRouter()


@lru_cache(maxsize=1)
def _index() -> tuple[dict, dict]:
    app = application()
    datasets = {d["dataset_id"]: d for d in app["datasets"]}
    metrics = {m["metric_id"]: (d, m) for d in app["datasets"] for m in d["metrics"]}
    return datasets, metrics


def _metric(metric_id: str) -> tuple[dict, dict]:
    _, metrics = _index()
    if metric_id not in metrics:
        raise ApiError(422, "unknown_metric", f"No metric '{metric_id}'.", allowed=sorted(metrics),
                       did_you_mean=difflib.get_close_matches(metric_id, list(metrics), n=3, cutoff=0.4))
    return metrics[metric_id]


def _fields(ds: dict) -> dict[str, dict]:
    return {f["name"]: f for f in ds["fields"]}


def _check_dims(ds: dict, dims: list[str]) -> None:
    fields = _fields(ds)
    ok = sorted(n for n, f in fields.items() if f["role"] == "dimension")
    for d in dims:
        if d not in fields or fields[d]["role"] != "dimension":
            raise ApiError(422, "invalid_dimension", f"'{d}' is not a dimension of {ds['dataset_id']}.",
                           allowed=ok, did_you_mean=difflib.get_close_matches(d, ok, n=3, cutoff=0.4))
    if len(set(dims)) != len(dims):
        raise ApiError(422, "invalid_dimension", "group_by repeats a dimension.")


def _check_filters(ds: dict, filters: dict[str, list[str]]) -> dict[str, list[str]]:
    fields = _fields(ds)
    out = {}
    for name, values in filters.items():
        f = fields.get(name)
        if f is None or not f.get("values"):
            ok = sorted(n for n, x in fields.items() if x.get("values"))
            raise ApiError(422, "invalid_filter", f"Cannot filter {ds['dataset_id']} by '{name}'.", allowed=ok)
        allowed = set(f["values"])
        bad = [v for v in values if v not in allowed]
        if bad:
            raise ApiError(422, "invalid_value", f"{bad} not valid for filter '{name}'.", param=name,
                           did_you_mean=[m for v in bad for m in difflib.get_close_matches(v, sorted(allowed), n=2, cutoff=0.5)],
                           allowed=sorted(allowed) if len(allowed) <= 30 else None)
        out[name] = list(values)
    return out


def _where(ds: dict, filters: dict[str, list[str]], f: date | None, t: date | None) -> tuple[sql.Composable, dict]:
    parts, params = [], {}
    tf = ds.get("time_field")
    if f:
        parts.append(sql.SQL("{} >= {}").format(sql.Identifier(tf), sql.Placeholder("d_from")))
        params["d_from"] = f
    if t:
        parts.append(sql.SQL("{} <= {}").format(sql.Identifier(tf), sql.Placeholder("d_to")))
        params["d_to"] = t
    for i, (name, values) in enumerate(filters.items()):
        parts.append(sql.SQL("{} = ANY({})").format(sql.Identifier(name), sql.Placeholder(f"f{i}")))
        params[f"f{i}"] = values
    where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts) if parts else sql.SQL("")
    return where, params


def run_metric(conn, ds: dict, m: dict, dims: list[str], filters: dict, f: date | None, t: date | None,
               order: sql.Composable | None = None, limit: int | None = None) -> list[dict]:
    where, params = _where(ds, filters, f, t)
    cols = sql.SQL(", ").join([sql.Identifier(d) for d in dims] + [sql.SQL("({}) AS value").format(sql.SQL(m["sql"]))])
    q = sql.SQL("SELECT {} FROM {}").format(cols, sql.Identifier(ds["table"])) + where
    if dims:
        q += sql.SQL(" GROUP BY ") + sql.SQL(", ").join(sql.Identifier(d) for d in dims)
    if order:
        q += sql.SQL(" ORDER BY ") + order
    if limit:
        q += sql.SQL(" LIMIT {}").format(sql.Literal(limit))
    return conn.execute(q, params).fetchall()


def _val(v: Any) -> float | None:
    return None if v is None else round(float(v), 4)


@endpoint(router, "GET", "/api/metrics", api_id="metrics.list", entity="metric",
          title="List metrics and dimensions",
          description="Every governed business metric (revenue, orders, customers, average order value, late rate, "
                      "review score, ...) with unit, whether it adds up across groups (additive), and the "
                      "dimensions and filter values it supports.",
          data_layers=["derived"], returns="metrics[] with dataset, dimensions and allowed filter values",
          examples=["what metrics do you have", "can I split revenue by seller state"])
def metrics_list():
    datasets, _ = _index()
    out = []
    for ds in datasets.values():
        dims = [{"name": f["name"], "description": f["description"], "values": f.get("values")}
                for f in ds["fields"] if f["role"] == "dimension"]
        for m in ds["metrics"]:
            out.append({"metric_id": m["metric_id"], "title": m["title"], "description": m["description"],
                        "unit": m["unit"], "additive": m["additive"], "higher_is_better": m["higher_is_better"],
                        "dataset": ds["dataset_id"], "table": ds["table"], "time_field": ds["time_field"],
                        "dimensions": dims})
    return {"metrics": out, "source": source([d["table"] for d in datasets.values()], ["derived"])}


class MetricQuery(Body):
    metric: str = Field(..., description="Metric id from GET /api/metrics, e.g. revenue, orders, late_rate")
    group_by: list[str] = Field(default_factory=list, max_length=2,
                                description="Dimensions to split by, e.g. ['customer_state'] or ['order_month']")
    filters: dict[str, list[str]] = Field(default_factory=dict,
                                          description="Dimension -> allowed values, e.g. {'customer_region': ['South']}")
    date_from: date | None = Field(None, description="Start (YYYY-MM-DD); default: no lower bound")
    date_to: date | None = Field(None, description="End (YYYY-MM-DD); default: no upper bound")
    preset: str | None = Field(None, description="Relative period instead of dates: last_quarter, month:2018-03, ...")
    sort: str | None = Field(None, description="'value', '-value' or a group_by field; default -value "
                                                "(time dimensions sort ascending)")
    limit: int = Field(100, ge=1, le=1000)


@endpoint(router, "POST", "/api/metrics/query", api_id="metrics.query", entity="metric", kind="read",
          title="Query a metric",
          description="Compute any governed metric, optionally split by one or two dimensions, filtered by dimension "
                      "values and a date range. Returns each group's value, its share of the total for additive "
                      "metrics, and the exact period and filters used. Use it for any 'how much / how many / which "
                      "is highest' question.",
          data_layers=["derived"],
          returns="rows[] of {<dimensions>, value, share_pct}, total, unit, period, filters",
          examples=["revenue by customer state last quarter", "late delivery rate by seller state",
                    "how many customers bought in 2017", "average review score in the South"])
def metrics_query(body: MetricQuery):
    ds, m = _metric(body.metric)
    _check_dims(ds, body.group_by)
    filters = _check_filters(ds, body.filters)
    f, t = date_range(body.date_from, body.date_to, body.preset)
    if body.sort:
        key = body.sort.lstrip("-")
        if key != "value" and key not in body.group_by:
            raise ApiError(422, "invalid_sort", f"Cannot sort by '{key}'.", allowed=["value", *body.group_by])
        order = sql.SQL("{} {} NULLS LAST").format(sql.Identifier(key), sql.SQL("DESC" if body.sort.startswith("-") else "ASC"))
    elif any(d in body.group_by for d in ("order_month",)):
        order = sql.SQL("order_month ASC")
    else:
        order = sql.SQL("value DESC NULLS LAST")
    with ro() as conn:
        rows = run_metric(conn, ds, m, body.group_by, filters, f, t, order, body.limit)
        total = _val(run_metric(conn, ds, m, [], filters, f, t)[0]["value"])
    out = []
    for r in rows:
        row = {d: jsonable(r[d]) for d in body.group_by}
        v = _val(r["value"])
        row["value"] = v
        if m["additive"] and body.group_by and total:
            row["share_pct"] = round(100 * v / total, 2) if v is not None else None
        out.append(row)
    return {"metric": m["metric_id"], "title": m["title"], "unit": m["unit"], "additive": m["additive"],
            "group_by": body.group_by, "filters": filters,
            "period": {"from": f.isoformat() if f else None, "to": t.isoformat() if t else None,
                       "preset": body.preset},
            "total": total, "row_count": len(out), "truncated": len(out) == body.limit, "rows": out,
            "definition": m["description"],
            "source": source([ds["table"]], ["derived"], "derived from the original Olist tables; "
                             "canceled and unavailable orders are excluded from sales metrics")}


class MetricCompare(Body):
    metric: str = Field(..., description="Metric id, e.g. revenue")
    group_by: str | None = Field(None, description="Dimension to attribute the change to, e.g. product_category")
    filters: dict[str, list[str]] = Field(default_factory=dict)
    date_from: date | None = None
    date_to: date | None = None
    preset: str | None = Field(None, description="The period to explain, e.g. last_quarter")
    compare_from: date | None = Field(None, description="Comparison period start (default: the previous period)")
    compare_to: date | None = None
    compare_preset: str | None = Field(None, description="Comparison period as a preset, e.g. last_year")
    limit: int = Field(20, ge=1, le=200, description="Groups to return, largest absolute change first")


@endpoint(router, "POST", "/api/metrics/compare", api_id="metrics.compare", entity="metric", kind="read",
          title="Compare a metric across two periods",
          description="Compare a metric between a period and an earlier one (default: the immediately preceding "
                      "period of the same length), with the change in absolute and percent terms. With group_by, "
                      "shows which groups drive the change (contribution to the change for additive metrics). "
                      "Use it for 'why did X change', 'compare this quarter with last', 'what drove the drop'.",
          data_layers=["derived"],
          returns="current, previous, delta, pct_change, drivers[] {group, current, previous, delta, contribution_pct}",
          examples=["why did revenue drop in July", "compare Q2 2018 revenue with Q1", "what drove the change in late rate"])
def metrics_compare(body: MetricCompare):
    ds, m = _metric(body.metric)
    dims = [body.group_by] if body.group_by else []
    _check_dims(ds, dims)
    filters = _check_filters(ds, body.filters)
    f, t = date_range(body.date_from, body.date_to, body.preset)
    if not (f and t):
        raise ApiError(422, "period_required", "Give the period to explain (preset, or date_from and date_to).")
    cf, ct = date_range(body.compare_from, body.compare_to, body.compare_preset)
    if not (cf and ct):
        cf, ct = (date.fromisoformat(x) for x in previous_period(f.isoformat(), t.isoformat()))
    with ro() as conn:
        cur = run_metric(conn, ds, m, dims, filters, f, t)
        prev = run_metric(conn, ds, m, dims, filters, cf, ct)
        tot_cur = _val(run_metric(conn, ds, m, [], filters, f, t)[0]["value"])
        tot_prev = _val(run_metric(conn, ds, m, [], filters, cf, ct)[0]["value"])

    def pct(a, b):
        return None if a is None or not b else round(100 * (a - b) / abs(b), 2)

    delta = None if tot_cur is None or tot_prev is None else tot_cur - tot_prev
    decomposition = None
    if dims and not m["additive"] and m.get("weight_sql"):
        with ro() as conn:
            decomposition = _mix_rate(conn, ds, m, dims[0], filters, (f, t), (cf, ct))
    drivers = []
    if dims:
        c = {r[dims[0]]: _val(r["value"]) for r in cur}
        p = {r[dims[0]]: _val(r["value"]) for r in prev}
        for g in sorted(set(c) | set(p), key=str):
            a, b = c.get(g), p.get(g)
            d = (a or 0.0) - (b or 0.0)
            row = {"group": jsonable(g), "current": a, "previous": b, "delta": round(d, 6), "pct_change": pct(a, b)}
            if m["additive"] and delta:
                row["contribution_pct"] = round(100 * d / delta, 1)
            drivers.append(row)
        drivers.sort(key=lambda r: -abs(r["delta"]))
        drivers = drivers[:body.limit]
    return {"metric": m["metric_id"], "title": m["title"], "unit": m["unit"], "additive": m["additive"],
            "group_by": body.group_by, "filters": filters,
            "current": {"from": f.isoformat(), "to": t.isoformat(), "value": tot_cur},
            "previous": {"from": cf.isoformat(), "to": ct.isoformat(), "value": tot_prev},
            "delta": None if delta is None else round(delta, 6), "pct_change": pct(tot_cur, tot_prev),
            "drivers": drivers,
            "ratio_decomposition": decomposition,
            "note": None if m["additive"] or not dims or decomposition else
            "this metric is a ratio/average, so group changes do not add up to the total change",
            "source": source([ds["table"]], ["derived"])}


def _weighted(conn, ds: dict, m: dict, dim: str, filters: dict, f: date | None, t: date | None) -> dict:
    """{group: (ratio, weight)} for one period."""
    where, params = _where(ds, filters, f, t)
    q = sql.SQL("SELECT {d} AS g, ({v}) AS value, ({w}) AS weight FROM {t}").format(
        d=sql.Identifier(dim), v=sql.SQL(m["sql"]), w=sql.SQL(m["weight_sql"]), t=sql.Identifier(ds["table"])) + where
    q += sql.SQL(" GROUP BY {}").format(sql.Identifier(dim))
    return {r["g"]: (_val(r["value"]), float(r["weight"] or 0)) for r in conn.execute(q, params).fetchall()}


def _mix_rate(conn, ds: dict, m: dict, dim: str, filters: dict, cur: tuple, prev: tuple) -> dict | None:
    """Exact split of the change in a ratio metric into mix and rate effects per group.

        M = sum_g s_g * m_g          (s_g = group share of the weight, m_g = group ratio)
        M1 - M0 = sum_g s1_g (m1_g - m0_g)            <- rate effect: the group's own ratio changed
                + sum_g (s1_g - s0_g)(m0_g - M0)      <- mix effect: more/less of the population is in the group
    A group missing in the earlier period takes the earlier overall ratio M0 as its m0 (so it has no mix effect).
    """
    c, p = _weighted(conn, ds, m, dim, filters, *cur), _weighted(conn, ds, m, dim, filters, *prev)
    wc, wp = sum(w for _, w in c.values()), sum(w for _, w in p.values())
    if not wc or not wp:
        return None
    m1 = sum(v * w for v, w in c.values() if v is not None) / wc
    m0 = sum(v * w for v, w in p.values() if v is not None) / wp
    rows = []
    for g in sorted(set(c) | set(p), key=str):
        v1, w1 = c.get(g, (None, 0.0))
        v0, w0 = p.get(g, (None, 0.0))
        s1, s0 = w1 / wc, w0 / wp
        v0n = m0 if v0 is None else v0
        rate = s1 * ((v1 if v1 is not None else v0n) - v0n)
        mix = (s1 - s0) * (v0n - m0)
        rows.append({"group": jsonable(g), "share_previous": round(s0, 6), "share_current": round(s1, 6),
                     "value_previous": v0, "value_current": v1, "rate_effect": round(rate, 8),
                     "mix_effect": round(mix, 8), "total_effect": round(rate + mix, 8)})
    total = m1 - m0
    for r in rows:
        r["contribution_pct"] = round(100 * r["total_effect"] / total, 1) if total else None
    rows.sort(key=lambda r: -abs(r["total_effect"]))
    return {"overall_previous": round(m0, 8), "overall_current": round(m1, 8), "delta": round(total, 8),
            "rate_effect_total": round(sum(r["rate_effect"] for r in rows), 8),
            "mix_effect_total": round(sum(r["mix_effect"] for r in rows), 8), "groups": rows[:30],
            "method": "mix/rate decomposition: rate = current share x change in the group's ratio; "
                      "mix = change in share x (group's previous ratio - overall previous ratio)"}


# ------------------------------------------------------------------------------------ trend
class TrendQuery(Body):
    metric: str = Field(..., description="Metric id, e.g. revenue, late_rate")
    filters: dict[str, list[str]] = Field(default_factory=dict)
    date_from: date | None = None
    date_to: date | None = None
    preset: str | None = Field(None, description="Period, e.g. last_12_months, year:2017 (default: all data)")


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


@endpoint(router, "POST", "/api/metrics/trend", api_id="metrics.trend", entity="metric", kind="read",
          title="Metric trend analysis",
          description="Month-by-month series of a metric with computed trend statistics: direction, average "
                      "monthly change, total change, peak and trough months, month-over-month and year-over-year "
                      "changes, volatility and gaps. Use it for 'is X growing or falling', 'when did it peak', "
                      "'how volatile is it'.",
          data_layers=["derived"],
          returns="rows[] {order_month, value, mom_pct, yoy_pct}, stats {trend, slope_per_month, change_pct, peak, trough, ...}",
          examples=["is revenue growing", "when did late deliveries peak", "how has the review score moved this year"])
def metrics_trend(body: TrendQuery):
    import numpy as np

    ds, m = _metric(body.metric)
    if not any(f["name"] == "order_month" for f in ds["fields"]):
        raise ApiError(422, "no_time_dimension", f"{body.metric} has no monthly time dimension.")
    filters = _check_filters(ds, body.filters)
    f, t = date_range(body.date_from, body.date_to, body.preset)
    with ro() as conn:
        raw = run_metric(conn, ds, m, ["order_month"], filters, f, t, sql.SQL("order_month ASC"))
    pts = [(r["order_month"], _val(r["value"])) for r in raw if r["value"] is not None]
    if len(pts) < 3:
        raise ApiError(422, "not_enough_data", "A trend needs at least 3 months of data in the period.", months=len(pts))
    idx = [_month_index(d) for d, _ in pts]
    vals = np.array([v for _, v in pts], dtype=float)
    x = np.array([i - idx[0] for i in idx], dtype=float)
    slope, intercept = np.polyfit(x, vals, 1)
    fit = slope * x + intercept
    ss_tot = float(((vals - vals.mean()) ** 2).sum())
    r2 = 1 - float(((vals - fit) ** 2).sum()) / ss_tot if ss_tot else 0.0
    mean = float(vals.mean())
    slope_pct = float(slope) / abs(mean) if mean else 0.0
    trend = "flat" if (r2 < 0.25 or abs(slope_pct) < 0.01) else ("rising" if slope > 0 else "falling")
    by_idx = {i: v for i, v in zip(idx, vals)}
    rows, moms = [], []
    for (d, v), i in zip(pts, idx):
        prev, yago = by_idx.get(i - 1), by_idx.get(i - 12)
        mom = round(100 * (v - prev) / abs(prev), 2) if prev else None
        rows.append({"order_month": d.isoformat(), "value": v, "mom_pct": mom,
                     "yoy_pct": round(100 * (v - yago) / abs(yago), 2) if yago else None})
        if mom is not None:
            moms.append((mom, d.isoformat()))
    pk, tr = int(vals.argmax()), int(vals.argmin())
    missing = [i for i in range(idx[0], idx[-1] + 1) if i not in by_idx]
    first, last = float(vals[0]), float(vals[-1])
    stats = {"months": len(pts), "first": {"month": pts[0][0].isoformat(), "value": first},
             "last": {"month": pts[-1][0].isoformat(), "value": last},
             "change_abs": round(last - first, 6), "change_pct": round(100 * (last - first) / abs(first), 2) if first else None,
             "trend": trend, "slope_per_month": round(float(slope), 6), "slope_pct_of_mean": round(100 * slope_pct, 2),
             "r_squared": round(r2, 3), "mean": round(mean, 6), "std": round(float(vals.std()), 6),
             "coefficient_of_variation": round(float(vals.std()) / abs(mean), 3) if mean else None,
             "peak": {"month": pts[pk][0].isoformat(), "value": float(vals[pk])},
             "trough": {"month": pts[tr][0].isoformat(), "value": float(vals[tr])},
             "largest_rise": max(moms)[::-1] if moms else None, "largest_fall": min(moms)[::-1] if moms else None,
             "missing_months": [f"{i // 12}-{i % 12 + 1:02d}" for i in missing],
             "definition": "trend = rising/falling when the least-squares line explains >= 25% of variance and moves "
                           ">= 1% of the mean per month; otherwise flat"}
    return {"metric": m["metric_id"], "title": m["title"], "unit": m["unit"], "filters": filters,
            "period": {"from": f.isoformat() if f else None, "to": t.isoformat() if t else None, "preset": body.preset},
            "rows": rows, "stats": stats, "source": source([ds["table"]], ["derived"])}
