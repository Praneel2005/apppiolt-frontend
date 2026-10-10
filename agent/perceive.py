"""L1 Perceive: deterministic annotation of a user message.

Finds every period, metric, dimension, entity value and cue in the text, assigns roles to periods
(primary / baseline) and measures how much of the sentence it can account for (`coverage`). It never decides what the
user wants; that is the interpreter's job. Dates and entity values found here are authoritative: the LLM only chooses
how they are used.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from agent.lexicon import Lexicon, Tok, ValueCand, norm_text, stem, tokenize
from contracts.dates import resolve as resolve_preset

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12}
_NUM = r"(\d{1,3}|" + "|".join(NUMWORDS) + r")"
_UNIT = r"(day|week|month|quarter|year)s?"
_YEAR = r"(20[12]\d)"
_PRE = r"(?:last|past|previous|prior)"


# ------------------------------------------------------------------------------------ date helpers
def _d(s: str) -> date:
    return date.fromisoformat(s)


def month_range(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def quarter_range(y: int, q: int) -> tuple[date, date]:
    return date(y, 3 * q - 2, 1), month_range(y, 3 * q)[1]


def shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    i = y * 12 + (m - 1) + delta
    return i // 12, i % 12 + 1


def infer_year(month: int, as_of: date) -> int:
    return as_of.year if month <= as_of.month else as_of.year - 1


def describe_range(start: str, end: str) -> str:
    s, e = _d(start), _d(end)
    if s.day == 1 and e == month_range(s.year, s.month)[1]:
        return s.strftime("%B %Y")
    if s.month in (1, 4, 7, 10) and s.day == 1 and e == quarter_range(s.year, (s.month - 1) // 3 + 1)[1]:
        return f"Q{(s.month - 1) // 3 + 1} {s.year}"
    if s == date(s.year, 1, 1) and e == date(s.year, 12, 31):
        return str(s.year)
    if s == e:
        return s.isoformat()
    return f"{start} to {end}"


@dataclass
class PeriodMention:
    a: int                # first token index
    b: int                # last token index (inclusive)
    start: str
    end: str
    preset: str | None
    label: str
    kind: str
    char_start: int = 0
    char_end: int = 0

    @property
    def length_days(self) -> int:
        return (_d(self.end) - _d(self.start)).days + 1


@dataclass
class MetricMention:
    a: int
    b: int
    metric_id: str
    text: str


@dataclass
class DimMention:
    a: int
    b: int
    dim: str
    group: bool
    text: str


@dataclass
class ValueMention:
    a: int
    b: int
    cands: list[ValueCand]
    text: str
    partial: bool = False


@dataclass
class Annotations:
    text: str
    norm: str
    toks: list[Tok]
    periods: list[PeriodMention] = field(default_factory=list)
    roles: list[tuple[PeriodMention, str]] = field(default_factory=list)
    metrics: list[MetricMention] = field(default_factory=list)
    dims: list[DimMention] = field(default_factory=list)
    values: list[ValueMention] = field(default_factory=list)
    cues: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    top_n: int | None = None
    sort_dir: str | None = None
    seller_context: bool = False
    expected_direction: str = "none"
    unexplained: list[str] = field(default_factory=list)
    coverage: float = 1.0
    content_tokens: int = 0

    def has(self, *names: str) -> bool:
        return any(n in self.cues for n in names)

    @property
    def metric_ids(self) -> list[str]:
        seen: list[str] = []
        for m in self.metrics:
            if m.metric_id not in seen:
                seen.append(m.metric_id)
        return seen

    def summary(self) -> dict:
        return {
            "periods": [{"text": p.label, "from": p.start, "to": p.end, "role": r} for p, r in self.roles],
            "metrics": self.metric_ids,
            "dims": [{"dim": d.dim, "group": d.group} for d in self.dims],
            "values": [{"text": v.text, "options": [f"{c.key}={c.canonical}" for c in v.cands]} for v in self.values],
            "cues": sorted(self.cues),
            "top_n": self.top_n,
            "coverage": round(self.coverage, 2),
            "unexplained": self.unexplained,
        }


# ------------------------------------------------------------------------------------ period finder
@dataclass
class _Cand:
    cs: int
    ce: int
    start: date
    end: date
    preset: str | None
    kind: str


def _num(raw: str) -> int:
    return int(raw) if raw.isdigit() else NUMWORDS.get(raw, 1)


def _preset_cand(m: re.Match, preset: str, as_of: str, kind: str) -> _Cand:
    f, t = resolve_preset(preset, as_of)
    return _Cand(m.start(), m.end(), _d(f), _d(t), preset, kind)


def _between_cand(m: re.Match, s: date, e: date, kind: str) -> _Cand:
    return _Cand(m.start(), m.end(), s, e, f"between:{s.isoformat()}:{e.isoformat()}", kind)


def find_periods(norm: str, as_of_s: str, comparing: bool = False) -> list[_Cand]:
    """English time-expression grammar. `comparing` (from the lexicon's compare cue) stops 'between X and Y' being read as one range."""
    a = _d(as_of_s)
    cands: list[_Cand] = []
    compare_words = comparing

    # --- explicit dates and ranges first
    for m in re.finditer(r"\b(20\d\d-\d\d-\d\d)\s*(?:to|and|until|through|-)\s*(20\d\d-\d\d-\d\d)\b", norm):
        s, e = _d(m.group(1)), _d(m.group(2))
        if s <= e:
            cands.append(_between_cand(m, s, e, "range"))
    for m in re.finditer(r"\b(20\d\d-\d\d-\d\d)\b", norm):
        try:
            d = _d(m.group(1))
        except ValueError:
            continue
        cands.append(_between_cand(m, d, d, "day"))

    # --- last / past N units
    for m in re.finditer(rf"\b{_PRE}\s+{_NUM}\s+(?:full\s+|complete\s+|whole\s+)(month|quarter|year)s?\b", norm):
        n, unit = _num(m.group(1)), m.group(2)
        if unit == "month":
            y, mo = shift_month(a.year, a.month, -n)
            cands.append(_between_cand(m, date(y, mo, 1), month_range(*shift_month(a.year, a.month, -1))[1], "complete"))
        elif unit == "quarter":
            q = (a.month - 1) // 3 + 1
            qs = quarter_range(a.year, q)[0]
            y, mo = shift_month(qs.year, qs.month, -3 * n)
            cands.append(_between_cand(m, date(y, mo, 1), qs - timedelta(days=1), "complete"))
        else:
            cands.append(_between_cand(m, date(a.year - n, 1, 1), date(a.year - 1, 12, 31), "complete"))
    for m in re.finditer(rf"\b{_PRE}\s+{_NUM}\s+{_UNIT}\b", norm):
        if re.match(r"\s+(?:full|complete|whole)\b", norm[m.end():m.end() + 10]):
            continue
        n, unit = _num(m.group(1)), m.group(2)
        if n <= 0:
            continue
        if unit == "day":
            preset = {7: "last_7_days", 30: "last_30_days", 90: "last_90_days"}.get(n, f"days:{n}")
            cands.append(_preset_cand(m, preset, as_of_s, "relative"))
        elif unit == "week":
            cands.append(_preset_cand(m, f"weeks:{n}", as_of_s, "relative"))
        elif unit == "month":
            preset = "last_12_months" if n == 12 else f"months:{n}"
            cands.append(_preset_cand(m, preset, as_of_s, "relative"))
        elif unit == "quarter":
            q = (a.month - 1) // 3 + 1
            qs = quarter_range(a.year, q)[0]
            y, mo = shift_month(qs.year, qs.month, -3 * n)
            cands.append(_between_cand(m, date(y, mo, 1), qs - timedelta(days=1), "relative"))
        else:
            cands.append(_between_cand(m, date(a.year - n, 1, 1), date(a.year - 1, 12, 31), "relative"))

    # --- last / this unit
    for m in re.finditer(rf"\b{_PRE}\s+(week|month|quarter|year)\b", norm):
        unit = m.group(1)
        if unit == "week":
            monday = a - timedelta(days=a.weekday())
            cands.append(_between_cand(m, monday - timedelta(days=7), monday - timedelta(days=1), "relative"))
        else:
            cands.append(_preset_cand(m, f"last_{unit}", as_of_s, "relative"))
    for m in re.finditer(r"\b(?:this|current)\s+(week|month|quarter|year)\b", norm):
        unit = m.group(1)
        if unit == "week":
            cands.append(_between_cand(m, a - timedelta(days=a.weekday()), a, "relative"))
        else:
            cands.append(_preset_cand(m, f"this_{unit}", as_of_s, "relative"))
    for m in re.finditer(r"\b(?:year to date|ytd)\b", norm):
        cands.append(_preset_cand(m, "year_to_date", as_of_s, "relative"))
    for m in re.finditer(r"\b(?:month to date|mtd)\b", norm):
        cands.append(_preset_cand(m, "this_month", as_of_s, "relative"))
    for m in re.finditer(r"\b(?:quarter to date|qtd)\b", norm):
        cands.append(_preset_cand(m, "this_quarter", as_of_s, "relative"))
    for m in re.finditer(r"\byesterday\b", norm):
        cands.append(_between_cand(m, a - timedelta(days=1), a - timedelta(days=1), "day"))
    for m in re.finditer(r"\btoday\b", norm):
        cands.append(_between_cand(m, a, a, "day"))

    # --- quarters and half years
    for m in re.finditer(rf"\bq([1-4])\s*(?:of\s+)?{_YEAR}\b|\b{_YEAR}\s*q([1-4])\b", norm):
        q = int(m.group(1) or m.group(4))
        y = int(m.group(2) or m.group(3))
        cands.append(_preset_cand(m, f"quarter:{y}Q{q}", as_of_s, "quarter"))
    for m in re.finditer(rf"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?{_YEAR}\b", norm):
        q = ["first", "second", "third", "fourth"].index(m.group(1)) + 1
        cands.append(_preset_cand(m, f"quarter:{m.group(2)}Q{q}", as_of_s, "quarter"))
    for m in re.finditer(rf"\bh([12])\s*(?:of\s+)?{_YEAR}\b|\b{_YEAR}\s*h([12])\b", norm):
        h = int(m.group(1) or m.group(4))
        y = int(m.group(2) or m.group(3))
        s, e = (date(y, 1, 1), date(y, 6, 30)) if h == 1 else (date(y, 7, 1), date(y, 12, 31))
        cands.append(_between_cand(m, s, e, "half"))
    for m in re.finditer(r"\bq([1-4])\b(?!\s*(?:of\s+)?20\d\d)", norm):
        cands.append(_preset_cand(m, f"quarter:{a.year}Q{m.group(1)}", as_of_s, "quarter"))

    # --- month ranges "from january to march [2018]" / "between ... and ..." (not when comparing)
    if not compare_words:
        for m in re.finditer(rf"\b(?:from|between)\s+({_MON})(?:\s+{_YEAR})?\s+(?:to|and|through|until|till)\s+({_MON})(?:\s+{_YEAR})?\b", norm):
            m1, m2 = MONTHS[m.group(1)], MONTHS[m.group(3)]
            y2 = int(m.group(4)) if m.group(4) else infer_year(m2, a)
            y1 = int(m.group(2)) if m.group(2) else (y2 if m1 <= m2 else y2 - 1)
            cands.append(_between_cand(m, date(y1, m1, 1), month_range(y2, m2)[1], "range"))

    # --- since
    for m in re.finditer(r"\bsince\s+(20\d\d-\d\d-\d\d)\b", norm):
        cands.append(_preset_cand(m, f"since:{m.group(1)}", as_of_s, "since"))
    for m in re.finditer(rf"\bsince\s+({_MON})(?:\s+{_YEAR})?\b", norm):
        mo = MONTHS[m.group(1)]
        y = int(m.group(2)) if m.group(2) else infer_year(mo, a)
        cands.append(_preset_cand(m, f"since:{y:04d}-{mo:02d}-01", as_of_s, "since"))
    for m in re.finditer(rf"\bsince\s+{_YEAR}\b", norm):
        cands.append(_preset_cand(m, f"since:{m.group(1)}-01-01", as_of_s, "since"))

    # --- month + year, then bare months after a preposition
    for m in re.finditer(rf"\b({_MON})\s*,?\s+{_YEAR}\b", norm):
        mo, y = MONTHS[m.group(1)], int(m.group(2))
        cands.append(_preset_cand(m, f"month:{y:04d}-{mo:02d}", as_of_s, "month"))
    month_preps = r"in|for|during|of|throughout|from|on|vs|versus|than|against|and|to|with|through|until|till|by"
    for m in re.finditer(rf"\b({month_preps})\s+({_MON})\b(?!\s*,?\s*20\d\d)", norm):
        if m.group(2) == "may" and m.group(1) not in ("in", "for", "of", "during", "throughout", "from", "on", "vs", "versus", "than"):
            continue  # "may" is also a verb
        mo = MONTHS[m.group(2)]
        y = infer_year(mo, a)
        c = _preset_cand(m, f"month:{y:04d}-{mo:02d}", as_of_s, "month")
        c.cs = m.start(2)
        cands.append(c)

    # --- bare year
    for m in re.finditer(rf"\b{_YEAR}\b", norm):
        cands.append(_preset_cand(m, f"year:{m.group(1)}", as_of_s, "year"))

    # --- keep non-overlapping, longest first
    cands.sort(key=lambda c: (-(c.ce - c.cs), c.cs))
    kept: list[_Cand] = []
    for c in cands:
        if all(c.ce <= k.cs or c.cs >= k.ce for k in kept):
            kept.append(c)
    kept.sort(key=lambda c: c.cs)
    return kept


# ------------------------------------------------------------------------------------ annotator
_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# Phrase lists for baseline markers, grouping words, polite words, etc. are data (lexicon "language"), not code.


def _overlaps(tok: Tok, spans: list[tuple[int, int]]) -> bool:
    return any(tok.start < e and tok.end > s for s, e in spans)


def _is_shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) > 6 and sum(c.isupper() for c in letters) / len(letters) > 0.6


def perceive(text: str, lex: Lexicon, as_of: str | None = None) -> Annotations:
    as_of = as_of or lex.as_of
    norm = norm_text(text)
    toks = tokenize(norm)
    ann = Annotations(text=text, norm=norm, toks=toks)
    blocked: set[int] = set()
    period_tokens: set[int] = set()
    dim_tokens: set[int] = set()

    # periods ("between March and April" is two periods when the user is comparing)
    comparing = bool(lex.cue_index["compare"].find(toks)) if "compare" in lex.cue_index else False
    for c in find_periods(norm, as_of, comparing):
        idx = [t.i for t in toks if t.start < c.ce and t.end > c.cs]
        if not idx:
            continue
        pm = PeriodMention(idx[0], idx[-1], c.start.isoformat(), c.end.isoformat(), c.preset,
                           describe_range(c.start.isoformat(), c.end.isoformat()), c.kind, c.cs, c.ce)
        ann.periods.append(pm)
        blocked.update(range(pm.a, pm.b + 1))
        period_tokens.update(range(pm.a, pm.b + 1))

    # cues
    for name, idx in lex.cue_index.items():
        hits = idx.find(toks)
        if hits:
            ann.cues[name] = [(a, b) for a, b, _ in hits]
    cue_tokens = {i for spans in ann.cues.values() for a, b in spans for i in range(a, b + 1)}

    def is_group(a: int, b: int, dim: str, explicit_only: bool = False) -> bool:
        prev = toks[a - 1].text if a > 0 else ""
        prev2 = toks[a - 2].text if a > 1 else ""
        if prev in lex.group_markers or (prev2, prev) in lex.group_phrases or prev in lex.which_words:
            return True
        if explicit_only:
            return False
        plural = toks[b].text != toks[b].stem and toks[b].text.endswith("s")
        return bool(plural or (dim in lex.time_dims and toks[a].text in lex.monthly_words))

    # a dimension introduced by an explicit grouping word ("by payment method") is reserved first, so a metric word inside it
    # ("payment") cannot steal it; a bare plural ("payments") stays free to be the metric
    reserved: set[int] = set()
    for a, b, payloads in lex.dim_index.find(toks, blocked):
        if is_group(a, b, payloads[0], explicit_only=True):
            reserved.update(range(a, b + 1))

    # metrics
    for a, b, payloads in lex.metric_index.find(toks, blocked | reserved):
        ann.metrics.append(MetricMention(a, b, payloads[0], " ".join(t.text for t in toks[a:b + 1])))
        blocked.update(range(a, b + 1))

    # dimensions
    for a, b, payloads in lex.dim_index.find(toks, blocked):
        dim = payloads[0]
        ann.dims.append(DimMention(a, b, dim, is_group(a, b, dim), " ".join(t.text for t in toks[a:b + 1])))
        blocked.update(range(a, b + 1))
        dim_tokens.update(range(a, b + 1))

    ann.seller_context = any(t.stem in lex.seller_stems for t in toks)

    # entity values (names, enum values, synonyms). A phrase may be both a metric and a value ("late delivery": the
    # late-delivery rate or the ticket category), so metric tokens do not block values; the interpreter decides by intent.
    for a, b, payloads in lex.value_index.find(toks, period_tokens | dim_tokens):
        ann.values.append(ValueMention(a, b, list(payloads), " ".join(t.text for t in toks[a:b + 1])))
        blocked.update(range(a, b + 1))

    # state codes
    codes: set[str] = set()
    for k in lex.code_dims:
        codes |= set(lex.domain(k) or [])
    can_case = len(norm) == len(text)
    shouting = _is_shouting(text)
    for i, t in enumerate(toks):
        if i in blocked or len(t.text) != 2 or t.text.upper() not in codes:
            continue
        code = t.text.upper()
        orig = text[t.start:t.end] if can_case else t.text
        prev = toks[i - 1].text if i > 0 else ""
        nxt = toks[i + 1].text if i + 1 < len(toks) else ""
        ok = False
        if orig.isupper() and not shouting:
            ok = True
        elif t.text in lex.safe_lower_codes and not (prev == "go" and t.text == "to"):
            ok = True
        elif t.text in lex.hard_ambiguous_codes:
            ok = prev in ("state", "states") or nxt in ("state", "states")
        else:
            ok = prev in lex.state_context_words
        if ok:
            cands = [ValueCand(k, code) for k in sorted(lex.code_dims) if code in (lex.domain(k) or [])]
            ann.values.append(ValueMention(i, i, cands, t.text))
            blocked.add(i)
    ann.values.sort(key=lambda v: v.a)
    # a word that is both a command verb and a filter value ("open orders" vs "list open tickets"):
    # at the start of the message it is the verb
    first_content = next((t.i for t in toks if t.text not in lex.polite_words), None)
    view_spans = ann.cues.get("view", [])
    ann.values = [v for v in ann.values
                  if not (v.a == v.b == first_content and any(a <= v.a <= b for a, b in view_spans))]

    # partial product-category words ("furniture", "health") when the user talks about categories/products
    cat_dom = lex.domain("product_category") or []
    if cat_dom and any(t.stem in ("category", "product") for t in toks):
        used = {c.canonical for v in ann.values for c in v.cands if c.key == "product_category"}
        for i, t in enumerate(toks):
            if i in blocked or i in cue_tokens or t.stem in lex.stop or len(t.text) < 4 or t.stem in ("category", "product"):
                continue
            matches = [v for v in cat_dom if t.stem in {stem(w) for w in v.split("_")} and v not in used]
            if 1 <= len(matches) <= 6:
                ann.values.append(ValueMention(i, i, [ValueCand("product_category", v) for v in matches], t.text, partial=True))
                blocked.add(i)
        ann.values.sort(key=lambda v: v.a)

    # numbers: top N
    m = re.search(r"\b(?:top|best|highest|bottom|worst|lowest|first)\s+(\d{1,3}|" + "|".join(_WORDNUM) + r")\b", norm) or \
        re.search(r"\b(\d{1,3}|" + "|".join(_WORDNUM) + r")\s+(?:best|worst|top|highest|lowest|biggest|largest|smallest)\b", norm)
    if m:
        raw = m.group(1)
        ann.top_n = int(raw) if raw.isdigit() else _WORDNUM[raw]
        for t in toks:
            if t.start >= m.start(1) and t.end <= m.end(1):
                blocked.add(t.i)
    if re.search(r"\b(?:bottom|worst|lowest|least|smallest|fewest)\b", norm):
        ann.sort_dir = "asc"
    elif re.search(r"\b(?:top|best|highest|most|largest|biggest)\b", norm):
        ann.sort_dir = "desc"

    # direction words
    down, up = ann.has("change_down"), ann.has("change_up")
    ann.expected_direction = "down" if down and not up else "up" if up and not down else "none"

    # roles of periods
    ann.roles = assign_roles(ann, lex)

    # coverage
    covered = set(blocked) | cue_tokens
    content = [t for t in toks if t.stem not in lex.stop]
    unexplained = []
    for t in content:
        if t.i in covered or t.text.isdigit() or t.stem in lex.page_vocab:
            continue
        unexplained.append(t.text)
    ann.content_tokens = len(content)
    ann.unexplained = unexplained
    ann.coverage = 1.0 if not content else max(0.0, 1.0 - len(unexplained) / len(content))
    return ann


def assign_roles(ann: Annotations, lex: Lexicon) -> list[tuple[PeriodMention, str]]:
    ps = ann.periods
    if not ps:
        return []
    toks = ann.toks

    def marker_before(p: PeriodMention) -> str | None:
        words = tuple(t.text for t in toks[max(0, p.a - 2):p.a])
        for n in (2, 1):
            tail = words[-n:] if len(words) >= n else ()
            if tail in lex.baseline_always:
                return "always"
            if tail in lex.baseline_if_change:
                return "change"
        return None

    changeish = ann.has("change_down", "change_up", "explain", "compare")
    if len(ps) == 1:
        mk = marker_before(ps[0])
        if mk == "always" or (mk == "change" and changeish):
            return [(ps[0], "baseline")]
        return [(ps[0], "primary")]
    out: list[tuple[PeriodMention, str]] = []
    first_marker = marker_before(ps[0])
    if first_marker == "always" or (first_marker == "change" and changeish):
        out.append((ps[0], "baseline"))
        out.append((ps[1], "primary"))
    else:
        out.append((ps[0], "primary"))
        out.append((ps[1], "baseline"))
    out.extend((p, "extra") for p in ps[2:])
    return out
