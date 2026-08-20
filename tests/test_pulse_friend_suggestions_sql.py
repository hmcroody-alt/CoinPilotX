"""People You May Know must actually return people.

## The defect this pins

`pulse_friend_graph` built its `suggested` list with

    SELECT DISTINCT p.user_id
    FROM pulse_posts p
    WHERE p.user_id!=? AND p.deleted_at IS NULL
    ORDER BY p.created_at DESC
    LIMIT 40

SQLite runs that. PostgreSQL refuses it:

    psycopg2.errors.InvalidColumnReference: for SELECT DISTINCT, ORDER BY
    expressions must appear in select list

so on production every call raised, the `except Exception` around it logged
`PULSE_FRIENDS_SUGGESTED_QUERY_FAILED` and left `suggested` at `[]`, and
`/api/pulse/friends` answered **200** with a well-formed graph that simply had
nobody to suggest. Home's People module builds from that list, finds it empty,
and renders nothing — the outage presents as "the suggestions feature does not
work" rather than as an error, for every viewer, on every request.

Local runs and the existing suite could not see it, because both run SQLite.

## What is asserted, and why in two forms

`test_suggested_*` are behavioural: they run the real function against a real
database and assert the list it produces. They would pass on the broken version
too — SQLite accepts it — so they pin the *meaning* (each author once, most
recently active first, nobody the viewer already knows) and would catch a future
rewrite that changes it.

`test_no_select_distinct_orders_by_an_unselected_column` is the one that catches
the engine fault. It is a source check rather than a behavioural one for the
honest reason that there is no PostgreSQL in this suite: the property "this SQL
is legal on the deployment engine" cannot be observed here, so it is asserted
structurally over every `SELECT DISTINCT` in `bot.py`, not just the one that
broke. A source check can only prove the shape is absent, which is exactly the
claim being made.

`bot.py` cannot be imported (it pulls in `stripe` and builds the Flask app), so
the function is lifted out with `ast` and executed against stubs — the same
technique as `test_pulse_post_save_wiring.py`, with the same caveat: it proves
the query and the logic, not the wiring into the route.
"""

import ast
import os
import re
import sqlite3
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

BOT_PATH = os.path.join(REPO_ROOT, "bot.py")


def _load_bot_source():
    with open(BOT_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _lift_function(name):
    """Return `name` from bot.py, executed against a stub `logging`."""
    tree = ast.parse(_load_bot_source(), filename=BOT_PATH)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            module = ast.Module(body=[node], type_ignores=[])
            namespace = {"logging": _SilentLogging()}
            exec(compile(module, BOT_PATH, "exec"), namespace)  # noqa: S102
            return namespace[name]
    raise AssertionError("%s is not defined in bot.py" % name)


class _SilentLogging:
    """Swallows the module's own warnings so a failing query is not printed.

    It also records them, so a test can prove the query did *not* fall into the
    `except` branch — the branch that hid this defect in production.
    """

    def __init__(self):
        self.warnings = []

    def warning(self, *args, **kwargs):
        self.warnings.append(args)

    def exception(self, *args, **kwargs):
        self.warnings.append(args)

    def info(self, *args, **kwargs):
        pass


def _seed(cur):
    cur.executescript(
        """
        CREATE TABLE pulse_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            deleted_at TEXT,
            created_at TEXT
        );
        CREATE TABLE pulse_friend_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_user_id INTEGER,
            receiver_user_id INTEGER,
            recipient_user_id INTEGER,
            status TEXT,
            created_at TEXT
        );
        CREATE TABLE pulse_friendships (user_id INTEGER, friend_user_id INTEGER, created_at TEXT);
        CREATE TABLE pulse_friends (user_id INTEGER, friend_user_id INTEGER, status TEXT, created_at TEXT);
        CREATE TABLE pulse_follows (follower_user_id INTEGER, followed_user_id INTEGER, created_at TEXT);
        """
    )


def _post(cur, user_id, created_at, deleted_at=None):
    cur.execute(
        "INSERT INTO pulse_posts (user_id, deleted_at, created_at) VALUES (?, ?, ?)",
        (user_id, deleted_at, created_at),
    )


class SuggestedPeopleBehaviour(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        _seed(self.cur)
        self.pulse_friend_graph = _lift_function("pulse_friend_graph")

    def tearDown(self):
        self.conn.close()

    def test_suggested_lists_recent_authors_the_viewer_does_not_know(self):
        _post(self.cur, 2, "2026-08-01T00:00:00")
        _post(self.cur, 3, "2026-08-03T00:00:00")
        _post(self.cur, 4, "2026-08-02T00:00:00")

        graph = self.pulse_friend_graph(self.cur, 1)

        self.assertEqual(graph["suggested"], [3, 4, 2])

    def test_suggested_names_each_author_once(self):
        for stamp in ("2026-08-01T00:00:00", "2026-08-05T00:00:00", "2026-08-09T00:00:00"):
            _post(self.cur, 2, stamp)
        _post(self.cur, 3, "2026-08-07T00:00:00")

        graph = self.pulse_friend_graph(self.cur, 1)

        # Author 2 is prolific, not three separate people, and their most recent
        # post is what places them ahead of author 3.
        self.assertEqual(graph["suggested"], [2, 3])

    def test_suggested_excludes_the_viewer_friends_and_followed_accounts(self):
        _post(self.cur, 1, "2026-08-09T00:00:00")  # the viewer
        _post(self.cur, 2, "2026-08-08T00:00:00")  # a friend
        _post(self.cur, 3, "2026-08-07T00:00:00")  # already followed
        _post(self.cur, 4, "2026-08-06T00:00:00")  # a stranger
        self.cur.execute(
            "INSERT INTO pulse_friendships (user_id, friend_user_id, created_at) VALUES (1, 2, '2026-08-01T00:00:00')"
        )
        self.cur.execute(
            "INSERT INTO pulse_follows (follower_user_id, followed_user_id, created_at) VALUES (1, 3, '2026-08-01T00:00:00')"
        )

        graph = self.pulse_friend_graph(self.cur, 1)

        self.assertEqual(graph["suggested"], [4])

    def test_suggested_ignores_deleted_posts(self):
        _post(self.cur, 2, "2026-08-09T00:00:00", deleted_at="2026-08-10T00:00:00")
        _post(self.cur, 3, "2026-08-08T00:00:00")

        graph = self.pulse_friend_graph(self.cur, 1)

        self.assertEqual(graph["suggested"], [3])

    def test_the_suggested_query_does_not_fall_into_the_except_branch(self):
        _post(self.cur, 2, "2026-08-09T00:00:00")

        graph = self.pulse_friend_graph(self.cur, 1)

        self.assertEqual(graph["suggested"], [2])
        # A query that raises is indistinguishable from "nobody to suggest" in
        # the return value alone. This is the assertion that tells them apart.
        self.assertEqual(_current_logging_warnings(self.pulse_friend_graph), [])


def _current_logging_warnings(func):
    return func.__globals__["logging"].warnings


SELECT_DISTINCT = re.compile(r"SELECT\s+DISTINCT\s+(.*?)\bFROM\b", re.IGNORECASE | re.DOTALL)


class SelectDistinctIsPortable(unittest.TestCase):
    """No `SELECT DISTINCT` in bot.py may order by a column it did not select.

    PostgreSQL rejects that combination outright. The check is deliberately
    coarse — it compares bare column names, so `ORDER BY MAX(x)` over a selected
    `MAX(x) AS y` is not understood — and errs toward flagging rather than
    passing, because a false alarm costs a rewrite and a miss costs a silently
    empty feature in production.
    """

    def test_no_select_distinct_orders_by_an_unselected_column(self):
        source = _load_bot_source()
        offenders = []

        for match in SELECT_DISTINCT.finditer(source):
            selected = {
                _bare_name(part)
                for part in match.group(1).split(",")
                if _bare_name(part)
            }
            tail = source[match.end():match.end() + 2000]
            order_by = re.search(r"\bORDER\s+BY\b(.*?)(\bLIMIT\b|\"\"\"|'''|\)\s*$)", tail, re.IGNORECASE | re.DOTALL)
            if not order_by:
                continue
            # An ORDER BY that appears after the statement's own closing quote
            # belongs to a different query.
            statement = tail[: order_by.start()]
            if '"""' in statement or "'''" in statement:
                continue
            for term in order_by.group(1).split(","):
                name = _bare_name(term)
                if not name or name in {"", "asc", "desc"}:
                    continue
                if "*" in selected:
                    continue
                if name not in selected:
                    offenders.append((source[: match.start()].count("\n") + 1, name))

        self.assertEqual(
            offenders,
            [],
            "SELECT DISTINCT ... ORDER BY <unselected column> is a PostgreSQL "
            "error and a silent empty result in production: %s" % offenders,
        )


def _bare_name(expression):
    """`p.created_at DESC` -> `created_at`; `COUNT(*) AS n` -> `n`."""
    text = " ".join(expression.split())
    alias = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.IGNORECASE)
    if alias:
        return alias.group(1).lower()
    text = re.sub(r"\b(ASC|DESC|NULLS|FIRST|LAST)\b", "", text, flags=re.IGNORECASE).strip()
    if not text or "(" in text:
        return text.lower()
    return text.split(".")[-1].strip().lower()


if __name__ == "__main__":
    unittest.main()
