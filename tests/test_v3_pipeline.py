"""End-to-end V3 pipeline (rules-only: no LLM): events, UI state, evidence, status, forms and safety."""
import pytest

from agent.agent import AppPilotAgent
from agent.llm import FakeLLM
from agent.schema import FieldF, InterpretationF
from backend.sessions import create_session, get_session
from contracts.actions import AgentConfig


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(not _db_ok(), reason="database not available")


async def run(agent, text, sid=None, cfg=None):
    sid = sid or create_session().id
    events = [e async for e in agent.handle(sid, text, cfg or AgentConfig())]
    return sid, events


def by_type(events, t):
    return [e for e in events if e.type == t]


def answer(events):
    return by_type(events, "answer")[0].data


def check_envelope(events):
    types = [e.type for e in events]
    assert types[0] == "understanding" and types[-1] == "done"
    for needed in ("understood", "plan", "status", "answer"):
        assert needed in types, needed
    assert [e.seq for e in events] == sorted(e.seq for e in events)


@needs_db
@pytest.mark.anyio
async def test_view_command_is_atomic_verified_and_honest():
    agent = AppPilotAgent(use_llm=False)
    sid, ev = await run(agent, "show late deliveries in RJ last month")
    check_envelope(ev)
    st = get_session(sid).state
    assert any("RJ" in fv.value for fv in st.filters.values())
    assert st.date_range and (st.date_range.from_, st.date_range.to) == ("2018-07-01", "2018-07-31")
    nav_actions = [e for e in by_type(ev, "action") if e.data.get("tool") == "navigate" and e.data.get("status") == "running"]
    assert len(nav_actions) == 1                                  # ONE atomic state change, not three
    a = answer(ev)
    assert a["status"] in ("VERIFIED", "PARTIAL") and "Opened" in a["text"]
    if a["status"] == "VERIFIED":
        assert "verified" in a["text"].lower()
    assert a["ungrounded_numbers"] == []


@needs_db
@pytest.mark.anyio
async def test_data_question_is_computed_cited_and_grounded():
    agent = AppPilotAgent(use_llm=False)
    sid, ev = await run(agent, "what was revenue last month")
    a = answer(ev)
    assert "R$" in a["text"] and a["citations"] and a["ungrounded_numbers"] == []
    from backend.db import fetch_all
    v = float(fetch_all("SELECT SUM(price) AS v FROM fact_order_items WHERE is_valid_sale "
                        "AND order_date BETWEEN '2018-07-01' AND '2018-07-31'")[0]["v"])
    assert f"{v:,.2f}" in a["text"]                               # the exact figure from independent SQL


@needs_db
@pytest.mark.anyio
async def test_breakdown_and_rank_produce_a_view_or_a_canvas():
    agent = AppPilotAgent(use_llm=False)
    sid, ev = await run(agent, "top 5 categories by sales this year")
    a = answer(ev)
    assert a["citations"] and "1." in a["text"] and a["ungrounded_numbers"] == []
    opened = [e for e in by_type(ev, "action") if e.data.get("tool") == "navigate"]
    assert by_type(ev, "canvas") or opened                        # shown somewhere, never only in prose


@needs_db
@pytest.mark.anyio
async def test_reasoning_question_gets_an_analysis_not_a_page():
    agent = AppPilotAgent(use_llm=False)
    sid, ev = await run(agent, "why did sales go down compared to last month")
    check_envelope(ev)
    a = answer(ev)
    assert len(by_type(ev, "analysis_step")) >= 3 and len(by_type(ev, "evidence")) >= 3
    assert "Revenue" in a["text"] and "R$" in a["text"] and "Where the change comes from" in a["text"]
    assert a["ungrounded_numbers"] == [] and a["status"] in ("VERIFIED", "PARTIAL")
    assert "July 2018" in a["text"] and "August 2018" in a["text"]   # resolved periods are stated


@needs_db
@pytest.mark.anyio
async def test_comparison_of_two_slices_and_health_scan():
    agent = AppPilotAgent(use_llm=False)
    _, ev = await run(agent, "compare revenue in SP and RJ in march 2018")
    a = answer(ev)
    assert "SP" in a["text"] and "RJ" in a["text"] and "Gap" in a["text"] and a["ungrounded_numbers"] == []
    _, ev2 = await run(agent, "how are we doing overall")
    assert "Business health" in answer(ev2)["text"] and by_type(ev2, "canvas")


@needs_db
@pytest.mark.anyio
async def test_refusal_clarification_and_help_make_no_changes():
    agent = AppPilotAgent(use_llm=False)
    sid, ev = await run(agent, "delete all orders from 2017")
    a = answer(ev)
    assert a["intent"] == "decline" and not by_type(ev, "action") and by_type(ev, "done")[0].data["steps"] == 0
    _, ev2 = await run(agent, "what can you do")
    assert answer(ev2)["intent"] == "help" and "Open and filter screens" in answer(ev2)["text"]


@needs_db
@pytest.mark.anyio
async def test_llm_unavailable_still_answers():
    class Boom(FakeLLM):
        async def generate_structured(self, schema, messages, system_prompt=""):
            raise RuntimeError("provider down")

    agent = AppPilotAgent(llm=Boom())
    _, ev = await run(agent, "why did revenue fall compared to last month")
    assert answer(ev)["status"] in ("VERIFIED", "PARTIAL") and "Revenue" in answer(ev)["text"]


@needs_db
@pytest.mark.anyio
async def test_interpretation_by_the_model_is_checked_before_it_is_shown():
    """A model that invents numbers or contradicts the facts is ignored; the deterministic answer stays."""
    from agent.answer import NarrativeF

    class LyingNarrator(FakeLLM):
        async def generate_structured(self, schema, messages, system_prompt=""):
            if schema is NarrativeF:
                return NarrativeF(reading="Revenue exploded by 900% because of a viral campaign.")
            return await super().generate_structured(schema, messages, system_prompt)

    fake = LyingNarrator()
    fake.queue_response(InterpretationF(intent="explain", metric="revenue", compare_kind="periods"))
    agent = AppPilotAgent(llm=fake)
    _, ev = await run(agent, "why did revenue change this month")
    a = answer(ev)
    assert "900" not in a["text"] and "viral" not in a["text"] and a["narrative_used"] is False


@needs_db
@pytest.mark.anyio
async def test_record_lookup_by_id_and_by_pointing_at_the_screen():
    from agent.capabilities import get_capability_model
    from backend.db import fetch_all
    cap = get_capability_model()
    if not any(a.entity == "order" and a.kind == "read" and "{" in a.path for a in cap.apis.values()):
        pytest.skip("the catalogue has no order lookup API")
    ref = fetch_all("SELECT upper(left(order_id, 8)) AS r FROM raw_orders WHERE left(order_id, 8) ~ '[0-9]' "
                    "AND left(order_id, 8) ~ '[a-f]' LIMIT 1")[0]["r"]
    agent = AppPilotAgent(use_llm=False)
    sid, ev = await run(agent, f"what happened with order {ref}")
    a = answer(ev)
    assert a["intent"] == "lookup" and ref in a["text"] and a["citations"] and a["status"] == "VERIFIED"
    assert a["ungrounded_numbers"] == []
    # point at the screen: open the orders page, then ask about "this order"
    sid2, _ = await run(agent, "open orders")
    _, ev2 = await run(agent, "tell me about this order", sid=sid2)
    assert answer(ev2)["intent"] == "lookup" and answer(ev2)["status"] == "VERIFIED"
    # and the record is remembered: "open a ticket for it" fills the order id without being told again
    _, ev3 = await run(agent, "I want to open a ticket for it", sid=sid2)
    forms = get_session(sid2).canvases
    assert any(c["kind"] == "form" and c["form"]["defaults"].get("order_id") for c in forms.values())


@needs_db
@pytest.mark.anyio
async def test_why_after_an_answer_keeps_the_metric_scope_and_period():
    agent = AppPilotAgent(use_llm=False)
    sid, _ = await run(agent, "what was revenue last month in SP")
    _, ev = await run(agent, "why?", sid=sid)
    a = answer(ev)
    assert a["intent"] == "explain" and "Revenue" in a["text"] and "July 2018" in a["text"]


@needs_db
@pytest.mark.anyio
async def test_form_opens_fills_from_chat_validates_and_cancels():
    agent = AppPilotAgent(use_llm=False)
    sid, ev = await run(agent, "create a support ticket")
    forms = [e for e in by_type(ev, "canvas") if e.data.get("kind") == "form"]
    assert forms and "still needed" in answer(ev)["text"]
    sid, ev2 = await run(agent, "the category is damaged", sid=sid)
    form_state = get_session(sid).canvases
    assert any(c["kind"] == "form" and c["form"]["defaults"].get("category") == "damaged_item" for c in form_state.values())
    sid, ev3 = await run(agent, "category: nonsense", sid=sid)
    assert "must be one of" in answer(ev3)["text"] or "problems" in answer(ev3)["text"]
    sid, ev4 = await run(agent, "cancel", sid=sid)
    assert "Cancelled" in answer(ev4)["text"]


@needs_db
@pytest.mark.anyio
async def test_write_goes_through_preview_confirmation_and_undo():
    from backend.db import fetch_all
    from backend import executor
    oid = fetch_all("SELECT left(order_id, 8) AS r FROM raw_orders o WHERE NOT EXISTS "
                    "(SELECT 1 FROM ops_support_tickets t WHERE t.order_id = o.order_id) LIMIT 1")[0]["r"].upper()
    agent = AppPilotAgent(use_llm=False)
    cfg = AgentConfig(auto_confirm_writes=True)              # tests only: the user's click is simulated
    sid, ev = await run(agent, f"open a ticket for order {oid}, category damaged item", cfg=cfg)
    try:
        assert by_type(ev, "confirm_request"), "a preview/confirmation card must be raised before anything is saved"
        assert "applied" in answer(ev)["text"] or "Done" in answer(ev)["text"]
        s = get_session(sid)
        assert s.writes
        _, ev_u = await run(agent, "undo that", sid=sid, cfg=cfg)
        assert "Reverted" in answer(ev_u)["text"] and not s.writes
    finally:
        s = get_session(sid)
        while s.writes:
            await executor.undo_last_write(s)


@needs_db
@pytest.mark.anyio
async def test_writes_are_never_auto_approved_by_default():
    import asyncio
    from backend.db import fetch_all
    from backend import sessions
    oid = fetch_all("SELECT left(order_id, 8) AS r FROM raw_orders o WHERE NOT EXISTS "
                    "(SELECT 1 FROM ops_support_tickets t WHERE t.order_id = o.order_id) LIMIT 1")[0]["r"].upper()
    agent = AppPilotAgent(use_llm=False)
    sid = create_session().id
    events = []

    async def drive():
        async for e in agent.handle(sid, f"open a ticket for order {oid}, category damaged item", AgentConfig()):
            events.append(e)

    task = asyncio.create_task(drive())
    s = get_session(sid)
    for _ in range(200):                                       # wait for the confirmation card
        pend = [k for k, f in s.confirms.items() if not f.done()]
        if pend:
            break
        await asyncio.sleep(0.05)
    assert pend, "the agent must wait for the user instead of approving by itself"
    assert not s.writes                                         # nothing saved yet
    sessions.resolve_confirm(s, pend[0], False)                # the user declines
    await asyncio.wait_for(task, timeout=20)
    assert not s.writes and "nothing was saved" in answer(events)["text"].lower()
