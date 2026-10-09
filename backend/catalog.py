"""API catalogue: the metadata the agent reads to know what the application can do.

Every route is declared with `@endpoint(...)`, which registers the FastAPI route AND its catalogue
entry in one place, so the catalogue can never drift from the real API. Parameter and body schemas
are taken from FastAPI's own OpenAPI output; closed vocabularies (states, categories, statuses) are
filled from the database. The agent retrieves over `GET /api/catalog` (like the mentor demo's
"semantic search over API catalogue"), and builds write forms from the body schemas.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Literal

from fastapi import APIRouter, FastAPI

from backend.common import enum_values


@dataclass
class ApiSpec:
    api_id: str            # stable id, e.g. "tickets.create"
    method: str
    path: str
    title: str
    description: str       # written for retrieval: what question/task this endpoint serves
    entity: str            # business entity it reads or changes
    kind: Literal["read", "write"]
    data_layers: list[str]  # original | derived | synthetic | system
    returns: str
    requires_confirmation: bool = False
    supports_dry_run: bool = False
    undoable: bool = False
    examples: list[str] = field(default_factory=list)  # user requests this endpoint serves


REGISTRY: dict[str, ApiSpec] = {}


def endpoint(router: APIRouter, method: str, path: str, *, api_id: str, title: str, description: str,
             entity: str, data_layers: list[str], returns: str, examples: list[str] | None = None,
             kind: Literal["read", "write"] | None = None, undoable: bool | None = None) -> Callable:
    """GET routes are reads. Other methods are writes (confirmation + dry_run + undo) unless
    kind="read" (e.g. POST /api/metrics/query only reads). Undo itself is a write that is not undoable."""
    kind = kind or ("read" if method == "GET" else "write")
    if api_id in REGISTRY:
        raise ValueError(f"duplicate api_id {api_id}")
    write = kind == "write"
    undo = write if undoable is None else undoable

    def deco(fn: Callable) -> Callable:
        REGISTRY[api_id] = ApiSpec(
            api_id=api_id, method=method, path=path, title=title, description=description, entity=entity,
            kind=kind, data_layers=data_layers, returns=returns, requires_confirmation=write,
            supports_dry_run=undo, undoable=undo, examples=examples or [])
        router.add_api_route(path, fn, methods=[method], operation_id=api_id, summary=title,
                             description=description, tags=[entity])
        return fn

    return deco


# Business entities: what each one is, how it is identified, where it lives.
ENTITIES = {
    "order": {"description": "A customer purchase on the marketplace (real Olist data).",
              "id": "order_id (32 hex chars); short ref = first 8 chars, upper-case (e.g. E481F51C)",
              "tables": ["raw_orders", "core_order_summary"], "data_layer": "original"},
    "product": {"description": "A product sold on the marketplace; listed products also have price, cost and stock.",
                "id": "product_id (32 hex chars); short ref = first 8 chars",
                "tables": ["raw_products", "ops_product_listings"], "data_layer": "original + synthetic"},
    "inventory": {"description": "Stock levels, reorder points and month-end snapshots (simulated).",
                  "id": "product_id", "tables": ["ops_product_listings", "syn_inventory_snapshots"],
                  "data_layer": "synthetic"},
    "restock_order": {"description": "A purchase order to replenish a product's stock.",
                      "id": "code RST-000123", "tables": ["ops_restock_orders"], "data_layer": "synthetic"},
    "seller": {"description": "A merchant selling on the marketplace (real Olist data) and their performance.",
               "id": "seller_id (32 hex chars); short ref = first 8 chars",
               "tables": ["raw_sellers", "fact_order_items", "ops_seller_flags"], "data_layer": "original + synthetic"},
    "seller_flag": {"description": "An operations flag on a seller for poor performance.",
                    "id": "code FLG-00012", "tables": ["ops_seller_flags"], "data_layer": "synthetic"},
    "review": {"description": "A customer review of an order (real Olist data) and the seller's reply.",
               "id": "review_id (+ order_id when a review_id repeats)",
               "tables": ["raw_reviews", "ops_review_replies"], "data_layer": "original + synthetic"},
    "ticket": {"description": "A customer support ticket about an order.",
               "id": "code TCK-000123", "tables": ["ops_support_tickets"], "data_layer": "synthetic"},
    "promotion": {"description": "A discount campaign for a category and/or state.",
                  "id": "code PRM-0012", "tables": ["ops_promotions"], "data_layer": "synthetic"},
    "target": {"description": "Monthly revenue and order targets per customer state (simulated plan).",
               "id": "(target_month, customer_state)", "tables": ["syn_monthly_sales_targets"],
               "data_layer": "synthetic"},
    "forecast": {"description": "Model-generated monthly revenue forecasts per state and scenario.",
                 "id": "(forecast_month, customer_state, scenario)", "tables": ["syn_monthly_sales_forecasts"],
                 "data_layer": "synthetic"},
    "sla": {"description": "Delivery service-level rules per destination state and SLA performance.",
            "id": "sla_rule_id", "tables": ["syn_delivery_sla_rules", "core_order_summary"],
            "data_layer": "synthetic + original"},
    "lead": {"description": "Seller acquisition funnel: marketing-qualified leads and closed deals (real Olist data).",
             "id": "mql_id", "tables": ["raw_marketing_qualified_leads", "raw_closed_deals"],
             "data_layer": "original"},
    "metric": {"description": "Governed business metrics (revenue, orders, late rate, ...) by any dimension.",
               "id": "metric_id", "tables": ["fact_*"], "data_layer": "derived"},
    "action": {"description": "The audit log of every change made through the API, with undo.",
               "id": "code ACT-000123", "tables": ["ops_action_log"], "data_layer": "system"},
    "metadata": {"description": "Application metadata: pages, widgets, API catalogue, data dictionary.",
                 "id": "-", "tables": [], "data_layer": "system"},
}

# parameter / body-field names whose values come from a closed vocabulary
PARAM_ENUMS = {
    "customer_state": "customer_state", "customer_states": "customer_state", "customer_region": "customer_region",
    "seller_state": "seller_state", "product_category": "product_category", "category": "product_category",
    "status": None, "order_status": "order_status", "payment_type": "payment_type",
    "origin": "lead_origin", "business_segment": "business_segment",
}
# per-entity meaning of generic names such as "status" / "category"
ENTITY_PARAM_ENUMS = {
    ("ticket", "status"): "ticket_status", ("ticket", "category"): "ticket_category",
    ("ticket", "priority"): "ticket_priority", ("restock_order", "status"): "restock_status",
    ("seller_flag", "status"): "flag_status", ("seller", "reason"): "flag_reason",
    ("seller", "severity"): "flag_severity", ("seller_flag", "severity"): "flag_severity",
    ("promotion", "status"): "promotion_status", ("forecast", "scenario"): "forecast_scenario",
    ("order", "status"): "order_status",
}


def _deref(schema, components: dict):
    if isinstance(schema, dict):
        if "$ref" in schema:
            return _deref(components[schema["$ref"].split("/")[-1]], components)
        return {k: _deref(v, components) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_deref(x, components) for x in schema]
    return schema


def _enum_for(entity: str, name: str) -> list[str] | None:
    key = ENTITY_PARAM_ENUMS.get((entity, name)) or PARAM_ENUMS.get(name)
    if not key:
        return None
    try:
        return enum_values(key)
    except Exception:  # noqa: BLE001  (e.g. funnel not loaded)
        return None


def build_catalog(app: FastAPI) -> list[dict]:
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    ops = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            ops[op.get("operationId")] = op
    out = []
    for api_id, s in REGISTRY.items():
        op = ops.get(api_id, {})
        params = []
        for p in op.get("parameters", []):
            schema = _deref(p.get("schema", {}), components)
            item = {"name": p["name"], "in": p["in"], "required": p.get("required", False),
                    "type": schema.get("type") or [x.get("type") for x in schema.get("anyOf", []) if x.get("type") != "null"],
                    "description": p.get("description") or schema.get("description", "")}
            if "default" in schema:
                item["default"] = schema["default"]
            for k in ("enum", "minimum", "maximum"):
                if k in schema:
                    item[k] = schema[k]
            if "enum" not in item and (vals := _enum_for(s.entity, p["name"])):
                item["enum"] = vals
            params.append(item)
        body = None
        rb = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if rb:
            body = _deref(rb, components)
            for name, prop in body.get("properties", {}).items():
                if "enum" not in prop and (vals := _enum_for(s.entity, name)):
                    prop["enum"] = vals
        entry = asdict(s)
        entry.update({"parameters": params, "body_schema": body,
                      "search_text": " ".join([s.title, s.description, s.entity, *s.examples,
                                               *[p["name"] for p in params],
                                               *((body or {}).get("properties", {}).keys())])})
        out.append(entry)
    return out
