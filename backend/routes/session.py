"""Session, state and agent-runtime endpoints (not business APIs, so not in the API catalogue).

  POST /api/session                        create a session (initial UI state)
  GET  /api/session/{sid}/state            current UI state
  GET  /api/session/{sid}/context          what is on screen right now (page, filters, widget contents, actions)
  POST /api/session/{sid}/state            the user changed something in the browser (also over the WebSocket)
  POST /api/session/{sid}/undo             {"what": "state" | "write"}  undo the last screen change / last data change
  POST /api/session/{sid}/plan             validate + execute a Plan (what the LLM agent calls); streams events
  POST /api/session/{sid}/confirm          approve / decline a confirm_request
  POST /api/session/{sid}/message          hand a user message to the configured agent (501 until one is set)
  GET  /api/session/{sid}/events?after=N   the event log (polling alternative to the WebSocket)
  GET  /api/session/{sid}/evidence/{id}    one piece of evidence; GET .../canvases lists generated canvases
  POST /api/session/{sid}/fault            simulated-browser fault injection (tests / evaluation)
  WS   /ws/{sid}                           ApplyState <-> RenderAck, agent events
  POST /api/widget-data                    rows for one widget under a UI state (the browser calls this)
  GET  /api/date-presets, GET /api/search
"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from backend import executor, retrieval, sessions
from backend.common import ApiError
from backend.config import app_model
from backend.sessions import FAULTS, create_session, get_session
from backend.views import ViewError, page_context, widget_data
from contracts.actions import AgentConfig, Plan
from contracts.dates import resolve
from contracts.ui_state import ApplyState, DateRange, FilterValue, Sort, UiState

router = APIRouter(tags=["session"])
PRESETS = ["last_7_days", "last_30_days", "last_90_days", "last_12_months", "this_month", "last_month",
           "this_quarter", "last_quarter", "this_year", "year_to_date", "last_year"]

_AGENT = {"agent": None}  # an object with handle(session_id, message, config) -> AsyncIterator[AgentEvent]


def set_agent(agent) -> None:
    _AGENT["agent"] = agent


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WidgetDataRequest(_Body):
    widget_id: str
    page_id: str | None = None
    filters: dict[str, FilterValue] = {}
    date_range: DateRange | None = None
    sort: Sort | None = None


class PlanRequest(_Body):
    plan: Plan
    config: AgentConfig | None = None
    auto_confirm: bool = Field(False, description="approve confirmations automatically (tests / evaluation only)")


class ConfirmRequest(_Body):
    action_id: str
    approve: bool


class MessageRequest(_Body):
    text: str = Field(..., min_length=1, max_length=2000)
    config: AgentConfig | None = None


class UndoRequest(_Body):
    what: Literal["state", "write"] = "state"


class FaultRequest(_Body):
    kind: str


# ------------------------------------------------------------------------------------ widget data / misc
@router.post("/api/widget-data")
async def post_widget_data(req: WidgetDataRequest):
    try:
        d = await widget_data(req.widget_id, req.filters, req.date_range, req.sort, req.page_id)
    except ViewError as e:
        raise ApiError(404 if e.code.startswith("unknown") else 502, e.code, e.message, **e.detail)
    # series_hash is NOT returned: the browser computes its own from what it displays (non-circular verification)
    return {"columns": d["columns"], "hash_columns": d["hash_columns"], "rows": d["rows"], "row_count": d["row_count"],
            "total": d.get("total"), "kind": d["kind"]}


@router.post("/api/deeplink/build")
def deeplink_build(state: UiState):
    from backend import deeplinks

    return {"url": deeplinks.state_to_url(app_model(), state)}


@router.get("/api/deeplink/parse")
def deeplink_parse(url: str):
    from backend import deeplinks

    return {"state": deeplinks.parse_url(app_model(), url).model_dump(by_alias=True)}


@router.get("/api/date-presets")
def date_presets():
    a = app_model().as_of_date
    return {p: dict(zip(("from", "to"), resolve(p, a))) for p in PRESETS}


@router.get("/api/search")
def search(q: str, kinds: str = "page,api,entity", k: int = 5):
    ks = tuple(x for x in kinds.split(",") if x in ("page", "api", "entity"))
    return {"query": q, "results": retrieval.search(q, ks or ("page",), max(1, min(k, 20)))}


# ------------------------------------------------------------------------------------ sessions
@router.post("/api/session")
def post_session():
    s = create_session()
    return {"session_id": s.id, "state": s.state.model_dump(by_alias=True)}


@router.get("/api/session/{sid}/state")
def get_state(sid: str):
    s = get_session(sid)
    return {"state": s.state.model_dump(by_alias=True), "verify": s.last_verify.model_dump() if s.last_verify else None}


@router.get("/api/session/{sid}/context")
async def get_context(sid: str):
    s = get_session(sid)
    ctx = await page_context(s.state)
    ctx["recent_writes"] = s.writes[-5:]
    ctx["canvases"] = [{"canvas_id": k, "kind": v["kind"], "title": v["title"]} for k, v in s.canvases.items()]
    ctx["next_evidence_id"] = f"E{s._ev + 1}"
    return ctx


@router.post("/api/session/{sid}/state")
async def post_state(sid: str, state: UiState):
    s = get_session(sid)
    await sessions.handle_ws_message(s, {"type": "user_state_change", "state": state.model_dump(by_alias=True)})
    return {"state": s.state.model_dump(by_alias=True)}


@router.post("/api/session/{sid}/undo")
async def post_undo(sid: str, body: UndoRequest | None = None):
    s = get_session(sid)
    what = body.what if body else "state"
    if what == "write":
        return await executor.undo_last_write(s)
    r = await sessions.undo_state(s)
    return {"ok": r["ok"], "state": s.state.model_dump(by_alias=True)} if r["ok"] else r


@router.post("/api/session/{sid}/plan")
async def post_plan(sid: str, req: PlanRequest):
    s = get_session(sid)
    start = len(s.events)
    result = await executor.run_plan(s, req.plan, req.config, req.auto_confirm)
    return {**result, "events": [e.model_dump() for e in s.events[start:]]}


@router.post("/api/session/{sid}/confirm")
def post_confirm(sid: str, req: ConfirmRequest):
    s = get_session(sid)
    if not sessions.resolve_confirm(s, req.action_id, req.approve):
        raise ApiError(404, "confirmation_not_found", f"No pending confirmation '{req.action_id}'.")
    return {"ok": True, "approved": req.approve}


@router.post("/api/session/{sid}/message", status_code=202)
async def post_message(sid: str, req: MessageRequest):
    s = get_session(sid)
    agent = _AGENT["agent"]
    if agent is None:
        raise ApiError(501, "agent_not_configured", "No agent is attached to the backend yet. "
                                                    "POST /api/session/{sid}/plan runs a Plan directly.")
    cfg = req.config or s.config

    async def run():
        try:
            async for ev in agent.handle(sid, req.text, cfg):
                if ev.type not in ("action", "verify", "evidence", "canvas", "confirm_request"):
                    await sessions.emit(s, ev.type, ev.data)
        except Exception as e:  # noqa: BLE001
            await sessions.emit(s, "error", {"code": "agent_failed", "message": f"{type(e).__name__}: {e}", "recoverable": False})

    asyncio.create_task(run())
    return {"accepted": True}


@router.get("/api/session/{sid}/events")
def get_events(sid: str, after: int = 0, limit: int = 200):
    s = get_session(sid)
    return {"events": [e.model_dump() for e in s.events if e.seq > after][:limit]}


@router.get("/api/session/{sid}/evidence/{evidence_id}")
def get_evidence(sid: str, evidence_id: str):
    ev = get_session(sid).evidence.get(evidence_id)
    if ev is None:
        raise ApiError(404, "evidence_not_found", f"No evidence '{evidence_id}'.")
    return ev.model_dump()


@router.get("/api/session/{sid}/canvases")
def get_canvases(sid: str):
    return {"canvases": list(get_session(sid).canvases.values())}


@router.get("/api/session/{sid}/stats")
def get_stats(sid: str):
    """Per-session measurements for the KPIs (docs/KPI_PLAN.md): effort, safety gates, latency, usefulness."""
    s = get_session(sid)
    st, runs = s.stats, s.runs
    ms = sorted(r["elapsed_ms"] for r in runs)
    pick = lambda p: ms[min(len(ms) - 1, int(p * len(ms)))] if ms else None  # noqa: E731
    ver = st["verify_passes"] + st["verify_failures"]
    acts = st["agent_steps"] + st["manual_changes"]
    return {
        **st, "runs": len(runs), "run_success_rate": (sum(r["ok"] for r in runs) / len(runs)) if runs else None,
        "avg_steps_per_run": (sum(r["steps"] for r in runs) / len(runs)) if runs else None,
        "latency_ms": {"p50": pick(0.5), "p90": pick(0.9), "mean": (sum(ms) / len(ms)) if ms else None},
        "verify_pass_rate": (st["verify_passes"] / ver) if ver else None,
        "agent_share_of_actions": (st["agent_steps"] / acts) if acts else None,
        "issues_caught_by_validator": s.issue_counts, "writes_applied": len(s.writes) + st["writes_undone"],
        "feedback": {"up": sum(f["rating"] == "up" for f in s.feedback), "down": sum(f["rating"] == "down" for f in s.feedback)},
    }


class FeedbackRequest(_Body):
    rating: Literal["up", "down"]
    comment: str | None = Field(None, max_length=500)
    event_seq: int | None = Field(None, description="The agent event (answer) the rating is about")


@router.post("/api/session/{sid}/feedback", status_code=201)
def post_feedback(sid: str, req: FeedbackRequest):
    """Perceived usefulness of the assistant (K7): a thumbs up/down, optionally with a comment."""
    import time
    from backend.config import ROOT

    s = get_session(sid)
    rec = {"session": sid, "ts": time.time(), "rating": req.rating, "comment": req.comment, "event_seq": req.event_seq,
           "page_id": s.state.page_id}
    s.feedback.append(rec)
    d = ROOT / "logs"
    d.mkdir(exist_ok=True)
    with (d / "feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return {"ok": True, "total": len(s.feedback)}


@router.post("/api/session/{sid}/fault")
def post_fault(sid: str, req: FaultRequest):
    if req.kind not in FAULTS:
        raise ApiError(422, "invalid_value", f"Unknown fault '{req.kind}'.", allowed=list(FAULTS))
    get_session(sid).fault = req.kind
    return {"fault": req.kind}


# ------------------------------------------------------------------------------------ WebSocket
@router.websocket("/ws/{sid}")
async def ws_endpoint(websocket: WebSocket, sid: str):
    s = sessions.SESSIONS.get(sid)
    if s is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    s.ws = websocket
    try:  # first render: tell the browser what to show
        await websocket.send_text(ApplyState(version=s.state.version, nonce="init", state=s.state, cause="connect")
                                  .model_dump_json(by_alias=True))
        while True:
            await sessions.handle_ws_message(s, json.loads(await websocket.receive_text()))
    except WebSocketDisconnect:
        pass
    finally:
        if s.ws is websocket:
            s.ws = None
