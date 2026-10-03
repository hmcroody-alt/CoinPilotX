"""A native provider credential is honoured once, and the server decides.

## What this closes

A native Apple/Google assertion is a bearer credential. `oidc_tokens.verify_id_token`
proves it was issued by the provider, for this audience, and has not expired --
all of which stay true for every *subsequent* presentation of the same bytes.
So anything that obtains the token once (a hostile SDK in the process, a
jailbroken device, a logging sink, a proxy that terminates TLS) can mint an
unlimited number of sessions until `exp`, typically an hour.

Measured rather than assumed, against the real verifier:

    verify_id_token(token, nonce="")  -> ACCEPTED
    verify_id_token(token, nonce="")  -> ACCEPTED AGAIN   (identical bytes)

The nonce is not what stands in the way, for two independent reasons:

  * `oidc_tokens.py` compares it only when the caller supplies one (`if nonce:`),
    so omitting it skips the check rather than failing it.
  * It is chosen by the *client*, not minted by this server. An attacker holding
    a stolen token can read its own nonce claim and present that value back, so
    the comparison succeeds for exactly the party it is supposed to stop. A
    value the attacker controls on both sides is not a check.

And it cannot simply be made mandatory: `@react-native-google-signin` v16.1.5
has no nonce field anywhere in its typings, so the Google native SDK gives no
way to put a server value inside the token. A mandatory nonce would not harden
native Google, it would delete it.

So replay defence here cannot live in the token. It lives in the server
remembering which credentials it has already honoured.

## Why an INSERT and not a read-then-write

The whole guarantee is decided by `UNIQUE(credential_hash)`. Two simultaneous
presentations of one token both run the same INSERT; the database serialises
them and exactly one affects a row. A `SELECT` followed by an `INSERT` would let
both observe an unconsumed credential and both proceed -- which is precisely the
race an attacker with a captured token would run deliberately.

`INSERT OR IGNORE` rather than catching the integrity error, because on
PostgreSQL a failed statement aborts the entire transaction: a caught
`UniqueViolation` would leave the sign-in's own connection poisoned and take
down every statement after it. `services/db.py:942` rewrites `INSERT OR IGNORE`
to `ON CONFLICT DO NOTHING`, so the conflict is resolved by the database without
ever raising. `rowcount == 0` is the replay.

## What is stored, and for how long

Only `sha256(credential)`. The token itself is never written, never logged, and
never returned -- a stored ID token is a stored credential, and the digest
answers the only question asked of it ("have I seen exactly this before?")
without being reversible to something that could be replayed by whoever reads
the table.

Retention is bounded by the credential's own expiry. Past `exp` the row stops
being load-bearing, because `verify_id_token` already refuses the token on
expiry and will do so before this module is ever consulted -- so keeping the row
protects nothing. `purge_expired` therefore deletes on exactly the boundary
where the defence transfers back to the signature check.

That purge is called inline from `consume`, not from a worker. The sibling
module `oauth_login_state` ships a `purge_expired` with **zero call sites** in
the entire repository, so its table grows without bound; a retention strategy
that depends on a caller nobody wrote is not a retention strategy. Doing it on
the write path costs one indexed DELETE on a table whose live set is "tokens
issued in the last hour", and it cannot silently stop running.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from services import db

TABLE = "federated_credential_uses"

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# How long a consumed credential is remembered when the token carries no usable
# `exp`. `verify_id_token` requires `exp` and refuses without it, so this is a
# floor for a token that verified yet produced an unreadable claim rather than a
# normal path. Erring long: remembering too long costs a row, remembering too
# briefly re-opens the replay window.
FALLBACK_RETENTION_SECONDS = 3600

# Apple and Google both publish ID tokens with an hour-ish lifetime. A token
# claiming a far-future `exp` would otherwise pin a row forever, so retention is
# capped independently of what the token asks for.
MAX_RETENTION_SECONDS = 24 * 3600


class ReplayError(Exception):
    """A credential that has already been honoured was presented again."""

    def __init__(self, reason: str = "credential_replayed", detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        # Never the token, and never the digest. `detail` exists for the
        # provider name and nothing else; a digest in a log line is a
        # correlation key for exactly the credential this module exists to
        # protect.
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime(_TIME_FORMAT)


def digest(credential: str) -> str:
    """The stored identity of a credential: irreversible, and exact.

    SHA-256 over the raw compact JWT. Exactness is the point: only
    *byte-identical* presentation is refused, which is the definition of a
    replay, so a genuinely re-minted token is correctly treated as the new
    credential it is.

    What makes a re-mint differ is the provider's own per-issuance claims, not
    the clock. `iat`/`exp` have one-second resolution and RSA PKCS#1 v1.5
    signing is deterministic, so two tokens built from otherwise identical
    claims inside the same second are the *same bytes* --
    `tests/test_oidc_token_verification.py` pins that rather than assuming
    otherwise. Real provider tokens carry claims that differ per issuance
    (Apple binds the client `nonce`; Google's ID token carries `at_hash` over a
    freshly minted access token), so the case is not reachable in the live
    flow.

    And if it ever were, the direction of the error is the safe one: refusing
    costs a member one extra tap on a credential that differs a second later,
    where admitting would hand a replayer a session.
    """

    return hashlib.sha256((credential or "").encode("utf-8")).hexdigest()


def ensure_schema(conn=None) -> None:
    """Create the table. Call from `init_db()` only.

    Same constraint as `oauth_login_state.ensure_schema` and for the same
    reason: a sign-in route is already mid-transaction on its own connection by
    the time it needs this, so creating it on demand from a second connection
    waits on a write lock the caller still holds and deadlocks the sign-in it
    was meant to protect.

    `INTEGER PRIMARY KEY AUTOINCREMENT` is rewritten to `SERIAL PRIMARY KEY` on
    PostgreSQL by `services/db.py:837`, so this DDL is portable as written.
    """

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credential_hash TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                consumed_at TEXT NOT NULL,
                expires_at TEXT NOT NULL)"""
        )
        # Retention is a range scan over `expires_at` on every consume, so it is
        # indexed. Without this the purge degrades to a full scan of the live
        # credential set on each native sign-in.
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_federated_credential_uses_expires "
            f"ON {TABLE} (expires_at)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def _retention_stamp(expires_at_epoch) -> str:
    """When this row stops being load-bearing, from the token's own `exp`."""

    now = _now()
    ceiling = now + timedelta(seconds=MAX_RETENTION_SECONDS)
    try:
        moment = datetime.fromtimestamp(int(expires_at_epoch), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        moment = now + timedelta(seconds=FALLBACK_RETENTION_SECONDS)
    if moment > ceiling:
        moment = ceiling
    if moment < now:
        # An already-expired token should not have reached this module at all --
        # `verify_id_token` refuses on `exp` first. If one does, remembering it
        # for zero seconds would make it replayable, so it gets the floor.
        moment = now + timedelta(seconds=FALLBACK_RETENTION_SECONDS)
    return _stamp(moment)


def consume(provider: str, credential: str, *, expires_at_epoch=None, conn=None) -> None:
    """Honour this credential once, or raise :class:`ReplayError`.

    Call *after* the signature, issuer, audience and expiry have been verified,
    and *before* any session is created. After, so that an unauthenticated flood
    of garbage cannot write rows; before, so that the second presentation of a
    genuine token never reaches a session.
    """

    provider = str(provider or "").strip().lower()
    if not credential:
        raise ReplayError("missing_credential")

    credential_hash = digest(credential)
    expires_at = _retention_stamp(expires_at_epoch)

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO {TABLE} "
            f"(credential_hash, provider, consumed_at, expires_at) VALUES (?, ?, ?, ?)",
            (credential_hash, provider, _stamp(_now()), expires_at),
        )
        inserted = getattr(cur, "rowcount", 0)
        if inserted == 1:
            # Bounded retention, on the only path guaranteed to run. Inside the
            # same transaction as the insert so a rollback cannot leave the
            # table purged but the credential unconsumed.
            _purge_expired(conn)
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()

    if inserted != 1:
        raise ReplayError("credential_replayed", provider)


def _purge_expired(conn, now=None) -> int:
    cutoff = _stamp(now or _now())
    cur = conn.execute(f"DELETE FROM {TABLE} WHERE expires_at < ?", (cutoff,))
    return getattr(cur, "rowcount", 0) or 0


def purge_expired(conn=None, now=None) -> int:
    """Drop rows whose credential can no longer be replayed anyway.

    Exposed for operational use and for the retention test. The write path calls
    the private form directly so that it shares the caller's transaction.
    """

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        removed = _purge_expired(conn, now)
        if owned:
            conn.commit()
        return removed
    finally:
        if owned:
            conn.close()
