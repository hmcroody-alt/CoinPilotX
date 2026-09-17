"""SQLite's scalar ``MAX(a, b)`` must reach Postgres as ``GREATEST(a, b)``.

## The defect this pins

SQLite has a two-argument scalar ``MAX``/``MIN``. Postgres does not: its
``MAX``/``MIN`` are one-argument aggregates, and the scalar spelling is
``GREATEST``/``LEAST``. A statement written in the repo's SQLite dialect
therefore raises ``function max(integer, integer) does not exist`` against the
deployment engine.

``pulse_communications_v2/service.py::mark_read`` carries two of them:

    UPDATE comm_v2_participants SET last_read_message_id=MAX(COALESCE(last_read_message_id,0),?), ...
      AND m.id>MAX(COALESCE(comm_v2_participants.last_read_message_id,0),?) ...

Production logs for 2026-09-17 show this firing on essentially every
``GET /api/pulse/communications/v2/conversations/<id>/messages`` request, then
being swallowed into ``COMM_V2_READ_STATE_DEFERRED``. The request still returned
200, so nothing surfaced: read state and ``unread_count`` simply never advanced
in production for that path.

Local runs and CI could not see it. The translator only rewrites for Postgres,
and the whole suite runs on SQLite, where the two-argument form is valid and the
statement does exactly what it says.

## What is asserted, and why in these forms

A behavioural test is worthless here -- on SQLite the pre-fix statement passes,
so the assertions are on the *generated SQL* instead.

``ScalarMaxMinRewriteTests`` unit-tests the rewriter directly, including the
negatives that make it safe to run over every statement in the repo: the
one-argument aggregate stays an aggregate (``MAX(COALESCE(amount, 14.99))`` has
a comma, but it belongs to the inner call), and text inside string literals,
quoted identifiers and comments is not SQL and is left alone.

``MarkReadQueryTests`` is the specific regression: it lifts the real literal out
of ``pulse_communications_v2/service.py`` with ``ast`` and asserts the translated
form has no two-argument ``MAX`` left. The caveat is the usual one -- this proves
the statement translates, not that the route around it is wired correctly.

``RepoWideScalarMaxMinTests`` generalises the property over every SQL literal in
``bot.py``, ``services/`` and ``pulse_communications_v2/``, so the next query
written in the SQLite dialect fails here rather than in production. That scan
found 14 call sites across 7 files when this was written -- the defect was never
specific to the messenger, which is why the fix belongs in the translator rather
than at the call site.

``TranslationGateTests`` pins the gate in both directions. GREATEST does not
exist in SQLite, so translating unconditionally would break every local run and
the entire test suite; leaving the gate stuck off is the shipped bug.
"""

import ast
import os
import re
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")
COMM_V2_SERVICE_PATH = os.path.join(REPO_ROOT, "pulse_communications_v2", "service.py")

import services.db as db  # noqa: E402
from services.db import _rewrite_scalar_max_min as rewrite  # noqa: E402

SQL_START = re.compile(r"\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)
CALL = re.compile(r"(MAX|MIN)\s*\(", re.I)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _two_arg_sites(sql):
    """Every two-or-more-argument ``MAX``/``MIN`` call in ``sql``, as source
    fragments.

    Deliberately a second implementation rather than a call into
    ``services.db``: if the detector shared the shipped parser, a parser that
    miscounted arguments would hide its own defect from this scan. Quoted text
    and comments are skipped because they are not SQL -- without that, the
    ``'MAX(a,b)'`` in this module's own test data would convict the repo.
    """
    sites = []
    for match in CALL.finditer(sql):
        start = match.start()
        if start and (sql[start - 1].isalnum() or sql[start - 1] == "_"):
            continue
        if _is_quoted_or_commented(sql, start):
            continue
        depth = 0
        args = 1
        index = match.end() - 1
        in_single = False
        while index < len(sql):
            char = sql[index]
            if in_single:
                if char == "'":
                    in_single = False
            elif char == "'":
                in_single = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            elif char == "," and depth == 1:
                args += 1
            index += 1
        else:
            continue
        if args > 1:
            sites.append(sql[start:index + 1])
    return sites


def _is_quoted_or_commented(sql, position):
    """Whether ``position`` falls inside a string literal, a quoted identifier
    or a comment, by replaying the statement from the start."""
    in_single = in_double = in_line = in_block = False
    index = 0
    while index < position:
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < len(sql) else ""
        if in_line:
            if char == "\n":
                in_line = False
            index += 1
            continue
        if in_block:
            if char == "*" and nxt == "/":
                in_block = False
                index += 2
                continue
            index += 1
            continue
        if not in_single and not in_double:
            if char == "-" and nxt == "-":
                in_line = True
                index += 2
                continue
            if char == "/" and nxt == "*":
                in_block = True
                index += 2
                continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        index += 1
    return in_single or in_double or in_line or in_block


def _docstring_node_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                first = node.body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    ids.add(id(first))
    return ids


def _sql_literals_in(source):
    """Every SQL string literal in one module's source, as ``(lineno, sql)``.

    Docstrings are excluded for the reason ``test_sql_placeholder_translation``
    documents: ``SQL_START`` only checks the opening keyword, and prose that
    opens on a SQL verb satisfies the rest of the false positive on its own.
    """
    tree = ast.parse(source)
    docstrings = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if SQL_START.match(node.value):
                yield node.lineno, node.value


def _mark_read_watermark_sql():
    """The watermark UPDATE that ``mark_read`` executes, read from source.

    Importing the module would drag in the service's dependencies; the repo's
    existing translation tests read SQL out of source for the same reason.
    """
    tree = ast.parse(_read(COMM_V2_SERVICE_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "mark_read":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if "UPDATE comm_v2_participants SET last_read_message_id" in inner.value:
                        return inner.value
    raise AssertionError("mark_read's watermark UPDATE was not found in pulse_communications_v2/service.py")


class ScalarMaxMinRewriteTests(unittest.TestCase):
    def test_two_argument_max_becomes_greatest(self):
        self.assertEqual(rewrite("SELECT MAX(a, b) FROM t"), "SELECT GREATEST(a, b) FROM t")

    def test_two_argument_min_becomes_least(self):
        self.assertEqual(rewrite("SELECT MIN(a, b) FROM t"), "SELECT LEAST(a, b) FROM t")

    def test_three_argument_call_is_rewritten_too(self):
        self.assertEqual(rewrite("SELECT MAX(a, b, c) FROM t"), "SELECT GREATEST(a, b, c) FROM t")

    def test_lowercase_call_is_rewritten(self):
        self.assertEqual(rewrite("SELECT max(a, b) FROM t"), "SELECT GREATEST(a, b) FROM t")

    def test_single_argument_aggregate_is_left_alone(self):
        sql = "SELECT MAX(id) FROM t GROUP BY user_id"
        self.assertEqual(rewrite(sql), sql)

    def test_aggregate_over_a_two_argument_coalesce_is_left_alone(self):
        """The comma belongs to COALESCE, so this is still the aggregate.

        Lifted from bot.py -- a rewriter that counted commas without tracking
        nesting would turn a working aggregate into a syntax error.
        """
        sql = "SELECT user_id, MAX(COALESCE(amount, 14.99)) AS latest_amount FROM t GROUP BY user_id"
        self.assertEqual(rewrite(sql), sql)

    def test_aggregate_nested_inside_coalesce_is_left_alone(self):
        sql = "SELECT COALESCE(MAX(id),0) AS max_id FROM comm_v2_messages WHERE conversation_id=?"
        self.assertEqual(rewrite(sql), sql)

    def test_call_inside_a_string_literal_is_left_alone(self):
        sql = "SELECT 'MAX(a,b)', MAX(x) FROM t"
        self.assertEqual(rewrite(sql), sql)

    def test_call_inside_a_quoted_identifier_is_left_alone(self):
        sql = 'SELECT "MAX(a,b)", MIN(y) FROM t'
        self.assertEqual(rewrite(sql), sql)

    def test_call_inside_a_comment_is_left_alone(self):
        sql = "SELECT MAX(x) FROM t -- MAX(a,b) was the old spelling\n"
        self.assertEqual(rewrite(sql), sql)

    def test_call_inside_a_block_comment_is_left_alone(self):
        sql = "SELECT /* MAX(a,b) */ MAX(x) FROM t"
        self.assertEqual(rewrite(sql), sql)

    def test_a_name_ending_in_max_is_not_a_max_call(self):
        sql = "SELECT my_max(a,b), running_min(c,d) FROM t"
        self.assertEqual(rewrite(sql), sql)

    def test_unbalanced_parens_leave_the_statement_untouched(self):
        sql = "SELECT MAX(a, b FROM t"
        self.assertEqual(rewrite(sql), sql)

    def test_both_calls_in_one_statement_are_rewritten(self):
        rewritten = rewrite("UPDATE t SET a=MAX(COALESCE(a,0),?), b=MIN(100, b+5)")
        self.assertIn("GREATEST(COALESCE(a,0),?)", rewritten)
        self.assertIn("LEAST(100, b+5)", rewritten)
        self.assertEqual(_two_arg_sites(rewritten), [])

    def test_placeholders_survive_the_rewrite(self):
        """The rewriter runs before ``?`` -> ``%s``; it must not eat a placeholder."""
        sql = "UPDATE t SET a=MAX(COALESCE(a,0),?), b=? WHERE id=?"
        self.assertEqual(rewrite(sql).count("?"), sql.count("?"))


class MarkReadQueryTests(unittest.TestCase):
    def test_watermark_update_has_no_two_argument_max_after_translation(self):
        sql = _mark_read_watermark_sql()
        self.assertEqual(
            _two_arg_sites(rewrite(sql)),
            [],
            "mark_read's watermark UPDATE still reaches Postgres with a two-argument MAX; "
            "psycopg2 will raise 'function max(integer, integer) does not exist' and read "
            "state will silently stop advancing",
        )

    def test_watermark_update_is_still_the_statement_that_broke(self):
        """If the call site is rewritten by hand this test stops testing anything."""
        sql = _mark_read_watermark_sql()
        self.assertTrue(
            _two_arg_sites(sql),
            "the source literal no longer contains a two-argument MAX -- if the call site "
            "was fixed directly, drop this test rather than letting it pass vacuously",
        )
        self.assertIn("GREATEST(COALESCE(last_read_message_id,0),?)", rewrite(sql))
        self.assertIn("GREATEST(COALESCE(comm_v2_participants.last_read_message_id,0),?)", rewrite(sql))


class TranslationGateTests(unittest.TestCase):
    def test_sqlite_leaves_the_statement_alone(self):
        """GREATEST does not exist in SQLite; translating there breaks every local run."""
        sql = "SELECT MAX(a, b) FROM t"
        with mock.patch.object(db, "IS_POSTGRES", False):
            self.assertEqual(db._translate_scalar_max_min(sql), sql)

    def test_postgres_rewrites_the_statement(self):
        sql = "SELECT MAX(a, b) FROM t"
        with mock.patch.object(db, "IS_POSTGRES", True):
            self.assertEqual(db._translate_scalar_max_min(sql), "SELECT GREATEST(a, b) FROM t")

    def test_full_translation_rewrites_and_still_binds_placeholders(self):
        """``_translate_sql`` is the path every statement actually takes."""
        sql = "UPDATE t SET a=MAX(COALESCE(a,0),?) WHERE id=?"
        with mock.patch.object(db, "IS_POSTGRES", True):
            translated = db._translate_sql(sql)
        self.assertIn("GREATEST(COALESCE(a,0),%s)", translated)
        self.assertEqual(translated.count("%s"), 2)
        self.assertEqual(_two_arg_sites(translated), [])


class RepoWideScalarMaxMinTests(unittest.TestCase):
    def _python_sources(self):
        paths = [BOT_PATH]
        for package in ("services", "pulse_communications_v2"):
            for directory, _, names in os.walk(os.path.join(REPO_ROOT, package)):
                paths.extend(
                    os.path.join(directory, name)
                    for name in sorted(names)
                    if name.endswith(".py")
                )
        return paths

    def _sql_literals(self):
        for path in self._python_sources():
            try:
                literals = list(_sql_literals_in(_read(path)))
            except SyntaxError:
                continue
            for lineno, sql in literals:
                yield path, lineno, sql

    def test_no_sql_literal_reaches_postgres_with_a_two_argument_max(self):
        offenders = []
        for path, lineno, sql in self._sql_literals():
            for site in _two_arg_sites(rewrite(sql)):
                offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{lineno} {site[:60]}")
        self.assertEqual(
            offenders,
            [],
            "these statements keep a two-argument MAX/MIN after translation, which Postgres "
            "rejects as 'function max(integer, integer) does not exist': " + "; ".join(offenders),
        )

    def test_the_scan_is_reading_real_statements(self):
        """A collector that silently matched nothing would leave the scan green.

        The untranslated repo has to contain the SQLite spelling, otherwise the
        test above is asserting over an empty set.
        """
        untranslated = [
            f"{os.path.relpath(path, REPO_ROOT)}:{lineno}"
            for path, lineno, sql in self._sql_literals()
            if _two_arg_sites(sql)
        ]
        self.assertTrue(
            untranslated,
            "no SQL literal in the repo uses the two-argument MAX/MIN spelling any more; "
            "this scan now proves nothing and should be re-pointed or removed",
        )

    def test_detector_catches_a_known_bad_query(self):
        """The production statement's shape, pinned so the detector cannot be neutered."""
        sql = "UPDATE p SET last_read_message_id=MAX(COALESCE(last_read_message_id,0),?) WHERE id=?"
        self.assertEqual(len(_two_arg_sites(sql)), 1)
        self.assertEqual(_two_arg_sites(rewrite(sql)), [])

    def test_detector_does_not_convict_a_plain_aggregate(self):
        self.assertEqual(_two_arg_sites("SELECT MAX(COALESCE(amount, 14.99)) FROM t"), [])


if __name__ == "__main__":
    unittest.main()
