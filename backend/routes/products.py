"""Products, inventory and restock orders. Products and sales are real; price/cost/stock are synthetic."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (ApiError, Body, Paging, Params, Where, check_enum, date_range, jsonable, order_by,
                            page, resolve_code, resolve_ref, rows_json, source)
from backend.config import as_of_date, sim_now
from backend.db import ro
from backend.writes import Changes, WriteOpts, run_write

router = APIRouter()

# demand window for "units sold in the last 90 days" (real sales up to as_of_date)
PRODUCT_SQL = """
WITH sales AS (
  SELECT product_id, count(*) AS units_90d, sum(price) AS revenue_90d
  FROM fact_order_items
  WHERE is_valid_sale AND order_date > %(d90)s AND order_date <= %(asof)s
  GROUP BY product_id),
open_ro AS (
  SELECT product_id, sum(quantity) AS on_order_qty, min(expected_arrival) AS next_arrival
  FROM ops_restock_orders WHERE status IN ('placed', 'in_transit') GROUP BY product_id)
SELECT p.product_id, upper(left(p.product_id, 8)) AS product_ref,
       COALESCE(m.product_category_name_english, p.product_category_name, 'unknown') AS product_category,
       (l.product_id IS NOT NULL) AS listed, l.status AS listing_status, l.list_price, l.unit_cost,
       round(100 * (l.list_price - l.unit_cost) / NULLIF(l.list_price, 0), 1) AS margin_pct,
       l.stock_quantity, l.reorder_point, l.order_up_to, l.lead_time_days,
       COALESCE(o.on_order_qty, 0) AS on_order_qty, o.next_arrival,
       (l.stock_quantity <= l.reorder_point) AS below_reorder_point,
       (l.stock_quantity <= l.reorder_point AND o.on_order_qty IS NULL) AS needs_restock,
       COALESCE(s.units_90d, 0) AS units_sold_90d, COALESCE(s.revenue_90d, 0) AS revenue_90d,
       round(l.stock_quantity / NULLIF(s.units_90d / 90.0, 0), 1) AS days_of_cover,
       round(l.stock_quantity * l.unit_cost, 2) AS inventory_value,
       p.product_weight_g, p.product_photos_qty
FROM raw_products p
LEFT JOIN core_category_map m ON m.product_category_name = p.product_category_name
LEFT JOIN ops_product_listings l ON l.product_id = p.product_id
LEFT JOIN open_ro o ON o.product_id = p.product_id
LEFT JOIN sales s ON s.product_id = p.product_id
"""
SORTS = {"units_sold_90d": "units_sold_90d", "revenue_90d": "revenue_90d", "stock_quantity": "stock_quantity",
         "days_of_cover": "days_of_cover", "list_price": "list_price", "inventory_value": "inventory_value",
         "margin_pct": "margin_pct"}


def _window() -> dict:
    a = as_of_date()
    return {"d90": a - timedelta(days=90), "asof": a}


class ProductsQuery(Paging):
    product_category: str | None = Field(None, description="Product category (English name)")
    listed: bool | None = Field(None, description="true = has a current listing (price, stock)")
    below_reorder_point: bool | None = Field(None, description="true = stock at or below the reorder point")
    needs_restock: bool | None = Field(None, description="true = below reorder point AND nothing on order")
    min_units_sold_90d: int | None = Field(None, ge=0, description="Sold at least this many units in the last 90 days")
    sort: str | None = Field(None, description="units_sold_90d, revenue_90d, stock_quantity, days_of_cover, "
                                                "list_price, inventory_value, margin_pct; '-' prefix = descending")


def search_products(conn, q: ProductsQuery, default_sort: str = "-units_sold_90d") -> tuple[list[dict], int]:
    w = Where()
    if q.product_category:
        w.add("x.product_category = %(cat)s", cat=check_enum("product_category", q.product_category))
    for flag in ("listed", "below_reorder_point", "needs_restock"):
        v = getattr(q, flag)
        if v is not None:
            w.add(f"COALESCE(x.{flag}, false) = %({flag})s", **{flag: v})
    if q.min_units_sold_90d is not None:
        w.add("x.units_sold_90d >= %(mu)s", mu=q.min_units_sold_90d)
    base = f"SELECT * FROM ({PRODUCT_SQL}) x{w.sql()}"
    params = {**_window(), **w.params}
    total = conn.execute(f"SELECT count(*) AS n FROM ({base}) c", params).fetchone()["n"]
    tie = ", units_sold_90d DESC, product_id" if q.sort is None else ", product_id"  # stable, best sellers first
    rows = conn.execute(f"{base}{order_by(q.sort, SORTS, default_sort)}{tie} LIMIT %(limit)s OFFSET %(offset)s",
                        {**params, "limit": q.limit, "offset": q.offset}).fetchall()
    return rows, total


SRC = ["raw_products", "fact_order_items", "ops_product_listings", "ops_restock_orders"]
NOTE = "categories and units sold are real Olist data; price, cost, stock and restock orders are synthetic"


@endpoint(router, "GET", "/api/products", api_id="products.search", entity="product",
          title="Search products and stock",
          description="List products with category, list price, unit cost, margin, stock on hand, reorder point, "
                      "quantity on order, units sold and revenue in the last 90 days, days of stock cover and "
                      "inventory value. Filter by category, listing, low stock or restock need.",
          data_layers=["original", "derived", "synthetic"],
          returns="items[] of products with stock and sales figures; total",
          examples=["best selling products in health_beauty", "products with the highest inventory value",
                    "which products have less than a week of stock"])
def products_search(q: Annotated[ProductsQuery, Query()]):
    with ro() as conn:
        rows, total = search_products(conn, q)
    return page(rows, total, q.limit, q.offset, source(SRC, ["original", "derived", "synthetic"], NOTE))


class AlertsQuery(Paging):
    product_category: str | None = Field(None, description="Only this product category")
    include_on_order: bool = Field(False, description="Also show low-stock products that already have a restock order")
    sort: str | None = Field(None, description="days_of_cover (default), units_sold_90d, revenue_90d, stock_quantity")


@endpoint(router, "GET", "/api/inventory/alerts", api_id="inventory.alerts", entity="inventory",
          title="Low-stock alerts",
          description="Products whose stock is at or below their reorder point and that have nothing on order "
                      "(need restocking), most urgent first (fewest days of cover).",
          data_layers=["synthetic", "original"],
          returns="items[] of products needing restock with stock, reorder point, suggested quantity; total",
          examples=["which products need restocking", "low stock alerts in furniture_decor",
                    "products below reorder point"])
def inventory_alerts(q: Annotated[AlertsQuery, Query()]):
    pq = ProductsQuery(limit=q.limit, offset=q.offset, product_category=q.product_category, sort=q.sort,
                       **({"below_reorder_point": True} if q.include_on_order else {"needs_restock": True}))
    with ro() as conn:
        rows, total = search_products(conn, pq, default_sort="days_of_cover")
    for r in rows:
        r["suggested_quantity"] = max(1, (r["order_up_to"] or 0) - (r["stock_quantity"] or 0) - (r["on_order_qty"] or 0))
    return page(rows, total, q.limit, q.offset, source(SRC, ["synthetic", "original"], NOTE))


class SnapshotsQuery(Params):
    product_id: str | None = Field(None, description="One product (id or 8-char ref); otherwise totals")
    product_category: str | None = Field(None, description="Totals for one category")


@endpoint(router, "GET", "/api/inventory/snapshots", api_id="inventory.snapshots", entity="inventory",
          title="Inventory history (month-end)",
          description="Month-end stock history (Mar-Aug 2018): units in stock, inventory value at cost and number "
                      "of products below their reorder point, for one product, one category or the whole catalogue.",
          data_layers=["synthetic"],
          returns="items[] per snapshot_date with stock_units, inventory_value, products_below_reorder_point",
          examples=["how has inventory value changed", "stock history of product 1E9E8EF0"])
def inventory_snapshots(q: Annotated[SnapshotsQuery, Query()]):
    w = Where()
    with ro() as conn:
        if q.product_id:
            w.add("s.product_id = %(p)s", p=resolve_ref(conn, "raw_products", "product_id", q.product_id, "product"))
        if q.product_category:
            w.add("COALESCE(m.product_category_name_english, p.product_category_name, 'unknown') = %(c)s",
                  c=check_enum("product_category", q.product_category))
        rows = conn.execute(f"""
            SELECT s.snapshot_date, count(*) AS products, sum(s.stock_quantity) AS stock_units,
                   round(sum(s.stock_quantity * s.unit_cost), 2) AS inventory_value,
                   count(*) FILTER (WHERE s.stock_quantity <= s.reorder_point) AS products_below_reorder_point
            FROM syn_inventory_snapshots s JOIN raw_products p USING (product_id)
            LEFT JOIN core_category_map m ON m.product_category_name = p.product_category_name
            {w.sql()} GROUP BY 1 ORDER BY 1""", w.params).fetchall()
    return {"items": rows_json(rows), "source": source(["syn_inventory_snapshots"], ["synthetic"],
                                                       "simulated stock driven by real Olist unit sales")}


@endpoint(router, "GET", "/api/products/{product_ref}", api_id="products.get", entity="product",
          title="Get product details",
          description="One product: category, listing (price, cost, stock, reorder settings), monthly units and "
                      "revenue (real), stock history, restock orders and price changes.",
          data_layers=["original", "derived", "synthetic"],
          returns="product, monthly_sales[], stock_history[], restock_orders[], price_changes[]",
          examples=["details of product 1E9E8EF0"])
def products_get(product_ref: str):
    with ro() as conn:
        pid = resolve_ref(conn, "raw_products", "product_id", product_ref, "product")
        product = conn.execute(f"SELECT * FROM ({PRODUCT_SQL}) x WHERE x.product_id = %(p)s",
                               {**_window(), "p": pid}).fetchone()
        monthly = conn.execute("""
            SELECT order_month, count(*) AS units, round(sum(price)::numeric, 2) AS revenue
            FROM fact_order_items WHERE product_id = %(p)s AND is_valid_sale AND order_date <= %(a)s
            GROUP BY 1 ORDER BY 1""", {"p": pid, "a": as_of_date()}).fetchall()
        history = conn.execute("SELECT snapshot_date, stock_quantity, reorder_point FROM syn_inventory_snapshots "
                               "WHERE product_id = %(p)s ORDER BY 1", {"p": pid}).fetchall()
        restocks = conn.execute("SELECT code, quantity, status, created_at, expected_arrival, received_at, created_by "
                                "FROM ops_restock_orders WHERE product_id = %(p)s ORDER BY id DESC", {"p": pid}).fetchall()
        prices = conn.execute("SELECT code, old_price, new_price, reason, created_at, created_by "
                              "FROM ops_price_changes WHERE product_id = %(p)s ORDER BY id DESC", {"p": pid}).fetchall()
    return {"product": jsonable(product), "monthly_sales": rows_json(monthly), "stock_history": rows_json(history),
            "restock_orders": rows_json(restocks), "price_changes": rows_json(prices),
            "source": source(SRC + ["syn_inventory_snapshots", "ops_price_changes"],
                             ["original", "derived", "synthetic"], NOTE)}


# ------------------------------------------------------------------------------------ writes
MAX_PRICE_CHANGE = 0.5  # guardrail: refuse list-price changes above +/-50 % in one step


class PriceChange(Body):
    new_price: float = Field(..., gt=0, le=100000, description="New list price in BRL")
    reason: str | None = Field(None, max_length=300, description="Why the price changes")


def _listing(conn, pid: str) -> dict:
    row = conn.execute("SELECT * FROM ops_product_listings WHERE product_id = %(p)s", {"p": pid}).fetchone()
    if row is None:
        raise ApiError(409, "product_not_listed", "This product has no current listing (no sales in the last 6 "
                                                  "months), so it has no price or stock to change.")
    return row


@endpoint(router, "PATCH", "/api/products/{product_ref}/price", api_id="products.update_price", entity="product",
          title="Change a product's list price",
          description="Set a new list price for a listed product and record the change in the price history. "
                      "Changes larger than 50% in one step are refused.",
          data_layers=["synthetic"], returns="listing before/after and the price change record",
          examples=["raise the price of 1E9E8EF0 to 129.90", "cut the price of this product by 10%"])
def products_update_price(product_ref: str, body: PriceChange, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        pid = resolve_ref(conn, "raw_products", "product_id", product_ref, "product")
        old = float(_listing(conn, pid)["list_price"])
        new = round(body.new_price, 2)
        if new == old:
            raise ApiError(409, "nothing_to_change", f"The list price is already {old:.2f}.")
        if abs(new - old) / old > MAX_PRICE_CHANGE:
            raise ApiError(422, "price_change_too_large",
                           f"{old:.2f} -> {new:.2f} is a {100 * (new - old) / old:+.0f}% change; the limit is "
                           f"±{int(MAX_PRICE_CHANGE * 100)}% per change.", current_price=old)
        listing = ch.update("ops_product_listings", pid, {"list_price": new, "updated_at": sim_now()})
        rec = ch.insert("ops_price_changes", {"product_id": pid, "old_price": old, "new_price": new,
                                              "reason": body.reason, "created_at": sim_now(),
                                              "created_by": opts.actor})
        return {"product_id": pid, "old_price": old, "new_price": new, "change_pct": round(100 * (new - old) / old, 1),
                "price_change": rec["code"], "listing": listing}

    return run_write("products.update_price", opts, {"product_ref": product_ref, **body.model_dump()}, apply)


class RestockQuery(Paging):
    status: str | None = Field(None, description="placed, in_transit, received, canceled")
    product_id: str | None = Field(None, description="Product id or 8-char ref")
    open_only: bool = Field(False, description="Only placed / in_transit orders")


@endpoint(router, "GET", "/api/restock-orders", api_id="restock_orders.search", entity="restock_order",
          title="List restock orders",
          description="Purchase orders that replenish stock, with product, quantity, status and expected arrival.",
          data_layers=["synthetic"], returns="items[] of restock orders; total",
          examples=["which restock orders are still in transit", "restock orders for product 1E9E8EF0"])
def restock_search(q: Annotated[RestockQuery, Query()]):
    w = Where()
    with ro() as conn:
        if q.status:
            w.add("r.status = %(s)s", s=check_enum("restock_status", q.status, "status"))
        if q.open_only:
            w.add("r.status IN ('placed', 'in_transit')")
        if q.product_id:
            w.add("r.product_id = %(p)s", p=resolve_ref(conn, "raw_products", "product_id", q.product_id, "product"))
        total = conn.execute(f"SELECT count(*) AS n FROM ops_restock_orders r{w.sql()}", w.params).fetchone()["n"]
        rows = conn.execute(f"""
            SELECT r.code, r.product_id, upper(left(r.product_id, 8)) AS product_ref,
                   COALESCE(m.product_category_name_english, p.product_category_name, 'unknown') AS product_category,
                   r.quantity, r.status, r.created_at, r.expected_arrival, r.received_at, r.created_by, r.note
            FROM ops_restock_orders r JOIN raw_products p USING (product_id)
            LEFT JOIN core_category_map m ON m.product_category_name = p.product_category_name
            {w.sql()} ORDER BY r.id DESC LIMIT %(limit)s OFFSET %(offset)s""",
                            {**w.params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset, source(["ops_restock_orders"], ["synthetic"]))


class RestockCreate(Body):
    product_id: str = Field(..., description="Product id or 8-char ref")
    quantity: int | None = Field(None, ge=1, le=10000,
                                 description="Units to order; default = order_up_to - stock - already on order")
    note: str | None = Field(None, max_length=300)


def _create_restock(conn, ch: Changes, pid: str, quantity: int | None, note: str | None, actor: str) -> dict:
    l = _listing(conn, pid)
    if l["status"] != "active":
        raise ApiError(409, "listing_paused", "The listing is paused; activate it before restocking.")
    open_row = conn.execute("SELECT code FROM ops_restock_orders WHERE product_id = %(p)s AND status IN "
                            "('placed', 'in_transit') LIMIT 1", {"p": pid}).fetchone()
    if open_row:
        raise ApiError(409, "restock_already_open", f"{open_row['code']} is already on order for this product.",
                       existing=open_row["code"])
    qty = quantity or max(1, l["order_up_to"] - l["stock_quantity"])
    now = sim_now()
    return ch.insert("ops_restock_orders", {
        "product_id": pid, "quantity": qty, "status": "placed", "created_at": now,
        "expected_arrival": now.date() + timedelta(days=l["lead_time_days"]), "created_by": actor, "note": note})


@endpoint(router, "POST", "/api/restock-orders", api_id="restock_orders.create", entity="restock_order",
          title="Place a restock order",
          description="Order more stock for a listed product. Quantity defaults to refilling up to the product's "
                      "order-up-to level; arrival = today + the product's lead time. Refused if an order is already open.",
          data_layers=["synthetic"], returns="the new restock order (code RST-...)",
          examples=["restock product 1E9E8EF0", "order 20 more units of this product"])
def restock_create(body: RestockCreate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        pid = resolve_ref(conn, "raw_products", "product_id", body.product_id, "product")
        return _create_restock(conn, ch, pid, body.quantity, body.note, opts.actor)

    return run_write("restock_orders.create", opts, body.model_dump(), apply)


class RestockBulk(Body):
    product_ids: list[str] | None = Field(None, max_length=100, description="Products to restock (ids or refs)")
    from_alerts: bool = Field(False, description="Restock every product in the low-stock alerts instead")
    product_category: str | None = Field(None, description="With from_alerts: only this category")
    max_orders: int = Field(25, ge=1, le=100, description="With from_alerts: at most this many orders")
    note: str | None = Field(None, max_length=300)


@endpoint(router, "POST", "/api/restock-orders/bulk", api_id="restock_orders.bulk_create", entity="restock_order",
          title="Restock several products",
          description="Place restock orders for a list of products, or for every product in the low-stock alerts "
                      "(optionally one category). Products that already have an open order are skipped and reported.",
          data_layers=["synthetic"], returns="created[] restock orders and skipped[] with reasons",
          examples=["restock everything below its reorder point", "restock all low-stock toys"])
def restock_bulk(body: RestockBulk, opts: WriteOpts = Depends()):
    if not body.product_ids and not body.from_alerts:
        raise ApiError(422, "missing_products", "Give product_ids or set from_alerts=true.")

    def apply(conn, ch: Changes):
        if body.from_alerts:
            pq = ProductsQuery(limit=body.max_orders, needs_restock=True, product_category=body.product_category)
            pids = [r["product_id"] for r in search_products(conn, pq, default_sort="days_of_cover")[0]]
        else:
            pids = [resolve_ref(conn, "raw_products", "product_id", p, "product") for p in body.product_ids]
        created, skipped = [], []
        for pid in dict.fromkeys(pids):
            try:
                with conn.transaction():  # savepoint: one refused product does not undo the others
                    created.append(_create_restock(conn, ch, pid, None, body.note, opts.actor)["code"])
            except ApiError as e:
                skipped.append({"product_id": pid, "reason": e.code, "message": e.message})
        return {"created": created, "skipped": skipped}

    return run_write("restock_orders.bulk_create", opts, body.model_dump(), apply)


class RestockUpdate(Body):
    status: Literal["in_transit", "received", "canceled"] = Field(..., description="New status")
    note: str | None = Field(None, max_length=300)


RESTOCK_NEXT = {"placed": {"in_transit", "received", "canceled"}, "in_transit": {"received", "canceled"}}


@endpoint(router, "PATCH", "/api/restock-orders/{code}", api_id="restock_orders.update", entity="restock_order",
          title="Receive or cancel a restock order",
          description="Mark a restock order in transit, received (adds the quantity to stock) or canceled.",
          data_layers=["synthetic"], returns="restock order and listing before/after",
          examples=["cancel RST-000412", "mark RST-000412 as received"])
def restock_update(code: str, body: RestockUpdate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        r = resolve_code(conn, "ops_restock_orders", code, "restock order", "RST")
        if body.status not in RESTOCK_NEXT.get(r["status"], set()):
            raise ApiError(409, "invalid_transition", f"{r['code']} is {r['status']}; it cannot become {body.status}.")
        values = {"status": body.status}
        if body.note:
            values["note"] = body.note
        out = {}
        if body.status == "received":
            values["received_at"] = sim_now()
            l = _listing(conn, r["product_id"])
            out["listing"] = ch.update("ops_product_listings", r["product_id"],
                                       {"stock_quantity": l["stock_quantity"] + r["quantity"], "updated_at": sim_now()})
        out["restock_order"] = ch.update("ops_restock_orders", r["id"], values)
        return out

    return run_write("restock_orders.update", opts, {"code": code, **body.model_dump()}, apply)
