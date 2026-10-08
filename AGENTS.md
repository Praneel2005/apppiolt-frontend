# AGENTS.md — standing instructions for the coding agent working on AppPilot

You are the **sole implementer** of all four components (data/metadata/evaluation, agent, backend, frontend). A separate **planner/monitor** (Claude) writes the specs and reviews your reports; the human sits between you. Follow this file at all times.

## What the project is (30 seconds)
AppPilot is a context-aware agent for a no-code business application (HackFest 2026, problem 5A). A user types a request ("compare revenue for São Paulo last quarter with the quarter before"); the agent finds the right page, sets filters and dates on the app, fetches data, computes the comparison, and explains it with cited numbers. Core idea: **the LLM proposes, plain code validates before and verifies after, every number is cited.** The app is drawn from a metadata description file; the data is the public Olist e-commerce dataset in Postgres.

## Read first, in this order (every new session)
1. `docs/PROGRESS.md` — what is done, what is next, decisions and open issues (**you maintain it**)
2. `docs/00_DECISIONS_AND_PLAN.md` — decisions, shared semantics S1–S9, endpoint table
3. `docs/MASTER_BUILD_PLAN.md` — the ordered cards and gates (**your task list**)
4. `contracts/` — the frozen data formats (read the docstrings)
5. Component specs, as each card points to them: `docs/PRANEEL.md` (data, metadata, evaluation), `docs/SHASHANK.md` (agent), `docs/SHREENIKETH.md` (backend, validator, verifier, analytics), `docs/ADITYA.md` (frontend). These describe **components, not people**: you build all of them. Card details for T0–T8: `docs/TASKS_FOR_LOCAL_AGENT.md`.

## Hard rules
1. **Never edit anything in `contracts/` unless the task card explicitly says so.** If a contract looks wrong, stop and report it (see "When blocked"). Do not "fix" it silently.
2. **Never hard-code** page ids, widget ids, routes, filter ids, filter values or metric SQL in code. They come from the metadata (`Application`).
3. **Never claim something works unless you ran it.** Paste the command and the actual output in your report. If you could not run it, say so.
4. **Do not invent results, numbers, dataset facts or paper claims.** If you need a fact (a column name, a row count, a license), look at the real file/page and quote it.
5. **Follow the card order in `docs/MASTER_BUILD_PLAN.md`.** Do exactly what each card says; no extra features. Continue from card to card inside a phase, but **stop at every GATE**, print the report block and wait for the human to bring back the planner's review.
6. **Tests first or alongside.** Every task ends with a runnable check. Keep `python -m pytest` (33 tests) green at all times; the JS hash test (`node --test contracts/ts/`) must stay green.
7. **Secrets:** never commit `.env`, API keys, or the Olist CSVs. `.gitignore` already excludes them; do not weaken it.
8. **Dependencies:** list every new dependency in your report and add it to `requirements.txt` (Python) or `package.json` (JS). Prefer what is already listed.
9. **Folder ownership:** commit only inside the folders your task names. Others own the rest.
10. If a spec is ambiguous, **ask (stop and report)**; do not guess about contracts, semantics S1–S9, or the hash.
11. **Update `docs/PROGRESS.md` after every card** (card id, status, one-line result, any decision or deviation). It is how a fresh session regains context.
12. Commit after every card with a message `T<id>: <what>`; never commit failing tests.

## Repo layout (target)
```
contracts/        frozen shared formats + tests (do not edit)
docs/             plans and task cards
sql/              database scripts (00_roles.sql exists)
generator/        Olist loader, semantic layer, application + task generators   (Praneel)
importers/        foreign-export importers (Superset)                           (Praneel)
eval/ results/    evaluation harness and outputs                                 (Praneel)
agent/            LLM adapter, retrieval, planner, narrator, baselines            (Shashank)
backend/          FastAPI, sessions, validator, query engine, verifier            (Shreeniketh)
frontend/         React app                                                       (Aditya)
scripts/          check_env.py, check_gemini.py
tests/            contract tests
data/             local data (gitignored CSVs); data/olist/application.json is committed once generated
```

## Commands
```
python -m venv .venv  &&  activate it
pip install -r requirements.txt            # core; requirements-ml.txt is heavy, install later
python -m pytest                            # must pass (33)
node --test contracts/ts/                   # JS hash vectors (needs Node 20+)
python scripts/check_env.py                 # toolchain check
docker compose up -d db                     # Postgres 16 on :5432 (user app, role app_ro read-only)
docker compose down                         # stop (add -v to delete the data volume)
python scripts/check_gemini.py              # Gemini key + Plan-schema compatibility
```

## Reporting format (print this at every GATE, and whenever blocked)
```
TASK: <id and title>
STATUS: done | partial | blocked
FILES CHANGED: <list>
COMMANDS RUN + OUTPUT: <exact commands and the real tail of their output>
TESTS: <what passes / fails, counts>
DEVIATIONS FROM THE SPEC: <anything you did differently and why, or "none">
NEW DEPENDENCIES: <or "none">
OPEN QUESTIONS / RISKS: <or "none">
```

## When blocked
Stop. Report `STATUS: blocked`, quote the exact error, say what you tried (max 2 attempts), and propose 1–2 options. Do not work around a contract or invent data to keep going.
