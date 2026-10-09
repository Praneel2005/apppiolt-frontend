"""The mock backend must behave like the real API contract (frontend pair builds against it)."""
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

if not (Path(__file__).resolve().parent.parent / "data" / "olist" / "application.json").exists():
    pytest.skip("application.json missing", allow_module_level=True)

from contracts.ui_state import canonical_series_hash  # noqa: E402
from mock.server import app  # noqa: E402

client = TestClient(app)


def test_application_and_presets():
    a = client.get("/api/application").json()
    assert len(a["pages"]) >= 40
    p = client.get("/api/date-presets").json()
    assert p["last_quarter"] == {"from": "2018-04-01", "to": "2018-06-30"}


def test_widget_data_matches_known_value():
    r = client.post("/api/widget-data", json={"widget_id": "sales.overview.kpi_revenue",
                                              "date_range": {"from": "2018-04-01", "to": "2018-06-30"}}).json()
    assert r["row_count"] == 1 and round(r["rows"][0]["revenue"], 2) == 2849730.26


# The WebSocket render_ack round trip is not tested here: FastAPI's in-process TestClient deadlocks
# when a websocket and a concurrent request share it. It was verified against a running server
# (uvicorn) on 2026-10-08: correct ack -> verify.ok true; drop_filter fault -> row_count/series_hash mismatch.
