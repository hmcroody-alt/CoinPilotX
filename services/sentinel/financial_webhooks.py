"""Sentinel Mission 5 — financial webhook security detections (Stage 18).

Observes webhook delivery metadata (provider, event id, signature outcome,
sequence position) and detects replay, signature failure, duplicate economic
effect risk, and out-of-order delivery. Works from facts/fixtures supplied
by the caller — Sentinel never subscribes to a provider or handles a real
webhook body, and never contains payloads (the inbox owns processing).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.sentinel import events, incidents, killswitches, store

_TS = "%Y-%m-%d %H:%M:%S"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime(_TS)


def _dedupe(provider: str, provider_event_id: str) -> str:
    return "finwh|" + hashlib.sha256(
        f"{provider}|{provider_event_id}".encode()).hexdigest()[:32]


def observe(provider: str, provider_event_id: str,
            *, event_kind: str = "", signature_valid: Optional[bool] = None,
            provider_created_at: str = "", conn=None) -> Dict[str, Any]:
    """Record one webhook delivery observation and run the detections.

    Idempotent: the SAME delivery observed twice is a dedupe no-op, but a
    RE-DELIVERY (same provider event id arriving as a new observation after
    the first was stored) is what the replay detection counts.
    """
    provider = str(provider or "").strip().lower()
    provider_event_id = str(provider_event_id or "").strip()
    if not provider or not provider_event_id:
        raise ValueError("provider and provider_event_id are required")

    out: Dict[str, Any] = {"provider": provider,
                           "provider_event_id": provider_event_id,
                           "detections": []}
    with store.connection(conn) as c:
        cur = c.cursor()
        # Count prior deliveries of this provider event id.
        cur.execute(
            "SELECT COUNT(*) FROM sentinel_events "
            "WHERE event_type = 'FINANCIAL_WEBHOOK_RECEIVED' "
            "AND source_event_id = ? AND source_system = ?",
            (provider_event_id, provider))
        prior = int(cur.fetchone()[0])

        evt = events.Event(
            category="PAYMENT",
            event_type="FINANCIAL_WEBHOOK_RECEIVED",
            severity="info",
            actor_id="service.sentinel.financial_webhooks",
            source=f"sentinel.financial.webhook.{provider}",
            subject_type="PAYMENT",
            subject_id=provider_event_id,
            payload={"event_kind": str(event_kind or ""),
                     "signature_valid": signature_valid,
                     "provider_created_at": str(provider_created_at or ""),
                     "delivery_index": prior + 1},
            source_system=provider,
            source_component="financial_webhooks",
            source_event_id=provider_event_id,
            source_trust="MEASURED",
            dedupe_key=_dedupe(provider, provider_event_id) + f"|{prior + 1}",
        )
        stored = events.ingest(evt, conn=c)
        out["stored"] = stored
        out["delivery_count"] = prior + (1 if stored else 0)

        if not killswitches.financial_detection_enabled():
            out["note"] = "detections skipped: SENTINEL_FINANCIAL_DETECTION_ENABLED is OFF"
            return out

        # --- replay: same provider event id delivered again -------------------
        if stored and prior >= 1:
            key = incidents.dedupe_key("finwh-replay", provider, provider_event_id)
            incidents.open_incident(
                key, "FINANCIAL_WEBHOOK_REPLAY", "high",
                f"Webhook replay: {provider} event {provider_event_id} "
                f"delivered {prior + 1} times",
                "service.sentinel.financial_webhooks",
                {"subject_ref": f"PAYMENT:{provider_event_id}",
                 "provider": provider, "provider_event_id": provider_event_id,
                 "delivery_count": prior + 1,
                 "authority_note": ("replay observed; the webhook inbox owns "
                                    "idempotent processing — Sentinel only "
                                    "verifies no duplicate economic effect")},
                conn=c, owner_action_required=True)
            out["detections"].append("REPLAY")
            # Duplicate-economic-effect RISK flag (the inbox should have
            # skipped it; whether it did is checked by FIN-003/FIN-014
            # against processing facts).
            out["detections"].append("DUPLICATE_ECONOMIC_EFFECT_RISK_CANDIDATE")

        # --- signature failure ---------------------------------------------------
        if signature_valid is False:
            key = incidents.dedupe_key("finwh-sig", provider, provider_event_id)
            incidents.open_incident(
                key, "FINANCIAL_PROVIDER_INCONSISTENCY", "high",
                f"Webhook signature verification FAILED: {provider} event "
                f"{provider_event_id}",
                "service.sentinel.financial_webhooks",
                {"subject_ref": f"PAYMENT:{provider_event_id}",
                 "provider": provider, "provider_event_id": provider_event_id,
                 "authority_note": "possible forged webhook — evidence only"},
                conn=c, owner_action_required=True)
            out["detections"].append("SIGNATURE_FAILURE")

        # --- out-of-order: provider timestamp older than the newest seen ---------
        if provider_created_at:
            cur.execute(
                "SELECT MAX(json_extract(payload_json, '$.provider_created_at')) "
                "FROM sentinel_events "
                "WHERE event_type = 'FINANCIAL_WEBHOOK_RECEIVED' "
                "AND source_system = ? AND source_event_id != ?",
                (provider, provider_event_id))
            row = cur.fetchone()
            newest = str(row[0] or "") if row else ""
            if newest and str(provider_created_at) < newest:
                out["detections"].append("OUT_OF_ORDER")
                out["out_of_order_against"] = newest
                # Out-of-order alone is NOT an incident — providers do not
                # guarantee ordering. It is recorded as context only.
    return out
