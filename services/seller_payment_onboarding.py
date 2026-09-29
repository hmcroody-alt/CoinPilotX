"""The one way a seller enters Stripe Connect onboarding.

## The defect this exists for

An approved seller tapped "Set up payments with Stripe" in her approval email on
an iPhone and landed inside the PulseSoc app. She never saw Stripe.

Nothing was broken in the sense of throwing. The email CTA resolved to

    https://pulsesoc.com/pulse/merchant/payouts?pulse_app=1&pulse_src=email

which is the page Stripe Connect *returns* to, not the place onboarding starts.
`services/payments_email_templates._seller_approved` builds that button from
``ctx["stripe_onboarding_url"]`` with ``SELLER_PAYMENTS_PATH`` as the fallback,
and the approval context — `bot.seller_application_email_context` — has never
carried that key. So the fallback was the only value the button ever had.

Then the path decided the rest. `/pulse/*` is claimed by the published
apple-app-site-association (`services/native_app_links.APPLE_LINK_COMPONENTS`),
so iOS hands the URL to the app *before any HTTP request is made*. The app's
router maps `pulse/merchant/payouts` to `MoneyLayer`
(`mobile-native/src/navigation/linking.ts`). There is no hop to intercept and no
server log line to find, because the server was never asked.

The onboarding service itself was fine and is still here: `POST
/api/pulse/payouts/connect` reuses the seller's account, mints a fresh
`AccountLink`, and keeps `refresh_url` and `return_url` distinct. It simply had
no door a mail client could knock on — a JSON POST is not something you can put
in an email.

## What this module is

The decision and the Stripe work, with no Flask and no `bot` import, so all five
outcomes are reachable in a test without booting the monolith. Three callers —
the email's durable `GET /seller/payments/setup`, the web payouts page, and the
app — share it, which is the point: three implementations of "start onboarding"
is how they drift into disagreeing about who is allowed to start.

## What it deliberately does NOT do

Not authorization rendering. `authorize()` answers one question against
`seller_access_state`, the same authority the payouts page and the JSON route
already use, and hands back a plain verdict. A JSON caller turns that into a
403 body and an HTML caller turns it into a page; one authority, two renderings,
which is not the same thing as two opinions.

Not "may this seller be paid". `bot.seller_destination_account_id` owns that and
is untouched. This module only ever writes what Stripe actually said.

Not the return leg. `services/stripe_onboarding_return` classifies that, and
this module reuses its classifier rather than growing a second opinion about
what a Stripe snapshot means.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from services import stripe_onboarding_return as _return


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

#: A fresh Stripe onboarding link was minted. ``url`` is where the seller goes.
START_REDIRECT = "redirect"

#: Stripe already considers this account done — either live, or submitted and
#: under review. Sending the seller back through onboarding here is the bug
#: `stripe_onboarding_return` was written about: telling someone to "finish
#: setting up" when they already have is how a completed flow gets restarted.
START_ALREADY_ENABLED = "already_enabled"

#: PulseSoc has not approved this account to sell. A different axis from Stripe
#: readiness and never to be conflated with it.
START_NOT_APPROVED = "not_approved"

#: This deployment has no Stripe secret key. Not the seller's problem and not
#: something a retry will fix, so it is its own answer rather than "try again".
START_STRIPE_UNCONFIGURED = "stripe_unconfigured"

#: Stripe refused, or could not be reached. Retryable in principle.
START_FAILED = "failed"

START_STATES = (
    START_REDIRECT,
    START_ALREADY_ENABLED,
    START_NOT_APPROVED,
    START_STRIPE_UNCONFIGURED,
    START_FAILED,
)

#: The lanes that exist. `teacher` is a separate product with its own approval
#: table; it is accepted here so the two lanes share the Stripe half, and its
#: approval question stays with its own authority in the caller.
SELLER_TYPES = ("merchant", "teacher")
DEFAULT_SELLER_TYPE = "merchant"


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------

# Named for the funnel, not for the code path, so the drop-off between "she
# clicked" and "she reached Stripe" — the exact gap this incident lived in — is
# one query rather than an inference across three services.
EVENT_REQUESTED = "stripe_onboarding_requested"
EVENT_ACCOUNT_CREATED = "stripe_account_created"
EVENT_ACCOUNT_REUSED = "stripe_account_reused"
EVENT_LINK_CREATED = "stripe_onboarding_link_created"
EVENT_REDIRECTED = "stripe_onboarding_redirected"
EVENT_ALREADY_ENABLED = "stripe_onboarding_already_enabled"
EVENT_ERROR = "stripe_onboarding_error"


def _noop(_event: str, _payload: Mapping[str, Any]) -> None:
    return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def normalize_seller_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in SELLER_TYPES else DEFAULT_SELLER_TYPE


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "t", "on"}


def account_id_of(account: Mapping[str, Any] | None) -> str:
    """The Connect account id on a stored payout row, or "".

    Reads both column names for the reason `bot._connect_return_snapshot` does:
    the row carries ``connected_account_id`` and ``provider_account_id`` and
    different writers have populated different ones.
    """
    row = dict(account or {})
    return str(
        row.get("connected_account_id") or row.get("provider_account_id") or ""
    ).strip()


def correlation_id(user_id: Any, seller_type: str, attempt: str) -> str:
    """A non-reversible handle that ties a click to its Stripe calls.

    Not the user id and not the account id: this value reaches log lines that
    are read by people who have no business knowing which seller is which, and
    §41's rule about verification data is easier to keep when the identifier
    itself carries none.
    """
    raw = f"{user_id}\x1f{seller_type}\x1f{attempt}".encode()
    return hashlib.sha256(raw).hexdigest()[:12]


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------


def authorize(cur, user_id: Any, seller_type: str) -> dict | None:
    """``None`` when this account may start Connect onboarding, else a verdict.

    Reads `seller_access_state` — the same authority `bot.seller_access_refusal`
    and `bot.seller_payouts_page` consult. That matters more than it looks: the
    payouts page once read ``marketplace_sellers.status`` directly instead, and
    a seller the JSON route had cleared to start onboarding could be refused by
    the page on the way back, which is the "Approval Required" screen an already
    approved seller was shown after finishing Stripe.

    Only the `merchant` lane is answered here. The `teacher` lane has its own
    approval table and its own authority, so the caller keeps that check; this
    returning ``None`` for a teacher means "nothing to say", not "approved".
    """
    if normalize_seller_type(seller_type) != "merchant":
        return None

    from services import seller_access_state

    body = seller_access_state.require_seller_access(cur, int(user_id or 0))
    if body is None:
        return None
    return {
        "state": START_NOT_APPROVED,
        "access": body,
        "message": "PulseSoc has not approved this account to sell yet.",
        "http_status": 403,
    }


# --------------------------------------------------------------------------
# The onboarding start
# --------------------------------------------------------------------------


def _persist_account_row(conn, cur, user_id: int, seller_type: str, account_id: str, now: str) -> None:
    """Record that onboarding has begun, without inventing capability.

    ``charges_enabled``/``payouts_enabled`` stay 0 here on purpose. They are
    Stripe's to grant and are only ever written from a snapshot Stripe handed
    us — writing an optimistic 1 is how a row claims a seller can be paid before
    Stripe would accept a transfer to them.
    """
    cur.execute(
        """
        INSERT INTO seller_payout_accounts
        (user_id, seller_type, provider, connected_account_id, provider_account_id,
         onboarding_status, payouts_enabled, charges_enabled,
         last_checked_at, last_synced_at, created_at, updated_at)
        VALUES (?, ?, 'stripe', ?, ?, 'onboarding_started', 0, 0, ?, ?, ?, ?)
        ON CONFLICT(user_id, seller_type) DO UPDATE SET
          connected_account_id=excluded.connected_account_id,
          provider_account_id=excluded.provider_account_id,
          onboarding_status='onboarding_started',
          last_checked_at=excluded.last_checked_at,
          last_synced_at=excluded.last_synced_at,
          updated_at=excluded.updated_at
        """,
        (int(user_id), seller_type, account_id, account_id, now, now, now, now),
    )
    conn.commit()


def _persist_unconfigured(conn, cur, user_id: int, seller_type: str, now: str) -> None:
    cur.execute(
        """
        INSERT INTO seller_payout_accounts
        (user_id, seller_type, provider, onboarding_status, payouts_enabled, charges_enabled,
         missing_requirements_json, last_checked_at, created_at, updated_at)
        VALUES (?, ?, 'stripe', 'stripe_not_configured', 0, 0, ?, ?, ?, ?)
        ON CONFLICT(user_id, seller_type) DO UPDATE SET
          onboarding_status='stripe_not_configured',
          missing_requirements_json=excluded.missing_requirements_json,
          last_checked_at=excluded.last_checked_at,
          updated_at=excluded.updated_at
        """,
        (
            int(user_id),
            seller_type,
            json.dumps(["STRIPE_SECRET_KEY required for live Connect onboarding"]),
            now,
            now,
            now,
        ),
    )
    conn.commit()


def start_onboarding(
    *,
    conn,
    cur,
    user: Mapping[str, Any],
    seller_type: str,
    base_url: str,
    now: str,
    provider,
    stripe_configured: bool,
    existing_account: Mapping[str, Any] | None = None,
    emit: Callable[[str, Mapping[str, Any]], None] = _noop,
    on_snapshot: Callable[[int, Mapping[str, Any]], None] | None = None,
) -> dict:
    """Get this seller to Stripe, or say precisely why not.

    Safe to call on a bare ``GET``, which is a requirement and not an accident:
    the durable email CTA is a link, and mail security scanners fetch links
    before a human ever sees them. Every write below is idempotent — the account
    create carries a Stripe idempotency key derived from the seller, and the row
    write is an upsert — so a prefetch costs one reused account and one wasted
    link, and the seller's own click still mints a fresh one.

    ``on_snapshot`` is how the caller persists an authoritative Stripe reading
    through its own writer. This module does not reach for
    `business_os.payments.connect_accounts` itself, because that writer lands in
    two tables in one transaction and owning that from here would make this the
    second place that knows the shape of both.
    """
    seller_type = normalize_seller_type(seller_type)
    user_id = int((user or {}).get("user_id") or 0)
    attempt = correlation_id(user_id, seller_type, now)
    base = str(base_url or "").rstrip("/")
    emit(EVENT_REQUESTED, {"seller_type": seller_type, "attempt": attempt})

    if not stripe_configured:
        # Not a failure the seller can retry their way out of, and saying "try
        # again" here is the advice that can never come true which
        # tests/test_connect_payout_onboarding.py was written about.
        _persist_unconfigured(conn, cur, user_id, seller_type, now)
        emit(EVENT_ERROR, {"attempt": attempt, "reason": "stripe_unconfigured"})
        return {
            "state": START_STRIPE_UNCONFIGURED,
            "attempt": attempt,
            "message": (
                "Payment setup is not available in this environment yet. "
                "Your seller approval is unaffected."
            ),
            "http_status": 503,
        }

    account_row = dict(existing_account or {})
    account_id = account_id_of(account_row)

    # ---------------------------------------------------------------- reuse
    #
    # One approved seller, one Connect account. The stored id is the durable
    # guard — a second `Account.create` for someone who already has one is how
    # a seller ends up with accounts A, B and C and money arriving in whichever
    # the payout worker happened to read.
    if account_id:
        emit(EVENT_ACCOUNT_REUSED, {"attempt": attempt})
        status = {}
        try:
            status = provider.get_account_status(account_id) or {}
        except Exception:
            status = {}
        if status.get("ok"):
            # Stripe is the authority on what this account is, not the column
            # onboarding left behind. Persisting here also repairs the exact row
            # shape that stranded the first live seller: charges and payouts
            # enabled, `onboarding_status` still `onboarding_started`.
            if on_snapshot is not None:
                try:
                    on_snapshot(user_id, status)
                except Exception:
                    pass
            state = _return.classify_return(status)
            if state in (_return.RETURN_READY, _return.RETURN_UNDER_REVIEW):
                emit(EVENT_ALREADY_ENABLED, {"attempt": attempt, "return_state": state})
                return {
                    "state": START_ALREADY_ENABLED,
                    "attempt": attempt,
                    "return_state": state,
                    "charges_enabled": _enabled(status.get("charges_enabled")),
                    "payouts_enabled": _enabled(status.get("payouts_enabled")),
                    # One wording per state, and it lives in the return module
                    # so the "already enabled" page and the Stripe return page
                    # cannot describe the same account differently.
                    "message": _return.return_presentation(status)["headline"],
                    "http_status": 200,
                }
            # more_info / incomplete / failed all mean the same thing for this
            # decision: Stripe still wants something, so mint a link and let
            # Stripe say what. Requirements can also appear long after a
            # successful onboarding, which is why this is not gated on having
            # never finished.
    else:
        # ------------------------------------------------------------ create
        created = provider.create_connected_account(dict(user or {}), seller_type)
        if not created.get("ok"):
            emit(EVENT_ERROR, {"attempt": attempt, "reason": created.get("code") or "account_create"})
            return {
                "state": START_FAILED,
                "attempt": attempt,
                "stage": "account_create",
                "message": created.get("message") or "Payment setup could not start.",
                "code": created.get("code") or "",
                "provider_error": created.get("provider_error") or {},
                "retryable": bool(created.get("retryable")),
                "http_status": int(created.get("http_status") or 503),
            }
        account_id = str(created.get("provider_account_id") or "").strip()
        if not account_id:
            emit(EVENT_ERROR, {"attempt": attempt, "reason": "account_id_missing"})
            return {
                "state": START_FAILED,
                "attempt": attempt,
                "stage": "account_create",
                "message": "Payment setup could not start.",
                "retryable": True,
                "http_status": 503,
            }
        emit(EVENT_ACCOUNT_CREATED, {"attempt": attempt})

    _persist_account_row(conn, cur, user_id, seller_type, account_id, now)

    # ------------------------------------------------------------------ link
    #
    # Minted now, never at approval time. A Stripe AccountLink is single-use and
    # short-lived, so an onboarding URL baked into an email is already dead by
    # the time most people open it — which is the whole reason the email points
    # at a durable PulseSoc route instead.
    #
    # Two URLs for two events. Stripe sends `refresh_url` when the link went
    # stale before it was used and `return_url` when the seller came out the
    # other end; collapsing them discards the only signal that tells "come back,
    # your link expired" apart from "you're done".
    link = provider.create_onboarding_link(
        account_id,
        refresh_url=f"{base}/pulse/{seller_type}/payouts/refresh",
        return_url=f"{base}/pulse/{seller_type}/payouts/return",
    )
    if not link.get("ok"):
        emit(EVENT_ERROR, {"attempt": attempt, "reason": link.get("code") or "account_link"})
        return {
            "state": START_FAILED,
            "attempt": attempt,
            "stage": "account_link",
            "message": link.get("message") or "Payment setup could not start.",
            "code": link.get("code") or "",
            "provider_error": link.get("provider_error") or {},
            "retryable": bool(link.get("retryable")),
            "http_status": int(link.get("http_status") or 503),
            "connected_account_id": account_id,
        }

    url = str(link.get("url") or "").strip()
    if not _is_stripe_url(url):
        # A redirect target is the one value in this flow that must never be
        # taken on trust. It comes from Stripe today, but this function hands it
        # straight to `Location:`, and an onboarding URL that is not Stripe's is
        # an open redirect with a seller's session attached.
        emit(EVENT_ERROR, {"attempt": attempt, "reason": "non_stripe_url"})
        return {
            "state": START_FAILED,
            "attempt": attempt,
            "stage": "account_link",
            "message": "Payment setup could not start.",
            "retryable": True,
            "http_status": 502,
            "connected_account_id": account_id,
        }

    emit(EVENT_LINK_CREATED, {"attempt": attempt})
    emit(EVENT_REDIRECTED, {"attempt": attempt})
    return {
        "state": START_REDIRECT,
        "attempt": attempt,
        "url": url,
        "connected_account_id": account_id,
        "message": "Stripe onboarding ready.",
        "http_status": 200,
    }


# --------------------------------------------------------------------------
# Redirect safety
# --------------------------------------------------------------------------

#: Stripe's own onboarding hosts. `connect.stripe.com` is where an AccountLink
#: points today; `hosted.stripe.com` and the `*.stripe.com` shape are accepted
#: because Stripe has moved hosted flows between subdomains before and a
#: hard-coded single host would fail a correct link.
_STRIPE_SUFFIX = ".stripe.com"


def _is_stripe_url(url: str) -> bool:
    from urllib.parse import urlsplit

    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return host == "stripe.com" or host.endswith(_STRIPE_SUFFIX)
