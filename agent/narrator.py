"""Narrator for AppPilot: produces grounded, citation-checked natural language answers.

Features:
- Deterministic template narration for navigation and filter tasks (zero LLM calls)
- Evidence-based placeholder interpolation ({E1.total}, {E1.rows[0].value})
- Strict citation check (rejects any ungrounded digits not found in cited evidence or user query)
- Generates evidence citations and supporting deep links
- Toggleable via AgentConfig.use_citation_check (ablation A6)
"""
from __future__ import annotations

import re
from typing import Any

from backend.config import app_model
from backend.deeplinks import state_to_url
from contracts.actions import AgentConfig, Plan
from contracts.ui_state import UiState


def _extract_digits(text: str) -> set[str]:
    """Finds all numeric token sequences in a text, ignoring formatting commas like 1,000,000."""
    cleaned = re.sub(r"(\d),(\d)", r"\1\2", text)
    return set(re.findall(r"\d+(?:\.\d+)?", cleaned))


def _ev_values(ev: Any) -> dict[str, Any]:
    if isinstance(ev, dict):
        return ev.get("values", {})
    return getattr(ev, "values", {})


def _extract_evidence_numbers(evidence_map: dict[str, Any]) -> set[str]:
    """Recursively extracts all numbers present in evidence data."""
    numbers: set[str] = set()

    def _walk(obj: Any):
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            abs_v = abs(obj)
            for num in (obj, abs_v):
                numbers.add(str(num))
                numbers.add(f"{num:.1f}")
                numbers.add(f"{num:.2f}")
                numbers.add(str(int(round(num))))
        elif isinstance(obj, str):
            for m in re.findall(r"\d+(?:\.\d+)?", obj):
                numbers.add(m)
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    for ev_id, ev in evidence_map.items():
        # Include evidence ID numbers like '1' from 'E1'
        for m in re.findall(r"\d+", ev_id):
            numbers.add(m)
        vals = _ev_values(ev)
        _walk(vals)
    return numbers


def narrate_navigation(
    plan: Plan,
    final_state: UiState | None,
    verified: bool = True,
    status: str = "VERIFIED",
    mismatches: list[dict] | None = None,
    assumptions: list[str] | None = None,
) -> str:
    """Zero-LLM deterministic narration for UI navigation and state changes."""
    app = app_model()
    page_title = "requested page"
    if final_state and final_state.page_id:
        try:
            page_title = app.page(final_state.page_id).title
        except Exception:
            page_title = final_state.page_id

    filter_strs = []
    if final_state and final_state.filters:
        for k, v in final_state.filters.items():
            val = getattr(v, "value", v)
            filter_strs.append(f"{k} = {','.join(map(str, val)) if isinstance(val, list) else val}")
    filters_desc = f" with {'; '.join(filter_strs)}" if filter_strs else ""

    date_desc = ""
    if final_state and final_state.date_range:
        if final_state.date_range.preset:
            date_desc = f" for {final_state.date_range.preset.replace('_', ' ')}"
        elif getattr(final_state.date_range, "from_", None) and getattr(final_state.date_range, "to", None):
            date_desc = f" from {final_state.date_range.from_} to {final_state.date_range.to}"

    if status == "VERIFIED" and verified:
        ver_tag = " [Screen verified ✓]"
    elif status == "PARTIAL":
        m_info = f" (mismatches: {[m.get('field') for m in (mismatches or [])]})" if mismatches else ""
        ver_tag = f" [Screen status: PARTIAL{m_info}]"
    elif status == "FAILED" or not verified:
        ver_tag = " [Screen status: FAILED]"
    else:
        ver_tag = " [Screen state applied]"

    assump_text = f"\n*Assumptions: {'; '.join(assumptions)}*" if assumptions else ""
    return f"Opened {page_title}{filters_desc}{date_desc}.{ver_tag}{assump_text}"




def _get_nested_val(data: Any, path_parts: list[str]) -> Any:
    curr = data
    for part in path_parts:
        if curr is None:
            return None
        m = re.match(r"^(\w+)\[(\d+)\]$", part)
        if m and isinstance(curr, dict):
            k, idx = m.group(1), int(m.group(2))
            sub = curr.get(k)
            if isinstance(sub, list) and 0 <= idx < len(sub):
                curr = sub[idx]
            else:
                return None
        elif isinstance(curr, dict):
            curr = curr.get(part)
        elif isinstance(curr, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(curr):
                curr = curr[idx]
            else:
                return None
        else:
            return None
    return curr


def fill_placeholders(template_text: str, evidence_map: dict[str, Any]) -> str:
    """Fills placeholders like {E1.pct_change} or {E1.rows[0].value} from evidence."""
    def _repl(match: re.Match) -> str:
        token = match.group(1)  # e.g. E1.pct_change
        parts = token.split(".")
        ev_id = parts[0]
        if ev_id not in evidence_map:
            return match.group(0)

        ev = evidence_map[ev_id]
        vals = _ev_values(ev)

        val = _get_nested_val(vals, parts[1:])
        if val is not None:
            if isinstance(val, float):
                return f"{val:,.2f}"
            if isinstance(val, int):
                return f"{val:,}"
            return str(val)
        return match.group(0)

    return re.sub(r"\{([A-Za-z0-9_\[\].]+)\}", _repl, template_text)


def check_numbers(text: str, original_query: str, evidence_numbers: set[str]) -> bool:
    """Strict verification: every number in text must come from evidence or the user query."""
    text_nums = _extract_digits(text)
    allowed_nums = _extract_digits(original_query) | evidence_numbers
    # Also allow standard calendar years or single digits if reasonable
    disallowed = text_nums - allowed_nums
    return len(disallowed) == 0


def narrate_analytics_fallback(evidence_map: dict[str, Any]) -> str:
    """Intuitive deterministic summary of metric or dataset evidence."""
    lines = []
    for ev_id, ev in evidence_map.items():
        vals = _ev_values(ev)
        title = vals.get("title") or vals.get("metric", "Results")
        unit = vals.get("unit", "")
        unit_prefix = "R$ " if unit == "BRL" else ""
        total = vals.get("total")

        if total is not None:
            if isinstance(total, float):
                lines.append(f"**Total {title}**: {unit_prefix}{total:,.2f}")
            else:
                lines.append(f"**Total {title}**: {total:,}")

        rows = vals.get("rows", [])
        if "stats" in vals:
            st = vals["stats"]
            direction = st.get("trend", "flat")
            chg = st.get("change_pct")
            chg_str = f" ({chg:+.1f}% total change)" if chg is not None else ""
            lines.append(f"**Trend Analysis for {title}**: Overall direction is **{direction}**{chg_str}.")
            pk = st.get("peak")
            if pk:
                lines.append(f"- **Peak**: {pk.get('month')} ({unit_prefix}{pk.get('value'):,.2f})")
            tr = st.get("trough")
            if tr:
                lines.append(f"- **Trough**: {tr.get('month')} ({unit_prefix}{tr.get('value'):,.2f})")
            if rows:
                lines.append("\n**Monthly Growth (MoM)**:")
                for r in rows:
                    m_str = r.get("order_month")
                    v_str = f"{unit_prefix}{r.get('value', 0):,.2f}"
                    mom = r.get("mom_pct")
                    mom_str = f" ({mom:+.1f}% MoM)" if mom is not None else " (baseline)"
                    lines.append(f"- **{m_str}**: {v_str}{mom_str}")
        elif "drivers" in vals or (vals.get("current") and vals.get("previous") and isinstance(vals.get("current"), dict)):
            cur_val = vals.get("current", {}).get("value")
            prev_val = vals.get("previous", {}).get("value")
            pct = vals.get("pct_change", 0.0)
            delta = vals.get("delta", 0.0)
            dir_word = "increased" if delta > 0 else ("decreased" if delta < 0 else "remained flat")
            cur_str = f"{unit_prefix}{cur_val:,.2f}" if cur_val is not None else "N/A"
            prev_str = f"{unit_prefix}{prev_val:,.2f}" if prev_val is not None else "N/A"
            lines.append(
                f"**{title} Comparison**: {title} {dir_word} by **{pct:+.2f}%** ({unit_prefix}{delta:+,.2f}) "
                f"from {prev_str} to {cur_str}."
            )
            drivers = vals.get("drivers", rows)
            if drivers:
                lines.append(f"\n**Key Drivers of Change [{ev_id}]**:")
                for idx, d in enumerate(drivers[:5], 1):
                    grp = d.get("group", f"Segment #{idx}")
                    d_val = d.get("delta", 0.0)
                    c_pct = d.get("contribution_pct")
                    c_str = f" ({c_pct:+.1f}% contribution)" if c_pct is not None else ""
                    lines.append(f"- **{grp}**: {unit_prefix}{d_val:+,.2f}{c_str}")
        elif rows:
            lines.append(f"\nTop breakdowns [{ev_id}]:")
            for idx, r in enumerate(rows[:5], 1):
                dim = next((k for k in r if k not in ("value", "share_pct", "total", "count", "items_value", "is_late")), None)
                val = r.get("value") or r.get("count") or r.get("items_value")
                share = r.get("share_pct")

                val_str = f"{val:,.2f}" if isinstance(val, float) else f"{val}"
                share_str = f" ({share:.1f}%)" if share is not None else ""
                dim_str = str(r.get(dim)) if dim else f"Item #{idx}"

                lines.append(f"- **{dim_str}**: {unit_prefix}{val_str}{share_str}")

        elif "pct_change" in vals:
            pct = vals.get("pct_change", 0.0)
            lines.append(f"- [{ev_id}] Period comparison: **{pct:+.2f}%** change.")
        elif "summary" in vals:
            lines.append(f"- [{ev_id}] {vals['summary']}")

    return "\n".join(lines) if lines else "Verified evidence available."


async def narrate(
    plan: Plan,
    evidence_map: dict[str, Any],
    query: str,
    final_state: UiState | None = None,
    verified: bool = True,
    status: str = "VERIFIED",
    mismatches: list[dict] | None = None,
    assumptions: list[str] | None = None,
    config: AgentConfig | None = None,
) -> tuple[str, list[str], list[str]]:
    """Master narration function returning (answer_text, citations, deep_links)."""
    cfg = config or AgentConfig()
    app = app_model()

    citations = list(evidence_map.keys())
    deep_links: list[str] = []

    # Prefer supporting view deep links from evidence
    for ev in evidence_map.values():
        vals = _ev_values(ev)
        view = vals.get("view")
        if isinstance(view, dict) and view.get("url"):
            if view["url"] not in deep_links:
                deep_links.append(view["url"])

    # Fallback to current screen state deep link
    if not deep_links and final_state:
        try:
            deep_links.append(state_to_url(app, final_state))
        except Exception:
            pass

    # 1. Navigation / set_state only: zero-LLM template
    if plan.intent.name in ("navigate", "set_state") and not evidence_map:
        text = narrate_navigation(
            plan,
            final_state,
            verified=verified,
            status=status,
            mismatches=mismatches,
            assumptions=assumptions,
        )
        return text, citations, deep_links

    # 2. Refusal / clarification
    if plan.intent.name in ("clarify_needed", "out_of_scope", "unsafe_action") and plan.clarifying_question:
        return plan.clarifying_question, [], []

    # 3. Analytics / Multi-step narration: build intuitive summary
    evidence_nums = _extract_evidence_numbers(evidence_map)
    filled_text = narrate_analytics_fallback(evidence_map)

    # Verify citation numbers if enabled (ablation A6)
    if cfg.use_citation_check:
        if not check_numbers(filled_text, query, evidence_nums):
            # Safe minimal fallback
            filled_text = "Verified metric findings available in attached evidence cards."

    return filled_text, citations, deep_links
