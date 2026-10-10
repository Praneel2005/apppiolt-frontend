"""Tests for agent normalisers (WP2 in AGENT_V2_DESIGN.md)."""
import pytest

from agent.normalise.dates import parse_date_query
from agent.normalise.entities import extract_geo_entities, disambiguate_filter_for_page
from agent.normalise.cues import extract_cues
from agent.normalise.references import resolve_structured_references


AS_OF = "2018-08-31"


def test_dates_last_3_months_not_last_quarter():
    """Audit Finding 2: 'last 3 months' must NEVER map to last_quarter."""
    r3 = parse_date_query("AOV in SP and RJ, last 3 months", as_of=AS_OF)
    assert r3 is not None
    assert r3.preset == "months:3"
    assert r3.from_date == "2018-06-01"
    assert r3.to_date == "2018-08-31"

    # Verify last_quarter is different
    rq = parse_date_query("AOV in SP and RJ, last quarter", as_of=AS_OF)
    assert rq is not None
    assert rq.preset == "last_quarter"
    assert rq.from_date == "2018-04-01"
    assert rq.to_date == "2018-06-30"

    assert r3.from_date != rq.from_date


def test_dates_various_expressions():
    r_lm = parse_date_query("Revenue for last month", as_of=AS_OF)
    assert r_lm is not None
    assert r_lm.preset == "last_month"
    assert r_lm.from_date == "2018-07-01"
    assert r_lm.to_date == "2018-07-31"

    r_q = parse_date_query("Orders in Q2 2018", as_of=AS_OF)
    assert r_q is not None
    assert r_q.preset == "quarter:2018Q2"
    assert r_q.from_date == "2018-04-01"
    assert r_q.to_date == "2018-06-30"

    r_since = parse_date_query("Deliveries since June", as_of=AS_OF)
    assert r_since is not None
    assert r_since.preset == "since:2018-06-01"
    assert r_since.from_date == "2018-06-01"

    r_yr = parse_date_query("Month-over-month growth 2017", as_of=AS_OF)
    assert r_yr is not None
    assert r_yr.preset == "year:2017"
    assert r_yr.from_date == "2017-01-01"
    assert r_yr.to_date == "2017-12-31"


def test_entities_geo_synonyms_and_hierarchy():
    # Audit query 2: SP and RJ
    geo2 = extract_geo_entities("AOV in SP and RJ, last 3 months")
    assert geo2.states == ["RJ", "SP"]
    assert geo2.role == "customer_state"

    # Audit query 3: Southeast in São Paulo (hierarchy subset)
    geo3 = extract_geo_entities("Sales for Southeast in São Paulo")
    assert geo3.region == "Southeast"
    assert geo3.states == ["SP"]
    assert geo3.is_hierarchy_subset is True

    # Audit query 5: Sellers in PR
    geo5 = extract_geo_entities("Sellers in PR")
    assert geo5.states == ["PR"]
    assert geo5.role == "seller_state"

    # Synonyms
    geo_syn = extract_geo_entities("Compare sales between Rio and Minas")
    assert "RJ" in geo_syn.states
    assert "MG" in geo_syn.states


def test_entities_filter_disambiguation():
    geo_seller = extract_geo_entities("Sellers in PR")
    # Page with seller_state filter
    flts = disambiguate_filter_for_page(["seller_state", "date_range"], geo_seller)
    assert flts == {"seller_state": ["PR"]}

    # Page with customer_state only
    flts2 = disambiguate_filter_for_page(["customer_state", "date_range"], geo_seller)
    assert flts2 == {"customer_state": ["PR"]}


def test_cues():
    c_rank = extract_cues("Show me the top 5 products by revenue")
    assert c_rank.is_rank is True
    assert c_rank.rank_limit == 5
    assert c_rank.rank_direction == "desc"

    c_worst = extract_cues("Worst 3 sellers in delivery time")
    assert c_worst.is_rank is True
    assert c_worst.rank_limit == 3
    assert c_worst.rank_direction == "asc"

    c_explain = extract_cues("Why did revenue go down from last month?")
    assert c_explain.is_explanation is True
    assert c_explain.act == "explain"

    c_trend = extract_cues("Month-over-month growth 2017")
    assert c_trend.is_trend is True

    c_comp = extract_cues("Compare revenue in SP and RJ")
    assert c_comp.is_comparison is True


def test_references_never_fires_on_generic_phrases():
    page_ctx = {
        "widgets": [
            {"rows": [{"order_id": "ORD-12345", "customer": "John"}]}
        ]
    }
    # Generic phrases MUST NOT resolve order_id
    r1 = resolve_structured_references("what is the order of magnitude of sales?", page_ctx)
    assert "order_id" not in r1

    r2 = resolve_structured_references("sort by revenue in descending order", page_ctx)
    assert "order_id" not in r2

    r3 = resolve_structured_references("show average order value", page_ctx)
    assert "order_id" not in r3

    # Direct reference MUST resolve
    r_valid = resolve_structured_references("show details for this order", page_ctx)
    assert r_valid.get("order_id") == "ORD-12345"
