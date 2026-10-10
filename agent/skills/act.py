"""Act skill: forms and writes by intent.

"I want to open a ticket for order E481F51C, it was damaged" opens the right form (generated from the write API's body
schema), fills it from the message, validates every field, and asks for what is missing. Later messages fill or correct
the form; "submit" starts the existing safe path: dry-run preview -> explicit user confirmation -> audited write -> undo.
Writes are never approved on the user's behalf. Passwords and card numbers are never taken through chat.
"""
from __future__ import annotations

import difflib
import logging
import re
import uuid
from datetime import date
from typing import Any, AsyncIterator

from agent.lexicon import get_lexicon, norm_text, squash, tokenize
from agent.schema import Interp
from agent.skills.base import SkillResult, emit_canvas
from backend import executor
from contracts.actions import Intent, Plan, ToolCall
from contracts.canvas import FormSpec
from contracts.forms import FormState

logger = logging.getLogger("agent.skills.act")

# All phrase lists (submit / cancel / resume words, secret and free-text field markers, pronouns) are data in
# data/olist/agent_lexicon.json ("language"), reached through the Lexicon; this module has no natural-language word lists.


# ------------------------------------------------------------------------------------ API schema helpers
def flat_spec(spec: dict) -> dict:
    s = dict(spec or {})
    if "anyOf" in s:
        alts = [a for a in s["anyOf"] if a.get("type") != "null"]
        base = dict(alts[0]) if alts else {}
        base.update({k: v for k, v in s.items() if k != "anyOf"})
        s = base
    return s


def api_fields(api) -> tuple[dict[str, dict], list[str], list[str]]:
    """(name -> spec, required names, path-parameter names). Path parameters come first."""
    fields: dict[str, dict] = {}
    path_names: list[str] = []
    for p in api.parameters or ():
        name = p.get("name")
        if name and "{" + name + "}" in (api.path or ""):
            fields[name] = flat_spec(p)
            path_names.append(name)
    schema = api.body_schema or {}
    for name, spec in (schema.get("properties") or {}).items():
        fields[name] = flat_spec(spec)
    required = list(path_names) + [r for r in (schema.get("required") or []) if r in fields]
    return fields, required, path_names


def validate_field(name: str, spec: dict, raw: Any) -> tuple[Any, str | None]:
    spec = flat_spec(spec)
    s = str(raw).strip()
    if s == "":
        return None, "empty"
    if any(k in name.lower() for k in get_lexicon().secret_markers):
        return None, "this is a secret; please type it in the form, not in the chat"
    enum = spec.get("enum")
    if enum:
        key = squash(s)
        for e in enum:
            if squash(str(e)) == key:
                return e, None
        cands = [e for e in enum if key and (key in squash(str(e)) or squash(str(e)) in key)]
        if len(cands) == 1:
            return cands[0], None
        near = difflib.get_close_matches(key, [squash(str(e)) for e in enum], n=1, cutoff=0.7)
        if near:
            return next(e for e in enum if squash(str(e)) == near[0]), None
        return None, f"must be one of: {', '.join(map(str, enum))}"
    t = spec.get("type")
    if t == "integer":
        m = re.search(r"-?\d+", s.replace(",", ""))
        if not m:
            return None, "must be a whole number"
        n = int(m.group(0))
        if ("minimum" in spec and n < spec["minimum"]) or ("maximum" in spec and n > spec["maximum"]):
            return None, f"must be between {spec.get('minimum', '-')} and {spec.get('maximum', '-')}"
        if "exclusiveMinimum" in spec and n <= spec["exclusiveMinimum"]:
            return None, f"must be greater than {spec['exclusiveMinimum']}"
        return n, None
    if t == "number":
        m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
        if not m:
            return None, "must be a number"
        x = float(m.group(0))
        if ("minimum" in spec and x < spec["minimum"]) or ("maximum" in spec and x > spec["maximum"]):
            return None, f"must be between {spec.get('minimum', '-')} and {spec.get('maximum', '-')}"
        return x, None
    if t == "boolean":
        if s.lower() in ("true", "yes", "y", "1"):
            return True, None
        if s.lower() in ("false", "no", "n", "0"):
            return False, None
        return None, "must be yes or no"
    if spec.get("maxLength") and len(s) > spec["maxLength"]:
        return None, f"must be at most {spec['maxLength']} characters"
    if "email" in name.lower() and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s):
        return None, "must be a valid email address"
    if spec.get("format") == "date" or name.endswith(("_date", "date")):
        try:
            date.fromisoformat(s)
        except ValueError:
            return None, "must be a date like 2018-08-31"
    if name.endswith("_id") and len(s) < 6 and not re.fullmatch(r"[A-Za-z]{2,4}-?\d+", s):
        return None, "needs at least 6 characters of the id"
    return s, None


# ------------------------------------------------------------------------------------ deterministic extraction
def _match_enum(text_stems: set[str], enum: list, consumed: set[str]) -> tuple[Any, set[str]] | None:
    from agent.lexicon import stem
    best, best_score, best_words = None, 0, set()
    tie = False
    for e in enum:
        words = [stem(w) for w in str(e).lower().replace("-", "_").split("_") if w]
        hit = {w for w in words if w in text_stems and w not in consumed}
        if not hit:
            continue
        score = (2 * len(words) if len(hit) == len(words) else 1) + (0.5 if len(hit) == len(words) and len(words) == 1 else 0)
        if score > best_score:
            best, best_score, best_words, tie = e, score, hit, False
        elif score == best_score:
            tie = True
    if best is None or tie:
        return None
    return best, best_words


def extract_values(ctx, api, text: str, ann=None) -> dict[str, Any]:
    """Field values the message states explicitly or unambiguously (raw; validated later)."""
    fields, required, path_names = api_fields(api)
    vals: dict[str, Any] = {}
    norm = norm_text(text)
    toks = tokenize(norm)
    stems = {t.stem for t in toks}

    # 1. "field: value" / "field is value"
    for name in fields:
        words = name.split("_")
        labels = {name, name.replace("_", " ")} | {" ".join(words[i:]) for i in range(1, len(words)) if len(" ".join(words[i:])) > 3}
        for label in sorted(labels, key=len, reverse=True):   # "full name" before "name"
            m = re.search(rf"\b{re.escape(label)}\b\s*(?:=|:|is|to|should be|as)\s*(?:\"([^\"]+)\"|'([^']+)'|([^,;\n]+))", text, re.I)
            if m:
                raw = next(g for g in m.groups() if g)
                vals[name] = re.split(r"\s+(?:and|then)\s+", raw.strip())[0].strip()
                break

    # 2. ids (order refs, ticket codes) and references from the screen
    id_fields = [n for n in fields if n.endswith("_id") or n in ("code", "order_ref")]
    hexes = [h for h in re.findall(r"\b[0-9a-fA-F]{8,32}\b", text) if re.search(r"\d", h) and re.search(r"[a-fA-F]", h)]
    codes = re.findall(r"\b[A-Za-z]{2,4}-\d{2,}\b", text)
    for name in id_fields:
        if name in vals:
            continue
        kw = name.replace("_id", "").replace("_ref", "")
        m = re.search(rf"\b{kw}\W+(?:id\W+|ref\W+|number\W+)?([0-9a-fA-F]{{6,32}}|[A-Za-z]{{2,4}}-\d+)\b", text, re.I) if kw and kw != "code" else None
        if m:
            vals[name] = m.group(1)
    left_hex = [h for h in hexes if h not in vals.values()]
    for name in id_fields:
        if name in vals:
            continue
        if name == "code" and codes:
            vals[name] = codes.pop(0)
        elif left_hex and name.endswith("_id"):
            vals[name] = left_hex.pop(0)
    if ctx.lex.re_pronoun.search(norm) or ctx.lex.re_screen.search(norm):   # "this order": the row the user is pointing at
        from agent.interpret import screen_record
        for name in fields:
            ent = name[:-3] if name.endswith("_id") else (api.entity if name == "code" else None)
            if ent and name not in vals and (rid := screen_record(ctx, ent)):
                vals[name] = rid
    le = getattr(ctx.mem, "last_entity", None)             # "open a ticket for it" right after looking at an order
    if le and ctx.lex.re_pronoun.search(norm):
        key = f"{le[0]}_id"
        if key in fields and key not in vals:
            vals[key] = le[1]

    # 3. enums by mention
    consumed: set[str] = set()
    for name, spec in fields.items():
        enum = spec.get("enum")
        if enum and name not in vals:
            hit = _match_enum(stems, list(enum), consumed)
            if hit:
                vals[name], words = hit
                consumed |= words

    # 4. quoted text -> free-text fields
    quoted = [next(g for g in m if g) for m in re.findall(r'"([^"]+)"|\'([^\']{3,})\'', text)]
    free = [n for n, sp in fields.items() if sp.get("type") == "string" and not sp.get("enum") and n not in vals
            and "email" not in n.lower() and not n.endswith(("_id", "_date")) and not any(s in n.lower() for s in ctx.lex.secret_markers)]
    free.sort(key=lambda n: (0 if any(k in n for k in ctx.lex.free_text_markers) else 1))  # subject / name / notes first, then any text field
    for name in free:
        if quoted:
            vals[name] = quoted.pop(0)

    # 5. numbers for numeric fields
    num_fields = [n for n, sp in fields.items() if sp.get("type") in ("integer", "number") and n not in vals]
    if num_fields:
        masked = re.sub(r"\b[0-9a-fA-F]{8,32}\b|\d{4}-\d{2}-\d{2}|[A-Za-z]{2,4}-\d+", " ", text)
        nums = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", masked)
        if len(num_fields) == 1 and nums:
            vals[num_fields[0]] = nums[0]
        else:
            for n in num_fields:
                m = re.search(rf"(\d+(?:\.\d+)?)\s*(?:x\s*)?{re.escape(n.replace('_', ' '))}|{re.escape(n.replace('_', ' '))}\W+(\d+(?:\.\d+)?)", text, re.I)
                if m:
                    vals[n] = next(g for g in m.groups() if g)

    # 6. emails
    for name in fields:
        if "email" in name.lower() and name not in vals:
            m = re.search(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", text)
            if m:
                vals[name] = m.group(0)
    return {k: v for k, v in vals.items() if v not in (None, "")}


# ------------------------------------------------------------------------------------ signals for the interpreter
def form_signal(ctx, ann) -> str | None:
    form = ctx.mem.form
    if form is None:
        return None
    n = ann.norm
    if form.parked:
        return "fill" if ctx.lex.re_resume.search(n) else None
    if ctx.lex.re_cancel.search(n):
        return "cancel"
    api = ctx.cap.apis.get(form.api_id)
    vals = extract_values(ctx, api, ctx.text, ann) if api else {}
    if vals:
        return "fill"
    if ctx.lex.re_submit.search(n) and len(ann.toks) <= 8:
        return "submit"
    return None


# ------------------------------------------------------------------------------------ the skill
def _recompute(form: FormState, fields: dict, required: list[str]) -> None:
    form.missing = [r for r in required if r not in form.values or form.values[r] in (None, "")]


def _apply(form: FormState, fields: dict, raw: dict[str, Any], source: str) -> tuple[dict, dict]:
    changes, errors = {}, {}
    for name, rawv in raw.items():
        if name not in fields:
            continue
        val, err = validate_field(name, fields[name], rawv)
        if err:
            errors[name] = err
            form.errors[name] = err
        else:
            if name in form.values and form.values[name] != val and form.sources.get(name) == "user":
                pass  # user-typed value replaced by an explicit chat instruction: report it
            form.values[name] = val
            form.sources[name] = source
            form.errors.pop(name, None)
            changes[name] = val
    return changes, errors


def _form_canvas_spec(api, form: FormState) -> FormSpec:
    fields, _, path_names = api_fields(api)
    body_props = {k: v for k, v in fields.items() if k not in path_names}
    return FormSpec(api_id=api.api_id, method=api.method, path=api.path, title=api.title,
                    path_params={k: form.values[k] for k in path_names if k in form.values},
                    body_schema=api.body_schema or {"properties": body_props}, defaults=dict(form.values))


def _summary(api, form: FormState, fields: dict, required: list[str]) -> str:
    done = [f"{k.replace('_', ' ')} = {v}" for k, v in form.values.items() if k in fields]
    parts = [f"**{api.title}** form"]
    if done:
        parts.append("filled: " + "; ".join(done))
    if form.errors:
        parts.append("problems: " + "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in form.errors.items()))
    if form.missing:
        parts.append("still needed: " + ", ".join(m.replace("_", " ") for m in form.missing))
    optional = [k for k in fields if k not in required and k not in form.values]
    if optional and not form.missing:
        parts.append("optional: " + ", ".join(o.replace("_", " ") for o in optional[:5]))
    return " — ".join(parts)


async def run_act(ctx, it: Interp, out: SkillResult) -> AsyncIterator[tuple[str, dict]]:
    mem, lex, cap = ctx.mem, ctx.lex, ctx.cap
    out.kind = "write"
    form: FormState | None = mem.form
    action = it.form_action

    if action == "cancel" and form is not None:
        mem.form = None
        ctx.session.canvases.pop(mem.form_canvas_id or "", None)
        out.text, out.status = "Cancelled the form; nothing was saved.", "INFO"
        yield "form_patch", {"form_id": form.form_id, "status": "cancelled"}
        return

    api_id = it.write_api or (form.api_id if form else None)
    if form is not None and it.write_api and it.write_api != form.api_id and action in ("none", "fill"):
        form.parked = True
        yield "analysis_step", {"title": "Parked the previous form", "detail": form.title}
        form, api_id = None, it.write_api
    from agent.interpret import _write_apis  # local import: interpret imports this module lazily
    if api_id is None or api_id not in _write_apis(ctx):
        writes = list(_write_apis(ctx).values())
        out.text = ("I can start these actions: " + "; ".join(f"**{a.title}**" for a in writes[:10]) +
                    ". Which one do you want?") if writes else "This application has no actions I can start."
        out.status = "INFO"
        out.suggestions = [a.title for a in writes[:3]]
        return
    api = cap.apis[api_id]
    fields, required, path_names = api_fields(api)

    opened = False
    if form is None:
        form = FormState(form_id="F" + uuid.uuid4().hex[:6], api_id=api_id, title=api.title)
        for name, spec in fields.items():  # defaults the API declares
            if "default" in spec and spec["default"] is not None:
                form.values[name], form.sources[name] = spec["default"], "default"
        mem.form, opened = form, True
        yield "form_open", form.model_dump()
    elif form.parked:
        form.parked = False
        yield "form_patch", {"form_id": form.form_id, "status": "resumed"}

    raw = {**extract_values(ctx, api, ctx.text, ctx.ann), **{k: v for k, v in it.fields.items() if k in fields}}
    changes, errors = _apply(form, fields, raw, "agent")
    _recompute(form, fields, required)
    if changes or errors or opened:
        yield "form_patch", {"form_id": form.form_id, "changes": changes, "errors": form.errors, "missing": form.missing}

    spec = _form_canvas_spec(api, form)
    mem.form_canvas_id = await emit_canvas(ctx, "form", api.title, form=spec, layers=list(api.data_layers),
                                           canvas_id=mem.form_canvas_id or f"FORM-{api.api_id}")

    wants_submit = action == "submit" or bool(ctx.lex.re_submit.search(ctx.ann.norm) and len(ctx.ann.toks) <= 8)
    complete = not form.missing and not form.errors
    direct = opened and complete  # the first message already carried everything: go straight to the confirmation card
    if complete and (wants_submit or direct):
        async for ev in _submit(ctx, api, form, fields, path_names, out):
            yield ev
        return

    out.text = _summary(api, form, fields, required)
    out.extra_text = [str(v) for v in form.values.values()]
    if form.missing:
        out.text += ". Tell me the missing values here or type them into the form."
    elif form.errors:
        out.text += ". Please correct those values."
    else:
        out.text += ". Say **submit** to preview and confirm it."
    out.status = "INFO"
    out.suggestions = ["submit", "cancel the form"] if complete else []


async def _submit(ctx, api, form: FormState, fields: dict, path_names: list[str], out: SkillResult) -> AsyncIterator[tuple[str, dict]]:
    mem = ctx.mem
    body = {k: v for k, v in form.values.items() if k in fields and k not in path_names}
    params = {k: form.values[k] for k in path_names if k in form.values}
    summary = f"{api.title}: " + ", ".join(f"{k}={v}" for k, v in form.values.items() if k in fields and form.sources.get(k) != "default")
    yield "analysis_step", {"title": "Previewing the change", "detail": "nothing is saved until you confirm"}
    form.status = "confirming"
    plan = Plan(intent=Intent(name="multi_step"), steps=[ToolCall(tool="write_api", args={
        "api_id": api.api_id, "params": params, "body": body, "summary": summary[:240]})])
    res = await executor.run_plan(ctx.session, plan, ctx.cfg, auto_confirm=bool(getattr(ctx.cfg, "auto_confirm_writes", False)))
    out.steps = 1
    sr = (res.get("results") or [{}])[0] if res.get("results") else {}
    if res.get("ok") and res.get("evidence_ids"):
        eid = res["evidence_ids"][-1]
        vals = ctx.session.evidence[eid].values
        result = vals.get("result", {}) or {}
        ident = next((str(result[k]) for k in ("code", "id", "ticket_code", "name") if result.get(k)), "")
        out.citations.append(eid)
        form.status = "submitted"
        mem.form, mem.last_action_kind = None, "write"
        out.text = (f"Done: **{api.title}** was applied" + (f" ({ident})" if ident else "") +
                    f". It is logged as {vals.get('action_id')}; say **undo that** to revert it.")
        out.status = "VERIFIED"
        out.suggestions = ["undo that"]
        yield "form_patch", {"form_id": form.form_id, "status": "submitted", "action_id": vals.get("action_id")}
        return
    form.status = "draft"
    if res.get("stage") == "validation":
        issues = res.get("issues", [])
        for i in issues:
            msg = i.get("message", "")
            for name in fields:
                if re.search(rf"\b{re.escape(name)}\b", msg):
                    form.errors[name] = msg
        out.text = "The request did not pass validation: " + "; ".join(i.get("message", "") for i in issues[:3])
        out.status = "FAILED"
    elif sr.get("error_code") == "denied":
        out.text, out.status = "Understood, nothing was saved. The form is still open if you want to change it.", "INFO"
    else:
        out.text = f"The change was not applied: {sr.get('message') or 'unknown error'}."
        out.status = "FAILED"
        if "duplicate" in (sr.get("message") or "").lower():
            out.text += " It looks like the same record already exists."
