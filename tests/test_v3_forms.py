"""Forms are generic: a write API that did not exist when the agent was written is detected, opened as a form, filled from chat
and validated, with no code change. A synthetic 'account sign-up' API is injected into the capability model to prove it."""
import pytest

from agent.capabilities import ApiCapability, CapabilityModel, get_capability_model
from agent.interpret import interpret
from agent.lexicon import get_lexicon
from agent.schema import TurnContext, get_agent_memory
from agent.skills.act import run_act, validate_field
from agent.skills.base import SkillResult
from backend.sessions import create_session
from contracts.actions import AgentConfig

SIGNUP = ApiCapability(
    api_id="accounts.signup", kind="write", title="Create a customer account", description="Register a new customer account",
    entity="account", path="/api/accounts", method="POST", parameters=(),
    body_schema={"type": "object", "required": ["full_name", "email", "state"], "properties": {
        "full_name": {"type": "string", "maxLength": 80},
        "email": {"type": "string"},
        "state": {"type": "string", "enum": ["SP", "RJ", "MG"]},
        "tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free"},
        "password": {"type": "string"}}},
    data_layers=("synthetic",), examples=("register a new account for Ana", "create an account"))


def cap_with_signup() -> CapabilityModel:
    base = get_capability_model()
    return CapabilityModel(pages=base.pages, datasets=base.datasets, apis={**base.apis, SIGNUP.api_id: SIGNUP})


def make_ctx(text, session, cap):
    lex = get_lexicon()
    return TurnContext(session=session, text=text, page_ctx={"page": {}, "widgets": [], "filters": []}, cfg=AgentConfig(),
                       lex=lex, cap=cap, mem=get_agent_memory(session.id), llm=None, as_of=lex.as_of)


async def turn(text, session, cap):
    ctx = make_ctx(text, session, cap)
    it = await interpret(ctx)
    out = SkillResult()
    events = [e async for e in run_act(ctx, it, out)] if it.intent == "act" else []
    return ctx, it, out, events


@pytest.mark.anyio
async def test_a_brand_new_form_is_opened_filled_from_chat_and_validated():
    cap, s = cap_with_signup(), create_session()
    ctx, it, out, events = await turn("I want to create an account", s, cap)
    assert it.intent == "act" and it.write_api == "accounts.signup"
    form = ctx.mem.form
    assert form and form.api_id == "accounts.signup" and set(form.missing) == {"full_name", "email", "state"}
    assert form.values == {"tier": "free"}                                    # the API's own default is pre-filled
    canvas = s.canvases["FORM-accounts.signup"]
    assert canvas["kind"] == "form" and "email" in canvas["form"]["body_schema"]["properties"]
    assert any(k == "form_open" for k, _ in events) and "still needed" in out.text

    ctx, it, out, _ = await turn('my name is "Ana Souza", email ana@example.com, from sp', s, cap)
    form = ctx.mem.form
    assert it.intent == "act" and form.values["full_name"] == "Ana Souza"
    assert form.values["email"] == "ana@example.com" and form.values["state"] == "SP" and form.missing == []
    assert s.canvases["FORM-accounts.signup"]["form"]["defaults"]["state"] == "SP"      # the open form shows the new values
    assert "submit" in out.text

    ctx, it, out, _ = await turn("password is hunter2", s, cap)                          # secrets never go through chat
    form = ctx.mem.form
    assert "password" not in form.values and "password" in form.errors and "hunter2" not in out.text

    ctx, it, out, _ = await turn("email is not-an-email", s, cap)
    assert "email" in ctx.mem.form.errors and ctx.mem.form.values["email"] == "ana@example.com"   # bad value rejected, old kept

    ctx, it, out, _ = await turn("cancel", s, cap)
    assert ctx.mem.form is None and "Cancelled" in out.text


@pytest.mark.anyio
async def test_many_phrasings_find_the_new_form_and_read_requests_do_not():
    cap = cap_with_signup()
    for text in ("register a new account for Ana", "I need to create a customer account", "open a new account"):
        _, it, _, _ = await turn(text, create_session(), cap)
        assert it.intent == "act" and it.write_api == "accounts.signup", text
    for text in ("show open tickets", "show account numbers", "go to orders", "what was revenue last month"):
        ctx = make_ctx(text, create_session(), cap)
        it = await interpret(ctx)
        assert it.intent != "act", text


@pytest.mark.anyio
async def test_existing_forms_still_work_and_are_not_confused_with_the_new_one():
    cap = cap_with_signup()
    _, it, _, _ = await turn("I want to open a ticket for order 1e9e8ef04dae854f6484de3a007b579d it was damaged", create_session(), cap)
    assert it.intent == "act" and it.write_api == "tickets.create"


def test_field_validation_is_driven_by_the_schema():
    state = SIGNUP.body_schema["properties"]["state"]
    assert validate_field("state", state, "rj") == ("RJ", None)
    assert validate_field("state", state, "bahia")[1].startswith("must be one of")
    assert validate_field("email", {"type": "string"}, "a@b.co") == ("a@b.co", None)
    assert validate_field("email", {"type": "string"}, "nope")[1]
    assert validate_field("qty", {"type": "integer", "minimum": 1, "maximum": 5}, "3 units") == (3, None)
    assert validate_field("qty", {"type": "integer", "minimum": 1, "maximum": 5}, "9")[1]
    assert validate_field("password", {"type": "string"}, "x")[0] is None
    assert validate_field("order_id", {"type": "string"}, "abc")[1]                    # id too short
    assert validate_field("note", {"anyOf": [{"type": "string", "maxLength": 5}, {"type": "null"}]}, "toolongvalue")[1]
