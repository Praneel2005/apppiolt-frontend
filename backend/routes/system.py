"""Metadata and audit endpoints: API catalogue, entities, data dictionary, action log, undo."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from backend.catalog import ENTITIES, REGISTRY, endpoint
from backend.common import Paging, Where, enum_values, jsonable, page, resolve_code, rows_json, source, STATIC_ENUMS
from backend.config import application, as_of_date
from backend.db import fetch_all, ro
from backend.writes import WriteOpts, undo_action

router = APIRouter()
_CATALOG: dict = {}


def catalog_entries(app) -> list[dict]:
    from backend.catalog import build_catalog
    if "v" not in _CATALOG:
        _CATALOG["v"] = build_catalog(app)
    return _CATALOG["v"]


class CatalogQuery(Paging):
    kind: Literal["read", "write"] | None = Field(None, description="Only reads or only writes")
    entity: str | None = Field(None, description="Only this entity (order, ticket, product, ...)")


@endpoint(router, "GET", "/api/catalog", api_id="metadata.catalog", entity="metadata",
          title="API catalogue",
          description="Every API the application offers, with description, parameters (and allowed values), "
                      "body schema, read/write, data layer and example requests. The agent retrieves over this.",
          data_layers=["system"], returns="items[] of API entries",
          examples=["what can this application do", "which APIs change data"])
def get_catalog(q: Annotated[CatalogQuery, Query()]):
    from backend.main import app
    items = catalog_entries(app)
    if q.kind:
        items = [i for i in items if i["kind"] == q.kind]
    if q.entity:
        items = [i for i in items if i["entity"] == q.entity]
    return {"items": items[q.offset:q.offset + q.limit], "total": len(items), "limit": q.limit, "offset": q.offset}


@endpoint(router, "GET", "/api/entities", api_id="metadata.entities", entity="metadata",
          title="Business entities",
          description="The business objects (order, product, ticket, ...), how each is identified and which data "
                      "layer it lives in.", data_layers=["system"], returns="entities{}")
def get_entities():
    return {"entities": ENTITIES, "enums": {k: v for k, v in STATIC_ENUMS.items()}}


@endpoint(router, "GET", "/api/data-dictionary", api_id="metadata.data_dictionary", entity="metadata",
          title="Data dictionary",
          description="Every table with its data layer (original, derived, synthetic, system), description and "
                      "columns. Use it to say whether a number is real Olist data or simulated.",
          data_layers=["system"], returns="tables[] with layer, description, columns")
def data_dictionary():
    tabs = fetch_all("""
        SELECT c.relname AS table_name, obj_description(c.oid) AS comment
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1""")
    cols = fetch_all("SELECT table_name, column_name, data_type FROM information_schema.columns "
                     "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position")
    by: dict[str, list] = {}
    for c in cols:
        by.setdefault(c["table_name"], []).append({"name": c["column_name"], "type": c["data_type"]})
    out = []
    for t in tabs:
        layer, _, desc = (t["comment"] or "undocumented: ").partition(": ")
        out.append({"table": t["table_name"], "layer": layer, "description": desc, "columns": by.get(t["table_name"], [])})
    return {"tables": out, "as_of_date": as_of_date().isoformat()}


class ActionsQuery(Paging):
    api_id: str | None = Field(None, description="Only actions made through this API")
    actor: Literal["user", "agent"] | None = Field(None, description="Who made the change")
    undone: bool | None = Field(None, description="true = only undone actions")


@endpoint(router, "GET", "/api/actions", api_id="actions.search", entity="action",
          title="Action log",
          description="Audit log of every change made through the API: who, which API, what changed (before/after), "
                      "and whether it was undone.", data_layers=["system"], returns="items[] of actions; total",
          examples=["what changes did the agent make", "show my last 5 actions"])
def actions_search(q: Annotated[ActionsQuery, Query()]):
    w = Where()
    if q.api_id:
        w.add("api_id = %(a)s", a=q.api_id)
    if q.actor:
        w.add("actor = %(u)s", u=q.actor)
    if q.undone is not None:
        w.add(("undone_at IS NOT NULL" if q.undone else "undone_at IS NULL"))
    with ro() as conn:
        total = conn.execute(f"SELECT count(*) AS n FROM ops_action_log{w.sql()}", w.params).fetchone()["n"]
        rows = conn.execute(f"SELECT code, api_id, actor, params, changes, result, sim_time, undone_at, undone_by "
                            f"FROM ops_action_log{w.sql()} ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s",
                            {**w.params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset, source(["ops_action_log"], ["system"]))


@endpoint(router, "POST", "/api/actions/{code}/undo", api_id="actions.undo", entity="action",
          title="Undo an action",
          description="Reverse a change made through the API (restores the exact before-image). Refused if the "
                      "affected rows were changed afterwards.", data_layers=["system"],
          returns="the reverted rows", examples=["undo that", "undo ACT-000012"], undoable=False)
def actions_undo(code: str, opts: WriteOpts = Depends()):
    return undo_action(code, opts.actor)


@endpoint(router, "GET", "/api/application", api_id="metadata.application", entity="metadata",
          title="Application metadata",
          description="Pages, widgets, datasets and metrics of the analytics application (pages and report library).",
          data_layers=["system"], returns="the Application metadata document")
def get_application():
    return application()
