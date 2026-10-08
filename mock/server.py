"""AppPilot MOCK backend for frontend development (no Postgres, no LLM).

Same endpoints and WebSocket protocol as the real backend (docs/FRONTEND_BRIEF.md §4), backed by a
SQLite copy of the Olist fact tables. The "agent" is SCRIPTED (keyword matching), only so the chat
UI receives realistic event streams. Do not judge agent quality on it.

Run from the repo root:
    pip install -r mock/requirements.txt
    python -m uvicorn mock.server:app --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import gzip
import json
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from contracts.dates import resolve
from contracts.metadata import Application
from contracts.ui_state import ApplyState, RenderAck, UiState, canonical_series_hash

ROOT = Path(__file__).resolve().parent.parent
APP_FILE = ROOT / "data" / "olist" / "application.json"
DB_FILE = Path(__file__).resolve().parent / "olist_mock.sqlite"
PRESETS = ["last_7_days", "last_30_days", "last_90_days", "last_12_months", "this_month", "last_month",
           "this_quarter", "last_quarter", "this_year", "year_to_date", "last_year"]
ACK_TIMEOUT_S = 6.0

if not DB_FILE.exists():
    with gzip.open(str(DB_FILE) + ".gz", "rb") as src, open(DB_FILE, "wb") as dst:
        shutil.copyfileobj(src, dst)

APP = Application.model_validate(json.loads(APP_FILE.read_text(encoding="utf-8")))
DB = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, check_same_thread=False)
DB.row_factory = sqlite3.Row

app = FastAPI(title="AppPilot mock backend")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

FAULT = {"kind": "none"}  # none | drop_filter | empty_widget | slow_widget


# ----------------------------------------------------------------------------- data
class WidgetDataRequest(BaseModel):
    widget_id: str
    filters: dict = {}  # filter_id -> {"op": "in", "value": [...]}
    date_range: dict | None = None  # {"from": "...", "to": "...", "preset": ...}
    sort: dict | None = None  # {"field": "...", "dir": "asc|desc"}


def query_widget(req: WidgetDataRequest, apply_faults: bool = True) -> dict:
    w = APP.widget(req.widget_id)
    if w is None:
        raise HTTPException(404, f"unknown widget {req.widget_id}")
    ds = APP.dataset(w.dataset_id)
    fields = {f.name for f in ds.fields}
    metrics = {m.metric_id: m for m in ds.metrics}
    cols = list(w.dimensions) + list(w.metrics)
    select = [f'"{d}"' for d in w.dimensions] + [f'({metrics[m].sql}) AS "{m}"' for m in w.metrics]
    where, params = [], []
    filters = dict(req.filters)
    if apply_faults and FAULT["kind"] == "drop_filter" and filters:
        filters.pop(sorted(filters)[0])
    for fid, fv in filters.items():  # shared semantics S5: only filters whose field is in this dataset
        if fid in fields and fv.get("value"):
            where.append(f'"{fid}" IN ({",".join("?" * len(fv["value"]))})')
            params += list(fv["value"])
    if req.date_range and ds.time_field:
        where.append(f'"{ds.time_field}" BETWEEN ? AND ?')
        params += [req.date_range["from"], req.date_range["to"]]
    sql = f"SELECT {', '.join(select)} FROM {ds.table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if w.dimensions:
        sql += " GROUP BY " + ", ".join(f'"{d}"' for d in w.dimensions)
    if req.sort and req.sort.get("field") in cols:
        sql += f' ORDER BY "{req.sort["field"]}" {"ASC" if req.sort.get("dir") == "asc" else "DESC"}'
    elif "order_month" in w.dimensions:
        sql += ' ORDER BY "order_month" ASC'
    elif w.metrics and w.dimensions:
        sql += f' ORDER BY "{w.metrics[0]}" DESC'
    rows = [dict(r) for r in DB.execute(sql, params).fetchall()]
    if apply_faults and FAULT["kind"] == "empty_widget":
        rows = []
    return {"columns": cols, "rows": rows, "row_count": len(rows)}


@app.get("/api/application")
def get_application():
    return json.loads(APP_FILE.read_text(encoding="utf-8"))


@app.get("/api/date-presets")
def date_presets():
    return {p: dict(zip(("from", "to"), resolve(p, APP.as_of_date))) for p in PRESETS}


@app.post("/api/widget-data")
async def widget_data(req: WidgetDataRequest):
    if FAULT["kind"] == "slow_widget":
        await asyncio.sleep(8)
    return query_widget(req)


@app.post("/api/debug/fault")
def set_fault(body: dict):
    FAULT["kind"] = body.get("kind", "none")
    return FAULT


# ----------------------------------------------------------------------------- sessions
class Session:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        first = APP.pages[0]
        frm, to = resolve(first.default_state["date_range"]["preset"], APP.as_of_date)
        self.state = UiState(route=first.route, page_id=first.page_id,
                             date_range={"from": frm, "to": to, "preset": first.default_state["date_range"]["preset"]})
        self.ws: WebSocket | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.undo: list[UiState] = []
        self.confirm: asyncio.Future | None = None
        self.last_ack: RenderAck | None = None
        self.seq = 0


SESSIONS: dict[str, Session] = {}


def get_session(sid: str) -> Session:
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown session")
    return SESSIONS[sid]


@app.post("/api/session")
def create_session():
    s = Session()
    SESSIONS[s.id] = s
    return {"session_id": s.id, "state": s.state.model_dump(by_alias=True)}


def expected_for(state: UiState) -> dict:
    """What every widget on the page SHOULD show for this state (no faults applied)."""
    page = APP.page(state.page_id)
    out = {}
    for wid in page.widgets:
        w = APP.widget(wid)
        fields = {f.name for f in APP.dataset(w.dataset_id).fields}
        applied = {k: v.model_dump() for k, v in state.filters.items() if k in fields}
        req = WidgetDataRequest(widget_id=wid, filters=applied,
                                date_range=state.date_range.model_dump(by_alias=True) if state.date_range else None)
        data = query_widget(req, apply_faults=False)
        out[wid] = {"applied_filters": applied, "row_count": data["row_count"],
                    "series_hash": canonical_series_hash(data["rows"], data["columns"])}
    return out


def verify(state: UiState, ack: RenderAck) -> dict:
    exp = expected_for(state)
    mismatches = []
    if ack.route != state.route:
        mismatches.append({"widget_id": None, "field": "route", "expected": state.route, "actual": ack.route})
    got = {w.widget_id: w for w in ack.widgets}
    for wid, e in exp.items():
        a = got.get(wid)
        if a is None:
            mismatches.append({"widget_id": wid, "field": "missing_ack", "expected": "ack", "actual": None})
            continue
        a_filters = {k: v.model_dump() for k, v in a.applied_filters.items()}
        for field, actual in (("applied_filters", a_filters), ("row_count", a.row_count), ("series_hash", a.series_hash)):
            if actual != e[field]:
                mismatches.append({"widget_id": wid, "field": field, "expected": e[field], "actual": actual})
    return {"ok": not mismatches, "mismatches": mismatches}


async def push_state(s: Session, state: UiState, cause: str) -> dict:
    if s.ws is None:
        return {"error": "browser not connected"}
    state = state.model_copy(update={"version": s.state.version + 1})
    nonce = uuid.uuid4().hex[:8]
    fut = asyncio.get_running_loop().create_future()
    s.pending[nonce] = fut
    s.undo.append(s.state)
    s.state = state
    await s.ws.send_text(ApplyState(version=state.version, nonce=nonce, state=state, cause=cause).model_dump_json(by_alias=True))
    try:
        ack: RenderAck = await asyncio.wait_for(fut, ACK_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"error": f"no render_ack within {ACK_TIMEOUT_S}s"}
    finally:
        s.pending.pop(nonce, None)
    s.last_ack = ack
    return {"ack": ack.model_dump(by_alias=True), "verify": verify(state, ack)}


@app.websocket("/ws/{sid}")
async def ws_endpoint(websocket: WebSocket, sid: str):
    if sid not in SESSIONS:
        await websocket.close(code=4404)
        return
    s = SESSIONS[sid]
    await websocket.accept()
    s.ws = websocket
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "render_ack":
                ack = RenderAck.model_validate(msg)
                fut = s.pending.get(ack.nonce)
                if fut and not fut.done():
                    fut.set_result(ack)
                else:
                    s.last_ack = ack
            elif msg.get("type") == "user_state_change":
                s.undo.append(s.state)
                st = UiState.model_validate(msg["state"])
                s.state = st.model_copy(update={"version": s.state.version + 1})
    except WebSocketDisconnect:
        s.ws = None


class ApplyBody(BaseModel):
    session_id: str
    state: dict


@app.post("/api/debug/apply_state")
async def debug_apply_state(body: ApplyBody):
    """Push a state to the browser, wait for its render_ack, and verify it. Use this to test your ack."""
    s = get_session(body.session_id)
    return await push_state(s, UiState.model_validate(body.state), "debug")


@app.get("/api/debug/expected/{sid}")
def debug_expected(sid: str):
    return expected_for(get_session(sid).state)


@app.post("/api/session/{sid}/undo")
async def undo(sid: str):
    s = get_session(sid)
    if not s.undo:
        return {"error": "nothing to undo"}
    prev = s.undo.pop()
    res = await push_state(s, prev, "undo")
    if s.undo:
        s.undo.pop()  # push_state stacked the undone state; drop it
    return res


@app.post("/api/session/{sid}/confirm")
def confirm(sid: str, body: dict):
    s = get_session(sid)
    if s.confirm and not s.confirm.done():
        s.confirm.set_result(bool(body.get("approve")))
        return {"ok": True}
    return {"ok": False, "error": "no pending confirmation"}


# ----------------------------------------------------------------------------- scripted agent (MOCK)
def _match_values(text: str, page) -> dict:
    found = {}
    low = f" {text.lower()} "
    for f in page.filters:
        if not f.allowed_values:
            continue
        for v in f.allowed_values:
            names = [v] + f.synonyms.get(v, [])
            if any(re.search(rf"(?<![a-z]){re.escape(n.lower())}(?![a-z])", low) for n in names if len(n) > 1):
                found.setdefault(f.filter_id, []).append(v)
    return found


def _pick_pages(text: str, k: int = 3):
    words = set(re.findall(r"[a-z]+", text.lower())) - {"the", "a", "of", "for", "by", "and", "show", "me", "in", "what", "is"}
    scored = []
    for p in APP.pages:
        hay = (p.title + " " + p.description + " " + " ".join(p.keywords)).lower()
        scored.append((sum(1 for w in words if w in hay), p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:k]]


class MessageBody(BaseModel):
    text: str
    config: dict = {}


async def emit(s: Session, type_: str, data: dict):
    s.seq += 1
    if s.ws:
        await s.ws.send_text(json.dumps({"type": "agent_event", "event": {"seq": s.seq, "type": type_, "data": data}}))
    await asyncio.sleep(0.35)


async def scripted_agent(s: Session, text: str, config: dict):
    low = text.lower()
    if any(w in low for w in ("delete", "drop", "remove all")):
        await emit(s, "understanding", {"intent": "unsafe_action", "slots": {}})
        await emit(s, "answer", {"text": "I can't delete or change data. I can only navigate, filter and analyse.",
                                 "citations": [], "deep_links": []})
        await emit(s, "done", {"steps": 0, "replans": 0, "elapsed_ms": 700})
        return
    intent = "compare_periods" if "compare" in low else "explain_change" if "why" in low else "set_state"
    pages = _pick_pages(text)
    page = pages[0]
    await emit(s, "understanding", {"intent": intent, "slots": {"text": text}})
    await emit(s, "retrieval", {"candidates": [{"page_id": p.page_id, "title": p.title} for p in pages]})
    filters = {fid: {"op": "in", "value": vals} for fid, vals in _match_values(text, page).items()}
    preset = next((p for p in PRESETS if p.replace("_", " ") in low), page.default_state["date_range"]["preset"])
    frm, to = resolve(preset, APP.as_of_date)
    steps = [{"tool": "navigate", "args": {"page_id": page.page_id}}]
    steps += [{"tool": "set_filter", "args": {"filter_id": k, "op": "in", "value": v["value"]}} for k, v in filters.items()]
    steps.append({"tool": "set_date_range", "args": {"preset": preset}})
    await emit(s, "plan", {"steps": steps})
    await emit(s, "validation", {"ok": True, "issues": []})
    if config.get("confirm_mode"):
        s.confirm = asyncio.get_running_loop().create_future()
        await emit(s, "confirm_request", {"action_id": uuid.uuid4().hex[:8],
                                          "summary": f"Open '{page.title}' with {len(filters)} filter(s), {preset.replace('_', ' ')}"})
        try:
            approved = await asyncio.wait_for(s.confirm, 120)
        except asyncio.TimeoutError:
            approved = False
        if not approved:
            await emit(s, "answer", {"text": "Okay, I did not change anything.", "citations": [], "deep_links": []})
            await emit(s, "done", {"steps": 0, "replans": 0, "elapsed_ms": 0})
            return
    new_state = UiState(route=page.route, page_id=page.page_id, filters=filters,
                        date_range={"from": frm, "to": to, "preset": preset})
    for st in steps:
        await emit(s, "action", {"tool": st["tool"], "args": st["args"], "status": "running"})
    res = await push_state(s, new_state, f"agent: {text[:60]}")
    if "error" in res:
        await emit(s, "error", {"code": "timeout", "message": res["error"], "recoverable": True})
        await emit(s, "done", {"steps": len(steps), "replans": 0, "elapsed_ms": 0})
        return
    await emit(s, "verify", res["verify"])
    w = APP.widget(page.widgets[0])
    total = query_widget(WidgetDataRequest(widget_id=w.widget_id, filters=filters,
                                           date_range={"from": frm, "to": to}), apply_faults=False)
    metric = APP.dataset(w.dataset_id)
    m = next(mm for mm in metric.metrics if mm.metric_id == w.metrics[0])
    top = total["rows"][0] if total["rows"] else None
    ev = {"evidence_id": "e1", "kind": "widget_read", "source": w.widget_id, "query": None,
          "values": {"top_row": top, "rows": total["row_count"], "metric": m.metric_id, "unit": m.unit}}
    await emit(s, "evidence", ev)
    if top:
        label = ", ".join(str(top[d]) for d in w.dimensions) or "total"
        text_out = f"[MOCK] On '{page.title}' ({frm} to {to}), the top entry is {label} with {m.title.lower()} {top[m.metric_id]:,.2f} {m.unit} [e1]."
    else:
        text_out = f"[MOCK] '{page.title}' shows no data for {frm} to {to} [e1]."
    await emit(s, "answer", {"text": text_out, "citations": ["e1"], "deep_links": [page.route]})
    await emit(s, "done", {"steps": len(steps), "replans": 0, "elapsed_ms": 0})


@app.post("/api/session/{sid}/message", status_code=202)
async def message(sid: str, body: MessageBody):
    s = get_session(sid)
    asyncio.create_task(scripted_agent(s, body.text, body.config))
    return {"accepted": True}
