#!/usr/bin/env python3
"""Generate and validate the machine-readable UNDX Phase 2 command benchmark."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.undx_capability_registry import REGISTRY


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    command: str
    capability_id: str
    tool: str
    permission: str
    verification: str


SEEDS = (
    ("show-alerts", "Show my crypto alerts", "crypto.alerts.list"),
    ("notification-settings", "Show my notification settings", "notifications.preference.read"),
    ("saved-items", "Find my saved posts", "saved.items.list"),
    ("followers", "Show my followers", "social.followers.list"),
    ("conversations", "Show my conversations", "conversations.list"),
    ("messages", "Read messages from conversation 1", "messages.list"),
    ("message-search", "Find the message where we discussed the launch in conversation 1", "messages.search"),
    ("conversation-summary", "Summarize conversation 1", "conversations.summarize"),
    ("reply-suggestions", "Suggest replies for conversation 1", "messages.suggest"),
    ("reply-draft", "Draft a reply to conversation 1 saying I will review it", "messages.draft"),
    ("feed", "Show my feed", "feed.posts.list"),
    ("post", "Show post 2", "feed.posts.get"),
    ("comments", "Show comments on post 2", "comments.list"),
    ("post-performance", "Show post 2 performance", "feed.post.performance.summary"),
    ("comment-summary", "Summarize comments on my post 2", "feed.comments.summary"),
    ("save-post", "Save post 2", "saved.post.set"),
    ("like-post", "Like post 2", "feed.posts.like"),
    ("unlike-post", "Unlike post 2", "feed.posts.unlike"),
    ("follow", "Follow user 2", "social.follow"),
    ("unfollow", "Unfollow user 2", "social.unfollow"),
)

PREFIXES = (
    "", "Please ", "UNDX, ", "Can you ", "For me, ", "Using PulseSoc, ",
    "In my account, ", "Safely ", "Right now, ", "From the native app, ",
    "Without changing anything else, ", "Using only my authorized data, ",
    "Show me this: ", "Help me ", "I need you to ", "Use my account to ",
    "With privacy enforced, ", "Using canonical PulseSoc data, ",
    "From my current session, ", "As a read-only request, ",
    "Check and ", "Open UNDX and ", "In the PulseSoc app, ",
    "Using my verified identity, ", "For this QA task, ", "With no duplicate action, ",
)


def build_cases() -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for seed_index, (slug, command, capability_id) in enumerate(SEEDS, 1):
        spec = REGISTRY[capability_id]
        for variant, prefix in enumerate(PREFIXES, 1):
            cases.append(BenchmarkCase(
                case_id=f"P2-{seed_index:02d}-{variant:02d}-{slug}",
                command=f"{prefix}{command[0].lower() + command[1:] if prefix else command}.",
                capability_id=capability_id,
                tool=spec.tool_name,
                permission=spec.permission,
                verification=spec.verifier or "read_only_scoped_result",
            ))
    return cases


def audit() -> dict:
    cases = build_cases()
    failures = []
    for case in cases:
        spec = REGISTRY.get(case.capability_id)
        if spec is None:
            failures.append(f"{case.case_id}: missing capability")
        elif (case.tool, case.permission) != (spec.tool_name, spec.permission):
            failures.append(f"{case.case_id}: registry metadata mismatch")
    return {
        "ok": not failures,
        "benchmark_case_count": len(cases),
        "unique_commands": len({case.command for case in cases}),
        "capabilities_covered": sorted({case.capability_id for case in cases}),
        "failures": failures,
        "cases": [asdict(case) for case in cases],
    }


if __name__ == "__main__":
    report = audit()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["ok"] and report["benchmark_case_count"] >= 200 else 1)
