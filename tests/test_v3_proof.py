"""Proof records: every cited number can be traced to a governed call and re-checked. Pure parts run anywhere."""
import pytest

from agent.reasoning import proof
from agent.reasoning.facts import FactSheet
from agent.reasoning.gateway import Gateway, make_period
from agent.reasoning.proof import build_claims, compare_values, fact_support, replay
from agent.skills.base import SkillResult
from agent.skills.data import run_data
from agent.schema import Interp
from contracts.actions import Evidence
from agent.capabilities import get_capability_model
from agent.lexicon import get_lexicon
from agent.schema import TurnContext, get_agent_memory
from backend.sessions import create_session
from contracts.actions import AgentConfig


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
    # no page navigation: with no browser attached it is simulated by re-querying every widget, which is slow and not under test
    return TurnContext(session=s, text="", page_ctx={"page": {}, "widgets": [], "filters": []},
                       cfg=AgentConfig(open_supporting_view=False), lex=lex,
                       cap=get_capability_model(), mem=get_agent_memory(s.id), llm=None, as_of=lex.as_of)


# ------------------------------------------------------------------------------------ pure
def test_compare_matches_rows_by_label_not_position():
    stored = {"total": 10.0, "rows": [{"state": "SP", "value": 7.0}, {"state": "RJ", "value": 3.0}], "view": {"x": 1}}
    fresh = {"total": 10.0, "rows": [{"state": "RJ", "value": 3.0}, {"state": "SP", "value": 7.0}]}
    r = compare_values(stored, fresh)
    assert r["match"] and r["checked"] == 3 and not r["differences"]


def test_compare_reports_a_changed_number_and_ignores_text_and_missing_paths():
    stored = {"total": 10.0, "rows": [{"state": "SP", "value": 7.0}], "note": "text 99"}
    fresh = {"total": 11.0, "rows": [{"state": "SP", "value": 7.0}, {"state": "MG", "value": 4.0}]}
    r = compare_values(stored, fresh)
    assert not r["match"] and [d["path"] for d in r["differences"]] == ["total"]
    assert compare_values({"note": "only text"}, {"note": "only text"})["match"] is False      # nothing comparable is not a match


def test_fact_support_levels():
    class S:  # a minimal session
        evidence = {"E1": Evidence(evidence_id="E1", kind="metric_query", source="revenue", values={"total": 1234.5, "rows": []})}
    fs = FactSheet()
    direct = fs.value("total", 1234.5, "BRL", evidence="E1")
    derived = fs.delta("gap", 99.0, "BRL", evidence="E1")
    none = fs.text("label", "SP")
    assert fact_support(direct, S) == "direct" and fact_support(derived, S) == "derived" and fact_support(none, S) == "none"


def test_claims_pair_each_line_with_its_facts_and_evidence():
    class S:
        evidence = {"E1": Evidence(evidence_id="E1", kind="metric_query", source="revenue", values={"total": 50.0})}
    fs = FactSheet()
    f1 = fs.value("rev", 50.0, "BRL", evidence="E1")
    tpl = "**Revenue** was {" + f1.id + "}.\nA line with no numbers.\n- Same again {" + f1.id + "}"
    claims = build_claims(fs, tpl, S)
    assert len(claims) == 2 and claims[0]["evidence"] == ["E1"] and claims[0]["support"][f1.id] == "direct"
    assert "R$ 50.00" in claims[0]["text"]
    assert build_claims(fs, None, S) == []                                    # bad input degrades to nothing, never raises


@pytest.mark.anyio
async def test_replay_without_a_proof_says_so():
    r = await replay(create_session(), "E404")                                  # needs no database
    assert r["ok"] is False and r["reason"] == "no_proof"


# ------------------------------------------------------------------------------------ with the database
@needs_db
@pytest.mark.anyio
async def test_a_data_answer_records_its_call_and_replays_to_the_same_numbers():
    ctx = make_ctx()
    it = Interp(intent="data", shape="breakdown", metric="revenue", group_by=["customer_state"],
                primary=make_period("2018-07-01", "2018-07-31"))
    out = SkillResult()
    async for _ in run_data(ctx, it, out):
        pass
    s = ctx.session
    assert out.citations and out.claims and all(c["evidence"] for c in out.claims)
    eid = out.citations[0]
    p = s.proofs[eid]
    assert p["exact"] and p["call"]["op"] == "query" and p["call"]["request"]["metric"] == "revenue"
    assert p["call"]["request"]["date_from"] == "2018-07-01" and p["metric"]["definition"] and p["row_count"]
    assert "_call" not in s.evidence[eid].values                                # the private tag never reaches the evidence
    r = await replay(s, eid)
    assert r["ok"] and r["match"] and r["checked"] > 0, r

    s.evidence[eid].values["total"] = float(s.evidence[eid].values["total"]) + 1.0     # tamper with what was "shown"
    r2 = await replay(s, eid)
    assert r2["ok"] and not r2["match"] and any(d["path"] == "total" for d in r2["differences"])


@needs_db
@pytest.mark.anyio
async def test_derived_evidence_lists_the_queries_it_used_and_cannot_be_replayed_as_one_call():
    ctx = make_ctx()
    gw = Gateway(ctx)
    a = await gw.query("revenue", [], {}, make_period("2018-07-01", "2018-07-31"))
    b = await gw.query("orders", [], {}, make_period("2018-07-01", "2018-07-31"))
    eid = await gw.evidence("comparison", "revenue", {"rows": [{"label": "revenue", "value": a["total"]}, {"label": "orders", "value": b["total"]}]})
    p = ctx.session.proofs[eid]
    assert p["exact"] is False and len(p["calls"]) == 2
    r = await replay(ctx.session, eid)
    assert r["ok"] is False and r["reason"] == "derived"
