"""The server's memory of a federated sign-in that is still in flight.

A sign-in leaves this server, happens at Apple or Google, and comes back. The
thing that comes back has to be tied to the thing that left, or the endpoint
accepts an authorization response that *this* browser never asked for — login
CSRF, whose payoff is that a victim ends up silently signed into the attacker's
account, with the attacker holding the credentials to it.

The obvious place to keep that tie is the Flask session cookie. It does not work
here, and the reason is specific rather than stylistic: **Apple replies with
``response_mode=form_post``**, a cross-site POST from ``appleid.apple.com`` to
us. A ``SameSite=Lax`` cookie is not sent on a cross-site POST. So a session
cookie is simply absent at the moment the callback runs, and the two available
ways out are both worse than this table:

* Widen the session cookie to ``SameSite=None`` — weakening CSRF posture for
  all 1,500-odd routes to serve one.
* Trust the ``state`` parameter alone — which proves the request came from
  Apple, and nothing whatsoever about which browser it came through.

So: the state lives server-side, and the browser is tied to it by a *separate*
single-purpose cookie whose only job is to survive this round trip. That cookie
can be ``Lax`` because it is only ever read on the same-site GET that finishes
the flow, never on Apple's cross-site POST. The POST verifies the state row and
hands off to that GET.

Three properties this table is responsible for:

* **Single use.** Consumption is one atomic ``UPDATE ... WHERE consumed_at=''``
  and a row count — not read-then-write, which two concurrent replays both pass.
* **Short lived.** A state is valid for minutes. An authorization response is
  something a member is actively waiting on; there is no legitimate slow path.
* **Browser bound.** The row stores a hash of the binding cookie, so possession
  of a `state` value that leaked (a referrer, a shared screen, a proxy log) is
  not by itself enough to complete a sign-in.

What is *not* stored: no IP address and no user agent. Both were considered as
extra binding and both are wrong here. Mobile networks change IP mid-flow and
would fail real sign-ins, and neither adds anything the binding cookie does not
already prove. This table holds a pending handshake, not a session record.

One thing *is* stored that looks like session data and is not: `profile_json`,
the already-verified provider claims, written by the callback and read by the
completing GET. It is here because the callback is the only place that can
verify the provider's signature and the completing GET is the only place that
can check the browser binding, and no authorisation may happen before both. The
two alternatives were worse: writing it to the Flask session means the callback
emits a ``Set-Cookie`` built from an empty session (Apple's cross-site POST
carries none), which silently signs the member out of whatever they were already
in; re-deriving it in the GET is not possible, because an authorization code is
single-use and an ID token arrives once. Nothing is kept here that
`user_external_identities` does not keep permanently, and it is gone when the
handshake is spent or purged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from services import db

#: Providers allowed to own a state row. A typo in a caller becomes a refusal
#: here rather than an unreachable row that no callback can ever consume.
PROVIDERS = ("apple", "google")

#: What the member was trying to do. Kept on the row rather than inferred at the
#: callback, so a flow started as "add Google to my signed-in account" cannot be
#: completed as "sign me in as whoever this token says", or the reverse.
MODES = ("login", "link")

#: Minutes, not hours. See the module docstring.
DEFAULT_TTL_SECONDS = 600

#: Name of the single-purpose browser-binding cookie. Distinct from the session
#: cookie on purpose: it carries no authority, expires with the handshake, and
#: is readable on a same-site GET without relaxing anything about the session.
BINDING_COOKIE = "psx_oauth_bind"

#: Name of the one-time handoff token's query parameter on the completing GET.
HANDOFF_PARAM = "h"

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class StateError(Exception):
    """A state could not be consumed. `reason` is for the audit log, not a user."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime(_TIME_FORMAT)


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def ensure_schema(conn=None) -> None:
    """Create the table. Call from `init_db()` only.

    Same constraint as `legal_acceptance.ensure_schema`: a sign-in route is
    mid-transaction on its own connection by the time it needs this, so creating
    it on demand from a second connection waits on a write lock the caller still
    holds and fails the sign-in it was trying to protect.
    """

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS oauth_login_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_hash TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                mode TEXT NOT NULL,
                nonce TEXT NOT NULL,
                binding_hash TEXT NOT NULL,
                handoff_hash TEXT NOT NULL DEFAULT '',
                link_user_id INTEGER NOT NULL DEFAULT 0,
                next_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT NOT NULL DEFAULT '',
                profile_json TEXT NOT NULL DEFAULT '')"""
        )
        # There is no migration framework here, so a column added after the
        # table first shipped has to be added by hand. Asked rather than
        # attempted-and-swallowed: on PostgreSQL a failed statement aborts the
        # whole transaction, and this runs on `init_db()`'s shared connection --
        # so a blind `ALTER` that raises "column already exists" would take every
        # later statement in that boot down with it.
        if "profile_json" not in db.get_table_columns(conn, "oauth_login_states"):
            conn.execute(
                "ALTER TABLE oauth_login_states "
                "ADD COLUMN profile_json TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_login_states_expires "
            "ON oauth_login_states (expires_at)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def create(
    provider: str,
    *,
    mode: str = "login",
    link_user_id: int = 0,
    next_path: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    conn=None,
) -> dict:
    """Open a handshake. Returns the secrets to send out; the row keeps hashes.

    The returned `state` and `binding_secret` exist in this process and in the
    browser, and as digests in the table. A database reader therefore cannot
    forge a completable callback, which matters because `state` travels through
    the provider and can turn up in logs on the way.
    """

    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {PROVIDERS}")
    mode = str(mode or "").strip().lower()
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    if mode == "link" and not link_user_id:
        raise ValueError("a link handshake needs the member it will link to")
    if mode == "login" and link_user_id:
        raise ValueError("a login handshake must not name a member in advance")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    binding_secret = secrets.token_urlsafe(32)
    opened = _now()

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO oauth_login_states "
            "(state_hash, provider, mode, nonce, binding_hash, link_user_id, next_path, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                _digest(state),
                provider,
                mode,
                nonce,
                _digest(binding_secret),
                int(link_user_id or 0),
                str(next_path or "")[:512],
                _stamp(opened),
                _stamp(opened + timedelta(seconds=max(60, int(ttl_seconds)))),
            ),
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()

    return {
        "state": state,
        "nonce": nonce,
        "binding_secret": binding_secret,
        "provider": provider,
        "mode": mode,
    }


def _row_to_dict(row) -> dict:
    (
        row_id,
        provider,
        mode,
        nonce,
        binding_hash,
        handoff_hash,
        link_user_id,
        next_path,
        created_at,
        expires_at,
        consumed_at,
        profile_json,
    ) = db.row_values(row)
    try:
        profile = json.loads(profile_json) if profile_json else {}
    except ValueError:
        profile = {}
    if not isinstance(profile, dict):
        profile = {}
    return {
        "id": int(row_id),
        "profile": profile,
        "provider": str(provider or ""),
        "mode": str(mode or ""),
        "nonce": str(nonce or ""),
        "binding_hash": str(binding_hash or ""),
        "handoff_hash": str(handoff_hash or ""),
        "link_user_id": int(link_user_id or 0),
        "next_path": str(next_path or ""),
        "created_at": str(created_at or ""),
        "expires_at": str(expires_at or ""),
        "consumed_at": str(consumed_at or ""),
    }


_SELECT = (
    "SELECT id, provider, mode, nonce, binding_hash, handoff_hash, link_user_id, "
    "next_path, created_at, expires_at, consumed_at, profile_json "
    "FROM oauth_login_states "
)


def _spend(column: str, value: str, provider: str, conn=None) -> dict:
    """Spend one handshake exactly once, matched on `column`.

    The spend is a single conditional UPDATE. Reading the row, checking
    ``consumed_at``, then writing it would let two simultaneous replays of the
    same authorization response both observe an unconsumed row and both proceed.

    Returns the row *and* a fresh `handoff_token`, whose digest is written in the
    same statement. The caller cannot finish the sign-in at that point — it has
    no binding cookie, because both providers reply with a cross-site POST — so
    it redirects to a same-site GET carrying this token, and that GET is where
    the browser binding is checked.
    """

    provider = str(provider or "").strip().lower()
    if not value:
        raise StateError("missing_state")
    if provider not in PROVIDERS:
        raise StateError("unknown_provider", provider)

    handoff = secrets.token_urlsafe(32)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            f"UPDATE oauth_login_states SET consumed_at=?, handoff_hash=? "
            f"WHERE {column}=? AND provider=? AND consumed_at=''",
            (_stamp(_now()), _digest(handoff), value, provider),
        )
        spent = getattr(cur, "rowcount", 0)
        if owned:
            conn.commit()
        if spent != 1:
            # Either no such handshake, or it was already spent. Both are
            # refusals and neither is distinguished to the caller: telling a
            # prober which one it was tells them whether the value is real.
            raise StateError("unknown_or_spent_state")
        row = conn.execute(
            _SELECT + f"WHERE {column}=? AND provider=?", (value, provider)
        ).fetchone()
    finally:
        if owned:
            conn.close()

    if row is None:
        raise StateError("unknown_or_spent_state")
    record = _row_to_dict(row)
    if record["expires_at"] and record["expires_at"] < _stamp(_now()):
        # Expiry is checked after the spend, not instead of it: an expired
        # handshake must also be burned, or it stays replayable until the purge.
        raise StateError("expired_state")
    record["handoff_token"] = handoff
    return record


def consume(state: str, provider: str, conn=None) -> dict:
    """Spend the handshake named by a `state` parameter. Apple's callback path."""

    return _spend("state_hash", _digest(state), provider, conn)


def consume_by_nonce(nonce: str, provider: str, conn=None) -> dict:
    """Spend the handshake named by the nonce. Google's credential path.

    Google Identity Services posts a `credential` and its own CSRF token, and
    offers no way to add a `state` field to that POST — so there is no state
    parameter to match on. The nonce is the usable join instead, and it is a
    *stronger* one: a `state` parameter is a bare string in a redirect, while
    the nonce arrives inside the ID token Google signed. A caller cannot present
    a nonce for a handshake it did not open without also presenting a Google
    signature over it.

    Only safe because `verify_id_token` has already checked that signature. Spent
    before the nonce is trusted for anything else, so a replayed credential
    finds the handshake gone.
    """

    return _spend("nonce", str(nonce or ""), provider, conn)


def attach_profile(state_id: int, profile: dict, conn=None) -> None:
    """Park the verified provider claims on a spent handshake.

    Called only after the provider's signature has been checked, and only with
    claims — never with an authorization code, an access token or an ID token.
    What is parked is what the completing GET needs to identify the member and
    nothing else, because the completing GET is where the browser binding is
    checked and therefore the first place any of it may be acted on.
    """

    if not state_id:
        raise StateError("missing_state")
    payload = json.dumps(profile or {}, separators=(",", ":"), sort_keys=True)
    if len(payload) > 4000:
        # A provider that starts returning something large enough to hit this is
        # returning something this flow does not use. Refusing is correct: a
        # truncated profile would be a silently wrong identity.
        raise StateError("profile_too_large", str(len(payload)))

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            "UPDATE oauth_login_states SET profile_json=? WHERE id=?",
            (payload, int(state_id)),
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def discard(state_id: int, conn=None) -> None:
    """Drop a handshake and the claims parked on it.

    Called when the completing GET is done with it, successfully or not. The
    purge is a floor, not the mechanism: it runs hourly at best, and there is no
    reason for a verified email address to sit in this table for an hour after
    the sign-in it belonged to has finished.
    """

    if not state_id:
        return
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute("DELETE FROM oauth_login_states WHERE id=?", (int(state_id),))
        if owned:
            conn.commit()
    except Exception as exc:
        logging.warning("OAUTH_STATE_DISCARD_FAILED id=%s error=%s", state_id, exc)
    finally:
        if owned:
            conn.close()


def load(state_id: int, conn=None) -> dict:
    """Re-read a handshake by id. Spends nothing and proves nothing.

    For the one step that comes *after* the browser binding has already been
    checked: a brand new federated account still has to be asked its age and
    shown the agreements, and that answer arrives on a later request with the
    handshake long spent and the handoff burned.

    So the authority for that request is not this function — it is the signed
    Flask session, which could only have been given the id by the completing GET
    after `claim_handoff` passed. This just fetches the parked claims again,
    which is why it is deliberately not called `consume_by_id`: a caller that
    treated the id alone as permission would be authorising on an integer.
    """

    if not state_id:
        raise StateError("missing_state")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = conn.execute(_SELECT + "WHERE id=?", (int(state_id),)).fetchone()
    finally:
        if owned:
            conn.close()
    if row is None:
        raise StateError("unknown_or_spent_state")
    record = _row_to_dict(row)
    if record["expires_at"] and record["expires_at"] < _stamp(_now()):
        raise StateError("expired_state")
    return record


def claim_handoff(handoff_token: str, binding_secret: str, conn=None) -> dict:
    """Finish the handshake on the same-site GET. Verifies the browser binding.

    This is the step the whole table exists for. `handoff_token` proves the
    callback already validated the provider's response; `binding_secret` — read
    from a ``Lax`` cookie that only arrives here — proves it is the *same
    browser* that opened the handshake. Neither alone is sufficient.
    """

    if not handoff_token:
        raise StateError("missing_handoff")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = conn.execute(
            _SELECT + "WHERE handoff_hash=?", (_digest(handoff_token),)
        ).fetchone()
        if row is None:
            raise StateError("unknown_handoff")
        record = _row_to_dict(row)
        # Burn the handoff whatever the binding says, so a failed binding check
        # cannot be retried against a guessed cookie.
        conn.execute(
            "UPDATE oauth_login_states SET handoff_hash='' WHERE id=?", (record["id"],)
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()

    if not hmac.compare_digest(_digest(binding_secret), record["binding_hash"]):
        raise StateError("binding_mismatch")
    if record["expires_at"] and record["expires_at"] < _stamp(_now()):
        raise StateError("expired_state")
    return record


def purge_expired(older_than_seconds: int = 3600, conn=None) -> int:
    """Delete spent and expired handshakes. Nothing depends on them afterwards."""

    cutoff = _stamp(_now() - timedelta(seconds=max(0, int(older_than_seconds))))
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute("DELETE FROM oauth_login_states WHERE expires_at < ?", (cutoff,))
        removed = getattr(cur, "rowcount", 0) or 0
        if owned:
            conn.commit()
    except Exception as exc:
        logging.warning("OAUTH_STATE_PURGE_FAILED error=%s", exc)
        return 0
    finally:
        if owned:
            conn.close()
    return int(removed)
