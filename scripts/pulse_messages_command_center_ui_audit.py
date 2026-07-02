#!/usr/bin/env python3
"""Compatibility entry point for the current PulseSoc Messenger UI audit."""

from __future__ import annotations

import sys

from messenger_v3_audit import run_checks


def main() -> int:
    run_checks()
    print("pulse_messages_command_center_ui_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pulse_messages_command_center_ui_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
