"""Curated operations pages (the 'Olist Seller Operations' console), bound to the catalogue APIs.

These are the screens a marketplace operations manager works in: orders, late deliveries, tickets,
reviews, stock, restocking, sellers, the acquisition funnel, plan-vs-actual, SLA, forecasts, promotions.
Each page carries `agent_context` (what it is for, what "this"/"here" refers to, typical next steps)
and each write the user can do is an Action bound to a catalogue API. The 100 generated report pages
stay as the 'Reports' library (generator/build_application.py).

Closed vocabularies below mirror the CHECK constraints in sql/20_synthetic_schema.sql and
backend/common.STATIC_ENUMS; tests/test_backend_api.py asserts they match.
"""
from __future__ import annotations

from typing import Callable

import psycopg

from contracts.metadata import (Action, ChartSpec, Column, Directory, FilterDef, Module, Page, RenderContract,
                                Widget, WidgetSource)

TICKET_STATUS = ["open", "in_progress", "resolved", "closed"]
TICKET_CATEGORY = ["late_delivery", "not_received", "damaged_item", "wrong_item", "product_quality",
                   "refund_request", "other"]
TICKET_PRIORITY = ["low", "medium", "high", "urgent"]
RESTOCK_STATUS = ["placed", "in_transit", "received", "canceled"]
FLAG_STATUS = ["open", "resolved", "dismissed"]
FLAG_SEVERITY = ["low", "medium", "high"]
PROMO_STATUS = ["scheduled", "active", "ended", "canceled"]
SCENARIOS = ["baseline", "optimistic", "conservative"]
YES_NO = ["true", "false"]
SCORES = ["1", "2", "3", "4", "5"]

OPS_MODULES = [
    ("ops_overview", "Overview", "Operations dashboard: key numbers, open tickets and stock alerts at a glance"),
    ("ops_orders", "Orders", "Customer orders, late and overdue deliveries"),
    ("ops_support", "Customer support", "Support tickets and customer reviews with seller replies"),
    ("ops_inventory", "Inventory", "Products, stock levels, low-stock alerts and restock orders"),
    ("ops_sellers", "Sellers", "Seller performance, performance flags and the seller acquisition funnel"),
    ("ops_planning", "Planning", "Sales targets, delivery SLA and revenue forecasts versus what happened"),
    ("ops_marketing", "Promotions", "Discount campaigns and how sales moved during them"),
]


# ------------------------------------------------------------------------------------ helpers
def _c(name, title, type="text", unit="", description=""):
    return Column(name=name, title=title, type=type, unit=unit, description=description)


def _money(name, title):
    return _c(name, title, "money", "BRL")


def _date_filter(preset: str) -> FilterDef:
    return FilterDef(filter_id="date_range", field="date", type="date_range", title="Date range",
                     allowed_ops=["between"])


def _select(fid: str, title: str, values: list[str], syn: dict | None = None, multi: bool = False) -> FilterDef:
    return FilterDef(filter_id=fid, field=fid, type="multiselect" if multi else "select", title=title,
                     allowed_ops=["eq", "in"] if multi else ["eq"], allowed_values=values, synonyms=syn or {})


def _act(action_id, api_id, label, params_from_row=None, fixed=None, form=False, style="default", description=""):
    return Action(action_id=action_id, api_id=api_id, label=label, params_from_row=params_from_row or {},
                  fixed=fixed or {}, form=form, style=style, description=description)


def _grid(wid, title, desc, api_id, columns, *, params=None, filters=None, date=None, actions=None, sort=True):
    return Widget(widget_id=wid, type="grid", title=title, description=desc,
                  supports=["sort", "filter"] + (["date_range"] if date else []) if sort else ["filter"],
                  render=RenderContract(query_metrics=[]),
                  source=WidgetSource(api_id=api_id, params=params or {}, filter_params=filters or {},
                                      date_params=date, columns=columns, row_actions=actions or []))


def _chart(kind, wid, title, desc, api_id, columns, x, y, *, params=None, filters=None, date=None):
    return Widget(widget_id=wid, type=kind, title=title, description=desc, supports=["filter"] + (["date_range"] if date else []),
                  render=RenderContract(query_metrics=[]),
                  source=WidgetSource(api_id=api_id, params=params or {}, filter_params=filters or {}, date_params=date,
                                      columns=columns, chart=ChartSpec(x=x, y=y), sort_param=None))


DATES = {"from": "date_from", "to": "date_to"}


# ------------------------------------------------------------------------------------ columns
ORDER_COLS = [
    _c("order_ref", "Order"), _c("order_status", "Status", "badge"), _c("purchased_at", "Purchased", "datetime"),
    _c("customer_state", "State"), _c("items", "Items", "number"), _money("items_value", "Value"),
    _money("freight", "Freight"), _c("days_late", "Days late", "number", description="days after the estimated date"),
    _c("days_overdue", "Days overdue", "number", description="undelivered days past the estimated date"),
    _c("review_score", "Review", "number"), _c("open_tickets", "Open tickets", "number"),
]
TICKET_COLS = [
    _c("code", "Ticket"), _c("order_ref", "Order"), _c("category", "Category", "badge"),
    _c("priority", "Priority", "badge"), _c("status", "Status", "badge"), _c("assignee", "Assignee"),
    _c("customer_state", "State"), _c("created_at", "Created", "datetime"),
    _c("age_hours", "Age (h)", "number"),
]
PRODUCT_COLS = [
    _c("product_ref", "Product"), _c("product_category", "Category"), _money("list_price", "Price"),
    _money("unit_cost", "Cost"), _c("margin_pct", "Margin %", "percent"), _c("stock_quantity", "Stock", "number"),
    _c("reorder_point", "Reorder at", "number"), _c("on_order_qty", "On order", "number"),
    _c("units_sold_90d", "Sold 90d", "number"), _c("days_of_cover", "Days of cover", "number"),
    _money("inventory_value", "Stock value"),
]
SELLER_COLS = [
    _c("seller_ref", "Seller"), _c("seller_state", "State"), _money("revenue", "Revenue"),
    _c("items_sold", "Items", "number"), _c("late_rate", "Late rate", "ratio"),
    _c("avg_delivery_days", "Delivery days", "number"), _c("avg_review_score", "Review score", "number"),
    _c("open_flags", "Open flags", "number"),
]


# ------------------------------------------------------------------------------------ builder
def build_ops(conn: psycopg.Connection, datasets: dict, synonyms: Callable[[str, list[str]], dict]):
    """-> (modules, directories, pages, widgets) for the operations section."""
    items = datasets["ds_order_items"]
    states = items.field("customer_state").values
    regions = items.field("customer_region").values
    categories = items.field("product_category").values
    statuses = items.field("order_status").values
    seller_states = items.field("seller_state").values
    origins = [r[0] for r in conn.execute("SELECT DISTINCT COALESCE(origin, 'unknown') FROM raw_marketing_qualified_leads ORDER BY 1")]
    segments = [r[0] for r in conn.execute("SELECT DISTINCT business_segment FROM raw_closed_deals WHERE business_segment IS NOT NULL ORDER BY 1")]
    f_state = lambda fid="customer_state", title="Customer state": _select(fid, title, states, synonyms("customer_state", states), True)  # noqa: E731
    f_cat = lambda: _select("product_category", "Product category", categories, synonyms("product_category", categories))  # noqa: E731

    modules = [Module(module_id=m, title=t, description=d, section="operations") for m, t, d in OPS_MODULES]
    directories = [Directory(path=m, title=t, description=d) for m, t, d in OPS_MODULES]
    pages: list[Page] = []
    widgets: list[Widget] = []

    def add(page: Page, ws: list[Widget]):
        pages.append(page)
        widgets.extend(ws)

    # ---- P-100 dashboard
    kpis = [
        Widget(widget_id="ops.dashboard.kpi_revenue", type="kpi_card", title="Revenue",
               description="Total revenue (item prices, excluding freight) of valid sales in the period.",
               dataset_id="ds_order_items", metrics=["revenue"], supports=["filter", "date_range"],
               render=RenderContract(query_metrics=["revenue"])),
        Widget(widget_id="ops.dashboard.kpi_orders", type="kpi_card", title="Orders",
               description="Number of orders with at least one item (valid sales) in the period.",
               dataset_id="ds_order_items", metrics=["orders"], supports=["filter", "date_range"],
               render=RenderContract(query_metrics=["orders"])),
        Widget(widget_id="ops.dashboard.kpi_late", type="kpi_card", title="Late order rate",
               description="Share of delivered orders that arrived after the estimated date.",
               dataset_id="ds_orders", metrics=["order_late_rate"], supports=["filter", "date_range"],
               render=RenderContract(query_metrics=["order_late_rate"])),
        Widget(widget_id="ops.dashboard.kpi_review", type="kpi_card", title="Average review score",
               description="Average customer review score (1 to 5) of orders in the period.",
               dataset_id="ds_reviews", metrics=["avg_review_score"], supports=["filter", "date_range"],
               render=RenderContract(query_metrics=["avg_review_score"])),
    ]
    trend = Widget(widget_id="ops.dashboard.trend", type="line_chart", title="Revenue by month",
                   description="Monthly revenue for the selected period.", dataset_id="ds_order_items",
                   metrics=["revenue"], dimensions=["order_month"], supports=["filter", "date_range"],
                   render=RenderContract(query_metrics=["revenue"], query_dimensions=["order_month"]))
    open_tickets = _grid("ops.dashboard.open_tickets", "Open tickets, newest first",
                         "The latest open or in-progress customer support tickets.", "tickets.search",
                         [TICKET_COLS[i] for i in (0, 1, 2, 3, 4, 7)],
                         params={"open_only": True, "limit": 8}, sort=False,
                         actions=[_act("resolve", "tickets.update", "Resolve", {"code": "code"}, {"status": "resolved"})])
    alerts = _grid("ops.dashboard.low_stock", "Products to restock",
                   "Products at or below their reorder point with nothing on order, most urgent first.",
                   "inventory.alerts", [PRODUCT_COLS[i] for i in (0, 5, 6, 8, 9)],
                   params={"limit": 8}, sort=False,
                   actions=[_act("restock", "restock_orders.create", "Restock", {"product_id": "product_ref"})])
    add(Page(page_id="ops.dashboard", page_code="P-100", kind="operations", title="Operations dashboard",
             description="Operations dashboard: revenue, orders, late delivery rate and review score for the period, "
                         "the monthly revenue trend, open support tickets and products that need restocking.",
             agent_context="Landing page. 'This'/'today' means the dashboard period (default last 12 months to "
                           "2018-08-31). The ticket and stock tables show live operational records, not the period. "
                           "Typical next steps: open the late-orders page, resolve tickets, restock products.",
             directory="ops_overview", route="/ops/dashboard",
             keywords=["dashboard", "home", "overview", "kpi", "summary", "operations"],
             widgets=[w.widget_id for w in kpis + [trend, open_tickets, alerts]],
             layout=[[w.widget_id for w in kpis], [trend.widget_id], [open_tickets.widget_id, alerts.widget_id]],
             filters=[_date_filter("last_12_months")], default_state={"date_range": {"preset": "last_12_months"}}),
        kpis + [trend, open_tickets, alerts])

    # ---- P-200 orders
    order_filters = {"order_status": "status", "customer_state": "customer_state", "product_category": "product_category",
                     "late": "late", "has_open_ticket": "has_open_ticket"}
    orders = _grid("ops.orders.grid", "Orders",
                   "Customer orders with status, value, delivery lateness, review and open tickets.",
                   "orders.search", ORDER_COLS, params={"limit": 50}, filters=order_filters, date=DATES,
                   actions=[_act("ticket", "tickets.create", "Open ticket", {"order_id": "order_ref"}, form=True,
                                 description="Open a support ticket for this order")])
    add(Page(page_id="ops.orders", page_code="P-200", kind="operations", title="Orders",
             description="Search customer orders by status, state, product category, purchase date, lateness and "
                         "open tickets; see value, freight, days late and review score; open a support ticket.",
             agent_context="'This order' means the selected row or the order the user names (8-character ref like "
                           "E481F51C). Orders are real Olist data; tickets are simulated. Typical next steps: filter "
                           "late orders, open a ticket for an order, check the seller.",
             directory="ops_orders", route="/ops/orders", keywords=["orders", "purchases", "search orders", "customers"],
             widgets=[orders.widget_id], layout=[[orders.widget_id]],
             filters=[_date_filter("last_month"), _select("order_status", "Status", statuses),
                      f_state(), f_cat(), _select("late", "Delivered late", YES_NO),
                      _select("has_open_ticket", "Has open ticket", YES_NO)],
             default_state={"date_range": {"preset": "last_month"}}), [orders])

    # ---- P-210 late and overdue
    late = _grid("ops.late.late", "Delivered late",
                 "Delivered orders that arrived after the estimated date, longest delay first.", "orders.search",
                 ORDER_COLS, params={"late": True, "sort": "-days_late", "limit": 50},
                 filters={"customer_state": "customer_state", "product_category": "product_category",
                          "has_open_ticket": "has_open_ticket"}, date=DATES,
                 actions=[_act("ticket", "tickets.create", "Open ticket", {"order_id": "order_ref"},
                               {"category": "late_delivery"}, form=True)])
    overdue = _grid("ops.late.overdue", "Not delivered yet and overdue",
                    "Orders not delivered and already past the estimated date, longest overdue first.", "orders.search",
                    ORDER_COLS, params={"overdue": True, "sort": "-days_overdue", "limit": 50},
                    filters={"customer_state": "customer_state", "product_category": "product_category",
                             "has_open_ticket": "has_open_ticket"}, date=DATES,
                    actions=[_act("ticket_overdue", "tickets.create", "Open ticket", {"order_id": "order_ref"},
                                  {"category": "not_received"}, form=True)])
    add(Page(page_id="ops.late_deliveries", page_code="P-210", kind="operations", title="Late and overdue deliveries",
             description="Orders delivered after the estimated date and orders still undelivered past it, with days "
                         "late, customer state, review score and open tickets; open tickets for them.",
             agent_context="Two tables: delivered-late and still-overdue. 'The late ones' means the delivered-late "
                           "table unless the user says undelivered. Typical request: open tickets for every late order "
                           "in a state (use the bulk ticket API), then check which sellers are responsible.",
             directory="ops_orders", route="/ops/orders/late", keywords=["late", "delayed", "overdue", "delivery", "sla"],
             widgets=[late.widget_id, overdue.widget_id], layout=[[late.widget_id], [overdue.widget_id]],
             filters=[_date_filter("last_month"), f_state(), f_cat(), _select("has_open_ticket", "Has open ticket", YES_NO)],
             default_state={"date_range": {"preset": "last_month"}}), [late, overdue])

    # ---- P-300 tickets
    tickets = _grid("ops.tickets.grid", "Support tickets", "Customer support tickets with category, priority, status, "
                    "assignee and age.", "tickets.search", TICKET_COLS, params={"limit": 50},
                    filters={"status": "status", "priority": "priority", "category": "category",
                             "customer_state": "customer_state"}, date=DATES,
                    actions=[_act("resolve", "tickets.update", "Resolve", {"code": "code"}, {"status": "resolved"}),
                             _act("escalate", "tickets.update", "Make urgent", {"code": "code"}, {"priority": "urgent"}),
                             _act("assign", "tickets.update", "Assign", {"code": "code"}, form=True)])
    add(Page(page_id="ops.tickets", page_code="P-300", kind="operations", title="Support tickets",
             description="Customer support tickets: filter by status, priority, category and state; resolve, assign or "
                         "escalate them; open a new ticket for an order.",
             agent_context="'This ticket' means the selected row or a code like TCK-005631. All tickets are simulated "
                           "records linked to real orders. Status flow: open -> in_progress -> resolved -> closed.",
             directory="ops_support", route="/ops/support/tickets", keywords=["tickets", "support", "complaints", "cases"],
             widgets=[tickets.widget_id], layout=[[tickets.widget_id]],
             filters=[_date_filter("last_30_days"), _select("status", "Status", TICKET_STATUS),
                      _select("priority", "Priority", TICKET_PRIORITY), _select("category", "Category", TICKET_CATEGORY),
                      f_state()],
             actions=[_act("new_ticket", "tickets.create", "New ticket", form=True)],
             default_state={"date_range": {"preset": "last_30_days"}}), [tickets])

    # ---- P-310 reviews
    reviews = _grid("ops.reviews.grid", "Customer reviews",
                    "Customer reviews with score, comment, state and the seller's reply.", "reviews.search",
                    [_c("order_ref", "Order"), _c("review_score", "Score", "number"),
                     _c("review_comment_title", "Title"), _c("review_comment_message", "Comment"),
                     _c("created_at", "Created", "datetime"), _c("customer_state", "State"),
                     Column(name="review_id", title="Review id", hidden=True),
                     Column(name="order_id", title="Order id", hidden=True)],
                    params={"limit": 50}, filters={"review_score_max": "score_max", "customer_state": "customer_state",
                                                    "has_reply": "has_reply"}, date=DATES,
                    actions=[_act("reply", "reviews.reply", "Reply", {"review_id": "review_id", "order_id": "order_id"},
                                  form=True)])
    add(Page(page_id="ops.reviews", page_code="P-310", kind="operations", title="Customer reviews",
             description="Customer reviews with score and comment; find negative reviews without a reply and reply to them.",
             agent_context="'This review' means the selected row. Reviews are real Olist data (text is Portuguese); "
                           "replies are simulated. Typical request: reply to unanswered 1-2 star reviews.",
             directory="ops_support", route="/ops/support/reviews", keywords=["reviews", "ratings", "feedback", "complaints"],
             widgets=[reviews.widget_id], layout=[[reviews.widget_id]],
             filters=[_date_filter("last_30_days"), _select("review_score_max", "Score at most", SCORES), f_state(),
                      _select("has_reply", "Has reply", YES_NO)],
             default_state={"date_range": {"preset": "last_30_days"}}), [reviews])

    # ---- P-400 products
    products = _grid("ops.products.grid", "Products and stock",
                     "Products with price, cost, margin, stock, reorder point, quantity on order, units sold in the "
                     "last 90 days, days of cover and stock value.", "products.search", PRODUCT_COLS,
                     params={"listed": True, "limit": 50},
                     filters={"product_category": "product_category", "below_reorder_point": "below_reorder_point"},
                     actions=[_act("restock", "restock_orders.create", "Restock", {"product_id": "product_ref"}, form=True),
                              _act("price", "products.update_price", "Change price", {"product_ref": "product_ref"}, form=True)])
    add(Page(page_id="ops.products", page_code="P-400", kind="operations", title="Products and stock",
             description="Listed products with price, cost, margin, stock level, units sold and days of cover; "
                         "restock a product or change its price.",
             agent_context="'This product' means the selected row or a product ref like 1E9E8EF0. Sales are real; price, "
                           "cost and stock are simulated. Price changes above +/-50% are refused.",
             directory="ops_inventory", route="/ops/inventory/products", keywords=["products", "stock", "inventory", "price", "catalog"],
             widgets=[products.widget_id], layout=[[products.widget_id]],
             filters=[f_cat(), _select("below_reorder_point", "Below reorder point", YES_NO)]), [products])

    # ---- P-410 alerts
    alerts_grid = _grid("ops.alerts.grid", "Products to restock",
                        "Products at or below their reorder point and not on order, fewest days of cover first, with a "
                        "suggested order quantity.", "inventory.alerts",
                        PRODUCT_COLS[:2] + PRODUCT_COLS[5:10] + [_c("suggested_quantity", "Suggested qty", "number")],
                        params={"limit": 100}, filters={"product_category": "product_category"},
                        actions=[_act("restock", "restock_orders.create", "Restock", {"product_id": "product_ref"})])
    stock_trend = _chart("line_chart", "ops.alerts.trend", "Inventory value over time",
                         "Month-end inventory value at cost and number of products below their reorder point.",
                         "inventory.snapshots",
                         [_c("snapshot_date", "Month end", "date"), _money("inventory_value", "Inventory value"),
                          _c("products_below_reorder_point", "Below reorder point", "number")],
                         "snapshot_date", ["inventory_value"], filters={"product_category": "product_category"})
    add(Page(page_id="ops.low_stock", page_code="P-410", kind="operations", title="Low-stock alerts",
             description="Products that need restocking now, most urgent first, with suggested quantities, and how "
                         "inventory value and low-stock counts moved month by month.",
             agent_context="'Low stock' means stock at or below the reorder point with nothing on order. 'Restock them' "
                           "means place restock orders for the alert list (bulk restock API with from_alerts). Simulated data.",
             directory="ops_inventory", route="/ops/inventory/alerts", keywords=["low stock", "alerts", "restock", "reorder"],
             widgets=[alerts_grid.widget_id, stock_trend.widget_id],
             layout=[[stock_trend.widget_id], [alerts_grid.widget_id]], filters=[f_cat()],
             actions=[_act("restock_all", "restock_orders.bulk_create", "Restock all alerts", fixed={"from_alerts": True})]),
        [alerts_grid, stock_trend])

    # ---- P-420 restock orders
    restocks = _grid("ops.restock.grid", "Restock orders", "Purchase orders that replenish stock, with product, "
                     "quantity, status and expected arrival.", "restock_orders.search",
                     [_c("code", "Order"), _c("product_ref", "Product"), _c("product_category", "Category"),
                      _c("quantity", "Qty", "number"), _c("status", "Status", "badge"),
                      _c("created_at", "Placed", "datetime"), _c("expected_arrival", "Expected", "date"),
                      _c("received_at", "Received", "datetime")],
                     params={"limit": 50}, filters={"status": "status"}, sort=False,
                     actions=[_act("receive", "restock_orders.update", "Mark received", {"code": "code"}, {"status": "received"}),
                              _act("cancel", "restock_orders.update", "Cancel", {"code": "code"}, {"status": "canceled"}, style="danger")])
    add(Page(page_id="ops.restock_orders", page_code="P-420", kind="operations", title="Restock orders",
             description="Restock purchase orders and their status; mark them received (adds stock) or cancel them.",
             agent_context="'This order' on this page means a restock order (code RST-000123), not a customer order. Simulated.",
             directory="ops_inventory", route="/ops/inventory/restock-orders", keywords=["restock", "purchase orders", "replenishment"],
             widgets=[restocks.widget_id], layout=[[restocks.widget_id]],
             filters=[_select("status", "Status", RESTOCK_STATUS)]), [restocks])

    # ---- P-500 sellers
    sellers = _grid("ops.sellers.grid", "Sellers", "Sellers with revenue, items sold, late rate, delivery days, "
                    "review score and open performance flags.", "sellers.search", SELLER_COLS, params={"limit": 50},
                    filters={"seller_state": "seller_state", "flagged": "flagged"}, date=DATES,
                    actions=[_act("flag", "sellers.flag", "Flag seller", {"seller_ref": "seller_ref"}, form=True)])
    add(Page(page_id="ops.sellers", page_code="P-500", kind="operations", title="Sellers",
             description="Seller performance ranking: revenue, late delivery rate, delivery days, review score and "
                         "flags; flag a seller for poor performance.",
             agent_context="'This seller' means the selected row or a seller ref like 3442F895. Performance is real Olist "
                           "data for the chosen period; flags are simulated.",
             directory="ops_sellers", route="/ops/sellers", keywords=["sellers", "merchants", "vendors", "performance"],
             widgets=[sellers.widget_id], layout=[[sellers.widget_id]],
             filters=[_date_filter("last_12_months"), _select("seller_state", "Seller state", seller_states, multi=True),
                      _select("flagged", "Has open flag", YES_NO)],
             default_state={"date_range": {"preset": "last_12_months"}}), [sellers])

    # ---- P-510 flags
    flags = _grid("ops.flags.grid", "Seller flags", "Performance flags on sellers with reason, severity, status and evidence.",
                  "seller_flags.search",
                  [_c("code", "Flag"), _c("seller_ref", "Seller"), _c("seller_state", "State"), _c("reason", "Reason", "badge"),
                   _c("severity", "Severity", "badge"), _c("status", "Status", "badge"), _c("note", "Evidence"),
                   _c("created_at", "Raised", "datetime")],
                  params={"limit": 50}, filters={"status": "status", "severity": "severity"}, sort=False,
                  actions=[_act("resolve", "seller_flags.update", "Resolve", {"code": "code"}, {"status": "resolved"}),
                           _act("dismiss", "seller_flags.update", "Dismiss", {"code": "code"}, {"status": "dismissed"})])
    add(Page(page_id="ops.seller_flags", page_code="P-510", kind="operations", title="Seller flags",
             description="Open and closed performance flags on sellers; resolve or dismiss them.",
             agent_context="'This flag' means the selected row or a code like FLG-00012. Flags are simulated, raised by rules "
                           "over real seller performance (late rate above 20% or review score below 3.0).",
             directory="ops_sellers", route="/ops/sellers/flags", keywords=["flags", "seller issues", "violations"],
             widgets=[flags.widget_id], layout=[[flags.widget_id]],
             filters=[_select("status", "Status", FLAG_STATUS), _select("severity", "Severity", FLAG_SEVERITY)],
             default_state={}), [flags])

    # ---- P-520 funnel
    funnel_chart = _chart("bar_chart", "ops.funnel.chart", "Leads and won deals by channel",
                          "Marketing leads and closed deals (new sellers) per acquisition channel.", "funnel.channels",
                          [_c("origin", "Channel"), _c("leads", "Leads", "number"), _c("won_deals", "Won deals", "number")],
                          "origin", ["leads", "won_deals"], filters={"origin": "origin"}, date=DATES)
    funnel_grid = _grid("ops.funnel.channels", "Funnel by channel", "Per channel: leads, won deals, conversion rate, active "
                        "sellers and the revenue the won sellers made afterwards.", "funnel.channels",
                        [_c("origin", "Channel"), _c("leads", "Leads", "number"), _c("won_deals", "Won", "number"),
                         _c("conversion_pct", "Conversion %", "percent"), _c("active_sellers", "Selling", "number"),
                         _money("seller_revenue", "Seller revenue")],
                        filters={"origin": "origin"}, date=DATES)
    deals = _grid("ops.funnel.deals", "Closed deals (new sellers)", "Leads that became sellers, with segment, declared "
                  "revenue and the revenue they made after joining.", "funnel.deals",
                  [_c("seller_ref", "Seller"), _c("won_date", "Won", "date"), _c("business_segment", "Segment"),
                   _c("origin", "Channel"), _c("business_type", "Type"), _money("declared_monthly_revenue", "Declared / month"),
                   _money("revenue_since_won", "Revenue since won")],
                  params={"limit": 50}, filters={"origin": "origin", "business_segment": "business_segment"})
    add(Page(page_id="ops.funnel", page_code="P-520", kind="operations", title="Seller acquisition funnel",
             description="Where new sellers come from: leads, closed deals and conversion by marketing channel, the "
                         "list of won sellers, and how much they sold after joining.",
             agent_context="Real Olist marketing-funnel data (leads June 2017 - June 2018). 'Unknown' channel = missing origin "
                           "in the source. Seller revenue counts from the day the deal was won.",
             directory="ops_sellers", route="/ops/sellers/funnel", keywords=["funnel", "leads", "acquisition", "channels", "conversion", "new sellers"],
             widgets=[funnel_chart.widget_id, funnel_grid.widget_id, deals.widget_id],
             layout=[[funnel_chart.widget_id, funnel_grid.widget_id], [deals.widget_id]],
             filters=[_date_filter("year:2018"), _select("origin", "Channel", origins),
                      _select("business_segment", "Segment", segments)],
             default_state={}), [funnel_chart, funnel_grid, deals])

    # ---- P-600 targets
    t_cols = [_c("customer_state", "State"), _money("revenue_actual", "Actual"), _money("revenue_target", "Target"),
              _c("attainment_pct", "Attainment %", "percent"), _money("variance", "Variance"),
              _c("orders_actual", "Orders", "number"), _c("order_target", "Order target", "number")]
    t_filters = {"customer_state": "customer_state", "below_target": "below_target"}
    t_chart = _chart("bar_chart", "ops.targets.chart", "Actual vs target revenue by state",
                     "Actual revenue next to the simulated revenue target for each state.", "targets.performance", t_cols,
                     "customer_state", ["revenue_actual", "revenue_target"], params={"limit": 27, "sort": "-revenue_target"},
                     filters=t_filters, date=DATES)
    t_grid = _grid("ops.targets.grid", "Sales vs target by state", "Revenue and orders versus target per state with "
                   "attainment percent and variance, worst attainment first.", "targets.performance", t_cols,
                   params={"limit": 27}, filters=t_filters, date=DATES)
    add(Page(page_id="ops.targets", page_code="P-600", kind="operations", title="Sales vs target",
             description="Actual revenue and orders against the monthly sales targets per state: which states missed or "
                         "beat their targets and by how much.",
             agent_context="Targets are SIMULATED plans (3-month run-rate plus 8% growth), not real company targets; actuals "
                           "are real. Small states have noisy attainment; rank by variance for the biggest absolute misses.",
             directory="ops_planning", route="/ops/planning/targets", keywords=["targets", "goals", "plan", "attainment", "missed target"],
             widgets=[t_chart.widget_id, t_grid.widget_id], layout=[[t_chart.widget_id], [t_grid.widget_id]],
             filters=[_date_filter("last_quarter"), f_state(), _select("below_target", "Only missed targets", YES_NO)],
             default_state={"date_range": {"preset": "last_quarter"}}), [t_chart, t_grid])

    # ---- P-610 SLA
    s_cols = [_c("customer_state", "State"), _c("delivered_orders", "Delivered", "number"), _c("breaches", "Breaches", "number"),
              _c("breach_rate_pct", "SLA breach %", "percent"), _c("olist_late_rate_pct", "Olist late %", "percent",
                                                                  description="delivered after Olist's own estimated date"),
              _c("avg_delivery_days", "Avg days", "number"), _c("sla_threshold_days", "SLA days", "number")]
    s_chart = _chart("bar_chart", "ops.sla.chart", "SLA breach rate by state", "Share of deliveries slower than the "
                     "simulated delivery SLA, per destination state.", "sla.performance", s_cols, "customer_state",
                     ["breach_rate_pct"], params={"limit": 27}, filters={"customer_region": "customer_region"}, date=DATES)
    s_grid = _grid("ops.sla.grid", "Delivery SLA by state", "Deliveries, SLA breaches, breach rate and Olist's own late rate "
                   "per destination state.", "sla.performance", s_cols, params={"limit": 27},
                   filters={"customer_region": "customer_region"}, date=DATES)
    add(Page(page_id="ops.sla", page_code="P-610", kind="operations", title="Delivery SLA",
             description="How many deliveries were slower than the delivery SLA, by destination state, next to "
                         "Olist's own late-delivery rate.",
             agent_context="The SLA is SIMULATED (set at the 80th percentile of 2017 delivery times, one day tighter from "
                           "2018). 'Late' (Olist estimated date) and 'SLA breach' are different measures; say which one you use.",
             directory="ops_planning", route="/ops/planning/sla", keywords=["sla", "service level", "delivery breaches", "delivery performance"],
             widgets=[s_chart.widget_id, s_grid.widget_id], layout=[[s_chart.widget_id], [s_grid.widget_id]],
             filters=[_date_filter("last_12_months"), _select("customer_region", "Region", regions, synonyms("customer_region", regions))],
             default_state={"date_range": {"preset": "last_12_months"}}), [s_chart, s_grid])

    # ---- P-620 forecast
    f_cols = [_c("month", "Month", "date"), _money("predicted_revenue", "Forecast"), _money("actual_revenue", "Actual"),
              _c("error_pct", "Error %", "percent", description="positive = forecast too high")]
    f_chart = _chart("line_chart", "ops.forecast.chart", "Forecast vs actual revenue", "Monthly revenue forecast next to "
                     "actual revenue; months after August 2018 have a forecast only.", "forecasts.comparison", f_cols,
                     "month", ["predicted_revenue", "actual_revenue"], params={"group_by": "month"},
                     filters={"scenario": "scenario", "customer_state": "customer_state"})
    f_grid = _grid("ops.forecast.grid", "Forecast accuracy by month", "Forecast, actual and forecast error per month.",
                   "forecasts.comparison", f_cols, params={"group_by": "month"},
                   filters={"scenario": "scenario", "customer_state": "customer_state"}, sort=False)
    add(Page(page_id="ops.forecast", page_code="P-620", kind="operations", title="Revenue forecast",
             description="Revenue forecast versus actual revenue by month for the baseline, optimistic and conservative "
                         "scenarios, and forecast error.",
             agent_context="Forecasts are model estimates (linear trend on trailing 6 months), not sales. Jan-Aug 2018 are "
                           "backtests that can be checked against actuals; Sep-Dec 2018 are future.",
             directory="ops_planning", route="/ops/planning/forecast", keywords=["forecast", "prediction", "projection", "outlook"],
             widgets=[f_chart.widget_id, f_grid.widget_id], layout=[[f_chart.widget_id], [f_grid.widget_id]],
             filters=[_select("scenario", "Scenario", SCENARIOS), f_state()],
             default_state={}), [f_chart, f_grid])

    # ---- P-700 promotions
    promos = _grid("ops.promotions.grid", "Promotions", "Discount campaigns with category, state, discount, dates and status.",
                   "promotions.search",
                   [_c("code", "Promotion"), _c("name", "Name"), _c("product_category", "Category"), _c("customer_state", "State"),
                    _c("discount_pct", "Discount %", "percent"), _c("start_date", "Starts", "date"), _c("end_date", "Ends", "date"),
                    _c("status", "Status", "badge")],
                   params={"limit": 50}, filters={"status": "status", "product_category": "product_category"},
                   sort=False,
                   actions=[_act("cancel", "promotions.update", "Cancel", {"code": "code"}, {"cancel": True}, style="danger"),
                            _act("extend", "promotions.update", "Change", {"code": "code"}, form=True)])
    add(Page(page_id="ops.promotions", page_code="P-700", kind="operations", title="Promotions",
             description="Discount campaigns by category and state with status; create, change or cancel a promotion.",
             agent_context="'This promotion' means the selected row or a code like PRM-0013. Promotions are simulated and did "
                           "not cause observed sales; the performance API compares sales before vs during.",
             directory="ops_marketing", route="/ops/promotions", keywords=["promotions", "discounts", "campaigns", "offers"],
             widgets=[promos.widget_id], layout=[[promos.widget_id]],
             filters=[_select("status", "Status", PROMO_STATUS), f_cat()],
             actions=[_act("new_promotion", "promotions.create", "New promotion", form=True)]), [promos])

    from generator.ops_visuals import enrich

    enrich(pages, widgets)  # KPI strips, donuts, bars and lines next to the tables
    return modules, directories, pages, widgets
