#!/usr/bin/env python3
"""Audit generated Pulse handles use Pulse branding."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "bot.py",
    ROOT / "services" / "pulse_identity_engine.py",
    ROOT / "services" / "chat_realtime_service.py",
    ROOT / "services" / "roast_battle_engine.py",
    ROOT / "services" / "roast_live_engine.py",
]


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    sources = {path.name: path.read_text(encoding="utf-8") for path in FILES}
    combined = "\n".join(sources.values())
    failures = []

    for token in [
        'return f"@Pulse-{suffix}"',
        'base = "Pulse"',
        'f"Pulse-{str(user_id)[-6:]}"',
        'f"Pulse-{str(abs(int(row.get(',
        'f"Pulse-{int(user_id)}"',
        "'Pulse-' || p.user_id",
    ]:
        require(token in combined, f"Pulse handle fallback missing: {token}", failures)

    disallowed = [
        'return f"@Pilot-',
        'base = "Pilot"',
        'f"Pilot-{',
        'f"pilot-{int(user_id)}"',
        'f"pilot-{str(user_id)',
    ]
    for token in disallowed:
        require(token not in combined, f"old Pilot generated handle remains: {token}", failures)

    require('startswith(("pilot-", "pulse-"))' in combined, "legacy pilot lookup compatibility is missing", failures)

    if failures:
        print("Pulse default handle branding audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse default handle branding audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
