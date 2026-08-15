"""Every column the code writes must be a column the schema declares.

Production runs PostgreSQL and there is no migration framework: `bot.init_db()`
creates tables imperatively and widens them through `add_columns_if_missing`.
That works right up until someone writes a query against a column nobody ever
added to either list. SQLite (local, dev, most tests) is forgiving in ways that
hide it -- and the read path here defaults a missing key to 0 -- so the gap
reaches production intact and only the WRITE fails:

    UPDATE pulse_posts SET view_count=COALESCE(view_count,0)+1 ...
    psycopg2.errors.UndefinedColumn: column "view_count" does not exist
    -> POST /api/pulse/reels/<id>/view  500

That was live in production: `view_count` was written by the reel view route
and rendered by the post serializer, but appeared in neither the CREATE TABLE
nor the `add_columns_if_missing` list for `pulse_posts`. Reel view counting was
completely broken and the only symptom was a 500 on a fire-and-forget endpoint
that the client ignores.

This test closes the loop for the core Pulse post/reel tables: every column
assigned by an UPDATE must be declared. It is deliberately a *declaration*
check, not a database check, so it runs anywhere and fails at review time
rather than in production.
"""

import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BOT_PY = os.path.join(REPO_ROOT, "bot.py")

# Tables whose write paths are core to the feed/reels surface. Scoped rather
# than global: these are the ones whose UPDATE statements are all plain column
# assignments, so a violation here is a real defect and never a parser artifact.
GUARDED_TABLES = ("pulse_posts", "pulse_reels")


def _read_bot():
    with open(BOT_PY, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _declared_columns(source: str, table: str) -> set:
    """Columns the schema declares: CREATE TABLE body + add_columns_if_missing."""
    declared = set()

    create = re.search(
        r"CREATE TABLE IF NOT EXISTS\s+" + re.escape(table) + r"\s*\((.*?)\n\s*\)",
        source, re.DOTALL)
    if create:
        for line in create.group(1).splitlines():
            line = line.strip().strip(",")
            if not line or line.upper().startswith(
                    ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT")):
                continue
            name = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", line)
            if name:
                declared.add(name.group(1).lower())

    for block in re.finditer(
            r"add_columns_if_missing\(\s*cur\s*,\s*[\"']" + re.escape(table)
            + r"[\"']\s*,\s*\[(.*?)\]", source, re.DOTALL):
        for name in re.findall(r"\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*,", block.group(1)):
            declared.add(name.lower())

    return declared


def _assigned_columns(source: str, table: str) -> set:
    """Columns appearing on the left of `=` in an UPDATE <table> SET ... clause.

    Paren depth is tracked so that `view_count=COALESCE(view_count,0)+1` yields
    `view_count` alone and never the arguments inside the function call.
    """
    assigned = set()
    pattern = re.compile(
        r"UPDATE\s+" + re.escape(table) + r"\s+SET\s+(.*?)(?:\s+WHERE\b|[\"'])",
        re.DOTALL | re.IGNORECASE)
    for match in pattern.finditer(source):
        clause = match.group(1)
        depth = 0
        token = ""
        expect_name = True
        for char in clause:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            if depth == 0:
                if char == "=" and expect_name:
                    name = token.strip().lstrip(",").strip()
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
                        assigned.add(name.lower())
                    expect_name = False
                    token = ""
                    continue
                if char == ",":
                    expect_name = True
                    token = ""
                    continue
            token += char
    return assigned


def test_updated_columns_are_declared():
    source = _read_bot()
    problems = []
    for table in GUARDED_TABLES:
        declared = _declared_columns(source, table)
        assert declared, f"no schema declaration found for {table} -- parser drift"
        for column in sorted(_assigned_columns(source, table)):
            if column not in declared:
                problems.append(f"{table}.{column}")
    assert not problems, (
        "columns written by UPDATE but never declared in CREATE TABLE or "
        "add_columns_if_missing (these 500 on PostgreSQL with UndefinedColumn): "
        + ", ".join(problems))


def test_pulse_posts_declares_view_count():
    """The specific production regression, pinned by name.

    The general check above would catch a reintroduction only while the UPDATE
    still exists. This asserts the column stays declared even if the write path
    is refactored, because the post serializer reads it too.
    """
    declared = _declared_columns(_read_bot(), "pulse_posts")
    assert "view_count" in declared, (
        "pulse_posts.view_count is not declared; POST /api/pulse/reels/<id>/view "
        "will 500 on PostgreSQL and reel view counts will silently stop")


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
