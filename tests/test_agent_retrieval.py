"""Tests for agent/retrieval.py hybrid retrieval and graph expansion."""
from agent.retrieval import (
    SimpleDenseIndex,
    build_metadata_graph,
    hybrid_search,
)
from contracts.actions import AgentConfig


def test_simple_dense_index():
    docs = [
        {"id": "d1", "title": "Late orders", "text": "Customer orders that arrived past SLA deadline"},
        {"id": "d2", "title": "Support tickets", "text": "Customer complaints and issues with sellers"},
        {"id": "d3", "title": "Revenue report", "text": "Monthly sales by state and product category"},
    ]
    idx = SimpleDenseIndex()
    idx.fit(docs)

    results = idx.search("delayed delivery")
    assert len(results) > 0
    top_id, top_score = results[0]
    assert top_score > 0.0


def test_metadata_graph():
    G = build_metadata_graph()
    assert G.number_of_nodes() > 50
    assert G.number_of_edges() > 50
    assert "ops.orders" in G
    assert "ops.dashboard" in G


def test_hybrid_search():
    res = hybrid_search("late deliveries in Rio")
    assert len(res.pages) > 0
    page_ids = [p.id for p in res.pages]
    assert any("order" in pid or "late" in pid or "sla" in pid for pid in page_ids)

    # Check that candidates contain enriched metadata (route, filters)
    first_page = res.pages[0]
    assert "route" in first_page.data
    assert "filters" in first_page.data


def test_hybrid_search_with_graph_expansion():
    cfg_with = AgentConfig(use_graph_expansion=True)
    cfg_without = AgentConfig(use_graph_expansion=False)

    res_with = hybrid_search("tickets", config=cfg_with)
    res_without = hybrid_search("tickets", config=cfg_without)

    assert len(res_with.pages) > 0
    assert len(res_without.pages) > 0
