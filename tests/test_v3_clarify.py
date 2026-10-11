"""Confidence-aware clarification: ask only when the readings differ materially, show both numbers, remember the answer."""
import pytest

from agent.capabilities import get_capability_model
from agent.clarify import ambiguity_clarification, option_message, remember_choice
from agent.interpret import interpret
from agent.lexicon import get_lexicon
from agent.reasoning.facts import fmt_number
from agent.reasoning.gateway import Gateway, make_period
from agent.schema import AgentMemory, TurnContext, get_agent_memory
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


def make_ctx(text: str, session=None, cfg=None):
    s = session or create_session()
    lex = get_lexicon()
    return TurnContext(session=s, text=text, page_ctx={"page": {}, "widgets": [], "filters": []},
                       cfg=cfg or AgentConfig(clarify_ambiguity=True, open_supporting_view=False), lex=lex,
                       cap=get_capability_model(), mem=get_agent_memory(s.id), llm=None, as_of=lex.as_of)


# ------------------------------------------------------------------------------------ pure
def test_option_message_makes_the_value_explicit_in_the_users_own_words():
    assert option_message("compare revenue in SP and RJ", "SP", "seller_state") == "compare revenue in seller state SP and RJ"
    assert option_message("revenue in sp", "SP", "customer_state") == "revenue in customer state sp"      # the user's own spelling
    assert option_message("revenue", "SP", "customer_state").endswith("(customer state SP)")
    assert option_message("show SPAIN and SP", "SP", "seller_state") == "show SPAIN and seller state SP"   # whole words only


def test_a_choice_is_kept_only_when_the_message_is_one_of_the_offered_options():
    mem = AgentMemory()
    mem.pending_choices = {"revenue in seller state SP": ("customer_state|seller_state", "seller_state")}
    remember_choice(mem, "something else entirely")
    assert mem.prefs == {} and mem.pending_choices == {}                    # pending options never outlive a turn
    mem.pending_choices = {"revenue in seller state SP": ("customer_state|seller_state", "seller_state")}
    remember_choice(mem, "  revenue in seller state SP ")
    assert mem.prefs == {"customer_state|seller_state": "seller_state"}


# ------------------------------------------------------------------------------------ with the database
@needs_db
@pytest.mark.anyio
async def test_an_ambiguous_state_is_asked_about_with_both_numbers_shown():
    ctx = make_ctx("revenue in SP in July 2018")
    it = await interpret(ctx)
    assert it.intent == "data" and it.ambiguities and set(it.ambiguities[0]["options"]) == {"customer_state", "seller_state"}
    assert await ambiguity_clarification(ctx, it) is True
    assert it.intent == "clarify" and len(it.clarify_options) == 2 and len(it.clarify_evidence) == 2
    assert any("customer state SP" in o for o in it.clarify_options) and any("seller state SP" in o for o in it.clarify_options)
    gw = Gateway(ctx)
    p = make_period("2018-07-01", "2018-07-31")
    for key in ("customer_state", "seller_state"):
        total = (await gw.query("revenue", [], {key: ["SP"]}, p))["total"]
        assert fmt_number(total, "BRL") in it.clarification                  # each number comes from the real query
    assert ctx.mem.pending_choices and all(k == "customer_state|seller_state" for k, _ in ctx.mem.pending_choices.values())


@needs_db
@pytest.mark.anyio
async def test_choosing_an_option_resolves_it_and_is_remembered_for_the_session():
    s = create_session()
    ctx = make_ctx("revenue in SP in July 2018", s)
    it = await interpret(ctx)
    await ambiguity_clarification(ctx, it)
    seller_option = next(o for o in it.clarify_options if "seller state" in o)
    # the user clicks the option: it is explicit, so nothing is ambiguous, and the choice is kept
    remember_choice(ctx.mem, seller_option)
    ctx2 = make_ctx(seller_option, s)
    it2 = await interpret(ctx2)
    assert not it2.ambiguities and it2.filters.get("seller_state") == ["SP"]
    # later, the plain wording resolves the same way without asking again
    ctx3 = make_ctx("orders in SP in July 2018", s)
    it3 = await interpret(ctx3)
    assert not it3.ambiguities and it3.filters.get("seller_state") == ["SP"] and "customer_state" not in it3.filters
    assert await ambiguity_clarification(ctx3, it3) is False


@needs_db
@pytest.mark.anyio
async def test_explicit_words_and_the_default_config_never_trigger_a_question():
    for text in ("revenue in customer state SP in July 2018", "revenue by seller state SP in July 2018", "revenue for seller SP in July 2018"):
        ctx = make_ctx(text)
        it = await interpret(ctx)
        assert not it.ambiguities, text
        assert await ambiguity_clarification(ctx, it) is False
    off = make_ctx("revenue in SP in July 2018", cfg=AgentConfig(open_supporting_view=False))      # library default: switch is off
    it = await interpret(off)
    assert await ambiguity_clarification(off, it) is False and it.intent == "data" and it.filters.get("customer_state") == ["SP"]


@needs_db
@pytest.mark.anyio
async def test_readings_that_agree_closely_are_not_worth_a_question():
    ctx = make_ctx("revenue in SP in July 2018")
    it = await interpret(ctx)
    old = ctx.lex.materiality_pct
    try:
        ctx.lex.materiality_pct = 1e9                                          # nothing counts as a material difference
        assert await ambiguity_clarification(ctx, it) is False
    finally:
        ctx.lex.materiality_pct = old
    assert it.intent == "data" and not it.clarify_evidence and not ctx.mem.pending_choices
