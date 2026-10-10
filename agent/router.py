"""Router and capability resolver (WP3 in AGENT_V2_DESIGN.md).

Routes a structured Frame to the appropriate execution skill:
- Converse: help, greetings, refusals, clarifications
- Act: forms, mutations, preview/confirm/undo
- Operate: atomic view navigation and filter/date refinement
- Analyze: multi-slice comparison, trend, drivers, confounders, and workspace
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from agent.capabilities import CapabilityModel, get_capability_model
from agent.frame import Frame


RouteSkill = Literal["Converse", "Act", "Operate", "Analyze"]


@dataclass
class RoutingDecision:
    skill: RouteSkill
    target_page_id: Optional[str] = None
    target_api_id: Optional[str] = None
    is_in_place_refinement: bool = False
    routed_reason: str = ""
    missing_capabilities: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def route_frame(
    frame: Frame,
    current_page_id: Optional[str] = None,
    cap_model: Optional[CapabilityModel] = None,
) -> RoutingDecision:
    """Evaluates the Frame against the capability model and chooses the execution skill and target."""
    cm = cap_model or get_capability_model()
    assumptions = list(frame.assumptions)

    # 1. Converse (help, decline, undo)
    if frame.act in ("help", "decline"):
        return RoutingDecision(
            skill="Converse",
            routed_reason=f"Frame act is {frame.act}",
            assumptions=assumptions,
        )

    # 2. Act (mutations, write APIs, forms)
    if frame.act in ("create", "update", "cancel"):
        return RoutingDecision(
            skill="Act",
            routed_reason=f"Frame act is write/mutation ({frame.act})",
            assumptions=assumptions,
        )

    # 3. Analyze vs Operate
    # Collect requested filters, metric, group_by, and date requirements
    primary_slice = frame.slices[0] if frame.slices else None
    req_filters = set(primary_slice.filters.keys()) if primary_slice else set()
    req_metric = primary_slice.metric if primary_slice else frame.target.ref
    req_group_by = set(frame.group_by)
    req_date = bool(frame.period)

    # If the act is explicitly explain, compare across slices, or drivers
    if frame.act in ("explain", "investigate"):
        return RoutingDecision(
            skill="Analyze",
            routed_reason=f"Analytic deep dive required for act '{frame.act}'",
            assumptions=assumptions,
        )

    # If multi-slice comparison (e.g. comparing 2 unrelated slices)
    if len(frame.slices) > 1 and frame.act == "compare":
        return RoutingDecision(
            skill="Analyze",
            routed_reason="Multi-slice comparison routes to analysis engine",
            assumptions=assumptions,
        )

    # Check for pages that support requested metric, filters, group-by, and dates
    candidate_pages = [
        p.page_id
        for p in cm.pages_supporting(
            filters=list(req_filters),
            group_by=list(req_group_by),
            metric=req_metric,
            supports_date=req_date,
        )
    ]

    # Preference 1: Current page if it supports everything
    if current_page_id and current_page_id in candidate_pages:
        return RoutingDecision(
            skill="Operate",
            target_page_id=current_page_id,
            is_in_place_refinement=True,
            routed_reason=f"Refining current page '{current_page_id}' in-place",
            assumptions=assumptions,
        )

    # Preference 2: Best covering page
    if candidate_pages:
        best_pid = candidate_pages[0]
        # If current page was missing a filter but another page covers it:
        if current_page_id and current_page_id != best_pid:
            curr_missing = cm.missing_capabilities(current_page_id, list(req_filters))
            if curr_missing:
                assumptions.append(
                    f"Routed from '{current_page_id}' to '{best_pid}' to support filter(s): {curr_missing}."
                )

        return RoutingDecision(
            skill="Operate",
            target_page_id=best_pid,
            is_in_place_refinement=False,
            routed_reason=f"Navigating to page '{best_pid}' which fully covers capabilities",
            assumptions=assumptions,
        )

    # Preference 3: Best page for the metric even if some filters require workspace
    if req_metric:
        metric_pages = [
            pid for pid, p in cm.pages.items() if req_metric in p.metrics
        ]
        if metric_pages:
            best_m_pid = metric_pages[0]
            missing = cm.missing_capabilities(best_m_pid, list(req_filters))
            if not missing:
                return RoutingDecision(
                    skill="Operate",
                    target_page_id=best_m_pid,
                    routed_reason=f"Navigating to metric page '{best_m_pid}'",
                    assumptions=assumptions,
                )
            else:
                # If a filter is missing on this page, do NOT silently drop it.
                # If no other page covers it, route to Analyze!
                return RoutingDecision(
                    skill="Analyze",
                    target_page_id=best_m_pid,
                    missing_capabilities=missing,
                    routed_reason=f"No standard page supports metric '{req_metric}' with filter(s) {missing}; routing to Analysis Workspace",
                    assumptions=assumptions + [f"Analysis required because page '{best_m_pid}' cannot filter by {missing}."],
                )

    # Fallback to Analyze
    return RoutingDecision(
        skill="Analyze",
        routed_reason="No covering page found; routing to analysis engine",
        assumptions=assumptions,
    )
