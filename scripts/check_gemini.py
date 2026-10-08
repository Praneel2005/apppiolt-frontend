"""Tests the Gemini API key and, crucially, whether our `Plan` schema works with Gemini structured output.
Run from the repo root after:  pip install -r requirements.txt   and filling .env (GEMINI_API_KEY, GEMINI_MODEL)
    python scripts/check_gemini.py
Exit code 0 only if BOTH checks pass. If the Plan check fails, paste the whole output to the planner."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
try:
    from google import genai
except ImportError:
    sys.exit("google-genai is not installed: pip install -r requirements.txt")

from pydantic import BaseModel

from contracts.actions import Plan

key, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_MODEL")
if not key or not model:
    sys.exit("Set GEMINI_API_KEY and GEMINI_MODEL in .env (model name as shown in Google AI Studio).")
client = genai.Client(api_key=key)


class Tiny(BaseModel):
    page: str
    filters: list[str]


def attempt(label, schema, prompt):
    t = time.time()
    try:
        r = client.models.generate_content(
            model=model,
            contents=prompt,
            config={"response_mime_type": "application/json", "response_schema": schema},
        )
        print(f"[OK]   {label} ({time.time() - t:.1f}s)\n       parsed: {str(r.parsed)[:400]}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {label}: {type(e).__name__}: {str(e)[:600]}")
        return False


ok1 = attempt("tiny schema", Tiny, "Return page 'Revenue by Region' and filters ['West'].")
ok2 = attempt(
    "our Plan schema (contracts/actions.py)",
    Plan,
    "The user says: open Revenue by Region and filter region to West. Return a Plan with intent 'set_state' "
    "and two steps using tools navigate and set_filter.",
)
if ok1 and not ok2:
    print("\nThe API works but our Plan schema is rejected or fails. Tell the planner: we will flatten the schema "
          "(e.g. `args` as a JSON string) and update contracts/actions.py by agreement.")
sys.exit(0 if (ok1 and ok2) else 1)
