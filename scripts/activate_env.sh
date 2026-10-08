# source this file:  . scripts/activate_env.sh
# Workstation-only helper: puts Node 20 (conda env "appnode") and the project venv on PATH.
export PATH="$HOME/miniconda3/envs/appnode/bin:$PATH"
[ -f .venv/bin/activate ] && . .venv/bin/activate
# Playwright's bundled Chromium does not officially support this workstation's Ubuntu 20.04; this override works.
export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64
