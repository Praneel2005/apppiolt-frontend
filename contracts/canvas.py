"""Generated canvas (Contract 8): what the agent shows in the main area when no existing page fits.

Three kinds, all produced by the backend from evidence or from the API catalogue (the LLM only chooses
the kind, the title and which evidence/API to use):
  chart  - bar or line chart over rows that came from an API or a metric query (cited by evidence_ids)
  table  - sortable table of such rows
  form   - an input form generated from a write API's body schema; submitting it calls that API
           (dry_run preview first, then the user's confirmation, then the real call, then undo available)
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .metadata import ChartSpec, Column


class _Model(BaseModel):
    model_config = {"extra": "forbid"}


class FormSpec(_Model):
    api_id: str
    method: str
    path: str  # may contain {path_params}
    title: str
    path_params: dict[str, Any] = {}  # already known path parameter values
    body_schema: dict = {}  # JSON Schema of the request body, enums filled from the database
    defaults: dict[str, Any] = {}  # prefilled values (path or body fields)
    submit_label: str = "Preview"
    requires_confirmation: bool = True
    # live forms (managed by the agent, filled from chat, editable in the browser)
    live: bool = False
    missing: list[str] = []            # required fields still empty
    errors: dict[str, str] = {}        # field -> why the value was refused
    sources: dict[str, str] = {}       # field -> user | agent | default
    # the website's own form this fills: the page that hosts the action and the action's label
    page_id: str = ""
    action_label: str = ""
    locked: dict[str, Any] = {}        # values the action fixes (not editable)


class CanvasSpec(_Model):
    canvas_id: str
    kind: Literal["chart", "table", "form"]
    title: str
    subtitle: str = ""
    chart_type: Literal["bar_chart", "line_chart"] | None = None
    chart: ChartSpec | None = None
    columns: list[Column] = []
    rows: list[dict] = []
    form: FormSpec | None = None
    evidence_ids: list[str] = []  # every number on a chart/table maps to evidence
    data_layers: list[str] = []  # original / derived / synthetic, shown as a badge
    note: str = ""
