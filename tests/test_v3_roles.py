"""Roles: which pages, writes and data a signed-in role may use, enforced on the server and respected by the agent."""
from types import SimpleNamespace

import pytest

from backend import access
from backend.common import ApiError


@pytest.fixture
def as_role():
    tokens = []

    def setter(rid: str) -> None:
        tokens.append(access.current_role.set(rid))

    yield setter
    for tk in reversed(tokens):
        try:
            access.current_role.reset(tk)
        except ValueError:       # set inside an async test's own context: that context ends with the test, nothing leaks
            pass


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(not _db_ok(), reason="database not available")
page = lambda d: SimpleNamespace(directory=d, title=d)  # noqa: E731


# ------------------------------------------------------------------------------------ the role rules (no database)
def test_roles_are_data_with_an_unrestricted_admin_and_a_safe_fallback(as_role):
    ids = {r["id"] for r in access.list_roles()}
    assert {"admin", "ops_manager", "analyst", "south_analyst"} <= ids
    assert not access.is_restricted("admin") and access.is_restricted("analyst")
    as_role("no-such-role")
    assert access.role_id() == access.default_role()             # an unknown role is the default, never an invented one


def test_pages_writes_reads_and_scope_follow_the_role(as_role):
    assert access.page_allowed(page("ops_orders"), "admin") and access.page_allowed(page("ops_orders"), "ops_manager")
    assert not access.page_allowed(page("ops_orders"), "analyst") and access.page_allowed(page("sales"), "analyst")
    assert access.page_allowed(page("sales"), "ops_manager")
    assert access.write_allowed("tickets.create", "ops_manager") and not access.write_allowed("tickets.create", "analyst")
    assert access.read_allowed("metrics.query", "analyst") and not access.read_allowed("orders.get", "analyst")
    assert access.scope("south_analyst") == {"customer_region": ["South"]} and access.scope("admin") == {}


def test_a_scope_that_cannot_be_applied_refuses_access_instead_of_widening_it(as_role):
    as_role("south_analyst")
    assert access.scope_filters(["customer_region", "price"]) == {"customer_region": ["South"]}
    with pytest.raises(ApiError) as e:
        access.scope_filters(["price", "order_date"])
    assert e.value.status == 403 and "customer_region" in e.value.message
    as_role("admin")
    assert access.scope_filters(["price"]) == {}


def test_writes_and_undo_are_stopped_for_a_read_only_role(as_role):
    from backend.writes import WriteOpts, run_write, undo_action
    as_role("analyst")
    with pytest.raises(ApiError) as e:
        run_write("tickets.create", WriteOpts(dry_run=True, x_actor="user"), {}, lambda conn, ch: {})
    assert e.value.status == 403 and e.value.code == "forbidden_write"
    with pytest.raises(ApiError):
        undo_action("ACT-000001", "user")


def test_the_role_header_sets_who_is_asking_and_the_default_applies_without_it():
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        assert c.get("/api/me").json()["id"] == access.default_role()
        assert c.get("/api/me", headers={"X-Role": "south_analyst"}).json()["scope"] == {"customer_region": ["South"]}
        roles = c.get("/api/roles").json()
        assert roles["default"] == "admin" and any(r["id"] == "analyst" for r in roles["roles"])


# ------------------------------------------------------------------------------------ data scope and page access (database)
@needs_db
def test_the_data_scope_is_added_to_every_metric_query(as_role):
    from datetime import date

    from backend.config import application
    from backend.db import fetch_one
    from backend.routes.metrics import MetricQuery, metrics_query
    ds = next(d for d in application()["datasets"] if d["dataset_id"] == "ds_order_items")
    sql_metric = next(m["sql"] for m in ds["metrics"] if m["metric_id"] == "revenue")
    base = f"FROM {ds['table']} WHERE {ds['time_field']} BETWEEN '2018-07-01' AND '2018-07-31'"
    south = float(fetch_one(f"SELECT ({sql_metric}) AS v {base} AND customer_region = 'South'")["v"])
    allr = float(fetch_one(f"SELECT ({sql_metric}) AS v {base}")["v"])
    q = MetricQuery(metric="revenue", date_from=date(2018, 7, 1), date_to=date(2018, 7, 31))
    as_role("admin")
    assert abs(metrics_query(q)["total"] - allr) < 0.01
    as_role("south_analyst")
    assert abs(metrics_query(q)["total"] - south) < 0.01 and south < allr           # independent SQL agrees; the scope really narrowed it
    q_north = MetricQuery(metric="revenue", date_from=date(2018, 7, 1), date_to=date(2018, 7, 31), filters={"customer_region": ["North"]})
    assert not metrics_query(q_north)["total"]                                       # the user's own filter cannot widen the scope


@needs_db
@pytest.mark.anyio
async def test_widgets_are_scoped_and_pages_outside_the_role_are_closed(as_role):
    from backend.config import app_model
    from backend.views import widget_data
    app = app_model()
    as_role("south_analyst")
    kpi = await widget_data("sales.revenue.kpi", {}, None, None, "sales.revenue", app)
    as_role("admin")
    full = await widget_data("sales.revenue.kpi", {}, None, None, "sales.revenue", app)
    assert float(kpi["rows"][0]["revenue"]) < float(full["rows"][0]["revenue"])
    as_role("analyst")
    ops_widget = app.page("ops.orders").widgets[0]
    with pytest.raises(ApiError) as e:
        await widget_data(ops_widget, {}, None, None, "ops.orders", app)
    assert e.value.status == 403 and e.value.code == "forbidden_page"


@needs_db
def test_the_validator_refuses_navigation_to_a_page_the_role_cannot_open(as_role):
    from backend import executor
    from backend.config import app_model
    from backend.sessions import create_session
    from backend.validator import validate
    from contracts.actions import Intent, Plan, ToolCall
    app, s = app_model(), create_session()
    plan = Plan(intent=Intent(name="navigate"), steps=[ToolCall(tool="navigate", args={"page_id": "ops.tickets"})])
    as_role("admin")
    assert validate(app, plan, s.state, executor._catalog()).ok
    as_role("analyst")
    r = validate(app, plan, s.state, executor._catalog())
    assert not r.ok and r.issues[0].code == "disallowed_tool" and "cannot open" in r.issues[0].message


@needs_db
def test_a_new_session_starts_on_a_page_the_role_may_open(as_role):
    from backend.config import app_model
    from backend.sessions import create_session
    as_role("analyst")
    pid = create_session().state.page_id
    assert access.page_allowed(app_model().page(pid))
    as_role("admin")
    assert create_session().state.page_id == app_model().pages[0].page_id


# ------------------------------------------------------------------------------------ the agent respects the role (database)
async def ask(text: str):
    from agent.agent import AppPilotAgent
    from backend.sessions import create_session
    from contracts.actions import AgentConfig
    s = create_session()
    events = [e async for e in AppPilotAgent(use_llm=False).handle(s.id, text, AgentConfig(open_supporting_view=False))]
    ans = next(e for e in events if e.type == "answer")
    return s, events, ans.data


@needs_db
@pytest.mark.anyio
async def test_the_agent_answers_within_the_scope_and_says_so(as_role):
    from agent.reasoning.facts import fmt_number
    from backend.db import fetch_one
    from backend.config import application
    ds = next(d for d in application()["datasets"] if d["dataset_id"] == "ds_order_items")
    sql_metric = next(m["sql"] for m in ds["metrics"] if m["metric_id"] == "revenue")
    base = f"FROM {ds['table']} WHERE {ds['time_field']} BETWEEN '2018-07-01' AND '2018-07-31'"
    south = float(fetch_one(f"SELECT ({sql_metric}) AS v {base} AND customer_region = 'South'")["v"])
    as_role("south_analyst")
    s, events, data = await ask("what was revenue in July 2018")
    assert fmt_number(south, "BRL") in data["text"]                                  # the number is the South-only figure
    assert "customer region = South" in data["text"] and data["role"] == "south_analyst"


@needs_db
@pytest.mark.anyio
async def test_the_agent_explains_a_refusal_instead_of_failing_or_obeying(as_role):
    as_role("analyst")
    s, events, data = await ask("open support tickets")
    assert s.state.page_id != "ops.tickets" and "cannot open" in data["text"] and data["status"] == "INFO"
    s, events, data = await ask("create a support ticket for order 1e9e8ef04dae854f6484de3a007b579d it was damaged")
    assert "cannot make changes" in data["text"] and not any(e.type == "form_open" for e in events)
    as_role("ops_manager")
    s, events, data = await ask("create a support ticket for order 1e9e8ef04dae854f6484de3a007b579d it was damaged")
    assert any(e.type == "form_open" for e in events)                                # the role that may write gets the form
