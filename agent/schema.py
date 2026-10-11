"""Shared types of the V3 pipeline.

* `InterpretationF`  - the flat, provider-neutral structure an LLM (or the rules) produces.
* `Interp`           - the validated interpretation every later layer uses.
* `AgentMemory`      - what the agent remembers per session (last request, open form).
* `TurnContext`      - everything a layer may need during one turn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal["view", "data", "compare", "explain", "investigate", "lookup", "act", "undo", "help", "clarify", "decline"]


# ------------------------------------------------------------------------------------ LLM-facing schema (flat)
class FilterF(BaseModel):
    model_config = ConfigDict(extra="ignore")
    dimension: str
    values: list[str] = Field(default_factory=list)


class SliceF(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = ""
    filters: list[FilterF] = Field(default_factory=list)


class PeriodF(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Literal["primary", "baseline"] = "primary"
    text: str = ""


class FieldF(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    value: str = ""


class InterpretationF(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paraphrase: str = ""
    intent: Intent = "clarify"
    shape: Literal["single", "breakdown", "rank", "trend", "list", "none"] = "none"
    metric: Optional[str] = None
    extra_metrics: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    filters: list[FilterF] = Field(default_factory=list)
    periods: list[PeriodF] = Field(default_factory=list)
    compare_kind: Literal["none", "periods", "slices"] = "none"
    slices: list[SliceF] = Field(default_factory=list)
    top_n: Optional[int] = None
    sort_dir: Optional[Literal["asc", "desc"]] = None
    expected_direction: Literal["up", "down", "none"] = "none"
    target_page: Optional[str] = None
    sort_field: Optional[str] = None
    write_api: Optional[str] = None
    read_api: Optional[str] = None
    entity_id: Optional[str] = None
    form_action: Literal["none", "fill", "submit", "cancel"] = "none"
    fields: list[FieldF] = Field(default_factory=list)
    help_topic: Literal["none", "capabilities", "screen", "howto"] = "none"
    follow_up: bool = False
    clarification: Optional[str] = None
    clarify_options: list[str] = Field(default_factory=list)
    decline_reason: Optional[str] = None
    confidence: float = 0.8


# ------------------------------------------------------------------------------------ validated interpretation
@dataclass
class PeriodSel:
    start: str
    end: str
    preset: str | None
    label: str

    def args(self) -> dict:
        return {"preset": self.preset, "from": self.start, "to": self.end}


@dataclass
class SliceSel:
    label: str
    filters: dict[str, list[str]]


class Notes(list):
    """The assumptions list of an interpretation. `drop` records a note that also means 'part of the request was NOT used'
    (an unknown value, an impossible split), so the answer can say it only partly covers what was asked."""

    def __init__(self, *a):
        super().__init__(*a)
        self.ignored: list[str] = []

    def drop(self, msg: str) -> None:
        self.append(msg)
        self.ignored.append(msg)


@dataclass
class Interp:
    intent: str
    shape: str = "none"
    metric: str | None = None
    extra_metrics: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    # filters the parser guessed from words that only some pages understand ("open" -> ticket status): applied when the
    # chosen page has that filter, never a reason to reject or penalise a page
    soft_filters: dict[str, list[str]] = field(default_factory=dict)
    primary: PeriodSel | None = None
    baseline: PeriodSel | None = None
    compare_kind: str = "none"
    slices: list[SliceSel] = field(default_factory=list)
    top_n: int | None = None
    sort_dir: str | None = None
    expected_direction: str = "none"
    target_page: str | None = None
    sort_field: str | None = None
    write_api: str | None = None
    read_api: str | None = None
    entity_id: str | None = None
    form_action: str = "none"
    fields: dict[str, str] = field(default_factory=dict)
    help_topic: str = "none"
    follow_up: bool = False
    clarification: str | None = None
    decline_reason: str | None = None
    paraphrase: str = ""
    assumptions: list[str] = field(default_factory=list)
    confidence: float = 0.8
    source: str = "llm"                 # llm | rules | fast
    clarify_options: list[str] = field(default_factory=list)
    # a value that could mean more than one field (a customer's state or a seller's state) and was not resolved by the words,
    # the page or an earlier choice: {text, kind, options, values, chosen}
    ambiguities: list[dict] = field(default_factory=list)
    clarify_evidence: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)   # parts of the request that were not understood or could not be used

    def brief(self) -> str:
        parts = [f"intent={self.intent}"]
        if self.shape != "none":
            parts.append(f"shape={self.shape}")
        if self.metric:
            parts.append(f"metric={self.metric}")
        if self.group_by:
            parts.append(f"group_by={self.group_by}")
        if self.filters:
            parts.append(f"filters={self.filters}")
        if self.slices:
            parts.append("slices=" + "; ".join(f"{s.label}:{s.filters}" for s in self.slices))
        if self.primary:
            parts.append(f"period={self.primary.label}")
        if self.baseline:
            parts.append(f"baseline={self.baseline.label}")
        if self.target_page:
            parts.append(f"page={self.target_page}")
        if self.write_api:
            parts.append(f"write={self.write_api}")
        return ", ".join(parts)


# ------------------------------------------------------------------------------------ memory and context
@dataclass
class AgentMemory:
    last: Interp | None = None
    last_answer: str = ""
    last_kind: str = ""                 # what the previous turn did: view | data | write | ...
    form: Any = None                    # contracts.forms.FormState while a form is open
    form_canvas_id: str | None = None
    last_action_kind: str | None = None  # "write" | "state" for "undo that"
    last_entity: tuple[str, str] | None = None   # (entity, id) of the record last looked at: "open a ticket for it"
    turns: int = 0
    prefs: dict[str, str] = field(default_factory=dict)             # ambiguity kind -> the field the user chose (kept for the session)
    pending_choices: dict[str, tuple[str, str]] = field(default_factory=dict)  # clarification option text -> (kind, field)


_MEM: dict[str, AgentMemory] = {}


def get_agent_memory(session_id: str) -> AgentMemory:
    if session_id not in _MEM:
        _MEM[session_id] = AgentMemory()
    return _MEM[session_id]


@dataclass
class TurnContext:
    session: Any
    text: str
    page_ctx: dict
    cfg: Any
    lex: Any
    cap: Any
    mem: AgentMemory
    llm: Any = None
    ann: Any = None
    as_of: str = "2018-08-31"

    @property
    def state(self):
        return self.session.state
