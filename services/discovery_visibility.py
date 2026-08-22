"""Canonical discovery-visibility predicate for user-facing surfaces.

App Review item 4: QA/test accounts must never surface in discovery
(creator search, suggested people, content search). Instead of scattering
ad-hoc ``LIKE 'qa%'`` filters, every discovery query appends the single
predicate built here.

Hidden when either:
  - ``users.hidden_from_discovery = 1`` (set by scripts/qa_account_classification.py
    or admin tooling), or
  - ``users.account_status`` is a non-discoverable status. Real statuses used in
    code: 'active', 'restricted', 'suspended', 'deleted' (bot.owner_update_user_status)
    plus legacy 'disabled_qa' rows present in production data. 'banned' and
    'disabled' are included defensively for legacy rows.

The fragment is safe for SQLite and PostgreSQL, uses no bind parameters, and is
NULL-tolerant (missing/NULL columns count as visible/active).
"""

from __future__ import annotations

# Account statuses that must never appear in discovery surfaces.
HIDDEN_ACCOUNT_STATUSES: tuple[str, ...] = (
    "deleted",
    "suspended",
    "banned",
    "disabled",
    "disabled_qa",
)

#: The ``users`` columns the predicate reads, with the DDL needed to add one
#: that is missing. Callers on hot paths (the feed) use this to self-heal the
#: schema before querying, because a missing column is a hard SQL error rather
#: than a degraded result. It lives here, next to the predicate that depends on
#: it, so the two cannot drift apart — adding a third column to the predicate
#: without adding it here would reintroduce exactly that outage.
REQUIRED_USER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("hidden_from_discovery", "INTEGER DEFAULT 0"),
    ("account_status", "TEXT DEFAULT 'active'"),
)

_IDENTIFIER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _safe_alias(alias: str) -> str:
    alias = str(alias or "").strip()
    if not alias or not set(alias) <= _IDENTIFIER_CHARS or alias[0].isdigit():
        raise ValueError(f"Unsafe SQL alias for discovery predicate: {alias!r}")
    return alias


def discovery_visible_sql(alias: str = "u") -> str:
    """Return a SQL boolean fragment: True when the aliased users row may
    appear in discovery surfaces.

    Example: ``discovery_visible_sql("u")`` ->
    ``(COALESCE(u.hidden_from_discovery,0)=0 AND COALESCE(u.account_status,'active')
    NOT IN ('deleted','suspended','banned','disabled','disabled_qa'))``
    """
    alias = _safe_alias(alias)
    statuses = ", ".join(f"'{status}'" for status in HIDDEN_ACCOUNT_STATUSES)
    return (
        f"(COALESCE({alias}.hidden_from_discovery, 0) = 0 "
        f"AND COALESCE({alias}.account_status, 'active') NOT IN ({statuses}))"
    )
