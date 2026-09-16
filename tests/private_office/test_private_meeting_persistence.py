"""Private Meetings — a booking that was reported as made must exist.

Why this file exists
--------------------
Two meetings were scheduled on a physical device and neither was ever in the
database. Nothing errored. The API answered ``201`` with a complete meeting
object both times, the wizard closed, and production's sequences kept the only
record of what had happened: ``private_meetings_id_seq`` had issued ids 3 and
4 that no row was using, and ``private_meeting_reminders_id_seq`` stood at 6
over an empty table — two bookings and their six reminders, written and then
erased.

The cause was one column. ``_recipient_email`` asked ``users`` for
``user_id=? OR id=?``, meaning to accept either name; ``users`` is keyed on
``user_id`` and has no ``id``, and on PostgreSQL an unknown column is a hard
error before a single row is read. The ``except`` around it turned that into
"no address" and carried on, but the statement had already aborted the
transaction, so ``services/db.py`` rolled the connection back to keep it
usable — discarding the meeting, its host participant, its audit row and its
reminders. ``create_meeting`` then built its response from the meeting dict it
still held in memory and returned it.

So the tests here are about the gap between what the API says and what the
database holds, and they are deliberately of two kinds, because one kind
cannot cover it:

* **Behavioural**, for what SQLite can show — that a meeting survives an email
  layer that fails, and that what ``create_meeting`` returns can be read back.
* **Structural**, for what SQLite cannot show — SQLite does not abort a
  transaction on a failed statement, so the production failure is literally
  unreproducible here. Those invariants are asserted on the statements issued
  rather than on the outcome, because on this engine the outcome is the same
  either way. That is the same blindness that let the original defect ship:
  the suite's own fixture had invented a ``users.id`` column, so every test
  had the column production lacked.
"""

import ast
import os
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import pulsesoc_communications_engine as eng  # noqa: E402
from services.private_office import meeting_emails as pm_mail  # noqa: E402
from services.private_office import meetings  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402

HOST = 101
GUEST = 202

FUTURE = "2033-07-19T14:30:00"
ZONE = "America/New_York"


class RecordingCursor:
    """A cursor that remembers the SQL put through it.

    Used only by the structural tests. It forwards everything, so the code
    under test behaves exactly as it would otherwise; the recording is how a
    savepoint — invisible in SQLite's results — becomes assertable.
    """

    def __init__(self, inner):
        self._inner = inner
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(str(sql).strip())
        return self._inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _make_cursor():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    meetings.reset_meetings_schema_cache()
    meetings.ensure_meetings_schema(cursor, force=True)
    cursor.execute(po_schema.AUDIT_TABLE_DDL)
    cursor.execute(
        "CREATE TABLE blocked_users (blocker_user_id INT, blocked_user_id INT)")
    cursor.execute(
        "CREATE TABLE comm_v2_blocks (id INTEGER PRIMARY KEY, "
        "blocker_user_id INT, blocked_user_id INT, status TEXT)")
    # The production shape, from bot.py's `CREATE TABLE users`. No `id`.
    cursor.execute(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, "
        "display_name TEXT, email TEXT)")
    for user_id, email in ((HOST, "host@example.com"), (GUEST, "guest@example.com")):
        cursor.execute("INSERT INTO users (user_id, email) VALUES (?, ?)",
                       (user_id, email))
    return conn, cursor


@pytest.fixture()
def cur(monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_ENABLED", "1")
    monkeypatch.setattr(eng, "agora_config_status", lambda: {"configured": True})
    conn, cursor = _make_cursor()
    yield cursor
    conn.close()
    meetings.reset_meetings_schema_cache()


@pytest.fixture(autouse=True)
def _outbox(monkeypatch):
    """Capture mail instead of queueing it. Returns the captured list."""
    captured = []

    def fake_queue(user_id, to_email, subject, html_body, text_body="",
                   email_type="transactional", metadata=None, notification_id=0,
                   send_after=""):
        captured.append({"user_id": user_id, "to_email": to_email,
                         "subject": subject, "send_after": send_after})
        return {"ok": True, "status": "queued", "queue_id": len(captured)}

    from services import notification_service

    monkeypatch.setattr(notification_service, "_queue_email_job", fake_queue)
    return captured


def _schedule(cur, **overrides):
    params = {
        "owner_user_id": HOST,
        "title": "Board sync",
        "scheduled_start_at": FUTURE,
        "timezone_name": ZONE,
        "duration_minutes": 30,
        "agenda": "Quarterly review",
    }
    params.update(overrides)
    return meetings.create_meeting(cur, **params)


def _row_for(cur, public_id):
    cur.execute("SELECT * FROM private_meetings WHERE public_id=?", (public_id,))
    return cur.fetchone()


# ---------------------------------------------------------------------------
# The invariant the defect broke
# ---------------------------------------------------------------------------


def test_a_returned_meeting_can_be_read_back_from_the_database(cur):
    """The whole defect, stated as one assertion.

    ``create_meeting`` returned a meeting object that no query could find. It
    is not enough to check that the call succeeded or that the payload looks
    right — both were true in production. The row has to be there.
    """
    meeting = _schedule(cur)
    public_id = meeting["public_id"]
    assert public_id

    row = _row_for(cur, public_id)
    assert row is not None, (
        "create_meeting reported a meeting that is not in the database")
    assert row["owner_user_id"] == HOST
    assert row["status"] == meetings.ST_SCHEDULED
    assert row["duration_minutes"] == 30
    # The timezone is part of the booking, not decoration: a reschedule cannot
    # know what "the same time" means without it.
    assert row["scheduled_timezone"] == ZONE


def test_the_host_participant_and_reminders_survive_alongside_the_meeting(cur):
    """The rollback took the whole transaction, not just the meeting row."""
    meeting = _schedule(cur)
    meeting_row = _row_for(cur, meeting["public_id"])
    meeting_id = meeting_row["id"]

    cur.execute("SELECT count(*) FROM private_meeting_participants "
                "WHERE meeting_id=? AND user_id=?", (meeting_id, HOST))
    assert cur.fetchone()[0] == 1, "the host lost their own seat"

    cur.execute("SELECT count(*) FROM private_meeting_reminders "
                "WHERE meeting_id=?", (meeting_id,))
    assert cur.fetchone()[0] > 0, "the reminder plan did not survive the booking"


def test_the_host_is_told_their_meeting_exists(cur, _outbox):
    """A confirmation is the only thing that makes the booking checkable.

    Without it the member's sole evidence is a screen that closed, which is
    exactly what they had while nothing was being saved.
    """
    _schedule(cur)
    confirmations = [m for m in _outbox if m["user_id"] == HOST]
    assert confirmations, "the host was never told the meeting was scheduled"
    assert confirmations[0]["to_email"] == "host@example.com"


# ---------------------------------------------------------------------------
# The email layer must never be able to cancel a booking
# ---------------------------------------------------------------------------


def test_a_meeting_survives_an_email_layer_that_raises(cur, monkeypatch):
    """Mission §13, as a test: a mail failure degrades, it does not delete.

    The docstrings promised this before the savepoint existed, and on
    PostgreSQL the promise was false — the swallow hid the abort rather than
    containing it.
    """
    def explode(*args, **kwargs):
        raise RuntimeError("the mail provider fell over")

    monkeypatch.setattr(pm_mail, "announce", explode)

    meeting = _schedule(cur)
    assert _row_for(cur, meeting["public_id"]) is not None, (
        "a failed confirmation email deleted the meeting")


def test_a_meeting_survives_a_reminder_planner_that_raises(cur, monkeypatch):
    """Same guarantee on the other non-fatal block."""
    from services.private_office import meeting_reminders

    def explode(*args, **kwargs):
        raise RuntimeError("the reminder planner fell over")

    monkeypatch.setattr(meeting_reminders, "plan_reminders", explode)

    meeting = _schedule(cur)
    assert _row_for(cur, meeting["public_id"]) is not None, (
        "a failed reminder plan deleted the meeting")


def test_a_meeting_survives_a_recipient_lookup_that_raises(cur, monkeypatch):
    """The exact shape of the production failure, as close as SQLite allows.

    SQLite will not abort the transaction the way PostgreSQL does, so this
    cannot reproduce the data loss — it pins the one thing that is engine
    independent: a lookup that fails must not change whether the meeting was
    booked.
    """
    def explode(*args, **kwargs):
        raise RuntimeError("no such column: id")

    monkeypatch.setattr(pm_mail, "_recipient_email", explode)

    meeting = _schedule(cur)
    assert _row_for(cur, meeting["public_id"]) is not None


# ---------------------------------------------------------------------------
# Structural — what this engine cannot show
# ---------------------------------------------------------------------------


def test_the_recipient_lookup_asks_only_for_columns_users_actually_has(cur):
    """Guards the specific column that cost two bookings.

    ``users`` here is built to the production shape, so a query naming any
    other column raises instead of quietly returning nothing.
    """
    assert pm_mail._recipient_email(cur, HOST) == "host@example.com"
    assert pm_mail._recipient_email(cur, GUEST) == "guest@example.com"
    # A user who is not there is an empty answer, not an error.
    assert pm_mail._recipient_email(cur, 999999) == ""


def test_the_lookup_does_not_name_a_column_outside_the_canonical_schema(monkeypatch):
    """Read the statement, not the result.

    A behavioural test cannot see this one: on SQLite the defective query and
    the correct one both end with "no email", and the difference only becomes
    a lost meeting on PostgreSQL. So assert on what was sent.
    """
    conn, inner = _make_cursor()
    recorder = RecordingCursor(inner)
    try:
        pm_mail._recipient_email(recorder, HOST)
    finally:
        conn.close()

    lookup = " ".join(recorder.statements).lower()
    assert "from users" in lookup
    assert "user_id=?" in lookup.replace(" ", "")
    # The phantom column, named exactly. `OR id=?` is the whole defect.
    assert "or id=?" not in lookup.replace(" ", " ").replace("  ", " ")
    assert " id=" not in lookup.split("from users")[1]


def test_the_non_fatal_blocks_run_inside_a_savepoint(cur, monkeypatch):
    """The containment, asserted where SQLite cannot demonstrate it.

    On PostgreSQL the savepoint is what stops ``db.py`` rolling the whole
    connection back on behalf of a block that has already swallowed its own
    error. SQLite never poisons a transaction, so the meeting survives here
    with or without it — which means a passing behavioural test proves nothing
    about production. The statements do.
    """
    conn, inner = _make_cursor()
    recorder = RecordingCursor(inner)
    try:
        meetings.create_meeting(
            recorder, owner_user_id=HOST, title="Board sync",
            scheduled_start_at=FUTURE, timezone_name=ZONE,
            duration_minutes=30)
    finally:
        conn.close()

    issued = [s.upper() for s in recorder.statements]
    savepoints = [s for s in issued if s.startswith("SAVEPOINT ")]
    releases = [s for s in issued if s.startswith("RELEASE SAVEPOINT ")]

    assert savepoints, "the non-fatal blocks ran unprotected"
    # Every savepoint is closed: an unreleased one would grow the stack on a
    # connection the route reuses.
    assert len(releases) == len(savepoints), (
        f"{len(savepoints)} savepoints opened, {len(releases)} released")

    names = {s.split()[1] for s in savepoints}
    assert "PM_PLAN_REMINDERS" in names
    assert "PM_ANNOUNCE" in names
    # The audit row is bookkeeping about the booking, so it must never be able
    # to cost the booking. `audit.record` catches its own exception and returns
    # False, which reads as best-effort and was not: on PostgreSQL the failed
    # INSERT had already aborted the transaction and db.py had already rolled
    # the connection back, so the meeting was gone before `record` reached its
    # `except`. Verified against production's own 18.6 engine by dropping
    # `private_audit_events` and booking anyway.
    assert "PM_AUDIT" in names


class PoisonableCursor:
    """A cursor that can pretend its transaction has been aborted.

    Stands in for PostgreSQL's behaviour, which SQLite has no equivalent of:
    after a failed statement every later one raises until something unwinds.
    """

    def __init__(self, *, poisoned_after=None):
        self.statements: list[str] = []
        self._poisoned = False
        self._poison_trigger = poisoned_after

    def execute(self, sql, params=()):
        text = str(sql).strip()
        self.statements.append(text)
        upper = text.upper()
        if upper.startswith("ROLLBACK TO SAVEPOINT"):
            self._poisoned = False
            return self
        if self._poisoned:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until end "
                "of transaction block")
        if self._poison_trigger and self._poison_trigger in text:
            self._poisoned = True
            raise RuntimeError('column "id" does not exist')
        return self

    def fetchone(self):
        return None


def test_a_block_that_swallowed_an_abort_is_unwound_to_the_savepoint():
    """The health probe, which is the part that is easy to leave out.

    ``meeting_emails`` catches its own errors several frames below
    ``_nonfatal``, so a clean exit says nothing about the transaction — that
    is precisely how the original failure stayed invisible. The block has to
    ask.
    """
    cur = PoisonableCursor(poisoned_after="SELECT email FROM users")

    with meetings._nonfatal(cur, "announce"):
        try:
            cur.execute("SELECT email FROM users WHERE user_id=? OR id=?", (1, 1))
        except Exception:  # noqa: BLE001 — exactly what meeting_emails does
            pass

    issued = [s.upper() for s in cur.statements]
    assert any(s.startswith("ROLLBACK TO SAVEPOINT PM_ANNOUNCE") for s in issued), (
        "a swallowed abort was never unwound; on PostgreSQL db.py would then "
        "roll back the whole connection and take the meeting with it")
    assert any(s.startswith("RELEASE SAVEPOINT PM_ANNOUNCE") for s in issued)


def test_a_healthy_block_is_released_without_being_unwound():
    """The converse, and it matters as much.

    Unwinding unconditionally would be a quieter bug in the same family: the
    meeting would survive and its reminders would vanish every single time.
    """
    cur = PoisonableCursor()

    with meetings._nonfatal(cur, "plan_reminders"):
        cur.execute("INSERT INTO private_meeting_reminders (meeting_id) VALUES (?)",
                    (1,))

    issued = [s.upper() for s in cur.statements]
    assert not any(s.startswith("ROLLBACK TO SAVEPOINT") for s in issued), (
        "a block that succeeded had its work thrown away")
    assert any(s.startswith("RELEASE SAVEPOINT PM_PLAN_REMINDERS") for s in issued)


# ---------------------------------------------------------------------------
# The suite's own premise.
# ---------------------------------------------------------------------------

_DDL_RE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?users\s*\((.*?)\)\s*(?:\"|$)",
                     re.S | re.M)
_NOT_A_COLUMN = ("primary", "unique", "foreign", "constraint", "check")


def _declared_columns(body: str) -> set:
    """Column names from the inside of a ``CREATE TABLE`` — commas or newlines."""
    names = set()
    for part in body.replace("\n", ",").split(","):
        part = part.strip().strip('"').strip()
        if not part or "(" in part:
            continue          # a composite PRIMARY KEY (a, b) fragment
        head = part.split()[0].strip('"').lower()
        if head in _NOT_A_COLUMN:
            continue
        names.add(head)
    return names


def _fixture_user_tables():
    """Every ``users`` table this directory builds, as (file, columns).

    Uses ``ast`` because the fixtures write their DDL as adjacent string
    literals; Python folds those into one constant, so the parser hands back
    the whole statement instead of the fragments a line-based read would see.
    """
    out = []
    here = pathlib.Path(__file__).resolve().parent
    for path in sorted(here.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for body in _DDL_RE.findall(node.value):
                out.append((path.name, _declared_columns(body)))
    return out


def test_no_fixture_in_this_directory_keys_users_on_an_invented_id():
    """The check that would have caught the original defect weeks earlier.

    ``test_private_meeting_reminders.py`` used to create ``users`` with an
    ``id`` primary key. Production has no such column, and that one invented
    word is the whole reason a 7,000-test suite watched two real bookings
    disappear and reported success: the broken ``OR id=?`` lookup found the
    column it asked for, every time, in every test.

    The assertion is deliberately about identity and nothing else. Production's
    ``users`` carries 106 columns, assembled by a ``CREATE TABLE`` in
    ``bot.py`` plus ``ALTER TABLE`` passes scattered across services — no test
    can hold a true list of them, and one that tried would be wrong within a
    week and would start failing honest fixtures. What a test *can* pin is the
    fact that broke: a user row is found by ``user_id``, and ``users.id`` does
    not exist. Verified against the production catalogue, which has all 106 of
    those columns and no ``id``.
    """
    tables = _fixture_user_tables()
    assert len(tables) >= 8, (
        f"expected this directory's users fixtures, found {len(tables)} — "
        f"if the parse broke, this test passes while checking nothing")

    for name, columns in tables:
        assert "user_id" in columns, (
            f"{name} builds a users table with no user_id; production is keyed "
            f"on it, so anything this fixture proves is about a different table")
        assert "id" not in columns, (
            f"{name} declares users.id, which production does not have. A test "
            f"holding that column cannot see code that asks for it — which is "
            f"exactly how the `OR id=?` lookup shipped and lost two meetings.")


# ---------------------------------------------------------------------------
# The general rule, rather than one more instance of it
# ---------------------------------------------------------------------------

#: ``_nonfatal`` is built out of exactly the pattern it exists to make safe —
#: it probes with ``SELECT 1`` and unwinds with ``ROLLBACK TO SAVEPOINT``, both
#: inside ``try``. ``sweep_meetings`` catches only ``PrivateMeetingRejected``,
#: which application logic raises over a healthy connection rather than a
#: failed statement. Anything else added here needs a reason in writing.
_SAVEPOINT_EXEMPT = {"_nonfatal", "sweep_meetings"}


def _swallowing_db_blocks(path):
    """Every try/except that runs SQL and does not re-raise, with its status.

    Yields ``(function_name, lineno, protected)`` where ``protected`` means the
    block is lexically inside a ``with _nonfatal(...)``.
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
                    and item.context_expr.func.id == "_nonfatal"
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
        if not isinstance(node, ast.Try):
            continue
        runs_sql = any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in {"execute", "executemany", "record",
                                   "record_denied"}
            for statement in node.body
            for call in ast.walk(statement))
        if not runs_sql:
            continue
        swallows = any(
            not any(isinstance(n, ast.Raise) for n in ast.walk(handler))
            for handler in node.handlers)
        if not swallows:
            continue
        name = owner.get(node.lineno, ("<module>", 0))[0]
        yield name, node.lineno, id(node) in protected


def test_no_swallowed_database_failure_escapes_a_savepoint():
    """The rule the audit write broke, enforced against the whole module.

    Catching an exception around ``cur.execute`` looks like graceful
    degradation and is not one on PostgreSQL: the failed statement has already
    aborted the transaction, so by the time the ``except`` runs ``db.py`` has
    rolled the *connection* back and taken the caller's uncommitted work with
    it. The block then returns its polite fallback — ``0``, ``False``, ``None``
    — to a caller whose meeting no longer exists, and ``create_meeting`` builds
    a ``201`` out of the dict it still holds in memory.

    Three call sites were found this way rather than one: the audit row, the
    ``blocked_users`` probe, and the ``users`` lookup that decides whether an
    invited address belongs to a member. Only the last was reachable in the
    harness run that exposed it, which is the argument for testing the shape
    instead of hunting instances — the other two were one schema difference
    away from costing somebody the same booking.

    Structural on purpose. SQLite does not poison a transaction, so every one
    of these blocks behaves impeccably in this suite whether or not it is
    savepointed, and a behavioural test would have stayed green through the
    entire defect.
    """
    module = pathlib.Path(meetings.__file__)
    blocks = list(_swallowing_db_blocks(module))

    assert len(blocks) >= 5, (
        f"expected to find this module's swallowing DB blocks, found "
        f"{len(blocks)} — if the parse broke, this test passes vacuously")

    unguarded = [
        (name, line) for name, line, ok in blocks
        if not ok and name not in _SAVEPOINT_EXEMPT]

    assert not unguarded, (
        "these blocks swallow a database failure without a savepoint, so on "
        "PostgreSQL they discard the caller's transaction and then report "
        "success: "
        + ", ".join(f"{name}() line {line}" for name, line in unguarded))
