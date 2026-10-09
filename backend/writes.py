"""Every write goes through run_write(): one transaction, before/after images, audit log, undo.

    dry_run=true  -> the write runs inside a transaction that is rolled back; the response shows
                     exactly what WOULD change (the agent's confirmation card shows this diff).
    dry_run=false -> committed, logged in ops_action_log as ACT-000123, undoable via
                     POST /api/actions/{code}/undo (refused if the rows changed since).
Only tables listed in OPS_TABLES can be touched, and the app_rw role can only write ops_* anyway.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

from fastapi import Header, Query
from psycopg import sql
from psycopg.types.json import Jsonb

from backend.common import ApiError, jsonable
from backend.config import sim_now
from backend.db import rw

OPS_TABLES = {
    "ops_product_listings": "product_id", "ops_restock_orders": "id", "ops_support_tickets": "id",
    "ops_review_replies": "id", "ops_promotions": "id", "ops_price_changes": "id", "ops_seller_flags": "id",
}


class WriteOpts:
    """Common options of every write endpoint (FastAPI dependency)."""

    def __init__(
        self,
        dry_run: bool = Query(False, description="Preview only: run the change, return the diff, save nothing"),
        x_actor: Literal["user", "agent"] = Header("user", description="Who is making the change"),
    ):
        self.dry_run, self.actor = dry_run, x_actor


class Changes:
    """Records each row change with its before/after image."""

    def __init__(self, conn):
        self.conn, self.items = conn, []

    def _pk(self, table: str) -> str:
        if table not in OPS_TABLES:
            raise ApiError(403, "table_not_writable", f"{table} cannot be changed through the API.")
        return OPS_TABLES[table]

    def insert(self, table: str, values: dict[str, Any]) -> dict:
        pk = self._pk(table)
        cols = list(values)
        q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder(c) for c in cols))
        row = self.conn.execute(q, values).fetchone()
        self.items.append({"table": table, "pk_col": pk, "pk": jsonable(row[pk]), "op": "insert",
                           "before": None, "after": jsonable(row)})
        return row

    def update(self, table: str, pk_val: Any, values: dict[str, Any]) -> dict:
        pk = self._pk(table)
        before = self.conn.execute(
            sql.SQL("SELECT * FROM {} WHERE {} = %(pk)s FOR UPDATE").format(sql.Identifier(table), sql.Identifier(pk)),
            {"pk": pk_val}).fetchone()
        if before is None:
            raise ApiError(404, "row_not_found", f"{table} row {pk_val} not found.")
        sets = sql.SQL(", ").join(sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder(c)) for c in values)
        after = self.conn.execute(
            sql.SQL("UPDATE {} SET {} WHERE {} = %(__pk)s RETURNING *").format(sql.Identifier(table), sets,
                                                                              sql.Identifier(pk)),
            {**values, "__pk": pk_val}).fetchone()
        self.items.append({"table": table, "pk_col": pk, "pk": jsonable(pk_val), "op": "update",
                           "before": jsonable(before), "after": jsonable(after),
                           "changed": sorted(c for c in values if jsonable(before[c]) != jsonable(after[c]))})
        return after


class _Preview(Exception):
    def __init__(self, result, changes):
        self.result, self.changes = result, changes


def run_write(api_id: str, opts: WriteOpts, params: dict, fn: Callable[[Any, Changes], dict]) -> dict:
    """Run fn(conn, changes) in one transaction. fn validates, writes via `changes`, returns a result."""
    with rw() as conn:
        try:
            with conn.transaction():
                ch = Changes(conn)
                result = fn(conn, ch)
                if not ch.items:
                    raise ApiError(409, "nothing_to_change", "The request would not change anything.",
                                   result=jsonable(result))
                if opts.dry_run:
                    raise _Preview(result, ch.items)
                log = conn.execute(
                    "INSERT INTO ops_action_log (api_id, actor, params, changes, result, sim_time) "
                    "VALUES (%(a)s, %(u)s, %(p)s, %(c)s, %(r)s, %(t)s) RETURNING code",
                    {"a": api_id, "u": opts.actor, "p": Jsonb(jsonable(params)), "c": Jsonb(ch.items),
                     "r": Jsonb(jsonable(result)), "t": sim_now()}).fetchone()
        except _Preview as p:
            return {"status": "preview", "api_id": api_id, "result": jsonable(p.result), "changes": p.changes,
                    "message": "Nothing was saved. Call again without dry_run to apply."}
    return {"status": "applied", "api_id": api_id, "action_id": log["code"], "result": jsonable(result),
            "changes": ch.items,
            "undo": {"api_id": "actions.undo", "method": "POST", "path": f"/api/actions/{log['code']}/undo"}}


_COLTYPES: dict[str, dict[str, str]] = {}


def _coltypes(conn, table: str) -> dict[str, str]:
    if table not in _COLTYPES:
        rows = conn.execute("SELECT column_name, data_type FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = %(t)s", {"t": table}).fetchall()
        _COLTYPES[table] = {r["column_name"]: r["data_type"] for r in rows}
    return _COLTYPES[table]


def undo_action(code: str, actor: str) -> dict:
    """Reverse a logged action. Refuses (409) if any row was changed after the action."""
    digits = code.strip().upper().rpartition("-")[2]
    if not digits.isdigit():
        raise ApiError(422, "invalid_code", f"'{code}' is not an action code (ACT-000123).")
    with rw() as conn:
        with conn.transaction():
            log = conn.execute("SELECT * FROM ops_action_log WHERE id = %(i)s FOR UPDATE", {"i": int(digits)}).fetchone()
            if log is None:
                raise ApiError(404, "action_not_found", f"No action {code}.")
            if log["undone_at"] is not None:
                raise ApiError(409, "already_undone", f"{log['code']} was already undone.")
            conflicts, reverted = [], []
            for ch in reversed(log["changes"]):
                table, pk_col, pk = ch["table"], ch["pk_col"], ch["pk"]
                if table not in OPS_TABLES:
                    raise ApiError(403, "table_not_writable", f"{table} cannot be changed through the API.")
                ident = sql.Identifier(table)
                current = conn.execute(sql.SQL("SELECT * FROM {} WHERE {} = %(pk)s FOR UPDATE").format(
                    ident, sql.Identifier(pk_col)), {"pk": pk}).fetchone()
                if current is None or jsonable(current) != ch["after"]:
                    conflicts.append({"table": table, "pk": pk,
                                      "reason": "row deleted" if current is None else "row changed after the action"})
                    continue
                if ch["op"] == "insert":
                    conn.execute(sql.SQL("DELETE FROM {} WHERE {} = %(pk)s").format(ident, sql.Identifier(pk_col)),
                                 {"pk": pk})
                else:
                    types = _coltypes(conn, table)
                    cols = [c for c in ch["before"] if ch["before"][c] != ch["after"].get(c)]
                    sets = sql.SQL(", ").join(
                        sql.SQL("{} = {}::{}").format(sql.Identifier(c), sql.Placeholder(c), sql.SQL(types[c]))
                        for c in cols)
                    conn.execute(sql.SQL("UPDATE {} SET {} WHERE {} = %(__pk)s").format(ident, sets,
                                                                                        sql.Identifier(pk_col)),
                                 {**{c: ch["before"][c] for c in cols}, "__pk": pk})
                reverted.append({"table": table, "pk": pk, "op": f"undo_{ch['op']}"})
            if conflicts:
                raise ApiError(409, "undo_conflict", f"{log['code']} cannot be undone: data changed since.",
                               conflicts=conflicts)
            conn.execute("UPDATE ops_action_log SET undone_at = now(), undone_by = %(u)s WHERE id = %(i)s",
                         {"u": actor, "i": log["id"]})
    return {"status": "undone", "action_id": log["code"], "api_id": log["api_id"], "reverted": reverted}
