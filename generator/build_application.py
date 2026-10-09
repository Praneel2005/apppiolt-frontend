"""Application generator (card T6): builds the canonical Application (pages, widgets, filters)
from the semantic layer and writes data/olist/application.json.

    python -m generator.build_application

Pages come from templates (overview, metric-by-dimension, metric trend). Descriptions name the
question each page answers and the words a user would say, because retrieval embeds them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg

from contracts.metadata import Application, Directory, FilterDef, Module, Page, RenderContract, Widget
from generator.semantic_layer import build_datasets

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "olist" / "application.json"
AS_OF_DATE = "2018-08-31"  # last complete month, see docs/DATA_NOTES.md

STATE_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}
REGION_SYNONYMS = {
    "Southeast": ["south east", "south-east", "sudeste"], "South": ["southern", "sul"],
    "Northeast": ["north east", "north-east", "nordeste"], "North": ["northern", "norte"],
    "Central-West": ["central west", "centre-west", "midwest", "centro-oeste", "center west"],
}
PAYMENT_SYNONYMS = {
    "credit_card": ["credit card", "card", "credit"], "boleto": ["bank slip", "boleto bancario"],
    "debit_card": ["debit card", "debit"], "voucher": ["gift voucher", "coupon"], "not_defined": ["undefined"],
}

METRIC_WORDS = {
    "revenue": ["sales", "turnover", "income", "gmv", "money made"],
    "freight": ["shipping cost", "delivery cost", "freight charges"],
    "items_sold": ["units sold", "items", "quantity", "volume"],
    "orders": ["purchases", "number of orders", "order volume"],
    "aov": ["average ticket", "basket size", "average order value", "spend per order"],
    "avg_delivery_days": ["delivery time", "shipping time", "lead time", "how long delivery takes"],
    "late_rate": ["late deliveries", "delays", "delayed", "on-time", "overdue"],
    "order_count": ["number of orders", "all orders", "order volume"],
    "cancel_rate": ["cancellations", "canceled orders", "cancelled"],
    "order_late_rate": ["late orders", "delayed orders", "on-time orders"],
    "payment_value": ["payments", "amount paid", "money received", "collections"],
    "payment_count": ["number of payments", "transactions"],
    "avg_installments": ["installments", "parcelas", "payment plans", "split payments"],
    "review_count": ["reviews", "number of reviews", "feedback volume"],
    "avg_review_score": ["rating", "satisfaction", "stars", "customer score", "nps"],
}
DIM_WORDS = {
    "customer_state": ("customer state", ["state", "where customers are", "location", "geography"]),
    "customer_region": ("customer region", ["region", "area", "macro-region", "geography"]),
    "seller_state": ("seller state", ["seller location", "where sellers are", "merchant state"]),
    "product_category": ("product category", ["category", "product type", "department"]),
    "order_status": ("order status", ["status", "delivered", "canceled", "shipped"]),
    "payment_type": ("payment method", ["payment type", "how customers pay", "card or boleto"]),
    "order_month": ("month", ["over time", "monthly", "trend", "time series"]),
}

# module -> (title, description, dataset, metrics for breakdowns, dimensions, trend metrics, filter dims)
MODULES = {
    "sales": ("Sales", "Revenue, orders and basket size of valid sales", "ds_order_items",
              ["revenue", "orders", "items_sold", "aov"],
              ["product_category", "customer_state", "customer_region", "seller_state"],
              ["revenue", "orders", "aov"], ["product_category", "customer_state", "customer_region", "seller_state"]),
    "customers": ("Customers", "Where customers are, how much they buy and how often they cancel", "ds_orders",
                  ["order_count", "cancel_rate"], ["customer_state", "customer_region", "order_status"],
                  ["order_count", "cancel_rate"], ["customer_region", "customer_state", "order_status"]),
    "sellers": ("Sellers", "Seller locations, their sales and their delivery performance", "ds_order_items",
                ["revenue", "items_sold", "late_rate", "avg_delivery_days", "freight"], ["seller_state", "product_category"],
                [], ["seller_state", "customer_region", "product_category"]),
    "logistics": ("Logistics", "Delivery times, late deliveries and freight costs", "ds_order_items",
                  ["avg_delivery_days", "late_rate", "freight"], ["customer_state", "customer_region", "product_category"],
                  ["avg_delivery_days", "late_rate", "freight"], ["customer_region", "customer_state", "product_category"]),
    "payments": ("Payments", "How customers pay: methods, amounts and installments", "ds_payments",
                 ["payment_value", "payment_count", "avg_installments"], ["payment_type", "customer_state", "customer_region"],
                 ["payment_value", "payment_count", "avg_installments"], ["payment_type", "customer_region", "customer_state"]),
    "reviews": ("Reviews", "Customer satisfaction and review volume", "ds_reviews",
                ["avg_review_score", "review_count"], ["customer_state", "customer_region"],
                ["avg_review_score", "review_count"], ["customer_region", "customer_state"]),
    "catalog": ("Catalog", "Product categories: what sells, what it costs to ship and how well it is delivered", "ds_order_items",
                ["revenue", "items_sold", "aov", "freight", "late_rate", "avg_delivery_days"], ["product_category", "customer_state"],
                ["items_sold"], ["product_category", "customer_region", "customer_state"]),
}
# extra cross-module pages (orders-level logistics) to cover remaining questions
EXTRA_BREAKDOWNS = [("logistics", "ds_orders", "order_late_rate", "customer_state"),
                    ("logistics", "ds_orders", "order_late_rate", "customer_region")]
# two-dimension breakdowns: (module, metric, first dimension, second dimension)
MATRIX = [
    ("sales", "revenue", "customer_region", "product_category"), ("sales", "orders", "customer_region", "product_category"),
    ("sales", "aov", "customer_region", "product_category"), ("sales", "revenue", "customer_state", "product_category"),
    ("sales", "revenue", "seller_state", "customer_region"), ("sales", "revenue", "customer_region", "order_status"),
    ("logistics", "late_rate", "customer_region", "product_category"),
    ("logistics", "avg_delivery_days", "customer_region", "product_category"),
    ("logistics", "avg_delivery_days", "seller_state", "customer_region"),
    ("logistics", "late_rate", "seller_state", "customer_region"), ("logistics", "freight", "customer_region", "product_category"),
    ("payments", "payment_value", "payment_type", "customer_region"),
    ("payments", "avg_installments", "payment_type", "customer_region"),
    ("payments", "payment_count", "payment_type", "customer_state"),
    ("customers", "order_count", "customer_region", "order_status"), ("customers", "order_count", "customer_state", "order_status"),
    ("catalog", "items_sold", "product_category", "customer_region"), ("catalog", "late_rate", "product_category", "seller_state"),
]


def _slug(s: str) -> str:
    return s.replace("_", "-")


def _synonyms(field: str, values: list[str]) -> dict[str, list[str]]:
    if field in ("customer_state", "seller_state"):
        out = {}
        for code in values:
            name = STATE_NAMES.get(code)
            if name:
                ascii_name = (name.replace("ã", "a").replace("á", "a").replace("â", "a").replace("é", "e")
                              .replace("í", "i").replace("ô", "o").replace("ó", "o").replace("ç", "c"))
                out[code] = sorted({name, ascii_name, name.lower(), ascii_name.lower()})
        return out
    if field == "customer_region":
        return {k: v for k, v in REGION_SYNONYMS.items() if k in values}
    if field == "payment_type":
        return {k: v for k, v in PAYMENT_SYNONYMS.items() if k in values}
    if field == "product_category":
        return {v: [v.replace("_", " ")] for v in values if "_" in v}
    return {}


def _filters(ds, dims: list[str]) -> list[FilterDef]:
    fs = [FilterDef(filter_id="date_range", field=ds.time_field, type="date_range", title="Date range",
                    allowed_ops=["between"])]
    for d in dims:
        f = ds.field(d)
        if f is None or not f.values:
            continue
        fs.append(FilterDef(filter_id=d, field=d, type="multiselect", title=DIM_WORDS[d][0].title(),
                            allowed_ops=["eq", "in"], allowed_values=f.values, synonyms=_synonyms(d, f.values)))
    return fs


def build(conn: psycopg.Connection) -> Application:
    datasets = {d.dataset_id: d for d in build_datasets(conn)}
    metric = {m.metric_id: (d, m) for d in datasets.values() for m in d.metrics}
    modules, directories, pages, widgets = [], [], [], []
    title_of = {mod: spec[0] for mod, spec in MODULES.items()}

    def add_page(page: Page, ws: list[Widget]):
        pages.append(page)
        widgets.extend(ws)

    for mod, (title, desc, ds_id, bmetrics, bdims, tmetrics, fdims) in MODULES.items():
        ds = datasets[ds_id]
        modules.append(Module(module_id=mod, title=title, description=desc))
        directories += [Directory(path=mod, title=title, description=desc),
                        Directory(path=f"{mod}/breakdowns", title=f"{title} breakdowns",
                                  description=f"{title} metrics split by a dimension"),
                        Directory(path=f"{mod}/trends", title=f"{title} trends",
                                  description=f"{title} metrics month by month")]
        filters = _filters(ds, fdims)

        # overview page: KPI cards + monthly line of the first metric
        pid = f"{mod}.overview"
        kpis = [Widget(widget_id=f"{pid}.kpi_{m}", type="kpi_card", title=metric[m][1].title,
                       description=f"Headline {metric[m][1].title.lower()} for the selected period ({metric[m][1].description.lower()}).",
                       dataset_id=ds_id, metrics=[m], supports=["filter", "date_range"],
                       render=RenderContract(query_metrics=[m])) for m in bmetrics[:4]]
        line = Widget(widget_id=f"{pid}.trend", type="line_chart", title=f"{metric[bmetrics[0]][1].title} by month",
                      description=f"Monthly {metric[bmetrics[0]][1].title.lower()} for the selected period.",
                      dataset_id=ds_id, metrics=[bmetrics[0]], dimensions=["order_month"],
                      supports=["filter", "date_range"], render=RenderContract(query_metrics=[bmetrics[0]], query_dimensions=["order_month"]))
        add_page(Page(page_id=pid, title=f"{title} overview",
                      description=f"{title} overview dashboard: {desc.lower()}. Headline numbers "
                                  f"({', '.join(metric[m][1].title.lower() for m in bmetrics[:4])}) and the monthly "
                                  f"{metric[bmetrics[0]][1].title.lower()} trend for the selected period.",
                      directory=mod, route=f"/{mod}/overview",
                      keywords=[mod, "overview", "dashboard", "summary", "kpi"] + [w for m in bmetrics[:4] for w in METRIC_WORDS[m][:2]],
                      widgets=[w.widget_id for w in kpis + [line]], filters=filters,
                      default_state={"date_range": {"preset": "last_12_months"}}), kpis + [line])

        # consolidated metric pages with multi-dimensional filtering
        for m in bmetrics:
            mdef = metric[m][1]
            pid = f"{mod}.{m}"
            m_filters = _filters(ds, fdims)

            kpi_w = Widget(
                widget_id=f"{pid}.kpi",
                type="kpi_card",
                title=f"Total {mdef.title}",
                description=f"Total {mdef.title.lower()} for the selected filters and date range.",
                dataset_id=ds_id,
                metrics=[m],
                supports=["filter", "date_range"],
                render=RenderContract(query_metrics=[m]),
            )
            page_widgets = [kpi_w]
            layout_rows = [[kpi_w.widget_id]]

            trend_w = Widget(
                widget_id=f"{pid}.trend",
                type="line_chart",
                title=f"{mdef.title} over time",
                description=f"Monthly {mdef.title.lower()} trend ({mdef.unit}) for the selected period.",
                dataset_id=ds_id,
                metrics=[m],
                dimensions=["order_month"],
                supports=["filter", "date_range"],
                render=RenderContract(query_metrics=[m], query_dimensions=["order_month"]),
            )
            page_widgets.append(trend_w)
            layout_rows.append([trend_w.widget_id])

            breakdown_row = []
            for d in bdims[:2]:
                dname, _ = DIM_WORDS[d]
                b_w = Widget(
                    widget_id=f"{pid}.by_{d}",
                    type="bar_chart",
                    title=f"{mdef.title} by {dname.title()}",
                    description=f"Bar chart of {mdef.title.lower()} per {dname}.",
                    dataset_id=ds_id,
                    metrics=[m],
                    dimensions=[d],
                    supports=["sort", "filter", "date_range"],
                    render=RenderContract(query_metrics=[m], query_dimensions=[d]),
                )
                page_widgets.append(b_w)
                breakdown_row.append(b_w.widget_id)
            if breakdown_row:
                layout_rows.append(breakdown_row)

            primary_dim = bdims[0]
            pname, _ = DIM_WORDS[primary_dim]
            extra_m = [x for x in ([m] + [k for k in ("orders", "order_count", "payment_count", "review_count") if any(mm.metric_id == k for mm in ds.metrics)]) if x]
            grid_metrics = list(dict.fromkeys(extra_m))[:2]
            grid_w = Widget(
                widget_id=f"{pid}.grid",
                type="grid",
                title=f"{mdef.title} by {pname.title()} (table)",
                description=f"Sortable table of {mdef.title.lower()} per {pname}.",
                dataset_id=ds_id,
                metrics=grid_metrics,
                dimensions=[primary_dim],
                supports=["sort", "filter", "date_range"],
                render=RenderContract(query_metrics=grid_metrics, query_dimensions=[primary_dim]),
            )
            page_widgets.append(grid_w)
            layout_rows.append([grid_w.widget_id])

            dim_names = [DIM_WORDS[d][0] for d in fdims]
            add_page(
                Page(
                    page_id=pid,
                    title=mdef.title,
                    description=f"{mdef.title} analysis in the {title} module: {mdef.description.lower()}. "
                                f"Includes trend and geographical/categorical breakdowns. Filter by {', '.join(dim_names)}.",
                    directory=mod,
                    route=f"/{mod}/{_slug(m)}",
                    keywords=[mod, m.replace("_", " "), mdef.title.lower()] + METRIC_WORDS.get(m, []) + [w for d in fdims for w in DIM_WORDS[d][1][:2]],
                    widgets=[w.widget_id for w in page_widgets],
                    filters=m_filters,
                    layout=layout_rows,
                    default_state={"date_range": {"preset": "last_12_months"}},
                ),
                page_widgets,
            )

    # codes for the generated report pages / widgets: P-1001.., R-2001.. (curated ops pages carry P-100..P-700)
    for i, p in enumerate(pages, start=1):
        p.page_code = f"P-{1000 + i}"
        p.agent_context = (f"Report page from the Reports library ({title_of[p.directory.split('/')[0]]}). "
                           "Shows real Olist data for the selected period and filters; 'this'/'here' means the "
                           "numbers on screen.")
    # curated operations section (orders, tickets, stock, sellers, planning, promotions) comes first in the nav
    from generator.ops_pages import build_ops
    o_modules, o_dirs, o_pages, o_widgets = build_ops(conn, datasets, _synonyms)
    modules, directories = o_modules + modules, o_dirs + directories
    pages, widgets = o_pages + pages, o_widgets + widgets
    for n, w in enumerate(widgets, start=1):
        w.widget_code = f"R-{n:04d}"

    return Application(app_id="olist", name="Olist Seller Operations", source_format="native",
                       as_of_date=AS_OF_DATE, modules=modules, directories=directories, pages=pages,
                       widgets=widgets, datasets=list(datasets.values()))


def main() -> None:
    url = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
    with psycopg.connect(url) as conn:
        app = build(conn)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(app.model_dump(mode="json", by_alias=True), indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(app.modules)} modules, {len(app.pages)} pages, "
          f"{len(app.widgets)} widgets, {len(app.datasets)} datasets")


if __name__ == "__main__":
    main()
