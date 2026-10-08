"""UI state and the backend<->browser protocol (Contract 2).

Design decision: the BACKEND is authoritative for UI state. The agent runs on the
backend; the browser renders. They talk over ONE WebSocket per session:

  backend -> browser : ApplyState(version, nonce, state)
  browser -> backend : RenderAck(version, nonce, per-widget acks)   <- computed from what
                                                                      was actually queried
                                                                      and displayed
  browser -> backend : UserStateChange(state)   (user clicked something themselves)

The verifier compares RenderAck against an expectation computed independently by the
query engine. It does NOT re-read the store the executor wrote (that would be circular).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "0.1.0"


class _Model(BaseModel):
    model_config = {"extra": "forbid"}


class DateRange(_Model):
    from_: str = Field(alias="from")  # ISO date, inclusive
    to: str  # ISO date, inclusive
    preset: str | None = None  # "last_quarter" etc., informational only

    model_config = {"extra": "forbid", "populate_by_name": True}


class Sort(_Model):
    field: str
    dir: Literal["asc", "desc"] = "desc"


class FilterValue(_Model):
    op: Literal["eq", "in", "between", "contains", "gte", "lte"]
    value: list[str]  # always a list; "eq" has one element


class UiState(_Model):
    route: str
    page_id: str
    filters: dict[str, FilterValue] = {}  # keyed by filter_id
    date_range: DateRange | None = None
    sort: Sort | None = None
    selected_widget: str | None = None
    version: int = 0  # incremented by the backend on every ApplyState


# ------------------------------------------------------------- WebSocket messages
class ApplyState(_Model):
    type: Literal["apply_state"] = "apply_state"
    version: int
    nonce: str
    state: UiState
    cause: str = ""  # human-readable reason for the action log


class WidgetAck(_Model):
    widget_id: str
    applied_filters: dict[str, FilterValue]  # taken from the request the widget SENT
    applied_date_range: DateRange | None = None
    applied_sort: Sort | None = None
    row_count: int
    series_hash: str  # sha256 of the canonical serialization of the displayed series
    render_ms: int = 0
    error: str | None = None


class RenderAck(_Model):
    type: Literal["render_ack"] = "render_ack"
    version: int
    nonce: str
    route: str  # route actually shown
    widgets: list[WidgetAck]


class UserStateChange(_Model):
    type: Literal["user_state_change"] = "user_state_change"
    state: UiState


# Canonical series hash: Python (backend) and JavaScript/TypeScript (browser) MUST produce identical output.
# Language-neutral serialization (no JSON; no toFixed/format() because tie-rounding differs by language):
#   number: x = float(v); n = floor(abs(x) * 1e6 + 0.5)   (identical IEEE-754 operations in both languages)
#           text = ("-" if x < 0 and n != 0 else "") + decimal digits of n      e.g. 1.5 -> "1500000"
#           non-finite -> "nan" | "inf" | "-inf"
#   None -> "null"; bool -> "true"/"false"; date/datetime -> .isoformat(); Decimal -> treated as float;
#   everything else -> str(v)
#   row:    cells joined with "\x1f" in the order of `columns`
#   rows:   sorted lexicographically (plain code-unit comparison), joined with "\x1e"
#   text:   "\x1f".join(columns) + "\x1d" + rows
#   hash:   sha256(text.encode("utf-8")).hexdigest()
# `columns` = widget dimensions followed by widget metrics, in the order listed in the metadata.
# The query engine must convert Postgres NUMERIC (Decimal) to float before returning rows.
# Shared test vectors: contracts/hash_vectors.json. The JS port is contracts/ts/hash.mjs and must pass all of them.
def _cell(v) -> str:
    import math
    from decimal import Decimal

    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, (int, float)):
        x = float(v)
        if x != x:
            return "nan"
        if math.isinf(x):
            return "inf" if x > 0 else "-inf"
        n = int(math.floor(abs(x) * 1e6 + 0.5))
        return ("-" if x < 0 and n != 0 else "") + str(n)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def canonical_series_hash(rows: list[dict], columns: list[str]) -> str:
    import hashlib

    lines = sorted("\x1f".join(_cell(r.get(c)) for c in columns) for r in rows)
    text = "\x1f".join(columns) + "\x1d" + "\x1e".join(lines)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
