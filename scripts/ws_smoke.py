"""Acts as a browser over the REAL WebSocket to prove the protocol end to end (no frontend needed).

    python scripts/ws_smoke.py            # server must be running on localhost:8000

Run 1: an honest browser renders the state and acknowledges it      -> verify must be ok, source = browser
Run 2: a faulty browser claims one extra row                         -> verify must fail with a row_count mismatch
Run 3: a faulty browser drops the filter it was asked to apply       -> verify must fail with applied_filters
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts.ui_state import canonical_series_hash  # noqa: E402

BASE, WS = "http://localhost:8000", "ws://localhost:8000"
PLAN = {"plan": {"intent": {"name": "navigate"}, "steps": [{"tool": "navigate", "args": {
    "page_id": "ops.late_deliveries", "filters": {"customer_state": {"value": ["Rio de Janeiro"]}},
    "date_range": {"preset": "last_month"}}}]}}


async def render(http: httpx.AsyncClient, app: dict, msg: dict, tamper: str | None) -> dict:
    """What a browser does: for every widget of the page, request data under the state and report what it showed."""
    state = msg["state"]
    page = next(p for p in app["pages"] if p["page_id"] == state["page_id"])
    widgets = {w["widget_id"]: w for w in app["widgets"]}
    datasets = {d["dataset_id"]: {f["name"] for f in d["fields"]} for d in app["datasets"]}
    acks = []
    for wid in page["widgets"]:
        w = widgets[wid]
        if w.get("source"):
            applies = lambda fid: fid in w["source"]["filter_params"]  # noqa: E731  (S10)
        else:
            fields = datasets[w["dataset_id"]]
            fmap = {f["filter_id"]: f["field"] for f in page["filters"]}
            applies = lambda fid: fmap.get(fid, fid) in fields  # noqa: E731  (S5)
        filters = {k: v for k, v in state["filters"].items() if applies(k)}
        if tamper == "drop_filter":
            filters = {}
        r = await http.post("/api/widget-data", json={"widget_id": wid, "page_id": state["page_id"], "filters": state["filters"],
                                                      "date_range": state["date_range"], "sort": state["sort"]})
        d = r.json()
        count = d["row_count"] + (1 if tamper == "extra_row" and acks == [] else 0)
        acks.append({"widget_id": wid, "applied_filters": filters, "row_count": count,
                     "series_hash": canonical_series_hash(d["rows"], d["hash_columns"])})
    return {"type": "render_ack", "version": msg["version"], "nonce": msg["nonce"], "route": state["route"], "widgets": acks}


async def run(tamper: str | None) -> dict:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        app = (await http.get("/api/application")).json()
        sid = (await http.post("/api/session")).json()["session_id"]
        async with websockets.connect(f"{WS}/ws/{sid}") as ws:
            init = json.loads(await ws.recv())
            assert init["type"] == "apply_state" and init["nonce"] == "init", init
            await ws.send(json.dumps(await render(http, app, init, None)))
            plan = asyncio.create_task(http.post(f"/api/session/{sid}/plan", json=PLAN))
            while not plan.done():
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 0.5))
                except asyncio.TimeoutError:
                    continue
                if msg["type"] == "apply_state":
                    await ws.send(json.dumps(await render(http, app, msg, tamper)))
            res = plan.result().json()
    verify = next(e for e in res["events"] if e["type"] == "verify")["data"]
    return verify


async def main() -> int:
    bad = 0
    for tamper, expect_ok, field in ((None, True, None), ("extra_row", False, "row_count"), ("drop_filter", False, "applied_filters")):
        v = await run(tamper)
        fields = {m["field"] for m in v["mismatches"]}
        good = v["ok"] == expect_ok and v["source"] == "browser" and (field is None or field in fields)
        bad += not good
        print(f"{'PASS' if good else 'FAIL'}  browser={tamper or 'honest':12s} verify.ok={v['ok']} source={v['source']} mismatches={sorted(fields)}")
    return bad


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
