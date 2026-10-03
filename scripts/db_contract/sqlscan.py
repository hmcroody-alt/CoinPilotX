"""Structural extraction of SQL facts from Python source.

Why this is AST-based and anchored on the *execute call*, not on string content:
a text scan of this repo for ``FROM <ident>`` returns 12,877 hits, and the most
common "tables" it finds are ``the``, ``your``, ``this`` and ``an`` -- English
prose inside docstrings and server-rendered HTML ("UPDATE your profile",
"selected FROM the list"). A gate built on that is noise, and worse, the
inverse failure is real: after the ``private_chat_blocks`` fix landed, the dead
table name still appeared in ``bot.py`` as *a comment explaining the fix*, so a
text guard would have reported the defect as still present.

So the unit of analysis here is: a call to ``.execute``/``.executemany`` whose
first argument resolves to a string we can read. 84% of runtime call sites pass
a plain literal, 11% an f-string, 3% a local variable holding one. The rest are
reported as UNKNOWN rather than assumed safe (brief section 7).
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# What counts as "running SQL"
# --------------------------------------------------------------------------

EXEC_METHODS = {"execute", "executemany", "executescript"}

# Objects the database provides. Naming one is not a contract violation.
BUILTIN_OBJECTS = {
    "sqlite_master", "sqlite_sequence", "sqlite_temp_master", "dual",
    "generate_series", "unnest", "json_each", "jsonb_each",
    "json_array_elements", "jsonb_array_elements", "json_to_recordset",
    "jsonb_to_recordset", "regexp_split_to_table", "string_to_table",
}
BUILTIN_PREFIXES = ("information_schema.", "pg_catalog.", "pg_", "sqlite_")

# Tokens the reference regexes can grab that are never a table.
NON_TABLE_TOKENS = {
    "select", "values", "lateral", "only", "table", "set", "where",
    "__dynamic__",
}

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"
#: A table may be written bare or as a quoted identifier: FROM "users".
_QIDENT = r"[\"`\[]?(" + _IDENT + r")[\"`\]]?"

_REF_PATTERNS = (
    ("select", re.compile(r"\bFROM\s+" + _QIDENT, re.IGNORECASE)),
    ("join", re.compile(r"\bJOIN\s+" + _QIDENT, re.IGNORECASE)),
    ("insert", re.compile(
        r"\bINSERT\s+(?:OR\s+(?:REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\s+)?INTO\s+"
        + _QIDENT, re.IGNORECASE)),
    ("insert", re.compile(r"\bREPLACE\s+INTO\s+" + _QIDENT, re.IGNORECASE)),
    ("update", re.compile(r"\bUPDATE\s+(?:ONLY\s+)?" + _QIDENT, re.IGNORECASE)),
    ("delete", re.compile(r"\bDELETE\s+FROM\s+(?:ONLY\s+)?" + _QIDENT, re.IGNORECASE)),
    ("truncate", re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?" + _QIDENT, re.IGNORECASE)),
)

#: SQL comments and string *data*. Both contain English, and English contains
#: the words FROM and JOIN. Three live false positives came from exactly this:
#:   -- §9. Read for `seller_verdict` and stripped from the payload
#:   VALUES (?, ?, 'Marked safe from admin security center.', ...)
#:   -- LEFT JOIN, not JOIN: push is a DELIVERY channel ...
#: yielding tables named `the`, `admin`, `here` and `survives`. Double quotes
#: are NOT stripped: in PostgreSQL they delimit an identifier, not a string.
_SQL_NOISE = re.compile(
    r"--[^\n]*"                 # line comment
    r"|/\*.*?\*/"               # block comment
    r"|'(?:[^']|'')*'",         # single-quoted literal, '' escape included
    re.DOTALL)


def strip_sql_noise(sql: str) -> str:
    """Blank out comments and quoted data, preserving offsets and newlines."""
    def blank(match):
        return re.sub(r"[^\n]", " ", match.group(0))
    return _SQL_NOISE.sub(blank, sql)

_CTE = re.compile(r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(",
                  re.IGNORECASE)

_CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:TEMP\s+|TEMPORARY\s+|UNLOGGED\s+)?TABLE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(" + _IDENT + r")",
    re.IGNORECASE)
_CREATE_VIEW = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\s+|TEMPORARY\s+|MATERIALIZED\s+)?VIEW\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(" + _IDENT + r")",
    re.IGNORECASE)
_CREATE_INDEX = re.compile(
    r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s+ON\s+[\"'`\[]?(" + _IDENT + r")",
    re.IGNORECASE)
_ALTER_TABLE = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?[\"'`\[]?(" + _IDENT + r")",
    re.IGNORECASE)

# A string has to look like a statement before we read table names out of it.
_SQL_STATEMENT = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*"
    r"(WITH|SELECT|INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE)\b",
    re.IGNORECASE | re.DOTALL)

DYNAMIC_MARKER = "__dynamic__"

#: Identifier shapes used for the column-level rules. Applied only to SQL that
#: has already been resolved from an execute() argument and had its comments and
#: string literals blanked -- never to raw file text.
_BARE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUALIFIED = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Reference:
    """One table named by one executed statement."""
    path: str
    line: int
    func: str
    table: str
    verb: str          # select / insert / update / delete / truncate / join
    resolution: str    # literal / fstring / name / unresolved
    snippet: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Declaration:
    """One table created by one DDL statement."""
    path: str
    line: int
    func: str
    table: str
    kind: str          # table / view


@dataclass(frozen=True)
class Statement:
    """One executed statement, reduced to the names it mentions.

    Carried so that a *column*-level rule can be evaluated against resolved SQL
    rather than against file text. The distinction is the whole point: after the
    message-privacy fix, ``bot.py`` still contains the string
    ``private_chat_blocks`` -- inside a comment explaining the fix. Anything that
    greps the file reports a closed defect as open.
    """
    path: str
    line: int
    func: str
    tables: tuple          # table names this statement names
    identifiers: frozenset  # every bare identifier, lowercased
    qualified: frozenset    # every ``a.b`` pair seen, lowercased
    snippet: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass
class FileFacts:
    path: str
    references: list = field(default_factory=list)
    declarations: list = field(default_factory=list)
    alters: list = field(default_factory=list)       # (table, line, func)
    indexes: list = field(default_factory=list)      # (index, table, unique, line)
    unresolved: list = field(default_factory=list)   # (line, func, shape)
    statements: list = field(default_factory=list)   # [Statement]


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def _is_builtin(name: str) -> bool:
    low = name.lower()
    if low in BUILTIN_OBJECTS:
        return True
    return any(low.startswith(p) for p in BUILTIN_PREFIXES)


def table_references(sql: str):
    """Yield (verb, table) for a single SQL string.

    Comments and quoted string data are blanked first, then CTE names are
    collected and excluded: ``WITH recent AS (...) SELECT * FROM recent``
    names no table called ``recent``.
    """
    sql = strip_sql_noise(sql)
    ctes = {m.group(1).lower() for m in _CTE.finditer(sql)}
    seen = set()
    for verb, pattern in _REF_PATTERNS:
        for match in pattern.finditer(sql):
            name = match.group(1).lower()
            if name in NON_TABLE_TOKENS or name in ctes or _is_builtin(name):
                continue
            key = (verb, name)
            if key in seen:
                continue
            seen.add(key)
            yield verb, name


def _looks_like_sql(text: str) -> bool:
    return bool(_SQL_STATEMENT.match(text))


def _flatten_fstring(node: ast.JoinedStr, constants=None) -> str:
    """Render an f-string, substituting module constants we can resolve.

    ``f"SELECT x FROM orders WHERE id={oid}"`` keeps ``orders``.
    ``f"SELECT x FROM {tbl}"`` yields ``FROM __dynamic__`` and is reported as
    unresolved rather than quietly skipped.

    Constant substitution is not an optimisation, it is required for
    correctness. A large family of schema modules here declares tables as::

        CART_TABLE = "marketplace_cart_items"
        _DDL = f"CREATE TABLE IF NOT EXISTS {CART_TABLE} (...)"

    Without resolving ``CART_TABLE`` the declaration is invisible and the
    gate reports a live, populated production table as undeclared. Three of
    the first four findings this tool produced were exactly that mistake.
    """
    constants = constants or {}
    out = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.append(value.value)
            continue
        resolved = None
        if isinstance(value, ast.FormattedValue):
            inner = value.value
            if isinstance(inner, ast.Name):
                resolved = constants.get(inner.id)
            elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                resolved = inner.value
        out.append(resolved if resolved is not None else f" {DYNAMIC_MARKER} ")
    return "".join(out)


#: Helpers that answer "is this table provisioned?" before it is used. There
#: are ~15 copies of this function across bot.py and the dashboard services,
#: all with the same meaning, so the check is by name suffix.
def _is_existence_probe(func) -> bool:
    name = getattr(func, "attr", None) or getattr(func, "id", None) or ""
    return name.endswith("table_exists") or name.endswith("has_table")


def _existence_guards(tree):
    """Line ranges in which a table is known to have been probed first.

    An undeclared table is not automatically a defect. This is correct code::

        "queue_failures": admin_safe_count(cur, "SELECT COUNT(*) FROM platform_dead_letters")
                          if table_exists(cur, "platform_dead_letters") else 0,

    The author knows the table is optional and has said so in the code. That is
    the opposite of the ``private_chat_blocks`` failure, where absence was
    neither checked nor logged. Treating both the same way would bury the real
    defect in noise and would punish the one idiom we want people to use.

    Returns [(table, first_line, last_line)].
    """
    guards = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.IfExp)):
            continue
        for call in ast.walk(node.test):
            if not isinstance(call, ast.Call) or not _is_existence_probe(call.func):
                continue
            for arg in call.args:
                name = _const_str(arg)
                if name:
                    guards.append((name.lower(), node.lineno,
                                   getattr(node, "end_lineno", node.lineno)))
    return guards


def _sql_forwarders(tree):
    """Functions that forward one of their own parameters into ``execute``.

    ``bot.py`` is full of small local helpers::

        def scalar(query, params=()):
            try:
                cur.execute(query, params)
                return int((dict(cur.fetchone() or {}).get("total") or 0))
            except Exception:
                return 0

    Anchoring only on ``.execute`` makes every ``scalar("SELECT ... FROM x")``
    invisible. That is not a theoretical gap: it hid the second
    ``conversion_events`` read, plus ``product_events`` and
    ``product_health_events`` -- three reads of tables that do not exist in
    production, inside a helper whose except branch returns 0.

    Returns {function_name: arg_index}.
    """
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in node.args.args]
        if not params:
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in EXEC_METHODS
                    and inner.args
                    and isinstance(inner.args[0], ast.Name)
                    and inner.args[0].id in params):
                out[node.name] = params.index(inner.args[0].id)
                break
    return out


def _module_constants(tree):
    """Module-level ``NAME = "literal"`` bindings, for f-string resolution."""
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = _const_str(node.value)
        if value is None or len(value) > 200:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out.setdefault(target.id, value)
    return out


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _docstring_nodes(tree):
    """Ids of every Constant that is a docstring.

    A docstring is prose, never DDL, and counting it as a declaration would
    make the gate satisfiable by *documentation*. That is not hypothetical:
    after the ``private_chat_blocks`` fix landed, the dead table name still
    appeared in ``bot.py`` -- in a comment explaining the fix. A guard that
    reads prose as code reports a closed defect as open, and (here, in the
    declaration direction) would report an open one as closed.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            out.add(id(first.value))
    return out


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str, docstrings=frozenset(), constants=None,
                 forwarders=None, guards=()):
        self.facts = FileFacts(path=path)
        self._guards = tuple(guards)
        self._docstrings = docstrings
        self._constants = constants or {}
        self._forwarders = forwarders or {}
        self._scope = []
        self._assignments = [{}]   # stack of name -> ast node, module then function

    # -- scope tracking ----------------------------------------------------

    def _enter_scope(self, node):
        self._scope.append(node.name)
        self._assignments.append({})
        # Pre-pass so `sql = "..."` below a use still resolves.
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                self._note_assign(child)
        self.generic_visit(node)
        self._assignments.pop()
        self._scope.pop()

    visit_FunctionDef = _enter_scope
    visit_AsyncFunctionDef = _enter_scope
    visit_ClassDef = _enter_scope

    def _note_assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._assignments[-1].setdefault(target.id, node.value)

    def visit_Assign(self, node):
        self._note_assign(node)
        self.generic_visit(node)

    @property
    def _func(self):
        return ".".join(self._scope) or "<module>"

    def _lookup(self, name):
        for frame in reversed(self._assignments):
            if name in frame:
                return frame[name]
        return None

    # -- DDL: any string literal anywhere, because DDL is a declaration -----
    #    (a CREATE TABLE in a docstring is harmless noise for declarations:
    #     it can only ever *widen* what we consider declared, and we report
    #     declared-but-unreferenced separately rather than acting on it.)

    def visit_Constant(self, node):
        text = node.value
        if not isinstance(text, str) or len(text) < 12:
            return
        if id(node) in self._docstrings:
            return
        self._scan_ddl(text, node.lineno)

    def visit_JoinedStr(self, node):
        """DDL is frequently an f-string over a module constant, so the
        declaration scan has to look here too -- see _flatten_fstring."""
        text = _flatten_fstring(node, self._constants)
        if len(text) >= 12:
            self._scan_ddl(text, node.lineno)
        for value in node.values:
            if not isinstance(value, ast.Constant):
                self.visit(value)

    def _scan_ddl(self, text, lineno):
        text = strip_sql_noise(text)
        for match in _CREATE_TABLE.finditer(text):
            name = match.group(1).lower()
            if name == DYNAMIC_MARKER:
                # CREATE TABLE {something_we_cannot_resolve}: this declares a
                # table but we cannot say which, so it must not satisfy any
                # reference. Recorded as unresolved, never as a declaration.
                self.facts.unresolved.append((lineno, self._func, "interpolated-ddl"))
                continue
            self.facts.declarations.append(
                Declaration(self.facts.path, lineno, self._func, name, "table"))
        for match in _CREATE_VIEW.finditer(text):
            self.facts.declarations.append(
                Declaration(self.facts.path, lineno, self._func,
                            match.group(1).lower(), "view"))
        for match in _ALTER_TABLE.finditer(text):
            self.facts.alters.append((match.group(1).lower(), lineno, self._func))
        for match in _CREATE_INDEX.finditer(text):
            self.facts.indexes.append(
                (match.group(2).lower(), match.group(3).lower(),
                 bool(match.group(1)), lineno))

    # -- DML: only what an execute() call actually runs ---------------------

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in EXEC_METHODS and node.args:
            self._scan_execute(node, node.args[0])
        elif isinstance(func, ast.Name) and func.id in self._forwarders:
            index = self._forwarders[func.id]
            if len(node.args) > index:
                self._scan_execute(node, node.args[index])
        self.generic_visit(node)

    def _scan_execute(self, call, arg):
        sql, resolution = self._resolve(arg)
        line = getattr(call, "lineno", 0)
        if sql is None:
            self.facts.unresolved.append(
                (line, self._func, type(arg).__name__))
            return
        if not _looks_like_sql(sql):
            # e.g. PRAGMA, SET, BEGIN, VACUUM -- no table contract to check.
            return
        if DYNAMIC_MARKER in sql:
            for verb, table in table_references(sql):
                if table == DYNAMIC_MARKER:
                    self.facts.unresolved.append((line, self._func, "interpolated-table"))
        self._record_statement(sql, line)
        for verb, table in table_references(sql):
            if table == DYNAMIC_MARKER:
                continue
            if self._guarded(table, line):
                resolution_kind = "guarded"
            else:
                resolution_kind = resolution
            self.facts.references.append(
                Reference(self.facts.path, line, self._func, table, verb,
                          resolution_kind, _snippet(sql)))

    def _record_statement(self, sql, line):
        """Reduce one resolved statement to the names it mentions.

        Comments and single-quoted literals are already blanked by
        ``strip_sql_noise``, so prose inside the SQL cannot contribute an
        identifier.
        """
        clean = strip_sql_noise(sql)
        tables = tuple(sorted({t for _v, t in table_references(sql)
                               if t != DYNAMIC_MARKER}))
        idents = frozenset(m.group(0).lower()
                           for m in _BARE_IDENT.finditer(clean))
        qualified = frozenset(f"{m.group(1).lower()}.{m.group(2).lower()}"
                              for m in _QUALIFIED.finditer(clean))
        if not idents:
            return
        self.facts.statements.append(
            Statement(self.facts.path, line, self._func, tables, idents,
                      qualified, _snippet(sql)))

    def _guarded(self, table, line):
        return any(name == table and lo <= line <= hi
                   for name, lo, hi in self._guards)

    def _resolve(self, arg, depth=0):
        """Return (sql_text, how) or (None, reason)."""
        if depth > 3:
            return None, "deep"
        text = _const_str(arg)
        if text is not None:
            return text, "literal"
        if isinstance(arg, ast.JoinedStr):
            return _flatten_fstring(arg, self._constants), "fstring"
        if isinstance(arg, ast.Name):
            target = self._lookup(arg.id)
            if target is None:
                return None, "unbound-name"
            text, how = self._resolve(target, depth + 1)
            return (text, "name") if text is not None else (None, how)
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            left, _ = self._resolve(arg.left, depth + 1)
            right, _ = self._resolve(arg.right, depth + 1)
            if left is None and right is None:
                return None, "concat"
            return ((left or f" {DYNAMIC_MARKER} ")
                    + (right or f" {DYNAMIC_MARKER} ")), "concat"
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
            left, _ = self._resolve(arg.left, depth + 1)
            if left is None:
                return None, "percent"
            return left.replace("%s", DYNAMIC_MARKER), "percent"
        if isinstance(arg, ast.Call):
            f = arg.func
            # text("...") -- SQLAlchemy
            if isinstance(f, ast.Name) and f.id == "text" and arg.args:
                return self._resolve(arg.args[0], depth + 1)
            # "...".format(...) / "...".replace(...)
            if isinstance(f, ast.Attribute) and f.attr in ("format", "replace", "strip"):
                return self._resolve(f.value, depth + 1)
            return None, f"call:{getattr(f, 'attr', getattr(f, 'id', '?'))}"
        return None, type(arg).__name__


def _snippet(sql: str, limit: int = 90) -> str:
    flat = " ".join(sql.split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def scan_source(path: str, source: str) -> FileFacts:
    tree = ast.parse(source)
    scanner = _Scanner(path, _docstring_nodes(tree), _module_constants(tree),
                       _sql_forwarders(tree), _existence_guards(tree))
    scanner.visit(tree)
    return scanner.facts


def scan_file(root: str, abs_path: str) -> FileFacts:
    rel = os.path.relpath(abs_path, root)
    with open(abs_path, encoding="utf-8", errors="replace") as handle:
        return scan_source(rel, handle.read())
