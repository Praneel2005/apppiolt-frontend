"""Intent cues normaliser (WP2 in AGENT_V2_DESIGN.md).

Extracts structural cues for operations, analysis, ranking, trends, and follow-ups.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IntentCues:
    act: str = "operate"  # operate | compare | rank | trend | explain | anomaly | act | converse
    is_comparison: bool = False
    is_trend: bool = False
    is_rank: bool = False
    is_explanation: bool = False
    is_write_intent: bool = False
    is_undo: bool = False
    is_follow_up: bool = False
    rank_limit: Optional[int] = None
    rank_direction: Optional[str] = None  # "asc" | "desc"
    follow_up_kind: Optional[str] = None  # "add_filter" | "replace_filter" | "remove_filter" | "slice_swap"
    markers: list[str] = field(default_factory=list)


def extract_cues(text: str) -> IntentCues:
    lower = text.lower().strip()
    cues = IntentCues()

    # 1. Write / Mutate actions
    write_patterns = [
        r"\b(?:create|restock|open\s+ticket|file\s+ticket|submit|new\s+order|add\s+product|delete|cancel)\b"
    ]
    if any(re.search(p, lower) for p in write_patterns):
        cues.is_write_intent = True
        cues.act = "act"
        cues.markers.append("write_verb")

    # 2. Undo / Revert
    if re.search(r"\b(?:undo|revert|rollback|take\s+that\s+back)\b", lower):
        cues.is_undo = True
        cues.act = "act"
        cues.markers.append("undo")

    # 3. Explain / "Why"
    if re.search(r"\b(?:why|what\s+drove|reason\s+for|what\s+caused|driver\s+analysis)\b", lower):
        cues.is_explanation = True
        cues.act = "explain"
        cues.markers.append("explain")

    # 4. Rank: "top 5", "worst 3", "best", "bottom 10"
    m_top = re.search(r"\b(?:top|best|highest|leading)\s*(\d+)?\b", lower)
    m_bot = re.search(r"\b(?:bottom|worst|lowest)\s*(\d+)?\b", lower)
    if m_top:
        cues.is_rank = True
        cues.act = "rank"
        cues.rank_direction = "desc"
        cues.rank_limit = int(m_top.group(1)) if m_top.group(1) else 5
        cues.markers.append("rank_top")
    elif m_bot:
        cues.is_rank = True
        cues.act = "rank"
        cues.rank_direction = "asc"
        cues.rank_limit = int(m_bot.group(1)) if m_bot.group(1) else 5
        cues.markers.append("rank_bottom")

    # 5. Trend: "over time", "month over month", "MoM", "YoY", "growth", "trend"
    trend_patterns = [
        r"\b(?:over\s+time|month\s+over\s+month|mom|year\s+over\s+year|yoy|trend|growth|trajectory)\b"
    ]
    if any(re.search(p, lower) for p in trend_patterns):
        cues.is_trend = True
        if cues.act == "operate":
            cues.act = "trend"
        cues.markers.append("trend")

    # 6. Comparison: "compare X and Y", "vs", "versus", "against"
    compare_patterns = [
        r"\b(?:compare|comparison|versus|vs\.?|against)\b",
    ]
    if any(re.search(p, lower) for p in compare_patterns):
        cues.is_comparison = True
        cues.act = "compare"
        cues.markers.append("compare")

    # 7. Follow-up cues: "also", "only", "instead", "remove", "now by", "same for"
    if re.search(r"\b(?:same\s+for|what\s+about)\b", lower):
        cues.is_follow_up = True
        cues.follow_up_kind = "slice_swap"
        cues.markers.append("follow_up_swap")
    elif re.search(r"\b(?:instead|replace\s+with)\b", lower):
        cues.is_follow_up = True
        cues.follow_up_kind = "replace_filter"
        cues.markers.append("follow_up_replace")
    elif re.search(r"\b(?:also|and\s+also|add\s+filter)\b", lower):
        cues.is_follow_up = True
        cues.follow_up_kind = "add_filter"
        cues.markers.append("follow_up_add")
    elif re.search(r"\b(?:remove|clear|drop)\s+(?:filter|state|date)?\b", lower):
        cues.is_follow_up = True
        cues.follow_up_kind = "remove_filter"
        cues.markers.append("follow_up_remove")

    return cues
