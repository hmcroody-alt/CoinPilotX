"""Canonical, permanent PulseSoc account identity.

`pulse_id` belongs to the account row and is never derived from mutable profile
fields. Legacy public-player identifiers remain resolvable during migration but
are not used to allocate new identities.
"""

from __future__ import annotations

import re


PULSE_ID_RE = re.compile(r"^PLS-[A-Z0-9]+(?:-[A-Z0-9]+)*$")


def canonical_pulse_id(user_id: int) -> str:
    value = int(user_id or 0)
    if value <= 0:
        raise ValueError("A positive account id is required")
    return f"PLS-{value:06d}"


def normalize_pulse_id(value: object) -> str:
    normalized = str(value or "").strip().upper().lstrip("@")
    return normalized if PULSE_ID_RE.fullmatch(normalized) else ""


def _columns(cur, table: str, *, is_postgres: bool = False) -> set[str]:
    if is_postgres:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        return {str(row[0]) for row in cur.fetchall()}
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def ensure_schema(cur, *, is_postgres: bool = False) -> int:
    columns = _columns(cur, "users", is_postgres=is_postgres)
    if "pulse_id" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN pulse_id TEXT")
    cur.execute("SELECT user_id, pulse_id FROM users ORDER BY user_id ASC")
    rows = [dict(row) for row in cur.fetchall()]
    used: set[str] = set()
    changed = 0
    for row in rows:
        user_id = int(row.get("user_id") or 0)
        current = normalize_pulse_id(row.get("pulse_id"))
        candidate = current if current and current not in used else canonical_pulse_id(user_id)
        counter = 1
        base = candidate
        while candidate in used:
            counter += 1
            candidate = f"{base}-{counter}"
        used.add(candidate)
        if candidate != row.get("pulse_id"):
            cur.execute("UPDATE users SET pulse_id=? WHERE user_id=?", (candidate, user_id))
            changed += 1
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_pulse_id ON users(pulse_id) WHERE pulse_id IS NOT NULL")
    return changed


def ensure_user_pulse_id(cur, user_id: int) -> str:
    user_id = int(user_id or 0)
    cur.execute("SELECT pulse_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
    row = cur.fetchone()
    current = normalize_pulse_id(dict(row or {}).get("pulse_id"))
    if current:
        return current
    candidate = canonical_pulse_id(user_id)
    cur.execute("SELECT user_id FROM users WHERE pulse_id=? AND user_id!=? LIMIT 1", (candidate, user_id))
    counter = 1
    base = candidate
    while cur.fetchone():
        counter += 1
        candidate = f"{base}-{counter}"
        cur.execute("SELECT user_id FROM users WHERE pulse_id=? AND user_id!=? LIMIT 1", (candidate, user_id))
    cur.execute("UPDATE users SET pulse_id=? WHERE user_id=?", (candidate, user_id))
    return candidate


def resolve_user_id(cur, value: object) -> int | None:
    pulse_id = normalize_pulse_id(value)
    if not pulse_id:
        return None
    cur.execute("SELECT user_id FROM users WHERE upper(pulse_id)=? LIMIT 1", (pulse_id,))
    row = cur.fetchone()
    return int(dict(row).get("user_id") or 0) if row else None
