"""Two connection pools, two database roles.

    ro()  app_ro: read-only, 5 s statement timeout. Every read endpoint and the metric engine.
    rw()  app_rw: can change ops_* tables only (sql/30_roles_and_grants.sql). Write endpoints only.

Pools are created by open_pools() (app startup) and may be created again after close_pools(), so several
app instances can start and stop in one process (tests). If nobody opened them, the first use does.
"""
from __future__ import annotations

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.config import DATABASE_URL_RO, DATABASE_URL_RW

_pools: dict[str, ConnectionPool] = {}


def _make(url: str, max_size: int) -> ConnectionPool:
    pool = ConnectionPool(url, min_size=1, max_size=max_size, open=False, kwargs={"row_factory": dict_row})
    pool.open(wait=True, timeout=10)
    return pool


def open_pools() -> None:
    close_pools()
    _pools["ro"] = _make(DATABASE_URL_RO, 8)
    _pools["rw"] = _make(DATABASE_URL_RW, 4)


def close_pools() -> None:
    for name in list(_pools):
        _pools.pop(name).close()


def _pool(name: str) -> ConnectionPool:
    if name not in _pools:
        _pools[name] = _make(DATABASE_URL_RO if name == "ro" else DATABASE_URL_RW, 8 if name == "ro" else 4)
    return _pools[name]


@contextmanager
def ro():
    with _pool("ro").connection() as conn:
        yield conn


@contextmanager
def rw():
    with _pool("rw").connection() as conn:
        yield conn


def fetch_all(sql: str, params: dict | None = None) -> list[dict]:
    with ro() as conn:
        return conn.execute(sql, params or {}).fetchall()


def fetch_one(sql: str, params: dict | None = None) -> dict | None:
    with ro() as conn:
        return conn.execute(sql, params or {}).fetchone()
