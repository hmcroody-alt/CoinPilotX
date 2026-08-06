"""Engineer Access gate — pure policy for the Galactic Construction passcode challenge.

Everything in this module is deliberately free of Flask, of the database, and of
`bot`, so the whole authorization decision can be unit-tested without booting the
monolith. `bot.py` supplies the I/O (session user, attempt rows, audit writes) and
calls in here for every decision that matters.

Three separate facts must all hold before engineer access is granted, and they are
kept separate on purpose so that no single compromised input is sufficient:

  1. identity   — the caller is the configured owner account or holds an approved
                  internal role, resolved server-side from immutable fields.
  2. secret     — the submitted passcode matches a salted PBKDF2 hash supplied by
                  environment configuration. The raw value exists nowhere in this
                  repository, in the mobile bundle, or in any log line.
  3. standing   — no lockout is currently in force for this account.

A failure in any of the three produces the *same* opaque result. Callers must not
be able to distinguish "wrong account" from "wrong passcode" from "locked out",
because that distinction is exactly what turns a brute-force into a search.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from .construction_access import configured_owner_ids, flag_enabled


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

PASSCODE_HASH_ENV = "ENGINEER_ACCESS_PASSCODE_HASH"
GRANT_SECRET_ENV = "ENGINEER_ACCESS_GRANT_SECRET"
ENABLED_ENV = "ENGINEER_ACCESS_ENABLED"
ROLES_ENV = "ENGINEER_ACCESS_ROLES"

#: Passcode length is fixed by the client's 8-digit input design. It is declared
#: here so the server rejects malformed submissions *before* touching the hash,
#: which keeps the expensive KDF off the path of trivially-invalid input.
PASSCODE_LENGTH = 8

#: Default grant lifetime. Short by design: the mission's session policy is
#: "current authenticated app session", and a 30-minute ceiling means a stolen
#: grant expires long before it is useful.
DEFAULT_GRANT_TTL_SECONDS = 1800

#: PBKDF2 cost. 8 digits is only 10^8 candidates, so the KDF is the only thing
#: standing between a leaked hash and a trivial offline recovery of the passcode.
#: 600k iterations puts a full offline sweep in the many-CPU-days range.
PBKDF2_ITERATIONS = 600_000

#: Roles that may hold engineer access in addition to the configured owner IDs.
#: Resolved from the server-side admin table only — never from a client claim.
DEFAULT_ENGINEER_ROLES = frozenset(
    {"owner", "super_admin", "superadmin", "internal_supertester", "engineer"}
)

#: The native systems an engineer grant unlocks. Returned to the client so the
#: app reveals exactly what the server authorized, rather than assuming a
#: blanket "developer mode". Names are route-family identifiers, not secrets.
ENGINEER_ACCESS_SCOPE = (
    "business_os",
    "business_profile",
    "store",
    "inventory",
    "collections",
    "storefront",
    "listings",
    "orders",
    "shipping",
    "returns",
    "marketplace_selling",
    "marketplace_buying",
    "marketplace_messages",
    "offers",
    "sold_history",
    "seller_rating",
    "advertising",
    "marketplace_ads",
    "post_ads",
    "campaign_builder",
    "wallet_billing",
    "reports",
    "audiences",
    "creative_library",
    "payments",
    "verification_center",
    "events",
    "insights",
)


def engineer_access_enabled(value=None) -> bool:
    """Master switch. Absent configuration means the gate is closed."""
    return flag_enabled(os.getenv(ENABLED_ENV, "") if value is None else value)


def configured_engineer_roles(value=None) -> frozenset:
    raw = os.getenv(ROLES_ENV, "") if value is None else value
    roles = {token.strip().lower() for token in str(raw or "").split(",") if token.strip()}
    return frozenset(roles) or DEFAULT_ENGINEER_ROLES


# --------------------------------------------------------------------------
# Passcode hashing
# --------------------------------------------------------------------------

HASH_SCHEME = "pbkdf2_sha256"


def hash_passcode(raw_passcode: str, *, iterations: int = PBKDF2_ITERATIONS, salt: bytes = None) -> str:
    """Produce the encoded hash to place in ``ENGINEER_ACCESS_PASSCODE_HASH``.

    Used by ``scripts/generate_engineer_access_hash.py`` and by tests. The raw
    passcode is never returned, stored, or logged by this function.
    """
    salt = salt or os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", str(raw_passcode).encode("utf-8"), salt, iterations)
    return "{}${}${}${}".format(
        HASH_SCHEME,
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_passcode(candidate: str, encoded_hash: str = None) -> bool:
    """Constant-time check of a submitted passcode against the encoded hash.

    Returns False for every malformed or unconfigured case rather than raising,
    so a configuration mistake fails *closed* instead of throwing a 500 that
    would itself leak which branch was taken.
    """
    encoded_hash = os.getenv(PASSCODE_HASH_ENV, "") if encoded_hash is None else encoded_hash
    if not encoded_hash or not candidate:
        return False
    try:
        scheme, iterations_raw, salt_b64, digest_b64 = str(encoded_hash).strip().split("$")
        if scheme != HASH_SCHEME:
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError, base64.binascii.Error):
        return False
    if iterations <= 0 or not salt or not expected:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", str(candidate).encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def passcode_is_well_formed(candidate) -> bool:
    """Shape check only. Deliberately says nothing about correctness."""
    text = str(candidate or "")
    return len(text) == PASSCODE_LENGTH and text.isdigit()


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def engineer_identity_authorized(user, *, admin_role: str = "", admin_status: str = "active", owner_ids=None, roles=None) -> bool:
    """Is this account permitted to hold engineer access at all?

    Resolved from the immutable user ID and the server-side admin table. Display
    name, username, profile photo, and any client-supplied claim are ignored by
    construction — they are not parameters of this function.
    """
    user_id = int((user or {}).get("user_id") or 0)
    if user_id <= 0:
        return False
    allowed_ids = configured_owner_ids() if owner_ids is None else set(owner_ids)
    if user_id in allowed_ids:
        return True
    if str(admin_status or "active").strip().lower() != "active":
        return False
    allowed_roles = configured_engineer_roles() if roles is None else frozenset(roles)
    return str(admin_role or "").strip().lower() in allowed_roles


# --------------------------------------------------------------------------
# Lockout policy
# --------------------------------------------------------------------------

#: Consecutive-failure count -> lockout duration in seconds.
#: Attempts 1 and 2 warn only; the third locks; escalation is steep after that.
LOCKOUT_LADDER = {3: 60, 4: 300, 5: 900}

#: Beyond the ladder, the account must re-authenticate entirely before the
#: passcode challenge is offered again.
REAUTH_REQUIRED_AFTER_FAILURES = 6
REAUTH_LOCKOUT_SECONDS = 3600


def lockout_seconds_for(consecutive_failures: int) -> int:
    """Duration to lock after the Nth consecutive failure. 0 means "warn only"."""
    failures = max(0, int(consecutive_failures or 0))
    if failures >= REAUTH_REQUIRED_AFTER_FAILURES:
        return REAUTH_LOCKOUT_SECONDS
    return LOCKOUT_LADDER.get(failures, 0)


def requires_fresh_session(consecutive_failures: int) -> bool:
    return int(consecutive_failures or 0) >= REAUTH_REQUIRED_AFTER_FAILURES


def lockout_remaining(locked_until_epoch, now: float = None) -> int:
    """Whole seconds remaining on an active lockout, or 0 if none.

    The countdown is derived from a server-held timestamp, so killing and
    relaunching the app cannot shorten it.
    """
    try:
        locked_until = float(locked_until_epoch or 0)
    except (TypeError, ValueError):
        return 0
    now = time.time() if now is None else now
    remaining = locked_until - now
    return int(remaining) + 1 if remaining > 0 else 0


# --------------------------------------------------------------------------
# Signed grant
# --------------------------------------------------------------------------

def _grant_secret(secret: str = None) -> bytes:
    resolved = secret if secret is not None else (
        os.getenv(GRANT_SECRET_ENV)
        or os.getenv("SECRET_KEY")
        or os.getenv("FLASK_SECRET_KEY")
        or os.getenv("SESSION_SECRET")
        or ""
    )
    return str(resolved).encode("utf-8")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_grant(user_id: int, session_id: str = "", device_id: str = "", *, ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS, secret: str = None, now: float = None, scope=None) -> dict:
    """Mint a short-lived signed capability.

    The grant carries no passcode and no reversible secret — only the subject,
    the binding, an expiry, and a signature. Losing it costs the holder a
    time-boxed window, not the passcode.
    """
    now = time.time() if now is None else now
    expires_at = int(now) + int(ttl_seconds)
    payload = {
        "sub": int(user_id),
        "sid": str(session_id or ""),
        "did": str(device_id or ""),
        "exp": expires_at,
        "iat": int(now),
        "scope": list(scope if scope is not None else ENGINEER_ACCESS_SCOPE),
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64url(hmac.new(_grant_secret(secret), body.encode("ascii"), hashlib.sha256).digest())
    return {"token": "{}.{}".format(body, signature), "expires_at": expires_at, "scope": payload["scope"]}


def verify_grant(token: str, *, user_id: int = None, session_id: str = None, secret: str = None, now: float = None) -> dict:
    """Validate a grant. Returns the payload, or ``None`` for any failure.

    Signature is checked *before* expiry so a forged token never reaches the
    expiry branch, and every rejection returns the same ``None`` to the caller.
    """
    resolved_secret = _grant_secret(secret)
    if not resolved_secret or not token or "." not in str(token):
        return None
    body, _, signature = str(token).partition(".")
    expected = _b64url(hmac.new(resolved_secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64url(body).decode("utf-8"))
    except (ValueError, TypeError, base64.binascii.Error):
        return None
    if not isinstance(payload, dict):
        return None
    now = time.time() if now is None else now
    if int(payload.get("exp") or 0) <= int(now):
        return None
    if user_id is not None and int(payload.get("sub") or 0) != int(user_id):
        return None
    if session_id is not None and str(payload.get("sid") or "") != str(session_id or ""):
        return None
    return payload


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------

#: The single denial shape. Every failure path returns this — wrong account,
#: wrong passcode, malformed input, disabled feature, and active lockout are
#: indistinguishable to the caller.
def _denied(*, retry_after: int = 0, requires_reauth: bool = False) -> dict:
    result = {"ok": False, "authorized": False, "error": "engineer_access_denied"}
    if retry_after > 0:
        result["retry_after_seconds"] = retry_after
    if requires_reauth:
        result["requires_reauthentication"] = True
    return result


def evaluate_engineer_access(
    *,
    user,
    passcode,
    consecutive_failures: int = 0,
    locked_until: float = 0,
    admin_role: str = "",
    admin_status: str = "active",
    session_id: str = "",
    device_id: str = "",
    now: float = None,
    enabled: bool = None,
    encoded_hash: str = None,
    secret: str = None,
    ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
) -> dict:
    """Resolve one verification attempt into an outcome the route can act on.

    Returns a dict with ``authorized``, plus — for the caller's bookkeeping only,
    never for the client — ``record_failure`` and ``lock_for_seconds``.
    """
    now = time.time() if now is None else now
    remaining = lockout_remaining(locked_until, now=now)
    if remaining > 0:
        # Locked: do not evaluate the passcode at all. This both preserves the
        # lockout and keeps the KDF off the attacker's path.
        return {
            **_denied(retry_after=remaining, requires_reauth=requires_fresh_session(consecutive_failures)),
            "locked": True,
            "record_failure": False,
            "lock_for_seconds": 0,
        }

    identity_ok = engineer_access_enabled(enabled) and engineer_identity_authorized(
        user, admin_role=admin_role, admin_status=admin_status
    )
    secret_ok = passcode_is_well_formed(passcode) and verify_passcode(passcode, encoded_hash)

    # Both checks are evaluated before branching so the response time does not
    # separate "unauthorized account" from "wrong passcode".
    if identity_ok and secret_ok:
        grant = issue_grant(
            int(user.get("user_id") or 0),
            session_id=session_id,
            device_id=device_id,
            ttl_seconds=ttl_seconds,
            secret=secret,
            now=now,
        )
        return {
            "ok": True,
            "authorized": True,
            "grant": grant["token"],
            "expires_at": grant["expires_at"],
            "scope": grant["scope"],
            "record_failure": False,
            "reset_failures": True,
            "lock_for_seconds": 0,
        }

    failures = int(consecutive_failures or 0) + 1
    lock_for = lockout_seconds_for(failures)
    return {
        **_denied(retry_after=lock_for, requires_reauth=requires_fresh_session(failures)),
        "locked": lock_for > 0,
        "record_failure": True,
        "lock_for_seconds": lock_for,
        "failure_count": failures,
    }
