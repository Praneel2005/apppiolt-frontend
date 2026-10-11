"""Relationships between dimensions are found from the data, not guessed from names: siblings share a value set, and the words that
name their roles are the parts of their names the siblings do not share."""
import pytest

from agent.capabilities import get_capability_model
from agent.interpret import interpret
from agent.lexicon import get_lexicon, sibling_sets
from agent.perceive import perceive
from agent.schema import TurnContext, get_agent_memory
from backend.sessions import create_session
from contracts.actions import AgentConfig

STATES = [f"S{i:02d}" for i in range(27)]


def test_siblings_are_found_from_overlapping_values_whatever_the_names_are():
    domains = {"customer_state": STATES, "seller_state": STATES[:23], "customer_region": ["N", "S", "E", "W", "C"],
               "product_category": [f"cat{i}" for i in range(40)],
               "origin_city": [f"city{i}" for i in range(10)], "destination_city": [f"city{i}" for i in range(2, 12)]}
    groups = sibling_sets(domains)
    assert {"customer_state": {"customer"}, "seller_state": {"seller"}} in groups
    assert {"origin_city": {"origin"}, "destination_city": {"destination"}} in groups
    assert len(groups) == 2                                                    # region and category have no sibling


def test_a_shared_name_part_alone_does_not_make_dimensions_siblings():
    domains = {"order_status": ["a", "b", "c", "d", "e", "f"], "payment_status": ["p", "q", "r", "s", "t", "u"]}
    assert sibling_sets(domains) == []                                          # same suffix, unrelated values


def test_the_real_metadata_has_the_customer_seller_pair_and_its_role_words():
    lex = get_lexicon()
    assert any({"customer_state", "seller_state"} <= set(g) for g in lex.sibling_sets)
    assert lex.role_stems["seller"] == {"seller_state"} and lex.role_stems["customer"] == {"customer_state"}
    assert "seller_state" in lex.role_stems["vendor"] and "customer_state" in lex.role_stems["buyer"]    # synonyms from the data file


def test_a_role_word_selects_the_dimension_in_the_parser():
    lex = get_lexicon()
    for text in ("revenue for vendors in SP", "sellers in SP", "revenue from merchants in SP"):
        assert "seller_state" in perceive(text, lex, lex.as_of).role_dims, text
    assert perceive("revenue for buyers in SP", lex, lex.as_of).role_dims == {"customer_state"}
    assert perceive("revenue in SP", lex, lex.as_of).role_dims == set()


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


def make_ctx(text: str):
    s = create_session()
    lex = get_lexicon()
    return TurnContext(session=s, text=text, page_ctx={"page": {}, "widgets": [], "filters": []}, cfg=AgentConfig(open_supporting_view=False),
                       lex=lex, cap=get_capability_model(), mem=get_agent_memory(s.id), llm=None, as_of=lex.as_of)


@pytest.mark.skipif(not _db_ok(), reason="database not available")
@pytest.mark.anyio
async def test_role_words_decide_the_field_and_without_one_it_is_flagged_as_ambiguous():
    it = await interpret(make_ctx("revenue for vendors in SP in July 2018"))
    assert it.filters.get("seller_state") == ["SP"] and "customer_state" not in it.filters and not it.ambiguities
    it = await interpret(make_ctx("revenue for buyers in SP in July 2018"))
    assert it.filters.get("customer_state") == ["SP"] and not it.ambiguities
    it = await interpret(make_ctx("revenue in SP in July 2018"))                  # no role word: the metadata's first field, flagged
    assert it.filters.get("customer_state") == ["SP"] and it.ambiguities and it.ambiguities[0]["chosen"] == "customer_state"
