"""Stage 7 + Stage 10 — the private write boundary, enforced statically.

Stage 7: "Feature code must NOT directly INSERT private facts. Add a static
guard." Stage 10: "Static guard against direct edge INSERTs."

The reason this is a *static* guard rather than a runtime one is that the
protections it defends live in the writers, not in the tables. A direct
``INSERT INTO private_facts`` from a route handler would produce a row that is
structurally valid and completely untrustworthy: no provenance encoding, no
sensitivity default, no dedupe key, no temporal validity, no audit event. The
database would accept it, every count would go up by one, and the first sign of
trouble would be a contradiction engine that cannot explain where a claim came
from. A runtime check cannot catch that, because by the time the statement runs
the bypass has already happened. So the boundary is enforced at the only point
where it is cheap: the source.

What counts as a violation is a *write* — INSERT, UPDATE, DELETE, REPLACE,
DROP, TRUNCATE — naming one of the four private tables, either by literal name
or through the schema module's table-name constants, from any module outside
``services/private_office/``. Reads are deliberately not restricted here;
owner scoping on reads is proved at runtime by the owner-isolation suite in
``test_private_substrate.py``, which is the right place for it, because a read
can be wrong in ways no regex can see.

The guard also polices the package itself. Inside ``services/private_office/``
only the modules listed in ``WRITER_MODULES`` may write, so a future helper
dropped into the package does not silently inherit write rights just by living
next door to the ones that have them.

Two failure modes of a guard like this are worse than not having it, and both
are tested for below. The first is a regex that matches nothing, which passes
forever and proves nothing — so ``test_guard_detects_known_violations`` feeds
it synthetic offending code and requires every case to be caught. The second is
a scanner that quietly skips most of the tree, which also passes forever — so
the file count and the unparseable-file list are asserted, not merely computed.
"""

from __future__ import annotations

import ast
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PACKAGE_DIR = os.path.join("services", "private_office")

# The modules permitted to emit SQL writes against the private tables. `schema`
# owns the DDL, `facts` and `graph` own the row writers, `audit` owns the
# metadata trail, and `contradictions` stamps conflict ids onto rows it did not
# create. Nothing else, inside the package or out.
WRITER_MODULES = frozenset({
    "schema.py",
    "facts.py",
    "graph.py",
    "audit.py",
    "contradictions.py",
})

PRIVATE_TABLES = (
    "private_facts",
    "private_graph_nodes",
    "private_graph_edges",
    "private_audit_events",
)

# The schema module exports these; interpolating one into a write statement is
# the same offence as naming the table, just harder to grep for by eye.
TABLE_CONSTANTS = ("FACTS_TABLE", "NODES_TABLE", "EDGES_TABLE", "AUDIT_TABLE")

_TARGET = "(?:" + "|".join(PRIVATE_TABLES + TABLE_CONSTANTS) + ")"
# Quoting, braces, a module prefix or a schema qualifier may sit between the
# clause and the table name. The span is restricted to identifier-ish
# characters rather than `.*` so a write against one table cannot be blamed on
# a private table mentioned later in the same statement.
_NAME = r"[\"'`\[\{\}\w.]*?"
_EXISTS = r"(?:if\s+(?:not\s+)?exists\s+)?"

# Row writes. `INSERT OR IGNORE` is spelled out because services/db.py rewrites
# it to a bare INSERT on Postgres, so it appears in this codebase and must not
# slip past on the strength of the two extra words.
_ROW_WRITE = (r"(?:insert\s+(?:or\s+\w+\s+)?into|replace\s+into|update|"
              r"delete\s+from|truncate\s+table|truncate)\s+" + _EXISTS + _NAME + _TARGET)

# DDL. Stage 34 gives the private schema exactly one DDL owner, so a second
# module creating, altering or dropping one of these tables is the same class
# of offence as a second module inserting into it — arguably worse, since a
# divergent CREATE TABLE IF NOT EXISTS wins silently on whichever process
# boots first and every later reader inherits the wrong columns.
_TABLE_DDL = (r"(?:create\s+table|drop\s+table|alter\s+table)\s+"
              + _EXISTS + _NAME + _TARGET)
_INDEX_DDL = (r"create\s+(?:unique\s+)?index\s+" + _EXISTS
              + r"[\w{}.\"'`\[\]]*\s+on\s+" + _NAME + _TARGET)

_VIOLATION = re.compile(
    "|".join((_ROW_WRITE, _TABLE_DDL, _INDEX_DDL)), re.IGNORECASE)

SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "build", "dist", ".expo", "ios", "android", ".mypy_cache",
    ".pytest_cache", "site-packages",
})

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


def sql_strings(source: str) -> list[str]:
    """Every string literal in ``source`` that could be a SQL statement.

    Docstrings are excluded on purpose. This very file describes the offence it
    forbids in prose, and so do the writer modules; a guard that cannot tell an
    explanation from an instruction would flag its own documentation and force
    the next person to write around it. f-strings are reconstructed with their
    interpolations spelled out — ``f"UPDATE {_schema.FACTS_TABLE} SET ..."``
    has to remain visible as a write against the facts table.
    """
    tree = ast.parse(source)

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
                elif isinstance(piece, ast.FormattedValue):
                    try:
                        parts.append("{" + ast.unparse(piece.value) + "}")
                    except Exception:
                        parts.append("{}")
            found.append("".join(parts))
    return found


def offences(source: str) -> list[str]:
    """The write statements in ``source`` that target a private table."""
    hits: list[str] = []
    for text in sql_strings(source):
        for match in _VIOLATION.finditer(text):
            snippet = " ".join(text[match.start():match.start() + 90].split())
            hits.append(snippet)
    return hits


def python_files() -> list[str]:
    paths: list[str] = []
    for root, dirs, names in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".fuse_hidden")]
        for name in names:
            if name.endswith(".py"):
                paths.append(os.path.join(root, name))
    return sorted(paths)


def may_write(relpath: str) -> bool:
    if not relpath.startswith(PACKAGE_DIR + os.sep):
        return False
    return os.path.basename(relpath) in WRITER_MODULES


# --------------------------------------------------------------------------
# The guard, applied to the repository.
# --------------------------------------------------------------------------

def test_no_private_writes_outside_the_writers():
    print("\n[write boundary]")
    paths = python_files()
    check("the scan reached a plausible number of files", len(paths) > 200, str(len(paths)))

    guard_file = os.path.abspath(__file__)
    unparseable: list[str] = []
    violations: list[str] = []
    package_seen: set[str] = set()

    for path in paths:
        if path == guard_file:
            continue
        relpath = os.path.relpath(path, REPO_ROOT)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            unparseable.append(f"{relpath}: {exc.__class__.__name__}")
            continue
        try:
            hits = offences(source)
        except SyntaxError as exc:
            # Recorded rather than ignored: a file the guard cannot read is a
            # file the guard does not protect, and that has to be visible.
            unparseable.append(f"{relpath}: SyntaxError line {exc.lineno}")
            continue

        if relpath.startswith(PACKAGE_DIR + os.sep):
            package_seen.add(os.path.basename(relpath))
        if hits and not may_write(relpath):
            for hit in hits:
                violations.append(f"{relpath}: {hit}")

    check("no module outside the private writers writes to a private table",
          not violations, "; ".join(violations[:5]))

    # The writers must actually be present and actually contain writes. If a
    # rename left `facts.py` behind, the check above would pass vacuously.
    missing = sorted(WRITER_MODULES - package_seen)
    check("every declared writer module exists", not missing, str(missing))

    for name in sorted(WRITER_MODULES):
        path = os.path.join(REPO_ROOT, PACKAGE_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            hits = offences(handle.read())
        check(f"{name} really does contain the writes it is trusted with",
              bool(hits), "no write or DDL statement found")

    check("no python file defeated the parser",
          not unparseable, "; ".join(unparseable[:5]))
    return None


# --------------------------------------------------------------------------
# The guard, applied to itself.
# --------------------------------------------------------------------------

_MUST_CATCH = {
    "a bare insert": 'cur.execute("INSERT INTO private_facts (owner_user_id) VALUES (?)", (1,))',
    "insert or ignore": 'cur.execute("INSERT OR IGNORE INTO private_graph_edges (id) VALUES (1)")',
    "an update": 'cur.execute("UPDATE private_facts SET confidence = 1.0 WHERE id = 1")',
    "a delete": 'cur.execute("DELETE FROM private_graph_nodes WHERE id = 1")',
    "a node insert": 'cur.execute("insert into private_graph_nodes (node_type) values (?)", ("PERSON",))',
    "an audit insert": 'cur.execute("INSERT INTO private_audit_events (action) VALUES (?)", ("X",))',
    "a constant interpolated into a write":
        'cur.execute(f"INSERT INTO {_schema.FACTS_TABLE} (owner_user_id) VALUES (?)", (1,))',
    "a constant interpolated into an update":
        'cur.execute(f"UPDATE {schema.EDGES_TABLE} SET confidence = 0")',
    "a drop": 'cur.execute("DROP TABLE IF EXISTS private_facts")',
    "a rival create table": 'cur.execute("CREATE TABLE IF NOT EXISTS private_facts (id INTEGER)")',
    "a rival create table via constant": 'cur.execute(f"CREATE TABLE IF NOT EXISTS {FACTS_TABLE} (id INTEGER)")',
    "an alter": 'cur.execute("ALTER TABLE private_graph_edges ADD COLUMN sneaky TEXT")',
    "a rival index": 'cur.execute("CREATE INDEX IF NOT EXISTS idx_x ON private_facts (owner_user_id)")',
    "a write split across concatenation": 'cur.execute("INSERT INTO "\n              "private_facts (id) VALUES (1)")',
}

_MUST_IGNORE = {
    "a read": 'cur.execute("SELECT id FROM private_facts WHERE owner_user_id = ?", (1,))',
    "a count": 'cur.execute("SELECT COUNT(*) FROM private_graph_edges WHERE owner_user_id = ?", (1,))',
    "a join that only reads": 'cur.execute("SELECT n.id FROM private_graph_nodes n JOIN private_graph_edges e ON e.source_node_id = n.id")',
    "a write against an unrelated table": 'cur.execute("INSERT INTO users (id) VALUES (1)")',
    "an index on an unrelated table": 'cur.execute("CREATE INDEX idx_u ON users (id)")',
    "a read whose result column is named after a table":
        'cur.execute("SELECT id AS private_facts FROM users")',
    "a docstring that describes the rule": '"""Never INSERT INTO private_facts directly; call record_fact."""',
    "a comment that describes the rule": '# do not INSERT INTO private_graph_edges by hand\nx = 1',
    "prose naming a table without a verb": 'MESSAGE = "private_facts is owned by services/private_office/facts.py"',
}


def test_guard_detects_known_violations():
    """A guard that matches nothing passes forever. Prove it matches."""
    print("\n[guard self-test]")
    for label, snippet in sorted(_MUST_CATCH.items()):
        try:
            hits = offences(snippet)
        except SyntaxError:
            check(f"catches {label}", False, "synthetic sample did not parse")
            continue
        check(f"catches {label}", bool(hits), "no match")

    for label, snippet in sorted(_MUST_IGNORE.items()):
        try:
            hits = offences(snippet)
        except SyntaxError:
            check(f"allows {label}", False, "synthetic sample did not parse")
            continue
        check(f"allows {label}", not hits, str(hits))

    # The concatenation case above matters because Python folds adjacent
    # literals at parse time, so `"INSERT INTO " "private_facts"` is one
    # constant by the time the guard sees it. If that ever stops being true the
    # split-write case fails loudly rather than becoming a silent escape hatch.
    check("adjacent literals are folded before the guard sees them",
          len(sql_strings('x = "INSERT INTO " "private_facts"')) == 1,
          str(sql_strings('x = "INSERT INTO " "private_facts"')))
    return None


def test_package_membership_is_explicit():
    """A new module in the package must not inherit write rights by location."""
    print("\n[package membership]")
    package = os.path.join(REPO_ROOT, PACKAGE_DIR)
    names = {n for n in os.listdir(package) if n.endswith(".py")}
    unlisted = sorted(names - WRITER_MODULES - {"__init__.py"})

    offending: list[str] = []
    for name in unlisted:
        with open(os.path.join(package, name), "r", encoding="utf-8") as handle:
            try:
                hits = offences(handle.read())
            except SyntaxError:
                offending.append(f"{name}: unparseable")
                continue
        if hits:
            offending.append(f"{name}: {hits[0]}")

    check("no unlisted module in the package writes to a private table",
          not offending, "; ".join(offending))
    check("the package has non-writer modules, so the check is not vacuous",
          bool(unlisted), str(sorted(names)))
    return None


def main() -> int:
    print("PRIVATE OFFICE WRITE BOUNDARY — Stages 7 & 10")
    print(f"repo: {REPO_ROOT}")
    test_guard_detects_known_violations()
    test_no_private_writes_outside_the_writers()
    test_package_membership_is_explicit()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for line in _FAILURES:
            print(f"  - {line}")
        return 1
    print("PASS — every check held")
    return 0


def test_write_boundary_suite():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
