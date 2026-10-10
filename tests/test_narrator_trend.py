"""Tests for trend and analytics narration (WP5 in AGENT_V2_DESIGN.md)."""
import pytest
from agent.narrator import narrate
from contracts.actions import Intent, Plan


@pytest.mark.anyio
async def test_trend_narration_with_stats():
    """Audit query 8: Month-over-month growth 2017 states growth per month, direction, peak and trough."""
    plan = Plan(intent=Intent(name="analyze_trend"), steps=[])
    evidence_map = {
        "E1": {
            "values": {
                "title": "Monthly Revenue",
                "unit": "BRL",
                "stats": {
                    "trend": "rising",
                    "change_pct": 142.5,
                    "peak": {"month": "2017-11-01", "value": 1150000.0},
                    "trough": {"month": "2017-01-01", "value": 450000.0},
                },
                "rows": [
                    {"order_month": "2017-01-01", "value": 450000.0, "mom_pct": None},
                    {"order_month": "2017-02-01", "value": 520000.0, "mom_pct": 15.6},
                    {"order_month": "2017-11-01", "value": 1150000.0, "mom_pct": 25.0},
                ],
            }
        }
    }

    ans_text, citations, deep_links = await narrate(
        plan, evidence_map, "Month-over-month growth 2017"
    )

    assert "rising" in ans_text
    assert "Peak" in ans_text
    assert "2017-11-01" in ans_text
    assert "Trough" in ans_text
    assert "2017-01-01" in ans_text
    assert "Monthly Growth (MoM)" in ans_text
    assert "+15.6%" in ans_text
    assert citations == ["E1"]
