"""The DDL must be committed before the guard is allowed to remember it.

Every other test in this package runs on SQLite, which autocommits DDL — so
every other test in this package would pass whether or not ``ensure_schema``
commits. That is the whole reason this file exists and why it drives a fake
driver rather than a real database: the defect is invisible to the one engine
the suite can run against, and production is the other engine.

The failure has two shapes and neither raises anything:

* ``@run_once_per_process`` caches success. PostgreSQL DDL is transactional, so
  if the request that first created the tables later rolls back, the tables go
  with it while the cache does not. The worker then answers every discovery
  request from tables that do not exist, and the engine turns ``UndefinedTable``
  into "no placements" — a shop that is permanently, quietly shut.
* ``CREATE ... IF NOT EXISTS`` holds a ShareLock until its transaction ends.
  Ending that transaction immediately, rather than after a full ranking pass,
  is what keeps it from overlapping the RowExclusiveLock the next request takes
  to write its impression row.
"""

import pytest

from services import db as db_module
from services.commerce_discovery import schema


class RecordingCursor:
    """Just enough cursor to watch the order of operations.

    Deliberately has no ``.connection``: that is exactly what
    ``services.db.CompatCursor`` looks like on PostgreSQL, and reaching for it
    is the mistake this shape exists to make impossible.
    """

    def __init__(self, journal, *, fail_on=None):
        self.journal = journal
        self._fail_on = fail_on

    def execute(self, statement, params=None):
        if self._fail_on is not None and self._fail_on in statement:
            raise RuntimeError("DDL refused")
        self.journal.append(("execute", statement))


class RecordingConnection:
    """What ``ensure_schema`` is actually handed — it needs the commit."""

    def __init__(self, journal, *, fail_on=None):
        self.journal = journal
        self._cursor = RecordingCursor(journal, fail_on=fail_on)

    def cursor(self):
        return self._cursor

    def commit(self):
        self.journal.append(("commit", None))


@pytest.fixture(autouse=True)
def forget_the_cache():
    """The guard is process-wide, so every test here must start cold."""
    schema.ensure_schema.reset()
    yield
    schema.ensure_schema.reset()


class TestTheDDLIsCommitted:
    def test_commits_before_returning(self):
        journal = []
        assert schema.ensure_schema(RecordingConnection(journal)) is True
        assert ("commit", None) in journal, (
            "the guard is about to cache this success; an uncommitted CREATE on "
            "PostgreSQL does not survive the request that made it"
        )

    def test_commits_after_the_last_statement_and_not_before(self):
        journal = []
        schema.ensure_schema(RecordingConnection(journal))
        kinds = [entry[0] for entry in journal]
        assert kinds.count("commit") == 1
        assert kinds[-1] == "commit"
        assert kinds[0] == "execute"

    def test_every_ddl_statement_runs(self):
        journal = []
        schema.ensure_schema(RecordingConnection(journal))
        executed = [stmt for kind, stmt in journal if kind == "execute"]
        # Containment rather than a count. This used to assert
        # `len(executed) == len(schema._DDL)`, which was the same thing only while
        # `_DDL` was the sole source of statements; the additive-column path made
        # it a false negative. Naming each declaration means a statement that
        # silently stopped running is still caught, which is what the count was
        # standing in for.
        for statement in schema._DDL:
            assert statement in executed, f"declared but never executed: {statement!r}"

    def test_every_additive_column_is_attempted(self):
        """A column that exists only in the CREATE body never reaches production.

        The tables predate the column, and `CREATE TABLE IF NOT EXISTS` is a no-op
        against a database that already has them — so a column added to the
        declaration alone applies on a fresh database and silently does not apply
        to the one serving traffic. `_ADDITIVE_COLUMNS` is the path that reaches
        it, and an entry that never turns into an ALTER is that defect exactly.
        """
        journal = []
        schema.ensure_schema(RecordingConnection(journal))
        executed = [stmt for kind, stmt in journal if kind == "execute"]
        assert schema._ADDITIVE_COLUMNS, (
            "an empty tuple would make this file's additive coverage vacuous; "
            "delete the path deliberately rather than by emptying it"
        )
        for table, column, definition in schema._ADDITIVE_COLUMNS:
            expected = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            assert expected in executed, f"never attempted: {expected!r}"

    def test_nothing_runs_that_is_not_declared(self):
        """The other half of containment, and the reason it is safe to stop counting."""
        journal = []
        schema.ensure_schema(RecordingConnection(journal))
        declared = set(schema._DDL) | {
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            for table, column, definition in schema._ADDITIVE_COLUMNS
        }
        for _, statement in journal:
            if statement is None:
                continue
            assert statement in declared, f"undeclared statement executed: {statement!r}"

    def test_the_alters_run_after_the_creates(self):
        """Ordering is load-bearing, not tidiness.

        An ALTER against a table this same pass is about to create fails on a
        fresh database; on PostgreSQL that failure aborts the transaction and
        takes the CREATEs down with it, so a brand-new deployment would come up
        with no commerce tables at all.
        """
        journal = []
        schema.ensure_schema(RecordingConnection(journal))
        executed = [stmt for kind, stmt in journal if kind == "execute"]
        last_create = max(
            index for index, stmt in enumerate(executed) if stmt in set(schema._DDL)
        )
        for table, column, definition in schema._ADDITIVE_COLUMNS:
            alter = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            assert executed.index(alter) > last_create, (
                f"{alter!r} runs before the last CREATE; on a fresh PostgreSQL "
                "database that aborts the transaction the CREATEs are in"
            )


class TestTheAdditiveColumnsSurviveTheOtherEngine:
    """The half of the additive path that SQLite cannot show you.

    `_add_columns` has no ``IS_POSTGRES`` branch and no ``information_schema``
    probe, which looks like an omission and is not: `services.db._translate_sql`
    rewrites every ``ADD COLUMN`` into ``ADD COLUMN IF NOT EXISTS`` before the
    statement reaches PostgreSQL, so the statement is already idempotent there.
    SQLite has no such syntax and raises ``duplicate column name`` instead, which
    is the case `_DUPLICATE_COLUMN_MARKERS` catches.

    Both halves are load-bearing and each is invisible on the other engine, so
    both are pinned here. If the translation is ever narrowed, a second
    deployment's ALTER stops being a no-op and starts being a hard error that
    aborts the transaction the CREATEs are in — a brand-new worker comes up with
    no commerce tables at all, and `serve` reports that as "no placements".
    """

    def test_postgres_gets_if_not_exists_injected(self, monkeypatch):
        monkeypatch.setattr(db_module, "IS_POSTGRES", True)
        for table, column, definition in schema._ADDITIVE_COLUMNS:
            statement = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            translated = db_module._translate_alter_table(statement)
            assert "ADD COLUMN IF NOT EXISTS" in translated.upper(), (
                f"{statement!r} reaches PostgreSQL without IF NOT EXISTS, so the "
                "second deployment aborts the schema transaction"
            )

    def test_the_translation_is_not_applied_twice(self, monkeypatch):
        """Idempotent translation, so a statement that already says IF NOT EXISTS
        does not become ``IF NOT EXISTS IF NOT EXISTS``."""
        monkeypatch.setattr(db_module, "IS_POSTGRES", True)
        once = db_module._translate_alter_table(
            "ALTER TABLE t ADD COLUMN c TEXT"
        )
        assert db_module._translate_alter_table(once) == once

    def test_sqlite_is_left_alone_so_the_marker_path_is_the_live_one(self, monkeypatch):
        monkeypatch.setattr(db_module, "IS_POSTGRES", False)
        statement = "ALTER TABLE t ADD COLUMN c TEXT"
        assert db_module._translate_alter_table(statement) == statement

    def test_the_duplicate_marker_matches_what_sqlite_actually_says(self):
        """Pinned against a real sqlite3, not against a remembered string.

        The marker is a substring match on an error message. If a future SQLite
        reworded it, `_add_columns` would re-raise on every deployment after the
        first — and the wording is not something this repo controls.
        """
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE t (c TEXT)")
            with pytest.raises(sqlite3.OperationalError) as caught:
                conn.execute("ALTER TABLE t ADD COLUMN c TEXT")
        finally:
            conn.close()
        assert any(
            marker in str(caught.value).lower()
            for marker in schema._DUPLICATE_COLUMN_MARKERS
        ), (
            f"sqlite says {str(caught.value)!r}, which no marker in "
            f"{schema._DUPLICATE_COLUMN_MARKERS} matches"
        )


class TestAFailedRunIsNotRemembered:
    def test_returns_false_rather_than_raising(self):
        journal = []
        conn = RecordingConnection(journal, fail_on="commerce_discovery_impression_events")
        assert schema.ensure_schema(conn) is False

    def test_does_not_commit_a_partial_schema(self):
        journal = []
        conn = RecordingConnection(journal, fail_on="commerce_discovery_impression_events")
        schema.ensure_schema(conn)
        assert ("commit", None) not in journal

    def test_the_next_request_tries_again(self):
        """A transient DDL failure must not disable the worker permanently."""
        failing = RecordingConnection([], fail_on="commerce_discovery_impression_events")
        assert schema.ensure_schema(failing) is False

        journal = []
        assert schema.ensure_schema(RecordingConnection(journal)) is True
        assert ("commit", None) in journal


class TestTheConnectionIsPassedInRatherThanDerived:
    def test_the_fake_cursor_has_no_connection_attribute(self):
        """Pins the shape of the fake, which is the whole test.

        ``ensure_schema`` could get its commit from ``cur.connection`` instead of
        taking the connection, and on SQLite that works. ``CompatCursor`` — every
        cursor once ``DATABASE_URL`` is PostgreSQL — does not have the attribute,
        so it would raise, be swallowed as a schema failure, and shut discovery
        off on the one engine that matters. Adding ``.connection`` here to make a
        future refactor pass would delete that signal.
        """
        assert not hasattr(RecordingCursor([]), "connection")

    def test_the_ddl_runs_on_the_cursor_the_connection_handed_out(self):
        conn = RecordingConnection([])
        schema.ensure_schema(conn)
        assert conn._cursor.journal is conn.journal


class TestTheGuardStillGuards:
    def test_a_second_call_touches_the_database_not_at_all(self):
        assert schema.ensure_schema(RecordingConnection([])) is True

        journal = []
        assert schema.ensure_schema(RecordingConnection(journal)) is True
        assert journal == [], (
            "the point of the guard is that the DDL does not run per request; "
            "count the calls rather than trusting that it looks cached"
        )
