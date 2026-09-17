"""``ON CONFLICT DO UPDATE SET`` must table-qualify the row it reads from.

## The defect this pins

``POST /api/arena/reputation`` (``bot.py::api_arena_reputation``) ran::

    INSERT INTO arena_reputation (...) VALUES (?, 50, 50, 50, 50, 55, 50, ?)
    ON CONFLICT(user_id) DO UPDATE SET sportsmanship=MIN(100, sportsmanship+1), ...

Inside ``DO UPDATE SET``, Postgres has two rows in scope: the conflicting table
row, and the rejected row under the name ``excluded``. A bare ``sportsmanship``
on the right-hand side could mean either, so Postgres refuses it with ``column
reference "sportsmanship" is ambiguous``. SQLite resolves the same spelling to
the table row without complaint.

That is a *parse*-time error, which is what makes the blast radius total: the
whole statement is planned before any row is touched, so the failure fires even
on the first-ever insert, where the ``DO UPDATE`` branch would never run. The
seed row was never written either. ``arena_reputation`` held 0 rows in
production and the endpoint returned 500 on every request from 2026-05-16 until
this fix.

It is also independent of the two-argument ``MIN`` that ``e6cdfd03`` taught the
translator to rewrite. Verified by ``EXPLAIN`` against production PostgreSQL
18.6 in a read-only session:

    sportsmanship=MIN(100, sportsmanship+1)                        -> ambiguous
    sportsmanship=LEAST(100, sportsmanship+1)                      -> ambiguous
    sportsmanship=sportsmanship+1                                  -> ambiguous
    sportsmanship=LEAST(100, arena_reputation.sportsmanship+1)     -> plans
    sportsmanship=arena_reputation.sportsmanship+1                 -> plans

The middle line is the one that matters: the translator alone does not fix this,
and the ambiguity has nothing to do with which function wraps the reference.
Because Postgres reports the ambiguity first, this call site never even reached
the ``MIN`` error that ``e6cdfd03`` describes.

## What is asserted, and why in these forms

The suite runs on SQLite, where the pre-fix statement is valid and does exactly
what it reads like. So, as with the sibling ``MAX``/``MIN`` translation test,
the assertions are on the *source SQL* rather than on Postgres behaviour.

``AmbiguityDetectorTests`` pins the detector before it is pointed at the repo.
The negative controls are the load-bearing ones: an early draft of this scanner
counted the table qualifier in ``MIN(100, t.s+1)`` as itself a bare column
reference and convicted the fixed statement.

``ArenaReputationStatementTests`` lifts the real literal out of ``bot.py`` with
``ast`` and asserts it is qualified, that it keeps the SQLite two-argument
``MIN`` spelling the translator expects, and that the translated Postgres form
is ``LEAST(100, arena_reputation.sportsmanship+1)`` -- i.e. that the two fixes
compose rather than cancel.

``SqliteBehaviourTests`` runs that same lifted literal on SQLite, because
qualifying a column is only a safe fix if SQLite accepts the spelling and the
counter still seeds, increments and saturates as before.

``RepoWideUpsertTests`` generalises the property over every SQL literal in
``bot.py`` and ``services/``, so the next upsert written this way fails here
instead of in production. That scan found exactly one call site when this was
written.
"""

import ast
import os
import re
import sqlite3
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")

import services.db as db  # noqa: E402
from services.db import _rewrite_scalar_max_min as rewrite  # noqa: E402

# Identifiers that may legitimately appear bare on the right-hand side: SQL
# keywords, type names and function names. Anything else bare is a column of the
# target table, and therefore ambiguous against ``excluded``.
SQL_WORDS = frozenset("""
    excluded null true false current_timestamp current_date now case when then else end
    and or not is in like between cast as interval distinct select from where on conflict
    do update set values insert into returning nothing default
    integer text real numeric boolean timestamp jsonb json blob varchar char bigint serial
    min max least greatest coalesce nullif abs round ifnull length lower upper substr
    replace trim sum count avg concat array string_agg group_concat strftime datetime date
    time julianday unixepoch printf hex random typeof instr
""".split())

# A bare column reference: not preceded by a dot (that would make it the tail of
# ``excluded.col``), not followed by a dot (that would make it a qualifier), and
# not followed by ``(`` (that would make it a function name).
BARE_IDENT = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\b(?!\s*[.(])")

DO_UPDATE_SET = re.compile(r"DO\s+UPDATE\s+SET\s+", re.I)
CLAUSE_END = re.compile(r"\bWHERE\b|\bRETURNING\b|;", re.I)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _sql_literals(path):
    """Every string constant in ``path`` that contains an upsert, with its line.

    Lifted with ``ast`` rather than by grepping a window around a match: a
    character window bleeds past the closing quote into surrounding Python and
    reports prose as SQL.
    """
    tree = ast.parse(_read(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if DO_UPDATE_SET.search(node.value):
                yield node.lineno, node.value


def _split_assignments(clause):
    """Split a ``SET`` clause on its top-level commas, ignoring commas inside
    parentheses (function arguments) and string literals."""
    parts, current, depth, in_single = [], [], 0, False
    for char in clause:
        if in_single:
            current.append(char)
            if char == "'":
                in_single = False
            continue
        if char == "'":
            in_single = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _ambiguous_assignments(sql):
    """Every ``DO UPDATE SET col=<expr>`` in ``sql`` whose expression reads a
    column without saying which row it means.

    Returns ``(lhs, rhs, bare_identifiers)`` triples.
    """
    found = []
    for match in DO_UPDATE_SET.finditer(sql):
        tail = sql[match.end():]
        stop = CLAUSE_END.search(tail)
        clause = tail[:stop.start()] if stop else tail
        for assignment in _split_assignments(clause):
            lhs, sep, rhs = assignment.partition("=")
            if not sep:
                continue
            # String literals are data, not column references.
            without_strings = re.sub(r"'[^']*'", "''", rhs)
            bare = [name for name in BARE_IDENT.findall(without_strings)
                    if name.lower() not in SQL_WORDS]
            if bare:
                found.append((lhs.strip(), rhs.strip(), bare))
    return found


def _arena_reputation_statement():
    for _, literal in _sql_literals(BOT_PATH):
        if "INSERT INTO arena_reputation" in literal:
            return literal
    raise AssertionError("arena_reputation upsert not found in bot.py")


class AmbiguityDetectorTests(unittest.TestCase):
    """The detector, before it is trusted against the repo."""

    def test_flags_a_bare_column_reference(self):
        sql = ("INSERT INTO t (a) VALUES (1) ON CONFLICT(a) DO UPDATE SET "
               "s=MIN(100, s+1), u=excluded.u")
        self.assertEqual([("s", "MIN(100, s+1)", ["s"])], _ambiguous_assignments(sql))

    def test_ignores_a_table_qualified_reference(self):
        sql = ("INSERT INTO t (a) VALUES (1) ON CONFLICT(a) DO UPDATE SET "
               "s=MIN(100, t.s+1), u=excluded.u")
        self.assertEqual([], _ambiguous_assignments(sql))

    def test_ignores_excluded_and_literals_and_functions(self):
        sql = ("INSERT INTO t (a) VALUES (1) ON CONFLICT(a) DO UPDATE SET "
               "s=excluded.s, n=42, w='some bare words here', d=CURRENT_TIMESTAMP, "
               "c=COALESCE(excluded.c, 0)")
        self.assertEqual([], _ambiguous_assignments(sql))

    def test_stops_at_the_where_clause(self):
        """``WHERE`` ends the SET clause; its columns are unambiguous there."""
        sql = ("INSERT INTO t (a) VALUES (1) ON CONFLICT(a) DO UPDATE SET "
               "s=excluded.s WHERE t.s < 100")
        self.assertEqual([], _ambiguous_assignments(sql))


class ArenaReputationStatementTests(unittest.TestCase):
    """The specific regression."""

    def setUp(self):
        self.sql = _arena_reputation_statement()

    def test_the_statement_is_unambiguous(self):
        self.assertEqual([], _ambiguous_assignments(self.sql))

    def test_the_read_is_table_qualified(self):
        self.assertIn("MIN(100, arena_reputation.sportsmanship+1)", self.sql)

    def test_source_keeps_the_sqlite_two_argument_spelling(self):
        """The repo dialect is SQLite; ``LEAST`` is the translator's job."""
        self.assertNotIn("LEAST", self.sql.upper())

    def test_postgres_form_is_qualified_least(self):
        """The qualifier survives the ``MIN`` -> ``LEAST`` rewrite."""
        self.assertIn("LEAST(100, arena_reputation.sportsmanship+1)", rewrite(self.sql))

    def test_translation_gate_leaves_sqlite_alone(self):
        original = db.IS_POSTGRES
        try:
            db.IS_POSTGRES = False
            self.assertEqual(self.sql, db._translate_scalar_max_min(self.sql))
            db.IS_POSTGRES = True
            self.assertIn("LEAST(100, arena_reputation.sportsmanship+1)",
                          db._translate_scalar_max_min(self.sql))
        finally:
            db.IS_POSTGRES = original


class SqliteBehaviourTests(unittest.TestCase):
    """Qualifying the column must not change what the counter does locally."""

    def setUp(self):
        self.sql = _arena_reputation_statement()
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE arena_reputation ("
            "user_id INTEGER PRIMARY KEY, discipline INTEGER, helpfulness INTEGER, "
            "leadership INTEGER, scam_defense INTEGER, sportsmanship INTEGER, "
            "consistency INTEGER, updated_at TEXT)"
        )

    def tearDown(self):
        self.conn.close()

    def _bump(self, times):
        for index in range(times):
            self.conn.execute(self.sql, (7, "t%d" % index))

    def _sportsmanship(self):
        return self.conn.execute(
            "SELECT sportsmanship FROM arena_reputation WHERE user_id=7").fetchone()[0]

    def test_first_call_seeds_the_row(self):
        self._bump(1)
        self.assertEqual(55, self._sportsmanship())

    def test_subsequent_calls_increment(self):
        self._bump(3)
        self.assertEqual(57, self._sportsmanship())

    def test_the_counter_saturates_at_one_hundred(self):
        self._bump(60)
        self.assertEqual(100, self._sportsmanship())

    def test_updated_at_comes_from_the_rejected_row(self):
        self._bump(2)
        row = self.conn.execute(
            "SELECT updated_at FROM arena_reputation WHERE user_id=7").fetchone()
        self.assertEqual("t1", row[0])


class RepoWideUpsertTests(unittest.TestCase):
    """No other upsert may read a column without naming its row."""

    def test_no_ambiguous_do_update_set_in_backend_sources(self):
        paths = [BOT_PATH]
        for base, dirs, names in os.walk(os.path.join(REPO_ROOT, "services")):
            dirs[:] = [name for name in dirs if name != "__pycache__"]
            paths.extend(os.path.join(base, name)
                         for name in names if name.endswith(".py"))

        offenders = []
        for path in paths:
            try:
                literals = list(_sql_literals(path))
            except (SyntaxError, ValueError):
                continue
            for lineno, literal in literals:
                for lhs, rhs, bare in _ambiguous_assignments(literal):
                    offenders.append(
                        "%s:%d SET %s=%s (bare: %s)"
                        % (os.path.relpath(path, REPO_ROOT), lineno, lhs, rhs,
                           ", ".join(bare))
                    )
        self.assertEqual(
            [], offenders,
            "ambiguous ON CONFLICT DO UPDATE SET; qualify the read with the "
            "table name:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
