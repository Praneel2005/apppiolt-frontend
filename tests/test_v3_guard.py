"""Typo repair in the parser, and the guard against confident answers to requests that were only partly understood."""
import pytest

from agent.capabilities import get_capability_model
from agent.clarify import guard_unmapped
from agent.interpret import interpret
from agent.lexicon import get_lexicon
from agent.perceive import perceive
from agent.schema import TurnContext, get_agent_memory
from backend.sessions import create_session
from contracts.actions import AgentConfig


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(not _db_ok(), reason="database not available")


# ------------------------------------------------------------------------------------ typo repair (no database)
@pytest.mark.parametrize("text,metric", [("reveneu in july 2018", "revenue"), ("what was revnue last month", "revenue"),
                                         ("show me ordrs in 2017", "orders"), ("whats the averge order value", "aov")])
def test_misspelt_metrics_are_still_found(text, metric):
    lex = get_lexicon()
    ann = perceive(text, lex, lex.as_of)
    assert ann.metric_ids and ann.metric_ids[0] == metric, (text, ann.metric_ids)


def test_a_misspelt_dimension_is_still_found_and_correct_words_are_never_changed():
    lex = get_lexicon()
    assert any(d.dim == "customer_state" for d in perceive("revenue by statee last quarter", lex, lex.as_of).dims)
    for text in ("revenue by customer state last quarter", "show the monthly trend of orders", "compare revenue for the next quarter"):
        clean = perceive(text, lex, lex.as_of)
        assert clean.metric_ids, text
    # words with no near neighbour stay exactly as typed
    ann = perceive("revenue for Atlantis", lex, lex.as_of)
    assert "atlantis" in ann.unexplained


# ------------------------------------------------------------------------------------ the guard
def make_ctx(text: str, guard: bool = True):
    s = create_session()
    lex = get_lexicon()
    return TurnContext(session=s, text=text, page_ctx={"page": {}, "widgets": [], "filters": []},
                       cfg=AgentConfig(guard_unmapped=guard, open_supporting_view=False), lex=lex, cap=get_capability_model(),
                       mem=get_agent_memory(s.id), llm=None, as_of=lex.as_of)


@needs_db
@pytest.mark.anyio
async def test_an_unknown_word_is_reported_and_a_mostly_unmapped_request_is_asked_about():
    ctx = make_ctx("revenue for Mars")
    it = await interpret(ctx)
    assert guard_unmapped(ctx, it) is False
    assert it.intent == "data" and it.ignored and any("mars" in w for w in it.ignored)
    assert any("did not understand" in a for a in it.assumptions)

    ctx = make_ctx("what is the weather in Rio tomorrow")
    it = await interpret(ctx)
    assert guard_unmapped(ctx, it) is True and it.intent == "clarify" and "could not map" in it.clarification


@needs_db
@pytest.mark.anyio
async def test_a_period_after_the_data_ends_is_explained_not_answered():
    ctx = make_ctx("revenue in 2030")
    it = await interpret(ctx)
    assert guard_unmapped(ctx, it) is True
    assert it.intent == "clarify" and ctx.as_of in it.clarification and "2030" in it.clarification


@needs_db
@pytest.mark.anyio
async def test_well_understood_requests_pass_untouched_and_the_switch_is_off_by_default():
    ctx = make_ctx("revenue by customer state in July 2018")
    it = await interpret(ctx)
    assert guard_unmapped(ctx, it) is False and it.intent == "data" and not it.ignored
    off = make_ctx("revenue for Mars", guard=False)
    it = await interpret(off)
    assert guard_unmapped(off, it) is False and it.intent == "data" and not it.ignored
