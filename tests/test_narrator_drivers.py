"""Tests for driver and 'why did metric change' narration (Section 9 in AGENT_V2_DESIGN.md)."""
import pytest
from agent.narrator import narrate
from contracts.actions import Intent, Plan


@pytest.mark.anyio
async def test_why_sales_changed_narration():
    """Query: 'Why did sales decrease compared to last month'."""
    plan = Plan(intent=Intent(name="explain_change"), steps=[])
    evidence_map = {
        "E1": {
            "values": {
                "title": "Revenue",
                "unit": "BRL",
                "current": {"from": "2018-07-01", "to": "2018-07-31", "value": 850000.0},
                "previous": {"from": "2018-06-01", "to": "2018-06-30", "value": 900000.0},
                "delta": -50000.0,
                "pct_change": -5.56,
                "drivers": [
                    {"group": "SP", "current": 320000.0, "previous": 350000.0, "delta": -30000.0, "contribution_pct": 60.0},
                    {"group": "RJ", "current": 110000.0, "previous": 125000.0, "delta": -15000.0, "contribution_pct": 30.0},
                ],
            }
        }
    }

    ans_text, citations, _ = await narrate(
        plan, evidence_map, "why did sales decrease compared to last month"
    )

    assert "Revenue Comparison" in ans_text
    assert "decreased" in ans_text
    assert "-5.56%" in ans_text
    assert "Key Drivers of Change [E1]" in ans_text
    assert "SP" in ans_text
    assert "60.0% contribution" in ans_text
    assert citations == ["E1"]
