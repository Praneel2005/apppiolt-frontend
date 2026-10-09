"""Backend API checks against the real database (skipped if Postgres / synthetic tables are missing).

Numbers from the API are compared with independent SQL on the ORIGINAL raw_* tables, so a bug in the
derived tables or the semantic layer cannot hide. Write tests undo what they do.
"""
import os

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
from fastapi.testclient import TestClient  # noqa: E402

URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
RW_URL = os.environ.get("DATABASE_URL_RW", "postgresql://app_rw:app_rw@localhost:5432/appdb")


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg.connect(URL, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    if not c.execute("SELECT to_regclass('ops_action_log')").fetchone()[0]:
        pytest.skip("synthetic tables not generated (python -m generator.synthesize)")
    yield c
    c.close()


@pytest.fixture(scope="module")
def api(conn):
    from backend.main import app

    with TestClient(app) as c:
        yield c


def one(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


# ------------------------------------------------------------------------------ numbers
def test_revenue_total_matches_raw_tables(api, conn):
    r = api.post("/api/metrics/query", json={"metric": "revenue"}).json()
    hand = one(conn, """SELECT sum(i.price)::float8 FROM raw_order_items i JOIN raw_orders o USING (order_id)
                        WHERE o.order_status NOT IN ('canceled', 'unavailable')""")
    assert r["total"] == pytest.approx(hand, abs=0.01)


def test_revenue_by_state_shares_sum_to_100(api):
    r = api.post("/api/metrics/query", json={"metric": "revenue", "group_by": ["customer_state"],
                                             "preset": "last_quarter"}).json()
    assert r["period"]["from"] == "2018-04-01" and r["period"]["to"] == "2018-06-30"
    assert sum(x["share_pct"] for x in r["rows"]) == pytest.approx(100, abs=0.2)
    assert sum(x["value"] for x in r["rows"]) == pytest.approx(r["total"], abs=0.01)


def test_customers_metric_counts_unique_people(api, conn):
    r = api.post("/api/metrics/query", json={"metric": "customers", "preset": "year:2017"}).json()
    hand = one(conn, """SELECT count(DISTINCT c.customer_unique_id) FROM raw_orders o
                        JOIN raw_customers c USING (customer_id)
                        JOIN (SELECT DISTINCT order_id FROM raw_order_items) i USING (order_id)
                        WHERE o.order_status NOT IN ('canceled', 'unavailable')
                          AND o.order_purchase_timestamp >= '2017-01-01' AND o.order_purchase_timestamp < '2018-01-01'""")
    assert r["total"] == hand


def test_compare_contributions_add_up(api):
    r = api.post("/api/metrics/compare", json={"metric": "revenue", "group_by": "customer_region",
                                               "preset": "last_quarter"}).json()
    assert r["previous"]["from"] == "2018-01-01"
    assert sum(d["delta"] for d in r["drivers"]) == pytest.approx(r["delta"], abs=0.01)
    assert sum(d["contribution_pct"] for d in r["drivers"]) == pytest.approx(100, abs=0.5)


def test_metric_and_filter_validation(api):
    r = api.post("/api/metrics/query", json={"metric": "revenu"})
    assert r.status_code == 422 and "revenue" in r.json()["error"]["did_you_mean"]
    r = api.post("/api/metrics/query", json={"metric": "revenue", "filters": {"customer_state": ["XX"]}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_value"
    r = api.post("/api/metrics/query", json={"metric": "revenue", "group_by": ["price"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_dimension"


def test_late_orders_total_matches_raw(api, conn):
    r = api.get("/api/orders", params={"late": "true", "customer_state": "RJ", "limit": 1}).json()
    hand = one(conn, """SELECT count(*) FROM raw_orders o JOIN raw_customers c USING (customer_id)
                        WHERE c.customer_state = 'RJ' AND o.order_delivered_customer_date IS NOT NULL
                          AND o.order_delivered_customer_date > o.order_estimated_delivery_date""")
    assert r["total"] == hand


def test_targets_actuals_match_metric(api):
    t = api.get("/api/targets/performance", params={"preset": "last_quarter", "group_by": "none"}).json()["items"][0]
    m = api.post("/api/metrics/query", json={"metric": "revenue", "preset": "last_quarter"}).json()["total"]
    assert t["revenue_actual"] == pytest.approx(m, abs=0.01)
    assert t["attainment_pct"] == pytest.approx(100 * t["revenue_actual"] / t["revenue_target"], abs=0.1)


def test_sla_and_forecast_endpoints(api):
    s = api.get("/api/sla/performance", params={"group_by": "none"}).json()["items"][0]
    assert 10 <= s["breach_rate_pct"] <= 35, "SLA is calibrated on history, so breaches should be a minority"
    by_state = api.get("/api/sla/performance", params={"limit": 50}).json()["items"]
    assert by_state and all(0 <= r["breach_rate_pct"] <= 100 for r in by_state)
    f = api.get("/api/forecasts/comparison", params={"group_by": "month"}).json()["items"]
    assert len(f) == 12 and f[-1]["has_actual"] is False and f[0]["has_actual"] is True


def test_funnel_matches_raw(api, conn):
    items = api.get("/api/funnel/channels").json()["items"]
    assert sum(i["leads"] for i in items) == one(conn, "SELECT count(*) FROM raw_marketing_qualified_leads")
    assert sum(i["won_deals"] for i in items) == one(conn, "SELECT count(*) FROM raw_closed_deals")


# ------------------------------------------------------------------------------ writes
def test_app_rw_cannot_modify_original_or_derived_data():
    with psycopg.connect(RW_URL) as c:
        for stmt in ("UPDATE raw_orders SET order_status = 'x'", "DELETE FROM raw_sellers",
                     "UPDATE fact_order_items SET price = 0", "DELETE FROM syn_monthly_sales_targets",
                     "DELETE FROM ops_action_log"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()


def test_read_role_is_read_only(api):
    from backend.db import ro

    with ro() as c:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            c.execute("INSERT INTO ops_promotions (name, discount_pct, start_date, end_date, created_at, created_by) "
                      "VALUES ('x', 5, now(), now(), now(), 'x')")


def test_restock_dry_run_apply_duplicate_and_undo(api, conn):
    alerts = api.get("/api/inventory/alerts", params={"limit": 1}).json()["items"]
    assert alerts, "no product needs restocking"
    ref = alerts[0]["product_ref"]
    before = one(conn, "SELECT count(*) FROM ops_restock_orders")
    conn.rollback()

    prev = api.post("/api/restock-orders", params={"dry_run": "true"}, json={"product_id": ref}).json()
    assert prev["status"] == "preview" and prev["changes"][0]["op"] == "insert"
    assert one(conn, "SELECT count(*) FROM ops_restock_orders") == before  # nothing saved
    conn.rollback()

    done = api.post("/api/restock-orders", headers={"x-actor": "agent"}, json={"product_id": ref}).json()
    assert done["status"] == "applied" and done["action_id"].startswith("ACT-")
    dup = api.post("/api/restock-orders", json={"product_id": ref})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "restock_already_open"

    undo = api.post(f"/api/actions/{done['action_id']}/undo").json()
    assert undo["status"] == "undone"
    conn.rollback()
    assert one(conn, "SELECT count(*) FROM ops_restock_orders") == before
    again = api.post(f"/api/actions/{done['action_id']}/undo")
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_undone"
    log = api.get("/api/actions", params={"actor": "agent", "limit": 1}).json()["items"][0]
    assert log["code"] == done["action_id"] and log["undone_at"] is not None


def test_price_change_guardrail_and_undo_restores_exact_state(api, conn):
    p = api.get("/api/products", params={"listed": "true", "limit": 1}).json()["items"][0]
    ref, old = p["product_ref"], p["list_price"]
    big = api.patch(f"/api/products/{ref}/price", json={"new_price": old * 3})
    assert big.status_code == 422 and big.json()["error"]["code"] == "price_change_too_large"
    ok = api.patch(f"/api/products/{ref}/price", json={"new_price": round(old * 1.1, 2), "reason": "test"}).json()
    assert ok["status"] == "applied" and len(ok["changes"]) == 2
    assert api.post(f"/api/actions/{ok['action_id']}/undo").json()["status"] == "undone"
    conn.rollback()
    assert float(one(conn, "SELECT list_price FROM ops_product_listings WHERE product_id = %s", p["product_id"])) == old
    assert one(conn, "SELECT count(*) FROM ops_price_changes WHERE product_id = %s", p["product_id"]) == \
        len(api.get(f"/api/products/{ref}").json()["price_changes"])


def test_undo_refused_when_data_changed_since(api):
    t = api.get("/api/tickets", params={"status": "resolved", "limit": 1}).json()["items"][0]
    a = api.patch(f"/api/tickets/{t['code']}", json={"status": "open"}).json()
    b = api.patch(f"/api/tickets/{t['code']}", json={"priority": "urgent"}).json()
    r = api.post(f"/api/actions/{a['action_id']}/undo")
    assert r.status_code == 409 and r.json()["error"]["code"] == "undo_conflict"
    assert api.post(f"/api/actions/{b['action_id']}/undo").json()["status"] == "undone"
    assert api.post(f"/api/actions/{a['action_id']}/undo").json()["status"] == "undone"


def test_ticket_rules(api):
    late = api.get("/api/orders", params={"late": "true", "has_open_ticket": "false", "limit": 1}).json()["items"][0]
    prev = api.post("/api/tickets", params={"dry_run": "true"},
                    json={"order_id": late["order_ref"], "category": "late_delivery"}).json()
    assert prev["status"] == "preview"
    bad = api.post("/api/tickets", json={"order_id": late["order_ref"], "category": "nonsense"})
    assert bad.status_code == 422
    short = api.post("/api/tickets", json={"order_id": "abc", "category": "other"})
    assert short.status_code == 422 and short.json()["error"]["code"] == "ref_too_short"


def test_ambiguous_order_ref_lists_candidates(api, conn):
    row = conn.execute("""SELECT left(order_id, 8) p FROM raw_orders GROUP BY 1 HAVING count(*) > 1 LIMIT 1""").fetchone()
    conn.rollback()
    if row is None:
        pytest.skip("no ambiguous prefix")
    r = api.get(f"/api/orders/{row[0]}")
    assert r.status_code == 409 and len(r.json()["error"]["candidates"]) >= 2


# ------------------------------------------------------------------------------ catalogue
def test_catalogue_matches_routes_and_is_complete(api):
    from backend.catalog import REGISTRY

    cat = api.get("/api/catalog", params={"limit": 500}).json()
    ids = {i["api_id"] for i in cat["items"]}
    assert ids == set(REGISTRY)
    paths = {(m.upper(), p) for p, ms in api.get("/openapi.json").json()["paths"].items() for m in ms}
    for i in cat["items"]:
        assert (i["method"], i["path"]) in paths, i["api_id"]
        assert len(i["description"]) > 40 and i["data_layers"], i["api_id"]
        if i["kind"] == "write" and i["api_id"] != "actions.undo":
            assert i["requires_confirmation"] and i["supports_dry_run"] and i["undoable"], i["api_id"]
            assert i["body_schema"] or i["parameters"], i["api_id"]
    stat = {i["api_id"]: i for i in cat["items"]}
    assert stat["restock_orders.create"]["kind"] == "write" and stat["metrics.query"]["kind"] == "read"
    # closed vocabularies are filled from the database
    cat_param = next(p for p in stat["orders.search"]["parameters"] if p["name"] == "customer_state")
    assert len(cat_param["enum"]) == 27


def test_every_synthetic_or_derived_table_is_labelled(api):
    layers = {t["table"]: t["layer"] for t in api.get("/api/data-dictionary").json()["tables"]}
    assert all(layers[t] == "original" for t in layers if t.startswith("raw_"))
    assert all(layers[t] == "synthetic" for t in layers if t.startswith(("syn_", "ops_")) and t != "ops_action_log")
    assert all(layers[t] == "derived" for t in layers if t.startswith(("fact_", "core_")))
