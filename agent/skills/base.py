"""Shared skill plumbing: results, canvas emission, and the validated atomic navigate (with one repair attempt)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend import executor
from backend.config import app_model
from backend.deeplinks import nav_args_from_url
from backend.sessions import emit
from backend.validator import validate
from contracts.actions import Intent, Plan, ToolCall
from contracts.canvas import CanvasSpec, FormSpec
from contracts.metadata import ChartSpec, Column

logger = logging.getLogger("agent.skills")


@dataclass
class SkillResult:
    text: str = ""
    citations: list[str] = field(default_factory=list)
    deep_links: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    status: str = "INFO"                 # VERIFIED | PARTIAL | FAILED | INFO
    mismatches: list[dict] = field(default_factory=list)
    kind: str = ""                       # view | data | analysis | write | converse
    steps: int = 0
    screen_note: str = ""                # outcome of the screen update, if any
    narrative_used: bool = False
    facts: Any = None
    extra_text: list[str] = field(default_factory=list)   # notes/caveats whose numbers come from code, not facts
    claims: list[dict] = field(default_factory=list)     # sentence -> facts -> evidence (agent/reasoning/proof.py)


# ------------------------------------------------------------------------------------ canvas
def _columns(rows: list[dict], names: list[str] | None = None) -> list[Column]:
    first = rows[0] if rows else {}
    names = names or [k for k, v in first.items() if not isinstance(v, (dict, list))]
    cols = []
    for n in names:
        v = next((r.get(n) for r in rows if r.get(n) is not None), None)
        t = "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else "text"
        cols.append(Column(name=n, title=n.replace("_", " ").title(), type=t))
    return cols


async def emit_canvas(ctx, kind: str, title: str, rows: list[dict] | None = None, *, x: str | None = None,
                      y: list[str] | None = None, chart_type: str = "bar_chart", evidence_ids: list[str] | None = None,
                      layers: list[str] | None = None, note: str = "", columns: list[str] | None = None,
                      form: FormSpec | None = None, canvas_id: str | None = None) -> str:
    s = ctx.session
    cid = canvas_id or f"CV{len(s.canvases) + 1}"
    if kind == "form":
        spec = CanvasSpec(canvas_id=cid, kind="form", title=title, form=form, data_layers=layers or [], note=note)
    else:
        rows = [r for r in (rows or []) if isinstance(r, dict)][:200]
        cols = _columns(rows, columns)
        chart = None
        if kind == "chart":
            names = [c.name for c in cols]
            x = x or names[0]
            y = y or [c.name for c in cols if c.type == "number" and c.name != x][:2]
            chart = ChartSpec(x=x, y=y)
        spec = CanvasSpec(canvas_id=cid, kind=kind, title=title, columns=cols, rows=rows,
                          chart_type=chart_type if kind == "chart" else None, chart=chart,
                          evidence_ids=evidence_ids or [], data_layers=layers or [], note=note)
    s.canvases[cid] = spec.model_dump()
    await emit(s, "canvas", s.canvases[cid])
    return cid


def reset_canvases(ctx) -> None:
    """Canvases belong to the turn that produced them; the next analytic turn replaces them."""
    ctx.session.canvases.clear()


# ------------------------------------------------------------------------------------ navigation
def _catalog() -> dict:
    return executor._catalog()


def sanitize_nav_args(ctx, args: dict) -> tuple[dict, list[str]]:
    """Keep only the parts of a navigate request that validate; report what was dropped."""
    app, cat, state = app_model(), _catalog(), ctx.state
    base = {"page_id": args["page_id"]}
    dropped: list[str] = []

    def ok(a: dict) -> bool:
        return validate(app, Plan(intent=Intent(name="navigate"), steps=[ToolCall(tool="navigate", args=a)]), state, cat).ok

    good = dict(base)
    if args.get("filters"):
        gf = {}
        for fid, spec in args["filters"].items():
            if ok({**base, "filters": {fid: spec}}):
                gf[fid] = spec
            else:
                dropped.append(fid)
        if gf and ok({**base, "filters": gf}):
            good["filters"] = gf
        elif gf:  # individually fine but not together
            first = next(iter(gf))
            good["filters"] = {first: gf[first]}
            dropped += [k for k in gf if k != first]
    for key in ("date_range", "sort"):
        if args.get(key):
            if ok({**good, key: args[key]}):
                good[key] = args[key]
            else:
                dropped.append(key)
    if args.get("keep_state"):
        good["keep_state"] = True
    return good, dropped


async def navigate(ctx, args: dict) -> dict:
    """Apply one atomic navigate through the validated executor path. One re-apply if the screen did not match."""
    s = ctx.session
    clean, dropped = sanitize_nav_args(ctx, args)
    plan = Plan(intent=Intent(name="navigate"), steps=[ToolCall(tool="navigate", args=clean)])
    attempts = 0
    res: dict = {}
    while attempts < 2:
        attempts += 1
        res = await executor.run_plan(s, plan, ctx.cfg, auto_confirm=getattr(ctx.cfg, "auto_confirm_writes", False))
        verified = bool(s.last_verify.ok) if s.last_verify else False
        if not getattr(ctx.cfg, "use_verifier", True):
            verified = True
        if res.get("ok") and verified:
            break
        if res.get("stage") == "validation":
            break
    verified = bool(s.last_verify.ok) if s.last_verify else False
    if not getattr(ctx.cfg, "use_verifier", True):      # ablation: without the verify layer the agent trusts that its action worked
        verified = True
    denied = next((i.get("message") for i in res.get("issues", []) if isinstance(i, dict) and i.get("code") == "disallowed_tool"), None)
    return {"ok": bool(res.get("ok")), "verified": verified and bool(res.get("ok")), "dropped": dropped, "denied": denied,
            "mismatches": list(s.last_verify.mismatches) if s.last_verify and not s.last_verify.ok
            and getattr(ctx.cfg, "use_verifier", True) else [],
            "attempts": attempts, "stage": res.get("stage"), "issues": res.get("issues", [])}


async def open_supporting_view(ctx, view: dict | None) -> dict | None:
    """If a page reproduces the numbers exactly, put the app on it (same filters and period)."""
    if not view or not view.get("exact") or not view.get("url"):
        return None
    try:
        args = nav_args_from_url(app_model(), view["url"])
    except Exception as e:  # noqa: BLE001
        logger.info("supporting view not openable: %s", e)
        return None
    args.pop("route", None)
    out = await navigate(ctx, args)
    if out.get("denied"):          # the role cannot open the supporting page: the answer stands without moving the screen
        return None
    out["title"] = view.get("title")
    return out


def screen_sentence(nav: dict | None) -> str:
    if not nav:
        return ""
    if nav["verified"]:
        return "Screen updated and verified."
    if nav["ok"]:
        miss = ", ".join(str(m.get("field")) for m in nav["mismatches"][:3]) or "unconfirmed"
        return f"Screen updated, but verification flagged: {miss}."
    return "I could not update the screen as requested."
