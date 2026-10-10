"""Vocabulary for the agent, derived from the application metadata plus a tenant file (data/olist/agent_lexicon.json).

Nothing about a particular business is hard-coded here: metric / dimension / value vocabularies come from
`application.json`; synonyms, cue words and identities come from the tenant lexicon file.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import app_model

ROOT = Path(__file__).resolve().parent.parent
LEXICON_FILE = ROOT / "data" / "olist" / "agent_lexicon.json"
TOKEN_RE = re.compile(r"[a-z0-9]+")


# ------------------------------------------------------------------------------------ text helpers
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_text(s: str) -> str:
    return strip_accents(s).lower()


def squash(s: str) -> str:
    """Case, accent, space and punctuation insensitive key: 'Central-West' == 'central west'."""
    return re.sub(r"[^a-z0-9]", "", norm_text(s))


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


@dataclass(frozen=True)
class Tok:
    i: int
    text: str
    stem: str
    start: int
    end: int


def tokenize(norm: str) -> list[Tok]:
    return [Tok(i, m.group(0), stem(m.group(0)), m.start(), m.end()) for i, m in enumerate(TOKEN_RE.finditer(norm))]


def phrase_re(items: list[str]) -> re.Pattern:
    """Regex matching any phrase of a data list on normalised text. A trailing '*' means 'any ending' (increase*)."""
    alts = []
    for it in sorted(items, key=len, reverse=True):
        body = r"\s+".join(re.escape(w) for w in it.rstrip("*").split())
        alts.append(body + (r"\w*" if it.endswith("*") else ""))
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b") if alts else re.compile(r"(?!x)x")


def phrase_tokens(phrase: str) -> tuple[str, ...]:
    return tuple(stem(t) for t in TOKEN_RE.findall(norm_text(phrase)))


class PhraseIndex:
    """Longest-match-first phrase finder over stemmed tokens. Payload is any object."""

    def __init__(self) -> None:
        self._idx: dict[tuple[str, ...], list[Any]] = {}
        self.max_len = 1

    def add(self, phrase: str, payload: Any) -> None:
        key = phrase_tokens(phrase)
        if not key:
            return
        bucket = self._idx.setdefault(key, [])
        if payload not in bucket:
            bucket.append(payload)
        self.max_len = max(self.max_len, len(key))

    def find(self, toks: list[Tok], blocked: set[int] | None = None) -> list[tuple[int, int, list[Any]]]:
        """Non-overlapping matches as (first_token_index, last_token_index_inclusive, payloads)."""
        blocked = blocked or set()
        stems = [t.stem for t in toks]
        out: list[tuple[int, int, list[Any]]] = []
        i = 0
        while i < len(stems):
            hit = None
            for n in range(min(self.max_len, len(stems) - i), 0, -1):
                if any((i + k) in blocked for k in range(n)):
                    continue
                key = tuple(stems[i:i + n])
                if key in self._idx:
                    hit = (i, i + n - 1, self._idx[key])
                    break
            if hit:
                out.append(hit)
                i = hit[1] + 1
            else:
                i += 1
        return out


# ------------------------------------------------------------------------------------ structures
@dataclass(frozen=True)
class MetricInfo:
    metric_id: str
    title: str
    description: str
    unit: str
    additive: bool
    higher_is_better: bool
    dataset_id: str
    has_weight: bool


@dataclass
class DimInfo:
    name: str
    description: str
    datasets: set[str] = field(default_factory=set)
    values: list[str] | None = None


@dataclass(frozen=True)
class ValueCand:
    key: str          # dimension name or page filter id
    canonical: str


@dataclass
class Canon:
    value: str | None
    how: str = ""     # exact | synonym | name | fuzzy | unchecked | none
    suggestions: list[str] = field(default_factory=list)


class Lexicon:
    def __init__(self, app, raw: dict):
        self.app = app
        self.raw = raw
        self.as_of: str = app.as_of_date or "2018-08-31"
        self.stop: set[str] = {stem(w) for w in map(norm_text, raw.get("stopwords", []))}
        self.materiality_pct: float = float(raw.get("materiality_pct", 5.0))
        self.identities: list[dict] = raw.get("identities", [])
        self.related: dict[str, list[str]] = raw.get("related_metrics", {})
        self.health_metrics: list[str] = raw.get("health_metrics", [])
        self.skip_driver_dims: set[str] = set(raw.get("skip_driver_dimensions", []))
        self.dim_parents: dict[str, str] = raw.get("dimension_parents", {})  # child dimension -> parent dimension
        self.examples: list[dict] = raw.get("examples", [])
        self.comparators: list[tuple[str, ...]] = [phrase_tokens(p) for p in raw.get("comparators", [])]
        # ---- language data (phrase lists live in the data file, never in code)
        lang = raw.get("language", {})
        self.lang = lang
        self.re_submit = phrase_re(lang.get("submit_phrases", []))
        self.re_cancel = phrase_re(lang.get("cancel_phrases", []))
        self.re_resume = phrase_re(lang.get("resume_phrases", []))
        self.re_screen = phrase_re(lang.get("screen_reference_phrases", []))
        self.re_screen_help = phrase_re(lang.get("screen_help_phrases", []))
        self.re_pronoun = phrase_re(lang.get("pronouns", []))
        self.re_union = phrase_re(lang.get("union_words", []))
        self.re_up = phrase_re(lang.get("direction_up", []))
        self.re_down = phrase_re(lang.get("direction_down", []))
        self.re_overconfident = phrase_re(lang.get("overconfident", []))
        self.re_ignore = phrase_re(lang.get("ignore_instruction_phrases", []))
        marks = "|".join(re.escape(m) for m in sorted(lang.get("injection_markers", []), key=len, reverse=True))
        self.re_inject = re.compile(rf"(?:{marks})\s*[:\-]") if marks else re.compile(r"(?!x)x")
        parts = [rf"{re.escape(v)}\s+(?:{'|'.join(re.escape(o) for o in objs)})" for v, objs in lang.get("destructive", {}).items() if objs]
        self.re_destructive = re.compile(rf"\b(?:{'|'.join(parts)})\b") if parts else re.compile(r"(?!x)x")
        self.secret_markers: tuple[str, ...] = tuple(lang.get("secret_field_markers", []))
        self.free_text_markers: tuple[str, ...] = tuple(lang.get("free_text_field_markers", []))
        self.generic_values: set[str] = set(lang.get("generic_values", []))
        self.generic_nouns: set[str] = set(lang.get("generic_nouns", []))
        self.view_verb_stems: set[str] = {stem(w) for w in lang.get("view_verbs", [])}
        self.polite_words: set[str] = set(lang.get("polite_words", []))
        self.baseline_always: set[tuple[str, ...]] = {tuple(p.split()) for p in lang.get("baseline_markers_always", [])}
        self.baseline_if_change: set[tuple[str, ...]] = {tuple(p.split()) for p in lang.get("baseline_markers_if_change", [])}
        self.group_markers: set[str] = set(lang.get("group_markers", []))
        self.group_phrases: set[tuple[str, ...]] = {tuple(p.split()) for p in lang.get("group_phrases", [])}
        self.which_words: set[str] = set(lang.get("which_words", []))
        self.monthly_words: set[str] = set(lang.get("monthly_words", []))
        self.seller_stems: set[str] = {stem(w) for w in lang.get("seller_words", [])}
        self.state_context_words: set[str] = set(lang.get("state_context_words", []))
        self.id_prefixes: dict[str, str] = {k.upper(): v for k, v in raw.get("id_prefixes", {}).items()}
        self.default_hex_entity: str = raw.get("default_hex_entity", "")
        self.entity_highlights: list[str] = raw.get("entity_highlights", [])
        self.write_verbs: set[str] = {stem(w) for w in map(norm_text, raw.get("write_verbs", []))}
        self.intent_phrases = PhraseIndex()
        for ph in raw.get("intent_phrases", []):
            self.intent_phrases.add(ph, "intent")
        self.safe_lower_codes: set[str] = set(raw.get("safe_lowercase_state_codes", []))
        self.hard_ambiguous_codes: set[str] = set(raw.get("hard_ambiguous_state_codes", []))

        self.datasets = {d.dataset_id: d for d in app.datasets}
        self.metrics: dict[str, MetricInfo] = {}
        self.dims: dict[str, DimInfo] = {}
        for d in app.datasets:
            for m in d.metrics:
                self.metrics[m.metric_id] = MetricInfo(m.metric_id, m.title, m.description, m.unit, m.additive,
                                                       m.higher_is_better, d.dataset_id, bool(m.weight_sql))
            for f in d.fields:
                if f.role != "dimension":
                    continue
                info = self.dims.setdefault(f.name, DimInfo(f.name, f.description))
                info.datasets.add(d.dataset_id)
                if f.values:
                    info.values = sorted(set(info.values or []) | set(f.values))

        # time dimensions come from the metadata (a dimension of type date), not from a name
        self.time_dims: set[str] = {f.name for d in app.datasets for f in d.fields if f.role == "dimension" and f.type == "date"}

        # dimensions whose values are two-letter codes (state-like) and the one that groups them (region-like): from the data
        self.code_dims: set[str] = {n for n, i in self.dims.items()
                                    if i.values and all(len(v) == 2 and v.isalpha() and v.isupper() for v in i.values)}
        self.region_dim: str | None = raw.get("region_dimension")

        self._domains: dict[str, list[str]] = {}          # key (dimension or filter id) -> allowed values
        self._value_synonyms: dict[str, dict[str, list[str]]] = {}
        for name, info in self.dims.items():
            if info.values:
                self._domains[name] = list(info.values)
        for key, table in raw.get("value_synonyms", {}).items():
            self._value_synonyms.setdefault(key, {}).update(table)
        self.state_names: dict[str, list[str]] = raw.get("state_names", {})
        self.region_synonyms: dict[str, list[str]] = raw.get("region_synonyms", {})
        self.region_states: dict[str, list[str]] = raw.get("region_states", {})

        # page filters contribute domains and value synonyms (keyed by filter id)
        self.page_filter_ids: dict[str, set[str]] = {}    # filter id -> page ids
        for p in app.pages:
            for f in p.filters:
                if f.type == "date_range":
                    continue
                self.page_filter_ids.setdefault(f.filter_id, set()).add(p.page_id)
                if f.allowed_values:
                    cur = self._domains.setdefault(f.filter_id, [])
                    for v in f.allowed_values:
                        if v not in cur:
                            cur.append(v)
                for canon, aliases in (f.synonyms or {}).items():
                    self._value_synonyms.setdefault(f.filter_id, {}).setdefault(canon, [])
                    for a in aliases:
                        if a not in self._value_synonyms[f.filter_id][canon]:
                            self._value_synonyms[f.filter_id][canon].append(a)

        # ---- phrase indexes
        self.metric_index = PhraseIndex()
        for mid, mi in self.metrics.items():
            self.metric_index.add(mid.replace("_", " "), mid)
            self.metric_index.add(mi.title, mid)
            for syn in raw.get("metric_synonyms", {}).get(mid, []):
                self.metric_index.add(syn, mid)

        self.dim_index = PhraseIndex()
        for name in self.dims:
            self.dim_index.add(name.replace("_", " "), name)
        for name, syns in raw.get("dimension_synonyms", {}).items():
            if name in self.dims:
                for s in syns:
                    self.dim_index.add(s, name)

        self.cue_index: dict[str, PhraseIndex] = {}
        for cue, phrases in raw.get("cues", {}).items():
            idx = PhraseIndex()
            for ph in phrases:
                idx.add(ph, cue)
            self.cue_index[cue] = idx

        self.value_index = PhraseIndex()
        self._build_value_index()

        # words that name pages / entities, so coverage does not count them as unexplained
        vocab: set[str] = set()
        for p in app.pages:
            for t in [p.title, p.directory.replace("/", " "), *p.keywords]:
                vocab.update(phrase_tokens(t))
            for f in p.filters:
                vocab.update(phrase_tokens(f.title))
        self.page_vocab = {w for w in vocab if len(w) > 2}

    # ---------------------------------------------------------------- value vocabulary
    def _build_value_index(self) -> None:
        for key, values in self._domains.items():
            if key in self.code_dims or key == self.region_dim:
                continue
            for v in values:
                if squash(str(v)) in {squash(g) for g in self.generic_values}:
                    continue  # words like "other" or "all" are too common to treat as filter values
                self.value_index.add(str(v).replace("_", " "), ValueCand(key, v))
            for canon, aliases in self._value_synonyms.get(key, {}).items():
                real = self.resolve_in_domain(key, canon)
                if real:
                    for a in aliases:
                        self.value_index.add(a, ValueCand(key, real))
        for key in sorted(self.code_dims):
            for code, names in self.state_names.items():
                if code in self._domains.get(key, []):
                    for n in names:
                        self.value_index.add(n, ValueCand(key, code))
        if self.region_dim:
            for canon, names in self.region_synonyms.items():
                real = self.resolve_in_domain(self.region_dim, canon)
                if real:
                    self.value_index.add(canon, ValueCand(self.region_dim, real))
                    for n in names:
                        self.value_index.add(n, ValueCand(self.region_dim, real))

    # ---------------------------------------------------------------- domains
    def domain(self, key: str) -> list[str] | None:
        return self._domains.get(key)

    def resolve_in_domain(self, key: str, raw: str) -> str | None:
        dom = self._domains.get(key)
        if not dom:
            return None
        r = squash(raw)
        for v in dom:
            if squash(v) == r:
                return v
        return None

    def canonical_value(self, key: str, raw: str) -> Canon:
        """Map a user/LLM value to the canonical member of the key's closed domain."""
        raw = str(raw).strip()
        dom = self._domains.get(key)
        if not dom:
            return Canon(raw, "unchecked")
        hit = self.resolve_in_domain(key, raw)
        if hit:
            return Canon(hit, "exact")
        r = squash(raw)
        for canon, aliases in self._value_synonyms.get(key, {}).items():
            if r == squash(canon) or r in {squash(a) for a in aliases}:
                real = self.resolve_in_domain(key, canon)
                if real:
                    return Canon(real, "synonym")
        if key in self.code_dims:
            for code, names in self.state_names.items():
                if r in {squash(n) for n in names}:
                    real = self.resolve_in_domain(key, code)
                    if real:
                        return Canon(real, "name")
        if self.region_dim and key == self.region_dim:
            for canon, names in self.region_synonyms.items():
                if r == squash(canon) or r in {squash(n) for n in names}:
                    real = self.resolve_in_domain(key, canon)
                    if real:
                        return Canon(real, "name")
        near = difflib.get_close_matches(r, [squash(v) for v in dom], n=3, cutoff=0.84)
        if near:
            real = next(v for v in dom if squash(v) == near[0])
            return Canon(real, "fuzzy", [next(v for v in dom if squash(v) == n) for n in near])
        sugg = difflib.get_close_matches(r, [squash(v) for v in dom], n=3, cutoff=0.5)
        return Canon(None, "none", [next(v for v in dom if squash(v) == n) for n in sugg])

    # ---------------------------------------------------------------- metric / dimension helpers
    def metric(self, metric_id: str | None) -> MetricInfo | None:
        return self.metrics.get(metric_id) if metric_id else None

    def resolve_metric(self, raw: str | None) -> tuple[str | None, list[str]]:
        """Metric id from an id, title, synonym or near-miss. Returns (id|None, suggestions)."""
        if not raw:
            return None, []
        r = squash(raw)
        for mid, mi in self.metrics.items():
            if r in (squash(mid), squash(mi.title)):
                return mid, []
        toks = tokenize(norm_text(str(raw)))
        for _, _, payloads in self.metric_index.find(toks):
            if payloads:
                return payloads[0], []
        near = difflib.get_close_matches(r, [squash(m) for m in self.metrics], n=3, cutoff=0.6)
        ids = [next(m for m in self.metrics if squash(m) == n) for n in near]
        return None, ids

    def resolve_dimension(self, raw: str | None) -> str | None:
        if not raw:
            return None
        r = squash(raw)
        for name in self.dims:
            if squash(name) == r:
                return name
        toks = tokenize(norm_text(str(raw)))
        for _, _, payloads in self.dim_index.find(toks):
            if payloads:
                return payloads[0]
        near = difflib.get_close_matches(r, [squash(n) for n in self.dims], n=1, cutoff=0.8)
        return next((n for n in self.dims if squash(n) == near[0]), None) if near else None

    def dataset_of(self, metric_id: str) -> Any:
        mi = self.metrics.get(metric_id)
        return self.datasets.get(mi.dataset_id) if mi else None

    def dims_of_metric(self, metric_id: str) -> list[str]:
        ds = self.dataset_of(metric_id)
        return [f.name for f in ds.fields if f.role == "dimension"] if ds else []

    def filterable_dims(self, metric_id: str) -> list[str]:
        ds = self.dataset_of(metric_id)
        return [f.name for f in ds.fields if f.role == "dimension" and f.values] if ds else []

    def metrics_supporting(self, filter_dims: list[str], group_dims: list[str] | None = None) -> list[str]:
        out = []
        for mid in self.metrics:
            ds = {f.name for f in self.dataset_of(mid).fields}
            if all(d in ds for d in filter_dims) and all(d in ds for d in (group_dims or [])):
                out.append(mid)
        return out

    def describe_metrics(self) -> list[str]:
        return [f"{m.metric_id}: {m.title} ({m.unit or 'number'}, {'adds up' if m.additive else 'ratio/average'}) - {m.description}"
                for m in self.metrics.values()]


@lru_cache(maxsize=1)
def get_lexicon() -> Lexicon:
    raw = json.loads(LEXICON_FILE.read_text(encoding="utf-8")) if LEXICON_FILE.exists() else {}
    return Lexicon(app_model(), raw)
