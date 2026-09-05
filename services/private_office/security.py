"""The Private Office second lock — one owner for passcode and unlock grants.

Threat model, stated once so every function below can be read against it: the
attacker is *holding the member's logged-in device*. A borrowed phone, an
unlocked laptop, a session cookie that outlived a coffee break. Every property
this module enforces follows from that premise:

* A logged-in session is **not** an unlocked Office. Data flows only to a
  request that carries a valid, unexpired, unrevoked **unlock grant**, minted
  by this module after the member proved the office passcode to the *server*.
* The passcode exists in exactly one durable form: a salted KDF hash in
  ``private_office_security.passcode_hash``. No plaintext column, no plaintext
  log line, no passcode in a URL, no passcode inside a grant token. The
  functions here never log their string arguments at all.
* Grants are bounded (server TTL), scoped (``scope='private_office'``), and
  bound to the session/device that earned them (Stage 14) — presenting device
  A's grant from device B fails closed.
* The rate limit lives here, server-side. A client that skips its own lockout
  UI still hits ``locked_until`` on the row. Lockouts protect the passcode;
  they never destroy Office data.
* Face ID never reaches this module. Biometrics are a *local convenience* for
  producing the same server round-trip; the only thing stored is the member's
  preference flag, so the server can render settings truthfully.

Verification order inside :func:`verify_and_unlock` is deliberate:
cooldown first (so a locked row does not even burn a KDF comparison), then the
hash check, then — only on success — counter reset, opportunistic rehash, and
grant mint. On failure the counter increments and the cooldown escalates.

Grant invalidation is belt-and-braces:

1. Explicit revocation (`lock now`, passcode change/reset, account security
   events) stamps ``revoked_at``.
2. Validation refuses any grant whose ``issued_at`` predates the passcode's
   ``changed_at`` — so even a revocation write that failed cannot leave a
   pre-change grant alive.
3. Binding equality — a revoked *login* session stops authenticating at the
   auth layer, and its binding string dies with it.

Nothing in this module can widen tier access: it answers "is the second lock
open", and the tier/entitlement question stays with ``access.decide``. Both
must say yes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from services import auth_service
from services.private_office import audit as _audit
from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.security")

SCOPE = "private_office"

#: The KDF actually in use, named so it can be superseded. werkzeug's
#: ``generate_password_hash`` (PBKDF2-SHA256, per-hash salt) — the same
#: primitive the account password already trusts. A future scheme bumps this
#: constant and every old hash migrates itself on its next successful verify.
HASH_VERSION = "werkzeug-pbkdf2-v1"

MIN_PASSCODE_LENGTH = 6
MAX_PASSCODE_LENGTH = 32

#: Trivial passcodes, refused at creation (Stage 2). All-same-digit and
#: straight runs are computed for any length; this set catches the named
#: classics whatever their shape.
_BANNED_PASSCODES = frozenset({"000000", "111111", "123456", "654321", "123123", "112233"})

#: Grant lifetime. Server-owned; env-tunable with a floor and a ceiling so a
#: typo in a Railway variable cannot mint week-long grants or 1-second ones.
GRANT_TTL_FLOOR_SECONDS = 60
GRANT_TTL_CEILING_SECONDS = 24 * 3600
GRANT_TTL_DEFAULT_SECONDS = 15 * 60

#: Rate limit schedule: free attempts, then escalating cooldowns.
FREE_ATTEMPTS = 5
COOLDOWN_STEPS_SECONDS = (30, 60, 300, 900)  # 6th → 30s, 7th → 60s, …, cap 15m

ERR_POLICY = "passcode_policy"
ERR_ALREADY_SET = "passcode_already_set"
ERR_NOT_SET = "passcode_not_set"
ERR_WRONG_PASSCODE = "wrong_passcode"
ERR_COOLDOWN = "cooldown"
ERR_LOCKED = "PRIVATE_OFFICE_LOCKED"
ERR_REVERIFY = "reverification_failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(text or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def token_hash(token: str) -> str:
    """SHA-256 of a grant token — the only form a token is ever stored in."""
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


GRANT_HEADER = "X-Office-Grant"
DEVICE_HEADER = "X-Office-Device"

_SESSION_FAMILY_RESOLVER = None


def register_session_family_resolver(resolver) -> None:
    """Install the host app's bearer-token → session-family lookup.

    The mobile access token rotates every ~15 minutes by design. Hashing the
    raw bearer into the session binding therefore orphaned the member's
    standing unlock grant at every rotation, relocking the Office mid-use.
    The host registers a resolver mapping a bearer to its stable session
    family id, which lives exactly as long as the sign-in itself — logout,
    revocation and password change still kill the binding; a routine token
    refresh no longer does. Pass ``None`` to uninstall (tests).
    """
    global _SESSION_FAMILY_RESOLVER
    _SESSION_FAMILY_RESOLVER = resolver


_COOKIE_SID_KEY = "po_sid"


def _cookie_session_source() -> str:
    """A stable identity for a cookie-authenticated request, or ``""``.

    The raw cookie *value* cannot be the binding source: sessions are
    permanent, so Flask re-signs the cookie with a fresh timestamp on every
    response, and a grant bound to the mint-time cookie bytes is orphaned by
    the unlock response itself. The decoded session payload is the stable
    thing — an opaque id is minted into it on first use and rides the cookie
    for the rest of the login. Logout clears the session and the id (and
    every grant bound to it) dies with it. Any failure here returns ``""``,
    which sends the caller to the raw-cookie fallback: unstable, but it can
    only ever refuse a validation, never pass one.
    """
    try:
        from flask import session as flask_session

        sid = str(flask_session.get(_COOKIE_SID_KEY) or "")
        if not sid:
            sid = secrets.token_urlsafe(16)
            flask_session[_COOKIE_SID_KEY] = sid
        return "web-session:" + sid
    except Exception:  # noqa: BLE001 — no session support, no stable id
        return ""


def request_bindings() -> tuple[str, str]:
    """(session_binding, device_binding) for the CURRENT Flask request.

    One owner, used by the HTTP routes and the UNDX executor alike, so the
    binding a grant was minted against is byte-identical to the binding it is
    later checked with — two extractors would eventually disagree and the
    disagreement would present as random lockouts.

    The session binding hashes the credential family that authenticated this
    request (the mobile bearer's session family when the host resolver knows
    it, else the raw bearer, else a stable id carried inside the web session,
    else the raw cookie): a grant therefore dies with the session that earned
    it, and a stolen grant token presented by a different session fails
    equality. Outside a request context both are empty, which can only ever
    *fail* a validation, never pass one.
    """
    try:
        from flask import has_request_context, request
    except Exception:  # noqa: BLE001 — no flask, no request, no binding
        return "", ""
    if not has_request_context():
        return "", ""
    source = ""
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        source = auth_header.split(" ", 1)[1].strip()
        if source and _SESSION_FAMILY_RESOLVER is not None:
            try:
                family = str(_SESSION_FAMILY_RESOLVER(source) or "")
            except Exception:  # noqa: BLE001 — a resolver failure must never widen access
                family = ""
            if family:
                source = "session-family:" + family
    if not source:
        raw_cookie = request.cookies.get("session") or ""
        if raw_cookie:
            source = _cookie_session_source() or raw_cookie
    session_binding = (
        hashlib.sha256(source.encode("utf-8")).hexdigest() if source else ""
    )
    device_binding = (request.headers.get(DEVICE_HEADER) or "").strip()[:128]
    return session_binding, device_binding


def request_grant_token() -> str:
    """The unlock grant presented by the current Flask request, or ``""``."""
    try:
        from flask import has_request_context, request
    except Exception:  # noqa: BLE001
        return ""
    if not has_request_context():
        return ""
    return (request.headers.get(GRANT_HEADER) or "").strip()


def request_is_unlocked(cur, user_id: int) -> dict:
    """The one question every non-HTTP surface (UNDX) asks: is the Office open
    for the person behind the CURRENT request?

    ``{"ok": bool, "setup_required": bool}`` — fails closed on every path,
    including "no request context at all".
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "setup_required": False}
    row = _security_row(cur, owner)
    if row is None:
        return {"ok": False, "setup_required": True}
    session_binding, device_binding = request_bindings()
    verdict = validate_grant(
        cur, owner, request_grant_token(),
        session_binding=session_binding, device_binding=device_binding,
    )
    return {"ok": bool(verdict.get("ok")), "setup_required": False}


# ---------------------------------------------------------------------------
# Stage 2 — passcode policy
# ---------------------------------------------------------------------------

def passcode_policy(passcode: str) -> dict:
    """Whether a proposed passcode is acceptable. Never logs its argument."""
    text = str(passcode or "")
    if not text.isdigit():
        return {"ok": False, "reason": "digits_only"}
    if len(text) < MIN_PASSCODE_LENGTH:
        return {"ok": False, "reason": "too_short"}
    if len(text) > MAX_PASSCODE_LENGTH:
        return {"ok": False, "reason": "too_long"}
    if len(set(text)) == 1:
        return {"ok": False, "reason": "trivial"}
    ascending = all(int(b) - int(a) == 1 for a, b in zip(text, text[1:]))
    descending = all(int(a) - int(b) == 1 for a, b in zip(text, text[1:]))
    if ascending or descending or text in _BANNED_PASSCODES:
        return {"ok": False, "reason": "trivial"}
    return {"ok": True, "reason": ""}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _security_row(cur, user_id: int) -> dict | None:
    cur.execute(
        f"""SELECT id, user_id, passcode_hash, hash_version, created_at,
                   changed_at, failed_attempt_count, locked_until,
                   biometric_preference
            FROM {_schema.SECURITY_TABLE} WHERE user_id = ?""",
        (int(user_id),),
    )
    row = cur.fetchone()
    if row is None:
        return None
    keys = (
        "id", "user_id", "passcode_hash", "hash_version", "created_at",
        "changed_at", "failed_attempt_count", "locked_until",
        "biometric_preference",
    )
    try:
        return dict(row)
    except (TypeError, ValueError):
        return dict(zip(keys, tuple(row)))


def security_state(cur, user_id: int) -> dict:
    """What a client may know: setup state, cooldown, biometric preference.

    Deliberately excludes the hash, the version and the raw counter — a lock
    screen needs "set or not" and "how long to wait", nothing else.
    """
    row = _security_row(cur, user_id)
    if row is None:
        return {
            "passcode_set": False,
            "cooldown_seconds": 0,
            "biometric_preference": "unset",
        }
    return {
        "passcode_set": True,
        "cooldown_seconds": _cooldown_remaining(row),
        "biometric_preference": str(row.get("biometric_preference") or "unset"),
    }


def _cooldown_remaining(row: dict) -> int:
    until = _parse(row.get("locked_until") or "")
    if until is None:
        return 0
    remaining = (until - _now()).total_seconds()
    return max(0, int(remaining))


# ---------------------------------------------------------------------------
# Stage 3 — creation
# ---------------------------------------------------------------------------

def create_passcode(cur, user_id: int, passcode: str) -> dict:
    """First-time setup. Refuses if a passcode already exists — replacement is
    :func:`change_passcode` (needs the old one) or :func:`reset_passcode`
    (needs elevated re-verification); an unauthenticated overwrite path here
    would be a reset with no proof."""
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "error": ERR_NOT_SET}
    verdict = passcode_policy(passcode)
    if not verdict["ok"]:
        return {"ok": False, "error": ERR_POLICY, "reason": verdict["reason"]}
    if _security_row(cur, owner) is not None:
        return {"ok": False, "error": ERR_ALREADY_SET}

    now = _iso(_now())
    cur.execute(
        f"""INSERT INTO {_schema.SECURITY_TABLE}
            (user_id, passcode_hash, hash_version, created_at, changed_at,
             failed_attempt_count, locked_until, biometric_preference)
            VALUES (?, ?, ?, ?, ?, 0, '', 'unset')""",
        (owner, auth_service.hash_password(passcode), HASH_VERSION, now, now),
    )
    _audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=_audit.ACTION_OFFICE_PASSCODE_CREATED, object_type="OFFICE_LOCK",
        purpose="user_request",
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Stages 4-5, 9-10 — verify, rate limit, grant mint
# ---------------------------------------------------------------------------

def grant_ttl_seconds() -> int:
    try:
        raw = int(os.getenv("PRIVATE_OFFICE_GRANT_TTL_SECONDS") or GRANT_TTL_DEFAULT_SECONDS)
    except (TypeError, ValueError):
        raw = GRANT_TTL_DEFAULT_SECONDS
    return max(GRANT_TTL_FLOOR_SECONDS, min(raw, GRANT_TTL_CEILING_SECONDS))


def _register_failure(cur, row: dict) -> int:
    """Bump the counter, compute the next cooldown. Returns wait seconds."""
    count = int(row.get("failed_attempt_count") or 0) + 1
    wait = 0
    if count > FREE_ATTEMPTS:
        step = min(count - FREE_ATTEMPTS - 1, len(COOLDOWN_STEPS_SECONDS) - 1)
        wait = COOLDOWN_STEPS_SECONDS[step]
    locked_until = _iso(_now() + timedelta(seconds=wait)) if wait else ""
    cur.execute(
        f"""UPDATE {_schema.SECURITY_TABLE}
            SET failed_attempt_count = ?, locked_until = ? WHERE user_id = ?""",
        (count, locked_until, int(row["user_id"])),
    )
    return wait


def register_external_failure(cur, user_id: int) -> int:
    """A failed proof on an adjacent surface (e.g. the reset flow's account
    password check) counts against the same cooldown. Returns wait seconds."""
    owner = int(user_id or 0)
    row = _security_row(cur, owner) if owner > 0 else None
    if row is None:
        return 0
    wait = _register_failure(cur, row)
    _audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=_audit.ACTION_OFFICE_UNLOCK_FAILED, object_type="OFFICE_LOCK",
        purpose="user_request", outcome=_audit.OUTCOME_DENIED,
    )
    return wait


def verify_and_unlock(
    cur,
    user_id: int,
    passcode: str,
    *,
    session_binding: str = "",
    device_binding: str = "",
) -> dict:
    """The unlock. Proof of passcode → one bounded, bound grant.

    Success returns ``{"ok": True, "grant_token": …, "expires_at": …}``. The
    token crosses the wire exactly once, here; the table keeps only its hash.
    """
    owner = int(user_id or 0)
    row = _security_row(cur, owner) if owner > 0 else None
    if row is None:
        return {"ok": False, "error": ERR_NOT_SET}

    wait = _cooldown_remaining(row)
    if wait > 0:
        # Refused before the KDF runs: a cooldown that still verifies is a
        # cooldown an attacker can ignore.
        _audit.record(
            cur, actor_user_id=owner, owner_user_id=owner,
            action=_audit.ACTION_OFFICE_UNLOCK_FAILED, object_type="OFFICE_LOCK",
            purpose="user_request", outcome=_audit.OUTCOME_DENIED,
        )
        return {"ok": False, "error": ERR_COOLDOWN, "retry_after_seconds": wait}

    if not auth_service.verify_password(row.get("passcode_hash"), passcode):
        retry_after = _register_failure(cur, row)
        _audit.record(
            cur, actor_user_id=owner, owner_user_id=owner,
            action=_audit.ACTION_OFFICE_UNLOCK_FAILED, object_type="OFFICE_LOCK",
            purpose="user_request", outcome=_audit.OUTCOME_DENIED,
        )
        result = {"ok": False, "error": ERR_WRONG_PASSCODE}
        if retry_after:
            result["error"] = ERR_COOLDOWN
            result["retry_after_seconds"] = retry_after
        return result

    # Success: counter reset, opportunistic rehash if the scheme moved on.
    updates = ["failed_attempt_count = 0", "locked_until = ''"]
    params: list = []
    if str(row.get("hash_version") or "") != HASH_VERSION:
        updates.insert(0, "passcode_hash = ?")
        updates.insert(1, "hash_version = ?")
        params.extend([auth_service.hash_password(passcode), HASH_VERSION])
    params.append(owner)
    cur.execute(
        f"UPDATE {_schema.SECURITY_TABLE} SET {', '.join(updates)} WHERE user_id = ?",
        tuple(params),
    )

    minted = _mint_grant(
        cur, owner,
        session_binding=session_binding, device_binding=device_binding,
    )
    _audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=_audit.ACTION_OFFICE_UNLOCKED, object_type="OFFICE_LOCK",
        purpose="user_request",
    )
    return {"ok": True, **minted}


def _mint_grant(cur, owner: int, *, session_binding: str, device_binding: str) -> dict:
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(seconds=grant_ttl_seconds())
    cur.execute(
        f"""INSERT INTO {_schema.GRANTS_TABLE}
            (owner_user_id, token_hash, session_binding, device_binding,
             scope, nonce, issued_at, expires_at, revoked_at, revoke_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '')""",
        (
            owner, token_hash(token),
            str(session_binding or "")[:128], str(device_binding or "")[:128],
            SCOPE, secrets.token_hex(16), _iso(now), _iso(expires),
        ),
    )
    return {"grant_token": token, "expires_at": _iso(expires)}


# ---------------------------------------------------------------------------
# Stage 50 — validation, the check every sensitive read stands behind
# ---------------------------------------------------------------------------

def validate_grant(
    cur,
    user_id: int,
    token: str,
    *,
    session_binding: str = "",
    device_binding: str = "",
) -> dict:
    """Is this request's unlock proof good *right now*?

    Fails closed on every branch: missing token, unknown hash, wrong owner,
    wrong scope, expired, revoked, binding mismatch, or issued before the
    passcode last changed. The caller receives only ``{"ok": bool}`` — which
    check failed is not information a locked screen needs.
    """
    owner = int(user_id or 0)
    token = str(token or "")
    if owner <= 0 or not token:
        return {"ok": False}

    row = _security_row(cur, owner)
    if row is None:
        # No passcode configured means no grant can be valid — setup first.
        return {"ok": False}

    cur.execute(
        f"""SELECT owner_user_id, session_binding, device_binding, scope,
                   issued_at, expires_at, revoked_at
            FROM {_schema.GRANTS_TABLE} WHERE token_hash = ?""",
        (token_hash(token),),
    )
    grant = cur.fetchone()
    if grant is None:
        return {"ok": False}
    try:
        grant = dict(grant)
    except (TypeError, ValueError):
        grant = dict(zip(
            ("owner_user_id", "session_binding", "device_binding", "scope",
             "issued_at", "expires_at", "revoked_at"),
            tuple(grant),
        ))

    if int(grant.get("owner_user_id") or 0) != owner:
        return {"ok": False}
    if str(grant.get("scope") or "") != SCOPE:
        return {"ok": False}
    if str(grant.get("revoked_at") or ""):
        return {"ok": False}

    expires = _parse(grant.get("expires_at") or "")
    if expires is None or expires <= _now():
        return {"ok": False}

    # Stage 14 — the grant is scoped to the session/device that earned it.
    # Empty-vs-empty matches (a browser session with no device hash), but a
    # grant earned WITH a binding is refused to a request arriving without one.
    if str(grant.get("session_binding") or "") != str(session_binding or "")[:128]:
        return {"ok": False}
    if str(grant.get("device_binding") or "") != str(device_binding or "")[:128]:
        return {"ok": False}

    # Stage 12 — a passcode change orphans every earlier grant even if the
    # explicit revocation write was lost.
    issued = _parse(grant.get("issued_at") or "")
    changed = _parse(row.get("changed_at") or "")
    if issued is None or (changed is not None and issued < changed):
        return {"ok": False}

    return {"ok": True}


# ---------------------------------------------------------------------------
# Stages 6, 12-13 — locking and revocation
# ---------------------------------------------------------------------------

def revoke_grants(cur, user_id: int, *, reason: str, token: str | None = None) -> int:
    """Stamp ``revoked_at`` on this member's live grants.

    ``token`` narrows to one grant (manual Lock of one device); omitted, every
    live grant dies (passcode change/reset, account security event).
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return 0
    now = _iso(_now())
    params: list = [now, str(reason or "")[:64], owner]
    sql = (
        f"UPDATE {_schema.GRANTS_TABLE} SET revoked_at = ?, revoke_reason = ? "
        f"WHERE owner_user_id = ? AND COALESCE(revoked_at, '') = ''"
    )
    if token:
        sql += " AND token_hash = ?"
        params.append(token_hash(token))
    cur.execute(sql, tuple(params))
    count = int(cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0)
    _audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=_audit.ACTION_OFFICE_LOCKED, object_type="OFFICE_LOCK",
        purpose="user_request", result_count=count,
    )
    return count


def on_account_security_event(cur, user_id: int, *, event: str) -> int:
    """Account password reset, session revocation, and kin → Office relocks.

    Thin, named wrapper so call sites read as policy rather than plumbing.
    """
    return revoke_grants(cur, user_id, reason=f"account_event:{event}"[:64])


# ---------------------------------------------------------------------------
# Stages 11-12 — change and reset
# ---------------------------------------------------------------------------

def change_passcode(cur, user_id: int, current_passcode: str, new_passcode: str) -> dict:
    """Rotate the passcode; proof of the current one is the authorization."""
    owner = int(user_id or 0)
    row = _security_row(cur, owner) if owner > 0 else None
    if row is None:
        return {"ok": False, "error": ERR_NOT_SET}
    wait = _cooldown_remaining(row)
    if wait > 0:
        return {"ok": False, "error": ERR_COOLDOWN, "retry_after_seconds": wait}
    if not auth_service.verify_password(row.get("passcode_hash"), current_passcode):
        retry_after = _register_failure(cur, row)
        result = {"ok": False, "error": ERR_WRONG_PASSCODE}
        if retry_after:
            result["error"] = ERR_COOLDOWN
            result["retry_after_seconds"] = retry_after
        return result
    verdict = passcode_policy(new_passcode)
    if not verdict["ok"]:
        return {"ok": False, "error": ERR_POLICY, "reason": verdict["reason"]}

    _rotate(cur, owner, new_passcode)
    revoke_grants(cur, owner, reason="passcode_changed")
    _audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=_audit.ACTION_OFFICE_PASSCODE_CHANGED, object_type="OFFICE_LOCK",
        purpose="user_request",
    )
    return {"ok": True}


def reset_passcode(cur, user_id: int, new_passcode: str, *, reverified: bool) -> dict:
    """Forgotten passcode. ``reverified`` is the route's assertion that the
    member just passed ELEVATED re-verification (account password proof at
    minimum — a merely logged-in session is exactly the thing the second lock
    exists to distrust, so it can never count). This function trusts the flag
    but exists so the trust boundary is one named parameter, not a convention
    spread over call sites."""
    owner = int(user_id or 0)
    if not reverified:
        return {"ok": False, "error": ERR_REVERIFY}
    row = _security_row(cur, owner) if owner > 0 else None
    if row is None:
        return {"ok": False, "error": ERR_NOT_SET}
    verdict = passcode_policy(new_passcode)
    if not verdict["ok"]:
        return {"ok": False, "error": ERR_POLICY, "reason": verdict["reason"]}

    _rotate(cur, owner, new_passcode)
    revoke_grants(cur, owner, reason="passcode_reset")
    _audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=_audit.ACTION_OFFICE_PASSCODE_RESET, object_type="OFFICE_LOCK",
        purpose="user_request",
    )
    return {"ok": True}


def _rotate(cur, owner: int, new_passcode: str) -> None:
    cur.execute(
        f"""UPDATE {_schema.SECURITY_TABLE}
            SET passcode_hash = ?, hash_version = ?, changed_at = ?,
                failed_attempt_count = 0, locked_until = ''
            WHERE user_id = ?""",
        (auth_service.hash_password(new_passcode), HASH_VERSION, _iso(_now()), owner),
    )


# ---------------------------------------------------------------------------
# Stages 7-8 — biometric preference (a flag, never a bypass)
# ---------------------------------------------------------------------------

def set_biometric_preference(cur, user_id: int, enabled: bool) -> dict:
    owner = int(user_id or 0)
    row = _security_row(cur, owner) if owner > 0 else None
    if row is None:
        return {"ok": False, "error": ERR_NOT_SET}
    value = "enabled" if enabled else "disabled"
    cur.execute(
        f"UPDATE {_schema.SECURITY_TABLE} SET biometric_preference = ? WHERE user_id = ?",
        (value, owner),
    )
    _audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=(
            _audit.ACTION_OFFICE_BIOMETRIC_ENABLED if enabled
            else _audit.ACTION_OFFICE_BIOMETRIC_DISABLED
        ),
        object_type="OFFICE_LOCK", purpose="user_request",
    )
    return {"ok": True, "biometric_preference": value}


# ---------------------------------------------------------------------------
# Step-up — the second proof, for reads the first proof should not buy
# ---------------------------------------------------------------------------

def verify_step_up(cur, user_id: int, passcode: str) -> dict:
    """Re-prove the passcode for one action. Mints nothing.

    An unlock grant answers "this member opened their Office within the last
    fifteen minutes". That is the right question for reading a masked list and
    the wrong question for handing over a passport number, because the two
    differ in what a stolen grant is worth: an attacker holding one gets the
    same masked screens the member sees, and must not also get the values
    behind them.

    So this is deliberately *not* a token. There is no reveal grant to steal,
    replay, or forget to expire — the proof exists only for the request that
    carried it, and the next reveal proves again. That is also why it does not
    call :func:`verify_and_unlock`: succeeding there would mint a grant, and a
    step-up that quietly extends the session it was supposed to interrupt is
    the failure this function is shaped to avoid.

    It shares the one thing that must be shared: the failure counter and its
    escalating cooldown. Guessing at a reveal is guessing at the passcode, and
    a second unmetered guessing surface beside the metered one would make the
    rate limit on the first decorative.

    Biometry does not appear here. ``biometric_preference`` is a client-side
    convenience — the device decides whether to ask for a face before it asks
    for a passcode — and treating it as a server-side proof would mean trusting
    a client's claim to have checked, which is not a proof at all.
    """
    owner = int(user_id or 0)
    row = _security_row(cur, owner) if owner > 0 else None
    if row is None:
        return {"ok": False, "error": ERR_NOT_SET}

    wait = _cooldown_remaining(row)
    if wait > 0:
        _audit.record(
            cur, actor_user_id=owner, owner_user_id=owner,
            action=_audit.ACTION_OFFICE_UNLOCK_FAILED, object_type="OFFICE_STEP_UP",
            purpose="user_request", outcome=_audit.OUTCOME_DENIED,
        )
        return {"ok": False, "error": ERR_COOLDOWN, "retry_after_seconds": wait}

    if not auth_service.verify_password(row.get("passcode_hash"), passcode):
        retry_after = _register_failure(cur, row)
        _audit.record(
            cur, actor_user_id=owner, owner_user_id=owner,
            action=_audit.ACTION_OFFICE_UNLOCK_FAILED, object_type="OFFICE_STEP_UP",
            purpose="user_request", outcome=_audit.OUTCOME_DENIED,
        )
        result = {"ok": False, "error": ERR_WRONG_PASSCODE}
        if retry_after:
            result["error"] = ERR_COOLDOWN
            result["retry_after_seconds"] = retry_after
        return result

    cur.execute(
        f"UPDATE {_schema.SECURITY_TABLE} "
        "SET failed_attempt_count = 0, locked_until = '' WHERE user_id = ?",
        (owner,),
    )
    # No success audit row here on purpose. The caller is about to write
    # PRIVATE_RECORD_FIELD_REVEAL, which names the record and the field; a
    # second row saying only "a step-up succeeded" would add a line to the
    # history that the member cannot connect to anything they did.
    return {"ok": True}


__all__ = [
    "SCOPE", "HASH_VERSION", "MIN_PASSCODE_LENGTH",
    "FREE_ATTEMPTS", "COOLDOWN_STEPS_SECONDS",
    "ERR_POLICY", "ERR_ALREADY_SET", "ERR_NOT_SET", "ERR_WRONG_PASSCODE",
    "ERR_COOLDOWN", "ERR_LOCKED", "ERR_REVERIFY",
    "passcode_policy", "security_state", "create_passcode",
    "register_external_failure",
    "verify_and_unlock", "validate_grant", "verify_step_up", "revoke_grants",
    "on_account_security_event", "change_passcode", "reset_passcode",
    "set_biometric_preference", "grant_ttl_seconds", "token_hash",
]
