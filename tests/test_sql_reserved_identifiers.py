"""No CREATE TABLE / CREATE INDEX may use a PostgreSQL reserved word as a bare identifier.

SQLite accepts nearly every keyword as an identifier; PostgreSQL does not. That asymmetry
is invisible to this repo's test suite, which runs on SQLite, so a reserved column name
passes every local check and then fails on the production boot that creates the table --
the table simply never exists, and because a failed statement aborts the whole PostgreSQL
transaction, neither do the tables after it in the same ``ensure_schema``.

That is not hypothetical: ``business_os_perf_samples`` declared a column named ``window``
and took all four ``business_os_perf_*`` tables down with it on every deploy for six weeks
(``BUSINESS_OS_SCHEMA_BOOTSTRAP_FAILED subsystem=performance``). This guard is the check
that would have caught it in the commit that introduced it.

Quoting an identifier (``"window"``) is a deliberate opt-out and is not flagged -- but it
forces every reference to stay quoted forever, so renaming is nearly always better.

    python tests/test_sql_reserved_identifiers.py   # no pytest needed
"""

import ast
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Cannot be a column name in PostgreSQL: the CREATE TABLE grammar takes a ``ColId``,
#: which excludes both the reserved and the type/function-name keyword classes.
_RESERVED = {
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric",
    "both", "case", "cast", "check", "collate", "column", "constraint", "create",
    "current_catalog", "current_date", "current_role", "current_time",
    "current_timestamp", "current_user", "default", "deferrable", "desc", "distinct",
    "do", "else", "end", "except", "false", "fetch", "for", "foreign", "from", "grant",
    "group", "having", "in", "initially", "intersect", "into", "lateral", "leading",
    "limit", "localtime", "localtimestamp", "not", "null", "offset", "on", "only", "or",
    "order", "placing", "primary", "references", "returning", "select", "session_user",
    "some", "symmetric", "system_user", "table", "then", "to", "trailing", "true",
    "union", "unique", "user", "using", "variadic", "when", "where", "window", "with",
}
_TYPE_FUNC_NAME = {
    "authorization", "binary", "collation", "concurrently", "cross", "current_schema",
    "freeze", "full", "ilike", "inner", "is", "isnull", "join", "left", "like",
    "natural", "notnull", "outer", "overlaps", "right", "similar", "tablesample",
    "verbose",
}
RESERVED_IDENTIFIERS = _RESERVED | _TYPE_FUNC_NAME

#: A table-level constraint clause, not a column definition -- these legitimately start
#: with a reserved word (``PRIMARY KEY (...)``, ``UNIQUE (...)``, ``CHECK (...)``).
_CONSTRAINT_STARTS = {
    "primary", "unique", "foreign", "check", "constraint", "exclude", "like",
}

#: Scanned roots, relative to the repo root. Every path that can create a table.
SCAN_TARGETS = ("services", "models", "migrations", "bot.py")

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist"}

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)\s*\(", re.I)
_CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\w.]+\s+ON\s+([A-Za-z_][\w.]*)\s*\(", re.I)

#: A bare index column: an identifier, optionally with a sort/nulls qualifier. Anything
#: else in an index parenthesis is an expression (``CAST(id AS TEXT)``, ``lower(x)``),
#: whose interior words are SQL syntax rather than identifiers being declared.
_BARE_INDEX_COL_RE = re.compile(
    r'^\s*(")?([A-Za-z_]\w*)\1?\s*(?:ASC|DESC)?\s*(?:NULLS\s+(?:FIRST|LAST))?\s*$', re.I)


def _balanced_body(sql, open_paren_idx):
    """Text inside the parenthesis opening at ``open_paren_idx`` ("" if unbalanced)."""
    depth = 0
    for i in range(open_paren_idx, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_paren_idx + 1:i]
    return ""


def _split_top_level(body):
    """Split on commas that are not nested inside a parenthesis."""
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _declared_columns(sql, match):
    """Column names declared in a CREATE TABLE body. Quoted names are opted out."""
    names = []
    for part in _split_top_level(_balanced_body(sql, match.end() - 1)):
        part = part.strip()
        if not part or part.startswith('"'):
            continue
        token = re.match(r"([A-Za-z_]\w*)", part)
        if token and token.group(1).lower() not in _CONSTRAINT_STARTS:
            names.append(token.group(1))
    return names


def _indexed_columns(sql, match):
    """Bare column names referenced by a CREATE INDEX; expressions are skipped."""
    names = []
    for part in _split_top_level(_balanced_body(sql, match.end() - 1)):
        bare = _BARE_INDEX_COL_RE.match(part)
        if bare and not bare.group(1):
            names.append(bare.group(2))
    return names


def scan_sql(sql, origin="<sql>"):
    """``[(origin, table, identifier), ...]`` for every reserved bare identifier."""
    found = []
    for match in _CREATE_TABLE_RE.finditer(sql):
        table = match.group(1)
        if table.lower() in RESERVED_IDENTIFIERS:
            found.append((origin, table, table))
        for column in _declared_columns(sql, match):
            if column.lower() in RESERVED_IDENTIFIERS:
                found.append((origin, table, column))
    for match in _CREATE_INDEX_RE.finditer(sql):
        for column in _indexed_columns(sql, match):
            if column.lower() in RESERVED_IDENTIFIERS:
                found.append((origin, match.group(1), column))
    return found


def scan_python_file(path):
    """Scan the DDL in every string literal of one Python file.

    Reads string constants via ``ast`` rather than raw text, so a statement split across
    adjacent literals (the dominant style in this repo) is already concatenated by the
    parser and can be matched as one statement.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
    except OSError:
        return []
    upper = source.upper()
    if "CREATE TABLE" not in upper and "CREATE INDEX" not in upper:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    origin = os.path.relpath(path, REPO_ROOT)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value.upper()
        if "CREATE TABLE" in text or "CREATE INDEX" in text:
            found.extend(scan_sql(node.value, origin))
    return found


def scan_repo(targets=SCAN_TARGETS):
    found = []
    for target in targets:
        path = os.path.join(REPO_ROOT, target)
        if os.path.isfile(path):
            found.extend(scan_python_file(path))
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    found.extend(scan_python_file(os.path.join(dirpath, filename)))
    return sorted(set(found))


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------
def test_no_reserved_identifier_in_any_ddl():
    findings = scan_repo()
    assert not findings, (
        "PostgreSQL reserved word used as a bare SQL identifier. SQLite accepts these, "
        "so the local suite passes while CREATE TABLE fails on the production boot and "
        "takes the rest of its ensure_schema down with it. Rename the identifier (e.g. "
        "window -> window_key) and keep any JSON key mapped in Python:\n  "
        + "\n  ".join("%s: table %s, identifier %r" % f for f in findings))


def test_scanner_flags_a_reserved_column():
    """The guard must fail on the bug it was written for.

    Without this, a parser that silently stops matching anything would still report a
    clean repo forever.
    """
    ddl = """
        CREATE TABLE IF NOT EXISTS business_os_perf_samples (
            sample_id TEXT PRIMARY KEY,
            window TEXT NOT NULL DEFAULT '',
            value REAL NOT NULL
        )
    """
    assert [f[2] for f in scan_sql(ddl)] == ["window"]


def test_scanner_accepts_the_fixed_column():
    ddl = """
        CREATE TABLE IF NOT EXISTS business_os_perf_samples (
            sample_id TEXT PRIMARY KEY,
            window_key TEXT NOT NULL DEFAULT '',
            value REAL NOT NULL
        )
    """
    assert scan_sql(ddl) == []


def test_table_constraints_are_not_columns():
    """``PRIMARY KEY (...)`` / ``UNIQUE (...)`` clauses start with a reserved word."""
    ddl = """
        CREATE TABLE t (
            a TEXT,
            b TEXT,
            PRIMARY KEY (a),
            UNIQUE (a, b),
            CHECK (a IN ('x', 'y'))
        )
    """
    assert scan_sql(ddl) == []


def test_expression_index_is_not_a_column_list():
    """``ON t (CAST(id AS TEXT))`` is an expression index -- CAST/AS are syntax here."""
    sql = "CREATE INDEX IF NOT EXISTS ix ON pulse_audio_tracks (CAST(id AS TEXT))"
    assert scan_sql(sql) == []


def test_quoted_identifier_is_an_explicit_opt_out():
    ddl = 'CREATE TABLE t ("window" TEXT NOT NULL)'
    assert scan_sql(ddl) == []


def _run_standalone():
    tests = [
        test_scanner_flags_a_reserved_column,
        test_scanner_accepts_the_fixed_column,
        test_table_constraints_are_not_columns,
        test_expression_index_is_not_a_column_list,
        test_quoted_identifier_is_an_explicit_opt_out,
        test_no_reserved_identifier_in_any_ddl,
    ]
    passed = 0
    for test in tests:
        test()
        print("PASS  %s" % test.__name__)
        passed += 1
    print("\n%s/%s tests passed" % (passed, len(tests)))
    return passed == len(tests)


if __name__ == "__main__":
    raise SystemExit(0 if _run_standalone() else 1)
