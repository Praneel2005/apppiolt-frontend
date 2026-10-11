"""Who am I, and which roles exist (for the role switcher and for hiding what a role cannot use)."""
from __future__ import annotations

from fastapi import APIRouter

from backend import access

router = APIRouter(tags=["access"])


@router.get("/api/roles")
def roles():
    return {"default": access.default_role(), "roles": access.list_roles()}


@router.get("/api/me")
def me():
    return access.public_role(access.role_id())
