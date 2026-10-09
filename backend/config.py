"""Settings from the environment / .env (never hard-code secrets)."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_FILE = ROOT / "data" / "olist" / "application.json"


def _load_env() -> None:
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

DATABASE_URL_RO = os.environ.get("DATABASE_URL_RO", "postgresql://app_ro:app_ro@localhost:5432/appdb")
DATABASE_URL_RW = os.environ.get("DATABASE_URL_RW", "postgresql://app_rw:app_rw@localhost:5432/appdb")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")


@lru_cache(maxsize=1)
def application() -> dict:
    """The application metadata (pages, widgets, datasets, metrics) as plain JSON."""
    return json.loads(APP_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def app_model():
    """The application metadata as validated contracts.metadata.Application models."""
    from contracts.metadata import Application

    return Application.model_validate(application())


def as_of_date() -> date:
    """The dataset's 'today'. Relative dates and the simulated clock resolve against it."""
    return date.fromisoformat(application().get("as_of_date") or "2018-08-31")


# Simulated business clock: starts at as_of_date 18:00 (after every generated record) and advances
# with real time while the server runs, so new records sort after the synthetic history.
_SIM_START = datetime.combine(as_of_date(), datetime.min.time()).replace(hour=18)
_REAL_START = datetime.now()


def sim_now() -> datetime:
    return (_SIM_START + (datetime.now() - _REAL_START)).replace(microsecond=0)
