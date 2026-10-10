"""Date normaliser (WP2 in AGENT_V2_DESIGN.md).

Parses natural language date expressions and resolves them against app.as_of_date.
Crucially: 'last 3 months' NEVER maps to 'last_quarter' (Audit Finding 2).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from contracts.dates import resolve as contract_resolve


@dataclass(frozen=True)
class ResolvedDateRange:
    preset: str
    from_date: str
    to_date: str
    display_text: str
    is_comparison: bool = False
    comparison_preset: Optional[str] = None
    warning: Optional[str] = None


MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

WORD_TO_NUM: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twelve": 12,
}


def parse_date_query(text: str, as_of: str = "2018-08-31") -> Optional[ResolvedDateRange]:
    """Extracts and resolves date ranges from user query.
    Returns ResolvedDateRange if a date expression is found, else None.
    """
    lower = text.lower().strip()
    as_of_date = date.fromisoformat(as_of)

    # 1. "last 3 months" / "past 3 months" / "last three months"
    # CRITICAL AUDIT FIX: Must NEVER map to last_quarter!
    m_months = re.search(r"(?:last|past)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+months?", lower)
    if m_months:
        raw_n = m_months.group(1)
        n = int(raw_n) if raw_n.isdigit() else WORD_TO_NUM.get(raw_n, 3)
        preset = f"months:{n}" if n != 12 else "last_12_months"
        frm, to = contract_resolve(preset, as_of)
        return ResolvedDateRange(
            preset=preset,
            from_date=frm,
            to_date=to,
            display_text=f"last {n} months ({frm} to {to})",
        )

    # 2. "last month" / "past month"
    if re.search(r"\b(?:last|past)\s+month\b", lower):
        frm, to = contract_resolve("last_month", as_of)
        return ResolvedDateRange(
            preset="last_month",
            from_date=frm,
            to_date=to,
            display_text=f"last month ({frm} to {to})",
        )

    # 3. "this month"
    if re.search(r"\bthis\s+month\b", lower):
        frm, to = contract_resolve("this_month", as_of)
        return ResolvedDateRange(
            preset="this_month",
            from_date=frm,
            to_date=to,
            display_text=f"this month ({frm} to {to})",
        )

    # 4. "last quarter" / "past quarter"
    if re.search(r"\b(?:last|past)\s+quarter\b", lower):
        frm, to = contract_resolve("last_quarter", as_of)
        return ResolvedDateRange(
            preset="last_quarter",
            from_date=frm,
            to_date=to,
            display_text=f"last quarter ({frm} to {to})",
        )

    # 5. "this quarter"
    if re.search(r"\bthis\s+quarter\b", lower):
        frm, to = contract_resolve("this_quarter", as_of)
        return ResolvedDateRange(
            preset="this_quarter",
            from_date=frm,
            to_date=to,
            display_text=f"this quarter ({frm} to {to})",
        )

    # 6. Specific Quarter: "Q1 2018", "2018 Q2", "Q3 of 2017", "2018q2"
    m_q = re.search(r"\bq([1-4])\s*(?:of\s*)?(\d{4})\b|\b(\d{4})\s*q([1-4])\b", lower)
    if m_q:
        if m_q.group(1):
            q, y = int(m_q.group(1)), int(m_q.group(2))
        else:
            y, q = int(m_q.group(3)), int(m_q.group(4))
        preset = f"quarter:{y}Q{q}"
        frm, to = contract_resolve(preset, as_of)
        return ResolvedDateRange(
            preset=preset,
            from_date=frm,
            to_date=to,
            display_text=f"{y} Q{q} ({frm} to {to})",
        )

    # 7. "since <month> [year]" / "since <YYYY-MM-DD>"
    m_since_date = re.search(r"\bsince\s+(\d{4}-\d{2}-\d{2})\b", lower)
    if m_since_date:
        since_iso = m_since_date.group(1)
        frm, to = contract_resolve(f"since:{since_iso}", as_of)
        return ResolvedDateRange(
            preset=f"since:{since_iso}",
            from_date=frm,
            to_date=to,
            display_text=f"since {since_iso} ({frm} to {to})",
        )

    m_since_month = re.search(r"\bsince\s+([a-z]+)(?:\s+(\d{4}))?\b", lower)
    if m_since_month and m_since_month.group(1) in MONTH_NAMES:
        m_num = MONTH_NAMES[m_since_month.group(1)]
        y = int(m_since_month.group(2)) if m_since_month.group(2) else as_of_date.year
        since_iso = f"{y:04d}-{m_num:02d}-01"
        frm, to = contract_resolve(f"since:{since_iso}", as_of)
        return ResolvedDateRange(
            preset=f"since:{since_iso}",
            from_date=frm,
            to_date=to,
            display_text=f"since {m_since_month.group(1).title()} {y} ({frm} to {to})",
        )

    # 8. Specific Month: "March 2018", "in March 2017", "for March", "March"
    m_month_year = re.search(r"\b(?:in|for)?\s*([a-z]+)\s+(\d{4})\b", lower)
    if m_month_year and m_month_year.group(1) in MONTH_NAMES:
        m_num = MONTH_NAMES[m_month_year.group(1)]
        y = int(m_month_year.group(2))
        preset = f"month:{y:04d}-{m_num:02d}"
        frm, to = contract_resolve(preset, as_of)
        return ResolvedDateRange(
            preset=preset,
            from_date=frm,
            to_date=to,
            display_text=f"{m_month_year.group(1).title()} {y} ({frm} to {to})",
        )

    # Bare month name like "in March" or "for July"
    m_bare_month = re.search(r"\b(?:in|for|of)\s+([a-z]+)\b", lower)
    if m_bare_month and m_bare_month.group(1) in MONTH_NAMES:
        m_num = MONTH_NAMES[m_bare_month.group(1)]
        y = as_of_date.year
        if m_num > as_of_date.month:
            y -= 1  # If month hasn't occurred yet in as_of year, refer to previous year
        preset = f"month:{y:04d}-{m_num:02d}"
        frm, to = contract_resolve(preset, as_of)
        return ResolvedDateRange(
            preset=preset,
            from_date=frm,
            to_date=to,
            display_text=f"{m_bare_month.group(1).title()} {y} ({frm} to {to})",
        )

    # 9. "last year" / "this year"
    if re.search(r"\blast\s+year\b", lower):
        frm, to = contract_resolve("last_year", as_of)
        return ResolvedDateRange(
            preset="last_year",
            from_date=frm,
            to_date=to,
            display_text=f"last year ({frm} to {to})",
        )

    if re.search(r"\bthis\s+year\b", lower):
        frm, to = contract_resolve("this_year", as_of)
        return ResolvedDateRange(
            preset="this_year",
            from_date=frm,
            to_date=to,
            display_text=f"this year ({frm} to {to})",
        )

    # 10. Explicit year: "2017", "in 2017", "growth 2017"
    m_year = re.search(r"\b(201[5-9]|202[0-5])\b", lower)
    if m_year:
        y = int(m_year.group(1))
        preset = f"year:{y}"
        frm, to = contract_resolve(preset, as_of)
        warning = None
        if y < 2016 or y > 2018:
            warning = f"Data window is 2016-2018; query for {y} may contain sparse or no data."
        return ResolvedDateRange(
            preset=preset,
            from_date=frm,
            to_date=to,
            display_text=f"{y} ({frm} to {to})",
            warning=warning,
        )

    # 11. "last 7 days" / "last 30 days" / "last 90 days" / "last week"
    if re.search(r"\blast\s+week\b", lower):
        frm, to = contract_resolve("last_7_days", as_of)
        return ResolvedDateRange(
            preset="last_7_days",
            from_date=frm,
            to_date=to,
            display_text=f"last 7 days ({frm} to {to})",
        )
    m_days = re.search(r"\blast\s+(\d+)\s+days?\b", lower)
    if m_days:
        d_val = int(m_days.group(1))
        preset = f"days:{d_val}" if d_val not in (7, 30, 90) else f"last_{d_val}_days"
        frm, to = contract_resolve(preset, as_of)
        return ResolvedDateRange(
            preset=preset,
            from_date=frm,
            to_date=to,
            display_text=f"last {d_val} days ({frm} to {to})",
        )

    return None
