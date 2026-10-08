# The one prompt for the local coding agent

Paste everything inside the box into a **new agent session** (also use it again whenever you start a fresh session; the agent re-reads the repo and `docs/PROGRESS.md` to regain context).

```
You are the sole implementer of AppPilot, a context-aware application agent for HackFest 2026, problem 5A,
in this repository. A planner (Claude) writes the specs and reviews your reports; a human relays between you.
You build ALL components: data/metadata/evaluation, agent, backend, frontend.

READ, IN THIS ORDER, before doing anything:
1. AGENTS.md  (your standing rules - follow them strictly)
2. docs/PROGRESS.md  (state of the project; you maintain it)
3. docs/00_DECISIONS_AND_PLAN.md  (decisions, shared semantics S1-S9, endpoints)
4. docs/MASTER_BUILD_PLAN.md  (your ordered task cards and gates)
5. every file in contracts/  (frozen data formats - never edit them)
Component specs are in docs/PRANEEL.md, docs/SHASHANK.md, docs/SHREENIKETH.md, docs/ADITYA.md; they describe
components, not people. Cards T0-T8 have extra detail in docs/TASKS_FOR_LOCAL_AGENT.md.

WORKING RULES (summary; AGENTS.md has all of them):
- Do the cards in order. Within a phase keep going card to card; STOP at every GATE and print the report block
  defined in AGENTS.md, then wait.
- Never edit contracts/. Never hard-code page/widget/filter ids, routes, values or metric SQL: they come from metadata.
- Never claim anything works unless you ran it; paste real command output. Never invent data, numbers or paper claims.
- If a spec is ambiguous or a contract looks wrong, stop and report instead of guessing.
- Update docs/PROGRESS.md and make a git commit ("T<id>: ...") after every card. Do not commit .env, API keys or the Olist CSVs.

START NOW with card T0 in docs/MASTER_BUILD_PLAN.md. After T0 and T1 you reach GATE G0: print the report block
(including the full output of check_env, pytest, node --test and docker compose ps) and stop.
```

## When to start a fresh session
Agents lose track in very long sessions. Start a new session (same prompt) after each GATE or whenever the agent starts forgetting rules. Nothing is lost: state lives in `docs/PROGRESS.md`, the git history and the repo.

## What to bring back to Claude at each gate
The report block, plus anything that surprised you. Claude replies ✅ / ❌ with fixes / go-ahead, and amends the plan if reality differs from the spec.
