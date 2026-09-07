#!/usr/bin/env python3
"""Extract the web (Flask) route table by AST, not by regex.

A line-window regex over ``bot.py`` mis-attributes: a route whose handler is
short reads the *next* function's ``render_template`` as its own. That is how
``/pulse`` first appeared to serve the static ``pulse_labs.html`` placeholder
when it in fact calls ``pulse_page_html``. Parsing the module and walking each
handler's own body is the only way to get an inventory worth acting on.

Emits JSON on stdout: one record per (rule, handler) with the templates the
handler can actually render, whether it redirects, and whether it returns JSON.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _route_rules(decorator: ast.expr) -> list[str]:
    """The URL rules named by a single decorator, if it is a route decorator."""
    if not isinstance(decorator, ast.Call):
        return []
    func = decorator.func
    if not isinstance(func, ast.Attribute) or func.attr != "route":
        return []
    if not decorator.args:
        return []
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return [first.value]
    return []


def _methods(decorator: ast.expr) -> list[str]:
    if not isinstance(decorator, ast.Call):
        return []
    for keyword in decorator.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
            return [
                element.value
                for element in keyword.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    return []


class HandlerFacts:
    """What a handler's own body does, ignoring anything defined after it."""

    def __init__(self, node: ast.AST) -> None:
        self.templates: list[str] = []
        self.calls: set[str] = set()
        self.redirects = False
        self.jsonify = False
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = self._callee(child.func)
            if not name:
                continue
            self.calls.add(name)
            if name == "render_template":
                if child.args and isinstance(child.args[0], ast.Constant):
                    value = child.args[0].value
                    if isinstance(value, str):
                        self.templates.append(value)
            elif name == "redirect":
                self.redirects = True
            elif name == "jsonify":
                self.jsonify = True

    @staticmethod
    def _callee(func: ast.expr) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return ""


def extract(path: pathlib.Path) -> list[dict]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    records: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        rules: list[str] = []
        methods: list[str] = []
        for decorator in node.decorator_list:
            found = _route_rules(decorator)
            if found:
                rules.extend(found)
                methods.extend(_methods(decorator))
        if not rules:
            continue
        facts = HandlerFacts(node)
        for rule in rules:
            records.append(
                {
                    "rule": rule,
                    "methods": sorted(set(methods)) or ["GET"],
                    "handler": node.name,
                    "source": str(path.relative_to(REPO_ROOT)),
                    "line": node.lineno,
                    "templates": sorted(set(facts.templates)),
                    "redirects": facts.redirects,
                    "jsonify": facts.jsonify,
                    "calls": sorted(facts.calls),
                }
            )
    return records


def main() -> int:
    targets = [REPO_ROOT / "bot.py"]
    targets.extend(sorted((REPO_ROOT / "services").glob("*_routes.py")))
    records: list[dict] = []
    for target in targets:
        if target.exists():
            records.extend(extract(target))
    json.dump(records, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
