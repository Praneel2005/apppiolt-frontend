"""Two connection pools, two database roles.

    ro()  app_ro: read-only, 5 s statement timeout. Every read endpoint and the metric engine.
    rw()  app_rw: can change ops_* tables only (sql/30_roles_and_grants.sql). Write endpoints only.
"""
from __future__ import annotations

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.config import DATABASE_URL_RO, DATABASE_URL_RW

_ro = ConnectionPool(DATABASE_URL_RO, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row})
_rw = ConnectionPool(DATABASE_URL_RW, min_size=1, max_size=4, open=False, kwargs={"row_factory": dict_row})


def open_pools() -> None:
    _ro.open(wait=True, timeout=10)
    _rw.open(wait=True, timeout=10)


def close_pools() -> None:
    _ro.close()
    _rw.close()


@contextmanager
def ro():
    with _ro.connection() as conn:
        yield conn


@contextmanager
def rw():
    with _rw.connection() as conn:
        yield conn


def fetch_all(sql: str, params: dict | None = None) -> list[dict]:
    with ro() as conn:
        return conn.execute(sql, params or {}).fetchall()


def fetch_one(sql: str, params: dict | None = None) -> dict | None:
    with ro() as conn:
        return conn.execute(sql, params or {}).fetchone()
