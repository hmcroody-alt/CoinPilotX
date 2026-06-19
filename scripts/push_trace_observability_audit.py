#!/usr/bin/env python3
"""Audit that sanitized push delivery traces reach production stdout."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot.py"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> None:
    source = BOT_PATH.read_text(encoding="utf-8")
    ast.parse(source)

    required = [
        "class _PushTraceOnlyFilter(logging.Filter):",
        '"PUSH_TRACE" in record.getMessage()',
        "logging.StreamHandler(sys.stdout)",
        "_push_trace_stdout_handler.addFilter(_PushTraceOnlyFilter())",
        "logging.getLogger().addHandler(_push_trace_stdout_handler)",
    ]
    for needle in required:
        if needle not in source:
            fail(f"missing push trace stdout logging guard: {needle}")

    if "filename=\"coinpilotx.log\"" not in source:
        fail("existing file logging was unexpectedly removed")

    print("OK: PUSH_TRACE records are mirrored to stdout with a trace-only filter.")


if __name__ == "__main__":
    main()
