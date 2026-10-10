"""Unit tests for agent/capabilities.py (WP1 in AGENT_V2_DESIGN.md)."""
import pytest
from agent.capabilities import (
    get_capability_model,
    BRAZIL_REGIONS,
    STATE_TO_REGION,
    STATE_SYNONYMS,
)


def test_capability_model_indexing():
    caps = get_capability_model()
    assert len(caps.pages) >= 47
    assert len(caps.datasets) > 0

    # Test Orders page capability
    orders = caps.get_page("ops.orders")
    assert orders is not None
    assert orders.route == "/ops/orders"
    assert "customer_state" in orders.filters
    assert orders.supports_date_range is True

    # Test allowed values are present
    state_flt = orders.filters["customer_state"]
    assert len(state_flt.allowed_values) > 0
    assert "SP" in state_flt.allowed_values
    assert "RJ" in state_flt.allowed_values


def test_geographic_hierarchy():
    assert STATE_TO_REGION["SP"] == "Southeast"
    assert STATE_TO_REGION["RJ"] == "Southeast"
    assert STATE_TO_REGION["PR"] == "South"
    assert STATE_SYNONYMS["sao paulo"] == "SP"
    assert STATE_SYNONYMS["rio de janeiro"] == "RJ"


def test_pages_supporting_queries():
    caps = get_capability_model()

    # Pages supporting seller_state
    seller_pages = caps.pages_supporting(filters=["seller_state"])
    assert any(p.page_id == "ops.sellers" for p in seller_pages)

    # Pages supporting customer_state
    cust_pages = caps.pages_supporting(filters=["customer_state"])
    assert any(p.page_id == "ops.orders" for p in cust_pages)


def test_compact_slice_generation():
    caps = get_capability_model()
    s = caps.compact_slice(["ops.orders", "sales.revenue"], ["orders.search"])
    assert "ops.orders" in s
    assert "customer_state" in s
    assert "sales.revenue" in s
    assert "orders.search" in s
