# KPI plan: what each hackathon KPI measures, and where it is measured

The rulebook lists seven KPIs. This table says what we count for each, which part of the system produces the number,
and what is still to build. "Live" = already measured for any session (`GET /api/session/{sid}/stats`, the **Metrics** tab
of the assistant panel). "Evaluation" = measured offline on a labelled task set by the evaluation harness (next stage).

| # | KPI (rulebook wording, short) | What we count | Produced by | Status |
|---|---|---|---|---|
| K1 | Intent-to-destination accuracy | share of requests whose final page equals the gold page (top-1), plus top-k from retrieval | gold tasks with `expected.page_id`; the executor's final `UiState.page_id` | Evaluation (harness + tasks pending) |
| K2 | UI-state correctness | per task: filters, date range and sort equal the gold state after normalisation (synonyms, presets); reported per element and per task | `UiState` after the run vs gold `expected.state`; state-based scoring, not text matching | Evaluation |
| K3 | Task success for multi-step requests; average steps and latency | success = all required state + all required evidence + no forbidden write; mean steps per run; p50/p90 latency | live: `runs`, `run_success_rate`, `avg_steps_per_run`, `latency_ms`; offline: pass^k over repeated runs | Live counters done; success labels need the gold tasks |
| K4 | Analytical quality; faithfulness (traceable to views) | numeric correctness of trends/comparisons against independently computed values; share of answer numbers that appear in cited evidence; share of evidence that links to a supporting view | evidence ids (`E1…`), `analyze_trend` / `compare_periods` / `explain_change` results, `view.url` deep links, citation check in the narrator | Tools and evidence done; citation check comes with the narrator |
| K5 | Retrieval quality over metadata at scale | precision@k, recall@k, MRR for page and API retrieval over 115 pages / 243 widgets / 41+ APIs, also on a scaled copy (generated pages) | `GET /api/search` (BM25 + synonyms) and the agent's hybrid retriever | Retriever done; the metrics need labelled queries |
| K6 | Reliability and safety | action-hallucination rate = invented references per plan caught by the validator (and, with the validator off, executed wrongly); verification pass rate; error/recovery rate; latency | live: `validation_failures`, `issues_caught_by_validator`, `verify_pass_rate`, `confirmations_*`, `writes_undone`; offline: ablation validator on/off, verifier on/off, injected browser faults | Live counters done; ablations need the harness |
| K7 | User experience | share of actions done by the assistant vs by hand (`agent_share_of_actions`); tasks completed without manual navigation; thumbs up/down | live: `agent_steps`, `manual_changes`, `feedback`; offline: scripted user study of 10 tasks done by hand vs with the assistant, counting clicks | Live counters and thumbs done; the study is planned |

## Measurement rules (so the numbers can be trusted)

- **State-based scoring.** A task passes by the state of the application and the evidence collected, never by comparing
  the assistant's wording. This is what makes K2/K3 objective.
- **Gold values come from independent SQL on the original `raw_*` tables**, not from the agent's own query path.
- **Seeded and repeatable.** Gold tasks are generated with a fixed seed; every run records the model, prompt version and
  data version. Repeated runs give pass^k and bootstrap confidence intervals.
- **Baselines and ablations** are switches in `AgentConfig` (`use_validator`, `use_verifier`, `use_graph_expansion`,
  `use_citation_check`): B0 = no metadata (LLM only), B1 = retrieval + one tool call without validation, B2 = full system minus
  one component at a time.
- **Safety cases are scored too:** unsafe writes, out-of-scope requests, instructions hidden in review text and
  non-existent pages must be refused, not attempted. They are in the gold set with `expected.outcome = "refuse"`.
- **Simulated data is labelled.** Answers about targets, SLA, forecasts, inventory, tickets, replies and promotions say
  they describe the simulation (`data_layers` in every evidence record).

## Still to build for full coverage

1. Gold task generator (about 150 tasks over navigation, state, analytics, multi-step, writes, refusals) with expected state and values.
2. Evaluation harness that runs a task through the agent against a fresh session and scores K1–K7 as above.
3. Retrieval labels (query -> relevant pages/APIs) for K5, including a scaled application with several hundred generated pages.
4. The narrator's citation check (every number in an answer must appear in cited evidence) for the K4 faithfulness figure.
