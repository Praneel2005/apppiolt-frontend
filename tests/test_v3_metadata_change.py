"""The no-code-platform claim: change the application's METADATA, not the code, and the agent follows.

A new page with a new filter is added to a copy of application.json (nothing in agent/, backend/ or web/ changes). The test points the
backend at that copy, clears every metadata cache, and checks that the agent finds the page from its title and keywords, opens it,
verifies the screen, and applies the new filter. Afterwards the original metadata is restored.
"""
import copy
import importlib
import json

import pytest

from agent.agent import AppPilotAgent
from backend import config
from backend.sessions import create_session
from contracts.actions import AgentConfig
from contracts.metadata import Application

NEW_PAGE = "sales.cancellation_exposure"
CACHES = [("backend.config", "application"), ("backend.config", "app_model"), ("backend.routes.metrics", "_index"),
          ("backend.retrieval", "_index"), ("agent.lexicon", "get_lexicon"), ("agent.capabilities", "build_capability_model"),
          ("agent.retrieval", "build_metadata_graph"), ("agent.retrieval", "_get_vector_index")]


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


def clear_caches() -> None:
    for mod, name in CACHES:
        fn = getattr(importlib.import_module(mod), name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()


def with_new_page(app: dict) -> dict:
    """The same application plus one new report page and one new filter on it, built only from existing datasets and widgets."""
    app = copy.deepcopy(app)
    src = next(p for p in app["pages"] if p["page_id"] == "sales.revenue")
    widgets = {w["widget_id"]: w for w in app["widgets"]}
    ds = next(d for d in app["datasets"] if d["dataset_id"] == widgets["sales.revenue.kpi"]["dataset_id"])
    status_values = next(f for f in ds["fields"] if f["name"] == "order_status")["values"]
    new_widgets = []
    for old in ("sales.revenue.kpi", "sales.revenue.trend"):
        w = copy.deepcopy(widgets[old])
        w["widget_id"] = old.replace("sales.revenue", "sales.cancellation_exposure")
        w["widget_code"] = None
        w["title"] = w["title"] + " (cancellation exposure)"
        new_widgets.append(w)
    app["widgets"] += new_widgets
    app["pages"].append({
        "page_id": NEW_PAGE, "page_code": "P-9901", "kind": "report", "title": "Cancellation exposure",
        "description": "How much revenue is tied up in canceled or unavailable orders, with the order status as a filter.",
        "agent_context": "Report page: revenue by order status, used to see the cost of cancellations.",
        "directory": src["directory"], "route": "/sales/cancellation-exposure",
        "keywords": ["cancellation exposure", "canceled revenue", "lost sales", "unavailable orders"],
        "widgets": [w["widget_id"] for w in new_widgets],
        "filters": [next(f for f in src["filters"] if f["type"] == "date_range"),
                    {"filter_id": "order_status", "field": "order_status", "type": "multiselect", "title": "Order status",
                     "allowed_ops": ["in"], "allowed_values": list(status_values), "synonyms": {}}],
        "default_state": {}, "actions": []})
    return app


@pytest.fixture
def swap_metadata(tmp_path, monkeypatch):
    """swap(app_dict): make the backend and the agent use this metadata; the original is restored afterwards."""
    def swap(app: dict) -> None:
        Application.model_validate(app)                        # the changed metadata is itself valid
        path = tmp_path / "application.json"
        path.write_text(json.dumps(app), encoding="utf-8")
        monkeypatch.setattr(config, "APP_FILE", path)
        clear_caches()
    yield swap
    monkeypatch.undo()
    clear_caches()


async def ask(text: str):
    agent = AppPilotAgent(use_llm=False)
    s = create_session()
    events = [e async for e in agent.handle(s.id, text, AgentConfig(open_supporting_view=False))]
    return s, events


@pytest.mark.skipif(not _db_ok(), reason="database not available")
@pytest.mark.anyio
async def test_a_page_and_a_filter_added_to_the_metadata_are_found_opened_and_used(swap_metadata):
    original = json.loads(config.APP_FILE.read_text(encoding="utf-8"))
    utterance = "open the cancellation exposure page"

    s, _ = await ask(utterance)                                              # before: the page does not exist
    assert s.state.page_id != NEW_PAGE
    assert all(p["page_id"] != NEW_PAGE for p in original["pages"])

    swap_metadata(with_new_page(original))                                   # after: only the metadata changed
    s, events = await ask(utterance)
    assert s.state.page_id == NEW_PAGE and s.state.route == "/sales/cancellation-exposure"
    assert s.last_verify is not None and s.last_verify.ok                    # the screen was verified, not assumed
    answer = next(e for e in events if e.type == "answer")
    assert answer.data["status"] in ("VERIFIED", "PARTIAL")

    s, _ = await ask("show cancellation exposure for canceled orders")       # the new filter is understood and applied
    assert s.state.page_id == NEW_PAGE
    assert "order_status" in s.state.filters and "canceled" in list(s.state.filters["order_status"].value)

    from agent.lexicon import get_lexicon
    assert "cancellation" in get_lexicon().page_vocab                        # the new page's words entered the vocabulary


@pytest.mark.skipif(not _db_ok(), reason="database not available")
@pytest.mark.anyio
async def test_the_original_metadata_is_back_after_the_swap(swap_metadata):
    original = json.loads(config.APP_FILE.read_text(encoding="utf-8"))
    swap_metadata(with_new_page(original))
    assert any(p.page_id == NEW_PAGE for p in config.app_model().pages)
    # (the fixture restores the original afterwards; the next test would fail if it did not)


@pytest.mark.skipif(not _db_ok(), reason="database not available")
@pytest.mark.anyio
async def test_no_trace_of_the_new_page_remains_in_a_later_test():
    assert all(p.page_id != NEW_PAGE for p in config.app_model().pages)
    s, _ = await ask("open the cancellation exposure page")
    assert s.state.page_id != NEW_PAGE
