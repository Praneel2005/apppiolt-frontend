"""Support tickets (synthetic, writable) on real Olist orders."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (ApiError, Body, Paging, Where, check_enum, check_enum_list, date_range, jsonable,
                            order_by, page, resolve_code, resolve_ref, source)
from backend.config import sim_now
from backend.db import ro
from backend.writes import Changes, WriteOpts, run_write

router = APIRouter()

Category = Literal["late_delivery", "not_received", "damaged_item", "wrong_item", "product_quality",
                   "refund_request", "other"]
Priority = Literal["low", "medium", "high", "urgent"]
SUBJECTS = {
    "late_delivery": "Order {ref} arrived after the promised date", "not_received": "Order {ref} not received yet",
    "damaged_item": "Item in order {ref} arrived damaged", "wrong_item": "Wrong item received in order {ref}",
    "product_quality": "Quality complaint about order {ref}", "refund_request": "Refund requested for order {ref}",
    "other": "Question about order {ref}",
}
TICKET_COLS = """t.code, t.order_id, upper(left(t.order_id, 8)) AS order_ref, t.category, t.priority, t.status,
  t.subject, t.assignee, t.created_at, t.resolved_at, t.created_by, t.notes, s.customer_state, s.customer_region,
  t.seller_id, upper(left(t.seller_id, 8)) AS seller_ref,
  round((EXTRACT(EPOCH FROM (COALESCE(t.resolved_at, %(now)s) - t.created_at)) / 3600.0)::numeric, 1) AS age_hours"""


class TicketsQuery(Paging):
    status: str | None = Field(None, description="Comma-separated: open, in_progress, resolved, closed")
    open_only: bool = Field(False, description="Only open or in_progress tickets")
    category: str | None = Field(None, description="late_delivery, not_received, damaged_item, wrong_item, ...")
    priority: str | None = Field(None, description="low, medium, high, urgent")
    assignee: str | None = Field(None, description="Support agent name")
    unassigned: bool | None = Field(None, description="true = nobody assigned")
    customer_state: str | None = Field(None, description="Customer state codes, comma-separated")
    order_id: str | None = Field(None, description="Order id or 8-char ref")
    seller_id: str | None = Field(None, description="Seller id or 8-char ref")
    date_from: date | None = Field(None, description="Created on or after")
    date_to: date | None = Field(None, description="Created on or before")
    preset: str | None = Field(None, description="Relative created-date range, e.g. last_7_days")
    sort: str | None = Field(None, description="created_at (default -created_at), priority, age_hours")


PRIORITY_RANK = "CASE t.priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END"


@endpoint(router, "GET", "/api/tickets", api_id="tickets.search", entity="ticket",
          title="Search support tickets",
          description="Customer support tickets with category, priority, status, assignee, age and the order's "
                      "customer state. Filter by status, category, priority, assignee, state, order, seller or date.",
          data_layers=["synthetic", "original"], returns="items[] of tickets; total; counts_by_status",
          examples=["open urgent tickets", "unassigned late-delivery tickets in RJ", "tickets for order E481F51C",
                    "how many tickets did we resolve last week"])
def tickets_search(q: Annotated[TicketsQuery, Query()]):
    w = Where()
    with ro() as conn:
        if statuses := check_enum_list("ticket_status", q.status, "status"):
            w.add("t.status = ANY(%(st)s)", st=statuses)
        if q.open_only:
            w.add("t.status IN ('open', 'in_progress')")
        if q.category:
            w.add("t.category = %(cat)s", cat=check_enum("ticket_category", q.category, "category"))
        if q.priority:
            w.add("t.priority = %(pr)s", pr=check_enum("ticket_priority", q.priority, "priority"))
        if q.assignee:
            w.add("t.assignee = %(asg)s", asg=q.assignee.strip().lower())
        if q.unassigned is not None:
            w.add("(t.assignee IS NULL) = %(un)s", un=q.unassigned)
        if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
            w.add("s.customer_state = ANY(%(states)s)", states=states)
        if q.order_id:
            w.add("t.order_id = %(o)s", o=resolve_ref(conn, "raw_orders", "order_id", q.order_id, "order"))
        if q.seller_id:
            w.add("t.seller_id = %(se)s", se=resolve_ref(conn, "raw_sellers", "seller_id", q.seller_id, "seller"))
        f, t = date_range(q.date_from, q.date_to, q.preset)
        if f:
            w.add("t.created_at >= %(f)s", f=f)
        if t:
            w.add("t.created_at < %(t)s::date + 1", t=t)
        frm = "FROM ops_support_tickets t JOIN core_order_summary s USING (order_id)"
        params = {**w.params, "now": sim_now()}
        total = conn.execute(f"SELECT count(*) AS n {frm}{w.sql()}", params).fetchone()["n"]
        counts = conn.execute(f"SELECT t.status, count(*) AS n {frm}{w.sql()} GROUP BY 1", params).fetchall()
        rows = conn.execute(
            f"SELECT {TICKET_COLS} {frm}{w.sql()}"
            f"{order_by(q.sort, {'created_at': 't.created_at', 'priority': PRIORITY_RANK, 'age_hours': 'age_hours'}, '-created_at')}"
            f" LIMIT %(limit)s OFFSET %(offset)s", {**params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset, source(["ops_support_tickets", "core_order_summary"],
                                                       ["synthetic", "original"]),
                counts_by_status={c["status"]: c["n"] for c in counts})


@endpoint(router, "GET", "/api/tickets/{code}", api_id="tickets.get", entity="ticket",
          title="Get a support ticket",
          description="One ticket with its order summary (status, dates, lateness, value, review).",
          data_layers=["synthetic", "original"], returns="ticket, order",
          examples=["show TCK-000123"])
def tickets_get(code: str):
    with ro() as conn:
        t = resolve_code(conn, "ops_support_tickets", code, "ticket", "TCK")
        row = conn.execute(f"SELECT {TICKET_COLS} FROM ops_support_tickets t JOIN core_order_summary s "
                           f"USING (order_id) WHERE t.id = %(i)s", {"i": t["id"], "now": sim_now()}).fetchone()
        order = conn.execute("SELECT order_ref, order_status, purchased_at, estimated_delivery_at, delivered_at, "
                             "is_late, days_late, days_overdue, items_value, freight, review_score, categories "
                             "FROM core_order_summary WHERE order_id = %(o)s", {"o": t["order_id"]}).fetchone()
    return {"ticket": jsonable(row), "order": jsonable(order),
            "source": source(["ops_support_tickets", "core_order_summary"], ["synthetic", "original"])}


class TicketCreate(Body):
    order_id: str = Field(..., description="Order id or 8-char ref")
    category: Category
    priority: Priority = "medium"
    subject: str | None = Field(None, max_length=200, description="Default: generated from category and order")
    assignee: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=2000)


def _create_ticket(conn, ch: Changes, oid: str, category: str, priority: str, subject: str | None,
                   assignee: str | None, notes: str | None, actor: str) -> dict:
    o = conn.execute("SELECT customer_unique_id, seller_ids[1] AS seller_id FROM core_order_summary "
                     "WHERE order_id = %(o)s", {"o": oid}).fetchone()
    dup = conn.execute("SELECT code FROM ops_support_tickets WHERE order_id = %(o)s AND category = %(c)s "
                       "AND status IN ('open', 'in_progress')", {"o": oid, "c": category}).fetchone()
    if dup:
        raise ApiError(409, "duplicate_ticket", f"{dup['code']} is already open for this order and category.",
                       existing=dup["code"])
    return ch.insert("ops_support_tickets", {
        "order_id": oid, "customer_unique_id": o["customer_unique_id"], "seller_id": o["seller_id"],
        "category": category, "priority": priority, "status": "in_progress" if assignee else "open",
        "subject": subject or SUBJECTS[category].format(ref=oid[:8].upper()),
        "assignee": assignee.lower() if assignee else None, "created_at": sim_now(), "created_by": actor,
        "notes": notes})


@endpoint(router, "POST", "/api/tickets", api_id="tickets.create", entity="ticket",
          title="Open a support ticket",
          description="Open a support ticket for an order (late delivery, not received, damaged, wrong item, "
                      "quality, refund). Refused if the same order already has an open ticket of that category.",
          data_layers=["synthetic"], returns="the new ticket (code TCK-...)",
          examples=["open a ticket for order E481F51C, it was not received", "create a refund ticket for this order"])
def tickets_create(body: TicketCreate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        oid = resolve_ref(conn, "raw_orders", "order_id", body.order_id, "order")
        return _create_ticket(conn, ch, oid, body.category, body.priority, body.subject, body.assignee,
                              body.notes, opts.actor)

    return run_write("tickets.create", opts, body.model_dump(), apply)


class TicketBulk(Body):
    order_ids: list[str] = Field(..., min_length=1, max_length=200, description="Orders (ids or 8-char refs)")
    category: Category
    priority: Priority = "medium"
    assignee: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=2000)


@endpoint(router, "POST", "/api/tickets/bulk", api_id="tickets.bulk_create", entity="ticket",
          title="Open tickets for many orders",
          description="Open one ticket per order for a list of orders (e.g. the result of an orders search). "
                      "Orders that already have an open ticket of the same category are skipped and reported.",
          data_layers=["synthetic"], returns="created[] ticket codes and skipped[] with reasons",
          examples=["open tickets for all RJ orders more than 10 days late",
                    "create late-delivery tickets for these orders"])
def tickets_bulk(body: TicketBulk, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        created, skipped = [], []
        for ref in dict.fromkeys(body.order_ids):
            try:
                with conn.transaction():
                    oid = resolve_ref(conn, "raw_orders", "order_id", ref, "order")
                    created.append(_create_ticket(conn, ch, oid, body.category, body.priority, None, body.assignee,
                                                  body.notes, opts.actor)["code"])
            except ApiError as e:
                skipped.append({"order": ref, "reason": e.code, "message": e.message})
        return {"created": created, "skipped": skipped}

    return run_write("tickets.bulk_create", opts, body.model_dump(), apply)


class TicketUpdate(Body):
    status: Literal["open", "in_progress", "resolved", "closed"] | None = None
    priority: Priority | None = None
    assignee: str | None = Field(None, max_length=50, description="Assign to this agent ('' to unassign)")
    add_note: str | None = Field(None, max_length=2000, description="Appended to the ticket notes")


ALLOWED = {"open": {"in_progress", "resolved"}, "in_progress": {"open", "resolved"},
           "resolved": {"closed", "open"}, "closed": {"open"}}


@endpoint(router, "PATCH", "/api/tickets/{code}", api_id="tickets.update", entity="ticket",
          title="Update a support ticket",
          description="Change a ticket's status (open -> in_progress -> resolved -> closed, or reopen), priority "
                      "or assignee, or add a note.",
          data_layers=["synthetic"], returns="the ticket before/after",
          examples=["resolve TCK-000123", "assign this ticket to carla", "escalate TCK-000123 to urgent"])
def tickets_update(code: str, body: TicketUpdate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        t = resolve_code(conn, "ops_support_tickets", code, "ticket", "TCK")
        values = {}
        if body.status and body.status != t["status"]:
            if body.status not in ALLOWED[t["status"]]:
                raise ApiError(409, "invalid_transition", f"{t['code']} is {t['status']}; it cannot become "
                                                          f"{body.status}.", allowed=sorted(ALLOWED[t["status"]]))
            values["status"] = body.status
            if body.status == "resolved":
                values["resolved_at"] = sim_now()
            elif body.status in ("open", "in_progress"):
                values["resolved_at"] = None
        if body.priority and body.priority != t["priority"]:
            values["priority"] = body.priority
        if body.assignee is not None:
            values["assignee"] = body.assignee.strip().lower() or None
        if body.add_note:
            stamp = sim_now().strftime("%Y-%m-%d %H:%M")
            values["notes"] = ((t["notes"] + "\n") if t["notes"] else "") + f"[{stamp} {opts.actor}] {body.add_note}"
        if not values:
            raise ApiError(409, "nothing_to_change", "No field would change.")
        return ch.update("ops_support_tickets", t["id"], values)

    return run_write("tickets.update", opts, {"code": code, **body.model_dump()}, apply)
