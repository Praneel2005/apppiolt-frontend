"""Tests for agent/narrator.py placeholder interpolation and citation checks."""
import asyncio
from agent.narrator import check_numbers, fill_placeholders, narrate, narrate_navigation
from contracts.actions import AgentConfig, Intent, Plan
from contracts.ui_state import FilterValue, UiState


def test_narrate_navigation():
    plan = Plan(intent=Intent(name="navigate"))
    state = UiState(route="/ops/orders", page_id="ops.orders", filters={"customer_state": FilterValue(op="in", value=["RJ"])})
    text = narrate_navigation(plan, state, verified=True)
    assert "Opened Orders" in text
    assert "customer_state = RJ" in text
    assert "[Screen verified ✓]" in text


def test_fill_placeholders():
    evidence = {
        "E1": {"values": {"pct_change": 52.06, "rows": [{"category": "health_beauty", "value": 1250000}]}}
    }
    template = "Change was {E1.pct_change}%, category total was {E1.rows[0].value}."
    filled = fill_placeholders(template, evidence)
    assert "52.06" in filled
    assert "1,250,000" in filled


def test_check_numbers_detection():
    evidence_nums = {"52.06", "1250000"}
    user_query = "show revenue"

    # Grounded text passes
    valid_text = "Revenue grew by 52.06% reaching 1250000."
    assert check_numbers(valid_text, user_query, evidence_nums) is True

    # Hallucinated number 999.99 fails
    hallucinated_text = "Revenue grew by 52.06% with an extra 999.99 bonus."
    assert check_numbers(hallucinated_text, user_query, evidence_nums) is False


def test_narrate_analytics_flow():
    evidence = {
        "E1": {"values": {"pct_change": 15.5, "rows": []}}
    }
    plan = Plan(intent=Intent(name="compare_periods"))

    async def _run():
        text, citations, links = await narrate(plan, evidence, "compare revenue", config=AgentConfig(use_citation_check=True))
        assert "15.5" in text
        assert "E1" in citations

    asyncio.run(_run())
