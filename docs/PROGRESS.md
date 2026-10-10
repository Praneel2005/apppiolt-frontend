# PROGRESS LOG (the implementer updates this after every card)

Last updated: 2026-10-08  ·  Mode: planner builds directly on the workstation (the local-agent route was dropped)  ·  Current phase: A  ·  Next: mock backend + FRONTEND_BRIEF for the frontend pair, then backend core (T9-T11)

## Facts established
- Contracts in `contracts/` are frozen; `python -m pytest` = 33 passing (Python side verified on the planner's workstation).
- Postgres compose file + read-only role verified on the planner's workstation. SQL drafts (`sql/05`, `sql/10`) parse on empty tables; **unverified on real Olist data**.
- **Unverified:** JS hash test (`node --test contracts/ts/`), Gemini access and `Plan`-schema compatibility, Olist license/columns.
- Calendar: mentor review with slides 9 Oct 2026; live offline demo on this machine 11 Oct 2026.

## Cards
| Card | Status | Result / notes |
|---|---|---|
| T0 environment + baselines | done (workstation) | Python 3.12 venv + `requirements.lock.txt` (50 pins); `pytest` 33 passed; **`node --test contracts/ts/` 13/13 pass: JS hash == Python hash on all vectors**; Postgres 16 compose + read-only role verified; Node 20.20.2 via conda env `appnode`; headless Chromium works with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64`; Gemini check **not yet run** (no key) |

| T2 inspect Olist | done | 9 CSVs, counts/nulls/date ranges in `docs/DATA_NOTES.md`; as_of_date 2018-08-31; **license sentence still to record** |
| T3 load raw tables | done | `generator/load_olist.py` (COPY, BOM-safe), 3.5 s; counts equal CSVs |
| T4 fact tables | done | `sql/10_facts.sql` verified on real data + `sql/06_category_fixes.sql`; `tests/test_olist_db.py` 14 checks; total pytest 47 passed |

| T5 semantic layer | done | `generator/semantic_layer.py`: 4 datasets, 15 governed metrics (BRL; rates as ratios), enum values from DB (27 states, 5 regions, 74 categories); `tests/test_semantic_layer.py`; pytest 52 passed. Example Q2-2018: revenue 2,849,730.26 BRL, 19,897 orders, late rate 0.0483 |

| T6 application generator | done | `generator/build_application.py` → `data/olist/application.json` (committed): 7 modules, **100 pages** (7 overviews, 61 one-dimension breakdowns, 18 two-dimension breakdowns, 14 trends), 215 widgets; unique descriptions; state-name, region, payment and category synonyms; `tests/test_application.py`; pytest 58 passed |
| T7 frontend renderer + UI/UX overhaul | done | Built responsive React+TS app in `frontend/`: 100 pages dynamic renderer, LeftNav with module icons & search, FilterBar with date presets & searchable multiselects, KPI / Bar / Line / Grid widgets with formatting, WebCrypto SHA-256 series hash matching all 12 vectors (12/12 Vitest passed), WebSocket `apply_state` + `render_ack` verified E2E with Playwright (`verify.ok = true` + fault injection detected 4 mismatches), and full AI-native agentic UI with interactive `ChatPanel` (thought timeline, live verification badge, interactive citations `[e1]` calling `highlightWidget`, confirm mode, undo) and `DebugDrawer`. |

## Decisions and deviations (append, dated)

- 2026-10-08: Frontend is built by Aditya + Shreeniketh in `frontend/` via the GitHub repo (they are not on the workstation network → they need a local mock backend). Praneel + Claude build data, backend, agent, evaluation.

## Open issues

- 2026-10-08: Workstation facts: Ubuntu 20.04, 48 cores, 94 GB RAM, Docker 28.1.1 (another container is already running here; do not touch it; ours is project `apppilot`), ports 5432/8000/5173 free, network reaches npm/PyPI/Kaggle/Google APIs. `. scripts/activate_env.sh` before running node/python.
- 2026-10-08: Demo is on the human's local PC (11 Oct). Mitigation: git as source of truth + a fresh-clone rehearsal on that PC early (see decisions file).
