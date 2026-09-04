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
"""

import ast
import os
import re
import unittest

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
                tree = ast.parse(_read(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if SQL_START.match(node.value):
                        yield path, node.lineno, node.value

    def test_no_sql_literal_loses_a_placeholder_to_a_comment(self):
        offenders = []
        for path, lineno, sql in self._sql_literals():
            if "--" not in sql and "/*" not in sql:
                continue
            expected = _strip_sql_comments(sql).count("?")
            if expected == 0:
                continue
            if translate(sql).count("%s") - sql.count("%s") != expected:
                offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{lineno}")
        self.assertEqual(
            offenders,
            [],
            "SQL comments are suppressing ? -> %s translation in: " + ", ".join(offenders),
        )

    def test_detector_catches_a_known_bad_query(self):
        """The old translator's failure mode, pinned so the scanner cannot be neutered."""
        sql = "SELECT * FROM t WHERE a=? -- people's faces\nLIMIT ?"
        self.assertEqual(_strip_sql_comments(sql).count("?"), 2)


if __name__ == "__main__":
    unittest.main()

