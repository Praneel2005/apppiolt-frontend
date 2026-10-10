"""Normalisers package (WP2 in AGENT_V2_DESIGN.md).

Fast, deterministic understanding layer:
- dates: parse natural language dates, resolve against app.as_of_date
- entities: geo hierarchy (state ⊂ region), state synonyms, role disambiguation
- cues: rank, trend, compare, explain, write verbs, follow-up markers
- references: contextual pronoun and anaphora resolution
"""
from agent.normalise.dates import ResolvedDateRange, parse_date_query
from agent.normalise.entities import (
    ResolvedGeo,
    disambiguate_filter_for_page,
    extract_geo_entities,
)
from agent.normalise.cues import IntentCues, extract_cues
from agent.normalise.references import resolve_structured_references

__all__ = [
    "ResolvedDateRange",
    "parse_date_query",
    "ResolvedGeo",
    "extract_geo_entities",
    "disambiguate_filter_for_page",
    "IntentCues",
    "extract_cues",
    "resolve_structured_references",
]
