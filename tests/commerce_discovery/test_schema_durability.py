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
        assert len(executed) == len(schema._DDL)


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
