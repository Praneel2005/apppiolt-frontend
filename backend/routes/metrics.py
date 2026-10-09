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
            "note": None if m["additive"] or not dims else
            "this metric is a ratio/average, so group changes do not add up to the total change",
            "source": source([ds["table"]], ["derived"])}
