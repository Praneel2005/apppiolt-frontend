# AppPilot Agent V3 — layered, intent-based architecture

Status: this document is the plan of record for the agent. It replaces the "what to build" parts of
`AGENT_V2_DESIGN.md`. The earlier tool-plan agent is kept only as baseline **B3** (`agent/legacy_agent.py`).

Thesis (unchanged): **the LLM proposes; deterministic code validates before and verifies after; every number is cited.**

## 1. Why a rewrite

The V2 attempt put regexes in front of an LLM tool-planner. Only the rules path (navigate + filter) worked, because
everything else still depended on the model emitting exact tool calls, and a keyword like "sales" made the rules claim
confidence for questions they cannot answer. V3 separates *understanding*, *deciding*, *doing* and *explaining*.

## 2. The layers

```
 L0  Context      what the user sees and has said: page, filters, dates, visible rows, last request, open form
 L1  Perceive     deterministic annotator: metrics, dimensions, entity values, ALL periods, cues, coverage
 L2  Interpret    one structured intent (LLM grounded by L0+L1; rules as fast lane and as fallback)
 L3  Route        intent -> skill (View | Data | Reason | Act | Converse), with capability checks
 L4  Skills       each skill is a deterministic pipeline; the LLM never computes, never picks SQL
 L5  Verify       UI acknowledgement, plan validation, repair once, status VERIFIED | PARTIAL | FAILED
 L6  Answer       facts registry -> deterministic composition -> optional LLM synthesis -> claim check
```

### L1 Perceive (`agent/perceive.py`, `agent/lexicon.py`)
Vocabulary is *derived from metadata* (metrics, dimensions, enum values, page titles/keywords/filters) plus a tenant file
`data/olist/agent_lexicon.json` (synonyms, cue words, identities). Nothing about Olist is hard-coded in code.
It extracts every period (not just the first), entity values with role hints (customer vs seller state), group-by cues,
`top N`, and a *coverage* score: the share of content words it can account for. Low coverage means "send to the LLM".

### L2 Interpret (`agent/interpret.py`)
Output is one flat `Interpretation` (provider-neutral schema):
`intent ∈ view | data | compare | explain | investigate | act | undo | help | clarify | decline`,
`shape ∈ single | breakdown | rank | trend | list`, metric(s), group-by, filters, periods with roles
(`primary`, `baseline`), compare kind (`periods | slices`), expected direction, target page, write API, form fields.
*Dates and entities are never trusted from the model*: the annotator's values win, the model only assigns roles.
All names are validated against metadata; unknown values are repaired (nearest match) or reported, never silently dropped.
Fast lane: a complete, unambiguous view command skips the LLM. If the LLM fails, rules still produce an interpretation.

### L3 Route (`agent/route.py`)
Intent → skill. Capability check: if no page can show the request, the skill falls back to a generated view (canvas)
and *says why*. Canvas is produced only by skills, tied to the turn, and replaced on the next turn.

### L4 Skills (`agent/skills/`)
* **View** — pick the page by capability score (metric, group-by, every filter, date, retrieval rank); one atomic `navigate`.
* **Data** — lookup, breakdown, rank, trend through `/api/metrics/*`; evidence; chart/table by data shape; opens the
  supporting page when it reproduces the numbers exactly.
* **Reason** — the analysis engine (`agent/analysis/`): period alignment, headline change, volume × price identity,
  driver scan across *all* dimensions with a localisation score, drill-down into the top driver, confounder metrics,
  seasonality (same transition last year), trend context, premise check ("sales dropped" when they rose),
  data-window caveats. `investigate` runs a health scan across the main metrics. Slice comparison
  ("SP vs RJ", "beds vs toys") needs no page.
* **Lookup** — everything the app knows about ONE record (order, ticket, product, seller...), found by id, coded id
  (`TCK-000123`), "this order" (resolved from the rows on screen) or "it" (the record looked at last). Fetched through the
  catalogue's read API as cited evidence; rendered generically (headline fields, sections, timeline with elapsed days, untrusted
  text labelled). Any new "get one record" API in the catalogue is usable without code changes.
* **Act** — forms: open the right write API as a draft form, fill it from chat (rules + LLM), validate each field
  against the API's body schema, show errors, then preview → explicit confirmation → apply → undo.
  Writes are **never** auto-approved (fixed: the previous loop auto-confirmed by default).
* **Converse** — help, "what is this page", clarification (with concrete options), refusals.

### L6 Answer (`agent/answer.py`)
Every number is a *fact* (`F1..`) with a formatted value. The deterministic composer always works. The LLM, when available,
may rewrite the explanation using only `{F#}` placeholders; the checker rejects stray digits, unknown placeholders and
direction contradictions, and falls back to the deterministic text.

## 2b. What is hard-coded and what is not
* **LLM available (the intended mode):** every message goes to the model, grounded in the metrics, dimensions, candidate pages,
  record lookups, write forms, the screen and the last request. No keyword decides the intent. The parser still supplies the
  dates and entity values (the user's own words), and every name the model returns is validated against the metadata.
* **No LLM / provider down (degraded mode):** the rules use the phrase lists in the data file. The paraphrase suite measures how
  far that goes; it is a safety net, not the design.
* **In code, deliberately:** arithmetic, validation, verification, and the English *time-expression grammar* ("last month",
  "Q2 2018") in `perceive.py`, which is a locale parser like any date library. Safety does not rest on keywords: no tool can delete
  anything, writes need the user's confirmation, and the checks that look for destructive phrases are data-driven extras.
* **In data:** `data/olist/agent_lexicon.json` (synonyms, cues, language phrase lists, identities, id prefixes). Changing language
  or wording means editing that file. `tests/test_v3_language_data.py` fails if word lists creep back into code.

## 3. Context awareness
* Screen: current page, filters, date range, visible rows (`page_context`), selected entities ("this order").
* Conversation: the last interpretation is stored structurally; follow-ups ("and for RJ", "by category", "why?") patch it.
* Defaults: scope inherited from the screen only when the user says "this/here/these" or asks a follow-up.
* Open form: while a form is open, chat is routed to it first (fill / submit / cancel); other topics park it.

## 4. Degradation order
LLM primary → fallback provider → rules-only interpretation → honest clarification. The answer is always produced.

## 4b. Module map

| Layer | Files |
|---|---|
| Vocabulary (metadata + tenant file) | `agent/lexicon.py`, `data/olist/agent_lexicon.json`, `agent/capabilities.py` |
| Perceive | `agent/perceive.py` (periods, metrics, dimensions, values, cues, coverage) |
| Interpret | `agent/interpret.py` (prompt grounding, rules, fast lane, reconcile), `agent/schema.py` |
| Orchestration | `agent/agent.py` (events, status, memory, form parking) |
| Skills | `agent/skills/{view,data,reason,act,converse,base}.py` |
| Analysis engine | `agent/reasoning/{gateway,engine,facts}.py` |
| Answer | `agent/answer.py` (composers, synthesis, claim checker) |
| LLM | `agent/llm.py` (Gemini, Groq, OpenAI-compatible, failover chain, FakeLLM) |
| Baselines | `agent/baselines.py` (B0-B2), `agent/legacy_agent.py` (B3 tool-plan agent) |

Events: `understanding, understood, frame, retrieval, plan, analysis_step, form_open, form_patch, confirm_request, action,
verify, evidence, canvas, status, answer, error, done`. The `answer` event carries `status`, `suggestions`,
`ungrounded_numbers` (end-to-end faithfulness measure) and `narrative_used`.

## 4b2. Adding a new form (no agent code change)
A form is a write API in `/api/catalog` that the application metadata exposes as an action on a page or row. Add the endpoint to the backend (with a Pydantic body, a title, a description and
`examples`) and restart: the capability model picks it up, the LLM prompt lists it with its fields, the rules fallback learns its
verbs (first word of the title and of each example) and nouns (title and entity words), the form is generated from its JSON
schema (enums, required fields, defaults, max lengths, ids), chat values are extracted by field name / enum / id / number /
email / quoted text, and submission goes through the same preview -> confirm -> audit -> undo path. `tests/test_v3_forms.py`
proves this with a synthetic account sign-up API. Secrets (fields named password, token, pin...) are refused in chat.

## 4b3. Proof layer (pillar 1, in progress)
`agent/reasoning/proof.py`. The Gateway tags every governed metric result with the call that produced it; when evidence is
created the proof is stored in `session.proofs` (call, metric definition, filters, period, row count, data layers, supporting
view). Derived evidence (several queries combined) lists the calls of the turn and says it is not a single call. Endpoints:
`GET /api/session/{sid}/evidence/{id}/proof` and `POST .../replay` (re-runs the call and compares numbers matched by label).
Each answer carries `claims`: one per line, with its facts, evidence and support (`direct` = the number is in the evidence,
`derived` = computed from it, `none`). The chat shows this under "How was this computed?" with a Replay button and a
"Show on screen" link. All of it is best-effort: a recording or replay failure never changes an answer.
Not done yet: a sentence-level entailment checker for the optional LLM text, and per-claim highlighting of the exact cell.

## 4b4. Statistical rigour (pillar 2, in progress)
`agent/reasoning/rigor.py`, attached to `ExplainResult.rigor` at full depth and rendered as "Reality checks" in the answer.
* **Is it unusual?** The month-over-month change is compared with the earlier monthly changes using the median and MAD (robust to a
  launch-month spike, with a floor on the scale so a flat history does not make everything unusual). Fewer than 6 earlier changes
  means "I cannot judge", never a verdict. Only for calendar-month comparisons whose history is available.
* **Counterfactual.** Additive metrics only: "if the top group had stayed at its old level, the change would have been X% instead of Y%".
* **Interaction.** Volume, value and the interaction between them (exact, order-free); shown when the interaction is >= 10% of the change.
* **Checks.** The parts must add up to the headline change; a failing check is added to the answer's notes.
Not yet: uncertainty ranges for non-monthly periods, significance for ratio metrics, a sentence-level entailment checker.

## 4b5. Clarification and robustness (pillar 3, in progress)
* **Confidence-aware clarification** (`agent/clarify.py`, switch `AgentConfig.clarify_ambiguity`, on for the live API, off for library
  and test defaults). A value that could mean different fields (a customer's state or a seller's state) is recorded as an ambiguity
  unless the user named the field, used a seller word, or already chose earlier in the session. The agent then measures the headline
  number under each reading (cited evidence). If the readings differ by at least `materiality_pct` it asks, showing both numbers and
  offering each reading as a ready-to-send message; if they agree it carries on silently. The choice is remembered for the session.
* **Typo repair** (`perceive.repair_typos`, vocabulary from the lexicon): an unknown 5+ letter word one or two letters from exactly one
  known word is read as that word ('reveneu' -> revenue). Correct words and periods are never changed.
* **Unmapped-terms guard** (`clarify.guard_unmapped`, switch `AgentConfig.guard_unmapped`, on for the live API): a period entirely after
  the data ends is explained, not answered; on the rules path, words the parser could not place are either asked about (most of the
  request) or reported as ignored (the answer becomes PARTIAL, never VERIFIED); values or fields dropped during validation are
  carried in `Interp.ignored` for every path. First measurement (rules mode, guard off): 7/7 unanswerable questions were answered
  confidently; the suite exists to show that number fall.
* **Robustness suite** (`eval/robustness.py`, `scripts/run_robustness.py`): typos, multilingual (needs a model), injected
  instructions, unanswerable questions, grounding and screen actions, with measured headline rates: injection success, false answers,
  ungrounded numbers, action hallucination, and calibration error (undefined when the confidence never varies). Quote a rate only
  with the mode it was measured in.

## 4b6. Ablation (pillar 4, in progress)
`eval/ablation.py`, `scripts/run_ablation.py`: one layer off at a time, each switch wired in the agent (`repair_typos`, `guard_unmapped`,
`clarify_ambiguity`, `use_verifier`), re-running the groups it protects (typos, unanswerable, ambiguity, fault injection). The table
is a difference from the full system on the same cases in the same run. `use_verifier=False` makes the agent trust that a screen
action worked, which is what the fault-injection group (dropped filter, wrong route, empty widget) is designed to expose.

## 4b7. Metadata change, not code change (pillar 4)
`tests/test_v3_metadata_change.py` adds a new page with a new filter to a copy of `application.json`, points the backend at it,
clears every metadata cache and checks that the agent finds the page from its title and keywords, opens it with a verified screen
and applies the new filter. Nothing in `agent/`, `backend/` or `web/` changes; afterwards the original metadata is restored.
This is the no-code-platform claim as an automated test: the vocabulary (page words, filter values), retrieval, capability model
and validator are all rebuilt from the metadata.

## 4b8. Relationships between dimensions (pillar 4)
`lexicon.sibling_sets`: dimensions that are the same kind of thing in different roles are found from the DATA (their value sets
overlap heavily: the customer's state and the seller's state share the state codes), never from a shared name part alone
(`order_status` and `payment_status` are not siblings). Within a group, a dimension's role words are the parts of its name the siblings
do not share ('customer', 'seller'); `role_synonyms` in `agent_lexicon.json` adds 'vendor', 'merchant', 'buyer'... Resolution order for an
ambiguous value: the user named the field ("seller state") -> the user named a role ("vendors") -> an earlier choice this session -> the
field the metadata lists first (flagged as an assumption, and asked about when the readings differ materially). This replaces the old
hard-coded `seller_words`; any new pair (origin / destination city) works with no code change.

## 4b9. Task plans (pillar 5a)
`agent/dag.py`: a plan is a list of typed `Task`s (id, dependencies, coroutine, kind, retries, timeout, required). `validate_plan` rejects
duplicate ids, unknown dependencies, cycles and kinds outside an allow-list (so a plan proposed by a model could only contain task
types the code knows). `run_plan` runs independent tasks side by side (bounded by `max_parallel`), passes each task the results of its
dependencies, retries a bounded number of times, times out slow tasks, and contains failures: a required task failing skips its
dependants, an optional one gives them None. It streams `analysis_step` events with `tasks` (the plan) and `task` (id, status, ms), which
the chat shows live as a checklist. The explain flow is built on it: the identity split, every dimension scan, related metrics, last year
and the trend all start together; the drill waits only for the scans; the reality checks wait for scans, identity and trend. A model-proposed
plan is not implemented: the allow-list and validation are the groundwork for it.

## 4b10. Roles and permissions (pillar 5b)
`data/olist/roles.json` (data, not code) defines roles: which modules (groups of pages) a role may open, which write APIs it may call, which
read APIs it may use, and a data SCOPE (for example `customer_region = South`) added to every metric query. `backend/access.py` carries the role
of the current request in a context variable set by `RoleMiddleware` from the `X-Role` header (so it follows the request into worker threads and
the app's own in-process calls). Enforced server-side at the central points: the validator (navigation), the widget data route (a page's
widgets), `run_write` and `undo_action` (every write and undo), and both places that build metric SQL (scope). A scope that cannot be
applied to a dataset REFUSES access rather than widening it. The agent respects the same rules: a refused page or change is explained
("Your role (Analyst) cannot open Support tickets"), figures carry a note that they are limited to the role's scope, the model prompt names
the role, and a new session starts on a page the role may open. The web app has a role switcher, hides pages and action buttons the role
cannot use, and shows a "no access" page for a forbidden link.
This is role CONTEXT for a demonstration, not authentication: there are no passwords or tokens, and an unknown or missing role is the default
role. Not enforced yet: the direct operational read routes (`/api/orders`, ...) when called outside the app's own pages.

## 4b11. Versioned screen state (pillar 5c)
Every answer carries `screen`: the page, filters, dates and state version it was about, and whether it depends on the screen (a view command or
an answer that moved the screen). The chat compares that with the current screen (`web/src/lib/screen.ts`, filter values compared as sets) and,
when the screen has changed since, marks the answer "out of date" with a "Show it again" link that restores it. Not done: invalidating cached
analysis results by dependency (answers are recomputed on every question, so there is no cache to go stale).

## 4c. Known limits (not yet done)
* Web app (done, untested in a browser): the assistant panel renders formatted answers (`web/src/agent/Markdown.tsx`), an
  "Understood as" card with assumptions, live progress steps, a verified / partial / failed badge, citation chips, "Open the view"
  links and follow-up suggestion chips. The agent never draws a form: it fills the website's own action dialog. A write API is
  a form only if an action for it exists in the application metadata (a page action such as "New ticket", or a table-row action);
  `CapabilityModel.form_hosts` maps each API to those actions. The agent opens the hosting page, the dialog appears with the
  values it understood, chat keeps filling it, typing in it syncs back (`POST /api/session/{sid}/form`), and the dialog's own
  Preview / Confirm or the chat's "submit" both go through the normal preview -> confirm -> audit -> undo path. A write API with no
  action in the app is refused as "not available". Confirmation cards appear in the chat panel, so keep it open. Secret fields (passwords...) are refused everywhere in this flow until a dedicated secure input exists.
* No onboarding-style write API exists (tickets, restocks, promotions only); an account form needs one new write API.
* Guarded text-to-SQL "analyst mode" for questions the governed metrics cannot express is not implemented.
* Dimensions available to the analysis engine are those of the four semantic-layer datasets; questions about entities outside
  them (for example per-seller drill-down by seller id) are answered with a stated limitation, not guessed.
* Nothing in this document is a measured result: run `pytest`, `scripts/run_paraphrases.py` and `scripts/run_eval.py`.

## 5. Evaluation
`eval/paraphrases.py` holds held-out paraphrase families per intent; `scripts/run_paraphrases.py` reports intent, metric,
period and filter accuracy for rules-only and live-LLM modes. Fix root causes, never single phrasings.
