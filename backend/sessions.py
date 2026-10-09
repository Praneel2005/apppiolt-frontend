"""Sessions: authoritative UI state, the backend<->browser protocol, events, evidence, confirmations.

One Session per user. The backend owns the UiState; the browser renders it and answers every ApplyState
with a RenderAck computed from what it actually requested and displayed (contracts/ui_state.py).
If no browser is connected (tests, evaluation, scripts) a SimulatedBrowser answers instead; its acks
are labelled source="simulated_browser" and it can inject faults to prove the verifier catches them.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend import verifier
from backend.config import app_model
from backend.views import ViewError, widget_data
from contracts.actions import AgentConfig, Evidence
from contracts.dates import resolve
from contracts.events import AgentEvent, AgentEventEnvelope
from contracts.metadata import Application, Page
from contracts.query import VerifyResult
from contracts.ui_state import (ApplyState, DateRange, FilterValue, RenderAck, Sort, UiState, WidgetAck)

ACK_TIMEOUT_S = 6.0
CONFIRM_TIMEOUT_S = 180.0
FAULTS = ("none", "drop_filter", "empty_widget", "wrong_route", "stale_data")


@dataclass
class Session:
    id: str
    state: UiState
    history: list[UiState] = field(default_factory=list)  # for undo of navigation / state changes
    ws: Any = None
    pending_acks: dict[str, asyncio.Future] = field(default_factory=dict)
    last_ack: RenderAck | None = None
    last_verify: VerifyResult | None = None
    events: list[AgentEvent] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    canvases: dict[str, dict] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)  # action ids applied in this session (newest last)
    # ---- measurement for the KPIs (see docs/KPI_PLAN.md): manual vs agent work, safety gates, latency, usefulness
    stats: dict = field(default_factory=lambda: {
        "manual_changes": 0, "agent_steps": 0, "plan_runs": 0, "validation_failures": 0, "verify_passes": 0,
        "verify_failures": 0, "confirmations_approved": 0, "confirmations_declined": 0, "writes_undone": 0})
    issue_counts: dict = field(default_factory=dict)  # validator issue code -> count (references the planner invented)
    runs: list[dict] = field(default_factory=list)  # one record per plan run: elapsed_ms, steps, ok, stage
    feedback: list[dict] = field(default_factory=list)
    confirms: dict[str, asyncio.Future] = field(default_factory=dict)
    fault: str = "none"
    config: AgentConfig = field(default_factory=AgentConfig)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created: float = field(default_factory=time.time)
    _seq: int = 0
    _ev: int = 0
    _confirm: int = 0

    def next_evidence_id(self) -> str:
        self._ev += 1
        return f"E{self._ev}"

    def next_confirm_id(self) -> str:
        self._confirm += 1
        return f"C{self._confirm}"


SESSIONS: dict[str, Session] = {}


# ------------------------------------------------------------------------------------ state construction
def default_range(app: Application, page: Page) -> DateRange | None:
    pre = (page.default_state.get("date_range") or {}).get("preset")
    if not pre:
        return None
    f, t = resolve(pre, app.as_of_date)
    return DateRange.model_validate({"from": f, "to": t, "preset": pre})


def state_for_page(app: Application, page: Page, filters: dict[str, FilterValue] | None = None,
                   date_range: DateRange | None = None, sort: Sort | None = None) -> UiState:
    """State after navigating to a page (shared semantics S1: navigation resets filters, date and sort
    to the page defaults; anything passed explicitly is applied on top)."""
    return UiState(route=page.route, page_id=page.page_id, filters=filters or {},
                   date_range=date_range if date_range is not None else default_range(app, page), sort=sort)


def create_session() -> Session:
    app = app_model()
    first = app.pages[0]
    s = Session(id=uuid.uuid4().hex[:12], state=state_for_page(app, first))
    SESSIONS[s.id] = s
    return s


def get_session(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if s is None:
        from backend.common import ApiError

        raise ApiError(404, "session_not_found", f"No session '{sid}'.")
    return s


# ------------------------------------------------------------------------------------ events
async def emit(s: Session, type_: str, data: dict | None = None) -> AgentEvent:
    s._seq += 1
    ev = AgentEvent(seq=s._seq, type=type_, data=data or {})
    s.events.append(ev)
    if s.ws is not None:
        try:
            await s.ws.send_text(AgentEventEnvelope(event=ev).model_dump_json())
        except Exception:  # noqa: BLE001  (browser went away; events stay available via REST)
            s.ws = None
    return ev


# ------------------------------------------------------------------------------------ simulated browser
async def simulated_ack(s: Session, state: UiState, nonce: str) -> RenderAck:
    """What a correct browser would report, with optional injected faults (for tests and evaluation)."""
    app = app_model()
    page = app.page(state.page_id)
    filters = dict(state.filters)
    if s.fault == "drop_filter" and filters:
        filters.pop(sorted(filters)[0])
    acks = []
    for wid in page.widgets:
        d = await widget_data(wid, filters, state.date_range, state.sort, state.page_id, app)
        rows, count, hsh = d["rows"], d["row_count"], d["series_hash"]
        if s.fault == "empty_widget":
            from contracts.ui_state import canonical_series_hash
            rows, count, hsh = [], 0, canonical_series_hash([], d["hash_columns"])
        acks.append(WidgetAck(widget_id=wid, applied_filters=d["applied_filters"],
                              applied_date_range=d["applied_date_range"], applied_sort=d["applied_sort"],
                              row_count=count, series_hash=hsh))
    route = "/wrong" if s.fault == "wrong_route" else state.route
    return RenderAck(version=state.version, nonce=nonce, route=route, widgets=acks)


async def push_state(s: Session, new: UiState, cause: str) -> dict:
    """Make `new` the session state, have the browser render it, and verify the acknowledgement."""
    app = app_model()
    new = new.model_copy(update={"version": s.state.version + 1})
    nonce = uuid.uuid4().hex[:8]
    s.history.append(s.state)
    s.state = new
    source = "browser"
    if s.ws is not None:
        fut = asyncio.get_running_loop().create_future()
        s.pending_acks[nonce] = fut
        try:
            await s.ws.send_text(ApplyState(version=new.version, nonce=nonce, state=new, cause=cause)
                                 .model_dump_json(by_alias=True))
            ack = await asyncio.wait_for(fut, ACK_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            s.pending_acks.pop(nonce, None)
            s.last_verify = VerifyResult(ok=False, mismatches=[{"widget_id": None, "field": "no_ack",
                                                                 "expected": "render_ack", "actual": type(e).__name__}])
            return {"verify": s.last_verify, "ack": None, "source": source}
        finally:
            s.pending_acks.pop(nonce, None)
    else:
        source = "simulated_browser"
        ack = await simulated_ack(s, new, nonce)
    s.last_ack = ack
    s.last_verify = await verifier.verify(app, new, ack)
    s.stats["verify_passes" if s.last_verify.ok else "verify_failures"] += 1
    return {"verify": s.last_verify, "ack": ack, "source": source}


async def undo_state(s: Session) -> dict:
    """Go back to the previous UI state (the previous screen, filters and date range)."""
    if not s.history:
        return {"ok": False, "message": "Nothing to undo."}
    prev = s.history.pop()
    # push_state appends the current state to history; undo must not grow it, so drop that entry after
    r = await push_state(s, prev, "undo")
    s.history.pop()
    return {"ok": r["verify"].ok, "state": s.state, "verify": r["verify"]}


# ------------------------------------------------------------------------------------ incoming WS messages
async def handle_ws_message(s: Session, msg: dict) -> None:
    t = msg.get("type")
    if t == "render_ack":
        ack = RenderAck.model_validate(msg)
        fut = s.pending_acks.get(ack.nonce)
        if fut and not fut.done():
            fut.set_result(ack)
        else:
            s.last_ack = ack
    elif t == "user_state_change":
        st = UiState.model_validate(msg["state"])
        s.stats["manual_changes"] += 1  # K7: how much the user navigates by hand
        s.history.append(s.state)
        s.state = st.model_copy(update={"version": s.state.version + 1})
    else:
        await emit(s, "error", {"code": "unknown_message", "message": f"Unknown message type {t!r}",
                                "recoverable": True})


def resolve_confirm(s: Session, action_id: str, approve: bool) -> bool:
    fut = s.confirms.get(action_id)
    if fut is None or fut.done():
        return False
    fut.set_result(bool(approve))
    return True


async def ask_confirmation(s: Session, summary: str, payload: dict, auto: bool = False) -> bool:
    """Ask the user to approve something. Emits confirm_request and waits for POST .../confirm."""
    action_id = s.next_confirm_id()
    fut = asyncio.get_running_loop().create_future()
    s.confirms[action_id] = fut
    await emit(s, "confirm_request", {"action_id": action_id, "summary": summary, "auto_confirmed": auto, **payload})
    if auto:
        s.confirms.pop(action_id, None)
        s.stats["confirmations_approved"] += 1
        return True
    try:
        ok = await asyncio.wait_for(fut, CONFIRM_TIMEOUT_S)
    except asyncio.TimeoutError:
        ok = False
    finally:
        s.confirms.pop(action_id, None)
    s.stats["confirmations_approved" if ok else "confirmations_declined"] += 1
    return ok
