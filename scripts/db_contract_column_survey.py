#!/usr/bin/env python3
"""Measure whether the table contract can be extended to columns (brief section 14).

The answer today is NO, and this is the instrument that says so. Keep it so the
decision can be re-litigated with numbers rather than opinion -- the sibling
test ``tests/protection/test_database_contract.py`` points a future engineer
here.

Last run against production ``information_schema.columns`` (read-only):

    13,454 checkable column writes
       115 flagged undeclared        (0.85%)
        88 distinct (table, column)
         2 genuinely absent from prod
        86 present in prod -> FALSE POSITIVE

    precision 2.3%

Why, and why it is not a parser bug you can fix: column DDL in this repo is
predominantly *data*. 1,089 column declarations across 103 tables are written
as ``("name", "TEXT")`` tuples or ``{"name": "TEXT"}`` dicts handed to one of
at least nine generic column-ensuring helpers, several of which take the table
name as a loop variable. This script harvests that data form too -- doing so
moved precision from 2.1% to 2.3%. The remainder is not reachable without
executing the code.

The two true positives are real and are reported as defects:
``conversations.last_message_at`` and ``private_messages.delivery_status``,
both written by ``/api/chat/start`` (bot.py:42305/42309), neither present in
production.

    python3 scripts/db_contract_column_survey.py
    python3 scripts/db_contract_column_survey.py --pairs   # machine-readable
"""

from __future__ import annotations

import ast
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_contract import contract, sqlscan  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INSERT_COLS = re.compile(
    r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
    re.IGNORECASE)
UPDATE_SET = re.compile(
    r"\bUPDATE\s+(?:ONLY\s+)?([A-Za-z_][A-Za-z0-9_]*)\s+SET\s+(.*?)(?:\bWHERE\b|\bRETURNING\b|$)",
    re.IGNORECASE | re.DOTALL)

CREATE_BODY = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)"
    r"[\"'`\]]?\s*\((.*)", re.IGNORECASE | re.DOTALL)
ADD_COLUMN = re.compile(
    r"\bALTER\s+TABLE\s+[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s+"
    r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE)

CONSTRAINT_WORDS = ("primary", "foreign", "unique", "check", "constraint",
                    "key", "index", "exclude")


def _balanced_body(text):
    depth = 0
    out = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        out.append(char)
    return "".join(out)


def _split_top(text):
    depth, token, parts = 0, "", []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(token)
            token = ""
            continue
        token += char
    parts.append(token)
    return parts


def declared_columns(root):
    """table -> set(columns), from CREATE TABLE bodies and ADD COLUMN."""
    cols = collections.defaultdict(set)
    for abs_path in contract.iter_python(root):
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        if contract.scope_of(rel) not in ("runtime", "migration"):
            continue
        try:
            source = open(abs_path, encoding="utf-8", errors="replace").read()
            tree = ast.parse(source)
        except SyntaxError:
            continue
        consts = sqlscan._module_constants(tree)
        docs = sqlscan._docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docs:
                    continue
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = sqlscan._flatten_fstring(node, consts)
            else:
                continue
            if len(text) < 12:
                continue
            clean = sqlscan.strip_sql_noise(text)
            for match in CREATE_BODY.finditer(clean):
                table = match.group(1).lower()
                for part in _split_top(_balanced_body(match.group(2))):
                    part = part.strip()
                    if not part or part.split(" ")[0].lower() in CONSTRAINT_WORDS:
                        continue
                    name = re.match(r"[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)", part)
                    if name:
                        cols[table].add(name.group(1).lower())
            for match in ADD_COLUMN.finditer(clean):
                cols[match.group(1).lower()].add(match.group(2).lower())
    # add_columns_if_missing(cur, "t", [("col", "TEXT"), ...])
    bot = open(os.path.join(root, "bot.py"), encoding="utf-8",
               errors="replace").read()
    for block in re.finditer(
            r"add_columns_if_missing\(\s*cur\s*,\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
            r"\s*,\s*\[(.*?)\]", bot, re.DOTALL):
        table = block.group(1).lower()
        for name in re.findall(r"\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*,",
                               block.group(2)):
            cols[table].add(name.lower())
    return cols


def written_columns(root):
    """(table, column, file, line) for every INSERT column list / UPDATE SET."""
    out = []
    star_inserts = 0
    for abs_path in contract.iter_python(root):
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        if contract.scope_of(rel) != "runtime":
            continue
        try:
            source = open(abs_path, encoding="utf-8", errors="replace").read()
            tree = ast.parse(source)
        except SyntaxError:
            continue
        consts = sqlscan._module_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = sqlscan._flatten_fstring(node, consts)
            else:
                continue
            if len(text) < 12:
                continue
            clean = sqlscan.strip_sql_noise(text)
            for match in INSERT_COLS.finditer(clean):
                table = match.group(1).lower()
                body = match.group(2)
                if sqlscan.DYNAMIC_MARKER in body or "*" in body:
                    star_inserts += 1
                    continue
                for raw in body.split(","):
                    name = re.match(r"\s*[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s*$",
                                    raw)
                    if name:
                        out.append((table, name.group(1).lower(), rel, node.lineno))
            for match in UPDATE_SET.finditer(clean):
                table = match.group(1).lower()
                if table == sqlscan.DYNAMIC_MARKER:
                    continue
                for part in _split_top(match.group(2)):
                    name = re.match(r"\s*[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s*=",
                                    part)
                    if name:
                        out.append((table, name.group(1).lower(), rel, node.lineno))
    return out, star_inserts


# --------------------------------------------------------------------------
# Column DDL expressed as DATA, not SQL -- the dominant form in this repo
# --------------------------------------------------------------------------

#: Generic "add any missing columns" helpers. Their column list is a Python
#: literal, so no SQL-string parser can see it.
COLUMN_HELPERS = {
    "add_columns_if_missing", "_ensure_columns", "ensure_columns",
    "_add_missing_columns", "ensure_table_columns", "_add_columns",
}


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _column_names(node):
    """Names from ``[("n", "TEXT"), ...]`` or ``{"n": "TEXT", ...}``."""
    out = set()
    if isinstance(node, ast.Dict):
        for key in node.keys:
            text = _const_str(key)
            if text:
                out.add(text.lower())
    elif isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            if isinstance(element, (ast.Tuple, ast.List)) and element.elts:
                text = _const_str(element.elts[0])
            else:
                text = _const_str(element)
            if text:
                out.add(text.lower())
    return out


def declared_columns_as_data(root):
    """table -> set(columns), harvested from literal helper call sites."""
    found = collections.defaultdict(set)
    unattributable = collections.defaultdict(set)
    for abs_path in contract.iter_python(root):
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        if contract.scope_of(rel) not in ("runtime", "migration"):
            continue
        try:
            tree = ast.parse(open(abs_path, encoding="utf-8",
                                  errors="replace").read())
        except SyntaxError:
            continue
        binds = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                binds[node.targets[0].id] = node.value
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and len(node.args) >= 2):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", None))
            if name not in COLUMN_HELPERS:
                continue
            table, columns = None, set()
            for arg in node.args:
                text = _const_str(arg)
                if text and table is None and not columns and " " not in text:
                    table = text.lower()
                    continue
                if isinstance(arg, ast.Name) and arg.id in binds:
                    arg = binds[arg.id]
                columns |= _column_names(arg)
            if table and columns:
                found[table] |= columns
            elif columns:
                # table name is a loop variable -- cannot be attributed
                unattributable[rel] |= columns
    return found, unattributable


def main():
    as_pairs = "--pairs" in sys.argv

    declared = declared_columns(ROOT)
    from_data, unattributable = declared_columns_as_data(ROOT)
    for table, columns in from_data.items():
        declared[table] |= columns

    written, skipped = written_columns(ROOT)
    marker = sqlscan.DYNAMIC_MARKER
    real = [w for w in written if w[0] != marker and w[1] != marker]
    checkable = [w for w in real if w[0] in declared]
    violations = [w for w in checkable if w[1] not in declared[w[0]]]
    pairs = sorted({(t, c) for t, c, _, _ in violations})

    if as_pairs:
        json.dump([list(p) for p in pairs], sys.stdout)
        sys.stdout.write("\n")
        return 0

    print(f"tables with a parsed column list : {len(declared)}")
    print(f"  of which from DATA-form DDL    : {len(from_data)} tables, "
          f"{sum(len(v) for v in from_data.values())} columns")
    print(f"  unattributable (table is a var): "
          f"{sum(len(v) for v in unattributable.values())} columns in "
          f"{len(unattributable)} files")
    print(f"column write references          : {len(written)}  "
          f"(dynamic skipped: {skipped + len(written) - len(real)})")
    print(f"  checkable                      : {len(checkable)}")
    print(f"  naming an undeclared column    : {len(violations)}"
          f"  ({100.0 * len(violations) / max(1, len(checkable)):.2f}%)")
    print(f"  distinct (table, column)       : {len(pairs)} "
          f"over {len({t for t, _ in pairs})} tables")
    print()
    print("To finish the measurement, resolve these against production "
          "information_schema.columns (read-only).")
    print("Last run: 2 real, 86 false positives -> 2.3% precision. "
          "See the module docstring.")
    print()
    counts = collections.Counter((t, c) for t, c, _, _ in violations)
    for (table, col), n in counts.most_common(50):
        ex = next(w for w in violations if w[0] == table and w[1] == col)
        print(f"  {n:4d}x {table}.{col:34s} {ex[2]}:{ex[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
