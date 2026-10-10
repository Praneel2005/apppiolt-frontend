"""L6 Answer: facts -> deterministic composition -> optional LLM synthesis -> claim check.

The deterministic composers produce a complete, correct answer from analysis results (every number is a fact placeholder
rendered by code). The LLM may add a short interpretation, but only using {F#} placeholders; `check_narrative` rejects
stray digits, unknown placeholders and direction contradictions, in which case the interpretation is simply omitted.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from agent.reasoning.engine import DimScan, ExplainResult, HealthResult, SliceCompareResult
from agent.reasoning.facts import FactSheet, fmt_number, fmt_pct

logger = logging.getLogger("agent.answer")


@dataclass
class Answer:
    text: str
    facts: FactSheet = field(default_factory=FactSheet)
    citations: list[str] = field(default_factory=list)
    deep_links: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    narrative_used: bool = False
    narrative_issues: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------ small helpers
def ph(f) -> str:
    return "{" + f.id + "}"


def pretty(v) -> str:
    return str(v).replace("_", " ")


def desc_filters(filters: dict[str, list[str]]) -> str:
    if not filters:
        return ""
    return "; ".join(f"{pretty(k)} = {', '.join(map(pretty, v))}" for k, v in filters.items())


def _verb(direction: str) -> str:
    return {"up": "rose", "down": "fell"}.get(direction, "was flat")


def _adverse(delta: float | None, higher_is_better: bool) -> bool:
    return delta is not None and delta != 0 and ((delta < 0 and higher_is_better) or (delta > 0 and not higher_is_better))


def _tone(delta, hib) -> str:
    if delta in (None, 0):
        return ""
    return " (unfavourable)" if _adverse(delta, hib) else " (favourable)"


def _cite(*ids: str | None) -> str:
    ids = [i for i in ids if i]
    return " [" + ", ".join(dict.fromkeys(ids)) + "]" if ids else ""


# ------------------------------------------------------------------------------------ explain / compare periods
def compose_explain(res: ExplainResult, fs: FactSheet) -> tuple[str, list[str]]:
    """Returns (template with {F#} placeholders, suggestions)."""
    u = res.unit
    ev = res.head_evidence
    prev = fs.value(f"{res.title}, {res.baseline.label}", res.previous, u, ev)
    cur = fs.value(f"{res.title}, {res.primary.label}", res.current, u, ev)
    dl = fs.delta(f"change in {res.title}", res.delta, u, ev)
    pc = fs.pct(f"percent change in {res.title}", res.pct, ev, signed=False) if res.pct is not None else None
    verb = _verb(res.direction)
    head = f"**{res.title} {verb}" + (f" {ph(pc)}" if pc and res.direction != "flat" else "")
    head += f" in {res.primary.label} versus {res.baseline.label}** ({ph(prev)} → {ph(cur)}, {ph(dl)})"
    head += _tone(res.delta, res.higher_is_better) + _cite(ev)
    lines = [head]

    if res.premise == "contradicted":
        word = "drop" if res.expected_direction == "down" else "rise"
        actual = "did not drop" if res.expected_direction == "down" else "did not rise"
        lines.append(f"> You mentioned a {word}, but in this comparison {res.title.lower()} {actual}: it {verb}.")
    elif res.direction == "flat":
        lines.append("> The change is negligible, so there is little to explain at the headline level.")

    takeaways: list[str] = []
    bullets: list[str] = []

    if res.identity:
        idn = res.identity
        v, r = idn["volume"], idn["rate"]
        vp = fs.pct(f"{v['title']} change", v.get("pct"), idn.get("evidence_id")) if v.get("pct") is not None else None
        rp = fs.pct(f"{r['title']} change", r.get("pct"), idn.get("evidence_id")) if r.get("pct") is not None else None
        ve = fs.delta(f"effect of {v['title']}", v["effect"], u, idn.get("evidence_id"))
        re_ = fs.delta(f"effect of {r['title']}", r["effect"], u, idn.get("evidence_id"))
        b = f"- **Volume vs value:** {v['title']} {_verb('up' if v['effect'] > 0 else 'down' if v['effect'] < 0 else 'flat')}"
        b += f" {ph(vp)}" if vp else ""
        b += f" (effect {ph(ve)}) and {r['title']} {_verb('up' if r['effect'] > 0 else 'down' if r['effect'] < 0 else 'flat')}"
        b += f" {ph(rp)}" if rp else ""
        b += f" (effect {ph(re_)}). Together they account for the whole change." + _cite(idn.get("evidence_id"))
        bullets.append(b)
        dom = v if abs(v["effect"]) >= abs(r["effect"]) else r
        takeaways.append(f"Mostly a {'volume' if dom is v else 'value'} effect ({dom['title'].lower()}).")

    for sc in res.scans[:2]:
        bullets.append(_scan_bullet(sc, res, fs))
    if res.scans:
        sc0 = res.scans[0]
        if sc0.movers:
            top = sc0.movers[0]
            td = fs.delta(f"{pretty(top['group'])} change", top["delta"], u, sc0.evidence_id)
            if sc0.localisation >= 0.15:
                takeaways.append(f"Concentrated by {pretty(sc0.dim)}: {pretty(top['group'])} alone moved {ph(td)}.")
            else:
                takeaways.append(f"Broad-based across {pretty(sc0.dim)} (no single group dominates).")
    if res.drill:
        d = res.drill
        mv = d.scan.movers[:2]
        if mv:
            parts = []
            for m in mv:
                f = fs.delta(f"{pretty(m['group'])} in {pretty(d.group)}", m["delta"], u, d.scan.evidence_id)
                parts.append(f"{pretty(m['group'])} {ph(f)}")
            bullets.append(f"- **Inside {pretty(d.group)}** (by {pretty(d.scan.dim)}): " + ", ".join(parts) + _cite(d.scan.evidence_id))

    signals: list[str] = []
    for c in sorted([c for c in res.confounders if c["material"]], key=lambda c: (not c["adverse"], -abs(c["pct"] or 0)))[:4]:
        p = fs.pct(f"{c['title']} change", c["pct"], c.get("evidence_id"), signed=False)
        fp = fs.value(f"{c['title']} before", c["previous"], c["unit"], c.get("evidence_id"))
        fc = fs.value(f"{c['title']} now", c["current"], c["unit"], c.get("evidence_id"))
        verb2 = _verb("up" if c["delta"] > 0 else "down")
        signals.append(f"- **{c['title']}** {verb2} {ph(p)} ({ph(fp)} → {ph(fc)})" +
                       (", unfavourable" if c["adverse"] else ", favourable") + _cite(c.get("evidence_id")))
    if signals:
        adv = [c for c in res.confounders if c["material"] and c["adverse"]]
        if adv:
            takeaways.append("Related metrics also moved unfavourably: " + ", ".join(c["title"].lower() for c in adv[:3]) + ".")
    if res.seasonality:
        s = res.seasonality
        if s.get("yoy_pct") is not None:
            f = fs.pct(f"year over year change in {res.title}", s["yoy_pct"], s.get("evidence_id"))
            signals.append(f"- **Year over year:** {res.title} is {ph(f)} compared with {res.primary.label} a year earlier" +
                           _cite(s.get("evidence_id")))
        if s.get("ly_transition_pct") is not None:
            f = fs.pct("same transition last year", s["ly_transition_pct"], s.get("evidence_id"))
            tail = " A similar move happened then, so part of this may be seasonal." if s.get("seasonal") else ""
            signals.append(f"- **Same transition last year** ({s['last_year_baseline']} → {s['last_year_period']}): {ph(f)}.{tail}" +
                           _cite(s.get("evidence_id")))
            if s.get("seasonal"):
                takeaways.append("The same seasonal move appeared a year earlier.")
    if res.trend:
        t = res.trend
        bits = []
        if t.get("trend"):
            bits.append(f"the 12-month trend is {t['trend']}")
        if t.get("streak", 0) >= 2:
            bits.append(f"{t['streak']} consecutive monthly {'rises' if t['streak_dir'] > 0 else 'falls'}")
        z = t.get("z_latest")
        if z is not None and abs(z) >= 2:
            bits.append("the latest month is unusual against the previous months")
        if bits:
            signals.append("- **Trend context:** " + "; ".join(bits) + "." + _cite(t.get("evidence_id")))

    if takeaways:
        lines.append("**In short:** " + " ".join(takeaways))
    if bullets:
        lines.append("**Where the change comes from**")
        lines += bullets
    if signals:
        lines.append("**Other signals** (associated with the change, not proven causes)")
        lines += signals
    notes = [*res.notes, *res.caveats]
    if notes:
        lines.append("**Notes:** " + " ".join(notes))
    sugg = []
    if res.scans:
        sugg.append(f"Show {res.title.lower()} by {pretty(res.scans[0].dim)}")
    if res.identity:
        sugg.append(f"Why did {res.identity['volume']['title'].lower()} change?")
    sugg.append(f"Compare {res.title.lower()} with the same period last year")
    return "\n".join(lines), sugg


def _scan_bullet(sc: DimScan, res: ExplainResult, fs: FactSheet) -> str:
    u = res.unit
    movers = []
    for m in sc.movers[:3]:
        f = fs.delta(f"{pretty(m['group'])} change", m["delta"], u, sc.evidence_id)
        movers.append(f"{pretty(m['group'])} {ph(f)}")
    b = f"- **By {pretty(sc.dim)}:** " + (", ".join(movers) if movers else "no group moved in the same direction")
    if sc.offsets:
        o = sc.offsets[0]
        f = fs.delta(f"{pretty(o['group'])} change", o["delta"], u, sc.evidence_id)
        b += f"; offsetting: {pretty(o['group'])} {ph(f)}"
    if sc.gross:
        sh = fs.share(f"share of all movement in the top three {pretty(sc.dim)} groups", 100 * sc.top3_share, sc.evidence_id)
        b += f". The top three groups account for {ph(sh)} of all movement"
    if sc.mode == "mix_rate":
        b += " (split into rate and mix effects)"
    elif sc.mode == "indicative":
        b += " (indicative: group changes do not add up to the total for this metric)"
    if sc.gross and res.delta and abs(sc.tail) / abs(res.delta) >= 0.05 and sc.mode != "indicative":
        tf = fs.delta("change not covered by the listed groups", sc.tail, u, sc.evidence_id)
        b += f"; {ph(tf)} sits in smaller groups"
    return b + "." + _cite(sc.evidence_id)


# ------------------------------------------------------------------------------------ slice comparison
def compose_slices(res: SliceCompareResult, fs: FactSheet) -> tuple[str, list[str]]:
    u = res.unit
    ev = res.slices[0].get("evidence_id") if res.slices else None
    parts = []
    for s in res.slices:
        f = fs.value(f"{res.title}, {s['label']}", s["value"], u, ev)
        parts.append(f"{pretty(s['label'])} {ph(f)}")
    per = f" in {res.period.label}" if res.period else ""
    head = f"**{res.title}{per}: " + " vs ".join(parts) + "**" + _cite(ev)
    lines = [head]
    a, b = res.slices[0], res.slices[1]
    if res.gap_abs is not None:
        g = fs.delta(f"gap {a['label']} minus {b['label']}", res.gap_abs, u, ev)
        txt = f"- **Gap:** {pretty(a['label'])} is {ph(g)} relative to {pretty(b['label'])}"
        if res.gap_pct is not None:
            gp = fs.pct("relative gap", res.gap_pct, ev)
            txt += f" ({ph(gp)})"
        if res.ratio:
            rt = fs.text("ratio", f"{res.ratio:.2f}×")
            txt += f", i.e. {ph(rt)} the size of {pretty(b['label'])}"
        lines.append(txt + ".")
    if all(s.get("share") is not None for s in res.slices):
        sh = " / ".join(ph(fs.share(f"share {s['label']}", s["share"], ev)) for s in res.slices)
        lines.append(f"- **Share of the compared total:** {sh}")
    if res.identity and len(res.identity["rows"]) >= 2:
        r0, r1 = res.identity["rows"][0], res.identity["rows"][1]
        v, k = res.identity["volume_id"], res.identity["rate_id"]
        n0, n1, a0, a1 = r0.get(v), r1.get(v), r0.get(k), r1.get(k)
        if None not in (n0, n1, a0, a1) and n1 and a1:
            vol = (n0 - n1) * a1
            rate = n0 * (a0 - a1)
            fv = fs.delta(f"gap from {res.identity['volume'].lower()}", vol, u, ev)
            fr = fs.delta(f"gap from {res.identity['rate'].lower()}", rate, u, ev)
            n0f, n1f = fs.value(f"{res.identity['volume']} {a['label']}", n0, "count", ev), fs.value(f"{res.identity['volume']} {b['label']}", n1, "count", ev)
            a0f, a1f = fs.value(f"{res.identity['rate']} {a['label']}", a0, u, ev), fs.value(f"{res.identity['rate']} {b['label']}", a1, u, ev)
            lines.append(f"- **Why they differ:** {res.identity['volume'].lower()} {ph(n0f)} vs {ph(n1f)} accounts for {ph(fv)}; "
                         f"{res.identity['rate'].lower()} {ph(a0f)} vs {ph(a1f)} accounts for {ph(fr)}.")
    for d in res.differences[:3]:
        vals = " vs ".join(f"{pretty(v['label'])} {ph(fs.value(d['title'] + ' ' + v['label'], v['value'], d['unit'], ev))}" for v in d["values"])
        lines.append(f"- **{d['title']}:** {vals}")
    if res.mix_dim:
        for s in res.slices:
            tg = s.get("top_groups") or []
            if tg:
                names = []
                for t in tg:
                    if t.get("share_pct") is not None:
                        sf = fs.share(str(t["group"]) + " share", t["share_pct"], ev)
                        names.append(pretty(t["group"]) + " (" + ph(sf) + ")")
                    else:
                        names.append(pretty(t["group"]))
                lines.append("- **Top " + pretty(res.mix_dim) + " in " + pretty(s["label"]) + ":** " + ", ".join(names))
    notes = [*res.notes, *res.caveats]
    if notes:
        lines.append("**Notes:** " + " ".join(notes))
    sugg = [f"Why does {pretty(a['label'])} differ from {pretty(b['label'])}?", f"Show {res.title.lower()} trend for {pretty(a['label'])}"]
    return "\n".join(lines), sugg


# ------------------------------------------------------------------------------------ health scan
def compose_health(res: HealthResult, fs: FactSheet) -> tuple[str, list[str]]:
    lines = [f"**Business health: {res.primary.label} vs {res.baseline.label}**"]
    concerns = sorted([i for i in res.items if i["material"] and i["adverse"]], key=lambda i: -abs(i["pct"] or 0))
    wins = sorted([i for i in res.items if i["material"] and not i["adverse"]], key=lambda i: -abs(i["pct"] or 0))
    steady = [i for i in res.items if not i["material"]]

    def line(i: dict) -> str:
        p = fs.pct(f"{i['title']} change", i["pct"], i.get("evidence_id"), signed=False)
        a = fs.value(f"{i['title']} before", i["previous"], i["unit"], i.get("evidence_id"))
        b = fs.value(f"{i['title']} now", i["current"], i["unit"], i.get("evidence_id"))
        extra = ""
        if i.get("streak", 0) >= 3:
            extra = f"; {i['streak']} months in a row"
        if i.get("outlier"):
            extra += "; unusual against recent months"
        return f"- **{i['title']}** {_verb('up' if i['delta'] > 0 else 'down')} {ph(p)} ({ph(a)} → {ph(b)}){extra}"

    if concerns:
        lines.append("**Needs attention**")
        lines += [line(i) for i in concerns[:4]]
    else:
        lines.append("Nothing moved materially in an unfavourable direction.")
    if wins:
        lines.append("**Going well**")
        lines += [line(i) for i in wins[:3]]
    if steady:
        lines.append("**Steady:** " + ", ".join(i["title"].lower() for i in steady))
    outl = [i for i in res.items if i.get("outlier")]
    if outl:
        lines.append("**Unusual against recent months:** " + ", ".join(i["title"].lower() for i in outl))
    if res.notes:
        lines.append("**Notes:** " + " ".join(res.notes))
    sugg = [f"Why did {concerns[0]['title'].lower()} change?"] if concerns else []
    sugg.append(f"Show the trend of {res.items[0]['title'].lower()}")
    return "\n".join(lines), sugg


# ------------------------------------------------------------------------------------ simple data answers
def compose_single(res: dict, prev_cmp: dict | None, fs: FactSheet, ev: str | None, label: str, cmp_ev: str | None = None) -> str:
    u = res.get("unit", "")
    v = fs.value(f"{res['title']}, {label}", res.get("total"), u, ev)
    flt = desc_filters(res.get("filters", {}))
    line = f"**{res['title']}** for {label}" + (f" ({flt})" if flt else "") + f": **{ph(v)}**" + _cite(ev)
    if prev_cmp and prev_cmp.get("previous", {}).get("value") is not None and prev_cmp.get("delta") is not None:
        pv = fs.value(f"{res['title']}, previous period", prev_cmp["previous"]["value"], u, cmp_ev or ev)
        pc = fs.pct("change vs previous period", prev_cmp.get("pct_change"), cmp_ev or ev) if prev_cmp.get("pct_change") is not None else None
        line += f"\n- Versus the previous period ({prev_cmp['previous']['from']} to {prev_cmp['previous']['to']}): {ph(pv)}" + (f", {ph(pc)}" if pc else "") + _cite(cmp_ev)
    if res.get("definition"):
        line += f"\n- Definition: {res['definition']}"
    return line


def compose_breakdown(res: dict, dim: str, fs: FactSheet, ev: str | None, label: str, top_n: int | None, asc: bool) -> str:
    u = res.get("unit", "")
    rows = [r for r in res.get("rows", []) if r.get("value") is not None]
    flt = desc_filters(res.get("filters", {}))
    if not rows:
        return f"No {res['title'].lower()} data for {label}" + (f" ({flt})" if flt else "") + "."
    rows = sorted(rows, key=lambda r: r["value"], reverse=not asc)
    show = rows[: (top_n or 5)]
    tot = fs.value(f"total {res['title']}", res.get("total"), u, ev) if res.get("additive") and res.get("total") is not None else None
    head = f"**{res['title']} by {pretty(dim)}, {label}**" + (f" ({flt})" if flt else "")
    if tot:
        head += f" — total {ph(tot)}"
    lines = [head + _cite(ev)]
    for i, r in enumerate(show, 1):
        f = fs.value(f"{r[dim]} {res['title']}", r["value"], u, ev)
        sh = f" ({ph(fs.share(f'{r[dim]} share', r['share_pct'], ev))})" if r.get("share_pct") is not None else ""
        lines.append(f"{i}. **{pretty(r[dim])}**: {ph(f)}{sh}")
    if res.get("additive") and len(rows) > 3:
        top3 = sum((r.get("share_pct") or 0) for r in rows[:3]) if not asc else None
        if top3:
            n_groups = fs.value("number of groups", len(rows), "count", ev)
            lines.append(f"- The top three of {ph(n_groups)} groups account for {ph(fs.share('top three share', top3, ev))}.")
    if not res.get("additive"):
        lines.append("- This is a ratio/average, so group values do not add up to a total.")
    if res.get("truncated"):
        lines.append("- The list was truncated; narrow the question for the rest.")
    return "\n".join(lines)


def time_key(tr: dict) -> str:
    """Name of the time column in a trend response (the first column that is not a computed one)."""
    first = (tr.get("rows") or [{}])[0]
    return next((k for k in first if k not in ("value", "mom_pct", "yoy_pct")), "month")


def compose_trend(tr: dict, summ: dict, fs: FactSheet, ev: str | None, label: str, filters: dict) -> str:
    u = tr.get("unit", "")
    st = tr.get("stats", {})
    flt = desc_filters(filters)
    first, last = st.get("first", {}), st.get("last", {})
    lines = [f"**{tr['title']} trend, {label}**" + (f" ({flt})" if flt else "") + _cite(ev)]
    word = {"rising": "rising", "falling": "falling", "flat": "flat"}.get(st.get("trend"), st.get("trend", "unclear"))
    if st.get("change_pct") is not None:
        c = fs.pct("change first to last month", st["change_pct"], ev)
        a = fs.value("first month", first.get("value"), u, ev)
        b = fs.value("latest month", last.get("value"), u, ev)
        lines.append(f"- The trend is **{word}**: {ph(a)} in {first.get('month', '')[:7]} → {ph(b)} in {last.get('month', '')[:7]} ({ph(c)}).")
    pk, tr_ = st.get("peak"), st.get("trough")
    if pk and tr_:
        lines.append(f"- Peak {pk['month'][:7]} ({ph(fs.value('peak', pk['value'], u, ev))}); low {tr_['month'][:7]} ({ph(fs.value('low', tr_['value'], u, ev))}).")
    rows = tr.get("rows", [])[-6:]
    tk = time_key(tr)
    if rows:
        lines.append("- Recent months: " + "; ".join(
            f"{r[tk][:7]} {ph(fs.value('month ' + r[tk][:7], r['value'], u, ev))}" +
            (f" ({ph(fs.pct('mom ' + r[tk][:7], r['mom_pct'], ev))} m/m)" if r.get("mom_pct") is not None else "") for r in rows))
    if summ.get("streak", 0) >= 3:
        lines.append(f"- {summ['streak']} consecutive monthly {'rises' if summ['streak_dir'] > 0 else 'falls'}.")
    if st.get("missing_months"):
        lines.append(f"- No data for: {', '.join(st['missing_months'][:4])}.")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------ LLM synthesis
class HypothesisF(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""
    support: str = "weak"       # strong | moderate | weak


class NarrativeF(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reading: str = ""
    hypotheses: list[HypothesisF] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)


SYNTH_SYSTEM = """You are the explanation layer of an analytics assistant. You are given verified facts (F1, F2, ...) and a
structured analysis. Write a short interpretation for a business user.
STRICT RULES
- Refer to numbers ONLY through placeholders like {F3}. Never type a digit yourself (no counts, percentages or amounts). Do not put currency symbols or units next to a placeholder, they are already included.
- Use only the facts given. Do not invent causes, events, campaigns or external reasons.
- Separate WHAT changed, WHERE it happened and POSSIBLE WHY. Say 'associated with' rather than 'caused by' unless the volume-vs-value split shows it directly.
- Mention what is NOT explained (offsets, notes, caveats) when relevant.
- reading: 3 to 5 sentences. hypotheses: at most 3, each with support strong | moderate | weak, based only on the facts. next_checks: at most 3 short follow-up questions the user could ask.
Return only the JSON object."""


def build_synth_prompt(question: str, template: str, fs: FactSheet, flags: dict) -> str:
    return "\n".join([
        f"USER QUESTION: {question}",
        "FLAGS: " + ", ".join(f"{k}={v}" for k, v in flags.items() if v not in (None, "", False)),
        "FACTS:", *fs.lines(),
        "ANALYSIS (placeholders refer to the facts above):", template,
    ])


def check_text(text: str, fs: FactSheet, allowed_numbers: set[str]) -> list[str]:
    """Return the list of problems (empty = acceptable). Direction / overconfidence words are data (lexicon 'language')."""
    from agent.lexicon import get_lexicon
    lex = get_lexicon()
    up_re, down_re, certain_re = lex.re_up, lex.re_down, lex.re_overconfident
    issues: list[str] = []
    for pid in re.findall(r"\{(F\d+)\}", text):
        if pid not in fs.facts:
            issues.append(f"unknown placeholder {pid}")
    cleaned = re.sub(r"\{F\d+\}", " ", text)
    for tok in re.findall(r"\d+(?:[.,]\d+)*", cleaned):
        if tok in allowed_numbers or re.fullmatch(r"20[12]\d", tok):
            continue
        issues.append(f"number '{tok}' is not from the facts")
    for sent in re.split(r"(?<=[.!?])\s+", text):
        ids = re.findall(r"\{(F\d+)\}", sent)
        signs = [fs.facts[i].sign for i in ids if i in fs.facts and fs.facts[i].kind in ("delta", "pct") and fs.facts[i].sign != 0]
        if not signs:
            continue
        low = sent.lower()
        if all(s < 0 for s in signs) and up_re.search(low) and not down_re.search(low):
            issues.append(f"direction contradicts the facts in: {sent[:80]}")
        if all(s > 0 for s in signs) and down_re.search(low) and not up_re.search(low):
            issues.append(f"direction contradicts the facts in: {sent[:80]}")
    if certain_re.search(text.lower()):
        issues.append("overconfident wording")
    return issues


async def synthesize(llm, question: str, template: str, fs: FactSheet, flags: dict, allowed_numbers: set[str]
                     ) -> tuple[str | None, list[str]]:
    """Return (markdown block or None, issues). One retry with feedback."""
    if llm is None:
        return None, []
    prompt = build_synth_prompt(question, template, fs, flags)
    messages = [{"role": "user", "content": prompt}]
    issues: list[str] = []
    for attempt in range(2):
        try:
            nar: NarrativeF = await llm.generate_structured(NarrativeF, messages, system_prompt=SYNTH_SYSTEM)
        except Exception as e:  # noqa: BLE001
            logger.warning("synthesis failed: %s", e)
            return None, [f"synthesis unavailable: {type(e).__name__}"]
        parts = [nar.reading, *[h.text for h in nar.hypotheses], *nar.next_checks]
        issues = []
        for p in parts:
            issues += check_text(p, fs, allowed_numbers)
        if not issues and nar.reading.strip():
            md = ["**Reading**", fs.render(nar.reading.strip())]
            hyps = [h for h in nar.hypotheses if h.text.strip()][:3]
            if hyps:
                md.append("**Possible explanations**")
                md += [f"- ({h.support}) {fs.render(h.text.strip())}" for h in hyps]
            return "\n".join(md), []
        messages = [{"role": "user", "content": prompt},
                    {"role": "assistant", "content": nar.model_dump_json()},
                    {"role": "user", "content": "Problems found: " + "; ".join(issues[:5]) +
                     ". Rewrite using only {F#} placeholders and no digits."}]
    return None, issues


def allowed_numbers_from(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)*", text))
