"""Unit tests for agent analysis engine (WP7 in AGENT_V2_DESIGN.md)."""
import pytest
from agent.analysis import check_period_comparability, reconcile_drivers


def test_period_comparability():
    # Equal 30-day periods
    res = check_period_comparability("2018-07-01", "2018-07-31", "2018-06-01", "2018-06-30")
    # July is 31 days, June is 30 days
    assert res["is_comparable"] is False
    assert len(res["warnings"]) > 0

    # Equal lengths
    res_eq = check_period_comparability("2018-06-01", "2018-06-30", "2018-05-01", "2018-05-30")
    assert res_eq["is_comparable"] is True
    assert len(res_eq["warnings"]) == 0


def test_reconcile_drivers():
    drivers = [
        {"group": "SP", "delta": -50.0},
        {"group": "RJ", "delta": -30.0},
        {"group": "MG", "delta": -10.0},
    ]
    tot_delta = -100.0
    rec = reconcile_drivers(drivers, tot_delta)

    assert rec["sum_drivers"] == -90.0
    assert rec["total_delta"] == -100.0
    assert rec["unexplained_delta"] == -10.0
    assert rec["unexplained_pct"] == 10.0
    assert rec["reconciled"] is False

    # Fully explained
    drivers_full = drivers + [{"group": "PR", "delta": -10.0}]
    rec_full = reconcile_drivers(drivers_full, tot_delta)
    assert rec_full["unexplained_delta"] == 0.0
    assert rec_full["reconciled"] is True
