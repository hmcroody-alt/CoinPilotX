#!/usr/bin/env python3
"""Static PostgreSQL compatibility audit for app SQL.

This gate is intentionally conservative: it fails on SQL that is known to
behave differently between SQLite and PostgreSQL in production routes.
SQLite-only migration helpers and local audit scripts are reported separately
instead of blocking deployment.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "postgres_compatibility_audit.json"

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "venv",
    "node_modules",
    "reports",
    "static",
    "templates",
}

RUNTIME_PATH_PREFIXES = (
    "bot.py",
    "services/",
    "pulse_communications_v2/",
    "media_worker.py",
    "alert_worker.py",
    "telegram_worker.py",
)

SQL_MARKERS = (
    "SELECT ",
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "CREATE TABLE",
    "ALTER TABLE",
    "CREATE INDEX",
    "WITH ",
)

SAFE_SQLITE_COMPAT_FILES = {
    "services/db.py",
}

SQLITE_PATTERNS = (
    (re.compile(r"\bPRAGMA\b", re.I), "SQLite PRAGMA"),
    (re.compile(r"\bAUTOINCREMENT\b", re.I), "SQLite AUTOINCREMENT"),
    (re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.I), "SQLite INSERT OR IGNORE"),
    (re.compile(r"\blast_insert_rowid\s*\(", re.I), "SQLite last_insert_rowid"),
    (re.compile(r"\bstrftime\s*\(", re.I), "SQLite strftime"),
    (re.compile(r"\bdatetime\s*\(\s*['\"]now['\"]", re.I), "SQLite datetime('now')"),
)


@dataclass
class Finding:
    severity: str
    file: str
    line: int
    kind: str
    detail: str
    sql: str


def iter_source_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix not in {".py", ".sql"}:
            continue
        yield path, rel


def is_runtime_path(rel: str) -> bool:
    return rel in RUNTIME_PATH_PREFIXES or any(rel.startswith(prefix) for prefix in RUNTIME_PATH_PREFIXES if prefix.endswith("/"))


def looks_like_sql(value: str) -> bool:
    normalized = " ".join(str(value or "").split()).upper()
    return any(marker in normalized for marker in SQL_MARKERS)


def extract_python_strings(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and looks_like_sql(node.value):
            yield int(getattr(node, "lineno", 1)), node.value


def extract_sql(path: Path, rel: str):
    if path.suffix == ".sql":
        yield 1, path.read_text(encoding="utf-8", errors="ignore")
    else:
        yield from extract_python_strings(path)


def compact_sql(sql: str) -> str:
    return " ".join(str(sql or "").split())[:700]


def strip_sql_strings(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", sql)


def select_aliases(sql: str) -> set[str]:
    match = re.search(r"\bSELECT\b(?P<select>.*?)\bFROM\b", sql, flags=re.I | re.S)
    if not match:
        return set()
    aliases = set()
    for alias in re.findall(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", match.group("select"), flags=re.I):
        aliases.add(alias.lower())
    return aliases


def segment_after(keyword: str, sql: str) -> str:
    match = re.search(rf"\b{keyword}\b(?P<body>.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|\bOFFSET\b|$)", sql, flags=re.I | re.S)
    return match.group("body") if match else ""


def alias_references(segment: str, aliases: set[str]) -> set[str]:
    clean = strip_sql_strings(segment)
    found = set()
    for alias in aliases:
        if re.search(rf"(?<![.])\b{re.escape(alias)}\b", clean, flags=re.I):
            found.add(alias)
    return found


def analyze_sql(rel: str, line: int, sql: str) -> list[Finding]:
    findings: list[Finding] = []
    aliases = select_aliases(sql)
    if aliases:
        having_refs = alias_references(segment_after("HAVING", sql), aliases)
        for alias in sorted(having_refs):
            findings.append(Finding("fail", rel, line, "having_alias", f"PostgreSQL does not allow SELECT alias '{alias}' in HAVING.", compact_sql(sql)))
        group_refs = alias_references(segment_after("GROUP BY", sql), aliases)
        for alias in sorted(group_refs):
            findings.append(Finding("fail", rel, line, "group_by_alias", f"PostgreSQL does not allow SELECT alias '{alias}' in GROUP BY.", compact_sql(sql)))

    for pattern, label in SQLITE_PATTERNS:
        if not pattern.search(sql):
            continue
        severity = "info"
        if is_runtime_path(rel) and rel not in SAFE_SQLITE_COMPAT_FILES:
            if label in {"SQLite PRAGMA", "SQLite last_insert_rowid", "SQLite strftime"}:
                severity = "fail"
        findings.append(Finding(severity, rel, line, "sqlite_specific_sql", label, compact_sql(sql)))
    return findings


def main() -> int:
    all_findings: list[Finding] = []
    for path, rel in iter_source_files():
        for line, sql in extract_sql(path, rel):
            all_findings.extend(analyze_sql(rel, line, sql))

    fail = [finding for finding in all_findings if finding.severity == "fail"]
    warn = [finding for finding in all_findings if finding.severity == "warn"]
    info = [finding for finding in all_findings if finding.severity == "info"]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "summary": {"fail": len(fail), "warn": len(warn), "info": len(info)},
                "findings": [asdict(finding) for finding in all_findings],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"PostgreSQL compatibility audit: fail={len(fail)} warn={len(warn)} info={len(info)}")
    print(f"report={REPORT.relative_to(ROOT)}")
    for finding in fail[:30]:
        print(f"FAIL {finding.file}:{finding.line} {finding.kind}: {finding.detail}")
    for finding in warn[:20]:
        print(f"WARN {finding.file}:{finding.line} {finding.kind}: {finding.detail}")
    if fail:
        return 1
    print("postgres compatibility audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
