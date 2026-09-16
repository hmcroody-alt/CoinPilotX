"""The audit trail must not be able to destroy what it is recording.

Why this file exists
--------------------
``audit.record`` wraps its INSERT in a try/except, logs
``PRIVATE_AUDIT_WRITE_FAILED`` and returns ``False``. The module docstring
called that "best-effort by design", and on SQLite it is. On PostgreSQL it was
the opposite of best-effort.

A failed statement there aborts the whole transaction, so ``services/db.py``
rolls the *connection* back to keep it usable — from inside its own ``except``,
which runs before ``record``'s. By the time ``record`` logged its polite
warning, the caller's uncommitted work was already gone: not the audit row, the
subject of the audit row. ``record`` then returned ``False`` to a caller that
never checks the return value, and the caller went on to build a success
response out of the objects it still held in memory. Confirmed empirically
against a scratch copy of production's PostgreSQL 18.6 schema with
``private_audit_events`` dropped: ``SQL_EXECUTE_ROLLBACK_OK``, then a 404
"Meeting not found" about a meeting inserted a few statements earlier.

``meetings.py`` fixed this for itself in 551a5297 by wrapping its single
``_audit`` chokepoint in a savepoint. This is the same fix made general —
placed at the two statements in ``audit.py`` rather than at the ~70 call sites
across 19 modules that would each have to remember.

Why these tests are shaped the way they are
-------------------------------------------
SQLite does not poison a transaction on a failed statement. Every test in the
``tests/private_office/`` tree passes identically whether or not the savepoint
is there — that blindness is precisely what let the defect ship and survive a
suite this size. So the assertions here are of two kinds, and neither is a
behavioural check against this engine:

* **structural** — the SQL actually issued, read off a recording cursor, and an
  AST rule over the module so the next statement added here inherits the
  guarantee instead of re-opening the hole;
* **simulated** — a cursor that fakes PostgreSQL's abort semantics, which is
  the only way to watch the unwind happen at all.

Follows the pattern established in ``test_private_meeting_persistence.py``.
"""

import ast
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services.private_office import audit  # noqa: E402
from services.private_office import meetings  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402

OWNER = 4101
ACTOR = 4202


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class RecordingCursor:
    """A cursor that remembers the SQL put through it.

    Forwards everything, so the code under test behaves exactly as it would
    otherwise; the recording is how a savepoint — invisible in SQLite's
    results — becomes assertable.
    """

    def __init__(self, inner):
        self._inner = inner
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(str(sql).strip())
        return self._inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class PoisonableCursor:
    """A cursor that can pretend its transaction has been aborted.

    Stands in for PostgreSQL's behaviour, which SQLite has no equivalent of:
    after a failed statement every later one raises until something unwinds.
    ``ROLLBACK TO SAVEPOINT`` is the thing that un-poisons it, which is the
    entire point being tested.
    """

    def __init__(self, *, poison_on=None):
        self.statements: list[str] = []
        self.poisoned = False
        self._poison_on = poison_on

    def execute(self, sql, params=()):
        text = str(sql).strip()
        self.statements.append(text)
        upper = text.upper()
        if upper.startswith("ROLLBACK TO SAVEPOINT"):
            self.poisoned = False
            return self
        if self.poisoned:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until end "
                "of transaction block")
        if self._poison_on and self._poison_on in upper:
            self.poisoned = True
            raise RuntimeError(
                'relation "private_audit_events" does not exist')
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _live_cursor(*, with_audit_table=True):
    """A real SQLite cursor, optionally without the audit table.

    Omitting the table rather than dropping it is deliberate: the write
    boundary guard in ``test_private_write_boundary.py`` rightly refuses any
    module outside the sanctioned writers that issues DDL against a private
    table, and "the table was never there" is the production shape anyway —
    ``private_audit_events`` missing is exactly the case the harness
    reproduced.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if with_audit_table:
        cur.execute(po_schema.AUDIT_TABLE_DDL)
    return conn, cur


@pytest.fixture()
def cur():
    conn, cursor = _live_cursor()
    yield cursor
    conn.close()


def _issued(statements):
    return [s.upper() for s in statements]


def _savepoint_trace(statements):
    """Just the transaction-control statements, in order."""
    out = []
    for raw in _issued(statements):
        for prefix in ("ROLLBACK TO SAVEPOINT ", "RELEASE SAVEPOINT ", "SAVEPOINT "):
            if raw.startswith(prefix):
                out.append((prefix.strip(), raw[len(prefix):].split()[0]))
                break
    return out


# ---------------------------------------------------------------------------
# The behaviour the fix must not have changed
# ---------------------------------------------------------------------------


def test_an_audit_row_still_lands_and_is_reported_as_landed(cur):
    assert audit.record(
        cur, actor_user_id=ACTOR, owner_user_id=OWNER,
        action=audit.ACTION_FACT_READ, object_type="INSURANCE_POLICY",
        object_id="382", purpose="undx_context") is True

    cur.execute(f"SELECT actor_user_id, owner_user_id, action, object_id "
                f"FROM {po_schema.AUDIT_TABLE}")
    row = cur.fetchone()
    assert (row[0], row[1], row[2], row[3]) == (
        ACTOR, OWNER, audit.ACTION_FACT_READ, "382")


def test_a_denial_still_lands(cur):
    assert audit.record_denied(
        cur, actor_user_id=ACTOR, owner_user_id=OWNER,
        object_type="INSURANCE_POLICY", object_id="382") is True

    cur.execute(f"SELECT action, outcome FROM {po_schema.AUDIT_TABLE}")
    assert tuple(cur.fetchone()) == (audit.ACTION_ACCESS_DENIED, audit.OUTCOME_DENIED)


def test_a_failed_write_is_still_swallowed_and_reported_as_not_landed():
    """The promise in the docstring, which the savepoint exists to make true.

    Against a real engine error rather than a simulated one: the audit table is
    simply absent, which is the state the production harness reproduced.
    """
    conn, cursor = _live_cursor(with_audit_table=False)
    try:
        assert audit.record(
            cursor, actor_user_id=ACTOR, owner_user_id=OWNER,
            action=audit.ACTION_FACT_READ) is False
        assert audit.recent_record_activity(cursor, owner_user_id=OWNER) == []
    finally:
        conn.close()


def test_the_activity_read_still_returns_rows(cur):
    audit.record(cur, actor_user_id=OWNER, owner_user_id=OWNER,
                 action=audit.ACTION_RECORD_CREATE, object_type="OBLIGATION",
                 object_id="7")
    activity = audit.recent_record_activity(cur, owner_user_id=OWNER)
    assert [a["record_id"] for a in activity] == ["7"]


# ---------------------------------------------------------------------------
# Structural — the savepoint SQLite cannot show you
# ---------------------------------------------------------------------------


def test_the_audit_write_runs_inside_a_savepoint():
    """The containment, asserted where SQLite cannot demonstrate it.

    On PostgreSQL the savepoint is what stops ``db.py`` rolling the whole
    connection back on behalf of a block that swallows its own error. Here the
    row lands either way, so a behavioural test proves nothing. The statements
    do.
    """
    conn, inner = _live_cursor()
    recorder = RecordingCursor(inner)
    try:
        assert audit.record(
            recorder, actor_user_id=ACTOR, owner_user_id=OWNER,
            action=audit.ACTION_FACT_READ) is True
    finally:
        conn.close()

    trace = _savepoint_trace(recorder.statements)
    assert trace == [("SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
                     ("RELEASE SAVEPOINT", "PRIVATE_AUDIT_WRITE")], trace

    # The INSERT is between them, not before or after.
    issued = _issued(recorder.statements)
    opened = issued.index("SAVEPOINT PRIVATE_AUDIT_WRITE")
    released = issued.index("RELEASE SAVEPOINT PRIVATE_AUDIT_WRITE")
    inserted = [i for i, s in enumerate(issued) if s.startswith("INSERT INTO")]
    assert inserted and all(opened < i < released for i in inserted)


def test_the_activity_read_runs_inside_a_savepoint_and_fetches_before_release():
    """A read aborts a PostgreSQL transaction exactly as thoroughly as a write.

    The ordering half is not decoration. ``RELEASE SAVEPOINT`` is another
    statement on the same cursor, and on psycopg running one discards the
    result set — so fetching after the release would return the rows on SQLite
    and nothing in production.
    """
    conn, inner = _live_cursor()
    recorder = RecordingCursor(inner)
    try:
        audit.record(recorder, actor_user_id=OWNER, owner_user_id=OWNER,
                     action=audit.ACTION_RECORD_CREATE, object_type="OBLIGATION",
                     object_id="7")
        recorder.statements.clear()
        assert audit.recent_record_activity(recorder, owner_user_id=OWNER)
    finally:
        conn.close()

    trace = _savepoint_trace(recorder.statements)
    assert trace == [("SAVEPOINT", "PRIVATE_AUDIT_READ"),
                     ("RELEASE SAVEPOINT", "PRIVATE_AUDIT_READ")], trace

    issued = _issued(recorder.statements)
    assert issued.index("SAVEPOINT PRIVATE_AUDIT_READ") < \
        next(i for i, s in enumerate(issued) if s.startswith("SELECT ACTION")) < \
        issued.index("RELEASE SAVEPOINT PRIVATE_AUDIT_READ")


def test_a_denial_opens_one_savepoint_not_two():
    """``record_denied`` delegates rather than guarding separately.

    Two savepoints for one INSERT would nest a name inside itself. PostgreSQL
    permits that — the older one is kept but made inaccessible until the newer
    is released — so it would not fail; it would just mean ``RELEASE`` closed a
    different savepoint than the one a reader of the code expects, and only at
    runtime.
    """
    conn, inner = _live_cursor()
    recorder = RecordingCursor(inner)
    try:
        audit.record_denied(recorder, actor_user_id=ACTOR, owner_user_id=OWNER,
                            object_type="INSURANCE_POLICY", object_id="382")
    finally:
        conn.close()

    trace = _savepoint_trace(recorder.statements)
    assert [t for t in trace if t[0] == "SAVEPOINT"] == [
        ("SAVEPOINT", "PRIVATE_AUDIT_WRITE")], trace


def test_a_healthy_write_is_never_unwound():
    """The converse, and it matters as much.

    Unwinding unconditionally would be a quieter bug in the same family: every
    audit row in the system would be written and then discarded, and the table
    would simply be empty.
    """
    conn, inner = _live_cursor()
    recorder = RecordingCursor(inner)
    try:
        audit.record(recorder, actor_user_id=ACTOR, owner_user_id=OWNER,
                     action=audit.ACTION_FACT_READ)
    finally:
        conn.close()

    assert not any(s.startswith("ROLLBACK TO SAVEPOINT")
                   for s in _issued(recorder.statements)), (
        "a write that succeeded was thrown away")


# ---------------------------------------------------------------------------
# Simulated PostgreSQL — the failure itself
# ---------------------------------------------------------------------------


def test_a_failed_write_is_unwound_before_record_returns():
    """The defect, stated as one assertion.

    Without the ``ROLLBACK TO SAVEPOINT``, ``record`` hands a still-poisoned
    transaction back to a caller that believes it degraded gracefully, and
    ``db.py`` rolls the whole connection back on the next failure — taking work
    the caller committed nothing about and still thinks it has.
    """
    cur = PoisonableCursor(poison_on="INSERT INTO")

    assert audit.record(cur, actor_user_id=ACTOR, owner_user_id=OWNER,
                        action=audit.ACTION_FACT_READ) is False

    trace = _savepoint_trace(cur.statements)
    assert trace == [
        ("SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
        ("ROLLBACK TO SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
        ("RELEASE SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
    ], trace

    assert not cur.poisoned, (
        "record returned False over an aborted transaction; on PostgreSQL "
        "db.py would then roll the connection back and take the caller's "
        "uncommitted work with it")


def test_a_failed_activity_read_is_unwound_before_record_returns():
    cur = PoisonableCursor(poison_on="SELECT ACTION")

    assert audit.recent_record_activity(cur, owner_user_id=OWNER) == []

    assert _savepoint_trace(cur.statements) == [
        ("SAVEPOINT", "PRIVATE_AUDIT_READ"),
        ("ROLLBACK TO SAVEPOINT", "PRIVATE_AUDIT_READ"),
        ("RELEASE SAVEPOINT", "PRIVATE_AUDIT_READ"),
    ]
    assert not cur.poisoned


def test_a_write_still_happens_when_the_savepoint_cannot_be_opened():
    """Degrade, do not refuse.

    If ``SAVEPOINT`` itself fails there is no containment to be had, but an
    audit row is still not worth refusing the caller's operation over. The old,
    unguarded behaviour stands — and is logged, so it is never a silent
    downgrade.
    """
    class NoSavepoints(PoisonableCursor):
        def execute(self, sql, params=()):
            if str(sql).strip().upper().startswith("SAVEPOINT"):
                self.statements.append(str(sql).strip())
                raise RuntimeError("savepoints unavailable")
            return super().execute(sql, params)

    cur = NoSavepoints()
    assert audit.record(cur, actor_user_id=ACTOR, owner_user_id=OWNER,
                        action=audit.ACTION_FACT_READ) is True
    assert any(s.startswith("INSERT INTO") for s in _issued(cur.statements))
    # Nothing is released that was never opened.
    assert not any(s.startswith("RELEASE SAVEPOINT") for s in _issued(cur.statements))


def test_an_unknown_action_opens_no_savepoint():
    """The vocabulary check runs first, so a typo costs no transaction control."""
    cur = PoisonableCursor()
    assert audit.record(cur, actor_user_id=ACTOR, owner_user_id=OWNER,
                        action="PRIVATE_FACT_REED") is False
    assert cur.statements == []


# ---------------------------------------------------------------------------
# Nesting — meetings already opens a savepoint around its call into here
# ---------------------------------------------------------------------------


def test_the_audit_savepoint_nests_correctly_inside_the_meetings_one():
    """``meetings._audit`` opens ``pm_audit`` and then calls into this module.

    Nested savepoints are legal on PostgreSQL, but the ordering has to be
    strictly last-in-first-out: releasing an outer savepoint releases every
    savepoint opened after it, so an inner one released *after* its outer would
    be a release of something that no longer exists.
    """
    conn, inner = _live_cursor()
    recorder = RecordingCursor(inner)
    try:
        meetings._audit(recorder, actor=ACTOR, owner=OWNER,
                        action=audit.ACTION_MEETING_CREATE, meeting_id=9)
    finally:
        conn.close()

    trace = _savepoint_trace(recorder.statements)
    assert trace == [
        ("SAVEPOINT", "PM_AUDIT"),
        ("SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
        ("RELEASE SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
        ("RELEASE SAVEPOINT", "PM_AUDIT"),
    ], trace


def test_a_failed_audit_write_is_healed_inside_the_meetings_savepoint():
    """The nested failure path, end to end.

    The inner savepoint un-poisons the transaction, so ``_nonfatal``'s health
    probe finds it healthy and releases ``pm_audit`` without unwinding — which
    is the correct outcome, because the meeting's own rows sit between the two
    savepoints and must survive.
    """
    cur = PoisonableCursor(poison_on="INSERT INTO")

    meetings._audit(cur, actor=ACTOR, owner=OWNER,
                    action=audit.ACTION_MEETING_CREATE, meeting_id=9)

    trace = _savepoint_trace(cur.statements)
    assert trace == [
        ("SAVEPOINT", "PM_AUDIT"),
        ("SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
        ("ROLLBACK TO SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
        ("RELEASE SAVEPOINT", "PRIVATE_AUDIT_WRITE"),
        ("RELEASE SAVEPOINT", "PM_AUDIT"),
    ], trace
    # The outer block did not unwind: the inner one had already healed the
    # transaction, and rolling back to pm_audit would discard the meeting.
    assert not cur.poisoned


def test_the_audit_savepoint_names_collide_with_nothing_else_in_the_codebase():
    """A collision would release the wrong savepoint, and only at runtime.

    ``SAVEPOINT x`` twice is legal SQL; ``RELEASE SAVEPOINT x`` then closes the
    inner one and leaves the outer open under the same name. Nothing raises.
    The caller that opened the outer savepoint believes it is still protected
    and is not.
    """
    root = pathlib.Path(audit.__file__).resolve().parents[2]
    ours = {audit._SP_WRITE.lower(), audit._SP_READ.lower()}

    others = set()
    for path in sorted((root / "services").rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        for raw in source.splitlines():
            marker = "SAVEPOINT "
            index = raw.upper().find(marker)
            if index < 0 or "ROLLBACK TO" in raw.upper():
                continue
            name = raw[index + len(marker):].strip().strip('"\')' + " ;")
            if not name or "{" in name or "%" in name:
                continue  # interpolated — covered by the meetings tests
            if path.name == "audit.py" and path.parent.name == "private_office":
                continue
            others.add(name.lower())

    assert ours.isdisjoint(others), (
        f"audit.py's savepoint names are also used elsewhere: "
        f"{sorted(ours & others)}")
    # meetings prefixes every one of its names, so the two families cannot meet.
    assert not any(n.startswith("pm_") for n in ours)


# ---------------------------------------------------------------------------
# The general rule, rather than one more instance of it
# ---------------------------------------------------------------------------

#: ``_contained`` is built out of exactly the pattern it exists to make safe:
#: it opens, unwinds and releases the savepoint, so its own statements cannot
#: themselves be inside one. Anything else added here needs a reason in
#: writing.
_SAVEPOINT_EXEMPT = {"_contained"}


def _execute_calls(path):
    """Every ``cur.execute``/``executemany`` in a module, with its guard status.

    Yields ``(function_name, lineno, protected)`` where ``protected`` means the
    call is lexically inside a ``with _contained(...)``.

    Deliberately keyed on the *statement* rather than on the enclosing
    ``try``/``except``, which is what the equivalent guard in
    ``test_private_meeting_persistence.py`` keys on. The two shapes differ:
    ``_nonfatal`` wraps blocks that swallow their errors internally, so the
    ``try`` sits inside the ``with``; here the exception is allowed to reach
    the contextmanager, so the ``try`` sits outside it. Asking "is every
    statement guarded" is true of both shapes and is the stronger claim
    anyway — it does not stop being enforced if somebody adds a statement with
    no ``try`` around it at all.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    protected = set()

    def mark(node, guarded):
        for child in ast.iter_child_nodes(node):
            is_guard = guarded
            if isinstance(child, ast.With):
                is_guard = guarded or any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Name)
                    and item.context_expr.func.id == "_contained"
                    for item in child.items)
            if is_guard:
                protected.add(id(child))
            mark(child, is_guard)

    mark(tree, False)

    owner = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                # Innermost definition wins for nested helpers.
                if line not in owner or node.lineno > owner[line][1]:
                    owner[line] = (node.name, node.lineno)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"execute", "executemany"}:
            continue
        name = owner.get(node.lineno, ("<module>", 0))[0]
        yield name, node.lineno, id(node) in protected


def test_no_statement_in_this_module_runs_outside_a_savepoint():
    """The rule the audit write broke, enforced against the whole module.

    Wrapping ``cur.execute`` in a try/except looks like graceful degradation
    and is not one on PostgreSQL: the failed statement has already aborted the
    transaction, so by the time the ``except`` runs ``db.py`` has rolled the
    *connection* back and taken the caller's uncommitted work with it. The
    block then returns its polite fallback — ``False``, ``[]`` — to a caller
    whose row no longer exists.

    Structural on purpose. SQLite does not poison a transaction, so every
    statement in this module behaves impeccably under this suite whether or not
    it is savepointed, and a behavioural test would have stayed green through
    the entire defect. This one is the reason the guarantee survives the next
    statement somebody adds here: ~70 call sites in 19 modules now depend on
    it, and none of them can see whether it holds.
    """
    module = pathlib.Path(audit.__file__)
    calls = list(_execute_calls(module))

    assert len(calls) >= 4, (
        f"expected this module's SQL statements, found {len(calls)} — if the "
        f"parse broke, this test passes while checking nothing")

    unguarded = [(name, line) for name, line, ok in calls
                 if not ok and name not in _SAVEPOINT_EXEMPT]

    assert not unguarded, (
        "these statements run outside a savepoint, so on PostgreSQL a failure "
        "in one discards the caller's transaction and this module then reports "
        "it as a best-effort miss: "
        + ", ".join(f"{name}() line {line}" for name, line in unguarded))
