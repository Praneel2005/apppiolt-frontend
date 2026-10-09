"""Summary APIs (feed the KPI strips and charts), page visuals, and the per-session KPI measurements.

Numbers are compared with independent SQL on the ORIGINAL raw_* tables / the search APIs, so a wrong grouping or a
double count cannot hide. Skipped without Postgres and the synthetic tables.
"""
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
from fastapi.testclient import TestClient  # noqa: E402

URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
APP_FILE = Path(__file__).resolve().parent.parent / "data" / "olist" / "application.json"


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg.connect(URL, connect_timeout=3, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    if not c.execute("SELECT to_regclass('ops_action_log')").fetchone()[0]:
        pytest.skip("synthetic tables not generated")
    yield c
    c.close()


@pytest.fixture(scope="module")
def api(conn):
    from backend.main import app

    with TestClient(app) as client:
        yield client


def one(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def total(api, path, key, **params):
    return api.get(path, params={"group_by": "none", **params}).json()["items"][0][key]


def grouped(api, path, group, **params):
    r = api.get(path, params={"group_by": group, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ------------------------------------------------------------------------------ orders
def test_orders_summary_matches_raw_tables(api, conn):
    s = api.get("/api/orders/summary").json()["items"][0]
    assert s["orders"] == one(conn, "SELECT count(*) FROM raw_orders")
    assert s["late_orders"] == one(conn, """SELECT count(*) FROM raw_orders WHERE order_delivered_customer_date IS NOT NULL
                                            AND order_delivered_customer_date > order_estimated_delivery_date""")
    assert s["items_value"] == pytest.approx(float(one(conn, "SELECT sum(price) FROM raw_order_items")), abs=0.05)
    rj = api.get("/api/orders/summary", params={"customer_state": "RJ", "preset": "last_month"}).json()["items"][0]
    listing = api.get("/api/orders", params={"customer_state": "RJ", "preset": "last_month", "limit": 1}).json()
    assert rj["orders"] == listing["total"]


@pytest.mark.parametrize("group", ["status", "customer_state", "customer_region", "month", "payment_type", "review_score",
                                   "days_late_bucket"])
def test_orders_groups_partition_the_total(api, group):
    t = total(api, "/api/orders/summary", "orders")
    rows = grouped(api, "/api/orders/summary", group)
    assert sum(r["orders"] for r in rows) == t
    assert len({r[group] for r in rows}) == len(rows)  # no duplicate groups


def test_late_buckets_are_ordered_and_consistent(api):
    rows = grouped(api, "/api/orders/summary", "days_late_bucket", late=True)
    assert [r["days_late_bucket"] for r in rows] == ["1-3 days", "4-7 days", "8-14 days", "15+ days"]
    assert sum(r["orders"] for r in rows) == total(api, "/api/orders/summary", "late_orders")
    bad = api.get("/api/orders/summary", params={"group_by": "colour"})
    assert bad.status_code == 422


# ------------------------------------------------------------------------------ tickets, reviews
def test_tickets_summary_matches_search(api):
    t = total(api, "/api/tickets/summary", "tickets")
    assert t == api.get("/api/tickets", params={"limit": 1}).json()["total"]
    assert total(api, "/api/tickets/summary", "open_tickets") == api.get("/api/tickets", params={"open_only": True, "limit": 1}).json()["total"]
    for group in ("status", "category", "priority", "customer_state", "assignee", "day"):
        assert sum(r["tickets"] for r in grouped(api, "/api/tickets/summary", group)) == t
    assert [r["status"] for r in grouped(api, "/api/tickets/summary", "status")] == ["open", "in_progress", "resolved", "closed"]
    assert total(api, "/api/tickets/summary", "tickets", priority="urgent") == total(api, "/api/tickets/summary", "urgent_tickets")


def test_reviews_summary_matches_raw(api, conn):
    s = api.get("/api/reviews/summary").json()["items"][0]
    assert s["reviews"] == one(conn, "SELECT count(*) FROM raw_reviews")
    assert s["avg_score"] == pytest.approx(float(one(conn, "SELECT avg(review_score) FROM raw_reviews")), abs=0.01)
    assert s["negative_reviews"] == one(conn, "SELECT count(*) FROM raw_reviews WHERE review_score <= 2")
    assert s["replied"] == one(conn, "SELECT count(*) FROM ops_review_replies")
    dist = grouped(api, "/api/reviews/summary", "score")
    assert [r["score"] for r in dist] == [1, 2, 3, 4, 5] and sum(r["reviews"] for r in dist) == s["reviews"]
    assert sum(r["reviews"] for r in grouped(api, "/api/reviews/summary", "has_reply")) == s["reviews"]


# ------------------------------------------------------------------------------ stock, restock, flags
def test_inventory_summary_matches_listings(api, conn):
    s = api.get("/api/inventory/summary").json()["items"][0]
    assert s["products"] == api.get("/api/products", params={"listed": True, "limit": 1}).json()["total"]
    assert s["inventory_value"] == pytest.approx(float(one(conn, "SELECT sum(stock_quantity * unit_cost) FROM ops_product_listings")), abs=0.05)
    assert s["stock_units"] == one(conn, "SELECT sum(stock_quantity) FROM ops_product_listings")
    assert s["below_reorder"] == one(conn, "SELECT count(*) FROM ops_product_listings WHERE stock_quantity <= reorder_point")
    assert s["needs_restock"] == api.get("/api/inventory/alerts", params={"limit": 1}).json()["total"]
    rows = grouped(api, "/api/inventory/summary", "category", limit=100)
    assert sum(r["products"] for r in rows) == s["products"]
    vals = [r["inventory_value"] for r in rows]
    assert vals == sorted(vals, reverse=True)


def test_restock_and_flag_summaries(api, conn):
    r = api.get("/api/restock-orders/summary").json()["items"][0]
    assert r["orders"] == one(conn, "SELECT count(*) FROM ops_restock_orders")
    assert r["open_units"] == one(conn, "SELECT COALESCE(sum(quantity), 0) FROM ops_restock_orders WHERE status IN ('placed', 'in_transit')")
    assert sum(x["orders"] for x in grouped(api, "/api/restock-orders/summary", "category")) == r["orders"]
    f = api.get("/api/seller-flags/summary").json()["items"][0]
    assert f["flags"] == api.get("/api/seller-flags", params={"limit": 1}).json()["total"]
    assert sum(x["flags"] for x in grouped(api, "/api/seller-flags/summary", "reason")) == f["flags"]


def test_sellers_summary_reconciles_with_revenue_metric(api):
    s = api.get("/api/sellers/summary").json()
    p = s["period"]
    t = s["items"][0]
    m = api.post("/api/metrics/query", json={"metric": "revenue", "date_from": p["from"], "date_to": p["to"]}).json()
    assert t["revenue"] == pytest.approx(m["total"], abs=0.05)
    assert t["active_sellers"] == api.get("/api/sellers", params={"min_items_sold": 1, "limit": 1}).json()["total"]
    by_state = grouped(api, "/api/sellers/summary", "seller_state")
    assert sum(r["revenue"] for r in by_state) == pytest.approx(t["revenue"], abs=0.05)
    from backend.config import as_of_date
    assert p["to"] == as_of_date().isoformat() and p["from"] == (as_of_date() - timedelta(days=364)).isoformat()


# ------------------------------------------------------------------------------ page visuals
def test_every_operations_page_has_a_visual_not_just_a_table():
    from contracts.metadata import Application

    app = Application.model_validate(json.loads(APP_FILE.read_text()))
    visual = {"kpi_strip", "kpi_card", "bar_chart", "line_chart", "pie_chart"}
    for p in (p for p in app.pages if p.kind == "operations"):
        kinds = [app.widget(w).type for w in p.widgets]
        assert any(k in visual for k in kinds), f"{p.page_id} shows only tables"
    counts: dict[str, int] = {}
    for w in app.widgets:
        counts[w.type] = counts.get(w.type, 0) + 1
    assert counts["kpi_strip"] >= 11 and counts["pie_chart"] >= 7 and counts["line_chart"] >= 15, counts
    ids = [i for p in app.pages for i in p.widgets]
    assert len(ids) == len(set(ids)), "a widget is placed on two pages"


# ------------------------------------------------------------------------------ KPI measurements
def run(api, sid, *steps, **kw):
    body = {"plan": {"intent": {"name": "multi_step"}, "steps": [{"tool": t, "args": a} for t, a in steps]}, **kw}
    return api.post(f"/api/session/{sid}/plan", json=body).json()


def test_session_stats_count_runs_gates_and_effort(api):
    sid = api.post("/api/session").json()["session_id"]
    s0 = api.get(f"/api/session/{sid}/stats").json()
    assert s0["runs"] == 0 and s0["run_success_rate"] is None and s0["verify_pass_rate"] is None

    ok = run(api, sid, ("navigate", {"page_id": "ops.orders", "filters": {"customer_state": {"value": ["rj"]}}}),
             ("set_sort", {"field": "days_late", "dir": "desc"}))
    assert ok["ok"]
    bad = run(api, sid, ("navigate", {"page_id": "ops.ordrs"}), ("set_filter", {"filter_id": "nope", "value": "x"}))
    assert not bad["ok"]
    api.post(f"/api/session/{sid}/fault", json={"kind": "drop_filter"})
    caught = run(api, sid, ("navigate", {"page_id": "ops.orders", "filters": {"customer_state": {"value": ["SP"]}}}))
    assert not caught["ok"]
    api.post(f"/api/session/{sid}/fault", json={"kind": "none"})
    state = api.get(f"/api/session/{sid}/state").json()["state"]
    api.post(f"/api/session/{sid}/state", json=state)  # the user changes something by hand

    s = api.get(f"/api/session/{sid}/stats").json()
    assert s["plan_runs"] == 3 and s["runs"] == 3
    assert s["validation_failures"] == 1 and s["issues_caught_by_validator"]["invalid_reference"] == 2
    assert s["verify_passes"] == 2 and s["verify_failures"] == 1 and s["verify_pass_rate"] == pytest.approx(2 / 3)
    assert s["agent_steps"] == 3 and s["manual_changes"] == 1 and s["agent_share_of_actions"] == pytest.approx(0.75)
    assert s["run_success_rate"] == pytest.approx(1 / 3) and s["avg_steps_per_run"] == pytest.approx(1.0)
    assert s["latency_ms"]["p50"] is not None and s["latency_ms"]["p90"] >= s["latency_ms"]["p50"]


def test_confirmations_undo_and_feedback_are_counted(api):
    sid = api.post("/api/session").json()["session_id"]
    ref = api.get("/api/inventory/alerts", params={"limit": 1}).json()["items"][0]["product_ref"]
    r = run(api, sid, ("write_api", {"api_id": "restock_orders.create", "body": {"product_id": ref}}), auto_confirm=True)
    assert r["ok"]
    assert api.post(f"/api/session/{sid}/undo", json={"what": "write"}).json()["ok"]
    fb = api.post(f"/api/session/{sid}/feedback", json={"rating": "up", "comment": "worked"})
    assert fb.status_code == 201
    api.post(f"/api/session/{sid}/feedback", json={"rating": "down"})
    assert api.post(f"/api/session/{sid}/feedback", json={"rating": "sideways"}).status_code == 422
    s = api.get(f"/api/session/{sid}/stats").json()
    assert s["confirmations_approved"] == 1 and s["confirmations_declined"] == 0
    assert s["writes_undone"] == 1 and s["writes_applied"] == 1
    assert s["feedback"] == {"up": 1, "down": 1}
