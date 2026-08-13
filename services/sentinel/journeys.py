"""Sentinel critical-journey model (Stage 10).

The six canonical journeys the platform lives or dies by. Each journey is a
declared sequence of expected event types; Sentinel evaluates observed events
against the declaration and reports which step broke. Declarative and
read-only — Sentinel never drives a journey, it watches one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JourneyStep:
    step_id: str
    description: str
    expected_category: str
    expected_event_types: tuple[str, ...]


@dataclass(frozen=True)
class Journey:
    journey_id: str
    description: str
    steps: tuple[JourneyStep, ...]


JOURNEYS: dict[str, Journey] = {
    "AUTH": Journey("AUTH", "Login → session issued → session used", (
        JourneyStep("login_attempt", "Credentials presented", "AUTH", ("login_attempt",)),
        JourneyStep("login_result", "Success or failure recorded", "AUTH", ("login_succeeded", "login_failed")),
        JourneyStep("session_active", "Session token exercised", "SESSION", ("session_used", "session_refreshed")),
    )),
    "CHECKOUT": Journey("CHECKOUT", "Cart → payment intent → webhook confirm → entitlement", (
        JourneyStep("intent", "Payment intent created", "PAYMENT", ("payment_intent_created",)),
        JourneyStep("webhook", "Provider webhook received (idempotent inbox)", "PAYMENT", ("webhook_received",)),
        JourneyStep("settled", "Ledger entries written", "LEDGER", ("ledger_entry_written",)),
    )),
    "SETTLEMENT": Journey("SETTLEMENT", "Order settled → fees → seller balance", (
        JourneyStep("settle", "Settlement state machine advanced", "SETTLEMENT", ("settlement_advanced",)),
        JourneyStep("fees", "Platform fee ledger entries", "LEDGER", ("ledger_entry_written",)),
        JourneyStep("balance", "Seller balance projection updated", "PAYOUT", ("balance_projected",)),
    )),
    "AD_DELIVERY": Journey("AD_DELIVERY", "Funded campaign → eligible ad → impression billed", (
        JourneyStep("funded", "Wallet reserve exists", "ADVERTISING", ("wallet_reserved",)),
        JourneyStep("served", "Impression served", "ADVERTISING", ("impression_served",)),
        JourneyStep("billed", "Spend recorded against reserve", "ADVERTISING", ("spend_recorded",)),
    )),
    "DEPLOYMENT": Journey("DEPLOYMENT", "New SHA live → workers healthy → error rate stable", (
        JourneyStep("deployed", "Deployment SHA changed", "DEPLOYMENT", ("deployment_detected",)),
        JourneyStep("workers", "Worker heartbeats fresh", "WORKER", ("heartbeat_ok",)),
        JourneyStep("stable", "No error-rate incident opened", "SENTINEL_SELF", ("post_deploy_check_ok",)),
    )),
    "NATIVE_API": Journey("NATIVE_API", "Mobile auth refresh → API served", (
        JourneyStep("refresh", "Token refresh accepted", "AUTH", ("token_refreshed",)),
        JourneyStep("served", "Authenticated API response", "SESSION", ("session_used",)),
    )),
}


def evaluate(journey_id: str, observed_events: list[dict]) -> dict:
    """Match observed events (dicts with category/event_type, oldest first)
    against a journey. Returns completion status and the first broken step.
    Pure function — no I/O, fully deterministic."""
    journey = JOURNEYS.get(journey_id)
    if journey is None:
        raise ValueError(f"unknown journey {journey_id!r} (SC15)")
    cursor = 0
    completed: list[str] = []
    for step in journey.steps:
        matched = False
        while cursor < len(observed_events):
            ev = observed_events[cursor]
            cursor += 1
            if (ev.get("category") == step.expected_category
                    and ev.get("event_type") in step.expected_event_types):
                matched = True
                break
        if not matched:
            return {"journey_id": journey_id, "complete": False,
                    "completed_steps": completed, "broken_step": step.step_id}
        completed.append(step.step_id)
    return {"journey_id": journey_id, "complete": True,
            "completed_steps": completed, "broken_step": None}
