"""Analysis engine, facts and the claim checker. Pure parts run anywhere; the numbers are checked against independent SQL."""
from datetime import date

import pytest

from agent.answer import check_text, compose_explain
from agent.capabilities import get_capability_model
from agent.lexicon import get_lexicon
from agent.reasoning.engine import DimScan, ExplainResult, compare_slices, explain, health_scan, trend_summary
from agent.reasoning.facts import FactSheet, fmt_number, fmt_pct
from agent.reasoning.gateway import Gateway, align_periods, default_periods, make_period, next_period, previous_of
from agent.schema import Interp, SliceSel, TurnContext, get_agent_memory
from backend.sessions import create_session
from contracts.actions import AgentConfig

AS_OF = "2018-08-31"


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(not _db_ok(), reason="database not available")


def make_ctx():
    s = create_session()
    lex = get_lexicon()
    return TurnContext(session=s, text="", page_ctx={"page": {}, "widgets": [], "filters": []}, cfg=AgentConfig(), lex=lex,
                       cap=get_capability_model(), mem=get_agent_memory(s.id), llm=None, as_of=lex.as_of)


# ------------------------------------------------------------------------------------ pure
def test_number_formatting():
    assert fmt_number(869578.74, "BRL") == "R$ 869,578.74"
    assert fmt_number(-18072.771, "BRL", signed=True) == "-R$ 18,072.77"
    assert fmt_number(0.2219, "ratio") == "22.2%"
    assert fmt_number(0.0071, "ratio", signed=True) == "+0.7 pp"
    assert fmt_number(1234, "count") == "1,234"
    assert fmt_number(12.34, "days") == "12.3 days"
    assert fmt_pct(-3.04) == "-3.0%" and fmt_pct(-3.04, signed=False) == "3.0%"


def test_fact_sheet_render_and_signs():
    fs = FactSheet()
    d = fs.delta("change", -50.0, "BRL")
    assert d.sign == -1 and d.text == "-R$ 50.00"
    assert fs.render("down {" + d.id + "} and {F99}") == "down -R$ 50.00 and {F99}"


def test_claim_checker():
    fs = FactSheet()
    d = fs.pct("chg", -5.0)
    v = fs.value("rev", 100.0, "BRL")
    ok = "Revenue fell {" + d.id + "} to {" + v.id + "}."
    assert check_text(ok, fs, set()) == []
    assert any("direction" in i for i in check_text("Revenue rose {" + d.id + "}.", fs, set()))
    assert any("number" in i for i in check_text("Revenue fell 5% last week.", fs, set()))
    assert any("unknown placeholder" in i for i in check_text("See {F99}.", fs, set()))
    assert check_text("In 2018 revenue fell {" + d.id + "}.", fs, set()) == []          # years are fine
    assert check_text("You asked about 12 months; revenue fell {" + d.id + "}.", fs, {"12"}) == []
    assert any("overconfident" in i for i in check_text("This proves it.", fs, set()))


def test_period_helpers():
    july = make_period("2018-07-01", "2018-07-31")
    nxt = next_period(july, AS_OF)
    assert (nxt.start, nxt.end) == ("2018-08-01", "2018-08-31")
    assert next_period(make_period("2018-08-01", "2018-08-31"), AS_OF) is None
    p, b, notes = default_periods(None, july, AS_OF)
    assert (p.start, b.start) == ("2018-08-01", "2018-07-01") and notes
    p, b, _ = default_periods(None, None, AS_OF)
    assert (p.start, p.end, b.start, b.end) == ("2018-08-01", "2018-08-31", "2018-07-01", "2018-07-31")
    p, b, _ = default_periods(july, None, AS_OF)
    assert (b.start, b.end) == ("2018-06-01", "2018-06-30")
    q = previous_of(make_period("2018-04-01", "2018-06-30"))
    assert (q.start, q.end) == ("2018-01-01", "2018-03-31")


def test_alignment_is_like_for_like_when_the_current_period_is_incomplete():
    cur = make_period("2018-08-01", "2018-08-15", "this_month")
    base = make_period("2018-07-01", "2018-07-31")
    p, b, notes = align_periods(cur, base, "2018-08-15")
    assert (b.start, b.end) == ("2018-07-01", "2018-07-15") and any("like-for-like" in n for n in notes)
    year = make_period("2018-01-01", "2018-12-31", "this_year")
    last = make_period("2017-01-01", "2017-12-31")
    p, b, notes = align_periods(year, last, AS_OF)
    assert p.end == "2018-08-31" and b.end == "2017-08-31"
    full = align_periods(make_period("2018-08-01", "2018-08-31", "this_month"), base, AS_OF)
    assert full[1].end == "2018-07-31"                                                    # complete month: untouched


def test_trend_summary_streak_and_outlier():
    rows = [{"order_month": f"2018-0{i}-01", "value": v} for i, v in enumerate([100, 102, 99, 101, 100, 98, 60], 1)]
    s = trend_summary({"rows": rows, "stats": {"trend": "falling"}})
    assert s["streak"] == 3 and s["streak_dir"] == -1 and s["z_latest"] < -2   # 60 < 98 < 100 < 101: three falls in a row
    down = trend_summary({"rows": [{"order_month": "x", "value": v} for v in (5, 4, 3, 2, 1)], "stats": {}})
    assert down["streak"] == 4


def _result(**kw) -> ExplainResult:
    base = dict(metric="revenue", title="Revenue", unit="BRL", additive=True, higher_is_better=True,
                primary=make_period("2018-08-01", "2018-08-31"), baseline=make_period("2018-07-01", "2018-07-31"),
                filters={}, current=800.0, previous=1000.0, delta=-200.0, pct=-20.0, direction="down",
                expected_direction="none", premise="none", head_evidence="E1")
    base.update(kw)
    return ExplainResult(**base)


def test_compose_explain_states_the_truth_and_resolves_every_placeholder():
    sc = DimScan("customer_state", "additive", 5, 300.0, 0.9, 0.3,
                 [{"group": "SP", "delta": -200.0, "current": 1.0, "previous": 2.0, "contribution_pct": 100.0}],
                 [{"group": "MG", "delta": 50.0, "current": 3.0, "previous": 2.5, "contribution_pct": -25.0}], 0.0, [], "E2")
    ident = {"explain": "Revenue = orders x average order value", "evidence_id": "E3", "total": -200.0, "unit": "BRL",
             "volume": {"metric": "orders", "title": "Orders", "previous": 10, "current": 8, "pct": -20.0, "effect": -200.0},
             "rate": {"metric": "aov", "title": "Average order value", "previous": 100, "current": 100, "pct": 0.0, "effect": 0.0}}
    res = _result(scans=[sc], identity=ident, expected_direction="up", premise="contradicted")
    fs = FactSheet()
    template, sugg = compose_explain(res, fs)
    text = fs.render(template)
    assert "Revenue fell" in text and "R$ 800.00" in text and "-R$ 200.00" in text
    assert "{F" not in text
    assert "Volume vs value" in text and "By customer state" in text and "offsetting" in text
    assert "mentioned a rise" in text and sugg
    up = _result(direction="up", delta=200.0, pct=20.0, expected_direction="down", premise="contradicted")
    fs2 = FactSheet()
    t2, _ = compose_explain(up, fs2)
    assert "did not drop" in fs2.render(t2)


# ------------------------------------------------------------------------------------ against the database
def _sql_total(where: str = "") -> float:
    from backend.db import fetch_all
    rows = fetch_all("SELECT SUM(price) AS v FROM fact_order_items WHERE is_valid_sale " + where)
    return float(rows[0]["v"] or 0)


async def _run(gen):
    steps, result = [], None
    async for kind, payload in gen:
        (steps if kind == "step" else []).append(payload)
        if kind == "result":
            result = payload
    return steps, result


@needs_db
@pytest.mark.anyio
async def test_explain_reconciles_and_matches_independent_sql():
    ctx = make_ctx()
    it = Interp(intent="explain", metric="revenue", primary=make_period("2018-07-01", "2018-07-31"),
                baseline=make_period("2018-06-01", "2018-06-30"), expected_direction="none")
    steps, res = await _run(explain(ctx, it, Gateway(ctx), depth="full"))
    jul = _sql_total("AND order_date BETWEEN '2018-07-01' AND '2018-07-31'")
    jun = _sql_total("AND order_date BETWEEN '2018-06-01' AND '2018-06-30'")
    assert res.current == pytest.approx(jul, rel=1e-6) and res.previous == pytest.approx(jun, rel=1e-6)
    assert res.delta == pytest.approx(jul - jun, abs=0.01)
    assert steps and res.scans and res.confounders
    for sc in res.scans:                                      # additive drivers add up to the whole change
        assert sc.mode == "additive"
        assert sum(r["delta"] for r in sc.rows) == pytest.approx(res.delta, abs=max(1.0, 0.001 * abs(res.delta)))
    ident = res.identity
    assert ident and ident["volume"]["effect"] + ident["rate"]["effect"] == pytest.approx(res.delta, abs=max(1.0, 0.001 * abs(res.delta)))
    assert len(res.evidence_ids) >= 4
    assert res.trend is None or res.trend["months"] >= 3


@needs_db
@pytest.mark.anyio
async def test_slice_comparison_matches_independent_sql():
    ctx = make_ctx()
    it = Interp(intent="compare", metric="revenue", compare_kind="slices", primary=make_period("2018-03-01", "2018-03-31"),
                slices=[SliceSel("SP", {"customer_state": ["SP"]}), SliceSel("RJ", {"customer_state": ["RJ"]})])
    _, res = await _run(compare_slices(ctx, it, Gateway(ctx)))
    sp = _sql_total("AND order_date BETWEEN '2018-03-01' AND '2018-03-31' AND customer_state = 'SP'")
    rj = _sql_total("AND order_date BETWEEN '2018-03-01' AND '2018-03-31' AND customer_state = 'RJ'")
    vals = {s["label"]: s["value"] for s in res.slices}
    assert vals["SP"] == pytest.approx(sp, rel=1e-6) and vals["RJ"] == pytest.approx(rj, rel=1e-6)
    assert res.gap_abs == pytest.approx(sp - rj, abs=0.01) and res.leader == ("SP" if sp > rj else "RJ")


@needs_db
@pytest.mark.anyio
async def test_health_scan_covers_the_main_metrics():
    ctx = make_ctx()
    it = Interp(intent="investigate")
    _, res = await _run(health_scan(ctx, it, Gateway(ctx)))
    assert len(res.items) >= 5 and all("pct" in i for i in res.items)


def test_slices_that_differ_in_one_dimension_can_be_shown_by_a_page():
    from agent.skills.reason import _slice_dimension

    class R:
        slices = [{"filters": {"customer_state": ["SP"], "product_category": ["toys"]}},
                  {"filters": {"customer_state": ["RJ"], "product_category": ["toys"]}}]
    assert _slice_dimension(R) == ("customer_state", ["SP", "RJ"])
    R.slices = [{"filters": {"customer_state": ["SP"]}}, {"filters": {"product_category": ["toys"]}}]
    assert _slice_dimension(R) is None
