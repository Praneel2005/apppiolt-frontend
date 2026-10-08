"""Query, validation and verification result types shared by backend and agent (Contract 4)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .actions import Plan
from .ui_state import DateRange, FilterValue, Sort


class _Model(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}


class QuerySpec(_Model):
    """Structured query over the semantic layer. The LLM never writes raw SQL for this path."""

    dataset_id: str
    metrics: list[str]  # metric ids
    dimensions: list[str] = []  # field names
    filters: dict[str, FilterValue] = {}  # keyed by field name
    date_range: DateRange | None = None
    order_by: Sort | None = None
    limit: int = 1000


class QueryResult(_Model):
    columns: list[str]  # dimensions then metrics, same order as the hash contract
    rows: list[dict]
    sql: str
    row_count: int
    series_hash: str


class ValidationIssue(_Model):
    step_index: int
    code: Literal["invalid_reference", "invalid_value", "invalid_op", "invalid_date", "disallowed_tool", "unsafe"]
    message: str
    suggestion: str | None = None  # e.g. nearest allowed value; goes back to the planner


class ValidationResult(_Model):
    ok: bool
    normalized_plan: Plan | None = None  # synonyms resolved, presets turned into dates
    issues: list[ValidationIssue] = []


class VerifyResult(_Model):
    ok: bool
    mismatches: list[dict] = []  # {"widget_id", "field": "applied_filters|row_count|series_hash|route", "expected", "actual"}
