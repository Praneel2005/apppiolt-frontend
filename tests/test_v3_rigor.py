"""Statistical rigour: is a change unusual, the counterfactual, the interaction term and the reconciliation checks."""
from types import SimpleNamespace

import pytest

from agent.answer import compose_explain
from agent.reasoning.engine import explain
from agent.reasoning.facts import FactSheet
from agent.reasoning.gateway import Gateway, make_period
from agent.reasoning.rigor import (MIN_HISTORY, abnormal_change, counterfactual, interaction, pct_changes, reconcile_checks)
from agent.capabilities import get_capability_model
from agent.lexicon import get_lexicon
from agent.schema import Interp, TurnContext, get_agent_memory
from backend.sessions import create_session
from contracts.actions import AgentConfig


def test_pct_changes_skip_gaps_and_zero_bases():
    assert pct_changes([100, 110, None, 120, 0, 5]) == [10.0, -100.0]       # a fall to zero counts; a zero base is skipped
    assert pct_changes([10]) == [] and pct_changes([]) == []


def test_too_little_history_gives_no_verdict():
    r = abnormal_change(12.0, [1.0, -2.0, 3.0])
    assert r["verdict"] == "insufficient" and r["n"] == 3 and MIN_HISTORY > 3
    assert abnormal_change(None, [1.0] * 10)["verdict"] == "insufficient"


def test_a_normal_move_is_within_and_a_huge_one_is_unusual():
    history = [1.0, -1.5, 2.0, -2.5, 1.2, 0.5, -0.8, 1.7]
    normal = abnormal_change(1.5, history)
    assert normal["verdict"] == "within" and not normal["beyond_history"] and normal["n"] == 8
    big = abnormal_change(-18.0, history)
    assert big["verdict"] == "unusual" and big["beyond_history"] and big["z"] < -2
    assert big["low_pct"] == -2.5 and big["high_pct"] == 2.0


def test_a_perfectly_flat_history_does_not_make_everything_unusual():
    assert abnormal_change(0.4, [0.0] * 8)["verdict"] == "within"           # the scale has a floor
    assert abnormal_change(25.0, [0.0] * 8)["verdict"] == "unusual"


def test_one_extreme_month_in_the_history_does_not_hide_a_real_move():
    history = [1.0, -1.0, 2.0, -2.0, 1.5, 90.0, -1.5, 0.5]              # one launch-month spike; the median is robust to it
    assert abnormal_change(-20.0, history)["verdict"] == "unusual"


def test_counterfactual_is_exact_for_additive_metrics_only():
    cf = counterfactual(True, 90.0, 100.0, [], "state", {"group": "SP", "delta": -8.0})
    assert round(cf["pct_with"], 6) == -10.0 and round(cf["pct_without"], 6) == -2.0
    assert round(cf["share_of_change"], 6) == 80.0 and cf["delta_without"] == -2.0
    assert counterfactual(False, 90.0, 100.0, [], "state", {"group": "SP", "delta": -8.0}) is None
    assert counterfactual(True, 90.0, 0.0, [], "state", {"group": "SP", "delta": -8.0}) is None


def test_interaction_term_makes_the_split_exact_and_order_free():
    idn = {"volume": {"previous": 100.0, "current": 110.0}, "rate": {"previous": 10.0, "current": 9.0}}
    r = interaction(idn)
    assert (r["volume"], r["rate"], r["interaction"], r["total"]) == (100.0, -100.0, -10.0, -10.0)
    assert r["volume"] + r["rate"] + r["interaction"] == r["total"] and round(r["interaction_share"], 6) == 100.0
    assert interaction(None) is None and interaction({"volume": {"previous": None, "current": 1}, "rate": {"previous": 1, "current": 1}}) is None


def test_checks_pass_when_parts_add_up_and_fail_when_they_do_not():
    idn = {"volume": {"previous": 100.0, "current": 110.0}, "rate": {"previous": 10.0, "current": 9.0}}
    inter = interaction(idn)
    good = reconcile_checks(-10.0, idn, inter, [SimpleNamespace(mode="additive", dim="state", tail=0.0)])
    assert [c["ok"] for c in good] == [True, True]
    bad = reconcile_checks(-50.0, idn, inter, [SimpleNamespace(mode="additive", dim="state", tail=-20.0),
                                                SimpleNamespace(mode="indicative", dim="other", tail=-20.0)])
    assert [c["ok"] for c in bad] == [False, False] and len(bad) == 2           # the indicative scan is not checked


# ------------------------------------------------------------------------------------ with the database
def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _db_ok(), reason="database not available")
@pytest.mark.anyio
async def test_a_real_explanation_carries_the_reality_checks_with_every_number_a_fact():
    s = create_session()
    lex = get_lexicon()
    ctx = TurnContext(session=s, text="", page_ctx={"page": {}, "widgets": [], "filters": []}, cfg=AgentConfig(open_supporting_view=False),
                      lex=lex, cap=get_capability_model(), mem=get_agent_memory(s.id), llm=None, as_of=lex.as_of)
    it = Interp(intent="explain", metric="revenue", primary=make_period("2018-07-01", "2018-07-31"),
                baseline=make_period("2018-06-01", "2018-06-30"))
    result = None
    async for kind, payload in explain(ctx, it, Gateway(ctx), depth="full"):
        if kind == "result":
            result = payload
    assert result is not None and result.rigor is not None
    assert result.rigor.abnormal and result.rigor.abnormal["verdict"] in ("within", "unusual", "insufficient")
    fs = FactSheet()
    template, _ = compose_explain(result, fs)
    text = fs.render(template)
    assert "Reality checks" in text and "{F" not in text
    if result.rigor.abnormal["verdict"] != "insufficient":
        assert "Is this unusual?" in text
    assert any(c["name"] == "factors" for c in result.rigor.checks)
