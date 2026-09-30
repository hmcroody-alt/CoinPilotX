"""Deleting an account has to remove the ways the platform can reach the person.

`permanently_delete_account` blanks a fixed dictionary of `users` columns and
deletes from a fixed tuple of tables. Both lists were correct when they were
written and then fell behind the schema, because there is no migration framework
here -- a column arrives via `add_columns_if_missing` and a table via a
`CREATE TABLE IF NOT EXISTS` in whichever service wanted it, and neither step has
any reason to visit this routine. Two consequences, and the second is worse:

* `users.recovery_email` and `users.recovery_phone` survived. A deletion that
  blanks `email` and `phone` while leaving a working recovery email and recovery
  phone has not removed the member's contact details.
* Three of the four push registries survived. `pulse_notification_devices` keeps
  the entire web-push subscription in `subscription_json`, and its unsubscribe
  path only sets `active=0` -- so to every reader a leftover row is a live,
  addressable device belonging to an account that no longer exists.

So neither half of this file names the things it checks. Both derive the
expectation from the schema as it actually is at run time:

* every `users` column whose name is drawn from the contact/identity vocabulary
  is seeded with a sentinel, and no sentinel may survive;
* every table that looks like a per-user push registry is seeded with a row, and
  no row may survive.

A test listing `recovery_email` would have passed the day before that column was
added and gone on passing after, which is precisely how this defect got in. A
future `backup_email`, or a fifth registry, fails here instead.

What this file deliberately does *not* claim: that deletion empties the whole
row. `users` also carries billing, subscription and moderation history, and
whether a deleted account may retain those is a retention question for counsel,
not something to settle by writing an assertion. This gate covers contact and
identity only.

Run: python3 -m pytest tests/test_account_deletion_removes_contact_data.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="account_deletion_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PASSWORD = "delete-me-correct-horse"

#: Names and name-parts that mark a value as a way of reaching or naming the
#: person. Matched against the live `users` columns, so the gate grows with the
#: vocabulary instead of with a hand-kept list.
CONTACT_VOCABULARY = (
    "username",
    "display_name",
    "full_name",
    "email",
    "phone",
    "recovery_",
    "telegram_",
    "date_of_birth",
    "bio",
    "avatar",
    "banner",
    "cover",
    "social_links",
    "expertise_tags",
    "roast_call_sign",
)

#: Matched by the vocabulary but holding a *setting* rather than the datum. A
#: filter name is not the picture and a crop position is not the banner, so
#: demanding they be cleared would assert something this routine never meant.
NOT_THE_DATUM = {"avatar_filter", "cover_filter", "cover_position"}

#: Suffixes that mark a flag or a timestamp *about* a contact channel rather than
#: the channel itself. `phone_verified` is not a phone number; clearing it is a
#: different question from removing the number.
NOT_A_VALUE_SUFFIXES = ("_at", "_verified", "_opt_in")


@pytest.fixture(scope="module", autouse=True)
def schema():
    bot.init_db()


def _connect():
    conn = bot.db()
    return conn, conn.cursor()


def contact_columns(cur) -> list[str]:
    """Which live `users` columns carry a contact detail or an identity.

    Matched on whole `_`-separated parts, or on the column starting with a
    vocabulary entry. Not on substrings: `hidden_from_discovery` contains
    "cover", and a gate that demanded a discovery flag be cleared would be
    asserting something nobody decided.
    """

    found = []
    for column in bot.table_columns(cur, "users"):
        if column in NOT_THE_DATUM:
            continue
        if column.endswith(NOT_A_VALUE_SUFFIXES):
            continue
        parts = set(column.split("_"))
        for entry in CONTACT_VOCABULARY:
            token = entry.rstrip("_")
            if token in parts or column == entry or column.startswith(entry):
                found.append(column)
                break
    return sorted(found)


def push_registries(cur) -> list[tuple[str, str]]:
    """Tables that register a device against a user, and the token column each uses.

    Identified by shape rather than by name. `endpoint` is an overloaded word --
    diagnostics tables use it for an HTTP route -- so a candidate only counts if
    it also carries device or subscription columns, which a request log does not.
    """

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = [row[0] for row in cur.fetchall()]

    registries = []
    for table in names:
        columns = set(bot.table_columns(cur, table))
        if "user_id" not in columns:
            continue
        token_columns = [c for c in ("push_token", "endpoint") if c in columns]
        if not token_columns:
            continue
        # A registry describes a device. A trace that happens to name an endpoint
        # does not.
        if not ({"device_id", "device_type", "platform", "subscription_json", "p256dh"} & columns):
            continue
        registries.append((table, token_columns[0]))
    return registries


@pytest.fixture
def member():
    """A member with a sentinel in every contact column and a row in every registry.

    Seeded by writing to the schema directly rather than through the routes that
    normally populate these, because the point is coverage of the columns that
    exist, not of the paths that happen to be wired today.
    """

    conn, cur = _connect()
    cur.execute(
        "INSERT INTO users (username, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
        ("del-target", "del-target@example.com", "Delete Target", generate_password_hash(PASSWORD)),
    )
    user_id = cur.lastrowid

    sentinels = {}
    for column in contact_columns(cur):
        if column == "password_hash":
            continue
        sentinel = f"SENTINEL-{column}-{user_id}"
        try:
            cur.execute(f"UPDATE users SET {column}=? WHERE user_id=?", (sentinel, user_id))
        except Exception:  # pragma: no cover - a typed column that rejects text
            continue
        sentinels[column] = sentinel

    seeded_registries = []
    for table, token_column in push_registries(cur):
        try:
            cur.execute(
                f"INSERT INTO {table} (user_id, {token_column}) VALUES (?, ?)",
                (user_id, f"token-{table}-{user_id}"),
            )
        except Exception:  # pragma: no cover - a NOT NULL column we did not fill
            continue
        seeded_registries.append(table)

    conn.commit()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    user = dict(zip([d[0] for d in cur.description], bot.db_service.row_values(row)))
    conn.close()

    # A fixture that seeded nothing would let every assertion below pass without
    # exercising anything.
    assert sentinels, "no contact columns were seeded"
    assert seeded_registries, "no push registries were seeded"
    return user, user_id, sentinels, seeded_registries


def _delete(user):
    ok, error = bot.permanently_delete_account(user, PASSWORD)
    assert ok is True, f"deletion refused: {error}"


def _users_row(user_id) -> dict:
    conn, cur = _connect()
    try:
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        assert row is not None, "the row is gone entirely, which this routine does not do"
        return dict(zip([d[0] for d in cur.description], bot.db_service.row_values(row)))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# D-P3: no contact detail survives
# ---------------------------------------------------------------------------


def test_no_contact_column_still_holds_its_value(member):
    """Every contact column, checked by value rather than by name.

    Asserting the sentinel is gone rather than that the column equals "" lets
    `username` pass on being replaced with a deleted handle, while still failing
    a column that was simply never cleared.
    """

    user, user_id, sentinels, _registries = member
    _delete(user)
    after = _users_row(user_id)

    survivors = {
        column: after.get(column)
        for column, sentinel in sentinels.items()
        if after.get(column) == sentinel
    }
    assert survivors == {}, (
        f"deletion left contact data in {sorted(survivors)}; every column matching the "
        "contact vocabulary must be cleared or replaced"
    )


def test_the_two_recovery_channels_are_cleared(member):
    """The defect, named explicitly.

    The sweep above already covers these. This test exists so that a future
    narrowing of `CONTACT_VOCABULARY` -- which would silently shrink that sweep
    to nothing -- still fails on the case that was actually reported.
    """

    user, user_id, _sentinels, _registries = member
    _delete(user)
    after = _users_row(user_id)
    assert not after.get("recovery_email")
    assert not after.get("recovery_phone")


def test_the_password_no_longer_authenticates(member):
    """Deletion has to end the credential, not only the profile."""

    user, user_id, _sentinels, _registries = member
    _delete(user)
    assert not _users_row(user_id).get("password_hash")


# ---------------------------------------------------------------------------
# D-P4: no push registry survives
# ---------------------------------------------------------------------------


def test_no_push_registry_keeps_a_row_for_the_deleted_account(member):
    """Every registry the schema has, not the one the routine remembered.

    Derived from table shape, so a fifth registry added by a future service is
    covered the day it appears -- which is the failure mode that produced this
    defect in the first place.
    """

    user, user_id, _sentinels, seeded = member
    _delete(user)

    conn, cur = _connect()
    try:
        remaining = {}
        for table in seeded:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (user_id,))
            count = int(bot.db_service.row_values(cur.fetchone())[0] or 0)
            if count:
                remaining[table] = count
    finally:
        conn.close()

    assert remaining == {}, (
        f"deletion left push registrations in {remaining}; a row here is an addressable "
        "device belonging to an account that no longer exists"
    )


def test_the_web_push_subscription_blob_is_gone(member):
    """`pulse_notification_devices` specifically, because of what it stores.

    The other registries keep a token. This one keeps the whole subscription in
    `subscription_json`, and nothing in the row marks it dead -- `active`
    defaults to 1 and the unsubscribe path is the only thing that ever sets it
    to 0. Named here rather than left to the sweep because the sweep would still
    pass if the shape heuristic stopped matching this table.
    """

    user, user_id, _sentinels, _registries = member
    _delete(user)

    conn, cur = _connect()
    try:
        cur.execute("SELECT COUNT(*) FROM pulse_notification_devices WHERE user_id=?", (user_id,))
        assert int(bot.db_service.row_values(cur.fetchone())[0] or 0) == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


def test_the_sweep_actually_covers_the_columns_that_regressed(member):
    """A vocabulary that matched nothing would make every assertion here vacuous."""

    _user, _user_id, sentinels, registries = member
    for column in ("recovery_email", "recovery_phone", "date_of_birth", "email", "phone"):
        assert column in sentinels, f"{column} is not being seeded, so nothing above checks it"
    assert len(registries) >= 4, f"only {len(registries)} push registries detected: {registries}"
