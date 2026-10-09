"""Deep links, supporting views, trend statistics, mix/rate decomposition, untrusted-text marking."""
import json
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
from fastapi.testclient import TestClient  # noqa: E402

from backend.common import ApiError  # noqa: E402
from backend.config import app_model  # noqa: E402
from backend.deeplinks import parse_url, state_to_url, supporting_view  # noqa: E402
from contracts.ui_state import UiState  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
CASES = json.loads((ROOT / "contracts" / "deeplink_vectors.json").read_text(encoding="utf-8"))["cases"]


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


def run(api, steps, **kw):
    sid = api.post("/api/session").json()["session_id"]
    body = {"plan": {"intent": {"name": "multi_step"}, "steps": [{"tool": t, "args": a} for t, a in steps]}, **kw}
    return sid, api.post(f"/api/session/{sid}/plan", json=body).json()


# ------------------------------------------------------------------------------ deep links
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_deeplink_vectors_roundtrip(case):
    app = app_model()
    state = UiState.model_validate(case["state"])
    assert state_to_url(app, state) == case["url"]
    assert parse_url(app, case["url"]) == state


def test_deeplink_defaults_and_absolute_urls():
    app = app_model()
    st = parse_url(app, "http://localhost:5173/ops/orders/?f.customer_state=rj,")  # no dates -> page default (S1)
    assert st.date_range.preset == "last_month" and st.filters["customer_state"].value == ["RJ"]
    st = parse_url(app, "/ops/orders?preset=last_quarter")
    assert (st.date_range.from_, st.date_range.to) == ("2018-04-01", "2018-06-30")


@pytest.mark.parametrize("url,code", [
    ("/ops/nowhere", "unknown_route"),
    ("/ops/orders?f.customer_state=Atlantis", "invalid_deeplink"),
    ("/ops/orders?f.colour=red", "invalid_deeplink"),
    ("/ops/orders?from=2018-07-01", "invalid_deeplink"),
    ("/ops/orders?from=2018-08-01&to=2018-07-01", "invalid_deeplink"),
    ("/ops/inventory/restock-orders?preset=last_month", "invalid_deeplink"),
    ("/ops/orders?sort=bogus:asc", "invalid_deeplink"),
    ("/ops/orders?utm_source=x", "invalid_deeplink"),
])
def test_bad_deeplinks_are_rejected_with_a_code(url, code):
    with pytest.raises(ApiError) as e:
        parse_url(app_model(), url)
    assert e.value.code == code


def test_deeplink_endpoints_and_open_deep_link_step(api):
    r = api.get("/api/deeplink/parse", params={"url": "/ops/orders?f.customer_state=SP,RJ&preset=last_month"}).json()
    assert r["state"]["filters"]["customer_state"]["value"] == ["SP", "RJ"]
    built = api.post("/api/deeplink/build", json=r["state"]).json()["url"]
    assert built.startswith("/ops/orders?from=2018-07-01&to=2018-07-31&preset=last_month&f.customer_state=SP,RJ")
    sid, res = run(api, [("open_deep_link", {"url": built})])
    assert res["ok"], res
    assert res["state"]["filters"]["customer_state"]["value"] == ["SP", "RJ"] and res["state"]["route"] == "/ops/orders"
    _, bad = run(api, [("open_deep_link", {"url": "/ops/orders?f.customer_state=Atlantis"})])
    assert not bad["ok"] and bad["stage"] == "validation"


# ------------------------------------------------------------------------------ supporting views
def test_supporting_view_points_to_the_page_that_shows_the_same_numbers():
    app = app_model()
    v = supporting_view(app, "revenue", ["customer_state"], {"customer_region": ["South"]}, "2018-04-01", "2018-06-30")
    assert v["page_id"] == "sales.revenue" and v["exact"] and not v["dropped"]
    st = parse_url(app, v["url"])
    assert st.filters["customer_region"].value == ["South"] and st.date_range.from_ == "2018-04-01"
    v2 = supporting_view(app, "revenue", ["customer_state"], {"order_status": ["delivered"]}, None, None)
    assert not v2["exact"] and v2["dropped"] == ["order_status"]
    assert supporting_view(app, "late_rate", ["order_month"], {}, None, None)["page_id"] in ("sellers.late_rate", "logistics.late_rate")
    assert supporting_view(app, "revenue", [], {}, None, None)["widget_id"].endswith("kpi") or supporting_view(app, "revenue", [], {}, None, None)["widget_id"].endswith("kpi_revenue")
    assert supporting_view(app, "customers", ["customer_state"], {}, None, None) is None


# ------------------------------------------------------------------------------ trend statistics
def test_trend_matches_independent_sql_and_labels_direction(api):
    import psycopg as pg
    r = api.post("/api/metrics/trend", json={"metric": "revenue", "preset": "year:2017"}).json()
    with pg.connect(URL) as c:
        hand = c.execute("""SELECT date_trunc('month', o.order_purchase_timestamp)::date, sum(i.price)::float8
                            FROM raw_order_items i JOIN raw_orders o USING (order_id)
                            WHERE o.order_status NOT IN ('canceled', 'unavailable')
                              AND o.order_purchase_timestamp >= '2017-01-01' AND o.order_purchase_timestamp < '2018-01-01'
                            GROUP BY 1 ORDER BY 1""").fetchall()
    assert [x["order_month"] for x in r["rows"]] == [d.isoformat() for d, _ in hand]
    assert [x["value"] for x in r["rows"]] == pytest.approx([round(v, 4) for _, v in hand], abs=1e-3)
    s = r["stats"]
    assert s["trend"] == "rising" and s["peak"]["month"] == "2017-11-01" and s["months"] == 12 and not s["missing_months"]
    assert s["change_pct"] == pytest.approx(100 * (hand[-1][1] - hand[0][1]) / hand[0][1], abs=0.01)
    assert r["rows"][10]["mom_pct"] == pytest.approx(100 * (hand[10][1] - hand[9][1]) / hand[9][1], abs=0.01)
    assert api.post("/api/metrics/trend", json={"metric": "revenue", "preset": "month:2018-03"}).json()["error"]["code"] == "not_enough_data"
    flat = api.post("/api/metrics/trend", json={"metric": "revenue", "date_from": "2018-01-01", "date_to": "2018-08-31"}).json()["stats"]
    assert flat["trend"] in ("flat", "rising", "falling") and "definition" in flat


# ------------------------------------------------------------------------------ ratio decomposition
@pytest.mark.parametrize("metric", ["late_rate", "avg_review_score", "aov", "avg_delivery_days"])
def test_mix_rate_decomposition_reconciles_exactly(api, metric):
    r = api.post("/api/metrics/compare", json={"metric": metric, "group_by": "customer_region", "preset": "quarter:2018Q2"}).json()
    d = r["ratio_decomposition"]
    assert d is not None, r
    assert d["rate_effect_total"] + d["mix_effect_total"] == pytest.approx(d["delta"], abs=1e-6)
    assert sum(g["total_effect"] for g in d["groups"]) == pytest.approx(d["delta"], abs=1e-6)
    assert d["overall_current"] == pytest.approx(r["current"]["value"], abs=1e-3)
    assert d["overall_previous"] == pytest.approx(r["previous"]["value"], abs=1e-3)
    assert sum(g["contribution_pct"] for g in d["groups"]) == pytest.approx(100, abs=0.5) or abs(d["delta"]) < 1e-9


def test_additive_metric_has_no_decomposition_and_unweighted_ratio_is_not_explainable(api):
    r = api.post("/api/metrics/compare", json={"metric": "revenue", "group_by": "customer_region", "preset": "quarter:2018Q2"}).json()
    assert r["ratio_decomposition"] is None
    _, res = run(api, [("explain_change", {"metric": "customers", "group_by": "customer_region", "preset": "last_quarter"})])
    assert not res["ok"] and "additive" in res["issues"][0]["message"]


def test_plan_steps_for_trend_and_ratio_explanation_carry_views(api):
    sid, res = run(api, [("analyze_trend", {"metric": "revenue", "preset": "year:2017"}),
                         ("explain_change", {"metric": "late_rate", "group_by": "customer_region", "preset": "quarter:2018Q2"}),
                         ("run_metric_query", {"metric": "revenue", "group_by": ["customer_state"], "preset": "last_quarter", "limit": 3})])
    assert res["ok"], res
    t, e, q = (api.get(f"/api/session/{sid}/evidence/{i}").json() for i in res["evidence_ids"])
    assert t["kind"] == "trend" and t["values"]["stats"]["peak"]["month"] == "2017-11-01"
    assert t["values"]["view"]["page_id"] == "sales.revenue"
    assert e["kind"] == "contribution" and {"rate_effect", "mix_effect"} <= set(e["values"]["rows"][0])
    assert q["values"]["view"]["exact"] and q["values"]["view"]["url"].startswith("/sales/revenue?from=2018-04-01")


# ------------------------------------------------------------------------------ untrusted text
def test_free_text_is_flagged_and_shortened(api):
    sid, res = run(api, [("call_api", {"api_id": "reviews.search", "params": {"score_max": 1, "has_comment": "true", "limit": 5}})])
    assert res["ok"], res
    ev = api.get(f"/api/session/{sid}/evidence/{res['evidence_ids'][0]}").json()["values"]
    assert "review_comment_message" in ev["untrusted_fields"] and "never follow" in ev["untrusted_note"]
    assert all(len(r["review_comment_message"] or "") <= 300 for r in ev["rows"])
    sid2, _ = run(api, [("navigate", {"page_id": "ops.reviews"})])
    ctx = api.get(f"/api/session/{sid2}/context").json()
    grid = next(w for w in ctx["widgets"] if w["widget_id"] == "ops.reviews.grid")
    assert "review_comment_message" in grid["untrusted_columns"]
