# Reading notes — candidate base papers for AppPilot (5A)

PDFs: `papers/*.pdf` · text: `papers/txt/` (layout) and `papers/read/` (reading order).
Status: **AXIS, WALT and VACP read in full** (main text + relevant appendices). Supporting papers: key numbers and code links checked by searching their full text, not read end to end.
All numbers below are copied from the papers' own tables/text.

---

## 1. AXIS — *Efficient Human-Agent-Computer Interaction with API-First LLM-Based Agents*
- **Venue:** ACL 2025 (main, long). arXiv 2409.17140v2. Peking Univ., Nanjing Univ., **Microsoft**.
- **Core idea:** API-first agents. The agent calls application APIs ("skills") and uses UI actions only when no API exists. Skills are *created automatically* by exploring the application.
- **Environment interface (App. A):** `state()` returns environment state (control positions, types, selection status; unpacked XML where possible); `step()` hosts a skill executor that runs skills and returns results.
- **Skill = description + skill code + usage example** (App. B.1); 5 types (atomic/composite UI, atomic/composite API, API-UI hybrid); skills nest (hierarchy depth).
- **Offline pipeline (Sec. 4):**
  1. *Trajectory collection*: Follower mode (tasks from help docs) and Explorer mode (LLM-brainstormed), ReAct.
  2. *Skill generation*: Monitor (extract skill insights), Generator (trajectory → skill code with parameter placeholders), Translator (RAG over app documentation to turn UI steps into API calls).
  3. *Skill validation*: static (structural checks: parameters, executor methods, no non-existent skills) + dynamic (Validator agent generates test inputs, Evaluator agent checks the **final state**). Only validated skills enter the library.
- **Adapting to a new app (App. E.2):** provider supplies operational manuals + an *Environment State Interface* + a *Basic Action Interface*; AXIS then explores and grows the skill library.
- **Results:** 73 skills discovered from 347 seed files. On 50 Word tasks vs UFO (GPT-4o): success **52.0% → 84.0%**, time 59.5 s → 29.9 s, steps 3.2 → 2.0, cost $0.4 → $0.2 (Table 1). User study (N = 20, IRB): success L1 98.3% / L2 95.0% vs UI agent 75.0% / 45.0% vs manual 100% / 97.5% (Table 3); NASA-TLX workload strongly reduced (Table 5).
- **Limitations (Sec. 9):** relies on Python-based APIs; exploration stability/efficiency; one application (Word).
- **Code:** no release mentioned in the paper.
- **For us:** peer-reviewed statement of our paradigm. Its "state interface + action interface + documentation → validated skills" ≈ our "metadata export → validated tool contract". Its **user-study design (NASA-TLX, time, success)** is a template for the UX KPI.
- **Difference:** AXIS learns skills by LLM exploration (offline, costly) and verifies skills at *creation* time; no retrieval over many pages, no analytics, no per-request verification of rendered results.

## 2. WALT — *Web Agents that Learn Tools*
- **Venue:** ICLR 2026 (proceedings listing); arXiv 2510.01524 v1 reads "Preprint. Under review". **Salesforce AI Research.**
- **Core idea:** reverse-engineer functionality a website already has (search, **filter, sort**, post, comment, create/edit/delete) into **callable tools**; the agent calls `search(query, category, sort_by)` instead of 8+ UI steps.
- **Pipeline:** discovery (agent explores sections, proposes tool candidates) → construction & validation: browser agent demonstrates → tool-construction agent builds an action script (navigation, extraction, UI interaction, agentic steps), **promotes UI chains to URL parameters**, induces an **input schema with validation (enums for dropdowns, optional fields, usage examples)**, registers and tests on pre-vetted inputs, refines on failure. Objective: minimise FailRate + StepCount + AgenticRatio.
- **Runtime:** GPT-5 planner + GPT-5-mini executor; tools added to the action space; agentic fallback; multimodal DOM parser; **external verification** (WebJudge-style LLM judge).
- **Results:** VisualWebArena avg **52.9%** (Classifieds 64.1%); WebArena avg **50.1%** (Table 1); tools give up to 30.7% relative SR gain and 1.4× fewer steps; with Gemini-2.5-flash SR 52.6% → 55.3% with tools (Table 2); external verification +3.3%.
- **Limitations (Sec. 5):** per-website exploration cost; dynamic UIs/anti-automation; schemas may miss values; **"broader external validity (e.g., enterprise apps) remains to be tested."**
- **Code:** "Our code will be made publicly available" (not in v1).
- **For us:** filter/sort/search/navigate **as schema-validated tools with enums** is exactly our action vocabulary; URL promotion ≈ our deep links / state API. Its own limitation names enterprise apps — our setting.
- **Difference:** WALT discovers tools by exploration and verifies the *task outcome* with an LLM judge; we derive tools from metadata and verify the *rendered state* deterministically.

## 3. VACP — *Visual Analytics Context Protocol*
- **Venue:** arXiv 2603.29322 (cs.HC), 31 Mar 2026; no venue stated. **ETH Zürich + CMU.**
- **Core idea:** make visual-analytics apps "agent-ready" by exposing (1) application state as a temporal semantic graph, (2) available interactions, (3) an execution gateway.
- **Principles P1–P8:** stable unique IDs, snapshot IDs/timestamps, semantic metadata; details-on-demand (only structure in state, data via `inspect_data()` / `get_schema()`, "delegating arithmetic to the system engine and avoiding LLM arithmetic calculation errors"); optional provenance/undo; dynamic interaction exposure with parameter types and ranges; **gateway validation** (resolve reference, check still valid in current snapshot, check parameter types and ranges) with structured errors for self-correction; undo/redo and human-in-the-loop hooks for high-stakes actions.
- **Library:** TypeScript (`@vacp/kit`, Vega-Lite and vgplot adapters), MCP server exposing a **small static tool set** (not one MCP tool per interaction, which would cost too many tokens).
- **Verification:** after acting, "the agent queries the updated state via stable references to verify the outcome" — i.e. it **re-reads the state**, not an independently recomputed expectation.
- **Evaluation:** 5 use cases × 3 tasks (locate/identify/compare) = 15 tasks; GPT-5.2, Claude Sonnet 4.5, Gemini 3 Pro; 3 runs each. Success (Fig. 7): **S1 UI+DOM 28.89–51.11%**; S2 +state in DOM 31.11–51.11%; **S3 VACP only 91.11–100%**; S4 VACP+UI+DOM 95.56–100%. S3 uses the fewest tokens and least time; S4 adds cost without proportional gain. Human experts: 85.71% completion (N = 7).
- **Limitations:** visual nuance lost in text; **developer overhead** to map state and interactions; LLM latency; small benchmark.
- **Code:** github.com/ETH-IVIA-Lab/VACP (license not checked).
- **For us:** the closest *runtime* design (state graph, capabilities, validating gateway, query-based data access, undo, HITL, static tool set).
- **Difference:** needs per-app developer instrumentation; verification by state re-read; 15 atomic tasks; no NL navigation across many pages.

---

## Supporting papers (numbers checked in full text)
| Paper | Venue | Checked fact | Code/data |
|---|---|---|---|
| Beyond Browsing: API-Based Web Agents | ACL 2025 Findings | Hybrid agent 38.9% on WebArena, >24.0 points over browsing | not found in paper |
| WorkArena | ICML 2024 | GPT-4o: 42.7% overall; list-filter 0.0%, list-sort 10.0% (Table 2) | ServiceNow/WorkArena |
| GROUND (governed semantic layer) | arXiv 2026 | 100-question synthetic enterprise benchmark + NHTSA data; enforced filters/security: zero violations on every model | github.com/aravindsp/ground-benchmark |
| Semantic-layer paired benchmark | arXiv 2026 (authors from Cube, a semantic-layer vendor) | +17 to +23 pp accuracy with a semantic-layer document; 67.7–68.7% vs 45.5–50.5% | github.com/cubedevinc/semantic-layer-benchmark |
| LLMCompiler | ICML 2024 | planner → task-fetching unit → executor, parallel calls | github.com/SqueezeAILab/LLMCompiler (MIT) |
| SWE-agent (ACI) | NeurIPS 2024 | interface design for agents is the limiting factor | SWE-agent repo |

## Gap that remains ours (relative to these three)
| | AXIS | WALT | VACP | AppPilot |
|---|---|---|---|---|
| Where the action contract comes from | LLM exploration + docs | LLM exploration per website | developer instrumentation | **deterministic derivation from the no-code metadata export** |
| Runtime verification | none per request (skills validated offline) | LLM judge on task outcome | agent re-reads state | **render acknowledgement vs independently recomputed expectation** |
| Many-page retrieval | — | — | — | **hybrid retrieval + graph over 100+ pages** |
| Analytics | — | — | data queries | **governed semantic layer, contribution analysis, cited numbers** |
| Evaluation | Word tasks + user study | WebArena/VWA | 15 VA tasks | **KPI-aligned benchmark (official 5A KPIs)** |

Honest limit: our derivation needs an export. For apps without one, AXIS/WALT-style exploration would be needed.
