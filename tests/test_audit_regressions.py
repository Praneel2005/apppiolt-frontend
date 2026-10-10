"""Audit regression suite (Section 17 in AGENT_V2_DESIGN.md)."""
import pytest
from agent.normalise.dates import parse_date_query
from agent.normalise.entities import extract_geo_entities
from agent.frame import extract_frame_by_rules
from agent.router import route_frame
from agent.compiler import compile_frame_to_plan
from backend.sessions import create_session, get_session
from agent.agent import AppPilotAgent

AS_OF = "2018-08-31"


def test_reg_1_aov_sp_last_quarter():
    """1. 'Average order value in SP, last quarter' -> one atomic push, verified."""
    f = extract_frame_by_rules("Average order value in SP, last quarter", {}, as_of=AS_OF)
    r = route_frame(f)
    p = compile_frame_to_plan(f, r)

    assert r.skill == "Operate"
    assert r.target_page_id == "sales.aov"
    assert len(p.steps) >= 1
    nav_step = p.steps[0]
    assert nav_step.tool == "navigate"
    assert nav_step.args["page_id"] == "sales.aov"
    assert nav_step.args["filters"] == {"customer_state": ["SP"]}
    assert nav_step.args["date_range"]["preset"] == "last_quarter"
    assert nav_step.args["date_range"]["from"] == "2018-04-01"
    assert nav_step.args["date_range"]["to"] == "2018-06-30"


def test_reg_2_aov_sp_rj_last_3_months():
    """2. 'AOV in SP and RJ, last 3 months' -> period is last 3 months (not last quarter), both states applied."""
    f = extract_frame_by_rules("AOV in SP and RJ, last 3 months", {}, as_of=AS_OF)
    r = route_frame(f)
    p = compile_frame_to_plan(f, r)

    assert f.period.preset == "months:3"
    assert f.period.from_date == "2018-06-01"
    assert f.period.to_date == "2018-08-31"
    assert set(p.steps[0].args["filters"]["customer_state"]) == {"RJ", "SP"}


def test_reg_3_sales_southeast_sao_paulo_hierarchy():
    """3. 'Sales for Southeast in São Paulo' -> hierarchy handled, both recognized."""
    geo = extract_geo_entities("Sales for Southeast in São Paulo")
    assert geo.region == "Southeast"
    assert geo.states == ["SP"]
    assert geo.is_hierarchy_subset is True


def test_reg_4_compare_revenue_sp_rj_no_stray_canvas():
    """4. 'Compare revenue in SP and RJ' -> comparison, no stray canvas tool in plan."""
    f = extract_frame_by_rules("Compare revenue in SP and RJ", {}, as_of=AS_OF)
    r = route_frame(f)
    p = compile_frame_to_plan(f, r)

    assert r.skill in ("Analyze", "Operate")
    tool_names = [st.tool for st in p.steps]
    assert "render_canvas" not in tool_names


def test_reg_5_sellers_in_pr_seller_role():
    """5. 'Sellers in PR' -> role disambiguated to seller_state."""
    geo = extract_geo_entities("Sellers in PR")
    assert geo.states == ["PR"]
    assert geo.role == "seller_state"


def test_reg_7_revenue_last_month():
    """7. 'Revenue for last month' -> resolved month."""
    d = parse_date_query("Revenue for last month", as_of=AS_OF)
    assert d.preset == "last_month"
    assert d.from_date == "2018-07-01"
    assert d.to_date == "2018-07-31"


def test_reg_9_delete_all_tickets_refused():
    """9. 'Delete all tickets' -> refused."""
    f = extract_frame_by_rules("Delete all tickets", {}, as_of=AS_OF)
    assert f.act == "decline"
    r = route_frame(f)
    assert r.skill == "Converse"
    p = compile_frame_to_plan(f, r)
    assert p.intent.name in ("unsafe_action", "clarify_needed")
    assert len(p.steps) == 0
