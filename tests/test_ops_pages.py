"""The operations-page metadata must agree with the live API catalogue (skipped without the database).

For every API-bound widget and action: the API exists, every parameter / body field it names exists,
the columns it shows are really in the API response, and filter vocabularies match the API's.
"""
import json
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
from fastapi.testclient import TestClient  # noqa: E402

from contracts.metadata import Application  # noqa: E402

APP_FILE = Path(__file__).resolve().parent.parent / "data" / "olist" / "application.json"
URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")


@pytest.fixture(scope="module")
def app_meta():
    if not APP_FILE.exists():
        pytest.skip("run: python -m generator.build_application")
    return Application.model_validate(json.loads(APP_FILE.read_text()))


@pytest.fixture(scope="module")
def api():
    try:
        c = psycopg.connect(URL, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    if not c.execute("SELECT to_regclass('ops_action_log')").fetchone()[0]:
        pytest.skip("synthetic tables not generated")
    c.close()
    from backend.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def catalog(api):
    return {i["api_id"]: i for i in api.get("/api/catalog", params={"limit": 500}).json()["items"]}


def api_widgets(app_meta):
    return [w for w in app_meta.widgets if w.source is not None]


def test_ops_section_exists_and_is_first(app_meta):
    assert app_meta.schema_version == "0.2.0"
    ops = [p for p in app_meta.pages if p.kind == "operations"]
    assert len(ops) >= 15 and app_meta.pages[0].kind == "operations"
    assert [m.section for m in app_meta.modules[:7]] == ["operations"] * 7
    for p in ops:
        assert p.page_code and p.agent_context and p.layout, p.page_id
    # every page and widget has a short code
    assert all(p.page_code for p in app_meta.pages) and all(w.widget_code for w in app_meta.widgets)


def test_widget_sources_match_catalogue_parameters(app_meta, catalog):
    for w in api_widgets(app_meta):
        spec = catalog[w.source.api_id]
        assert spec["kind"] == "read" and spec["method"] == "GET", w.widget_id
        names = {p["name"] for p in spec["parameters"]}
        used = set(w.source.params) | set(w.source.filter_params.values()) | set((w.source.date_params or {}).values())
        if w.source.sort_param and "sort" in w.supports:
            used.add(w.source.sort_param)
        assert used <= names, (w.widget_id, sorted(used - names))


def test_filter_vocabularies_match_api_enums(app_meta, catalog):
    for p in app_meta.pages:
        flt = {f.filter_id: f for f in p.filters}
        for wid in p.widgets:
            src = app_meta.widget(wid).source
            if not src:
                continue
            params = {x["name"]: x for x in catalog[src.api_id]["parameters"]}
            for fid, param in src.filter_params.items():
                f, spec = flt[fid], params[param]
                if f.allowed_values and "enum" in spec:
                    assert set(f.allowed_values) <= set(spec["enum"]), (p.page_id, fid, param)
                if f.allowed_values == ["true", "false"]:
                    t = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
                    assert "boolean" in t, (p.page_id, fid, spec["type"])


def test_actions_match_catalogue_writes(app_meta, catalog):
    pages = {p.page_id: p for p in app_meta.pages}
    for p in pages.values():
        acts = list(p.actions) + [a for wid in p.widgets if app_meta.widget(wid).source
                                  for a in app_meta.widget(wid).source.row_actions]
        for a in acts:
            spec = catalog[a.api_id]
            assert spec["kind"] == "write" and spec["requires_confirmation"], (p.page_id, a.action_id)
            path_params = {x["name"] for x in spec["parameters"] if x["in"] == "path"}
            body = (spec["body_schema"] or {}).get("properties", {})
            known = path_params | set(body)
            given = set(a.params_from_row) | set(a.fixed)
            assert given <= known, (p.page_id, a.action_id, sorted(given - known))
            required = path_params | set((spec["body_schema"] or {}).get("required", []))
            missing = required - given
            if missing:
                assert a.form, f"{p.page_id}/{a.action_id}: needs a form for {sorted(missing)}"
            if a.form:
                assert body or missing, (p.page_id, a.action_id)
            for field, value in a.fixed.items():
                if field in body and "enum" in body[field]:
                    assert value in body[field]["enum"], (p.page_id, a.action_id, field)
            if a.api_id == "tickets.create":
                assert a.fixed.get("category", "late_delivery") in body["category"]["enum"]


def test_every_declared_column_is_returned_by_the_api(app_meta, api, catalog):
    checked = 0
    for w in api_widgets(app_meta):
        spec = catalog[w.source.api_id]
        params = {k: v for k, v in w.source.params.items()}
        # path-less GETs only (widgets never bind to a detail endpoint)
        assert "{" not in spec["path"], w.widget_id
        r = api.get(spec["path"], params={k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items()})
        assert r.status_code == 200, (w.widget_id, r.text[:300])
        rows = r.json()[w.source.items_path]
        assert rows, f"{w.widget_id}: API returned no rows with its default parameters"
        missing = [c.name for c in w.source.columns if c.name not in rows[0]]
        assert not missing, (w.widget_id, missing)
        checked += 1
    assert checked >= 20


def test_ops_vocabularies_match_backend(app_meta):
    from backend.common import STATIC_ENUMS
    from generator import ops_pages as o

    assert o.TICKET_STATUS == STATIC_ENUMS["ticket_status"] and o.TICKET_CATEGORY == STATIC_ENUMS["ticket_category"]
    assert o.TICKET_PRIORITY == STATIC_ENUMS["ticket_priority"] and o.RESTOCK_STATUS == STATIC_ENUMS["restock_status"]
    assert o.FLAG_STATUS == STATIC_ENUMS["flag_status"] and o.FLAG_SEVERITY == STATIC_ENUMS["flag_severity"]
    assert o.PROMO_STATUS == STATIC_ENUMS["promotion_status"] and o.SCENARIOS == STATIC_ENUMS["forecast_scenario"]


def test_metric_widgets_still_valid_and_old_pages_untouched(app_meta):
    reports = [p for p in app_meta.pages if p.kind == "report"]
    assert len(reports) == 100
    assert all(app_meta.widget(w).source is None for p in reports for w in p.widgets)
