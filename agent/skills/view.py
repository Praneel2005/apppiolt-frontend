"""View skill: put the application on the screen the user asked for, in ONE atomic, verified state change.

The page is chosen by capability (does it support every requested filter, the metric, the group-by, the period, the sort?)
combined with retrieval rank. A request is never silently reduced: whatever the chosen page cannot do is reported, and when
the user asked for numbers no page can show, the Data skill answers analytically instead.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

from agent.interpret import get_retrieval
from agent.lexicon import phrase_tokens
from agent.schema import Interp
from agent.skills.base import SkillResult, navigate, screen_sentence
from backend import executor
from contracts.actions import ToolCall

logger = logging.getLogger("agent.skills.view")


@dataclass
class PageScore:
    page_id: str
    title: str
    score: float
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)   # page filter id -> values
    date_ok: bool = False
    sort_ok: bool = False

    @property
    def complete(self) -> bool:
        return not self.missing


def page_filter_id(page, key: str) -> str | None:
    if key in page.filters:
        return key
    for fid, fc in page.filters.items():
        if fc.field == key:
            return fid
    return None


def score_pages(ctx, it: Interp) -> list[PageScore]:
    cap, lex = ctx.cap, ctx.lex
    ret = get_retrieval(ctx)
    rank = {pg.id: i for i, pg in enumerate(ret.pages)} if ret else {}
    ann = getattr(ctx, "ann", None)
    cue_idx = {i for spans in ann.cues.values() for a, b in spans for i in range(a, b + 1)} if ann is not None else set()
    content = {t.stem for t in ann.toks if t.i not in cue_idx and t.stem not in lex.stop} if ann is not None else set()
    out: list[PageScore] = []
    for pid, p in cap.pages.items():
        sc = PageScore(pid, p.title, 0.0)
        flt_total = len(it.filters)
        covered = 0
        for key, vals in it.filters.items():
            fid = page_filter_id(p, key)
            if fid is None:
                sc.missing.append(key.replace("_", " "))
                continue
            fc = p.filters[fid]
            canon = []
            for v in vals:
                c = lex.canonical_value(fid, v)
                canon.append(c.value or v)
            if len(canon) > 1 and fc.type != "multiselect":
                sc.missing.append(f"several values of {key.replace('_', ' ')}")
                canon = canon[:1]
                sc.notes.append(f"{p.title} accepts one {key.replace('_', ' ')} at a time; used {canon[0]}.")
            else:
                covered += 1
            sc.filters[fid] = canon
        for key, vals in it.soft_filters.items():
            fid = page_filter_id(p, key)
            if fid is None or fid in sc.filters:
                continue
            fc = p.filters[fid]
            canon = [lex.canonical_value(fid, v).value or v for v in vals]
            sc.filters[fid] = canon[:1] if (len(canon) > 1 and fc.type != "multiselect") else canon
            sc.score += 0.8
        if flt_total:
            sc.score += 3.0 * (covered / flt_total)
        # a metric is only a hint for choosing a page when the request is about numbers (a period, a split, a filter or the
        # model said so): bare nouns like "orders" or "customers" name screens, not metrics
        if it.metric and it.metric in p.metrics and (it.group_by or it.primary or it.filters or it.source == "llm"):
            sc.score += 2.5
        if p.kind == "operations" and not it.group_by:
            sc.score += 0.6   # a bare noun ("orders", "tickets") means the working screen rather than the report
        for g in it.group_by:
            if g in p.dimensions:
                sc.score += 2.0
            else:
                sc.score -= 2.0
                sc.missing.append(f"breakdown by {g.replace('_', ' ')}")
        if it.primary:
            if p.supports_date_range:
                sc.score += 1.0
                sc.date_ok = True
            else:
                sc.score -= 2.0
                sc.missing.append("date range")
        if it.sort_field:
            sc.sort_ok = it.sort_field in p.sortable_fields
            sc.score += 1.0 if sc.sort_ok else 0.0
            if not sc.sort_ok:
                sc.missing.append(f"sorting by {it.sort_field.replace('_', ' ')}")
        if pid in rank:
            sc.score += 3.0 / (1 + rank[pid])
        # the user named this page: its title words are (nearly) all the content words of the message
        title_stems = {s for s in phrase_tokens(p.title) if s not in lex.stop}
        if title_stems and content:
            if title_stems == content:
                sc.score += 5.0
            elif title_stems <= content:
                sc.score += 3.0
            elif title_stems & content:
                sc.score += 1.5 * len(title_stems & content) / len(title_stems)
        if it.target_page == pid:
            sc.score += 10.0
        if ctx.state.page_id == pid:
            sc.score += 0.4
        if it.metric and "overview" in p.title.lower():
            sc.score -= 0.6
        out.append(sc)
    out.sort(key=lambda s: -s.score)
    return out


def view_confidence(ctx, it: Interp) -> tuple[PageScore | None, float]:
    scores = score_pages(ctx, it)
    if not scores:
        return None, 0.0
    margin = scores[0].score - (scores[1].score if len(scores) > 1 else 0.0)
    return scores[0], margin


def _op_for(fc, n: int) -> str:
    ops = list(fc.allowed_ops or [])
    if n > 1 and "in" in ops:
        return "in"
    if fc.type == "multiselect" and "in" in ops:
        return "in"
    if "eq" in ops:
        return "eq"
    return ops[0] if ops else "eq"


def _describe_state(ctx) -> str:
    st = ctx.state
    page = ctx.cap.get_page(st.page_id)
    parts = []
    for fid, fv in st.filters.items():
        title = page.filters[fid].filter_id if page and fid in page.filters else fid
        parts.append(f"{title.replace('_', ' ')} = {', '.join(fv.value)}")
    if st.date_range:
        parts.append(f"{st.date_range.from_} to {st.date_range.to}")
    if st.sort:
        parts.append(f"sorted by {st.sort.field} {st.sort.dir}")
    return "; ".join(parts)


async def run_view(ctx, it: Interp, out: SkillResult) -> AsyncIterator[tuple[str, dict]]:
    scores = score_pages(ctx, it)
    if not scores:
        out.text, out.status, out.kind = "I could not find a page for that request.", "FAILED", "view"
        return
    best = scores[0]
    yield "retrieval", {"pages": [{"id": s.page_id, "title": s.title, "score": round(s.score, 2),
                                   "missing": s.missing} for s in scores[:5]], "apis": [], "expanded": []}

    # numbers the page cannot show -> answer analytically (and say which page is closest)
    blocking = [m for m in best.missing if not m.startswith("sorting")]
    if it.metric and blocking and (it.group_by or (it.source == "llm" and (it.filters or it.primary))):
        unsupported = ", ".join(blocking)
        it.assumptions.append(f"No page can show {ctx.lex.metrics[it.metric].title} with {unsupported}; "
                              f"answering with a generated view instead (closest page: {best.title}).")
        from agent.skills.data import run_data  # local import: data imports this module's helpers
        shape = "breakdown" if it.group_by else "single"
        d_it = dataclasses.replace(it, intent="data", shape=shape if it.shape in ("none", "list") else it.shape)
        async for ev in run_data(ctx, d_it, out):
            yield ev
        return

    page = ctx.cap.get_page(best.page_id)
    args: dict = {"page_id": best.page_id}
    if best.filters:
        args["filters"] = {fid: {"op": _op_for(page.filters[fid], len(vals)), "value": vals} for fid, vals in best.filters.items()}
    if it.primary and best.date_ok:
        args["date_range"] = it.primary.args()
    if it.sort_field and best.sort_ok:
        args["sort"] = {"field": it.sort_field, "dir": it.sort_dir or "desc"}
    if (it.follow_up or (ctx.ann is not None and ctx.ann.has("followup"))) and best.page_id == ctx.state.page_id:
        args["keep_state"] = True

    yield "analysis_step", {"title": f"Opening {best.title}", "detail": _args_brief(args)}
    nav = await navigate(ctx, args)
    out.steps = 1

    # read the main table so the answer can say what is on screen
    rows_note = ""
    grid_id = None
    if page:
        from backend.config import app_model
        app = app_model()
        grid_id = next((w for w in page.row_widgets if app.widget(w) and app.widget(w).type == "grid"), None)
    if grid_id:
        try:
            res = await executor._read_view(ctx.session, ToolCall(tool="read_view", args={"widget_id": grid_id, "max_rows": 10}), 0)
            if res.evidence_id:
                out.citations.append(res.evidence_id)
                vals = ctx.session.evidence[res.evidence_id].values
                n, total = vals.get("row_count", 0), vals.get("total")
                rows_note = f" It shows {n:,} rows" + (f" (total {total:,})." if isinstance(total, int) and total != n else ".")
        except Exception as e:  # noqa: BLE001
            logger.info("could not read primary widget: %s", e)

    applied = _describe_state(ctx)
    unmet = list(dict.fromkeys([*best.missing, *nav["dropped"]]))
    text = f"Opened **{best.title}**" + (f" with {applied}" if applied else "") + "." + rows_note
    if unmet:
        text += f" Not applied (this page does not support it): {', '.join(unmet)}."
    for n in best.notes:
        text += f" {n}"
    out.text = text
    out.extra_text = [*best.notes, *unmet, applied]
    out.kind = "view"
    out.mismatches = nav["mismatches"]
    out.screen_note = screen_sentence(nav)
    if not nav["ok"]:
        out.status = "FAILED"
    elif not nav["verified"] or unmet:
        out.status = "PARTIAL"
    else:
        out.status = "VERIFIED"
    alt_dim = next((d for d in page.dimensions if d not in ctx.lex.time_dims and d not in it.filters), None)
    out.suggestions = ([f"Why did {ctx.lex.metrics[it.metric].title.lower()} change?"] if it.metric else []) + \
                      ([f"Break this down by {alt_dim.replace('_', ' ')}"] if alt_dim else [])


def _args_brief(args: dict) -> str:
    bits = []
    for fid, spec in (args.get("filters") or {}).items():
        bits.append(f"{fid}={spec['value']}")
    if args.get("date_range"):
        bits.append(f"{args['date_range'].get('from')}..{args['date_range'].get('to')}")
    return ", ".join(bits)
