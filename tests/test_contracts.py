import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.metadata import Application
from contracts.ui_state import ApplyState, RenderAck, UiState, canonical_series_hash
from contracts.actions import Plan

SAMPLE = Path(__file__).parent.parent / "examples" / "tenant_a_sample.json"


def load():
    return json.loads(SAMPLE.read_text())


def test_sample_tenant_validates():
    app = Application.model_validate(load())
    assert app.page("sales.revenue_by_region").route == "/sales/revenue-by-region"
    assert app.dataset("ds_sales_orders").field("region").values == ["North", "South", "East", "West"]


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d["pages"][0].update(widgets=["nope"]), "unknown widget"),
        (lambda d: d["widgets"][0].update(dataset_id="ds_missing"), "unknown dataset"),
        (lambda d: d["widgets"][0].update(metrics=["ghost_metric"]), "unknown metric"),
        (lambda d: d["pages"][0].update(directory="finance"), "unknown directory"),
        (lambda d: d["pages"][0].update(route="no-slash"), "must start with"),
        (lambda d: d["pages"][0].update(extra_field=1), "extra"),
    ],
)
def test_bad_exports_are_rejected(mutate, needle):
    data = load()
    mutate(data)
    with pytest.raises(ValidationError) as e:
        Application.model_validate(data)
    assert needle in str(e.value)


def test_duplicate_route_rejected():
    data = load()
    data["pages"].append({**data["pages"][0], "page_id": "sales.other"})
    with pytest.raises(ValidationError) as e:
        Application.model_validate(data)
    assert "duplicate route" in str(e.value)


def test_ui_state_and_ack_roundtrip():
    st = UiState.model_validate(
        {
            "route": "/sales/revenue-by-region",
            "page_id": "sales.revenue_by_region",
            "filters": {"region": {"op": "in", "value": ["West"]}},
            "date_range": {"from": "2026-04-01", "to": "2026-06-30", "preset": "last_quarter"},
        }
    )
    msg = ApplyState(version=1, nonce="n1", state=st, cause="set region=West")
    assert ApplyState.model_validate_json(msg.model_dump_json(by_alias=True)).state.filters["region"].value == ["West"]

    ack = RenderAck.model_validate(
        {
            "version": 1, "nonce": "n1", "route": st.route,
            "widgets": [{
                "widget_id": "sales.revenue_by_region.chart_main",
                "applied_filters": {"region": {"op": "in", "value": ["West"]}},
                "row_count": 1, "series_hash": "abc",
            }],
        }
    )
    assert ack.widgets[0].row_count == 1


def test_series_hash_is_order_independent_and_float_stable():
    a = [{"region": "West", "revenue": 1.0000000001}, {"region": "East", "revenue": 2.0}]
    b = [{"region": "East", "revenue": 2.0}, {"region": "West", "revenue": 1.0000000002}]
    assert canonical_series_hash(a, ["region", "revenue"]) == canonical_series_hash(b, ["region", "revenue"])
    c = [{"region": "West", "revenue": 1.5}, {"region": "East", "revenue": 2.0}]
    assert canonical_series_hash(a, ["region", "revenue"]) != canonical_series_hash(c, ["region", "revenue"])


def test_plan_rejects_unknown_tool():
    with pytest.raises(ValidationError):
        Plan.model_validate({"intent": {"name": "navigate"}, "steps": [{"tool": "delete_everything", "args": {}}]})


def test_hash_vectors_stable():
    """Frozen vectors: the TypeScript port must reproduce every hash in this file."""
    vec = json.loads((Path(__file__).parent.parent / "contracts" / "hash_vectors.json").read_text())
    for c in vec["cases"]:
        assert canonical_series_hash(c["rows"], c["columns"]) == c["hash"], c["name"]
    by = {c["name"]: c["hash"] for c in vec["cases"]}
    assert by["basic"] == by["order_independent"]


def test_hash_int_and_float_agree():
    assert canonical_series_hash([{"n": 2}], ["n"]) == canonical_series_hash([{"n": 2.0}], ["n"])


def test_as_of_date_and_new_contracts_import():
    app = Application.model_validate({**load(), "as_of_date": "2018-08-29"})
    assert app.as_of_date == "2018-08-29"
    from contracts.events import AgentEvent
    from contracts.query import QuerySpec, ValidationResult
    from contracts import interfaces  # noqa: F401
    from contracts.actions import AgentConfig

    AgentEvent(seq=1, type="action", data={"tool": "navigate"})
    QuerySpec(dataset_id="ds_sales_orders", metrics=["revenue"])
    assert ValidationResult(ok=True).issues == []
    assert AgentConfig().use_validator is True


def test_hash_tie_rounding_is_half_up_in_micro_units():
    # 0.0078125 is exactly 1/128: half-even vs half-up would differ; the contract is half-up on x*1e6
    from contracts.ui_state import _cell

    assert _cell(0.0078125) == "7813"
    assert _cell(-0.0078125) == "-7813"
    assert _cell(1.5) == "1500000"
    assert _cell(-0.0) == "0" and _cell(-0.0000001) == "0"


def test_hash_accepts_decimal_and_dates():
    from datetime import date
    from decimal import Decimal

    assert canonical_series_hash([{"m": Decimal("1.5")}], ["m"]) == canonical_series_hash([{"m": 1.5}], ["m"])
    assert canonical_series_hash([{"d": date(2018, 1, 2)}], ["d"]) == canonical_series_hash([{"d": "2018-01-02"}], ["d"])
