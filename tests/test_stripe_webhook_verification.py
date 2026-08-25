"""Contracts for inbound Stripe webhook signature verification.

The incident these guard against: the live destination pulsesoc-ads-billing-live
was signing with a secret this deployment did not hold, so every delivery came
back 400 "Invalid" and Stripe disabled the endpoint after nine days.

The repair widens *which* secrets are acceptable without widening *whether* a
signature is required. So the tests here are deliberately split in two halves:
the half that proves a legitimately-signed event from a second destination is
now accepted, and the half that proves nothing unsigned, mis-signed, tampered,
or stale gets in. The second half matters more -- a fix that let anything
through would also make the first half pass.
"""

import hashlib
import hmac
import json
import time

import pytest

from services import stripe_webhook_verification as swv

PRIMARY = "whsec_primarysecretvalueforthetests00"
SECONDARY = "whsec_secondarysecretvalueforthetest0"
UNKNOWN = "whsec_neverconfiguredanywhereatall000"

EVENT = {
    "id": "evt_test_00000000000001",
    "object": "event",
    "type": "invoice.payment_failed",
    "api_version": "2026-04-22.dahlia",
    "data": {"object": {"id": "in_test_1", "object": "invoice"}},
}


def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a real Stripe-Signature header, the way Stripe builds it."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed_payload = b"%d." % ts + payload
    mac = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


@pytest.fixture
def payload() -> bytes:
    return json.dumps(EVENT).encode()


def env(primary: str | None = PRIMARY, extra: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    if primary is not None:
        out["STRIPE_WEBHOOK_SECRET"] = primary
    if extra is not None:
        out["STRIPE_WEBHOOK_SECRETS"] = extra
    return out


# --- what the secret list is allowed to contain -------------------------------

def test_primary_secret_is_tried_first():
    assert swv.configured_secrets(env(extra=SECONDARY)) == [PRIMARY, SECONDARY]


@pytest.mark.parametrize("separator", [",", " ", "\n", ", ", " , ", ";"])
def test_additional_secrets_accept_any_common_separator(separator):
    value = separator.join([SECONDARY, UNKNOWN])
    assert swv.configured_secrets(env(extra=value)) == [PRIMARY, SECONDARY, UNKNOWN]


def test_duplicate_secret_is_listed_once():
    # Pasting the primary into the secondary list is a natural operator mistake
    # and must not cause the same key to be tried twice.
    assert swv.configured_secrets(env(extra=PRIMARY)) == [PRIMARY]


def test_blank_and_quoted_entries_are_not_treated_as_secrets():
    # Railway values routinely arrive wrapped in quotes, and an empty string
    # would make construct_event fail confusingly rather than read as absent.
    assert swv.configured_secrets({"STRIPE_WEBHOOK_SECRET": f'"{PRIMARY}"',
                                   "STRIPE_WEBHOOK_SECRETS": " , ,, "}) == [PRIMARY]


def test_no_configuration_yields_no_secrets():
    assert swv.configured_secrets({}) == []


# --- the half that proves the outage is repaired ------------------------------

def test_event_signed_with_the_primary_secret_is_accepted(payload):
    result = swv.verify(payload, sign(payload, PRIMARY), env())
    assert result["ok"] is True
    assert result["secret_index"] == 0
    assert result["event"]["id"] == EVENT["id"]


def test_event_from_a_second_destination_is_accepted(payload):
    # This is the case that was failing in production: a real Stripe event,
    # correctly signed, from a destination whose secret was not the primary.
    result = swv.verify(payload, sign(payload, SECONDARY), env(extra=SECONDARY))
    assert result["ok"] is True
    assert result["secret_index"] == 1, "should record which destination it came from"


def test_secret_index_identifies_the_destination_without_leaking_it(payload):
    result = swv.verify(payload, sign(payload, SECONDARY), env(extra=SECONDARY))
    assert SECONDARY not in json.dumps(result, default=str)
    assert PRIMARY not in json.dumps(result, default=str)


# --- the half that proves verification was not weakened -----------------------

def test_unsigned_payload_is_rejected(payload):
    result = swv.verify(payload, None, env(extra=SECONDARY))
    assert result["ok"] is False
    assert result["reason"] == swv.SIGNATURE_MISSING


def test_empty_signature_header_is_rejected(payload):
    result = swv.verify(payload, "", env(extra=SECONDARY))
    assert result["ok"] is False
    assert result["reason"] == swv.SIGNATURE_MISSING


def test_signature_from_an_unconfigured_secret_is_rejected(payload):
    # Adding secrets must not mean accepting *any* well-formed signature.
    result = swv.verify(payload, sign(payload, UNKNOWN), env(extra=SECONDARY))
    assert result["ok"] is False
    assert result["reason"] == swv.SIGNATURE_INVALID
    assert result["secrets_tried"] == 2


def test_tampered_payload_is_rejected(payload):
    header = sign(payload, PRIMARY)
    tampered = payload.replace(b"in_test_1", b"in_test_9")
    assert len(tampered) == len(payload), "length must match so only content differs"
    result = swv.verify(tampered, header, env(extra=SECONDARY))
    assert result["ok"] is False
    assert result["reason"] == swv.SIGNATURE_INVALID


def test_stale_signature_is_rejected(payload):
    # Stripe's own replay tolerance must keep applying; a captured header from
    # an hour ago cannot be replayed at us.
    old = int(time.time()) - 3600
    result = swv.verify(payload, sign(payload, PRIMARY, timestamp=old), env())
    assert result["ok"] is False
    assert result["reason"] == swv.SIGNATURE_INVALID


def test_missing_configuration_is_distinct_from_a_bad_signature(payload):
    # 503 (fix the deployment) and 400 (reject the caller) are different
    # answers, and the handler chooses between them on this reason code.
    result = swv.verify(payload, sign(payload, PRIMARY), {})
    assert result["ok"] is False
    assert result["reason"] == swv.SECRET_MISSING


# --- what the public health route may say -------------------------------------

def test_health_description_carries_no_secret_material():
    described = swv.describe_configuration(env(extra=SECONDARY))
    assert described == {"webhook_secret_configured": True, "webhook_secret_count": 2}
    blob = json.dumps(described)
    for secret in (PRIMARY, SECONDARY):
        assert secret not in blob
        assert secret[-6:] not in blob, "not even a truncated fingerprint"


def test_health_description_reports_an_unconfigured_deployment():
    assert swv.describe_configuration({}) == {
        "webhook_secret_configured": False,
        "webhook_secret_count": 0,
    }
