"""Seller-acquisition funnel (real Olist Marketing Funnel data) joined to the sellers' later sales."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import Paging, Params, Where, check_enum, check_enum_list, date_range, order_by, page, rows_json, source
from backend.db import ro

router = APIRouter()

# revenue each acquired seller made from the day they were won (real fact table)
SELLER_REV = """
  SELECT d.seller_id, sum(i.price) FILTER (WHERE i.is_valid_sale) AS revenue, count(*) FILTER (WHERE i.is_valid_sale) AS items
  FROM raw_closed_deals d JOIN fact_order_items i ON i.seller_id = d.seller_id AND i.order_date >= d.won_date::date
  GROUP BY d.seller_id"""


class ChannelsQuery(Params):
    date_from: date | None = Field(None, description="Leads first contacted on or after")
    date_to: date | None = Field(None, description="Leads first contacted on or before")
    preset: str | None = Field(None, description="Relative range on first contact date")
    origin: str | None = Field(None, description="Only these lead origins, comma-separated")
    sort: str | None = Field(None, description="leads (default -leads), won_deals, conversion_pct, seller_revenue")


@endpoint(router, "GET", "/api/funnel/channels", api_id="funnel.channels", entity="lead",
          title="Seller acquisition funnel by channel",
          description="For each marketing channel (organic search, paid search, social, ...): leads, closed deals "
                      "(leads that became sellers), conversion rate, and the revenue those new sellers made after "
                      "joining. All real Olist funnel and sales data (leads June 2017 - June 2018).",
          data_layers=["original", "derived"],
          returns="items[] of {origin, leads, won_deals, conversion_pct, active_sellers, seller_revenue}",
          examples=["which channel converts best", "how much revenue did paid-search sellers generate",
                    "lead conversion rate by origin"])
def funnel_channels(q: Annotated[ChannelsQuery, Query()]):
    f, t = date_range(q.date_from, q.date_to, q.preset)
    w = Where()
    if f:
        w.add("l.first_contact_date >= %(f)s", f=f)
    if t:
        w.add("l.first_contact_date <= %(t)s", t=t)
    if origins := check_enum_list("lead_origin", q.origin, "origin"):
        w.add("COALESCE(l.origin, 'unknown') = ANY(%(o)s)", o=origins)
    sorts = {"leads": "leads", "won_deals": "won_deals", "conversion_pct": "conversion_pct",
             "seller_revenue": "seller_revenue"}
    with ro() as conn:
        rows = conn.execute(f"""
            SELECT * FROM (
              SELECT COALESCE(l.origin, 'unknown') AS origin, count(*) AS leads, count(d.mql_id) AS won_deals,
                     round((100.0 * count(d.mql_id) / count(*))::numeric, 2) AS conversion_pct,
                     count(*) FILTER (WHERE r.revenue > 0) AS active_sellers,
                     round(COALESCE(sum(r.revenue), 0)::numeric, 2) AS seller_revenue
              FROM raw_marketing_qualified_leads l
              LEFT JOIN raw_closed_deals d USING (mql_id)
              LEFT JOIN ({SELLER_REV}) r ON r.seller_id = d.seller_id
              {w.sql()} GROUP BY 1) g{order_by(q.sort, sorts, '-leads')}""", w.params).fetchall()
    return {"items": rows_json(rows),
            "period": {"from": f.isoformat() if f else None, "to": t.isoformat() if t else None},
            "source": source(["raw_marketing_qualified_leads", "raw_closed_deals", "fact_order_items"],
                             ["original", "derived"],
                             "seller_revenue = valid-sale revenue of the acquired sellers from their won date on")}


class DealsQuery(Paging):
    business_segment: str | None = Field(None, description="Seller's business segment (e.g. health_beauty)")
    origin: str | None = Field(None, description="Lead origin")
    won_from: date | None = Field(None, description="Won on or after")
    won_to: date | None = Field(None, description="Won on or before")
    sort: str | None = Field(None, description="won_date (default -won_date), revenue_since_won, declared_monthly_revenue")


@endpoint(router, "GET", "/api/funnel/deals", api_id="funnel.deals", entity="lead",
          title="Closed deals (new sellers)",
          description="Leads that became sellers: when they were won, their business segment and type, declared "
                      "monthly revenue, lead origin, and the revenue they actually made on the marketplace afterwards.",
          data_layers=["original", "derived"], returns="items[] of deals with seller_ref and revenue_since_won; total",
          examples=["newest sellers from paid search", "which won sellers actually sell",
                    "closed deals in health_beauty"])
def funnel_deals(q: Annotated[DealsQuery, Query()]):
    w = Where()
    if q.business_segment:
        w.add("d.business_segment = %(seg)s", seg=check_enum("business_segment", q.business_segment, "business_segment"))
    if q.origin:
        w.add("COALESCE(l.origin, 'unknown') = %(o)s", o=check_enum("lead_origin", q.origin, "origin"))
    if q.won_from:
        w.add("d.won_date >= %(wf)s", wf=q.won_from)
    if q.won_to:
        w.add("d.won_date < %(wt)s::date + 1", wt=q.won_to)
    frm = (f"FROM raw_closed_deals d LEFT JOIN raw_marketing_qualified_leads l USING (mql_id) "
           f"LEFT JOIN ({SELLER_REV}) r ON r.seller_id = d.seller_id")
    sorts = {"won_date": "d.won_date", "revenue_since_won": "revenue_since_won",
             "declared_monthly_revenue": "d.declared_monthly_revenue"}
    with ro() as conn:
        total = conn.execute(f"SELECT count(*) AS n {frm}{w.sql()}", w.params).fetchone()["n"]
        rows = conn.execute(f"""
            SELECT d.mql_id, d.seller_id, upper(left(d.seller_id, 8)) AS seller_ref, d.won_date,
                   d.business_segment, d.lead_type, d.business_type, d.declared_monthly_revenue,
                   COALESCE(l.origin, 'unknown') AS origin, l.first_contact_date,
                   round(COALESCE(r.revenue, 0)::numeric, 2) AS revenue_since_won,
                   COALESCE(r.items, 0) AS items_since_won
            {frm}{w.sql()}{order_by(q.sort, sorts, '-won_date')} LIMIT %(limit)s OFFSET %(offset)s""",
                            {**w.params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset, source(["raw_closed_deals", "raw_marketing_qualified_leads",
                                                        "fact_order_items"], ["original", "derived"]))
