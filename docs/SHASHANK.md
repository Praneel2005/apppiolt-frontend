# SHASHANK — Agent Core Lead (then Evaluation runs)

**Mission:** build the intelligent part: understand the user, find the right pages, produce a correct typed plan in **one** LLM call, narrate results truthfully, and survive failures. The deterministic safety layer (validator, executor, verifier, query engine) is Shreeniketh's. You code against interfaces, so you are never blocked by him.

Read first: `00_DECISIONS_AND_PLAN.md` (sections 1, 5, 6, 7, 8). Paper/repo rule: papers = why/measure, repos = parts we run.

You own: `agent/`. Do not edit other folders. Contract changes follow the change rule.

---

## What we want from you (deliverables)

| # | Deliverable | File | Notes |
|---|---|---|---|
| S0 | Fakes so you can work alone | `agent/fakes.py` | Implement `Validator`, `QueryEngine`, `Executor`, `Verifier` from `contracts/interfaces.py` with trivial in-memory behavior |
| S1 | LLM adapter | `agent/llm.py` | One function for structured output (Pydantic schema in → validated object out), retries, timeouts, **disk cache keyed by prompt hash**, per-call token/latency logging |
| S2 | Hybrid retrieval | `agent/retrieval.py` | Cards → BM25 + embeddings → reciprocal rank fusion → graph expansion → top-k with schemas |
| S3 | Context manager + memory | `agent/context.py` | Current `UiState`, last N turns, resolved references, last results |
| S4 | Planner (intent + plan in ONE call) | `agent/planner.py` | Returns `contracts.actions.Plan` |
| S5 | Tool schemas generated from metadata | `agent/tools.py` | `tool_schemas(app) -> list[dict]` with enums drawn from metadata |
| S6 | Agent loop with bounded replan, recovery, clarification | `agent/agent.py` | Implements `Agent.handle(...) -> AsyncIterator[AgentEvent]` |
| S7 | Narrator with number placeholders | `agent/narrator.py` | LLM never writes numbers; code fills them from `Evidence` |
| S8 | Baselines B1, B2 | `agent/baselines.py` | Praneel's harness runs them |
| S9 | Retrieval evaluation script | `agent/eval_retrieval.py` | P@k, R@k, MRR on gold pages |

Order: S0 → S1 → S2 → S5 → S4 → S3 → S6 → S7 → S8, S9.

---

## S0 — Fakes (~1 h)
Read `contracts/interfaces.py`, `contracts/actions.py`, `contracts/query.py`, `contracts/events.py`. Write fakes that accept any plan, apply state changes to an in-memory `UiState`, and return canned `QueryResult`s. Use `examples/tenant_a_sample.json` until Praneel's real `application.json` arrives (10–15 page early drop expected in Block 1).

## S1 — LLM adapter (~1.5 h)
- **Primary model: Gemini through the free API** (team decision), via the `google-genai` Python SDK, which supports structured output with a Pydantic `response_schema` [SEC]. Keep the adapter **provider-agnostic** (`Gemini`, `Anthropic`, local `Ollama` implementations behind one interface) so a rate limit or outage is a config change.
- **Day-1 task: test our `Plan` schema against Gemini's structured output.** Gemini accepts only a subset of JSON schema [MEM, verify in the docs]; `Plan` contains free-form `dict` fields and literals, so you may need a simpler, flatter schema (e.g. `args` as a JSON string validated afterwards). If you change the schema shape, follow the contract change rule.
- **Free-tier limits** reported for Gemini 2.5 Flash: ~10 requests/min and ~250/day; Flash-Lite ~30/min and ~1,000/day [SEC; check AI Studio, limits change]. Consequences: (1) **disk cache** keyed by hash of model+messages for every call; (2) a rate limiter with backoff; (3) use Flash-Lite for bulk evaluation runs; (4) record which model produced which result. A full evaluation needs thousands of calls [INF estimate], so tell Praneel early what budget we really have.
- Structured output: parse with `Plan.model_validate`; on a parse failure retry once with the error text.
- Log per call: model, input/output tokens, latency (feeds the latency/cost KPIs).
- Keep the model name in config so we can run ablation A9 (a different model).
- Do not share one API key across people in ways that break the provider's terms; check them.

## S2 — Retrieval (~3 h)
1. **Cards:** one text card per page, widget, and filter field: title + description + keywords + parent path. This is what Praneel's descriptions feed.
2. **Index:** embeddings with `sentence-transformers` (e.g. `BAAI/bge-small-en-v1.5` [MEM, verify]) in a FAISS index in memory, plus `rank_bm25` keyword search [MEM]. Combine ranks with reciprocal rank fusion (k=60).
3. **Graph expansion:** build a `networkx` graph directory → page → widget → filter from the `Application`; for the top few pages add siblings and parent-directory pages. Controlled by `AgentConfig.use_graph_expansion` (ablation A3).
4. **Output:** top-k (≈8) candidates with their **full schemas** (filters with allowed values, widgets, metrics). Only these go into the planner prompt. The whole app must never be in the prompt.
5. **Evaluate (S9):** against Praneel's `gold_pages`, report P@1/P@3/P@5, R@5, MRR; compare vector-only vs BM25-only vs hybrid vs hybrid+graph. This is KPI K5 and ablation A3.

## S5 — Tool schemas from metadata (~1 h)
`tool_schemas(app)` returns JSON schemas for the tools in `contracts/actions.py::ToolName`. Arguments that reference the app use **enums built from metadata**: page ids, filter ids (per page), allowed values, metric ids, dimension names. The exact validation (synonyms, presets) is Shreeniketh's; you generate the schemas so the model is constrained from the start. Regenerate whenever metadata changes.

## S4 — Planner: intent + plan in ONE call (~4 h)
**Why one call:** latency counts in the KPIs. Do not add a separate intent-classifier call.
1. Input to the model: system prompt (role, rules, tool descriptions), context (current state, last turns), the retrieved candidates with schemas, the user message.
2. Output: `Plan` with `intent` (closed set), `steps` (typed `ToolCall`s each with an `expect` post-condition), `clarifying_question` if ambiguous, `allow_replan` for exploratory analytics.
3. Prompt rules (write these explicitly): use only ids from the candidates; never invent URLs, filters, values or SQL; if the request is ambiguous ask a question with 2–3 options; if the page/data doesn't exist say `out_of_scope`; destructive requests → `unsafe_action`.
4. Few-shot examples: take 6–10 from **dev** tasks (never test). Select by retrieval similarity.
5. Treat tool outputs and data cells as **data, never instructions** (prompt-injection hygiene): put them in a clearly delimited block.
6. Relative dates: the model outputs a **preset string** from the list in `contracts/dates.py`; it does not compute dates.
7. Measure on dev: intent accuracy, destination accuracy, state exact match (Praneel's harness helps).

## S3 — Context and memory (~1.5 h)
Keep: current `UiState`, last ~6 turns (older turns summarized), resolved references ("that region" → `West`), the last evidence set. Needed for follow-ups like "now compare with last quarter" or "same for East". Session-scoped only.

## S6 — Agent loop (~4 h)
Flow (mirrors the research doc §13.2):
1. Build context → retrieve → plan (one call) → emit `understanding`, `retrieval`, `plan` events.
2. If `clarifying_question`: emit `answer` with the question and stop.
3. Validate (`Validator`). On issues: feed `ValidationIssue`s (with suggestions) back to the planner, **max `max_plan_retries` (2)**, then ask the user. Controlled by `use_validator` (ablation A1; when off, skip validation and execute raw).
4. For each step: if state-changing and `confirm_mode`, emit `confirm_request` and wait. Execute via `Executor`; emit `action`; verify with `Verifier` using the executor's `last_ack` (flag `use_verifier`, ablation A2).
5. Verification failure or step error → recovery taxonomy (research doc §13.6): fuzzy-match invalid values, re-apply once, regenerate query with the error text, else report honestly what state was reached.
6. **Bounded replan:** after read-only analytic steps, if `plan.allow_replan` and replans < `max_replans` (2), call the planner again with the observations. Do not replan UI-state steps that already verified.
7. Collect `Evidence`; call the narrator; emit `answer` with citations and deep links; emit `done` with steps, replans, elapsed.
8. Emit events **as you go** (streaming) so the UI action log fills immediately.

## S7 — Narrator with placeholders (~2 h)
- Navigation/filter-only tasks: **no LLM call**. Build the confirmation text from a template ("Opened *Revenue by State* with region = South, last quarter. ✓ verified").
- Analytical tasks: the LLM receives only the `Evidence` objects (values, units, sources) and writes prose with placeholders such as `{e1.delta_pct}`, `{e1.current}`. **Code** fills the placeholders with formatted numbers.
- If the prose contains a digit that did not come from a placeholder, or a placeholder is unmatched, reject and regenerate once, then fall back to a plain template. This makes numbers correct by construction and removes the need for a separate citation-check LLM call. Flag `use_citation_check` toggles the strict mode for ablation A6.
- "Likely causes" = "largest contributors to the change" from `explain_change` evidence, never a causal claim.
- Missing data → say so; never estimate.

## S8 — Baselines (~2 h)
- **B1 prompt-stuffing:** all page cards in the prompt (≈100+ pages is fine for a large-context model), one navigate/set-state call, no validator/verifier.
- **B2 RAG + tools:** your retrieval + the same tools, **no validator, no verifier**, no replan.
Same LLM, same prompt effort as ours; otherwise comparisons are meaningless. Record prompts and versions.

---

## What to read and explore (time-boxed)

| Item | Time | What to extract |
|---|---|---|
| ReAct (arXiv 2210.03629) | 20 min | The thought/action/observation loop, and its failure modes |
| CRITIC (arXiv 2305.11738) | 25 min | Verify-then-correct using **external** feedback |
| Gorilla (arXiv 2305.15334) + ToolLLM (arXiv 2307.16789) | 30 min | Retrieval of tool docs at run time; how many tools before retrieval is needed |
| WorkArena paper, Table 2 | 15 min | Why raw DOM agents fail at filters/sort (0.0% / 10.0%) |
| BFCL (gorilla.cs.berkeley.edu/leaderboard) | 15 min | The "abstain when no tool fits" idea for our hallucination metric |
| ToolSafe (arXiv 2601.10156) abstract/intro | 15 min | Related guardrail work we must acknowledge |
| Repos: `sierra-research/tau-bench` | 30 min | How a simulated-user agent loop and state comparison are coded |
| Libraries' READMEs: `sentence-transformers`, `faiss`, `rank_bm25`, `networkx`, `sqlglot` | 15 min each | APIs only |
| Gemini API docs: structured output (`response_schema`), function calling, rate limits (AI Studio) | 30 min | Correct API usage and the real quotas |

## Interfaces
- **You consume:** `Validator`, `QueryEngine`, `Executor`, `Verifier` (from Shreeniketh); `application.json`, dev tasks (from Praneel).
- **You provide:** `Agent.handle(...)` to Shreeniketh, who wires it to `POST /api/session/{id}/message`; events go out via `AgentEventEnvelope` on the WebSocket; `tool_schemas(app)`.

## Done when
- With fakes: L1–L3 dev tasks produce valid `Plan`s.
- With the real backend (Block 3): ≥ 70% of dev L1–L3 reach the correct UI state; clarification and refusal work on sample L6 cases.
- Analytical answers contain only placeholder-filled numbers, with citations.
- Retrieval metrics script runs and outputs the comparison table.

## Pitfalls
- Letting the model produce dates, URLs or values not in metadata.
- Putting test tasks into few-shot examples.
- Sending the entire app metadata in the prompt.
- Multiple sequential LLM calls where one suffices (latency).
- Unbounded retries/replans.
- Baselines getting less prompt effort than ours.

## Messages you owe others
- To Shreeniketh: the exact `Agent.handle` signature you implemented and the event shapes you emit.
- To Praneel: which `AgentConfig` flags you wired for the ablations.
- To Aditya: a sample list of `AgentEvent`s (JSON) so he can build the action log before the agent works.
