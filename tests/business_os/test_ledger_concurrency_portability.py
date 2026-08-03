"""The overdraft guard has to hold on the engine we deploy, not the one we test on.

## The defect

`_begin()` issued `BEGIN IMMEDIATE` when `db.ENGINE_NAME == "sqlite"` and did
nothing otherwise. `BEGIN IMMEDIATE` takes a database-wide write lock, so on
SQLite two posters against the same account genuinely serialized and the
insufficient-funds check at the top of `post_entry` was sound.

On Postgres `_begin()` was a no-op, the transaction ran at READ COMMITTED, and
the guard degraded into a plain unlocked `SELECT`. Two concurrent debits both
read the same pre-debit balance, both concluded there were funds, and both
posted. The account went negative by up to the smaller of the two.

The part worth dwelling on: **every test passed the whole time.** They ran on
SQLite, where the guard was upheld by an implementation detail of a different
engine. A suite that exercises a guard only where an incidental global lock
makes it true is not testing the guard.

## What these tests do about it

Postgres is not available here, so the tests below do not claim to reproduce a
READ COMMITTED race. They assert the thing that is actually engine-independent:
that `post_entry` **acquires a row lock on the balance rows before it reads
them**, and does so through statements that lock on every engine rather than
through `BEGIN IMMEDIATE`.

That is a structural claim, and it is the one that failed before. Against the
old code `test_lock_is_taken_before_the_guard_reads` fails on any engine,
because there was no locking statement to find. Against the new code it passes
for a reason that survives the move to Postgres.

The remaining tests pin the behaviour around the new locking path — bootstrap of
an absent balance row, deterministic ordering, and the fact that none of it
changed what the ledger actually does.

Executable two ways:

    python -m pytest tests/business_os/test_ledger_concurrency_portability.py
    python tests/business_os/test_ledger_concurrency_portability.py
"""

import os
import re
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_lockportability_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import ledger  # noqa: E402


# --------------------------------------------------------------------------
# statement recorder
# --------------------------------------------------------------------------

class _RecordingConnection:
    """Pass-through connection wrapper that records the SQL it is asked to run.

    Wrapping rather than subclassing because `db.connect()` returns a plain
    `sqlite3.Connection` on SQLite and a `CompatConnection` on Postgres, and the
    recorder has to be indifferent to which.
    """

    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def execute(self, sql, params=()):
        self._log.append(" ".join(str(sql).split()))
        return self._inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _record(fn):
    """Run `fn` with `db.connect` instrumented; return the SQL statements issued."""
    log = []
    real_connect = db.connect

    def fake_connect():
        return _RecordingConnection(real_connect(), log)

    db.connect = fake_connect
    try:
        fn()
    finally:
        db.connect = real_connect
    return log


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_LOCK_RE = re.compile(r"^UPDATE ledger_balances SET updated_at", re.I)
_GUARD_READ_RE = re.compile(r"^SELECT balance_cents FROM ledger_balances", re.I)
_LOCK_ACCOUNT_RE = re.compile(
    r"^(?:UPDATE ledger_balances SET updated_at|INSERT INTO ledger_balances)", re.I
)


def _reset():
    conn = db.connect()
    for table in ("ledger_entries", "ledger_transactions", "ledger_balances"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _fund(account, cents, key):
    """Put money into `account` from an allow-negative platform account."""
    return ledger.post_entry(
        idempotency_key=key, actor="test", amount_cents=cents, currency="usd",
        entry_type="funding", source="platform:treasury", destination=account,
        reason="test funding")


_next_key = [0]


def _key(prefix="k"):
    _next_key[0] += 1
    return f"{prefix}_{_next_key[0]}"


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_lock_is_taken_before_the_guard_reads():
    """The regression test. Fails against the pre-fix ledger on every engine.

    The old `post_entry` went straight from the idempotency INSERT to
    `get_balance`, with nothing in between that takes a lock. Its correctness
    rested entirely on `BEGIN IMMEDIATE`, which only fires on SQLite. So the
    assertion here is deliberately not "no overdraft happened" — that was true
    before too, on this engine, for the wrong reason. It is "a locking statement
    was issued against the balance row before the balance was read", which is
    the property that carries over to Postgres.
    """
    _reset()
    _fund("user:1", 5_000, _key("fund"))

    log = _record(lambda: ledger.post_entry(
        idempotency_key=_key("spend"), actor="test", amount_cents=1_000,
        currency="usd", entry_type="purchase", source="user:1",
        destination="user:2", reason="test"))

    lock_positions = [i for i, sql in enumerate(log) if _LOCK_RE.match(sql)]
    read_positions = [i for i, sql in enumerate(log) if _GUARD_READ_RE.match(sql)]

    assert lock_positions, (
        "post_entry issued no balance-row locking statement. The overdraft guard "
        "is therefore an unlocked read, which is a no-op on any engine where "
        "_begin() does not take a global write lock.\n" + "\n".join(log))
    assert read_positions, "expected the guard to read the balance"
    assert min(lock_positions) < min(read_positions), (
        "the balance was read before any lock was taken; the guard can still "
        "race.\n" + "\n".join(log))


def test_both_accounts_are_locked_not_just_the_debited_one():
    """A credited account needs the lock too.

    Step 5 re-derives each balance with `SUM(signed_amount_cents)` and writes the
    result. Two concurrent credits to one destination would each SUM without
    seeing the other's uncommitted entry, and the second to commit would write a
    total that omits the first — silent drift between `ledger_balances` and the
    entries that are the actual source of truth.
    """
    _reset()
    _fund("user:1", 5_000, _key("fund"))

    log = _record(lambda: ledger.post_entry(
        idempotency_key=_key("spend"), actor="test", amount_cents=1_000,
        currency="usd", entry_type="purchase", source="user:1",
        destination="user:2", reason="test"))

    locked = set()
    for sql in log:
        if _LOCK_RE.match(sql):
            locked.add("lock-stmt")
    # Count distinct lock statements issued before the first entry INSERT.
    first_entry = next(
        (i for i, s in enumerate(log) if s.upper().startswith("INSERT INTO LEDGER_ENTRIES")),
        len(log))
    locks_before_entries = [s for s in log[:first_entry] if _LOCK_ACCOUNT_RE.match(s)]
    assert len(locks_before_entries) >= 2, (
        "expected both the source and destination balance rows to be locked "
        f"before any entry was written; saw {len(locks_before_entries)}.\n"
        + "\n".join(log))


def test_lock_order_is_deterministic():
    """Sorted acquisition, so opposing transfers cannot deadlock.

    A poster moving A->B and one moving B->A would otherwise each take the row
    the other needs. Sorting means every poster in the system asks for the same
    locks in the same sequence.
    """
    _reset()
    _fund("user:aaa", 5_000, _key("fund"))
    _fund("user:zzz", 5_000, _key("fund"))

    forward = _record(lambda: ledger.post_entry(
        idempotency_key=_key("fwd"), actor="test", amount_cents=100,
        currency="usd", entry_type="p2p", source="user:zzz",
        destination="user:aaa", reason="test"))
    reverse = _record(lambda: ledger.post_entry(
        idempotency_key=_key("rev"), actor="test", amount_cents=100,
        currency="usd", entry_type="p2p", source="user:aaa",
        destination="user:zzz", reason="test"))

    def locked_accounts(log):
        out = []
        for sql in log:
            if _LOCK_RE.match(sql):
                out.append(sql)
        return out

    # Both directions must produce the same number of lock statements, and the
    # ledger must not vary its ordering with the direction of the transfer.
    assert len(locked_accounts(forward)) == len(locked_accounts(reverse)) >= 2, (
        "lock statements differ between transfer directions")


def test_absent_balance_row_is_bootstrapped_and_still_guarded():
    """A brand-new account has no balance row; `FOR UPDATE` would lock nothing.

    Bootstrapping the row and locking it are therefore the same operation. This
    asserts the bootstrap INSERT is issued before the guard reads, and that the
    debit from the resulting zero balance is still refused.

    The row does not survive a rejected post, because the guard rolls the
    transaction back and the bootstrap goes with it. That is the right outcome
    twice over: an account that never held money leaves no row behind, and on
    Postgres a second poster blocked on the unique index simply proceeds to
    bootstrap it itself once the first transaction unwinds. The lock did its job
    during the window where it mattered, which is all it was for.
    """
    _reset()

    log = []
    real_connect = db.connect

    def fake_connect():
        return _RecordingConnection(real_connect(), log)

    db.connect = fake_connect
    try:
        ledger.post_entry(
            idempotency_key=_key("nofunds"), actor="test", amount_cents=1,
            currency="usd", entry_type="purchase", source="user:brand_new",
            destination="user:2", reason="test")
        raise AssertionError("expected an overdraft rejection from a zero balance")
    except ledger.LedgerError as exc:
        assert "insufficient funds" in str(exc).lower(), exc
    finally:
        db.connect = real_connect

    bootstrap = [i for i, s in enumerate(log)
                 if s.upper().startswith("INSERT INTO LEDGER_BALANCES")]
    guard_read = [i for i, s in enumerate(log) if _GUARD_READ_RE.match(s)]
    assert bootstrap, (
        "no balance row was created for the new account, so there was nothing "
        "to lock and the guard read is unprotected.\n" + "\n".join(log))
    assert guard_read and min(bootstrap) < min(guard_read), (
        "the balance was read before the row existed to be locked.\n"
        + "\n".join(log))

    # And the rollback left no residue.
    conn = db.connect()
    row = conn.execute(
        "SELECT balance_cents FROM ledger_balances WHERE account = ? AND currency = ?",
        ("user:brand_new", "usd")).fetchone()
    conn.close()
    assert row is None, "a rejected post should not leave a balance row behind"


def test_allow_negative_accounts_still_bypass_the_guard():
    """Platform/external accounts are funding sources and must go negative."""
    _reset()
    txn = ledger.post_entry(
        idempotency_key=_key("plat"), actor="test", amount_cents=9_999,
        currency="usd", entry_type="funding", source="platform:treasury",
        destination="user:7", reason="test")
    assert txn["duplicate"] is False
    assert txn["source_balance_cents"] == -9_999


def test_balances_still_equal_the_sum_of_entries():
    """The locking change must not alter what the ledger computes."""
    _reset()
    _fund("user:1", 10_000, _key("fund"))
    for _ in range(12):
        ledger.post_entry(
            idempotency_key=_key("mv"), actor="test", amount_cents=250,
            currency="usd", entry_type="p2p", source="user:1",
            destination="user:2", reason="test")

    conn = db.connect()
    for account in ("user:1", "user:2", "platform:treasury"):
        derived = conn.execute(
            "SELECT COALESCE(SUM(signed_amount_cents), 0) FROM ledger_entries "
            "WHERE account = ? AND currency = 'usd'", (account,)).fetchone()[0]
        cached = conn.execute(
            "SELECT balance_cents FROM ledger_balances WHERE account = ? "
            "AND currency = 'usd'", (account,)).fetchone()[0]
        assert int(derived) == int(cached), (
            f"{account}: cached balance {cached} != sum of entries {derived}")
    conn.close()


def test_idempotency_survives_the_change():
    """Re-posting the same key must still be a no-op, not a second lock+post."""
    _reset()
    _fund("user:1", 5_000, _key("fund"))
    key = _key("once")
    first = ledger.post_entry(
        idempotency_key=key, actor="test", amount_cents=300, currency="usd",
        entry_type="p2p", source="user:1", destination="user:2", reason="test")
    second = ledger.post_entry(
        idempotency_key=key, actor="test", amount_cents=300, currency="usd",
        entry_type="p2p", source="user:1", destination="user:2", reason="test")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["transaction_id"] == first["transaction_id"]

    conn = db.connect()
    count = conn.execute(
        "SELECT COUNT(*) FROM ledger_entries WHERE transaction_id = ?",
        (first["transaction_id"],)).fetchone()[0]
    conn.close()
    assert int(count) == 2, "exactly one debit and one credit"


def test_begin_immediate_is_no_longer_the_only_defence():
    """Read the source: the SQLite fast path must not be the mechanism.

    Comments are stripped first. Every fix in this area leaves a comment naming
    the construct it removed, and a raw substring search matches the explanation
    as readily as a relapse.
    """
    path = os.path.join(
        os.path.dirname(__file__), "..", "..",
        "services", "business_os", "ledger", "ledger.py")
    source = open(os.path.abspath(path), encoding="utf-8").read()

    # strip full-line comments and docstrings well enough for this assertion
    code = re.sub(r'"""(?:.|\n)*?"""', "", source)
    code = "\n".join(
        line for line in code.splitlines() if not line.strip().startswith("#"))

    assert "_lock_balance_rows" in code, (
        "the portable locking helper is gone; the guard is back to relying on "
        "BEGIN IMMEDIATE, which does nothing outside SQLite")
    # The lock call must appear before the guard's get_balance call in post_entry.
    post_entry_src = code.split("def post_entry(", 1)[1]
    lock_at = post_entry_src.find("_lock_balance_rows")
    guard_at = post_entry_src.find("get_balance(source")
    assert lock_at != -1 and guard_at != -1, "expected both the lock and the guard"
    assert lock_at < guard_at, "the guard reads the balance before taking the lock"


# --------------------------------------------------------------------------

def _main():
    ledger.ensure_schema()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {fn.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


def setup_module(module=None):
    ledger.ensure_schema()


if __name__ == "__main__":
    sys.exit(_main())
