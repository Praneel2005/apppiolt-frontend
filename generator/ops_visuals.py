"""KPI strips and charts for the curated operations pages.

Each operations page gets headline numbers (kpi_strip), distributions (donuts, bars) and trends (lines) in addition to
its table, all bound to the catalogue APIs so the page filters drive them too. The same APIs answer the assistant's
"how many ... by ..." questions, so what the user sees and what the agent can cite are the same numbers.
"""
from __future__ import annotations

from contracts.metadata import ChartSpec, Column, Page, RenderContract, Widget, WidgetSource

DATES = {"from": "date_from", "to": "date_to"}


def C(name: str, title: str, type: str = "text") -> Column:
    return Column(name=name, title=title, type=type, unit="BRL" if type == "money" else "", description="")


def _filters(page: Page, wanted: dict[str, str]) -> dict[str, str]:
    have = {f.filter_id for f in page.filters}
    return {k: v for k, v in wanted.items() if k in have}


def _src(page: Page, api_id: str, columns: list[Column], filters: dict[str, str], *, params=None, date=True,
         chart: ChartSpec | None = None) -> WidgetSource:
    has_date = date and any(f.type == "date_range" for f in page.filters)
    return WidgetSource(api_id=api_id, params=params or {}, filter_params=_filters(page, filters),
                        date_params=DATES if has_date else None, columns=columns, chart=chart, sort_param=None)


def strip(page: Page, wid: str, title: str, desc: str, api_id: str, columns: list[Column], filters: dict[str, str],
          params: dict | None = None, date: bool = True) -> Widget:
    s = _src(page, api_id, columns, filters, params=params, date=date)
    return Widget(widget_id=wid, type="kpi_strip", title=title, description=desc,
                  supports=["filter"] + (["date_range"] if s.date_params else []), render=RenderContract(query_metrics=[]), source=s)


def chart(page: Page, kind: str, wid: str, title: str, desc: str, api_id: str, columns: list[Column], x: str, y: list[str],
          filters: dict[str, str], params: dict | None = None, date: bool = True) -> Widget:
    s = _src(page, api_id, columns, filters, params=params, date=date, chart=ChartSpec(x=x, y=y))
    return Widget(widget_id=wid, type=kind, title=title, description=desc,
                  supports=["filter"] + (["date_range"] if s.date_params else []), render=RenderContract(query_metrics=[]), source=s)


def metric_chart(kind: str, wid: str, title: str, desc: str, dataset_id: str, metric: str, dim: str) -> Widget:
    return Widget(widget_id=wid, type=kind, title=title, description=desc, dataset_id=dataset_id, metrics=[metric],
                  dimensions=[dim], supports=["filter", "date_range"],
                  render=RenderContract(query_metrics=[metric], query_dimensions=[dim]))


def enrich(pages: list[Page], widgets: list[Widget]) -> None:
    """Add the visuals to the pages (in place) and append the new widgets to `widgets`."""
    P = {p.page_id: p for p in pages}
    new: list[Widget] = []

    def put(pid: str, ws: list[Widget], layout: list[list[str]]) -> None:
        p = P[pid]
        new.extend(ws)
        ids = [i for row in layout for i in row]
        assert sorted(ids) == sorted(p.widgets + [w.widget_id for w in ws]), f"{pid}: layout must place each widget once"
        p.widgets, p.layout = ids, layout

    # ---------------------------------------------------------------- dashboard
    p = P["ops.dashboard"]
    region = metric_chart("pie_chart", "ops.dashboard.region_share", "Revenue share by region",
                          "Share of revenue earned in each customer macro-region for the selected period.",
                          "ds_order_items", "revenue", "customer_region")
    late_reg = metric_chart("bar_chart", "ops.dashboard.late_by_region", "Late order rate by region",
                            "Share of delivered orders that arrived late in each customer macro-region.",
                            "ds_orders", "order_late_rate", "customer_region")
    tick = chart(p, "pie_chart", "ops.dashboard.tickets_status", "Tickets by status",
                 "How the support tickets of the period are split across open, in progress, resolved and closed.",
                 "tickets.summary", [C("status", "Status", "badge"), C("tickets", "Tickets", "number")], "status", ["tickets"],
                 {}, {"group_by": "status"})
    put("ops.dashboard", [region, late_reg, tick], [
        ["ops.dashboard.kpi_revenue", "ops.dashboard.kpi_orders", "ops.dashboard.kpi_late", "ops.dashboard.kpi_review"],
        ["ops.dashboard.trend", region.widget_id], [late_reg.widget_id, tick.widget_id],
        ["ops.dashboard.open_tickets", "ops.dashboard.low_stock"]])

    # ---------------------------------------------------------------- orders
    p = P["ops.orders"]
    f = {"order_status": "status", "customer_state": "customer_state", "product_category": "product_category",
         "late": "late", "has_open_ticket": "has_open_ticket"}
    st = strip(p, "ops.orders.strip", "Orders at a glance", "Totals for the filtered orders: count, value, late share, "
               "average delay, overdue orders and average review score.", "orders.summary",
               [C("orders", "Orders", "number"), C("items_value", "Order value", "money"), C("late_rate", "Late rate", "ratio"),
                C("avg_days_late", "Avg days late", "number"), C("overdue_orders", "Overdue now", "number"),
                C("avg_review_score", "Avg review", "number")], f)
    by_status = chart(p, "pie_chart", "ops.orders.by_status", "Orders by status", "How the filtered orders split by order status.",
                      "orders.summary", [C("status", "Status", "badge"), C("orders", "Orders", "number")], "status", ["orders"], f,
                      {"group_by": "status"})
    trend = chart(p, "line_chart", "ops.orders.trend", "Orders per month", "Number of filtered orders placed each month.",
                  "orders.summary", [C("month", "Month", "date"), C("orders", "Orders", "number")], "month", ["orders"], f,
                  {"group_by": "month"})
    by_state = chart(p, "bar_chart", "ops.orders.by_state", "Orders by customer state", "Which states the filtered orders come from.",
                     "orders.summary", [C("customer_state", "State"), C("orders", "Orders", "number")], "customer_state", ["orders"], f,
                     {"group_by": "customer_state"})
    put("ops.orders", [st, by_status, trend, by_state], [[st.widget_id], [by_status.widget_id, trend.widget_id],
                                                         [by_state.widget_id], ["ops.orders.grid"]])

    # ---------------------------------------------------------------- late and overdue
    p = P["ops.late_deliveries"]
    f = {"customer_state": "customer_state", "product_category": "product_category", "has_open_ticket": "has_open_ticket"}
    st = strip(p, "ops.late.strip", "Delivery problems at a glance", "Late orders, late rate, average delay and orders "
               "currently overdue for the filtered period.", "orders.summary",
               [C("late_orders", "Delivered late", "number"), C("late_rate", "Late rate", "ratio"),
                C("avg_days_late", "Avg days late", "number"), C("overdue_orders", "Overdue now", "number")], f)
    by_state = chart(p, "bar_chart", "ops.late.by_state", "Late orders by state", "Which customer states receive the most late deliveries.",
                     "orders.summary", [C("customer_state", "State"), C("late_orders", "Late orders", "number")], "customer_state",
                     ["late_orders"], f, {"group_by": "customer_state", "late": True})
    bucket = chart(p, "bar_chart", "ops.late.buckets", "How late are late orders", "Late deliveries by number of days after the estimated date.",
                   "orders.summary", [C("days_late_bucket", "Delay"), C("orders", "Orders", "number")], "days_late_bucket", ["orders"], f,
                   {"group_by": "days_late_bucket", "late": True})
    put("ops.late_deliveries", [st, by_state, bucket], [[st.widget_id], [by_state.widget_id, bucket.widget_id],
                                                        ["ops.late.late"], ["ops.late.overdue"]])

    # ---------------------------------------------------------------- tickets
    p = P["ops.tickets"]
    f = {"status": "status", "priority": "priority", "category": "category", "customer_state": "customer_state"}
    st = strip(p, "ops.tickets.strip", "Support at a glance", "Tickets in the period: total, open, urgent, unassigned and "
               "average resolution time.", "tickets.summary",
               [C("tickets", "Tickets", "number"), C("open_tickets", "Open", "number"), C("urgent_tickets", "Urgent", "number"),
                C("unassigned", "Unassigned (open)", "number"), C("avg_resolution_hours", "Avg resolution (h)", "number")], f)
    by_status = chart(p, "pie_chart", "ops.tickets.by_status", "Tickets by status", "Share of tickets that are open, in progress, resolved or closed.",
                      "tickets.summary", [C("status", "Status", "badge"), C("tickets", "Tickets", "number")], "status", ["tickets"], f,
                      {"group_by": "status"})
    by_cat = chart(p, "bar_chart", "ops.tickets.by_category", "Tickets by category", "What customers contact support about.",
                   "tickets.summary", [C("category", "Category", "badge"), C("tickets", "Tickets", "number")], "category", ["tickets"], f,
                   {"group_by": "category"})
    per_day = chart(p, "line_chart", "ops.tickets.per_day", "Tickets created per day", "Daily ticket volume for the selected period.",
                    "tickets.summary", [C("day", "Day", "date"), C("tickets", "Tickets", "number")], "day", ["tickets"], f,
                    {"group_by": "day"})
    by_prio = chart(p, "bar_chart", "ops.tickets.by_priority", "Tickets by priority", "How urgent the tickets are.",
                    "tickets.summary", [C("priority", "Priority", "badge"), C("tickets", "Tickets", "number")], "priority", ["tickets"], f,
                    {"group_by": "priority"})
    put("ops.tickets", [st, by_status, by_cat, per_day, by_prio], [
        [st.widget_id], [by_status.widget_id, by_cat.widget_id], [per_day.widget_id, by_prio.widget_id], ["ops.tickets.grid"]])

    # ---------------------------------------------------------------- reviews
    p = P["ops.reviews"]
    f = {"review_score_max": "score_max", "customer_state": "customer_state", "has_reply": "has_reply"}
    st = strip(p, "ops.reviews.strip", "Customer sentiment at a glance", "Reviews in the period: count, average score, "
               "negative share, reviews with comments and replied reviews.", "reviews.summary",
               [C("reviews", "Reviews", "number"), C("avg_score", "Avg score", "number"), C("negative_share", "Negative (1-2★)", "ratio"),
                C("with_comment", "With comment", "number"), C("replied", "Replied", "number")], f)
    dist = chart(p, "bar_chart", "ops.reviews.score_dist", "Score distribution", "How many reviews gave each score from 1 to 5.",
                 "reviews.summary", [C("score", "Score", "number"), C("reviews", "Reviews", "number")], "score", ["reviews"], f,
                 {"group_by": "score"})
    month = chart(p, "line_chart", "ops.reviews.monthly", "Average score by month", "How the average review score moved month by month.",
                  "reviews.summary", [C("month", "Month", "date"), C("avg_score", "Avg score", "number")], "month", ["avg_score"], f,
                  {"group_by": "month"})
    neg = chart(p, "bar_chart", "ops.reviews.neg_by_state", "Negative reviews by state", "Where the 1-2 star reviews come from.",
                "reviews.summary", [C("customer_state", "State"), C("negative_reviews", "Negative reviews", "number")], "customer_state",
                ["negative_reviews"], f, {"group_by": "customer_state"})
    put("ops.reviews", [st, dist, month, neg], [[st.widget_id], [dist.widget_id, month.widget_id], [neg.widget_id], ["ops.reviews.grid"]])

    # ---------------------------------------------------------------- products and stock
    stock_cols = [C("products", "Listed products", "number"), C("stock_units", "Units in stock", "number"),
                  C("inventory_value", "Inventory value", "money"), C("below_reorder", "Below reorder point", "number"),
                  C("needs_restock", "Need restock", "number"), C("avg_days_of_cover", "Avg days of cover", "number")]
    p = P["ops.products"]
    f = {"product_category": "product_category", "below_reorder_point": "below_reorder_point"}
    st = strip(p, "ops.products.strip", "Stock at a glance", "Listed products, units, inventory value at cost, products "
               "below their reorder point and average days of cover.", "inventory.summary", stock_cols, f, date=False)
    val = chart(p, "bar_chart", "ops.products.value_by_cat", "Inventory value by category", "Where the money tied up in stock is.",
                "inventory.summary", [C("category", "Category"), C("inventory_value", "Inventory value", "money")], "category",
                ["inventory_value"], f, {"group_by": "category", "limit": 12}, date=False)
    low = chart(p, "bar_chart", "ops.products.low_by_cat", "Low-stock products by category", "Categories with the most products at or below their reorder point.",
                "inventory.summary", [C("category", "Category"), C("below_reorder", "Below reorder point", "number"),
                                      C("needs_restock", "Need restock", "number")], "category", ["below_reorder", "needs_restock"], f,
                {"group_by": "category", "sort": "-below_reorder", "limit": 12}, date=False)
    put("ops.products", [st, val, low], [[st.widget_id], [val.widget_id, low.widget_id], ["ops.products.grid"]])

    p = P["ops.low_stock"]
    f = {"product_category": "product_category"}
    st = strip(p, "ops.alerts.strip", "Restock need at a glance", "How many products are low, how much is tied up in stock "
               "and how many days of cover remain.", "inventory.summary", stock_cols, f, date=False)
    cat = chart(p, "bar_chart", "ops.alerts.by_cat", "Restock need by category", "Categories with the most products to restock now.",
                "inventory.summary", [C("category", "Category"), C("needs_restock", "Need restock", "number")], "category", ["needs_restock"],
                f, {"group_by": "category", "sort": "-needs_restock", "limit": 12}, date=False)
    put("ops.low_stock", [st, cat], [[st.widget_id], ["ops.alerts.trend", cat.widget_id], ["ops.alerts.grid"]])

    p = P["ops.restock_orders"]
    f = {"status": "status"}
    st = strip(p, "ops.restock.strip", "Restocking at a glance", "Restock orders, how many are open, units on order and units received.",
               "restock_orders.summary", [C("orders", "Orders", "number"), C("open_orders", "Open orders", "number"),
                                          C("open_units", "Units on order", "number"), C("received_units", "Units received", "number")],
               f, date=False)
    by_status = chart(p, "pie_chart", "ops.restock.by_status", "Restock orders by status", "How many restock orders are placed, in transit, received or canceled.",
                      "restock_orders.summary", [C("status", "Status", "badge"), C("orders", "Orders", "number")], "status", ["orders"], f,
                      {"group_by": "status"}, date=False)
    month = chart(p, "bar_chart", "ops.restock.per_month", "Units ordered per month", "How many units were ordered each month.",
                  "restock_orders.summary", [C("month", "Month", "date"), C("units", "Units", "number")], "month", ["units"], f,
                  {"group_by": "month"}, date=False)
    put("ops.restock_orders", [st, by_status, month], [[st.widget_id], [by_status.widget_id, month.widget_id], ["ops.restock.grid"]])

    # ---------------------------------------------------------------- sellers
    p = P["ops.sellers"]
    f = {"seller_state": "seller_state", "flagged": "flagged"}
    st = strip(p, "ops.sellers.strip", "Sellers at a glance", "Selling sellers, flagged sellers, revenue, late rate and review "
               "score for the period.", "sellers.summary",
               [C("active_sellers", "Selling sellers", "number"), C("flagged_sellers", "Flagged", "number"), C("revenue", "Revenue", "money"),
                C("avg_late_rate", "Late rate", "ratio"), C("avg_review_score", "Avg review", "number")], f)
    top = chart(p, "bar_chart", "ops.sellers.top10", "Top 10 sellers by revenue", "The ten sellers with the highest revenue in the period.",
                "sellers.search", [C("seller_ref", "Seller"), C("revenue", "Revenue", "money")], "seller_ref", ["revenue"], f,
                {"limit": 10, "sort": "-revenue"})
    by_state = chart(p, "bar_chart", "ops.sellers.by_state", "Revenue by seller state", "Where the revenue-earning sellers are based.",
                     "sellers.summary", [C("seller_state", "State"), C("revenue", "Revenue", "money")], "seller_state", ["revenue"], f,
                     {"group_by": "seller_state"})
    put("ops.sellers", [st, top, by_state], [[st.widget_id], [top.widget_id, by_state.widget_id], ["ops.sellers.grid"]])

    p = P["ops.seller_flags"]
    f = {"status": "status", "severity": "severity"}
    st = strip(p, "ops.flags.strip", "Flags at a glance", "Seller performance flags: total, open and high severity.",
               "seller_flags.summary", [C("flags", "Flags", "number"), C("open_flags", "Open", "number"), C("high_severity", "High severity", "number")],
               f, date=False)
    reason = chart(p, "pie_chart", "ops.flags.by_reason", "Flags by reason", "Why sellers were flagged.", "seller_flags.summary",
                   [C("reason", "Reason", "badge"), C("flags", "Flags", "number")], "reason", ["flags"], f, {"group_by": "reason"}, date=False)
    sev = chart(p, "bar_chart", "ops.flags.by_severity", "Flags by severity", "How serious the flags are.", "seller_flags.summary",
                [C("severity", "Severity", "badge"), C("flags", "Flags", "number")], "severity", ["flags"], f, {"group_by": "severity"}, date=False)
    put("ops.seller_flags", [st, reason, sev], [[st.widget_id], [reason.widget_id, sev.widget_id], ["ops.flags.grid"]])

    p = P["ops.funnel"]
    f = {"origin": "origin"}
    cols = [C("origin", "Channel"), C("leads", "Leads", "number"), C("won_deals", "Won", "number"),
            C("conversion_pct", "Conversion %", "percent"), C("seller_revenue", "Seller revenue", "money")]
    conv = chart(p, "bar_chart", "ops.funnel.conversion", "Conversion rate by channel", "Share of leads that became sellers, per channel.",
                 "funnel.channels", cols, "origin", ["conversion_pct"], f)
    rev = chart(p, "pie_chart", "ops.funnel.revenue_share", "Seller revenue by acquisition channel", "How much the sellers won through each channel have sold since joining.",
                "funnel.channels", cols, "origin", ["seller_revenue"], f)
    put("ops.funnel", [conv, rev], [["ops.funnel.chart", conv.widget_id], [rev.widget_id, "ops.funnel.channels"], ["ops.funnel.deals"]])

    # ---------------------------------------------------------------- planning
    p = P["ops.targets"]
    f = {"customer_state": "customer_state", "below_target": "below_target"}
    st = strip(p, "ops.targets.strip", "Target attainment at a glance", "Actual versus target revenue and orders for the period.",
               "targets.performance", [C("revenue_actual", "Actual revenue", "money"), C("revenue_target", "Target revenue", "money"),
                                       C("attainment_pct", "Attainment", "percent"), C("variance", "Variance", "money"),
                                       C("orders_actual", "Orders", "number"), C("order_target", "Order target", "number")],
               f, {"group_by": "none"})
    mo = chart(p, "line_chart", "ops.targets.monthly", "Actual vs target by month", "Monthly revenue next to its target.", "targets.performance",
               [C("month", "Month", "date"), C("revenue_actual", "Actual", "money"), C("revenue_target", "Target", "money")], "month",
               ["revenue_actual", "revenue_target"], f, {"group_by": "month"})
    put("ops.targets", [st, mo], [[st.widget_id], [mo.widget_id, "ops.targets.chart"], ["ops.targets.grid"]])

    p = P["ops.sla"]
    f = {"customer_region": "customer_region"}
    st = strip(p, "ops.sla.strip", "Delivery SLA at a glance", "Deliveries, SLA breaches and breach rate next to Olist's own late rate.",
               "sla.performance", [C("delivered_orders", "Delivered", "number"), C("breaches", "SLA breaches", "number"),
                                   C("breach_rate_pct", "Breach rate", "percent"), C("olist_late_rate_pct", "Olist late rate", "percent"),
                                   C("avg_delivery_days", "Avg delivery days", "number")], f, {"group_by": "none"})
    mo = chart(p, "line_chart", "ops.sla.monthly", "SLA breach rate by month", "How the share of slow deliveries moved month by month.",
               "sla.performance", [C("month", "Month", "date"), C("breach_rate_pct", "SLA breach %", "percent"),
                                   C("olist_late_rate_pct", "Olist late %", "percent")], "month", ["breach_rate_pct", "olist_late_rate_pct"], f,
               {"group_by": "month"})
    put("ops.sla", [st, mo], [[st.widget_id], [mo.widget_id, "ops.sla.chart"], ["ops.sla.grid"]])

    p = P["ops.forecast"]
    f = {"scenario": "scenario", "customer_state": "customer_state"}
    err = chart(p, "bar_chart", "ops.forecast.error_by_state", "Forecast error by state (Jan-Aug 2018)",
                "How far the forecast was from actual revenue in each state; positive means the forecast was too high.",
                "forecasts.comparison", [C("customer_state", "State"), C("error_pct", "Forecast error %", "percent")], "customer_state",
                ["error_pct"], f, {"group_by": "customer_state", "date_from": "2018-01-01", "date_to": "2018-08-31", "limit": 27}, date=False)
    put("ops.forecast", [err], [["ops.forecast.chart", err.widget_id], ["ops.forecast.grid"]])

    # ---------------------------------------------------------------- promotions
    p = P["ops.promotions"]
    f = {"status": "status", "product_category": "product_category"}
    ch = chart(p, "bar_chart", "ops.promotions.discounts", "Discount by promotion", "How deep each campaign's discount is.", "promotions.search",
               [C("name", "Promotion"), C("discount_pct", "Discount %", "percent")], "name", ["discount_pct"], f, {"limit": 14}, date=False)
    put("ops.promotions", [ch], [[ch.widget_id], ["ops.promotions.grid"]])

    widgets.extend(new)
