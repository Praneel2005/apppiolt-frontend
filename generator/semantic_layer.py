"""Semantic layer (card T5): datasets, fields and governed metrics over the Olist fact tables.

Metric SQL is defined ONCE here; widgets, the query engine, the validator and the gold-answer
generator all reuse it. Enum values are read from the database, never typed by hand.
Currency: Olist prices are in Brazilian reais (BRL).
"""
from __future__ import annotations

import psycopg

from contracts.metadata import Dataset, FieldDef, Metric

# revenue-type metrics ignore canceled / unavailable orders (decision in docs/DATA_NOTES.md)
VALID = "FILTER (WHERE is_valid_sale)"

# (name, type, role, description); enum values are filled from the DB
_ITEM_FIELDS = [
    ("order_id", "string", "id", "Order identifier"),
    ("order_item_id", "integer", "id", "Line number within the order"),
    ("product_id", "string", "id", "Product identifier"),
    ("seller_id", "string", "id", "Seller identifier"),
    ("price", "decimal", "attribute", "Item price in BRL"),
    ("freight_value", "decimal", "attribute", "Freight charged for the item in BRL"),
    ("order_status", "enum", "dimension", "Order status (delivered, shipped, canceled, ...)"),
    ("is_valid_sale", "boolean", "attribute", "False for canceled or unavailable orders"),
    ("order_date", "date", "time", "Date the order was placed"),
    ("order_month", "date", "dimension", "Calendar month the order was placed"),
    ("customer_state", "enum", "dimension", "Brazilian state of the customer (two-letter code)"),
    ("customer_region", "enum", "dimension", "Brazilian macro-region of the customer"),
    ("seller_state", "enum", "dimension", "Brazilian state of the seller (two-letter code)"),
    ("product_category", "enum", "dimension", "Product category (English)"),
    ("delivery_days", "decimal", "attribute", "Days from purchase to delivery (delivered orders only)"),
    ("is_late", "integer", "attribute", "1 if delivered after the estimated date, 0 if on time, empty if not delivered"),
]
_ORDER_FIELDS = [
    ("order_id", "string", "id", "Order identifier"),
    ("order_status", "enum", "dimension", "Order status"),
    ("is_valid_sale", "boolean", "attribute", "False for canceled or unavailable orders"),
    ("order_date", "date", "time", "Date the order was placed"),
    ("order_month", "date", "dimension", "Calendar month the order was placed"),
    ("customer_state", "enum", "dimension", "Brazilian state of the customer"),
    ("customer_region", "enum", "dimension", "Brazilian macro-region of the customer"),
    ("delivery_days", "decimal", "attribute", "Days from purchase to delivery"),
    ("is_late", "integer", "attribute", "1 if delivered late, 0 if on time"),
]
_PAYMENT_FIELDS = [
    ("order_id", "string", "id", "Order identifier"),
    ("payment_type", "enum", "dimension", "Payment method (credit_card, boleto, voucher, debit_card)"),
    ("payment_installments", "integer", "attribute", "Number of installments"),
    ("payment_value", "decimal", "attribute", "Amount paid in BRL"),
    ("order_date", "date", "time", "Date the order was placed"),
    ("order_month", "date", "dimension", "Calendar month the order was placed"),
    ("customer_state", "enum", "dimension", "Brazilian state of the customer"),
    ("customer_region", "enum", "dimension", "Brazilian macro-region of the customer"),
]
_REVIEW_FIELDS = [
    ("review_id", "string", "id", "Review identifier (not unique in the source)"),
    ("order_id", "string", "id", "Order identifier"),
    ("review_score", "integer", "attribute", "Customer review score from 1 to 5"),
    ("order_date", "date", "time", "Date the reviewed order was placed"),
    ("order_month", "date", "dimension", "Calendar month the reviewed order was placed"),
    ("customer_state", "enum", "dimension", "Brazilian state of the customer"),
    ("customer_region", "enum", "dimension", "Brazilian macro-region of the customer"),
]

# (metric_id, title, description, sql, unit, additive, higher_is_better)
_SPEC = [
    ("ds_order_items", "fact_order_items",
     "Order lines (items) with customer and seller location, product category, price, freight and delivery outcome",
     _ITEM_FIELDS, [
         ("revenue", "Revenue", "Total item price of valid sales (excludes canceled and unavailable orders)",
          f"SUM(price) {VALID}", "BRL", True, True),
         ("freight", "Freight", "Total freight charged on valid sales",
          f"SUM(freight_value) {VALID}", "BRL", True, False),
         ("items_sold", "Items sold", "Number of order lines in valid sales",
          f"COUNT(*) {VALID}", "count", True, True),
         ("orders", "Orders", "Number of distinct orders with at least one item (valid sales)",
          f"COUNT(DISTINCT order_id) {VALID}", "count", True, True),
         ("aov", "Average order value", "Revenue divided by the number of orders (valid sales)",
          f"SUM(price) {VALID} / NULLIF(COUNT(DISTINCT order_id) {VALID}, 0)", "BRL", False, True),
         ("avg_delivery_days", "Average delivery time", "Average days from purchase to delivery for delivered items",
          "AVG(delivery_days)", "days", False, False),
         ("late_rate", "Late delivery rate", "Share of delivered items that arrived after the estimated date",
          "AVG(is_late)", "ratio", False, False),
     ]),
    ("ds_orders", "fact_orders",
     "Orders (one row per order) with status, customer location and delivery outcome",
     _ORDER_FIELDS, [
         ("order_count", "Order count", "Number of orders of any status", "COUNT(*)", "count", True, True),
         ("cancel_rate", "Cancellation rate", "Share of orders that were canceled",
          "AVG(CASE WHEN order_status = 'canceled' THEN 1.0 ELSE 0 END)", "ratio", False, False),
         ("order_late_rate", "Late order rate", "Share of delivered orders that arrived late",
          "AVG(is_late)", "ratio", False, False),
     ]),
    ("ds_payments", "fact_payments",
     "Payments (one row per payment) with method, installments and customer location",
     _PAYMENT_FIELDS, [
         ("payment_value", "Payments received", "Total amount paid", "SUM(payment_value)", "BRL", True, True),
         ("payment_count", "Number of payments", "Number of payment records", "COUNT(*)", "count", True, True),
         ("avg_installments", "Average installments", "Average number of installments per payment",
          "AVG(payment_installments)", "count", False, False),
     ]),
    ("ds_reviews", "fact_reviews",
     "Customer reviews (one row per review) with score and customer location",
     _REVIEW_FIELDS, [
         ("review_count", "Number of reviews", "Number of reviews submitted", "COUNT(*)", "count", True, True),
         ("avg_review_score", "Average review score", "Average customer review score (1 to 5)",
          "AVG(review_score)", "score", False, True),
     ]),
]


def _enum_values(conn: psycopg.Connection, table: str, column: str) -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY 1"
    ).fetchall()
    return [str(r[0]) for r in rows]


def build_datasets(conn: psycopg.Connection) -> list[Dataset]:
    datasets = []
    for dataset_id, table, description, fields, metrics in _SPEC:
        defs = [
            FieldDef(name=n, type=t, role=r, description=d,
                     values=_enum_values(conn, table, n) if t == "enum" else None)
            for n, t, r, d in fields
        ]
        datasets.append(Dataset(
            dataset_id=dataset_id,
            table=table,
            description=description,
            time_field="order_date",
            fields=defs,
            metrics=[Metric(metric_id=m, title=ti, description=de, sql=sq, unit=u,
                            additive=ad, higher_is_better=hb)
                     for m, ti, de, sq, u, ad, hb in metrics],
        ))
    return datasets
