"""Orders (real Olist data): search and order detail."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (Paging, Where, check_enum, check_enum_list, date_range, jsonable, order_by, page,
                            resolve_ref, rows_json, source)
from backend.db import ro

router = APIRouter()

ORDER_COLS = """s.order_id, s.order_ref, s.order_status, s.purchased_at, s.estimated_delivery_at, s.delivered_at,
  s.customer_unique_id, s.customer_city, s.customer_state, s.customer_region, s.items, s.items_value, s.freight,
  s.payment_total, s.payment_type, s.review_score, s.categories, s.seller_ids, s.is_late, s.days_late,
  s.days_overdue, round(s.delivery_days::numeric, 2) AS delivery_days,
  (SELECT count(*) FROM ops_support_tickets t WHERE t.order_id = s.order_id
     AND t.status IN ('open', 'in_progress')) AS open_tickets"""


class OrdersQuery(Paging):
    status: str | None = Field(None, description="Order status: delivered, shipped, canceled, ...")
    customer_state: str | None = Field(None, description="Customer state codes, comma-separated (SP,RJ)")
    customer_region: str | None = Field(None, description="Customer macro-region (Southeast, South, ...)")
    product_category: str | None = Field(None, description="Orders containing this product category")
    seller_id: str | None = Field(None, description="Orders containing items from this seller (id or 8-char ref)")
    customer_id: str | None = Field(None, description="customer_unique_id of the buyer")
    date_from: date | None = Field(None, description="Purchased on or after (YYYY-MM-DD)")
    date_to: date | None = Field(None, description="Purchased on or before (YYYY-MM-DD)")
    preset: str | None = Field(None, description="Relative purchase-date range, e.g. last_month, last_quarter, month:2018-03")
    late: bool | None = Field(None, description="true = delivered after the estimated date, false = on time")
    min_days_late: int | None = Field(None, ge=1, description="Delivered at least this many days late")
    overdue: bool | None = Field(None, description="true = not delivered yet and past the estimated date")
    review_score_max: int | None = Field(None, ge=1, le=5, description="Latest review score at most this (1-5)")
    has_open_ticket: bool | None = Field(None, description="true = has an open or in-progress support ticket")
    sort: str | None = Field(None, description="purchased_at, items_value, days_late, days_overdue, review_score; "
                                                "prefix '-' for descending (default -purchased_at)")


SORTS = {"purchased_at": "s.purchased_at", "items_value": "s.items_value", "days_late": "s.days_late",
         "days_overdue": "s.days_overdue", "review_score": "s.review_score", "delivery_days": "s.delivery_days"}


def search_orders(conn, q: OrdersQuery) -> tuple[list[dict], int]:
    w = Where()
    if q.status:
        w.add("s.order_status = %(status)s", status=check_enum("order_status", q.status, "status"))
    if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
        w.add("s.customer_state = ANY(%(states)s)", states=states)
    if q.customer_region:
        w.add("s.customer_region = %(region)s", region=check_enum("customer_region", q.customer_region))
    if q.product_category:
        w.add("%(cat)s = ANY(s.categories)", cat=check_enum("product_category", q.product_category))
    if q.seller_id:
        w.add("%(seller)s = ANY(s.seller_ids)",
              seller=resolve_ref(conn, "raw_sellers", "seller_id", q.seller_id, "seller"))
    if q.customer_id:
        w.add("s.customer_unique_id = %(cust)s", cust=q.customer_id.strip().lower())
    f, t = date_range(q.date_from, q.date_to, q.preset)
    if f:
        w.add("s.order_date >= %(f)s", f=f)
    if t:
        w.add("s.order_date <= %(t)s", t=t)
    if q.late is not None:
        w.add("s.is_late = %(late)s", late=q.late)
    if q.min_days_late:
        w.add("s.days_late >= %(mdl)s", mdl=q.min_days_late)
    if q.overdue is not None:
        w.add("(s.days_overdue > 0) = %(od)s" if q.overdue else "COALESCE(s.days_overdue, 0) = 0", od=q.overdue)
    if q.review_score_max:
        w.add("s.review_score <= %(rs)s", rs=q.review_score_max)
    if q.has_open_ticket is not None:
        w.add(("" if q.has_open_ticket else "NOT ") + "EXISTS (SELECT 1 FROM ops_support_tickets t WHERE "
              "t.order_id = s.order_id AND t.status IN ('open', 'in_progress'))")
    total = conn.execute(f"SELECT count(*) AS n FROM core_order_summary s{w.sql()}", w.params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {ORDER_COLS} FROM core_order_summary s{w.sql()}{order_by(q.sort, SORTS, '-purchased_at')}"
        f" LIMIT %(limit)s OFFSET %(offset)s", {**w.params, "limit": q.limit, "offset": q.offset}).fetchall()
    return rows, total


@endpoint(router, "GET", "/api/orders", api_id="orders.search", entity="order",
          title="Search orders",
          description="Find customer orders by status, customer state or region, product category, seller, "
                      "purchase date, lateness, overdue deliveries, review score or open tickets. Each order shows "
                      "value (items, excluding freight), freight, payment, delivery dates, days late and review.",
          data_layers=["original", "derived", "synthetic"],
          returns="items[] of orders with order_ref, status, dates, customer_state, items_value, freight, days_late, "
                  "days_overdue, review_score, open_tickets; total",
          examples=["show late orders in RJ last month", "orders more than 10 days late",
                    "undelivered orders past their estimated date", "orders with a 1-star review and no ticket"])
def orders_search(q: Annotated[OrdersQuery, Query()]):
    with ro() as conn:
        rows, total = search_orders(conn, q)
    return page(rows, total, q.limit, q.offset,
                source(["core_order_summary", "ops_support_tickets"], ["original", "derived", "synthetic"],
                       "open_tickets is synthetic; everything else is real Olist data"))


@endpoint(router, "GET", "/api/orders/{order_ref}", api_id="orders.get", entity="order",
          title="Get order details",
          description="Everything about one order: status and dates, customer, each item with product, category, "
                      "seller, price and freight, payments, reviews with seller replies, and support tickets.",
          data_layers=["original", "derived", "synthetic"],
          returns="order, items[], payments[], reviews[], tickets[]",
          examples=["what happened with order E481F51C", "why was this order late"])
def orders_get(order_ref: str):
    with ro() as conn:
        oid = resolve_ref(conn, "raw_orders", "order_id", order_ref, "order")
        order = conn.execute(f"SELECT {ORDER_COLS} FROM core_order_summary s WHERE s.order_id = %(o)s",
                             {"o": oid}).fetchone()
        items = conn.execute("""
            SELECT i.order_item_id, i.product_id, upper(left(i.product_id, 8)) AS product_ref,
                   COALESCE(m.product_category_name_english, p.product_category_name, 'unknown') AS product_category,
                   i.seller_id, upper(left(i.seller_id, 8)) AS seller_ref, se.seller_state, i.price, i.freight_value,
                   i.shipping_limit_date
            FROM raw_order_items i LEFT JOIN raw_products p USING (product_id)
            LEFT JOIN core_category_map m ON m.product_category_name = p.product_category_name
            LEFT JOIN raw_sellers se USING (seller_id)
            WHERE i.order_id = %(o)s ORDER BY i.order_item_id""", {"o": oid}).fetchall()
        payments = conn.execute("SELECT payment_sequential, payment_type, payment_installments, payment_value "
                                "FROM raw_payments WHERE order_id = %(o)s ORDER BY 1", {"o": oid}).fetchall()
        reviews = conn.execute("""
            SELECT r.review_id, r.review_score, r.review_comment_title, r.review_comment_message,
                   r.review_creation_date,
                   (SELECT json_build_object('code', x.code, 'reply_text', x.reply_text, 'created_at', x.created_at)
                    FROM ops_review_replies x WHERE x.review_id = r.review_id AND x.order_id = r.order_id
                    ORDER BY x.id DESC LIMIT 1) AS reply
            FROM raw_reviews r WHERE r.order_id = %(o)s ORDER BY r.review_creation_date""", {"o": oid}).fetchall()
        tickets = conn.execute("SELECT code, category, priority, status, subject, assignee, created_at, resolved_at "
                               "FROM ops_support_tickets WHERE order_id = %(o)s ORDER BY id", {"o": oid}).fetchall()
    return {"order": jsonable(order), "items": rows_json(items), "payments": rows_json(payments),
            "reviews": rows_json(reviews), "tickets": rows_json(tickets),
            "source": source(["raw_orders", "raw_order_items", "raw_payments", "raw_reviews", "core_order_summary",
                              "ops_review_replies", "ops_support_tickets"], ["original", "derived", "synthetic"],
                             "replies and tickets are synthetic")}
