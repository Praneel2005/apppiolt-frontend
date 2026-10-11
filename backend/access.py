"""Who is asking, and what they may see and do.

Roles live in data/olist/roles.json (data, not code): which modules (groups of pages) a role can open, which write APIs it may call, which
read APIs it may use, and a data SCOPE (for example customer_region = South) that is added to every metric query the role makes.

The role of the current request is carried in a context variable set by RoleMiddleware from the X-Role header, so it follows the request into
worker threads and in-process API calls. This is role CONTEXT for a demonstration, not authentication: there are no passwords or tokens, and
an unknown or missing role means the default role. What it does enforce, centrally and server-side:
  * pages and their widgets      (validator, widget data route)
  * writes                       (backend/writes.py run_write: preview, apply and undo all go through it)
  * metric data scope            (both places that build metric SQL); a scope that cannot be applied to a dataset REFUSES access
                                 instead of silently widening it
Not enforced yet: the direct operational read routes (/api/orders, ...) when called outside the app's own pages.
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, Iterable

from backend.common import ApiError
from backend.config import ROOT

ROLES_FILE = ROOT / "data" / "olist" / "roles.json"
current_role: ContextVar[str] = ContextVar("current_role", default="")


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        return json.loads(ROLES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"default_role": "admin", "roles": {"admin": {"title": "Administrator", "modules": "*", "writes": "*", "reads": "*", "scope": {}}}}


def default_role() -> str:
    return _load().get("default_role", "admin")


def role_id() -> str:
    v = current_role.get()
    return v if v in _load()["roles"] else default_role()


def role(rid: str | None = None) -> dict[str, Any]:
    return _load()["roles"][rid or role_id()]


def is_restricted(rid: str | None = None) -> bool:
    r = role(rid)
    return not (r.get("modules") == "*" and r.get("writes") == "*" and r.get("reads", "*") == "*" and not r.get("scope"))


def _allows(rule: Any, key: str, prefix: bool = False) -> bool:
    if rule == "*":
        return True
    if not rule:
        return False
    return any(key.startswith(x) if prefix else key == x for x in rule)


def module_of(page) -> str:
    return page.directory.split("/")[0]


def page_allowed(page, rid: str | None = None) -> bool:
    return _allows(role(rid).get("modules", "*"), module_of(page))


def module_allowed(module_id: str, rid: str | None = None) -> bool:
    return _allows(role(rid).get("modules", "*"), module_id)


def write_allowed(api_id: str, rid: str | None = None) -> bool:
    return _allows(role(rid).get("writes", "*"), api_id)


def read_allowed(api_id: str, rid: str | None = None) -> bool:
    return _allows(role(rid).get("reads", "*"), api_id, prefix=True)


def scope(rid: str | None = None) -> dict[str, list[str]]:
    return {k: list(v) for k, v in (role(rid).get("scope") or {}).items()}


def scope_filters(dataset_fields: Iterable[str]) -> dict[str, list[str]]:
    """The scope to add to a query on a dataset with these fields. If the dataset lacks a scoped field the scope cannot be applied, so
    access is refused: a restricted role must never see unrestricted numbers."""
    sc = scope()
    if not sc:
        return {}
    names = set(dataset_fields)
    missing = [k for k in sc if k not in names]
    if missing:
        raise ApiError(403, "scope_not_applicable",
                       f"Your role ({role()['title']}) is limited by {', '.join(missing)}, which this report does not have, so it cannot be shown.")
    return sc


def describe_scope(rid: str | None = None) -> str:
    return "; ".join(f"{k.replace('_', ' ')} = {', '.join(v)}" for k, v in scope(rid).items())


def require_any_write() -> None:
    if not role().get("writes"):
        raise ApiError(403, "forbidden_write", f"Your role ({role()['title']}) cannot make or undo changes.")


def require_page(page) -> None:
    if not page_allowed(page):
        raise ApiError(403, "forbidden_page", f"Your role ({role()['title']}) cannot open '{page.title}'.")


def require_write(api_id: str) -> None:
    if not write_allowed(api_id):
        raise ApiError(403, "forbidden_write", f"Your role ({role()['title']}) cannot make changes ('{api_id}').")


def public_role(rid: str) -> dict[str, Any]:
    r = role(rid)
    return {"id": rid, "title": r["title"], "description": r.get("description", ""), "modules": r.get("modules", "*"),
            "writes": r.get("writes", "*"), "reads": r.get("reads", "*"), "scope": r.get("scope") or {}}


def list_roles() -> list[dict[str, Any]]:
    return [public_role(r) for r in _load()["roles"]]


class RoleMiddleware:
    """Pure ASGI middleware: set the request's role from the X-Role header. A request with no header keeps the role already in force, so
    the app's own in-process calls (the agent calling the metric routes) run as the user who asked."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope_, receive, send):
        if scope_["type"] not in ("http", "websocket"):
            return await self.app(scope_, receive, send)
        raw = dict(scope_.get("headers") or []).get(b"x-role", b"").decode().strip()
        token = current_role.set(raw) if raw else None
        try:
            return await self.app(scope_, receive, send)
        finally:
            if token is not None:
                current_role.reset(token)
