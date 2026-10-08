"""Checks on the generated application metadata (card T6)."""
import json
import os
from pathlib import Path

import pytest

from contracts.metadata import Application

APP_FILE = Path(__file__).resolve().parent.parent / "data" / "olist" / "application.json"


@pytest.fixture(scope="module")
def app():
    if not APP_FILE.exists():
        pytest.skip("run: python -m generator.build_application")
    return Application.model_validate(json.loads(APP_FILE.read_text()))


def test_scale_and_uniqueness(app):
    assert len(app.pages) >= 100
    for attr in ("page_id", "route", "description"):
        vals = [getattr(p, attr) for p in app.pages]
        assert len(vals) == len(set(vals)), attr
    assert app.as_of_date == "2018-08-31"


def test_every_page_has_date_range_and_default_state(app):
    for p in app.pages:
        assert p.filters and p.filters[0].filter_id == "date_range", p.page_id
        assert p.default_state.get("date_range", {}).get("preset") == "last_12_months"


def test_page_filters_apply_to_at_least_one_widget(app):
    # shared semantics S5: a filter applies to a widget iff its field is in the widget's dataset
    for p in app.pages:
        ds_fields = [{f.name for f in app.dataset(app.widget(w).dataset_id).fields} for w in p.widgets]
        for f in p.filters:
            assert any(f.field in fields for fields in ds_fields), (p.page_id, f.filter_id)


def test_routes_are_slugs(app):
    for p in app.pages:
        assert p.route.startswith(f"/{p.directory.split('/')[0]}/") and "_" not in p.route, p.route


def test_filter_values_exist_in_database(app):
    psycopg = pytest.importorskip("psycopg")
    url = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
    try:
        conn = psycopg.connect(url, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    with conn:
        cache = {}
        for p in app.pages:
            table = app.dataset(app.widget(p.widgets[0]).dataset_id).table
            for f in p.filters:
                if not f.allowed_values:
                    continue
                key = (table, f.field)
                if key not in cache:
                    cache[key] = {str(r[0]) for r in conn.execute(f"SELECT DISTINCT {f.field} FROM {table}")}
                assert set(f.allowed_values) <= cache[key], (p.page_id, f.filter_id)


def test_synonyms_point_to_allowed_values(app):
    for p in app.pages:
        for f in p.filters:
            for canonical in f.synonyms:
                assert canonical in (f.allowed_values or []), (p.page_id, f.filter_id, canonical)
