"""`account_user_id()` must check every credential presented, not the first one.

The native app sends a session cookie **and** an `Authorization: Bearer` on
every request. The old resolver was
`session.get(...) or <bearer> or <cookie restore>`, and `or` short-circuits, so
the cookie always won and the bearer branch was unreachable in production. Two
things fell out of that, and they pull in opposite directions:

**It broke writes.** `g.mobile_access_user_id` is set by the bearer branch, and
it is what tells a write gate "this request carried a credential a cross-site
attacker cannot forge, so it does not need a CSRF token"
(`pulse_ads_verify_write`, `bot.py:18448`). With the branch unreachable the flag
was never set, and the app holds no CSRF token — so reads succeeded and writes
returned 403, with no server error to find. `services/business_os_commerce_routes.py`
carries a local workaround for exactly this, which is the tell.

**It skipped the comparison.** Two credentials naming two different users were
never compared; whichever was found first was believed.

The fix verifies both and denies on disagreement. The dangerous part is not the
denial — it is the temptation to deny too much. `MOBILE_ACCESS_TOKEN_TTL_SECONDS`
is 900, so every native session routinely holds an *expired* access token next
to a *valid* cookie while it waits to refresh. That is a working client, not an
attack. A resolver that treated "bearer did not verify" as a conflict would sign
every phone on the platform out within fifteen minutes, and it would look
correct in review. `test_an_expired_bearer_is_not_a_conflict` and its siblings
are the whole reason this file exists.

`bot` resolves its database at import and never looks again, so DATABASE_URL has
to be set before that line. It is chosen in this directory's `conftest.py`, which
pytest imports before any test module here — see the note there for why doing it
per-module silently broke whichever of two modules was collected second.

Run: python3 -m pytest tests/web_parity/test_credential_resolution.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `setdefault`, not assignment: conftest.py has already chosen one database for the
# whole directory, and overwriting it here would be a no-op for `bot` (already
# imported) while pointing this module's fixtures at a file nothing reads.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("COINPILOTX_INIT_DB_ON_IMPORT", "1")
os.environ.setdefault("FLASK_SECRET_KEY", "credential-resolution-tests")

import bot  # noqa: E402

HTTPS = {"X-Forwarded-Proto": "https"}


def mint_access_token(user_id: int, device_hash: str, ttl: int = 900) -> str:
    """Build a token the real verifier will accept, the way the server builds it.

    Deliberately not a call to `bot.mobile_access_token()`: these tests need to
    mint an *expired* token, which the production helper will not do. The
    structure is pinned to `account_user_id_from_mobile_access_token()`
    (`bot.py:3638`) — if that changes shape, these tests should fail loudly
    rather than quietly stop exercising the bearer path.
    """
    payload = {"uid": user_id, "dh": device_hash, "exp": int(time.time()) + ttl}
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        bot.COINPILOTX_SECRET_KEY.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


def now_utc() -> datetime:
    """Naive UTC, because that is what the column holds.

    `bot.py:3681` and the session writers all store
    `datetime.utcnow().isoformat(timespec="seconds")` — no offset, no `Z`. These
    are ISO-8601 *strings* in SQLite, so comparison is lexicographic, and an
    offset-aware value serialises with a trailing `+00:00` that sorts after every
    naive timestamp of the same instant. A fixture written aware would therefore
    look fractionally newer than it is. `utcnow()` itself is deprecated; this is
    the same instant without the warning.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def register_session(user_id: int, device_hash: str, access_token: str,
                     status: str = "active", ttl: int = 900) -> None:
    """Insert the `mobile_security_sessions` row the verifier looks for."""
    conn = bot.db()
    cur = conn.cursor()
    now = now_utc()
    cur.execute(
        "INSERT INTO mobile_security_sessions (user_id, device_hash, "
        "refresh_token_hash, access_token_hash, status, created_at, "
        "last_seen_at, access_expires_at, refresh_expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id, device_hash,
            # `refresh_token_hash` is UNIQUE. These fixtures are function-scoped
            # and several tests register more than one session for the same
            # user and device, so the value has to be unique per row rather
            # than per identity.
            f"refresh-{uuid.uuid4().hex}",
            bot.mobile_token_hash(access_token),
            status,
            now.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
            (now + timedelta(seconds=ttl)).isoformat(timespec="seconds"),
            (now + timedelta(days=3650)).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture(scope="module", autouse=True)
def schema():
    with bot.webhook_app.app_context():
        bot.init_db()


@pytest.fixture
def alice():
    token = mint_access_token(701, "device-alice")
    register_session(701, "device-alice", token)
    return {"user_id": 701, "device": "device-alice", "token": token}


@pytest.fixture
def bob():
    token = mint_access_token(702, "device-bob")
    register_session(702, "device-bob", token)
    return {"user_id": 702, "device": "device-bob", "token": token}


def resolve(cookie_user_id=None, bearer=None):
    """Run the real resolver inside a real request, and report what it saw."""
    headers = dict(HTTPS)
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    with bot.webhook_app.test_request_context("/api/probe", headers=headers):
        if cookie_user_id is not None:
            from flask import session
            session["account_user_id"] = cookie_user_id
        from flask import g
        resolved = bot.account_user_id()
        return resolved, getattr(g, "mobile_access_user_id", None)


# --------------------------------------------------------------------------
# The fixture has to actually work, or every assertion here is vacuous.
# --------------------------------------------------------------------------

def test_a_minted_token_really_verifies(alice):
    """If minting were broken, every 'denied' assertion below would pass anyway."""
    resolved, flag = resolve(bearer=alice["token"])
    assert resolved == 701
    assert flag == 701


def test_a_forged_signature_does_not_verify(alice):
    body = alice["token"].rsplit(".", 1)[0]
    resolved, flag = resolve(bearer=f"{body}.{'0' * 64}")
    assert resolved is None
    assert flag is None


def test_a_token_with_no_session_row_does_not_verify():
    """Signature alone is not authority — the row is checked too."""
    orphan = mint_access_token(999, "device-orphan")
    resolved, _ = resolve(bearer=orphan)
    assert resolved is None


# --------------------------------------------------------------------------
# The bug: the bearer branch was unreachable whenever a cookie was present
# --------------------------------------------------------------------------

def test_the_bearer_is_verified_even_when_a_cookie_is_present(alice):
    """The regression that broke every native write.

    `g.mobile_access_user_id` must be set. A gate that reads it is deciding
    whether to require a CSRF token the native app does not have, so an unset
    flag means reads work and writes 403 — the symptom that sent
    `business_os_commerce_routes.py` off to write its own re-verification.
    """
    resolved, flag = resolve(cookie_user_id=701, bearer=alice["token"])
    assert resolved == 701
    assert flag == 701, (
        "the bearer branch was skipped because a cookie was present; "
        "g.mobile_access_user_id is dead again and native writes will 403"
    )


def test_the_bearer_alone_still_authenticates(alice):
    resolved, flag = resolve(bearer=alice["token"])
    assert resolved == 701
    assert flag == 701


def test_the_cookie_alone_still_authenticates():
    resolved, flag = resolve(cookie_user_id=701)
    assert resolved == 701
    assert flag is None, "no bearer was presented, so nothing may claim one was"


# --------------------------------------------------------------------------
# Conflict: two credentials, two identities
# --------------------------------------------------------------------------

def test_two_credentials_naming_different_users_are_refused(alice, bob):
    """Refuse, do not arbitrate.

    Choosing either credential means guessing which one the caller meant, and
    the cost of guessing wrong is acting with one user's authority under
    another user's identity. There is no request this pattern makes legitimate.
    """
    resolved, _ = resolve(cookie_user_id=701, bearer=bob["token"])
    assert resolved is None, (
        "a cookie for 701 and a valid bearer for 702 resolved to a user "
        "instead of being refused"
    )


def test_a_refused_conflict_is_recorded(alice, bob):
    """Silent denial is indistinguishable from a bug, at exactly the wrong moment."""
    conn = bot.db()
    before = conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE event_type=?",
        ("credential_identity_mismatch",),
    ).fetchone()[0]
    conn.close()

    resolve(cookie_user_id=701, bearer=bob["token"])

    conn = bot.db()
    after = conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE event_type=?",
        ("credential_identity_mismatch",),
    ).fetchone()[0]
    conn.close()
    assert after == before + 1


def test_the_conflict_check_is_not_fooled_by_types(alice):
    """A cookie holding `"701"` and a bearer holding `701` is the same user.

    Flask sessions round-trip through JSON, so a string user id is reachable.
    Comparing without normalising would make an ordinary request look like an
    identity conflict and deny it.
    """
    resolved, flag = resolve(cookie_user_id="701", bearer=alice["token"])
    assert resolved is not None
    assert flag == 701


# --------------------------------------------------------------------------
# The asymmetry — the part that would take production down if inverted
# --------------------------------------------------------------------------

def test_an_expired_bearer_is_not_a_conflict(alice):
    """The most important test in this file.

    Access tokens live 900 seconds. Every native session spends part of its
    life holding an expired one next to a cookie that is still valid, waiting
    on a refresh. Treating that as a credential conflict signs out every phone
    on the platform within fifteen minutes — and reads as *more* secure, which
    is how it would survive review.
    """
    expired = mint_access_token(701, "device-alice", ttl=-60)
    register_session(701, "device-alice", expired, ttl=-60)
    resolved, _ = resolve(cookie_user_id=701, bearer=expired)
    assert resolved == 701, (
        "an expired bearer was treated as a conflict; this logs out every "
        "native client one access-token lifetime after deploy"
    )


def test_a_revoked_bearer_does_not_invalidate_a_good_cookie(alice):
    """Same shape as expiry: unverifiable is 'absent', not 'conflicting'."""
    revoked = mint_access_token(701, "device-alice")
    register_session(701, "device-alice", revoked, status="revoked")
    resolved, _ = resolve(cookie_user_id=701, bearer=revoked)
    assert resolved == 701


def test_a_garbage_bearer_does_not_invalidate_a_good_cookie():
    for junk in ("", "not-a-token", "a.b.c", "Bearer", "." * 10):
        resolved, _ = resolve(cookie_user_id=701, bearer=junk)
        assert resolved == 701, f"{junk!r} broke an otherwise valid cookie session"


def test_a_bearer_for_the_wrong_device_is_not_a_conflict(alice):
    """Device mismatch fails verification, which means 'no bearer', not 'conflict'.

    It resolves to nobody rather than to a different user, so there is nothing
    to disagree with the cookie about.
    """
    wrong_device = mint_access_token(701, "device-somewhere-else")
    resolved, flag = resolve(cookie_user_id=701, bearer=wrong_device)
    assert resolved == 701
    assert flag is None


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------

def test_a_request_with_no_bearer_does_no_database_work(monkeypatch):
    """Every plain web request runs this resolver. It must stay free.

    The verifier returns before opening a connection when there is no
    `Authorization` header. If that ever stops being true, the cost lands on
    every page view on the site.
    """
    calls = []
    real_db = bot.db
    monkeypatch.setattr(bot, "db", lambda *a, **k: calls.append(1) or real_db(*a, **k))
    resolve(cookie_user_id=701)
    assert calls == [], f"{len(calls)} database connection(s) opened for a cookie-only request"


def test_the_bearer_is_verified_once_per_request(alice, monkeypatch):
    """`account_user_id()` has 45 call sites. The verification must be cached."""
    calls = []
    real = bot.account_user_id_from_mobile_access_token
    monkeypatch.setattr(
        bot, "account_user_id_from_mobile_access_token",
        lambda: calls.append(1) or real(),
    )
    headers = dict(HTTPS, Authorization=f"Bearer {alice['token']}")
    with bot.webhook_app.test_request_context("/api/probe", headers=headers):
        for _ in range(8):
            bot.account_user_id()
    assert len(calls) == 1, f"verified the bearer {len(calls)} times in one request"


def test_the_schema_guard_runs_at_most_once_per_process(monkeypatch):
    """Twelve DDL/catalog round trips per request is not acceptable on auth.

    The guard is optimistic — it only runs if the SELECT fails — so on a healthy
    database it should never run at all.
    """
    bot.ensure_mobile_security_session_schema_once.reset()
    calls = []
    real = bot.ensure_mobile_security_session_schema
    monkeypatch.setattr(
        bot, "ensure_mobile_security_session_schema",
        lambda cur: calls.append(1) or real(cur),
    )
    token = mint_access_token(701, "device-alice")
    register_session(701, "device-alice", token)
    for _ in range(5):
        resolve(bearer=token)
    assert len(calls) == 0, (
        "the schema guard ran on a healthy database; it is supposed to be a "
        "recovery path, not a per-request cost"
    )
