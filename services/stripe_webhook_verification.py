"""Signature verification for inbound Stripe webhooks.

Why this module exists
----------------------
The app serves every Stripe webhook from a single Flask handler that answers on
five URL aliases. Stripe, however, issues a *separate* signing secret for every
event destination you create. A single ``STRIPE_WEBHOOK_SECRET`` therefore can
only ever verify events from one destination -- every other destination pointed
at this app fails verification and gets a 400, and Stripe disables an endpoint
after enough consecutive failures.

That is not hypothetical: the live destination ``pulsesoc-ads-billing-live``
(https://pulsesoc.com/stripe-webhook) was disabled by Stripe after nine days of
100%-failing deliveries. Every attempt returned HTTP 400 with the body
``"Invalid"`` -- the exact string the handler returns when
``stripe.Webhook.construct_event`` raises -- because the secret deployed to the
environment did not belong to that destination.

The fix is *not* to relax verification. Every payload still has to carry a valid
Stripe HMAC signature, produced by a secret this deployment was explicitly
configured with, and still has to be inside Stripe's replay tolerance. The only
thing that changes is that more than one such secret may be configured, so one
handler can legitimately serve several destinations and so a secret rotation has
an overlap window instead of an outage.

Configuration
-------------
``STRIPE_WEBHOOK_SECRET``   the primary secret (unchanged, still required)
``STRIPE_WEBHOOK_SECRETS``  optional additional secrets, comma/whitespace/
                            newline separated

Nothing here ever logs or returns secret material. Callers that want to describe
the configuration (health endpoints, startup logs) get counts and opaque labels
only -- see :func:`describe_configuration`.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import stripe

__all__ = [
    "configured_secrets",
    "describe_configuration",
    "verify",
    "SIGNATURE_MISSING",
    "SIGNATURE_INVALID",
    "SECRET_MISSING",
]

# Reasons a verification attempt can fail. These are stable, machine-readable
# strings so log analysis and tests can distinguish "nobody signed this" from
# "signed with a key we do not hold" from "this deployment is not configured".
SECRET_MISSING = "secret_missing"
SIGNATURE_MISSING = "signature_missing"
SIGNATURE_INVALID = "signature_invalid"

_SPLIT = re.compile(r"[\s,;]+")


def _clean(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


def configured_secrets(env: dict[str, str] | None = None) -> list[str]:
    """Every signing secret this deployment is allowed to verify against.

    Order is preserved and duplicates are dropped, so the primary secret is
    always tried first and a copy-paste of it into the secondary list costs
    nothing. Blank entries are ignored rather than treated as a secret, because
    an empty string would make ``construct_event`` fail in a confusing way
    instead of simply not being configured.
    """
    source = env if env is not None else os.environ

    candidates: list[str] = []
    primary = _clean(source.get("STRIPE_WEBHOOK_SECRET"))
    if primary:
        candidates.append(primary)

    for chunk in _SPLIT.split(_clean(source.get("STRIPE_WEBHOOK_SECRETS"))):
        chunk = _clean(chunk)
        if chunk:
            candidates.append(chunk)

    seen: set[str] = set()
    ordered: list[str] = []
    for secret in candidates:
        if secret not in seen:
            seen.add(secret)
            ordered.append(secret)
    return ordered


def describe_configuration(env: dict[str, str] | None = None) -> dict[str, Any]:
    """A description of the secret configuration that is safe to expose.

    The webhook health route is publicly reachable, so this deliberately carries
    no secret material at all -- not even a truncated fingerprint. A count is
    enough to answer the only question an operator asks from outside ("is this
    deployment configured to verify anything?"), and it cannot help an attacker
    forge a signature.
    """
    secrets = configured_secrets(env)
    return {
        "webhook_secret_configured": bool(secrets),
        "webhook_secret_count": len(secrets),
    }


def verify(payload: bytes, signature_header: str | None,
           env: dict[str, str] | None = None) -> dict[str, Any]:
    """Verify ``payload`` against every configured secret.

    Returns ``{"ok": True, "event": <event>, "secret_index": int}`` on the first
    secret that validates, otherwise ``{"ok": False, "reason": ...}``.

    ``secret_index`` is the position of the secret that worked. It identifies
    *which* destination an event came from without revealing the secret, which
    is what makes a future mismatch diagnosable from logs alone.

    A missing signature header is reported separately from a bad signature. Both
    are rejected -- an unsigned payload is never accepted -- but they mean very
    different things operationally: no header at all usually means something
    other than Stripe is posting to this URL, while a bad signature means Stripe
    is posting and we hold the wrong key.
    """
    secrets = configured_secrets(env)
    if not secrets:
        return {
            "ok": False,
            "reason": SECRET_MISSING,
            "secrets_tried": 0,
            "message": "No Stripe webhook signing secret is configured.",
        }

    if not signature_header:
        return {
            "ok": False,
            "reason": SIGNATURE_MISSING,
            "secrets_tried": 0,
            "message": "Request carried no Stripe-Signature header.",
        }

    last_error = ""
    for index, secret in enumerate(secrets):
        try:
            event = stripe.Webhook.construct_event(payload, signature_header, secret)
        except Exception as exc:  # noqa: BLE001 - stripe raises several types
            # Never let the exception text reach a caller verbatim without
            # care; Stripe's messages quote the header, not the secret, but we
            # keep only the type and a short message to be safe.
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            continue
        return {"ok": True, "event": event, "secret_index": index}

    logging.warning(
        "STRIPE_SIGNATURE_REJECTED secrets_tried=%s payload_bytes=%s last_error=%s",
        len(secrets), len(payload or b""), last_error,
    )
    return {
        "ok": False,
        "reason": SIGNATURE_INVALID,
        "secrets_tried": len(secrets),
        "message": "Signature did not match any configured signing secret.",
        "last_error": last_error,
    }
