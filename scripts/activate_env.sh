# source this file from the repo root:  . scripts/activate_env.sh
# Workstation-only helper: activates the project venv, then puts Node 20 (conda env "appnode") on PATH.
# Order matters: activating a venv restores its saved PATH, which would drop anything added before it.
[ -f .venv/bin/activate ] && . .venv/bin/activate
export PATH="$HOME/miniconda3/envs/appnode/bin:$PATH"
# Playwright's bundled Chromium does not officially support this workstation's Ubuntu 20.04; this override works.
export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64
