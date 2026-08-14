"""Sentinel Mission 5 — financial ATO chains FAT-1…FAT-5 (Stage 10, 27).

REUSES the Mission 3 temporal sequence engine (services.sentinel.sequences)
— no second framework. Each chain correlates identity events (Mission 3
bridge event types) with financial events (Mission 5 adapters) for one
subject inside a bounded window.

A chain with missing OPTIONAL steps fires PARTIAL (honest completeness).
Firing opens FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED — a suspicion for a human,
never a hold, freeze, or reversal.
"""

from __future__ import annotations

from typing import Optional

from services.sentinel import incidents, killswitches, sequences

FAT_SEQUENCES = (
    sequences.SequenceDefinition(
        sequence_id="FAT1_RESET_LOGIN_PAYOUT",
        title="Password reset, login, then a payout request",
        steps=(
            sequences.SequenceStep("recovery", ("password_reset_requested",)),
            sequences.SequenceStep("login", ("login_succeeded",)),
            sequences.SequenceStep("payout", ("PAYOUT_REQUESTED",)),
        ),
        window_minutes=240,
        incident_type="FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED",
        severity="high", cooldown_minutes=360,
        description="Recover the account, get in, pull the money."),
    sequences.SequenceDefinition(
        sequence_id="FAT2_NEWDEVICE_DEST_PAYOUT",
        title="New device changes payout destination then requests payout",
        steps=(
            sequences.SequenceStep("new_device", ("unusual_device",)),
            sequences.SequenceStep("dest_change", ("PAYOUT_DESTINATION_CHANGED",)),
            sequences.SequenceStep("payout", ("PAYOUT_REQUESTED",), optional=True),
        ),
        window_minutes=240,
        incident_type="FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED",
        severity="high", cooldown_minutes=360,
        description="Redirect the pipe before opening the tap. PARTIAL "
                    "without the payout request."),
    sequences.SequenceDefinition(
        sequence_id="FAT3_EMAILCHANGE_PAYOUT",
        title="Contact-point rewrite followed by a payout request",
        steps=(
            sequences.SequenceStep("email_change", ("email_changed",)),
            sequences.SequenceStep("payout", ("PAYOUT_REQUESTED",)),
        ),
        window_minutes=720,
        incident_type="FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED",
        severity="medium", cooldown_minutes=360,
        description="Cut off the owner's notifications, then move money."),
    sequences.SequenceDefinition(
        sequence_id="FAT4_FAILBURST_LOGIN_ORDERBURST",
        title="Failed-login burst, success, then rapid paid orders",
        steps=(
            sequences.SequenceStep("failed_burst", ("login_failed",), min_count=5),
            sequences.SequenceStep("login", ("login_succeeded",)),
            sequences.SequenceStep("order_burst", ("ORDER_PAID",), min_count=3),
        ),
        window_minutes=180,
        incident_type="FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED",
        severity="high", cooldown_minutes=360,
        description="Guessed way in, then spends fast — stored payment "
                    "method abuse pattern."),
    sequences.SequenceDefinition(
        sequence_id="FAT5_RECOVERY_ADWALLET_DRAIN",
        title="Account recovery followed by ad-wallet spending",
        steps=(
            sequences.SequenceStep("recovery", ("password_reset_requested",)),
            sequences.SequenceStep("login", ("login_succeeded",), optional=True),
            sequences.SequenceStep("wallet_debit", ("AD_WALLET_DEBITED",), min_count=2),
        ),
        window_minutes=360,
        incident_type="FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED",
        severity="medium", cooldown_minutes=360,
        description="Recovered account burning the advertiser wallet. "
                    "PARTIAL without the observed login."),
)


def evaluate_all(conn=None, *, now=None) -> list:
    """Run FAT chains through the shared engine. Gated by the financial
    detection kill switch: OFF → no evaluation at all."""
    if not killswitches.financial_detection_enabled():
        return []
    return sequences.evaluate_all(FAT_SEQUENCES, conn=conn, now=now)


def open_incidents_for(firings: list, conn=None,
                       actor_id: str = "service.sentinel.financial_sequences") -> list:
    """One incident per subject (correlation), idempotent by dedupe key.
    PARTIAL completeness is carried into the detail — never inflated."""
    refs = []
    for firing in firings:
        subject_ref = firing.get("subject_ref")
        if not subject_ref or firing.get("error"):
            continue
        key = incidents.dedupe_key("fat-chain", subject_ref)
        detail = {
            "subject_ref": subject_ref,
            "sequence_id": firing["sequence_id"],
            "completeness": firing.get("completeness", "FULL"),
            "missing_optional_steps": firing.get("missing_optional_steps", []),
            "matched_event_ids": firing.get("matched_event_ids", []),
            "authority_note": ("suspected financial account takeover — "
                               "suspicion for human review; Sentinel cannot "
                               "hold, freeze, or reverse anything"),
        }
        refs.append(incidents.open_incident(
            key, firing.get("incident_type",
                            "FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED"),
            firing.get("severity", "high"),
            f"{firing.get('title', firing['sequence_id'])} "
            f"[{detail['completeness']}]",
            actor_id, detail, conn=conn, owner_action_required=True))
    return refs
