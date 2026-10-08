# Mentor review — preparation

The mentors will discuss four things. Below: what to say for each (about 5 minutes total), then questions to ask them. Everything here is from the pre-implementation document; numbers marked (verify) must be checked in the papers before being quoted.

## 1. Problem statement and proposed approach (~2 min)
- **5A in one sentence (from the rulebook):** an assistant that understands a no-code app's navigation, pages, widgets, data views and filters, and lets users navigate, operate and analyze it in natural language, acting on the app and not just answering.
- **Our approach, AppPilot:** the LLM proposes a typed plan; deterministic code validates every action against the app's metadata before running it; the browser reports what it actually rendered, and we compare that with an independently computed expectation; analytics are computed in code over a semantic layer and every number is cited.
- **Why not a raw browser agent:** in the WorkArena paper (ICML 2024), a GPT-4o agent scored 0.0% on list-filter tasks and 10.0% on list-sort, 42.7% overall across 33 task types (checked in the paper's Table 2; 2024 model, so a baseline, not current state of the art).
- Metadata is the foundation: app, dataset and agent are all generated from or validated against one canonical schema.

## 2. KPIs (~1 min)
The rulebook's KPIs and how we measure each:
| Rulebook KPI | Our metric |
|---|---|
| Intent-to-destination accuracy | intent accuracy, top-1 destination page |
| UI-state correctness | per-slot filter/date/sort accuracy, full-state exact match |
| Multi-step task success, steps, latency | task success on L5 tasks, mean steps, p50/p95 latency, pass^k |
| Analytical quality and faithfulness | numeric accuracy vs gold SQL, citation faithfulness |
| Retrieval quality | precision@k, recall@k, MRR over ~120 pages |
| Reliability and safety | action-hallucination rate, recovery rate on injected faults |
| User experience | task completion without manual navigation, intervention rate |

## 3. Expectations and deliverables (~1 min)
- Working app driven by metadata; the agent; a labelled benchmark with baselines and ablations; a results table; a demo.
- We have not built anything yet beyond the contracts: say so, and show the plan and the frozen schema.

## 4. Expected outcomes (~1 min)
Hypotheses to measure, not promises: higher destination and UI-state accuracy and a much lower executed-hallucination rate than a RAG + tools baseline without validation, with recovery from injected faults, reported with confidence intervals and limitations.

## Questions to ask the mentors (these resolve open risks)
1. **Target application:** will a specific no-code platform or metadata export be provided, or do we build a synthetic tenant? Is a synthetic one acceptable for scoring?
2. **Importer:** if portability matters, which platform's export format should we support (we are considering Apache Superset)?
3. **Deliverables:** what exactly must be submitted (code, report, demo video, slides) and when?
4. **Demo:** is a live demo expected at the next stage, and for how long?
5. **Scoring:** how will KPIs be weighted or measured; do they have their own test set?
6. **Resources:** are LLM API credits or a model provided, or should we use our own access?
7. **Compute/hosting:** any requirement to deploy online, or is a local demo fine?
8. **Allowed tools:** any restrictions on frameworks, hosted LLMs, or pre-built libraries?

## Honest limits to volunteer if asked
- Literature review used search summaries; planning papers not reviewed in depth.
- Schema-based validation of tool calls already exists in recent guardrail work; our contributions are applying it to app navigation and UI state, rendered-state verification, and the benchmark.
- Results will be on a synthetic tenant unless a real export is provided.
