"""The one place the Private Office reads a PulseSoc account.

The Office is deliberately sealed: its facts, nodes and records are the
member's own, and nothing inside it looks anything up about anybody. That rule
has exactly one edge, and this module is it — when a member says "this contact
is @dana", something has to confirm that ``@dana`` names a real account and
fetch the face to show beside the name, or the member is linking blind.

What this module is allowed to do
---------------------------------
* Project **four public columns** of an account the member named: id, handle,
  display name, avatar. The whitelist is the point. ``SELECT *`` here would
  pull a password hash and an email address into a Private Office response,
  and the day somebody adds a column is the day it leaks.
* Answer about an account **the caller already named**. There is no search, no
  listing, and no "people you may know". A handle that names nobody gets
  ``None``, which is also what a handle with no account gets.

What it must never become
-------------------------
An enrichment layer. Nothing here reads a contact list, a social graph, a data
broker or a directory; see §34 of the product rules. It answers one question —
"whose account is this?" — about an identifier a human typed.

The direction is one-way. Reading an account never writes to it and never
notifies it: being recorded in someone's Private Office is not an event on the
person's account, and they are not told.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger("private_office.accounts")

#: The columns a linked contact may show, and the only ones read.
FIELDS: tuple[str, ...] = ("user_id", "username", "display_name", "avatar_url")

_SELECT = "SELECT user_id, username, display_name, avatar_url FROM users "


def _project(row) -> dict:
    data = dict(row)
    return {
        "user_id": int(data.get("user_id") or 0),
        "username": str(data.get("username") or ""),
        "display_name": str(data.get("display_name") or ""),
        "avatar_url": str(data.get("avatar_url") or ""),
    }


def lookup(cur, *, user_id: object = 0, username: object = "") -> dict | None:
    """One account by id or by handle, or ``None``.

    ``None`` for "no such account", for "that account has no username", and
    for a deployment whose ``users`` table is missing one of the columns. All
    three mean the same thing to every caller here: there is no profile to
    show, so fall back to what the member typed.
    """
    try:
        account = int(user_id or 0)
    except (TypeError, ValueError):
        account = 0
    handle = str(username or "").strip()

    try:
        if account > 0:
            cur.execute(_SELECT + "WHERE user_id = ? LIMIT 1", (account,))
        elif handle:
            cur.execute(
                _SELECT + "WHERE lower(username) = lower(?) "
                "AND COALESCE(username, '') != '' LIMIT 1", (handle,))
        else:
            return None
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_ACCOUNT_LOOKUP_FAILED")
        return None

    row = cur.fetchone()
    if row is None:
        return None
    projected = _project(row)
    return projected if projected["user_id"] > 0 else None


def lookup_many(cur, user_ids) -> dict[int, dict]:
    """``user_id`` → account, for the ids given. Missing ids are simply absent.

    One statement per id rather than an ``IN`` clause: the lists this serves
    are a contact page or a meeting's invitees, both already bounded, and a
    dynamically built ``IN`` is a place for a list of ints to stop being a list
    of ints.
    """
    wanted: list[int] = []
    for value in user_ids or ():
        try:
            account = int(value or 0)
        except (TypeError, ValueError):
            continue
        if account > 0 and account not in wanted:
            wanted.append(account)

    out: dict[int, dict] = {}
    for account in sorted(wanted):
        row = lookup(cur, user_id=account)
        if row:
            out[account] = row
    return out


def display_name_for(account: dict | None, *, fallback: str = "") -> str:
    """What to call someone: their display name, else their handle, else ``fallback``.

    Never an empty string when a fallback is given. A contact card with a blank
    name is the ghost row this whole feature is supposed to stop producing.
    """
    account = account or {}
    return (str(account.get("display_name") or "").strip()
            or str(account.get("username") or "").strip()
            or str(fallback or "").strip())
