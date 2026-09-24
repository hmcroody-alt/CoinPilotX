#!/usr/bin/env python3
"""Refuse to run fixture-style audits against a non-local database.

These audits INSERT synthetic rows into real application tables and DELETE them
afterwards. Run against production they would destroy real accounts, so they are
gated on the resolved connection target rather than on the caller's intent.
"""

from __future__ import annotations

from urllib.parse import urlparse

LOCAL_POSTGRES_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})
POSTGRES_SCHEMES = frozenset({"postgresql", "postgres"})


def local_database_refusal(engine_url: str) -> str | None:
    """Return why ``engine_url`` is not local, or ``None`` when it is."""
    url = (engine_url or "").strip()
    if not url:
        return "the database target could not be resolved"
    scheme = url.split("://", 1)[0].split("+", 1)[0].strip().lower()
    if scheme == "sqlite":
        return None
    if scheme not in POSTGRES_SCHEMES:
        return f"the target uses an unrecognised {scheme!r} driver"
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return "the PostgreSQL target's host could not be parsed"
    if host in LOCAL_POSTGRES_HOSTS:
        return None
    return f"the target is a remote PostgreSQL host ({host})"


def require_local_database(script_name: str) -> None:
    """Exit non-zero unless the resolved database is local."""
    from services import db as db_service

    reason = local_database_refusal(db_service.ENGINE_URL)
    if reason is None:
        return
    raise SystemExit(
        f"{script_name}: refusing to run.\n"
        "This audit creates and then deletes rows in real application tables "
        "such as users and admin_users, so it must only ever run against a "
        "local database.\n"
        f"Refused because {reason}.\n"
        f"Resolved target: {db_service.masked_database_url()}\n"
        "Unset DATABASE_URL, or point it at a local sqlite file or a localhost "
        "PostgreSQL, and re-run."
    )
