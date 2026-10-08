# Moving the project to your local machine (the demo machine)

**Roles [INF] (updated: one implementer):** your local machine (Docker, Node, and the Antigravity agent) is the **implementer and the demo machine**. Claude is the **planner/monitor**: it writes the specs, you run them through the local agent, and you paste the agent's reports back for review. Teammates use the same repo on their own machines with their own `docs/<NAME>.md`.

I have **not verified what Antigravity can do**; the brief is plain Markdown so any coding agent can follow it. If your agent reads a rules file by a different name than `AGENTS.md`, copy the contents there.

## Step 1 — Get the files onto your machine
**Recommended: GitHub.**
1. On GitHub create a **private** repository, e.g. `apppilot`.
2. On the machine that holds this folder (the workstation), from the `apppilot/` directory:
   ```
   git init
   git add .
   git commit -m "AppPilot: contracts, plans and handoff"
   git branch -M main
   git remote add origin https://github.com/<you>/apppilot.git
   git push -u origin main
   ```
   (Nothing sensitive is in the folder; `.gitignore` excludes data CSVs, `.env` and caches.)
3. On your local machine: `git clone https://github.com/<you>/apppilot.git` and `cd apppilot`.

**Fallback: ZIP.** A zip of the folder is at `/home/jatin/fest/apppilot_handoff.zip` (also contains the two big Markdown documents). Unzip it on your machine, then `git init` there and push to GitHub so teammates can clone it. From then on use git only; do not keep two copies.

Also copy these two files from `/home/jatin/fest/` next to the repo (they are references, not code): `5A_Context_Aware_Application_Agent_PreImplementation_Plan.md` and `5A_Simple_Explainer_and_Slide_Content.md`.

## Step 2 — Check the toolchain
```
python scripts/check_env.py
```
You want `OK` for git, node (20+), npm, docker, docker compose, and port 5432 free. If something is missing, install it first (Python 3.12, Node 20 LTS, Docker Desktop on Windows/macOS). **Windows:** use PowerShell or Git Bash; run Docker Desktop before `docker compose`; if the line endings give trouble run `git config core.autocrlf input`.

## Step 3 — Python environment
```
python -m venv .venv
# Linux/macOS: source .venv/bin/activate      Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest
```
Expected: `33 passed`. After a clean install run `pip freeze > requirements.lock.txt` and commit it (pins versions so everyone matches).

## Step 4 — Verify the JavaScript hash (this was never run before)
```
node --test contracts/ts/
```
Expected: every `vector:` test and the tie-rounding test pass. **If any fail, stop and paste the output to Claude**; it means the Python and JavaScript hashes disagree, which breaks the verifier.

## Step 5 — Database
```
docker compose up -d db
docker compose ps          # db should become "healthy"
```
I tested this exact compose file on the workstation: the database starts, the `app_ro` role can read, its inserts are rejected, and `statement_timeout` is 5 s. To reset the data: `docker compose down -v`.

## Step 6 — Gemini
1. Create a key in Google AI Studio and note the model name shown there (Flash or Flash-Lite).
2. `cp .env.example .env` and fill `GEMINI_API_KEY` and `GEMINI_MODEL`. Never commit `.env`.
3. `python scripts/check_gemini.py`
   - The **second check matters most**: it sends our real `Plan` schema to Gemini structured output. If it fails, paste the full output to Claude; we will flatten the schema together.
   - Free-tier limits are tight (see `docs/SHASHANK.md` S1). Do not loop this script.

## Step 7 — Olist data
Follow task card T4 in `docs/TASKS_FOR_LOCAL_AGENT.md` (download from Kaggle, read the license on the page, record real row counts).

## Step 8 — Start the implementer
Open the repo in Antigravity (or your agent) and paste the single prompt in `docs/CLI_PROMPT.md`. It tells the agent what to read and to start with T0. After that it works through `docs/MASTER_BUILD_PLAN.md`, stopping at each GATE.

## The review loop (how Claude monitors)
1. The agent works card by card inside a phase and updates `docs/PROGRESS.md`.
2. At every **GATE** it prints the **report block** defined in `AGENTS.md` and stops.
3. You paste that report (not the whole terminal) to Claude here, plus anything surprising.
4. Claude replies with: ✅ accepted / ❌ what to fix / the next card, and corrects the plan if needed.
Claude cannot see your machine; it relies on the real command outputs in the reports. Do not paraphrase them.

## What to paste back after Steps 2–6 (first message to Claude)
```
check_env output:
pytest result:
node --test result:
docker compose ps:
check_gemini output:
OS and versions:
```

## Why two AI agents will not fight
The referee is the contracts and the tests: the agent may not change `contracts/`, and both sides are judged by the same test suite. Specs come only from the repo's `docs/`, so there is one source of truth.
