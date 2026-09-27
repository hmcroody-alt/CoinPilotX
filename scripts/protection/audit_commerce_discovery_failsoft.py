#!/usr/bin/env python3
"""Which of this package's fail-soft paths does the suite actually walk?

`services/commerce_discovery/` is built to degrade rather than fail: a broken
read loses a signal, not a request. §19 of the delivery report shows what that
costs — 48 tests were green against an engine that could not run, because
`engine.serve`'s fail-safe made a crash and a decision the same value. That was
*one* handler. This script inventories the rest and asks, per handler, whether any
test has ever caused it to run.

Three states, and only one of them is fine:

``exercised``
    a test reaches it. Whatever it does on failure is tested behaviour.

``never``
    no test in the package reaches it. The code is a claim about what happens
    when something breaks, and nothing has checked the claim. The fixture
    comments in `conftest.py` already name two of these by hand — the money
    columns and the follow graph were added specifically because "a fail-soft
    path that no test ever leaves is indistinguishable from a read that does not
    work". This is that audit, mechanised.

``silent``
    reached, but the handler neither logs nor re-raises. Production cannot tell
    it happened; neither can a test, except by noticing a missing value.

Run it::

    python3 scripts/protection/audit_commerce_discovery_failsoft.py

It runs the package's own pytest suite under a line tracer, so it needs nothing
installed that the tests do not already need — deliberately, because `coverage`
is not a dependency of this repo and a measurement that requires one more install
than the suite is a measurement nobody re-runs.

Exit status is 0 whatever it finds. This is an inventory, not a gate: a `never`
handler is a question ("can this actually happen?"), and some of the answers are
legitimately "no" — §11a of the report argues that some fail-soft paths *should*
survive mutation. Turning it into a gate would force tests for unreachable
branches, which buys a green tick and no safety.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE = os.path.join(REPO, "services", "commerce_discovery")
TESTS = os.path.join(REPO, "tests", "commerce_discovery")


@dataclass
class Handler:
    """One ``except`` clause that does not let the exception out."""

    module: str
    function: str
    lineno: int          # the `except` line, for a human to go and read
    watch: int           # the first line of the body, which is what the tracer sees
    caught: str          # what it catches, as written
    logs: bool
    reraises: bool
    hit: bool = False

    @property
    def state(self) -> str:
        if not self.hit:
            return "never"
        return "exercised" if (self.logs or self.reraises) else "silent"

    @property
    def where(self) -> str:
        return f"{self.module}:{self.lineno} ({self.function})"


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    """line number -> the name of the innermost function containing it."""
    owner: dict[int, str] = {}
    span: dict[int, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        for line in range(node.lineno, end + 1):
            # Innermost wins. `ast.walk` is breadth-first, so a nested def is not
            # reliably visited after its parent — prefer the *smaller* span rather
            # than the later write.
            if (end - node.lineno) < span.get(line, 1 << 30):
                owner[line] = node.name
                span[line] = end - node.lineno
    return owner


def _catches(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "bare"
    try:
        return ast.unparse(handler.type)
    except Exception:  # pragma: no cover - ast.unparse is available on 3.9+
        return "?"


def _logs(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in {"LOGGER", "logger", "log"}:
                return True
    return False


def _reraises(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def inventory() -> list[Handler]:
    found: list[Handler] = []
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(PACKAGE, name)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        owners = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not node.body:
                continue
            found.append(Handler(
                module=name,
                function=owners.get(node.lineno, "<module>"),
                lineno=node.lineno,
                watch=node.body[0].lineno,
                caught=_catches(node),
                logs=_logs(node),
                reraises=_reraises(node),
            ))
    return found


def run_suite_under_tracer(handlers: list[Handler]) -> int:
    """Run the package's tests, recording which handler bodies execute."""
    watching: dict[tuple[str, int], Handler] = {
        (handler.module, handler.watch): handler for handler in handlers
    }
    package = PACKAGE + os.sep

    def local_trace(frame, event, _arg):
        if event == "line":
            key = (os.path.basename(frame.f_code.co_filename), frame.f_lineno)
            handler = watching.get(key)
            if handler is not None:
                handler.hit = True
        return local_trace

    def global_trace(frame, event, arg):
        # The filter that makes this affordable: every frame outside the package
        # gets no line tracing at all.
        if frame.f_code.co_filename.startswith(package):
            return local_trace(frame, event, arg)
        return None

    import pytest

    sys.settrace(global_trace)
    try:
        status = pytest.main([TESTS, "-q", "-p", "no:randomly", "--no-header"])
    finally:
        sys.settrace(None)
    return int(status)


def report(handlers: list[Handler]) -> None:
    by_state: dict[str, list[Handler]] = {"never": [], "silent": [], "exercised": []}
    for handler in handlers:
        by_state[handler.state].append(handler)

    total = len(handlers)
    print()
    print("=" * 78)
    print(f"{total} fail-soft handlers in services/commerce_discovery/")
    print("=" * 78)
    for state, blurb in (
        ("never", "NEVER REACHED by any test in the package"),
        ("silent", "reached, but neither logged nor re-raised"),
        ("exercised", "reached, and says so"),
    ):
        group = by_state[state]
        print(f"\n{len(group):>3} {blurb}")
        for handler in sorted(group, key=lambda h: (h.module, h.lineno)):
            flags = []
            if handler.logs:
                flags.append("logs")
            if handler.reraises:
                flags.append("may re-raise")
            if handler.caught == "bare":
                flags.append("BARE except")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"      {handler.where:<52} except {handler.caught}{suffix}")

    counts: dict[str, list[int]] = {}
    for handler in handlers:
        counts.setdefault(handler.module, [0, 0])
        counts[handler.module][0] += 1
        if handler.state == "never":
            counts[handler.module][1] += 1
    print("\nper module (never / total)")
    for module in sorted(counts):
        seen, never = counts[module]
        print(f"      {module:<24} {never:>3} / {seen:>3}")
    print()


def main() -> int:
    handlers = inventory()
    if not handlers:
        print("no except handlers found - has the package moved?", file=sys.stderr)
        return 1
    status = run_suite_under_tracer(handlers)
    report(handlers)
    if status != 0:
        print(
            "NOTE: the suite did not pass, so 'never' below may mean "
            "'the test that would reach it did not run'.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
