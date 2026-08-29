#!/usr/bin/env python3
"""Which gate is closed? — the acceptance sentence run under each flag shape.

Phase 2 of the production runtime mission asks for a *proved* root cause rather than
a plausible one. The local suite passes and production does not, and the honest first
question is not "what is broken in the code" but "is the code even reached". This
script answers that by holding the code constant and varying only the environment,
which is the one axis that differs between a green test run and a live Railway
container.

Each row runs the mission's own acceptance sentence — "Like my most recent post."
then "Yes" — against a real temporary SQLite database with a real post seeded, and
records what a person would actually see. A configuration that never reaches the
agent produces a blank turn here, which is the same blank turn it produces in
production; that correspondence is the evidence, not the assertion.

Run from the repository root:

    python3 scripts/undx_production_gate_probe.py

It writes nothing outside its own temporary databases and contacts no network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


#: The flag shapes worth distinguishing. Each is a plausible production state, and
#: each fails in a *different* place — which is the point: the symptom a user reports
#: ("it just talks, it never does anything") is identical across most of them, so a
#: report of the symptom alone cannot choose between them.
SHAPES: tuple[tuple[str, dict[str, str]], ...] = (
    ("fully enabled, caller in cohort (the local test's shape)", {}),
    ("master flag unset", {"UNDX_AGENT_ENABLED": ""}),
    ("enabled, but the caller is outside the QA cohort", {"UNDX_AGENT_QA_USER_IDS": "999"}),
    ("cohort set under the retired UNDX_V5 name only", {"UNDX_AGENT_QA_USER_IDS": "",
                                                       "UNDX_V5_QA_USER_IDS": "7,8"}),
    ("reads on, writes off", {"UNDX_AGENT_WRITES_ENABLED": ""}),
    ("writes on but the kill switch is set", {"UNDX_AGENT_DISABLE_WRITES": "1"}),
    ("emergency kill switch set", {"UNDX_EMERGENCY_KILL_SWITCH": "1"}),
    ("a required write guard explicitly disabled",
     {"UNDX_AGENT_REQUIRE_VERIFICATION": "0"}),
    ("capability allowlist that omits the like capability",
     {"UNDX_AGENT_ENABLED_CAPABILITIES": "crypto.alerts.pause"}),
)


def _seed(fixture) -> int:
    """Two viewable posts owned by the caller, and the id of the newer one.

    Two rather than one because the sentence under test names a post by recency, and
    a database holding exactly one post cannot tell a resolver that reads recency
    apart from one that simply takes whatever it finds.
    """
    fixture.make_post(body="An older thought", created_at="2026-08-11T00:00:00")
    return fixture.make_post(body="Launch day is getting closer",
                             created_at="2026-08-20T00:00:00")


def _liked(fixture, post_id: int) -> bool:
    from services.feed_intelligence_service import get_post_like

    return bool(get_post_like(7, post_id))


def _turn(runtime, fixture, text: str):
    return runtime.handle(fixture.cur, user_id=7, text=text,
                          conversation_id=1, confirmation_token="",
                          client_request_id="", correlation_id="probe")


def _describe(response) -> str:
    if response is None or not getattr(response, "handled", False):
        return "not handled — the agent was never consulted, UNDX answers as a chatbot"
    status = getattr(response, "status", "") or ""
    card = getattr(response, "card", None) or {}
    return f"handled status={status or card.get('status') or '(none)'}"


def run_shape(label: str, overrides: dict[str, str]) -> None:
    from tests.undx_agent import bootstrap

    bootstrap.install()
    from tests.undx_agent.harness import AgentFixture

    fixture = AgentFixture(**overrides).start()
    try:
        from services import undx_agent_runtime

        post_id = _seed(fixture)
        first = _turn(undx_agent_runtime, fixture, "Like my most recent post")
        second = _turn(undx_agent_runtime, fixture, "Yes")
        print(f"\n=== {label} ===")
        print(f"  turn 1 (request): {_describe(first)}")
        if first is not None and getattr(first, "reply", ""):
            print(f"    reply: {first.reply[:150]}")
        print(f"  turn 2 (approval): {_describe(second)}")
        if second is not None and getattr(second, "reply", ""):
            print(f"    reply: {second.reply[:150]}")
        print(f"  post {post_id} liked afterwards: {_liked(fixture, post_id)}")
    finally:
        fixture.stop()


def main() -> int:
    for label, overrides in SHAPES:
        # Cleared between shapes so one row's override cannot leak into the next; the
        # fixture restores only the keys it was given, and these are not all of them.
        for name in ("UNDX_EMERGENCY_KILL_SWITCH", "UNDX_V5_QA_USER_IDS",
                     "UNDX_AGENT_REQUIRE_VERIFICATION"):
            os.environ.pop(name, None)
        try:
            run_shape(label, overrides)
        except Exception as exc:  # a shape that cannot even boot is itself a finding
            print(f"\n=== {label} ===\n  FAILED TO RUN: {exc.__class__.__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
