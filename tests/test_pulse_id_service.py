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
