"""The database contract: does the schema our logic depends on actually exist?

THE FAILURE THIS EXISTS TO PREVENT, in full:

    try:
        cur.execute("SELECT 1 FROM private_chat_blocks WHERE ...")
        if cur.fetchone():
            return deny
    except Exception:
        pass

``private_chat_blocks`` had no CREATE TABLE, no migration, no writer and no row
in production. The read raised "no such table" on every request, the bare
``except`` swallowed it, and the block check had never once denied anything --
with no log line anywhere. The check reads correctly, names a plausible table,
passes review, and passes tests. The only way to find it is to ask whether the
table exists.

So this asks. Every table named by an executed statement in runtime code must
resolve to a recognised schema authority. Schema authority here is genuinely
distributed -- there is no migration framework, ``bot.init_db()`` creates ~536
tables imperatively and another ~380 come from ``services/**/schema.py``,
per-service ``ensure_schema`` functions and ``pulse_communications_v2`` -- so
the contract recognises all of them and names which one answered.

Three things this deliberately does NOT do:

  * It does not grep. The unit of analysis is a call to ``.execute()`` whose
    first argument resolves to a readable string, reached through an AST walk.
    Text scanning finds 12,877 "tables" in this repo, the most frequent of
    which are the English words "set", "the" and "your". Structural analysis
    finds 6,575 real references. More importantly, ``bot.py`` today contains
    the literal string ``private_chat_blocks`` inside a comment explaining the
    fix -- a text guard would report a closed defect as open.

  * It does not require the table to exist in production. 14 tables are created
    lazily by ``CREATE TABLE IF NOT EXISTS`` on a path that has not run yet;
    requiring prod presence would raise 14 false alarms. Declaration is the
    contract; prod is a separate read-only check.

  * It does not check columns. That was measured rather than assumed -- see
    ``test_column_contract_scope_is_deliberate`` below.
"""
from __future__ import annotations

import json
import os
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _runner import run_module_tests  # noqa: E402
from db_contract import contract, sqlscan  # noqa: E402

ALLOWLIST = os.path.join(ROOT, "config", "db_contract_allowlist.json")

_ANALYSIS = []


def analysis():
    """One pass over the repo, shared by every test in this file.

    The walk costs ~18s, dominated by ``ast.parse`` on bot.py (8.5 MB). Doing
    it once per module keeps the whole file inside a normal CI step.

    This is a cached function rather than a pytest fixture on purpose. The
    protection runner executes each suite as a plain script
    (``python tests/protection/<file>.py``) and asserts a non-zero check count;
    a file whose tests only run under pytest would report zero there, which is
    precisely the "green signal that measures nothing" failure the runner was
    rebuilt to make impossible.
    """
    if not _ANALYSIS:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _ANALYSIS.append(contract.analyse(ROOT, ALLOWLIST))
    return _ANALYSIS[0]


# ---------------------------------------------------------------------------
# The contract (brief section 5)
# ---------------------------------------------------------------------------

def test_every_runtime_table_reference_resolves_to_a_schema_authority():
    report, _allowlist = analysis()
    if not report.findings:
        return
    lines = [
        "",
        f"{len(report.findings)} runtime SQL reference(s) name a table with no "
        f"recognised schema authority.",
        "",
        "A query against a table that does not exist raises at runtime. If it "
        "sits inside a broad except, the statement is a permanent no-op that "
        "still reads correctly in review.",
        "",
    ]
    for finding in sorted(report.findings,
                          key=lambda f: (f.severity, f.reference.location)):
        lines.append(finding.render())
        lines.append("")
    lines.append(
        "If the reference is correct and static analysis simply cannot prove "
        "it, add an entry to config/db_contract_allowlist.json under 'tables' "
        "with a reason, an owner and why it is unprovable.")
    raise AssertionError("\n".join(lines))


def test_scan_actually_found_the_codebase():
    """A gate that silently analyses nothing passes forever.

    If a refactor moves runtime code out from under ``scope_of``, or an import
    breaks the walk, the contract test above would go green by finding no
    references at all. These floors are far below current values (2,617 files /
    6,575 references / 916 tables) and exist only to catch a scan that died.
    """
    report, _allowlist = analysis()
    assert report.files_scanned > 2000, report.files_scanned
    assert len(report.references) > 5000, len(report.references)
    assert len(report.declarations) > 800, len(report.declarations)


# ---------------------------------------------------------------------------
# The closed defect must stay closed (brief section 12)
# ---------------------------------------------------------------------------

def test_retired_objects_are_not_reintroduced():
    report, _allowlist = analysis()
    retired = [f for f in report.findings
               if f.kind in ("retired_table", "retired_column")]
    assert not retired, "\n".join([""] + [f.render() for f in retired])


def test_the_historical_defect_is_caught():
    """Brief section 28: the exact defect, reproduced, must be caught.

    This is the acceptance test for the whole sentinel. The fixture is the
    original shape -- a security check reading a table that has no schema
    authority, with the error swallowed -- and it is held in a string rather
    than a file so that reproducing it cannot reintroduce it.
    """
    _report, allowlist = analysis()
    source = '''
def user_is_blocked(cur, viewer_id, author_id):
    """Deny if either party has blocked the other."""
    try:
        cur.execute(
            "SELECT 1 FROM private_chat_blocks "
            "WHERE blocker_user_id = ? AND blocked_user_id = ? LIMIT 1",
            (author_id, viewer_id),
        )
        if cur.fetchone():
            return True
    except Exception:
        pass
    return False
'''
    facts = sqlscan.scan_source("services/_fixture_blocking.py", source)
    tables = {ref.table for ref in facts.references}
    assert "private_chat_blocks" in tables, (
        "the extractor did not even see the table reference; the sentinel "
        f"cannot catch what it cannot parse. saw: {sorted(tables)}")

    retired = allowlist.retired_table("private_chat_blocks")
    assert retired, (
        "private_chat_blocks is not recorded as retired, so reintroducing it "
        "would only be caught while it happens to be undeclared")

    # Belt and braces: it must also remain undeclared, so that the undeclared
    # path would catch it even if the retired list were edited away.
    assert "private_chat_blocks" not in set(_report.declarations), (
        "something now declares private_chat_blocks; the table was retired and "
        "must not come back")

    # And the live repo must not reference it at all.
    assert "private_chat_blocks" not in {r.table for r in _report.references}, (
        "runtime code references private_chat_blocks again")


def test_retired_columns_are_matched_structurally_not_textually():
    """The section 35 trap, as a test.

    ``bot.py`` contains the string ``private_chat_blocks`` in a comment that
    explains the fix. A guard that greps file text reports a closed defect as
    open, and the obvious "fix" is to delete the explanation. So the rule runs
    against SQL resolved from an execute() argument, with comments and quoted
    literals blanked. This proves both halves.
    """
    _report, allowlist = analysis()
    assert allowlist.retired_columns, "no retired columns configured"

    # A comment naming the retired column must NOT match.
    benign = '''
def load(cur, uid):
    cur.execute(
        "-- historical note: users.message_privacy was removed; see blocked_users\\n"
        "SELECT id, username FROM users WHERE id = ?",
        (uid,),
    )
'''
    facts = sqlscan.scan_source("services/_fixture_comment.py", benign)
    for stmt in facts.statements:
        assert "users.message_privacy" not in stmt.qualified, (
            "a retired column named inside a SQL comment was treated as a real "
            "reference; this gate would fail on its own documentation")

    # A real read of it must match.
    real = '''
def load(cur, uid):
    cur.execute("SELECT message_privacy FROM users WHERE id = ?", (uid,))
'''
    facts = sqlscan.scan_source("services/_fixture_real.py", real)
    hit = any(stmt.tables == ("users",) and "message_privacy" in stmt.identifiers
              for stmt in facts.statements)
    assert hit, "a genuine read of the retired column was not detected"


# ---------------------------------------------------------------------------
# UNKNOWN must not silently pass (brief section 7)
# ---------------------------------------------------------------------------

def test_an_undeclared_table_is_reported_not_skipped():
    """The sentinel must fail on a table it cannot resolve, not shrug.

    The whole class of bug being gated is "the check looked present and did
    nothing". A gate that returns True when it is unsure is the same bug one
    level up.
    """
    source = '''
def check(cur, uid):
    cur.execute("SELECT 1 FROM a_table_that_was_never_created WHERE id = ?", (uid,))
    return bool(cur.fetchone())
'''
    facts = sqlscan.scan_source("services/_fixture_unknown.py", source)
    tables = {ref.table for ref in facts.references}
    assert "a_table_that_was_never_created" in tables


def test_interpolated_ddl_does_not_declare_a_table_named_dynamic():
    """``CREATE TABLE {whatever}`` must not satisfy the contract.

    If an unresolvable interpolation registered as a declaration, one such
    statement anywhere would declare a table literally named ``__dynamic__``
    and nothing would ever resolve against it -- but worse, a sloppy version
    could be made to satisfy arbitrary references.
    """
    source = '''
def ensure(cur, name):
    cur.execute(f"CREATE TABLE IF NOT EXISTS {name} (id INTEGER PRIMARY KEY)")
'''
    facts = sqlscan.scan_source("services/_fixture_ddl.py", source)
    assert not [d for d in facts.declarations
                if d.table == sqlscan.DYNAMIC_MARKER], facts.declarations
    assert facts.unresolved, "an unresolvable CREATE TABLE was silently dropped"


def test_a_docstring_cannot_declare_a_table():
    """Prose that happens to contain DDL is not a schema authority."""
    source = '''
def helper(cur):
    """Historically this created the table:

        CREATE TABLE private_chat_blocks (id INTEGER PRIMARY KEY)

    It no longer does.
    """
    return None
'''
    facts = sqlscan.scan_source("services/_fixture_docstring.py", source)
    assert not facts.declarations, facts.declarations


# ---------------------------------------------------------------------------
# Accountability (brief section 25)
# ---------------------------------------------------------------------------

def test_allowlist_and_known_defects_are_accountable():
    _report, allowlist = analysis()
    problems = allowlist.validate()
    assert not problems, "\n".join([""] + problems)


def test_known_defects_are_real_and_still_present():
    """A known-defect entry that no longer matches anything is stale.

    Once the fix lands the reference is gone, and the entry must be removed in
    the same commit -- otherwise the list grows into a second, unaccountable
    allowlist.
    """
    report, allowlist = analysis()
    still_referenced = {f.reference.table for f in report.known}
    stale = sorted(set(allowlist.defects) - still_referenced)
    assert not stale, (
        f"known_defects entries no longer match any runtime reference: {stale}. "
        f"The defect is fixed -- delete the entry from "
        f"config/db_contract_allowlist.json.")


def test_there_is_no_blanket_disable():
    """No ignore_everything switch (brief section 24)."""
    with open(ALLOWLIST, encoding="utf-8") as handle:
        raw = json.load(handle)
    assert set(raw) <= {"_doc", "tables", "known_defects", "retired_objects"}, (
        f"unexpected top-level key in the allowlist: {sorted(raw)}")
    assert isinstance(raw.get("tables"), list)


# ---------------------------------------------------------------------------
# Column contract: measured, then scoped (brief section 14)
# ---------------------------------------------------------------------------

def test_column_contract_scope_is_deliberate():
    """Why this gate stops at tables, recorded so it is not re-litigated blind.

    Measured against production ``information_schema.columns``, read-only:

        13,454 checkable column writes
           115 flagged as undeclared  (0.85%)
            88 distinct (table, column) pairs
             2 real          -- absent from production
            86 false positive -- the column exists in production

        precision: 2.3%

    The cause is structural, not a parser bug. Column DDL in this repo is
    predominantly *data*, not SQL: 1,089 column declarations across 103 tables
    are expressed as ``("name", "TEXT")`` tuples or ``{"name": "TEXT"}`` dicts
    handed to one of at least nine distinct generic column-ensuring helpers
    (``add_columns_if_missing``, ``_ensure_columns``, ``ensure_columns``,
    ``_add_missing_columns``, ...), several of which receive the table name
    itself as a loop variable. Harvesting that data form was tried; it moved
    precision from 2.1% to 2.3%. A declaration-side parser cannot see the rest
    without executing the code.

    So a global column gate would be 40 false alarms per true one, and the
    existing hand-scoped check -- ``test_schema_declaration_integrity.py``,
    which guards ``pulse_posts`` and ``pulse_reels`` and whose docstring already
    calls itself "deliberately scoped rather than global" -- is the correct
    shape. This test pins that decision and the file it points at.

    The two true positives are reported as defects, not swallowed here.
    """
    sibling = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "test_schema_declaration_integrity.py")
    assert os.path.exists(sibling), (
        "the scoped column check this gate defers to is gone; either restore it "
        "or re-measure whether a global column contract is now viable")


if __name__ == "__main__":
    raise SystemExit(run_module_tests(globals()))
