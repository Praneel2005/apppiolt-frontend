"""Entity and hierarchy normaliser (WP2 in AGENT_V2_DESIGN.md).

Maps state synonyms, expands regions to constituent states, resolves geographic
hierarchy (SP ⊂ Southeast), and disambiguates customer vs seller state roles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from agent.capabilities import (
    BRAZIL_REGIONS,
    STATE_SYNONYMS,
    STATE_TO_REGION,
)


@dataclass
class ResolvedGeo:
    states: list[str] = field(default_factory=list)
    region: Optional[str] = None
    role: str = "customer_state"  # "customer_state" | "seller_state" | "both"
    is_hierarchy_subset: bool = False
    notes: list[str] = field(default_factory=list)


def extract_geo_entities(text: str) -> ResolvedGeo:
    """Extracts states, regions, hierarchy relationships, and role words from text."""
    lower = text.lower()
    res = ResolvedGeo()

    # 1. Role disambiguation
    seller_cues = [
        r"\bsellers?\b",
        r"\bseller\s+state\b",
        r"\bsellers?\s+in\b",
        r"\bsellers?\s+from\b",
        r"\bby\s+seller\b",
    ]
    if any(re.search(cue, lower) for cue in seller_cues):
        res.role = "seller_state"
    else:
        res.role = "customer_state"

    # 2. Extract regions
    for reg_name in BRAZIL_REGIONS:
        reg_pattern = rf"\b{re.escape(reg_name.lower())}\b"
        if re.search(reg_pattern, lower):
            res.region = reg_name
            break

    # 3. Extract states (check multi-word synonyms first, then single word/acronyms)
    found_states: set[str] = set()

    # Sort synonyms by length descending to match "rio de janeiro" before "rio"
    sorted_synonyms = sorted(STATE_SYNONYMS.keys(), key=lambda k: len(k), reverse=True)
    for syn in sorted_synonyms:
        # Match whole words
        pattern = rf"\b{re.escape(syn)}\b"
        if re.search(pattern, lower):
            st = STATE_SYNONYMS[syn]
            found_states.add(st)
            # Remove matched portion to avoid sub-matching (e.g. "rio" inside "rio de janeiro")
            lower = re.sub(pattern, " ", lower)

    res.states = sorted(list(found_states))

    # 4. Hierarchy check: if region and state are both present
    if res.region and res.states:
        reg_states = BRAZIL_REGIONS[res.region]
        all_subset = all(st in reg_states for st in res.states)
        if all_subset:
            res.is_hierarchy_subset = True
            res.notes.append(
                f"State(s) {res.states} are contained within Region {res.region}."
            )
        else:
            out_of_reg = [st for st in res.states if st not in reg_states]
            res.notes.append(
                f"Warning: State(s) {out_of_reg} are outside Region {res.region}."
            )

    return res


def disambiguate_filter_for_page(
    page_supported_filters: list[str], geo: ResolvedGeo
) -> dict[str, list[str]]:
    """Assigns resolved states to the best filter supported by the page.
    e.g. If page supports 'seller_state' but not 'customer_state', route states there.
    """
    assigned = {}
    if not geo.states:
        return assigned

    # If page supports customer_state and seller_state:
    if "customer_state" in page_supported_filters and "seller_state" in page_supported_filters:
        target_f = "seller_state" if geo.role == "seller_state" else "customer_state"
        assigned[target_f] = geo.states
    elif "customer_state" in page_supported_filters:
        assigned["customer_state"] = geo.states
    elif "seller_state" in page_supported_filters:
        assigned["seller_state"] = geo.states
    elif "state" in page_supported_filters:
        assigned["state"] = geo.states

    return assigned
