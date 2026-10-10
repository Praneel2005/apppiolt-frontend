"""Converse skill: help, describing the screen, clarification, refusals and undo."""
from __future__ import annotations

import logging
from typing import AsyncIterator

from agent.schema import Interp
from agent.skills.base import SkillResult
from backend import executor, sessions

logger = logging.getLogger("agent.skills.converse")


def capabilities_text(ctx) -> tuple[str, list[str]]:
    cap, lex = ctx.cap, ctx.lex
    groups: dict[str, int] = {}
    for p in cap.pages.values():
        groups[p.directory.split("/")[0]] = groups.get(p.directory.split("/")[0], 0) + 1
    from agent.interpret import _write_apis
    writes = [a.title for a in _write_apis(ctx).values()]
    lines = ["I can work with this application in four ways:",
             "- **Open and filter screens** — e.g. “show late deliveries in RJ last month” "
             f"({len(cap.pages)} pages: " + ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in groups.items()) + ").",
             "- **Answer data questions** — revenue, orders, delivery time, review score and more: totals, rankings, breakdowns and trends "
             f"({len(lex.metrics)} governed metrics).",
             "- **Look up one record** — “what happened with order E481F51C”, “status of TCK-000123”, or select a row and ask "
             "“why was this order late”.",
             "- **Explain and compare** — “why did sales drop compared to last month”, “SP vs RJ”, “how are we doing”: I find where a change comes from, "
             "check related metrics and last year, and show the numbers behind it.",
             ]
    if writes:
        lines.append("- **Make changes safely** — " + ", ".join(w.lower() for w in writes[:6]) +
                     ". I fill the form from what you say, show a preview, and nothing is saved until you confirm; every change can be undone.")
    ex = [e["q"] for e in lex.examples if e["i"].get("intent") in ("view", "data", "explain", "compare")][:5]
    return "\n".join(lines), ex


def screen_text(ctx) -> str:
    pc = ctx.page_ctx
    page = pc.get("page", {})
    lines = [f"You are on **{page.get('title', ctx.state.page_id)}**."]
    if page.get("description"):
        lines.append(page["description"])
    if page.get("agent_context"):
        lines.append(page["agent_context"])
    filt = [f"{f['title']} = {', '.join(f['value'])}" for f in pc.get("filters", []) if f.get("value")]
    if filt or pc.get("date_range"):
        dr = pc.get("date_range")
        bits = filt + ([f"{dr['from']} to {dr['to']}"] if dr else [])
        lines.append("Currently showing: " + "; ".join(bits) + ".")
    ws = pc.get("widgets", [])
    if ws:
        lines.append("What is on the page:")
        for w in ws[:6]:
            n = f" ({w['row_count']} rows)" if w.get("row_count") is not None and w.get("type") == "grid" else ""
            lines.append(f"- **{w.get('title')}**{n}: {w.get('description', '')[:140]}")
    return "\n".join(lines)


async def run_converse(ctx, it: Interp, out: SkillResult) -> AsyncIterator[tuple[str, dict]]:
    out.kind = "converse"
    out.status = "INFO"
    mem = ctx.mem
    if it.intent == "decline":
        out.text = (it.decline_reason or "I can't do that.") + \
                   " I can show data, explain changes and make safe, confirmed changes such as tickets, restocks and promotions."
        out.status = "INFO"
    elif it.intent == "clarify":
        q = it.clarification or "Could you tell me a bit more?"
        opts = it.clarify_options
        out.text = q + ("\n" + "\n".join(f"- {o}" for o in opts) if opts else "")
        out.suggestions = opts[:4]
    elif it.intent == "help":
        if it.help_topic == "screen":
            out.text = screen_text(ctx)
        else:
            text, ex = capabilities_text(ctx)
            out.text = text + ("\n\nTry: " + " · ".join(f"“{e}”" for e in ex) if ex else "")
            out.suggestions = ex[:3]
    elif it.intent == "undo":
        s = ctx.session
        if mem.last_action_kind == "write" and s.writes:
            r = await executor.undo_last_write(s)
            if r.get("ok"):
                out.text = f"Reverted {r['action_id']}; the data is back to how it was."
                out.status = "VERIFIED"
            else:
                msg = (r.get("response") or {}).get("error", {}).get("message") or r.get("message", "")
                out.text = f"I could not undo that: {msg}"
                out.status = "FAILED"
            mem.last_action_kind = None
        else:
            r = await sessions.undo_state(s)
            if r.get("ok"):
                page = ctx.cap.get_page(s.state.page_id)
                out.text = f"Went back to **{page.title if page else s.state.page_id}**."
                out.status = "VERIFIED"
            else:
                out.text = r.get("message") or "Nothing to undo."
    else:
        out.text = "How can I help?"
    if False:  # pragma: no cover  (makes this function an async generator like the other skills)
        yield "noop", {}
