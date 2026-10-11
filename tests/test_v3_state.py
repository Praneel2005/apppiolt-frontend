"""Each answer remembers the screen it was about, so the chat can tell when the screen has changed since."""
import pytest

from agent.agent import AppPilotAgent, screen_of
from backend.sessions import create_session
from contracts.actions import AgentConfig
from contracts.ui_state import DateRange, FilterValue, UiState


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


def test_a_screen_description_is_stable_and_complete():
    st = UiState(route="/x", page_id="p", filters={"b": FilterValue(op="in", value=["RJ", "MG"]), "a": FilterValue(op="eq", value=["x"])},
                 date_range=DateRange.model_validate({"from": "2018-07-01", "to": "2018-07-31", "preset": None}), version=4)
    d = screen_of(st, True)
    assert d["page_id"] == "p" and d["filters"] == {"b": ["MG", "RJ"], "a": ["x"]}                # values are sorted: order never causes a mismatch
    assert d["date_range"] == ["2018-07-01", "2018-07-31"] and d["depends"] is True and d["version"] == 4
    assert screen_of(UiState(route="/x", page_id="p"), False)["date_range"] is None


@pytest.mark.skipif(not _db_ok(), reason="database not available")
@pytest.mark.anyio
async def test_a_screen_command_records_the_screen_it_produced():
    s = create_session()
    events = [e async for e in AppPilotAgent(use_llm=False).handle(s.id, "show late deliveries in RJ last month",
                                                                   AgentConfig(open_supporting_view=False))]
    screen = next(e for e in events if e.type == "answer").data["screen"]
    assert screen["depends"] is True and screen["page_id"] == s.state.page_id
    assert any("RJ" in v for v in screen["filters"].values()) and screen["date_range"]
