#!/usr/bin/env python3
"""Audit the Pulse Spaces autonomous posting rotation scheduler."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "services" / "pulse_ai" / "space_post_scheduler.py"


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    source = SCHEDULER.read_text(encoding="utf-8")
    failures = []

    require("ROTATION_INTERVAL_HOURS = 3" in source, "rotation interval is not set to 3 hours", failures)
    require("pulse_ai_rotation_state" in source, "rotation state table is missing", failures)
    require("next_index" in source and "next_run_at" in source, "rotation pointer is not persisted", failures)
    require("last_run_key" in source, "duplicate tick guard is missing", failures)
    require("SPACE_AI_ROTATION_SELECTED" in source, "selected Space log is missing", failures)
    require("SPACE_AI_ROTATION_POST_CREATED" in source, "post-created log is missing", failures)
    require("SPACE_AI_ROTATION_NEXT" in source, "next Space log is missing", failures)
    require("ROTATION_SLOT" in source and "rotation_key" in source, "rotation metadata is not attached to posts", failures)
    require("publish_space_ai_post(" in source, "scheduler does not publish a selected Space post", failures)
    require("ran\": 1" in source or '"ran": 1' in source, "scheduler may still run multiple Spaces per tick", failures)
    require("SCHEDULE_SLOTS" not in source, "legacy morning/evening schedule slots are still enabled", failures)
    require("next_run_for_slot" not in source, "legacy per-slot next-run logic is still present", failures)
    require("_already_posted_today" not in source, "legacy daily duplicate guard is still present", failures)
    require(
        re.search(r"slot, ''\) IN \('morning', 'afternoon', 'evening'\)", source),
        "legacy morning/afternoon/evening schedules are not explicitly disabled",
        failures,
    )

    if failures:
        print("Pulse Spaces scheduler audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse Spaces scheduler audit passed: one Space rotates every 3 hours with persisted state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
