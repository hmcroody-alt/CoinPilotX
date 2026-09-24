"""Locks against the one bug class every test in this repo is blind to.

The defect
----------

``services/db.connect()`` returns a ``CompatConnection`` **only** when
``IS_POSTGRES``. SQLite gets a raw ``sqlite3.Connection`` with
``row_factory = sqlite3.Row``. So production and the test suite hand back two
different row types, and those types disagree about what iteration means:

    sqlite3.Row  is a SEQUENCE ->  tuple(row) == the column VALUES
    CompatRow    is a MAPPING  ->  tuple(row) == the column NAMES

Every idiom built on that -- ``tuple(row)``, ``list(row)``,
``dict(zip(columns, tuple(row)))`` -- is therefore correct on the engine the
tests run on and wrong on the engine production runs on. There is no assertion a
SQLite test can make that notices.

This is not a hypothetical. It has shipped four times:

  * ``pulse_communications_v2`` counted duplicate messages with ``list(row)[0]``
    and got ``int('group_count')`` -> ValueError. The send-idempotency index has
    consequently never installed in production (issue #25), so the TOCTOU race
    it exists to close has been open the whole time.
  * ``premium_crypto_access.load_user_row`` returned
    ``{"lifetime_premium": "lifetime_premium", ...}``; the legacy gate raised on
    it and answered False for every user.
  * ``seller_dashboard._count`` branched on ``hasattr(row, "keys")`` -- true for
    BOTH types -- and raised ValueError out of a function whose contract is to
    return None for "unavailable".
  * ``bot.init_db`` merged duplicate Messenger threads by searching for the
    thread whose ``user_one_id`` is the string ``"user_one_id"``. The merge that
    must precede the uniqueness index has never run on Postgres.

What these tests do
-------------------

Two things, because either alone is insufficient:

1. Exercise ``db.row_values`` against BOTH real row types -- the genuine
   ``CompatRow`` imported from ``services.db``, never a hand-rolled fake. A fake
   row class is free to share whichever misunderstanding produced the bug, so it
   would agree with the code under test and prove nothing.

2. Grep the source for the dangerous idioms and require each surviving one to be
   on the known-safe list. A fix applied to four files does not stop a fifth
   from being written next week, and the engine that would catch it is the one
   no test runs on.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import pathlib
import re
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from services.db import CompatRow, row_values  # noqa: E402


COLUMNS = ("user_id", "lifetime_premium", "premium_until")
VALUES = (7, 1, "2027-01-01")


def _sqlite_row(columns, values):
    """A real ``sqlite3.Row``, built the way the driver builds one."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    select = ", ".join(f"? AS {name}" for name in columns)
    row = conn.execute(f"SELECT {select}", values).fetchone()
    conn.close()
    assert isinstance(row, sqlite3.Row)
    return row


# ---------------------------------------------------------------------------
# 1. The asymmetry itself
# ---------------------------------------------------------------------------

def test_the_two_row_types_really_do_disagree_about_iteration():
    """If this ever fails, the bug class is gone and these locks are obsolete.

    Asserted rather than assumed, because every fix below is justified ONLY by
    this disagreement being real.
    """
    sqlite_row = _sqlite_row(COLUMNS, VALUES)
    compat_row = CompatRow(COLUMNS, VALUES)

    assert tuple(sqlite_row) == VALUES, (
        f"sqlite3.Row should iterate values, got {tuple(sqlite_row)!r}"
    )
    assert tuple(compat_row) == COLUMNS, (
        f"CompatRow should iterate keys (it is a Mapping), got {tuple(compat_row)!r}"
    )
    assert tuple(sqlite_row) != tuple(compat_row), (
        "the two row types agreed about tuple(row); that is the whole premise"
    )


def test_positional_indexing_is_the_idiom_that_already_agreed():
    """``row[0]`` is safe on both, which is why several fixes just use it."""
    sqlite_row = _sqlite_row(COLUMNS, VALUES)
    compat_row = CompatRow(COLUMNS, VALUES)
    for index, expected in enumerate(VALUES):
        assert sqlite_row[index] == expected
        assert compat_row[index] == expected


# ---------------------------------------------------------------------------
# 2. The helper
# ---------------------------------------------------------------------------

def test_row_values_gives_the_same_answer_for_both_engines():
    sqlite_row = _sqlite_row(COLUMNS, VALUES)
    compat_row = CompatRow(COLUMNS, VALUES)
    assert row_values(sqlite_row) == VALUES
    assert row_values(compat_row) == VALUES
    assert row_values(sqlite_row) == row_values(compat_row), (
        "row_values exists precisely so these two are interchangeable"
    )


def test_zipping_against_a_column_list_survives_both_engines():
    """The exact idiom that broke ``premium_crypto_access.load_user_row``."""
    expected = {"user_id": 7, "lifetime_premium": 1, "premium_until": "2027-01-01"}
    for row in (_sqlite_row(COLUMNS, VALUES), CompatRow(COLUMNS, VALUES)):
        built = dict(zip(COLUMNS, row_values(row)))
        assert built == expected, f"{type(row).__name__} built {built!r}"
        # The failure had a distinctive shape worth naming: every value was its
        # own key. Assert it is gone rather than only that the result is right.
        assert not any(k == v for k, v in built.items()), (
            f"{type(row).__name__}: column names leaked into the values: {built!r}"
        )


def test_row_values_keeps_duplicate_columns_apart():
    """``SELECT a, a`` collapses in CompatRow's mapping half but not its
    positional half, so the helper must read positionally."""
    row = CompatRow(("n", "n"), (1, 2))
    assert row_values(row) == (1, 2), (
        f"duplicate column names collapsed the row to {row_values(row)!r}"
    )


def test_row_values_handles_none_and_plain_tuples():
    assert row_values(None) == ()
    assert row_values((1, 2, 3)) == (1, 2, 3)


# ---------------------------------------------------------------------------
# 3. The two fixed call sites, driven end to end with a production row
# ---------------------------------------------------------------------------
#
# The scanner below catches a literal ``tuple(row)`` coming back. These catch
# the other direction: a rewrite that keeps the idiom out but still cannot read
# a Postgres row. Both functions are reached on every request to a surface that
# matters, and neither had a test that fed it the row type production uses.


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Answers like a CompatConnection: one row, Postgres-shaped."""

    def __init__(self, row):
        self._row = row

    def execute(self, sql, params=()):
        return _FakeResult(self._row)

    def close(self):
        pass


def test_seller_dashboard_count_reads_a_postgres_row():
    from services.business_os.marketplace import seller_dashboard

    row = CompatRow(("count",), (4,))
    assert seller_dashboard._count(_FakeConn(row), "SELECT COUNT(*) ...", ()) == 4, (
        "the seller dashboard's count branched on hasattr(row, 'keys') -- true "
        "for BOTH row types -- and raised ValueError('count') on Postgres"
    )
    # "Unavailable" must still be distinguishable from zero.
    assert seller_dashboard._count(_FakeConn(None), "SELECT 1", ()) is None
    assert seller_dashboard._count(_FakeConn(CompatRow(("c",), (0,))), "x", ()) == 0


def test_premium_crypto_access_reads_a_postgres_row(monkeypatch):
    from services import premium_crypto_access

    columns = premium_crypto_access._USER_COLUMNS
    values = tuple(range(len(columns)))
    monkeypatch.setattr(
        premium_crypto_access.db, "connect",
        lambda *a, **k: _FakeConn(CompatRow(columns, values)),
    )
    loaded = premium_crypto_access.load_user_row(1)
    assert loaded == dict(zip(columns, values)), (
        f"load_user_row built {loaded!r}; on Postgres it used to map every "
        "column name to itself, and the legacy premium gate then raised on it "
        "and answered False for every member"
    )


# ---------------------------------------------------------------------------
# 4. The source-level lock
# ---------------------------------------------------------------------------

# ``tuple(x)`` / ``list(x)`` where x is a row-ish name. Deliberately broad: a
# false positive costs one entry on the allowlist below, a false negative ships
# to production and cannot be caught by any test in this repository.
_DANGEROUS = re.compile(r"\b(?:list|tuple)\(\s*(row|r|rec|record|_row)\s*\)")

_SCAN_ROOTS = ("services", "pulse_communications_v2")
_SCAN_FILES = ("bot.py",)

# Sites that iterate a row wholesale and are NOT bugs, each with the reason.
#
# Keyed by (file, enclosing function) rather than by file. A whole-file entry
# would have been the softer bug of the two: ``bot.py`` earns its exemption from
# ONE guarded helper, and exempting the file would have left the other 130k
# lines -- including the Messenger dedup this very PR fixes -- unwatched.
#
# Adding an entry is a claim that must hold on Postgres, not on SQLite.
_ALLOWED = {
    # Defines row_values. Its docstring quotes the idiom it replaces, and its
    # last line -- ``return tuple(row)``, reached only once the Mapping cases
    # are handled -- IS the safe implementation.
    ("services/db.py", "row_values"),
    # Mapping-first: ``dict(row)`` succeeds for CompatRow, and sqlite3.Row is
    # not a Mapping so it raises TypeError and reaches this fallback -- which is
    # correct for a sequence. The dangerous arm is unreachable on Postgres.
    ("services/private_office/security.py", "_security_row"),
    # Key-access first (``{k: row[k] for k in keys}``) works on BOTH row types,
    # so the zip fallback is unreachable in practice.
    ("services/business_os/entitlements/premium.py", "identity_row"),
    ("services/business_os/entitlements/premium_api.py", "_canonical_subscription"),
    # Guarded by ``isinstance(row, sqlite3.Row)``, which -- unlike
    # ``hasattr(row, "keys")`` -- actually distinguishes the two types.
    ("bot.py", "admin_safe_count"),
    # SQLite-only path (PRAGMA table_info), and it says so at the call site.
    ("pulse_communications_v2/service.py", "_inspect_index_sqlite"),
}

_DEF = re.compile(r"^\s*def\s+(\w+)")


def _code_hits(line: str) -> bool:
    """True when the line uses the idiom as CODE rather than talking about it.

    Explaining this bug requires naming it, and the docstrings that do the
    explaining sit inside the very functions that were fixed. Flagging those
    would leave a maintainer two bad options: delete the explanation, or
    allowlist a function whose code is fine -- and a stale allowlist entry is
    how a gate starts lying. Backticks are the discriminator: prose in this
    repository writes ``tuple(row)``, and no expression can be preceded by one.
    """
    for match in _DANGEROUS.finditer(line):
        if match.start() == 0 or line[match.start() - 1] != "`":
            return True
    return False


def _enclosing_function(lines, index):
    """The nearest ``def`` at or above ``index`` (0-based), or "" at module
    scope. Purely line-based on the same ``splitlines()`` the scan uses: mixing
    in ``ast`` line numbers would reintroduce the U+2028 offset trap, since
    ``str.splitlines`` splits on it and ``ast`` does not."""
    for cursor in range(index, -1, -1):
        match = _DEF.match(lines[cursor])
        if match:
            return match.group(1)
    return ""


def _python_files():
    for name in _SCAN_FILES:
        path = REPO / name
        if path.exists():
            yield path
    for root in _SCAN_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_the_scan_actually_reaches_the_code():
    """A scanner whose file list quietly empties passes forever and protects
    nothing. Require it to still see the monolith and a realistic module count."""
    scanned = [path.relative_to(REPO).as_posix() for path in _python_files()]
    assert "bot.py" in scanned, "the monolith dropped out of the scan"
    assert len(scanned) > 200, (
        f"only {len(scanned)} files scanned; the roots {_SCAN_ROOTS} look wrong"
    )


def _occurrences():
    """Every (file, function, line) where a row is iterated wholesale."""
    found = []
    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _DANGEROUS.search(source):
            continue
        relative = path.relative_to(REPO).as_posix()
        lines = source.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            # Prose about the bug is not the bug; the explanatory comments left
            # at each fixed site would otherwise report themselves.
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            if _code_hits(line):
                found.append((relative, _enclosing_function(lines, index), index + 1))
    return found


def test_no_new_code_iterates_a_row_wholesale():
    offenders = [
        hit for hit in _occurrences() if (hit[0], hit[1]) not in _ALLOWED
    ]
    assert not offenders, (
        "tuple(row)/list(row) means the column VALUES on SQLite and the column "
        "NAMES on Postgres, so these lines are correct in the test suite and "
        "wrong in production:\n"
        + "\n".join(
            f"  {name}:{line} in {func or '<module>'}()"
            for name, func, line in sorted(offenders)
        )
        + "\nUse services.db.row_values(row), or index positionally with row[0]. "
        "If the site is genuinely safe, add (file, function) to _ALLOWED with "
        "the reason it is safe ON POSTGRES."
    )


def test_the_allowlist_has_not_gone_stale():
    """An allowlist entry that no longer matches anything is a lie about the
    code, and the next reader will trust it. Require every entry to still name a
    real occurrence."""
    live = {(name, func) for name, func, _ in _occurrences()}
    stale = sorted(entry for entry in _ALLOWED if entry not in live)
    assert not stale, (
        "these _ALLOWED entries no longer describe the code -- the file moved, "
        "the function was renamed, or the idiom is already gone -- and should "
        "be deleted:\n"
        + "\n".join(f"  {name}: {func}()" for name, func in stale)
    )


def test_the_scanner_can_actually_fail():
    """A grep-based lock that matches nothing passes forever. Prove the regex
    fires on the exact line that took comm_v2 down."""
    assert _code_hits('    groups = int(list(row)[0] or 0)')
    assert _code_hits('    return dict(zip(cols, tuple(row)))')
    assert not _code_hits('    groups = int(row[0] or 0)')
    assert not _code_hits('    return dict(zip(cols, row_values(row)))')
    # Prose is exempt, but only prose: a real call on a line that ALSO quotes
    # the idiom must still be caught, or the exemption becomes a hiding place.
    assert not _code_hits('    so ``tuple(row)`` yields the column names.')
    assert _code_hits('    x = tuple(row)  # unlike ``tuple(row)`` in prose')


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
