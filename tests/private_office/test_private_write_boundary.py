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
    # Batch C. `records` owns the DDL and the row writers for all six canonical
    # primitives. It is one module rather than six because the alternative —
    # `obligations.py`, `risks.py`, and four more, each with its own copy of the
    # owner-scoping and provenance rules — is exactly the duplication this guard
    # exists to prevent, moved inside the package where the guard would bless it.
    "records.py",
    # Batch D. `structured_records` owns the DDL and the row writers for the
    # structured record envelope, its typed field projection and its history.
    # It is trusted with writes for the same reason `facts.py` is: the
    # protections that make a field row safe — the RESTRICTED value going to
    # `cipher_text` rather than `value_text`, `search_text` staying empty for a
    # field the template says may not be indexed, `masked_text` being the only
    # thing a list query reads — live in the writer, not in the table. A direct
    # INSERT would produce a structurally valid row with a passport number in a
    # searchable plaintext column.
    "structured_records.py",
})

PRIVATE_TABLES = (
    "private_facts",
    "private_graph_nodes",
    "private_graph_edges",
    "private_audit_events",
    # Batch C — the six canonical primitives. `private_domain_events` is a
    # separate table from `private_audit_events` on purpose: one records what
    # happened in the member's life, the other records who looked at it, and
    # collapsing them would make the access log editable by the feature that
    # writes life events.
    "private_obligations",
    "private_domain_events",
    "private_decisions",
    "private_requests",
    "private_risks",
    "private_opportunities",
    # Batch D — the structured record store. `private_record_fields` is the one
    # table in this list where a single unguarded INSERT is directly a
    # disclosure rather than a loss of provenance: the row carries its own
    # `sensitivity`, `mask` and `searchable` flags, and a writer that sets them
    # from the request instead of from the server-owned template can mark a
    # passport number CONFIDENTIAL, unmasked and indexable in one statement.
    "private_structured_records",
    "private_record_fields",
    "private_record_revisions",
)

# The schema module exports these; interpolating one into a write statement is
# the same offence as naming the table, just harder to grep for by eye.
# `private_table_for` is the records module's table resolver, listed here for
# the same reason: `records.py` reaches its six tables through that call, so
# without the token in this list every write in the module would be invisible
# to the guard and the allowlist entry above would protect nothing. The name is
# distinctive enough not to appear as a substring of ordinary identifiers.
TABLE_CONSTANTS = ("FACTS_TABLE", "NODES_TABLE", "EDGES_TABLE", "AUDIT_TABLE",
                   "private_table_for")

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
    # Nested worktrees are full checkouts of this repo; scanning them reports
    # the sanctioned writers at a foreign path prefix as boundary violations.
    ".claude",
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


# Every alternative in `_VIOLATION` ends in `_TARGET`, which is a literal
# alternation of the four table names and the four constant names. A match
# therefore requires one of those eight tokens to appear verbatim in a string
# literal, and a string literal's text is a substring of the file's text. So a
# file whose raw bytes contain none of the eight cannot produce a hit, and
# parsing it can be skipped without changing a single verdict.
#
# This is not a shortcut for its own sake. The repository contains ~2,600
# Python files including a 118k-line monolith, and `ast.parse` on all of them
# costs more than a minute — long enough that the guard was being excluded from
# directory runs, which is the one outcome strictly worse than it being slow.
# `test_prefilter_cannot_hide_a_violation` below proves the equivalence rather
# than asserting it, and the parse/skip counts are checked so a prefilter that
# accidentally swallowed the whole tree would fail loudly.
_MENTIONS = re.compile("|".join(PRIVATE_TABLES + TABLE_CONSTANTS))


def mentions_a_private_table(source: str) -> bool:
    return _MENTIONS.search(source) is not None


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
    parsed = 0

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

        in_package = relpath.startswith(PACKAGE_DIR + os.sep)
        if in_package:
            package_seen.add(os.path.basename(relpath))

        # Files inside the package are always parsed — the membership checks
        # depend on it and the package is small. Everything else is parsed only
        # if it could possibly match.
        if not in_package and not mentions_a_private_table(source):
            continue

        try:
            hits = offences(source)
            parsed += 1
        except SyntaxError as exc:
            # Recorded rather than ignored: a file the guard cannot read is a
            # file the guard does not protect, and that has to be visible.
            unparseable.append(f"{relpath}: SyntaxError line {exc.lineno}")
            continue

        if hits and not may_write(relpath):
            for hit in hits:
                violations.append(f"{relpath}: {hit}")

    check("no module outside the private writers writes to a private table",
          not violations, "; ".join(violations[:5]))
    # A prefilter bug that matched nothing would leave this at roughly the size
    # of the package alone and every violation would go unseen.
    check("the prefilter still handed a real body of files to the parser",
          parsed >= 20, f"{parsed} file(s) parsed")

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
    # Batch C. A route or UNDX module writing a primitive directly is the same
    # offence as writing a fact directly and for the same reason: the row would
    # be missing its provenance, its dedupe key, its audit event, and — for an
    # obligation — the guarantee that DUE_SOON was never stored as if it were
    # a fact about the world rather than a fact about the clock.
    "an obligation insert":
        'cur.execute("INSERT INTO private_obligations (owner_user_id) VALUES (?)", (1,))',
    "a domain event insert":
        'cur.execute("INSERT INTO private_domain_events (event_type) VALUES (?)", ("X",))',
    "a decision update that would erase the question":
        'cur.execute("UPDATE private_decisions SET question = ? WHERE id = ?", ("q", 1))',
    "a request delete":
        'cur.execute("DELETE FROM private_requests WHERE id = 1")',
    "a risk insert that would assert safety":
        'cur.execute("insert into private_risks (severity) values (?)", ("LOW",))',
    "an opportunity insert":
        'cur.execute("INSERT INTO private_opportunities (title) VALUES (?)", ("x",))',
    "a rival create table for a primitive":
        'cur.execute("CREATE TABLE IF NOT EXISTS private_risks (id INTEGER)")',
    "a rival index on a primitive":
        'cur.execute("CREATE INDEX IF NOT EXISTS idx_o ON private_obligations (owner_user_id)")',
    "the records resolver interpolated into an insert":
        'cur.execute(f"INSERT INTO {private_table_for(kind)} (id) VALUES (?)", (1,))',
    "the records resolver interpolated into an update":
        'cur.execute(f"UPDATE {private_table_for(kind)} SET status = ?", ("OPEN",))',
    "the records resolver interpolated into a create table":
        'cur.execute(f"CREATE TABLE IF NOT EXISTS {private_table_for(kind)} (id INTEGER)")',
    "the records resolver interpolated into an index":
        'cur.execute(f"CREATE INDEX IF NOT EXISTS idx_x ON {private_table_for(kind)}(owner_user_id)")',
    # Batch D. The first two are the offence the field table exists to make
    # visible: a caller deciding for itself that a value is safe to store in
    # plaintext, or safe to index. Both produce a row the database is happy
    # with and the template would have refused.
    "a field insert that puts a value in the plaintext column":
        'cur.execute("INSERT INTO private_record_fields (value_text) VALUES (?)", (number,))',
    "a field update that would make a masked field searchable":
        'cur.execute("UPDATE private_record_fields SET searchable = 1 WHERE id = ?", (1,))',
    "an envelope insert":
        'cur.execute("INSERT INTO private_structured_records (owner_user_id) VALUES (?)", (1,))',
    "an envelope update that would claim verification":
        'cur.execute("update private_structured_records set verification_state = ?", ("USER_VERIFIED",))',
    "a revision delete that would erase history":
        'cur.execute("DELETE FROM private_record_revisions WHERE record_id = 1")',
    "a rival create table for the field projection":
        'cur.execute("CREATE TABLE IF NOT EXISTS private_record_fields (id INTEGER)")',
    "a rival index on the field projection":
        'cur.execute("CREATE INDEX IF NOT EXISTS idx_f ON private_record_fields (owner_user_id)")',
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
    "a read of a primitive":
        'cur.execute("SELECT id FROM private_obligations WHERE owner_user_id = ?", (1,))',
    "a read through the records resolver":
        'cur.execute(f"SELECT * FROM {private_table_for(kind)} WHERE owner_user_id = ?", (1,))',
    # The token is distinctive on purpose; an identifier that merely ends in
    # "table_for" must not be mistaken for the resolver.
    "an unrelated identifier ending in table_for":
        'x = f"UPDATE {mutable_table_format} SET a = 1"',
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


def test_prefilter_cannot_hide_a_violation():
    """Whatever the guard would catch, the prefilter must let through.

    The scan skips `ast.parse` for files whose raw text names none of the four
    private tables and none of the four table constants. That is only safe if
    every string the guard flags contains one of those eight tokens verbatim —
    so the claim is checked against the same corpus of known offences the guard
    itself is tested with, rather than argued for in a comment.
    """
    print("\n[prefilter equivalence]")
    for label, snippet in sorted(_MUST_CATCH.items()):
        caught = bool(offences(snippet))
        check(f"the prefilter admits the file containing {label}",
              (not caught) or mentions_a_private_table(snippet),
              "prefilter would have skipped a file the guard flags")

    check("the prefilter rejects a file with no private table anywhere in it",
          not mentions_a_private_table(
              'cur.execute("INSERT INTO users (id) VALUES (1)")'))
    check("the prefilter admits a file that merely mentions a private table",
          mentions_a_private_table('x = "SELECT 1 FROM private_facts"'))
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
    test_prefilter_cannot_hide_a_violation()
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
