"""Facts registry: every number an answer states is a Fact (F1, F2, ...) with a pre-formatted string.

The deterministic composer and the LLM synthesis both reference facts by id; the claim checker verifies text against them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def fmt_number(value: float | int | None, unit: str, signed: bool = False) -> str:
    """Format a metric value by unit. `signed` adds an explicit + or - (for changes)."""
    if value is None:
        return "n/a"
    v = float(value)
    sign = ""
    if signed:
        sign = "+" if v > 0 else "-" if v < 0 else ""
        v = abs(v)
    if unit == "BRL":
        body = f"R$ {v:,.2f}"
    elif unit == "ratio":
        body = f"{v * 100:.1f} pp" if signed else f"{v * 100:.1f}%"
    elif unit == "days":
        body = f"{v:,.1f} days"
    elif unit == "score":
        body = f"{v:.2f}"
    elif unit == "count":
        body = f"{v:,.0f}"
    else:
        body = f"{v:,.2f}" if abs(v) < 1000 and v != int(v) else f"{v:,.0f}"
    return sign + body


def fmt_pct(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "n/a"
    v = float(value)
    return f"{v:+.1f}%" if signed else f"{abs(v):.1f}%"


@dataclass
class Fact:
    id: str
    label: str
    text: str
    value: float | None = None
    unit: str = ""
    kind: str = "value"          # value | delta | pct | share | text
    sign: int = 0                # -1 / 0 / +1 for delta and pct facts
    evidence: str | None = None


@dataclass
class FactSheet:
    facts: dict[str, Fact] = field(default_factory=dict)
    _n: int = 0

    def _new(self, label: str, text: str, **kw: Any) -> Fact:
        self._n += 1
        f = Fact(id=f"F{self._n}", label=label, text=text, **kw)
        self.facts[f.id] = f
        return f

    def value(self, label: str, value: float | None, unit: str, evidence: str | None = None) -> Fact:
        return self._new(label, fmt_number(value, unit), value=None if value is None else float(value), unit=unit,
                         kind="value", evidence=evidence)

    def delta(self, label: str, value: float | None, unit: str, evidence: str | None = None) -> Fact:
        sign = 0 if value is None else (1 if value > 0 else -1 if value < 0 else 0)
        return self._new(label, fmt_number(value, unit, signed=True), value=None if value is None else float(value),
                         unit=unit, kind="delta", sign=sign, evidence=evidence)

    def pct(self, label: str, value: float | None, evidence: str | None = None, signed: bool = True) -> Fact:
        sign = 0 if value is None else (1 if value > 0 else -1 if value < 0 else 0)
        return self._new(label, fmt_pct(value, signed), value=None if value is None else float(value), unit="%",
                         kind="pct", sign=sign, evidence=evidence)

    def share(self, label: str, value: float | None, evidence: str | None = None) -> Fact:
        return self._new(label, fmt_pct(value, signed=False), value=None if value is None else float(value), unit="%",
                         kind="share", evidence=evidence)

    def text(self, label: str, text: str) -> Fact:
        return self._new(label, text, kind="text")

    def render(self, template: str) -> str:
        return re.sub(r"\{(F\d+)\}", lambda m: self.facts[m.group(1)].text if m.group(1) in self.facts else m.group(0), template)

    def lines(self) -> list[str]:
        return [f"{f.id} = {f.label}: {f.text}" for f in self.facts.values()]

    def all_texts(self) -> list[str]:
        return [f.text for f in self.facts.values()]
