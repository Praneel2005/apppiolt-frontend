"""Flat, provider-neutral Frame schema and rules-first extractor (WP3 in AGENT_V2_DESIGN.md).

Frame is the single intermediate representation across normalisers, LLM, router, and compiler.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from agent.normalise.cues import extract_cues
from agent.normalise.dates import ResolvedDateRange, parse_date_query
from agent.normalise.entities import extract_geo_entities
from agent.normalise.references import resolve_structured_references


ActType = Literal[
    "open", "show", "refine", "compare", "rank", "trend", "explain",
    "investigate", "lookup", "create", "update", "cancel", "undo",
    "help", "decline"
]


class TargetSpec(BaseModel):
    kind: Literal["page", "metric", "entity", "api", "form", "none"] = "none"
    ref: Optional[str] = None


class SliceSpec(BaseModel):
    label: str = "default"
    metric: Optional[str] = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    period: Optional[str] = None


class SortLimitSpec(BaseModel):
    field: Optional[str] = None
    dir: Literal["asc", "desc"] = "desc"
    n: Optional[int] = None


class AmbiguitySpec(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class Frame(BaseModel):
    frame_id: Optional[str] = None
    act: ActType = "show"
    target: TargetSpec = Field(default_factory=TargetSpec)
    slices: list[SliceSpec] = Field(default_factory=list)
    period: Optional[ResolvedDateRange] = None
    group_by: list[str] = Field(default_factory=list)
    sort_limit: Optional[SortLimitSpec] = None
    presentation: Optional[dict[str, Any]] = None
    references: list[dict[str, str]] = Field(default_factory=list)
    patch_of: Optional[str] = None
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    ambiguity: Optional[AmbiguitySpec] = None


def extract_frame_by_rules(
    query: str,
    page_ctx: dict[str, Any],
    memory_last_turn: Any | None = None,
    as_of: str = "2018-08-31",
) -> Optional[Frame]:
    """Fast, deterministic rules-based Frame extractor (~ms).

    If confident, returns a complete Frame (skipping the LLM).
    If ambiguous or complex, returns None so Tier-1 LLM can extract.
    """
    lower = query.lower().strip()

    # 1. Decline / Out-of-scope / Unsafe rules
    if re.search(r"\b(?:delete\s+all|drop\s+tables|ignore\s+previous\s+instructions)\b", lower):
        return Frame(
            act="decline",
            assumptions=["Refused unsafe or unauthorized data deletion attempt."],
            confidence=1.0,
            ambiguity=AmbiguitySpec(
                question="AppPilot cannot delete or modify historical marketplace records.",
                options=[]
            )
        )

    # 2. Undo
    if re.search(r"\b(?:undo|revert|take\s+that\s+back)\b", lower):
        return Frame(
            act="undo",
            assumptions=["Reverting previous state change."],
            confidence=1.0,
        )

    # 3. Normalisers run
    date_res = parse_date_query(query, as_of=as_of)
    geo_res = extract_geo_entities(query)
    cues_res = extract_cues(query)
    refs_res = resolve_structured_references(query, page_ctx, memory_last_turn)

    assumptions: list[str] = []
    if date_res:
        assumptions.append(f"Period resolved to {date_res.display_text}")
    if geo_res.is_hierarchy_subset and geo_res.region and geo_res.states:
        assumptions.append(f"State(s) {geo_res.states} resolved within region {geo_res.region}")
    for n in geo_res.notes:
        assumptions.append(n)

    # 4. Check for Metric keywords
    # Map common phrases to canonical metrics
    metric = None
    if re.search(r"\b(?:aov|average\s+order\s+value)\b", lower):
        metric = "aov"
    elif re.search(r"\b(?:revenue|sales|gmv)\b", lower):
        metric = "revenue"
    elif re.search(r"\b(?:orders|order\s+count)\b", lower):
        metric = "orders"
    elif re.search(r"\b(?:delivery\s+time|delivery\s+days|sla)\b", lower):
        metric = "avg_delivery_days"
    elif re.search(r"\b(?:freight|freight\s+value)\b", lower):
        metric = "freight"
    elif re.search(r"\b(?:cancellation|cancelled)\b", lower):
        metric = "cancel_rate"

    # Build filters map
    flt_map: dict[str, list[str]] = {}
    if geo_res.states:
        filter_name = "seller_state" if geo_res.role == "seller_state" else "customer_state"
        flt_map[filter_name] = geo_res.states

    slices = [
        SliceSpec(
            label="primary",
            metric=metric,
            filters=flt_map,
            period=date_res.preset if date_res else None,
        )
    ]

    # Sort & Limit
    sort_limit = None
    if cues_res.is_rank:
        sort_limit = SortLimitSpec(
            field=metric or "revenue",
            dir=cues_res.rank_direction or "desc",
            n=cues_res.rank_limit or 5,
        )

    # Group by
    group_by = []
    if re.search(r"\bby\s+region\b", lower):
        group_by.append("customer_region")
    elif re.search(r"\bby\s+state\b", lower):
        group_by.append(geo_res.role)
    elif re.search(r"\bby\s+category\b", lower):
        group_by.append("category")
    elif cues_res.is_trend or re.search(r"\b(?:month\s+over\s+month|mom|monthly)\b", lower):
        group_by.append("order_month")

    # Determine Act
    act: ActType = "show"
    if cues_res.is_explanation:
        act = "explain"
    elif cues_res.is_comparison:
        act = "compare"
    elif cues_res.is_rank:
        act = "rank"
    elif cues_res.is_trend:
        act = "trend"
    elif cues_res.is_write_intent:
        act = "create"
    elif metric and not cues_res.is_explanation:
        act = "open"

    return Frame(
        act=act,
        target=TargetSpec(kind="metric" if metric else "none", ref=metric),
        slices=slices,
        period=date_res,
        group_by=group_by,
        sort_limit=sort_limit,
        assumptions=assumptions,
        confidence=0.95 if (metric and act not in ("explain", "investigate")) else 0.6,
    )
