"""Helpers shared by the routes: errors, JSON conversion, filters, enums, short references."""
from __future__ import annotations

import difflib
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.config import as_of_date
from backend.db import fetch_all
from contracts.dates import resolve


# ------------------------------------------------------------------------------------ errors
class ApiError(Exception):
    """Structured error. The agent reads `code` and `hint`, so keep them specific."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def not_found(entity: str, ref: str) -> ApiError:
    return ApiError(404, f"{entity}_not_found", f"No {entity} matches '{ref}'.")


# ------------------------------------------------------------------------------------ JSON
def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, float):
        return round(v, 6)
    return v


def rows_json(rows: list[dict]) -> list[dict]:
    return [jsonable(r) for r in rows]


def source(tables: list[str], layers: list[str], note: str | None = None) -> dict:
    """Provenance attached to every response, so every number can be cited with its layer."""
    out = {"tables": tables, "data_layers": layers, "as_of_date": as_of_date().isoformat()}
    if note:
        out["note"] = note
    return out


def page(items: list[dict], total: int, limit: int, offset: int, src: dict, **extra) -> dict:
    return {"items": rows_json(items), "total": total, "limit": limit, "offset": offset, "source": src, **extra}


# ------------------------------------------------------------------------------------ params
class Params(BaseModel):
    """Base for query-parameter models: unknown parameters are rejected (catches invented params)."""

    model_config = ConfigDict(extra="forbid")


class Paging(Params):
    limit: int = Field(50, ge=1, le=500, description="Maximum rows to return")
    offset: int = Field(0, ge=0, description="Rows to skip (for paging)")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Where:
    """Accumulates SQL conditions with named parameters."""

    def __init__(self):
        self.parts: list[str] = []
        self.params: dict[str, Any] = {}

    def add(self, cond: str, **params: Any) -> "Where":
        self.parts.append(cond)
        self.params.update(params)
        return self

    def sql(self, keyword: str = "WHERE") -> str:
        return f" {keyword} " + " AND ".join(self.parts) if self.parts else ""


def order_by(sort: str | None, allowed: dict[str, str], default: str) -> str:
    """'-revenue' -> ORDER BY <expr> DESC. Only keys in `allowed` are accepted."""
    key = sort or default
    desc = key.startswith("-")
    name = key.lstrip("-")
    if name not in allowed:
        raise ApiError(422, "invalid_sort", f"Cannot sort by '{name}'.",
                       allowed=sorted(allowed) + [f"-{k}" for k in sorted(allowed)])
    return f" ORDER BY {allowed[name]} {'DESC' if desc else 'ASC'} NULLS LAST"


def date_range(date_from: date | None, date_to: date | None, preset: str | None) -> tuple[date | None, date | None]:
    """Explicit dates win; otherwise a preset (last_quarter, month:2018-03, ...) resolved against as_of."""
    if preset and not (date_from or date_to):
        try:
            f, t = resolve(preset, as_of_date().isoformat())
        except ValueError as e:
            raise ApiError(422, "invalid_preset", str(e),
                           hint="use last_7_days, last_30_days, last_90_days, last_12_months, this_month, "
                                "last_month, this_quarter, last_quarter, this_year, last_year, year_to_date, "
                                "month:YYYY-MM, quarter:YYYYQn, year:YYYY, between:YYYY-MM-DD:YYYY-MM-DD")
        return date.fromisoformat(f), date.fromisoformat(t)
    if date_from and date_to and date_from > date_to:
        raise ApiError(422, "invalid_date_range", f"date_from {date_from} is after date_to {date_to}.")
    return date_from, date_to


# ------------------------------------------------------------------------------------ enums
_ENUM_SQL = {
    "customer_state": "SELECT DISTINCT customer_state AS v FROM raw_customers",
    "customer_region": "SELECT DISTINCT br_region(customer_state) AS v FROM raw_customers",
    "seller_state": "SELECT DISTINCT seller_state AS v FROM raw_sellers",
    "product_category": "SELECT DISTINCT product_category AS v FROM fact_order_items",
    "order_status": "SELECT DISTINCT order_status AS v FROM raw_orders",
    "payment_type": "SELECT DISTINCT payment_type AS v FROM raw_payments",
    "lead_origin": "SELECT DISTINCT origin AS v FROM raw_marketing_qualified_leads WHERE origin IS NOT NULL",
    "business_segment": "SELECT DISTINCT business_segment AS v FROM raw_closed_deals WHERE business_segment IS NOT NULL",
}
# business vocabularies owned by the ops_* tables (mirror the CHECK constraints in sql/20)
STATIC_ENUMS = {
    "ticket_status": ["open", "in_progress", "resolved", "closed"],
    "ticket_category": ["late_delivery", "not_received", "damaged_item", "wrong_item", "product_quality",
                        "refund_request", "other"],
    "ticket_priority": ["low", "medium", "high", "urgent"],
    "restock_status": ["placed", "in_transit", "received", "canceled"],
    "flag_reason": ["high_late_rate", "low_review_score", "high_cancellation", "policy_violation", "other"],
    "flag_severity": ["low", "medium", "high"],
    "flag_status": ["open", "resolved", "dismissed"],
    "promotion_status": ["scheduled", "active", "ended", "canceled"],
    "forecast_scenario": ["baseline", "optimistic", "conservative"],
}


@lru_cache(maxsize=None)
def enum_values(name: str) -> list[str]:
    if name in STATIC_ENUMS:
        return STATIC_ENUMS[name]
    return sorted(str(r["v"]) for r in fetch_all(_ENUM_SQL[name]) if r["v"] is not None)


def check_enum(name: str, value: str | None, param: str | None = None) -> str | None:
    """Reject values outside the closed set, suggesting the closest valid ones."""
    if value is None:
        return None
    allowed = enum_values(name)
    if value in allowed:
        return value
    folded = {a.lower(): a for a in allowed}
    if value.lower() in folded:
        return folded[value.lower()]
    raise ApiError(422, "invalid_value", f"'{value}' is not a valid {param or name}.",
                   param=param or name,
                   did_you_mean=difflib.get_close_matches(value.lower(), list(folded), n=3, cutoff=0.5),
                   allowed=allowed if len(allowed) <= 30 else None)


def check_enum_list(name: str, csv: str | None, param: str) -> list[str] | None:
    """Comma-separated list of enum values ('SP,RJ')."""
    if not csv:
        return None
    return [check_enum(name, v.strip(), param) for v in csv.split(",") if v.strip()]


# ------------------------------------------------------------------------------------ references
def resolve_ref(conn, table: str, id_col: str, ref: str, entity: str, ref_len: int = 8) -> str:
    """Accept a full id or a unique prefix (>= 6 chars, case-insensitive), return the full id.
    Ambiguous prefixes return 409 with the candidates so the caller can ask which one."""
    r = ref.strip().lower()
    if len(r) < 6:
        raise ApiError(422, "ref_too_short", f"'{ref}' is too short; give at least 6 characters of the {entity} id.")
    rows = conn.execute(f"SELECT {id_col} AS id FROM {table} WHERE {id_col} LIKE %(p)s ORDER BY 1 LIMIT 5",
                        {"p": r + "%"}).fetchall()
    if not rows:
        raise not_found(entity, ref)
    if len(rows) > 1 and rows[0]["id"] != r:
        raise ApiError(409, "ambiguous_reference", f"'{ref}' matches more than one {entity}.",
                       candidates=[x["id"] for x in rows])
    return rows[0]["id"]


def resolve_code(conn, table: str, code: str, entity: str, prefix: str) -> dict:
    """Records with human codes (TCK-000123, RST-000001, ...). Accepts 'TCK-123' or '123' too."""
    c = code.strip().upper()
    head, _, digits = c.rpartition("-")
    if not digits.isdigit() or (head and head != prefix):
        raise ApiError(422, "invalid_code", f"'{code}' is not a valid {entity} code.")
    row = conn.execute(f"SELECT * FROM {table} WHERE id = %(i)s", {"i": int(digits)}).fetchone()
    if row is None:
        raise not_found(entity, code)
    return row
