"""L1 Perceive: periods, entities, cues and coverage (no database, no LLM)."""
import pytest

from agent.lexicon import get_lexicon
from agent.perceive import perceive

AS_OF = "2018-08-31"


@pytest.fixture(scope="module")
def lex():
    return get_lexicon()


def P(text, lex):
    return perceive(text, lex, AS_OF)


def periods(ann):
    return [(p.start, p.end, r) for p, r in ann.roles]


def test_last_three_months_is_not_last_quarter(lex):
    a = P("AOV in SP and RJ, last 3 months", lex)
    assert periods(a) == [("2018-06-01", "2018-08-31", "primary")]
    assert a.periods[0].preset == "months:3"


def test_relative_and_named_periods(lex):
    assert periods(P("revenue for last month", lex)) == [("2018-07-01", "2018-07-31", "primary")]
    assert periods(P("orders in March 2018", lex)) == [("2018-03-01", "2018-03-31", "primary")]
    assert periods(P("revenue Q2 2018", lex))[0][:2] == ("2018-04-01", "2018-06-30")
    assert periods(P("sales since June", lex))[0][:2] == ("2018-06-01", "2018-08-31")
    assert periods(P("sales in 2017", lex))[0][:2] == ("2017-01-01", "2017-12-31")
    assert periods(P("sales from january to march 2018", lex))[0][:2] == ("2018-01-01", "2018-03-31")


def test_comparison_roles(lex):
    a = P("revenue in march vs february", lex)
    assert [(p.start[:7], r) for p, r in a.roles] == [("2018-03", "primary"), ("2018-02", "baseline")]
    b = P("why did sales go down from last month", lex)
    assert [(p.start, r) for p, r in b.roles] == [("2018-07-01", "baseline")]
    c = P("show orders from last month", lex)
    assert [(p.start, r) for p, r in c.roles] == [("2018-07-01", "primary")]
    d = P("why did revenue drop compared to last quarter", lex)
    assert d.roles[0][1] == "baseline"


def test_entities_and_roles(lex):
    a = P("Sales for Southeast in São Paulo", lex)
    keys = {c.key + "=" + c.canonical for v in a.values for c in v.cands}
    assert "customer_state=SP" in keys
    assert any(c.key == "customer_region" for v in a.values for c in v.cands)
    b = P("compare revenue in sp and rj", lex)
    assert {c.canonical for v in b.values for c in v.cands if c.key == "customer_state"} == {"SP", "RJ"}
    assert P("sellers in PR", lex).seller_context is True
    assert "RJ" in {c.canonical for v in P("orders from Rio", lex).values for c in v.cands}


def test_state_code_words_do_not_fire_on_english(lex):
    for text in ("go to orders", "GO TO ORDERS", "take me to the tickets page", "what is the revenue for it"):
        a = P(text, lex)
        assert not [v for v in a.values if any(c.key.endswith("state") for c in v.cands)], text


def test_cues_metrics_dimensions(lex):
    a = P("why did sales drop", lex)
    assert a.has("explain") and a.has("change_down") and a.expected_direction == "down"
    assert a.metric_ids == ["revenue"]
    b = P("revenue by state last quarter", lex)
    assert b.dims and b.dims[0].dim == "customer_state" and b.dims[0].group
    c = P("top 5 categories by sales this year", lex)
    assert c.top_n == 5 and c.sort_dir == "desc"
    assert P("average review score trend", lex).has("trend")
    assert P("how are we doing overall", lex).has("health")


def test_open_is_a_verb_at_the_start_but_a_value_later(lex):
    a = P("open orders in RJ", lex)
    assert not [v for v in a.values if any(c.canonical == "open" for c in v.cands)]
    b = P("show open tickets", lex)
    assert [v for v in b.values if any(c.canonical == "open" for c in v.cands)]


def test_coverage_flags_unexplained_words(lex):
    ok = P("show late deliveries in RJ last month", lex)
    assert ok.coverage >= 0.8 and len(ok.unexplained) <= 1
    odd = P("show me the flurbo zorp quantum", lex)
    assert len(odd.unexplained) >= 2
