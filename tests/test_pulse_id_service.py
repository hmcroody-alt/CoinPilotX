import logging
import sqlite3

from services import pulse_id_service


def database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, pulse_id TEXT)")
    return conn


def test_schema_backfills_stable_unique_pulse_ids():
    conn = database()
    conn.executemany(
        "INSERT INTO users(user_id, username, pulse_id) VALUES (?, ?, ?)",
        [(1, "one", None), (2, "two", "pls-a83k91"), (3, "three", "PLS-A83K91")],
    )
    changed = pulse_id_service.ensure_schema(conn.cursor())
    rows = conn.execute("SELECT user_id, pulse_id FROM users ORDER BY user_id").fetchall()
    assert changed == 3
    assert [row["pulse_id"] for row in rows] == ["PLS-000001", "PLS-A83K91", "PLS-000003"]
    assert len({row["pulse_id"] for row in rows}) == 3


def test_existing_pulse_id_never_changes_and_resolves_case_insensitively():
    conn = database()
    conn.execute("INSERT INTO users(user_id, username, pulse_id) VALUES (8919, 'roodycherie', 'PLS-8919')")
    cur = conn.cursor()
    assert pulse_id_service.ensure_user_pulse_id(cur, 8919) == "PLS-8919"
    assert pulse_id_service.resolve_user_id(cur, "@pls-8919") == 8919


def test_new_account_receives_canonical_pulse_id():
    conn = database()
    conn.execute("INSERT INTO users(user_id, username) VALUES (298311, 'newmember')")
    assert pulse_id_service.ensure_user_pulse_id(conn.cursor(), 298311) == "PLS-298311"
    assert conn.execute("SELECT pulse_id FROM users WHERE user_id=298311").fetchone()["pulse_id"] == "PLS-298311"


def test_a_non_positive_id_is_skipped_without_blocking_the_other_rows():
    """A row the backfill cannot mint an identity for must not abort the pass.

    `ensure_schema` runs inside `bot._init_db_impl`, near the top of ~8,300 lines
    of `CREATE TABLE` that are this repo's only schema mechanism. `ORDER BY
    user_id ASC` puts negative ids first, so a single such row raised on the very
    first iteration and took the rest of the schema with it: a fresh boot
    produced 49 tables instead of 586, and every user was left unbackfilled.

    Two smoke-test fixtures with Telegram-shaped ids (-920871340, -910251359)
    already sit in the local dev database, so this is reachable, not theoretical.
    """
    conn = database()
    conn.executemany(
        "INSERT INTO users(user_id, username, pulse_id) VALUES (?, ?, ?)",
        [(-920871340, "tg_group", None), (1, "one", None), (2, "two", None)],
    )
    changed = pulse_id_service.ensure_schema(conn.cursor())
    rows = dict(conn.execute("SELECT user_id, pulse_id FROM users").fetchall())
    assert changed == 2
    assert rows[1] == "PLS-000001"
    assert rows[2] == "PLS-000002"
    assert rows[-920871340] is None, "no identity is mintable for a non-account row"


def test_a_non_positive_row_never_steals_a_real_account_identity():
    """The reason the fix is "skip" rather than "mint from abs(user_id)".

    `abs(-000123)` is a *well-formed* id, so it would be kept — and because the
    scan is ascending the negative row reaches PLS-000123 first, permanently
    bumping the real account 123 to PLS-000123-2. `pulse_id` is documented as
    permanent, so that is worse than leaving one row NULL.
    """
    conn = database()
    conn.executemany(
        "INSERT INTO users(user_id, username, pulse_id) VALUES (?, ?, ?)",
        [(-123, "tg_group", None), (123, "real", None)],
    )
    pulse_id_service.ensure_schema(conn.cursor())
    assert conn.execute("SELECT pulse_id FROM users WHERE user_id=123").fetchone()[0] == "PLS-000123"


def test_a_non_positive_row_keeps_an_identity_it_already_has():
    """Skipping applies to minting, not to ids already issued.

    Such a row must still reserve its id in the in-run `used` set, or a later
    account could be handed the same one.
    """
    conn = database()
    conn.executemany(
        "INSERT INTO users(user_id, username, pulse_id) VALUES (?, ?, ?)",
        [(-500, "legacy", "PLS-000900"), (900, "real", None)],
    )
    pulse_id_service.ensure_schema(conn.cursor())
    rows = dict(conn.execute("SELECT user_id, pulse_id FROM users").fetchall())
    assert rows[-500] == "PLS-000900"
    assert rows[900] == "PLS-000900-2", "the reserved id must not be handed out twice"


def test_skipping_is_logged_rather_than_silent(caplog):
    conn = database()
    conn.execute("INSERT INTO users(user_id, username) VALUES (-920871340, 'tg_group')")
    with caplog.at_level(logging.WARNING):
        pulse_id_service.ensure_schema(conn.cursor())
    assert "PULSE_ID_BACKFILL_SKIPPED_NON_POSITIVE_ID" in caplog.text
    assert "count=1" in caplog.text


class TupleCursor:
    """Small PostgreSQL-style cursor proving tuple rows remain supported."""

    def __init__(self):
        self.rows = [(1, None), (8919, "PLS-8919")]
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params or ())))

    def fetchall(self):
        if "information_schema.columns" in self.executed[-1][0]:
            return [("user_id",), ("pulse_id",)]
        return list(self.rows)

    def fetchone(self):
        return (8919,)


def test_postgres_tuple_rows_are_supported_during_schema_and_resolution():
    cur = TupleCursor()
    changed = pulse_id_service.ensure_schema(cur, is_postgres=True)
    assert changed == 1
    assert any("UPDATE users SET pulse_id" in sql for sql, _params in cur.executed)
    assert pulse_id_service.resolve_user_id(cur, "pls-8919") == 8919
