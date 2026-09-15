"""``?`` placeholders must survive translation to Postgres' ``%s`` paramstyle.

## The defect this pins

`services/db.py::_replace_question_placeholders` rewrites SQLite's `?`
placeholders into psycopg2's `%s` before every statement runs on Postgres. It
skipped `?` inside string literals and quoted identifiers, which is correct, but
it had no notion of SQL comments. An apostrophe inside a `--` comment therefore
read as the opening quote of a string literal that never closed, and every `?`
after it was left untranslated.

`bot.py::pulse_status_active_rows` -- the Status rail query -- carries this
comment:

    -- The Status rail is a discovery surface: it puts other people's faces
    -- on the home screen. ...
    AND (s.user_id=? OR {discovery_visible_sql('u')})
    ORDER BY s.created_at DESC
    LIMIT ?

The apostrophe in `people's` suppressed the last two placeholders: the statement
reached psycopg2 with five `%s` and seven bound parameters, which raises
`not all arguments converted during string formatting`. `GET /api/pulse/status/rail`
returned 500 for every user on every request; the native client surfaced the
route's own error copy as "PulseSoc Status could not load." / "Status unavailable".
Statuses were being created and stored correctly the whole time -- only the read
path was down.

Local runs and CI could not see it. SQLite talks to a raw `sqlite3` connection
that never enters `CompatCursor`, so the translator is a no-op path there; the
defect only exists against the deployment engine.

## What is asserted, and why in these forms

`test_comment_apostrophe_does_not_swallow_placeholders` and its siblings are
direct unit tests of the translator, including the negative cases the old
implementation got right (quoted literals, quoted identifiers, doubled `''`)
so a future rewrite cannot fix comments by breaking strings.

`test_status_rail_sql_translates_every_placeholder` is the specific regression:
it lifts the real f-string out of `bot.py` with `ast`, substitutes the one
interpolated fragment, and asserts the translated `%s` count equals both the
literal `?` count and the length of the parameter tuple `bot.py` actually binds.
`bot.py` cannot be imported (it pulls in `stripe` and builds the Flask app), so
the SQL is read from source; the caveat is the usual one -- this proves the
statement translates, not that the route around it is wired correctly.

`test_no_sql_literal_loses_a_placeholder_to_a_comment` generalises the property
across every SQL string literal in the repo, so the next query with an
apostrophe in a comment fails here rather than in production.

That scan identifies SQL by its opening keyword, which prose can satisfy by
accident: `_create_draft_listing`'s docstring opens "Insert one DRAFT listing.",
uses `--` as a dash and quotes ``quantity>=?``, and the apostrophes in it
suppressed the placeholder exactly as a real defect would. It reported a bug in
a comment. `_sql_literals_in` therefore skips docstrings -- the one string in a
body that Python can never execute as SQL -- and `LiteralCollectorTests` pins
both halves of that: the prose stays out, and the statements around it, plus a
translator that loses a placeholder, still fail the scan.
"""

import ast
import os
import re
import textwrap
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")

from services.db import _replace_question_placeholders as translate  # noqa: E402

SQL_START = re.compile(r"\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _strip_sql_comments(sql):
    """Remove `--` and `/* */` comments. Deliberately naive: used only to
    compute how many `?` a statement *should* bind, and only for statements
    whose quoting the caller has already vetted."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def _docstring_node_ids(tree):
    """`id()` of every string node a module, class or function uses as its
    docstring. `ast.walk` yields a Constant stripped of its parent, so by the
    time the collector sees one, prose and SQL are indistinguishable; the
    parentage has to be recorded before the walk flattens it away."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                first = node.body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    ids.add(id(first))
    return ids


def _sql_literals_in(source):
    """Every SQL string literal in one module's source, as `(lineno, sql)`.

    Docstrings are excluded. `SQL_START` only checks the opening keyword, so a
    docstring that begins "Insert one DRAFT listing." reads as an INSERT, and
    English supplies the rest of the false positive on its own: `--` as a dash,
    an apostrophe in "the merchant's", a `?` inside quoted sample code. Nothing
    Python ever executes as SQL is the first bare string in a body, so dropping
    those costs the scan no coverage -- the statements in the same function,
    assigned or passed to `execute`, are still collected."""
    tree = ast.parse(source)
    docstrings = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if SQL_START.match(node.value):
                yield node.lineno, node.value


def _placeholder_loss(sql):
    """Whether translating `sql` drops a `?` that a comment has hidden from the
    translator. False for literals with nothing at stake: no comment to hide a
    placeholder behind, or no placeholder left once the comments are stripped."""
    if "--" not in sql and "/*" not in sql:
        return False
    expected = _strip_sql_comments(sql).count("?")
    if expected == 0:
        return False
    return translate(sql).count("%s") - sql.count("%s") != expected


# The shape that made the scan cry wolf, reduced from
# `services/business_os/suppliers/importer.py::_create_draft_listing`: prose
# that opens on a SQL verb, uses `--` as a dash, quotes a `?` in sample code,
# and carries the apostrophes that convinced the translator it was inside an
# unterminated string literal. The two statements underneath it are the
# coverage the exclusion must not take with it.
_IMPORTER_SHAPED_SOURCE = textwrap.dedent('''
    """Update a seller's drafts -- how many? as many as the importer made."""


    def _create_draft_listing(cur, seller_user_id):
        """Insert one DRAFT listing. Status and approval are not parameters.

        The merchant's own ``quantity>=?`` decrement guard fails closed against
        NULL, and "import must never publish" is not a default -- it is the
        invariant, so the column stays unset.
        """
        cur.execute(
            "INSERT INTO marketplace_listings (seller_user_id, status) "
            "VALUES (?,'draft')", (seller_user_id,))
        cur.execute(
            "SELECT id FROM marketplace_listings WHERE seller_user_id=? "
            "-- newest first, the merchant's latest draft\\n"
            "ORDER BY id DESC LIMIT ?", (seller_user_id, 1))
''')


def _status_rail_sql():
    """The literal `pulse_status_active_rows` executes, with its one
    interpolation replaced by a placeholder-free stand-in."""
    tree = ast.parse(_read(BOT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "pulse_status_active_rows":
            for inner in ast.walk(node):
                if isinstance(inner, ast.JoinedStr):
                    parts = []
                    for value in inner.values:
                        if isinstance(value, ast.Constant):
                            parts.append(str(value.value))
                        else:
                            parts.append("(1=1)")
                    sql = "".join(parts)
                    if "FROM pulse_status" in sql:
                        return sql, node
    raise AssertionError("pulse_status_active_rows SQL not found in bot.py")


def _status_rail_param_count(node):
    """Length of the parameter tuple bound alongside the rail SQL."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and len(inner.args) == 2:
            params = inner.args[1]
            if isinstance(params, ast.Tuple):
                return len(params.elts)
    raise AssertionError("parameter tuple for the Status rail query not found")


class PlaceholderTranslatorTests(unittest.TestCase):
    def test_comment_apostrophe_does_not_swallow_placeholders(self):
        sql = "SELECT * FROM t WHERE a=?\n-- other people's faces\nAND b=?\nLIMIT ?"
        self.assertEqual(translate(sql).count("%s"), 3)

    def test_block_comment_apostrophe_does_not_swallow_placeholders(self):
        sql = "SELECT ? /* don't do this */, ? FROM t"
        self.assertEqual(translate(sql).count("%s"), 2)

    def test_question_mark_inside_a_comment_is_not_a_placeholder(self):
        sql = "SELECT ? FROM t -- is this right?\n"
        self.assertEqual(translate(sql).count("%s"), 1)

    def test_question_mark_inside_a_string_literal_is_left_alone(self):
        sql = "SELECT 'a?b', ? FROM t"
        translated = translate(sql)
        self.assertEqual(translated.count("%s"), 1)
        self.assertIn("'a?b'", translated)

    def test_question_mark_inside_a_quoted_identifier_is_left_alone(self):
        sql = 'SELECT "we?ird", ? FROM t'
        translated = translate(sql)
        self.assertEqual(translated.count("%s"), 1)
        self.assertIn('"we?ird"', translated)

    def test_doubled_quote_escape_does_not_desynchronise_the_scanner(self):
        sql = "SELECT ? FROM t WHERE x='it''s' AND y=?"
        self.assertEqual(translate(sql).count("%s"), 2)

    def test_comment_marker_inside_a_string_literal_is_not_a_comment(self):
        sql = "SELECT ? FROM t WHERE note LIKE '%--%' AND id=?"
        self.assertEqual(translate(sql).count("%s"), 2)


class StatusRailQueryTests(unittest.TestCase):
    def test_status_rail_sql_translates_every_placeholder(self):
        sql, node = _status_rail_sql()
        literal = sql.count("?")
        bound = _status_rail_param_count(node)
        self.assertEqual(
            literal,
            bound,
            "the Status rail SQL binds a different number of params than it has placeholders",
        )
        self.assertEqual(
            translate(sql).count("%s"),
            bound,
            "the Status rail SQL loses placeholders in translation; psycopg2 will raise "
            "'not all arguments converted during string formatting' and the rail will 500",
        )

    def test_status_rail_still_carries_the_comment_that_triggered_the_defect(self):
        """If the comment is deleted the test above stops testing anything."""
        sql, _ = _status_rail_sql()
        self.assertRegex(sql, r"--[^\n]*'")


class RepoWidePlaceholderTests(unittest.TestCase):
    def _python_sources(self):
        """bot.py plus every module under services/ -- where the SQL lives."""
        paths = [BOT_PATH]
        for directory, _, names in os.walk(os.path.join(REPO_ROOT, "services")):
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

    def test_no_sql_literal_loses_a_placeholder_to_a_comment(self):
        offenders = [
            f"{os.path.relpath(path, REPO_ROOT)}:{lineno}"
            for path, lineno, sql in self._sql_literals()
            if _placeholder_loss(sql)
        ]
        self.assertEqual(
            offenders,
            [],
            "SQL comments are suppressing ? -> %s translation in: " + ", ".join(offenders),
        )

    def test_detector_catches_a_known_bad_query(self):
        """The old translator's failure mode, pinned so the scanner cannot be neutered."""
        sql = "SELECT * FROM t WHERE a=? -- people's faces\nLIMIT ?"
        self.assertEqual(_strip_sql_comments(sql).count("?"), 2)


class LiteralCollectorTests(unittest.TestCase):
    """The scan is only as good as what it hands the predicate: prose it should
    never have read, and every statement it must not stop reading."""

    def test_a_docstring_that_reads_as_sql_is_not_scanned(self):
        collected = [sql for _, sql in _sql_literals_in(_IMPORTER_SHAPED_SOURCE)]
        self.assertEqual(
            [sql for sql in collected if "quantity>=?" in sql],
            [],
            "the function's prose docstring is being scanned as an INSERT statement",
        )
        docstring = ast.get_docstring(ast.parse(_IMPORTER_SHAPED_SOURCE).body[1])
        self.assertTrue(
            _placeholder_loss(docstring),
            "this sample no longer reproduces the false positive: the docstring has to "
            "be prose the predicate would convict, or excluding it proves nothing",
        )
        self.assertEqual(
            [sql for sql in collected if "as many as the importer made" in sql],
            [],
            "the module's prose docstring is being scanned as an UPDATE statement",
        )

    def test_the_statements_beside_that_docstring_are_still_scanned(self):
        collected = [sql for _, sql in _sql_literals_in(_IMPORTER_SHAPED_SOURCE)]
        self.assertEqual(
            len(collected),
            2,
            "excluding docstrings has also excluded real SQL: " + repr(collected),
        )
        self.assertTrue(any(sql.startswith("INSERT INTO marketplace_listings") for sql in collected))
        self.assertTrue(any("-- newest first" in sql for sql in collected))

    def test_the_scan_still_flags_sql_whose_comment_hides_a_placeholder(self):
        """Excluding docstrings must not cost the scan its teeth.

        The shipped translator understands comments, so nothing in the repo can
        fail `_placeholder_loss` today -- which means an exclusion that also
        swallowed real SQL would leave this suite green and scanning nothing.
        Restoring the pre-fix failure mode, a translator that stops converting
        partway through, proves the predicate still fires on the statements the
        collector hands it."""
        commented = [
            sql for _, sql in _sql_literals_in(_IMPORTER_SHAPED_SOURCE) if "--" in sql
        ]
        self.assertEqual(len(commented), 1)
        sql = commented[0]
        self.assertFalse(
            _placeholder_loss(sql),
            "the current translator converts both placeholders; this literal is clean",
        )
        with mock.patch(f"{__name__}.translate", lambda text: text.replace("?", "%s", 1)):
            self.assertTrue(
                _placeholder_loss(sql),
                "a translator that loses a placeholder no longer fails the scan",
            )


if __name__ == "__main__":
    unittest.main()
