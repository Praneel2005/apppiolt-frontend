"""Runtime checks: sessions, widget data, plan validation/execution/verification, confirmations, undo,
evidence, canvas, retrieval. REST only (the WebSocket path is exercised by running the real server).
Skipped when Postgres or the synthetic tables are missing."""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
from fastapi.testclient import TestClient  # noqa: E402

URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")


@pytest.fixture(scope="module")
def api():
    try:
        c = psycopg.connect(URL, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    ok = c.execute("SELECT to_regclass('ops_action_log')").fetchone()[0]
    c.close()
    if not ok:
        pytest.skip("synthetic tables not generated")
    from backend.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture()
def sid(api):
    return api.post("/api/session").json()["session_id"]


def plan(*steps, intent="multi_step", **kw):
    return {"plan": {"intent": {"name": intent}, "steps": [{"tool": t, "args": a} for t, a in steps]}, **kw}


def run(api, sid, *steps, **kw):
    return api.post(f"/api/session/{sid}/plan", json=plan(*steps, **kw)).json()


ORDERS_RJ = ("navigate", {"page_id": "ops.orders", "filters": {"customer_state": {"value": ["Rio de Janeiro"]}},
                          "date_range": {"preset": "last_month"}})


# ------------------------------------------------------------------------------ session and widgets
def test_session_starts_on_dashboard_and_context_describes_the_screen(api, sid):
    st = api.get(f"/api/session/{sid}/state").json()["state"]
    assert st["page_id"] == "ops.dashboard" and st["date_range"]["to"] == "2018-08-31"
    ctx = api.get(f"/api/session/{sid}/context").json()
    assert ctx["page"]["page_code"] == "P-100" and ctx["page"]["agent_context"]
    by = {w["widget_id"]: w for w in ctx["widgets"]}
    assert by["ops.dashboard.kpi_revenue"]["rows"][0]["revenue"] > 1e6
    assert by["ops.dashboard.open_tickets"]["row_count"] == 8 and by["ops.dashboard.open_tickets"]["row_actions"]
    assert api.get("/api/session/nope/state").json()["error"]["code"] == "session_not_found"


def test_widget_data_metric_and_api_widgets(api):
    r = api.post("/api/widget-data", json={"widget_id": "ops.dashboard.kpi_revenue",
                                           "date_range": {"from": "2018-04-01", "to": "2018-06-30"}}).json()
    q = api.post("/api/metrics/query", json={"metric": "revenue", "date_from": "2018-04-01", "date_to": "2018-06-30"}).json()
    assert r["rows"][0]["revenue"] == pytest.approx(q["total"], abs=0.01) and "series_hash" not in r
    o = api.post("/api/widget-data", json={"widget_id": "ops.orders.grid", "filters": {"customer_state": {"op": "in", "value": ["RJ"]}},
                                           "date_range": {"from": "2018-07-01", "to": "2018-07-31"}}).json()
    assert o["row_count"] == 50 and {x["customer_state"] for x in o["rows"]} == {"RJ"}
    assert api.post("/api/widget-data", json={"widget_id": "nope"}).status_code == 404


def test_presets_and_search(api):
    p = api.get("/api/date-presets").json()
    assert p["last_quarter"] == {"from": "2018-04-01", "to": "2018-06-30"}
    top = lambda q, k: [(r["kind"], r["id"]) for r in api.get("/api/search", params={"q": q, "kinds": k}).json()["results"]]  # noqa: E731
    assert ("page", "ops.late_deliveries") in top("delayed deliveries in Rio", "page")[:5]
    assert {i for _, i in top("which products need restocking", "page")[:3]} & {"ops.low_stock", "ops.products", "ops.restock_orders"}
    assert ("api", "tickets.create") in top("open a support ticket for an order", "api")[:5]
    assert top("forecast accuracy", "page")[0][1] == "ops.forecast"


# ------------------------------------------------------------------------------ plan: validate, execute, verify
def test_navigate_with_synonym_is_normalised_executed_and_verified(api, sid):
    r = run(api, sid, ORDERS_RJ)
    assert r["ok"], r
    st = r["state"]
    assert st["route"] == "/ops/orders" and st["filters"]["customer_state"] == {"op": "in", "value": ["RJ"]}
    assert st["date_range"] == {"from": "2018-07-01", "to": "2018-07-31", "preset": "last_month"}
    kinds = [e["type"] for e in r["events"]]
    assert kinds[:3] == ["plan", "validation", "action"] and kinds[-1] == "done" and "verify" in kinds
    ver = next(e for e in r["events"] if e["type"] == "verify")
    assert ver["data"]["ok"] and ver["data"]["source"] == "simulated_browser"
    ctx = api.get(f"/api/session/{sid}/context").json()
    grid = next(w for w in ctx["widgets"] if w["widget_id"] == "ops.orders.grid")
    assert grid["rows"] and all(x["customer_state"] == "RJ" for x in grid["rows"])
    strip = next(w for w in ctx["widgets"] if w["widget_id"] == "ops.orders.strip")  # charts follow the same filters
    assert strip["applied_filters"]["customer_state"]["value"] == ["RJ"]


def test_validator_blocks_bad_plans_with_suggestions(api, sid):
    bad = run(api, sid, ("navigate", {"page_id": "ops.ordrs"}))
    assert not bad["ok"] and bad["stage"] == "validation" and bad["issues"][0]["code"] == "invalid_reference"
    assert bad["issues"][0]["suggestion"] == "ops.orders"
    v = run(api, sid, ("navigate", {"page_id": "ops.orders", "filters": {"customer_state": {"value": ["Atlantis"]}}}))
    assert v["issues"][0]["code"] == "invalid_value"
    v = run(api, sid, ("set_filter", {"filter_id": "nonexistent", "value": "x"}))
    assert v["issues"][0]["code"] == "invalid_reference"
    v = run(api, sid, ("navigate", {"page_id": "ops.orders", "date_range": {"preset": "last_decade"}}))
    assert v["issues"][0]["code"] == "invalid_date"
    v = run(api, sid, ("call_api", {"api_id": "orders.search", "params": {"colour": "red"}}))
    assert v["issues"][0]["code"] == "invalid_reference"
    v = run(api, sid, ("call_api", {"api_id": "restock_orders.create", "params": {}}))
    assert v["issues"][0]["code"] == "disallowed_tool"
    v = run(api, sid, ("write_api", {"api_id": "tickets.create", "body": {"order_id": "abcdef12"}}))
    assert not v["ok"] and any("category" in i["message"] for i in v["issues"])
    v = run(api, sid, ("explain_change", {"metric": "customers", "group_by": "customer_region", "preset": "last_quarter"}))
    assert "additive" in v["issues"][0]["message"]
    v = run(api, sid, *[("write_api", {"api_id": "tickets.update", "params": {"code": "TCK-1"}, "body": {"priority": "low"}})] * 6)
    assert any(i["code"] == "unsafe" for i in v["issues"])
    assert api.get(f"/api/session/{sid}/state").json()["state"]["page_id"] == "ops.dashboard"  # nothing executed


@pytest.mark.parametrize("fault,field", [("drop_filter", "applied_filters"), ("empty_widget", "row_count"),
                                         ("wrong_route", "route")])
def test_verifier_catches_injected_browser_faults(api, sid, fault, field):
    api.post(f"/api/session/{sid}/fault", json={"kind": fault})
    step = ("navigate", {"page_id": "ops.late_deliveries", "filters": {"customer_state": {"value": ["RJ"]}},
                         "date_range": {"preset": "last_month"}})
    r = run(api, sid, step)
    assert not r["ok"] and r["results"][0]["error_code"] == "state_mismatch"
    ver = next(e for e in r["events"] if e["type"] == "verify")["data"]
    assert not ver["ok"] and field in {m["field"] for m in ver["mismatches"]}
    # without the verifier (ablation) the same fault goes unnoticed
    r2 = run(api, sid, step, config={"use_verifier": False})
    assert r2["ok"]
    api.post(f"/api/session/{sid}/fault", json={"kind": "none"})
    assert run(api, sid, step)["ok"]


def test_state_undo_and_keep_state(api, sid):
    run(api, sid, ORDERS_RJ)
    r = run(api, sid, ("navigate", {"page_id": "ops.late_deliveries", "keep_state": True}))
    st = r["state"]
    assert st["filters"]["customer_state"]["value"] == ["RJ"] and st["date_range"]["preset"] == "last_month"
    r = run(api, sid, ("navigate", {"page_id": "ops.tickets"}))  # S1: navigation without keep_state resets filters
    assert r["state"]["filters"] == {}
    assert api.post(f"/api/session/{sid}/undo").json()["ok"]
    assert api.get(f"/api/session/{sid}/state").json()["state"]["page_id"] == "ops.late_deliveries"
    assert api.post(f"/api/session/{sid}/undo").json()["ok"]
    assert api.get(f"/api/session/{sid}/state").json()["state"]["page_id"] == "ops.orders"


def test_filters_sort_and_date_steps(api, sid):
    r = run(api, sid, ("navigate", {"page_id": "ops.tickets"}), ("set_filter", {"filter_id": "priority", "value": "urgent"}),
            ("set_filter", {"filter_id": "status", "value": "open"}), ("set_date_range", {"preset": "last_7_days"}),
            ("set_sort", {"field": "age_hours", "dir": "desc"}))
    assert r["ok"], r
    f = r["state"]["filters"]
    assert f["priority"]["value"] == ["urgent"] and f["status"]["value"] == ["open"]
    assert r["state"]["sort"] == {"field": "age_hours", "dir": "desc"}
    ctx = api.get(f"/api/session/{sid}/context").json()
    rows = next(w for w in ctx["widgets"] if w["widget_id"] == "ops.tickets.grid")["rows"]
    assert all(x["priority"] == "urgent" and x["status"] == "open" for x in rows)
    r = run(api, sid, ("clear_filter", {"filter_id": "all"}))
    assert r["ok"] and r["state"]["filters"] == {}
    assert not run(api, sid, ("set_sort", {"field": "nonsense"}))["ok"]


def test_confirm_mode_asks_before_ui_changes(api, sid):
    r = run(api, sid, ORDERS_RJ, config={"confirm_mode": True}, auto_confirm=True)
    cr = next(e for e in r["events"] if e["type"] == "confirm_request")["data"]
    assert r["ok"] and cr["kind"] == "ui_state" and cr["auto_confirmed"] is True


# ------------------------------------------------------------------------------ analysis and evidence
def test_metric_steps_produce_cited_evidence(api, sid):
    r = run(api, sid,
            ("run_metric_query", {"metric": "revenue", "group_by": ["customer_state"], "preset": "last_quarter", "limit": 5}),
            ("compare_periods", {"metric": "revenue", "group_by": "product_category", "preset": "month:2017-11", "limit": 3}),
            ("explain_change", {"metric": "revenue", "group_by": "customer_region", "preset": "quarter:2018Q2"}),
            ("read_view", {}))
    assert r["ok"], r
    e1, e2, e3, e4 = (api.get(f"/api/session/{sid}/evidence/{i}").json() for i in r["evidence_ids"])
    direct = api.post("/api/metrics/query", json={"metric": "revenue", "group_by": ["customer_state"],
                                                  "preset": "last_quarter", "limit": 5}).json()
    assert e1["kind"] == "metric_query" and e1["values"]["rows"] == direct["rows"]
    assert e2["kind"] == "comparison" and e2["values"]["pct_change"] == pytest.approx(52.06, abs=0.1)
    assert e3["kind"] == "contribution" and sum(d["contribution_pct"] for d in e3["values"]["drivers"]) == pytest.approx(100, abs=1)
    assert e4["kind"] == "widget_read" and e4["values"]["widget_code"] == "R-0001" or e4["values"]["row_count"] >= 1
    assert api.get(f"/api/session/{sid}/evidence/E99").status_code == 404


def test_call_api_normalises_params_and_records_evidence(api, sid):
    r = run(api, sid, ("call_api", {"api_id": "orders.search", "params": {"late": "true", "customer_state": "rj", "limit": 3}}),
            ("search_pages", {"query": "tickets"}), ("search_apis", {"query": "restock"}))
    assert r["ok"], r
    ev = api.get(f"/api/session/{sid}/evidence/{r['evidence_ids'][0]}").json()
    assert ev["kind"] == "api_read" and len(ev["values"]["rows"]) == 3 and ev["values"]["params"]["customer_state"] == "RJ"
    assert ev["values"]["rows"][0]["is_late"] is True


def test_canvas_from_evidence_and_from_write_schema(api, sid):
    r = run(api, sid, ("run_metric_query", {"metric": "revenue", "group_by": ["customer_state"], "preset": "last_quarter", "limit": 6}),
            ("render_canvas", {"kind": "chart", "evidence_id": "E1", "title": "Revenue by state", "x": "customer_state", "y": ["value"]}),
            ("render_canvas", {"kind": "table", "evidence_id": "E1", "title": "Same data"}),
            ("render_canvas", {"kind": "form", "api_id": "tickets.create", "defaults": {"category": "late_delivery"}}))
    assert r["ok"], r
    cvs = api.get(f"/api/session/{sid}/canvases").json()["canvases"]
    chart, table, form = cvs
    assert chart["kind"] == "chart" and chart["chart"]["y"] == ["value"] and len(chart["rows"]) == 6 and chart["evidence_ids"] == ["E1"]
    assert chart["data_layers"] == ["derived"] and table["kind"] == "table"
    assert form["form"]["api_id"] == "tickets.create" and "category" in form["form"]["body_schema"]["properties"]
    assert any(e["type"] == "canvas" for e in r["events"])
    bad = run(api, sid, ("render_canvas", {"kind": "table", "evidence_id": "E99"}))
    assert not bad["ok"] and bad["results"][0]["error_code"] == "invalid_reference"


# ------------------------------------------------------------------------------ writes and confirmation
def _restock_ref(api):
    return api.get("/api/inventory/alerts", params={"limit": 1}).json()["items"][0]["product_ref"]


def _count(api, sid=None):
    return api.get("/api/restock-orders", params={"limit": 1}).json()["total"]


def run_with_manual_confirm(api, sid, p, approve, monkeypatch):
    monkeypatch.setattr("backend.sessions.CONFIRM_TIMEOUT_S", 15)
    with ThreadPoolExecutor(1) as ex:
        fut = ex.submit(lambda: api.post(f"/api/session/{sid}/plan", json=p).json())
        deadline, cr = time.time() + 20, None
        while time.time() < deadline and not cr:
            evs = api.get(f"/api/session/{sid}/events").json()["events"]
            cr = next((e for e in evs if e["type"] == "confirm_request"), None)
            time.sleep(0.1)
        assert cr, "no confirm_request was emitted"
        assert api.post(f"/api/session/{sid}/confirm", json={"action_id": cr["data"]["action_id"], "approve": approve}).json()["ok"]
        return fut.result(timeout=30), cr["data"]


def test_write_requires_confirmation_decline_saves_nothing(api, sid, monkeypatch):
    ref, before = _restock_ref(api), None
    before = _count(api)
    p = plan(("write_api", {"api_id": "restock_orders.create", "body": {"product_id": ref}, "summary": "Restock " + ref}))
    res, cr = run_with_manual_confirm(api, sid, p, False, monkeypatch)
    assert cr["kind"] == "write" and cr["preview"]["status"] == "preview" and cr["preview"]["changes"][0]["op"] == "insert"
    assert not res["ok"] and res["results"][0]["error_code"] == "denied"
    assert _count(api) == before


def test_write_confirmed_is_logged_and_undoable(api, sid, monkeypatch):
    ref = _restock_ref(api)
    before = _count(api)
    p = plan(("write_api", {"api_id": "restock_orders.create", "body": {"product_id": ref}}))
    res, cr = run_with_manual_confirm(api, sid, p, True, monkeypatch)
    assert res["ok"] and _count(api) == before + 1
    ev = api.get(f"/api/session/{sid}/evidence/{res['evidence_ids'][0]}").json()
    assert ev["kind"] == "api_write" and ev["values"]["action_id"].startswith("ACT-")
    log = api.get("/api/actions", params={"actor": "agent", "limit": 1}).json()["items"][0]
    assert log["code"] == ev["values"]["action_id"] and log["api_id"] == "restock_orders.create"
    assert api.post(f"/api/session/{sid}/undo", json={"what": "write"}).json()["ok"]
    assert _count(api) == before
    assert not api.post(f"/api/session/{sid}/undo", json={"what": "write"}).json()["ok"]


def test_api_errors_surface_as_failed_steps(api, sid):
    ref = _restock_ref(api)
    p = plan(("write_api", {"api_id": "restock_orders.create", "body": {"product_id": ref}}),
             ("write_api", {"api_id": "restock_orders.create", "body": {"product_id": ref}}))
    r = api.post(f"/api/session/{sid}/plan", json={**p, "auto_confirm": True}).json()
    assert not r["ok"] and r["results"][1]["error_code"] == "api_error" and "restock_already_open" in r["results"][1]["message"]
    api.post(f"/api/session/{sid}/undo", json={"what": "write"})


def test_message_endpoint_reports_missing_agent(api, sid):
    r = api.post(f"/api/session/{sid}/message", json={"text": "hello"})
    assert r.status_code == 501 and r.json()["error"]["code"] == "agent_not_configured"
