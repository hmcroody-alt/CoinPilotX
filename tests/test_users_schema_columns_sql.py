"""A query may not select a column that ``users`` does not have.

## The defect this pins

Two queries selected columns that have never existed on ``users``:

    SELECT user_id, country, state_region FROM users WHERE user_id IN (?)   # bot.py
    SELECT ..., is_pro, plan, subscription_plan, founder_number, founder_status
    FROM users WHERE user_id = ?                                  # entitlements

`state_region` belongs to `marketplace_sellers` / `marketplace_merchant_applications`.
`founder_number` belongs to `founder_memberships`, whose `status` column the rest
of the codebase aliases to `founder_status` (`fm.status AS founder_status`). Both
column lists were copy-pasted from a different table's schema.

This was never schema drift: `bot.init_db()` has never added either column to
`users`, nothing in the repo ever writes them, and production Postgres agrees.
The "fix" of adding them to `init_db()` would have created permanently-empty
columns -- and for `founder_number` would have been worse than the bug, because
`premium_identity_engine.has_active_premium` treats a non-zero `founder_number`
as proof of Founder status, so an always-NULL column on `users` would shadow the
real membership row.

Both sites sat inside `except Exception`, so neither raised. They degraded:

* the reels geo lookup failed *entirely*, so `author_country` was always `""`
  even though `users.country` exists -- which is what the "local" reel lane
  filters on, so that lane matched nothing for every viewer on every request;
* `identity_row()` always returned `{}`, so authority C never contributed an
  answer to premium resolution in production.

Local runs could not see it either: SQLite rejects the missing column too, and
the `except` swallowed it on both engines. The only visible trace was a
`SQL_EXECUTE_FAILED` log line, which is easy to misread as belonging to whatever
request happened to be interleaved with it in the log.

## What is asserted, and why in these forms

`test_reel_geo_query_runs_against_the_real_users_schema` is behavioural: it
builds a `users` table from the column list `bot.init_db()` actually installs and
runs the query bot.py actually contains. `test_state_region_is_not_a_users_column`
is its negative half, pinning that the old form really does fail.

The remaining tests are source checks over every `SELECT ... FROM users` in
`bot.py` and `services/`, because the property "this SQL is legal on the
deployment engine" cannot be observed from a suite with no PostgreSQL in it. A
source check can only prove the shape is absent, which is exactly the claim.
`test_detector_catches_a_known_bad_query` exists so that a future edit which
neuters the scanner fails loudly instead of silently passing everything.

The schema is read out of `bot.py` with `ast` rather than by importing it --
`bot.py` pulls in `stripe` and builds the Flask app, so it cannot be imported.
The SQL is likewise read from source rather than by lifting
`pulse_reel_feed_payload`, whose helper graph is far deeper than the one query
under test; the caveat is the usual one: this proves the query, not its wiring
into the route.
"""

import ast
import os
import re
import sqlite3
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")
SERVICES_ROOT = os.path.join(REPO_ROOT, "services")

# Columns that a SELECT may name without them being real `users` columns.
SQL_NOISE = {"as", "from", "where", "distinct", "all"}


def _read(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _python_sources():
    paths = [BOT_PATH]
    for directory, _, names in os.walk(SERVICES_ROOT):
        paths.extend(
            os.path.join(directory, name) for name in sorted(names) if name.endswith(".py")
        )
    return paths


def users_columns():
    """Every column `bot.init_db()` installs on `users`.

    Three mechanisms contribute: the `CREATE TABLE` body, the additive
    `add_columns_if_missing(cur, "users", [...])` list, and bare
    `ALTER TABLE users ADD COLUMN` statements (services/pulse_id_service.py
    adds `pulse_id` that way).
    """
    source = _read(BOT_PATH)
    columns = set()

    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS users\s*\((.*?)\n\s*\)", source, re.S | re.I
    ):
        for line in match.group(1).splitlines():
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(
                ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK")
            ):
                continue
            columns.add(line.split()[0])

    tree = ast.parse(source, filename=BOT_PATH)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") != "add_columns_if_missing":
            continue
        if len(node.args) < 3:
            continue
        table, entries = node.args[1], node.args[2]
        if not isinstance(table, ast.Constant) or table.value != "users":
            continue
        if not isinstance(entries, (ast.List, ast.Tuple)):
            continue
        for entry in entries.elts:
            if isinstance(entry, (ast.Tuple, ast.List)) and entry.elts:
                name = entry.elts[0]
                if isinstance(name, ast.Constant):
                    columns.add(name.value)

    for path in _python_sources():
        for match in re.finditer(
            r"ALTER TABLE users ADD COLUMN (\w+)", _read(path), re.I
        ):
            columns.add(match.group(1))

    return columns


# `SELECT <cols> FROM users [alias]`.
#
# The select list may not contain `FROM` (that would mean this match had run
# past the end of its own statement) nor a quote character (which would mean it
# had run out of one Python string literal and into another). Without both
# guards the lazy `.*?` happily bridges an unrelated earlier SELECT to a later
# `FROM users` and swallows the real query in between.
SELECT_FROM_USERS = re.compile(
    r"SELECT\s+(?P<cols>(?:(?!\bFROM\b)[^\"'])*?)\s+FROM\s+users\b"
    r"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_]\w*))?",
    re.I | re.S,
)

CLAUSE_KEYWORDS = re.compile(
    r"\b(WHERE|ORDER\s+BY|GROUP\s+BY|LIMIT|LEFT|RIGHT|INNER|OUTER|JOIN|UNION|ON)\b", re.I
)


def _selected_users_columns(cols_text, alias):
    """The bare `users` column names a select list names, or None to skip.

    Returns None when the select list cannot be read confidently -- `*`, a
    function call, a subquery, or an interpolated f-string fragment. Those are
    not failures, they are simply outside what a regex can honestly assert.
    """
    if "*" in cols_text or "(" in cols_text or "{" in cols_text:
        return None
    if CLAUSE_KEYWORDS.search(cols_text):
        return None

    names = []
    for term in cols_text.split(","):
        term = " ".join(term.split())
        if not term:
            return None
        term = re.sub(r"\s+AS\s+\w+$", "", term, flags=re.I).strip()
        if "." in term:
            prefix, _, bare = term.partition(".")
            # A prefixed column belongs to whichever table that alias names.
            # Only the ones bound to `users` are ours to check.
            if alias and prefix.lower() == alias.lower():
                names.append(bare)
            continue
        if not re.fullmatch(r"\w+", term):
            return None
        if term.lower() in SQL_NOISE:
            return None
        # `SELECT 1 FROM users WHERE ...` is an existence probe, not a column.
        if re.fullmatch(r"\d+", term) or term.lower() in {"null", "true", "false"}:
            continue
        # An unaliased bare column in a query that aliased `users` and joined
        # something else is ambiguous; don't guess which table owns it.
        if alias:
            return None
        names.append(term)
    return names


def scan_for_unknown_users_columns(source, known_columns):
    """(line, [bad columns]) for each `SELECT ... FROM users` naming a stranger."""
    offenders = []
    for match in SELECT_FROM_USERS.finditer(source):
        alias = match.group("alias")
        if alias and alias.upper() in {
            "WHERE", "ORDER", "GROUP", "LIMIT", "LEFT", "RIGHT",
            "INNER", "OUTER", "JOIN", "UNION", "ON", "AS",
        }:
            alias = None
        names = _selected_users_columns(match.group("cols"), alias)
        if not names:
            continue
        unknown = [name for name in names if name not in known_columns]
        if unknown:
            offenders.append((source[: match.start()].count("\n") + 1, unknown))
    return offenders


class UsersSchemaTest(unittest.TestCase):
    def setUp(self):
        self.columns = users_columns()

    def test_schema_extraction_found_a_plausible_users_table(self):
        # Guards the rest of the suite: an extraction that silently returned
        # an empty or tiny set would make every other assertion vacuous.
        self.assertGreater(len(self.columns), 50)
        for expected in ("user_id", "username", "country", "premium_status"):
            self.assertIn(expected, self.columns)

    def test_state_region_is_not_a_users_column(self):
        self.assertNotIn("state_region", self.columns)

    def test_founder_columns_are_not_users_columns(self):
        # Adding these to `users` would shadow founder_memberships, which is
        # where has_active_premium's answer legitimately comes from.
        self.assertNotIn("founder_number", self.columns)
        self.assertNotIn("founder_status", self.columns)

    def test_reel_geo_query_runs_against_the_real_users_schema(self):
        source = _read(BOT_PATH)
        match = re.search(
            r'"(SELECT user_id, country FROM users WHERE user_id IN \(\{placeholders\}\))"',
            source,
        )
        self.assertIsNotNone(
            match, "the reels author-geo query changed shape; update this test"
        )
        sql = match.group(1).replace("{placeholders}", "?")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE users (%s)"
            % ", ".join("%s TEXT" % name for name in sorted(self.columns))
        )
        conn.execute("INSERT INTO users (user_id, country) VALUES ('4', 'NG')")

        rows = [dict(row) for row in conn.execute(sql, ("4",)).fetchall()]
        conn.close()

        # The whole point: the country actually arrives. Before the fix the
        # statement raised, the caller swallowed it, and author_country was "".
        self.assertEqual(rows, [{"user_id": "4", "country": "NG"}])

    def test_old_reel_geo_query_would_still_fail(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE users (%s)"
            % ", ".join("%s TEXT" % name for name in sorted(self.columns))
        )
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute(
                "SELECT user_id, country, state_region FROM users WHERE user_id IN (?)",
                ("4",),
            )
        conn.close()

    def test_detector_catches_a_known_bad_query(self):
        bad = 'cur.execute("SELECT user_id, country, state_region FROM users WHERE user_id=?")'
        self.assertEqual(
            scan_for_unknown_users_columns(bad, self.columns),
            [(1, ["state_region"])],
        )

        aliased = (
            'conn.execute("SELECT u.is_pro, u.founder_number FROM users u '
            'LEFT JOIN founder_memberships fm ON fm.user_id=u.user_id")'
        )
        self.assertEqual(
            scan_for_unknown_users_columns(aliased, self.columns),
            [(1, ["founder_number"])],
        )

        # A column owned by a joined table must not be reported.
        joined = (
            'conn.execute("SELECT u.is_pro, fm.founder_number FROM users u '
            'LEFT JOIN founder_memberships fm ON fm.user_id=u.user_id")'
        )
        self.assertEqual(scan_for_unknown_users_columns(joined, self.columns), [])

    def test_entitlements_identity_query_separates_users_from_founder_columns(self):
        # This query builds its select list at runtime with `', '.join(...)`,
        # so the source scanner above cannot see it. Read the two tuples it
        # joins from instead, and check the split is the one the schema allows.
        path = os.path.join(
            REPO_ROOT, "services", "business_os", "entitlements", "premium.py"
        )
        source = _read(path)
        tree = ast.parse(source, filename=path)

        tuples = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id not in {"_USER_COLUMNS", "_FOUNDER_COLUMNS"}:
                continue
            if isinstance(node.value, (ast.Tuple, ast.List)):
                tuples[target.id] = [
                    elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)
                ]

        self.assertIn("_USER_COLUMNS", tuples)
        self.assertIn("_FOUNDER_COLUMNS", tuples)

        unknown = [c for c in tuples["_USER_COLUMNS"] if c not in self.columns]
        self.assertEqual(
            unknown, [], "identity_row selects these from `users`, which lacks them"
        )
        self.assertEqual(sorted(tuples["_FOUNDER_COLUMNS"]),
                         ["founder_number", "founder_status"])

        # ...and the founder pair has to be joined in from its real table,
        # because has_active_premium reads both.
        self.assertRegex(source, r"LEFT JOIN founder_memberships")
        self.assertRegex(source, r"fm\.status AS founder_status")

    def test_no_query_selects_an_unknown_users_column(self):
        offenders = []
        for path in _python_sources():
            for line, unknown in scan_for_unknown_users_columns(
                _read(path), self.columns
            ):
                offenders.append(
                    "%s:%d selects %s" % (os.path.relpath(path, REPO_ROOT), line, unknown)
                )

        self.assertEqual(
            offenders,
            [],
            "selecting a column `users` does not have raises UndefinedColumn on "
            "PostgreSQL; every one of these sites is wrapped in `except "
            "Exception`, so it degrades silently instead of failing: %s" % offenders,
        )


if __name__ == "__main__":
    unittest.main()
