"""The Private Office second lock, proved against a real database.

Mission stages covered here (26-27, 36-48 test battery, backend half):

* **Policy (Stage 2)** — length, digits-only, trivial sequences refused.
* **Hash storage (Stage 3)** — a salted KDF hash lands in the row; the
  passcode's plaintext appears nowhere in the database file. This is scanned
  against the raw SQLite bytes, not a column, because "no plaintext column"
  is a claim about the whole file.
* **Unlock grant (Stages 4-5, 9-10)** — wrong passcode refused, right passcode
  mints a bounded grant; the token itself is never stored, only its SHA-256.
* **Bindings (Stage 14)** — a grant earned on one session/device fails from
  another.
* **Rate limit (Stage 5)** — free attempts, then escalating cooldowns that
  refuse even the *correct* passcode until they lapse; success resets.
* **Revocation (Stages 6, 12-13)** — lock-now, passcode change/reset, and
  account security events all kill live grants; a grant issued before the
  passcode changed is dead even if the revocation write were lost.
* **Reset (Stages 11-12)** — nothing happens without the route's elevated
  re-verification assertion.
* **Isolation (Stage 47)** — member B cannot ride member A's grant.
* **Audit (Stages 26-27)** — every security transition writes a metadata-only
  audit row, and no audit row ever contains the passcode.

Run either way::

    python -m pytest tests/private_office/test_office_security.py
    python tests/private_office/test_office_security.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_lock_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import security  # noqa: E402

USER_A = 9401
USER_B = 9402

PASSCODE_A = "824913"
PASSCODE_A2 = "371049"
PASSCODE_B = "605827"


_SHARED = None


def _conn():
    """One connection for the whole module, reused by every test.

    This used to open a fresh `db.connect()` per test and never close it. Around
    twenty-five live connections then accumulated against the same SQLite file,
    and as soon as one of them was left holding a write, every later write in
    the file blocked for the full busy timeout and then raised
    "database is locked". It presented as six or seven failures that moved
    around between runs and a seventy-second suite — a flaky security test,
    which is worth nothing: it cannot fail honestly and it trains you to ignore
    it.

    A single connection is safe here because no test needs two of them (each
    calls `_conn()` exactly once) and `_fresh_user` already resets the rows it
    depends on, so tests stay order-independent.
    """
    global _SHARED
    if _SHARED is None:
        conn = db.connect()
        cur = conn.cursor()
        schema.ensure_private_schema(cur)
        conn.commit()
        _SHARED = (conn, cur)
    return _SHARED


def _fresh_user(cur, conn, user_id: int, passcode: str) -> None:
    """A user with a configured lock and clean counters, whatever ran before."""
    cur.execute(f"DELETE FROM {schema.SECURITY_TABLE} WHERE user_id = ?", (user_id,))
    cur.execute(f"DELETE FROM {schema.GRANTS_TABLE} WHERE owner_user_id = ?", (user_id,))
    conn.commit()
    result = security.create_passcode(cur, user_id, passcode)
    conn.commit()
    assert result["ok"], result


def _expire_cooldown(cur, conn, user_id: int) -> None:
    cur.execute(
        f"UPDATE {schema.SECURITY_TABLE} SET locked_until = '', failed_attempt_count = 0 "
        "WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()


class TestPasscodePolicy:
    def test_too_short_refused(self):
        verdict = security.passcode_policy("12345")
        assert not verdict["ok"] and verdict["reason"] == "too_short"

    def test_non_digits_refused(self):
        for candidate in ("abcdef", "12345a", "12 456", ""):
            verdict = security.passcode_policy(candidate)
            assert not verdict["ok"] and verdict["reason"] == "digits_only", candidate

    def test_too_long_refused(self):
        verdict = security.passcode_policy("1" * 33)
        assert not verdict["ok"] and verdict["reason"] == "too_long"

    def test_trivial_refused(self):
        for candidate in ("000000", "999999", "123456", "654321", "456789",
                          "987654", "112233", "123123"):
            verdict = security.passcode_policy(candidate)
            assert not verdict["ok"] and verdict["reason"] == "trivial", candidate

    def test_reasonable_passcodes_accepted(self):
        for candidate in (PASSCODE_A, "203040", "918273", "1" * 6 + "2"):
            assert security.passcode_policy(candidate)["ok"], candidate


class TestHashStorage:
    def test_creation_stores_kdf_hash_never_plaintext(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)

        cur.execute(
            f"SELECT passcode_hash, hash_version FROM {schema.SECURITY_TABLE} "
            "WHERE user_id = ?",
            (USER_A,),
        )
        passcode_hash, hash_version = tuple(cur.fetchone())
        assert hash_version == security.HASH_VERSION
        assert PASSCODE_A not in passcode_hash
        assert passcode_hash.startswith("pbkdf2:") or "$" in passcode_hash

        # The zero-plaintext claim, made against the file rather than a column
        # — and against the WAL, so the check cannot pass vacuously while the
        # data is still sitting in the write-ahead log.
        conn.commit()
        cur.execute(
            f"SELECT COUNT(*) FROM {schema.SECURITY_TABLE} WHERE user_id = ?",
            (USER_A,),
        )
        assert tuple(cur.fetchone())[0] == 1
        scanned = 0
        for path in (_TMP_DB, _TMP_DB + "-wal"):
            if not os.path.exists(path):
                continue
            with open(path, "rb") as handle:
                assert PASSCODE_A.encode("utf-8") not in handle.read(), path
            scanned += 1
        assert scanned >= 1

    def test_second_creation_refused(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        result = security.create_passcode(cur, USER_A, PASSCODE_A2)
        assert not result["ok"] and result["error"] == security.ERR_ALREADY_SET

    def test_policy_enforced_at_creation(self):
        conn, cur = _conn()
        cur.execute(f"DELETE FROM {schema.SECURITY_TABLE} WHERE user_id = ?", (USER_A,))
        conn.commit()
        result = security.create_passcode(cur, USER_A, "123456")
        assert not result["ok"] and result["error"] == security.ERR_POLICY


class TestUnlockAndGrant:
    def test_wrong_passcode_refused(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        result = security.verify_and_unlock(cur, USER_A, PASSCODE_A2)
        assert not result["ok"] and result["error"] == security.ERR_WRONG_PASSCODE
        assert "grant_token" not in result

    def test_unlock_without_setup_refused(self):
        conn, cur = _conn()
        cur.execute(f"DELETE FROM {schema.SECURITY_TABLE} WHERE user_id = ?", (USER_B,))
        conn.commit()
        result = security.verify_and_unlock(cur, USER_B, PASSCODE_B)
        assert not result["ok"] and result["error"] == security.ERR_NOT_SET

    def test_correct_passcode_mints_bounded_grant(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        result = security.verify_and_unlock(cur, USER_A, PASSCODE_A)
        conn.commit()
        assert result["ok"], result
        token = result["grant_token"]
        assert token and result["expires_at"]

        assert security.validate_grant(cur, USER_A, token)["ok"]

        # Only the hash is stored: the row is found by hash, and the raw token
        # appears nowhere in the database file (or its WAL, if any).
        cur.execute(
            f"SELECT COUNT(*) FROM {schema.GRANTS_TABLE} WHERE token_hash = ?",
            (security.token_hash(token),),
        )
        assert tuple(cur.fetchone())[0] == 1
        for path in (_TMP_DB, _TMP_DB + "-wal"):
            if not os.path.exists(path):
                continue
            with open(path, "rb") as handle:
                assert token.encode("utf-8") not in handle.read(), path

    def test_expired_grant_refused(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        token = security.verify_and_unlock(cur, USER_A, PASSCODE_A)["grant_token"]
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        cur.execute(
            f"UPDATE {schema.GRANTS_TABLE} SET expires_at = ? WHERE token_hash = ?",
            (past, security.token_hash(token)),
        )
        conn.commit()
        assert not security.validate_grant(cur, USER_A, token)["ok"]

    def test_garbage_tokens_refused(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        for bad in ("", "not-a-token", security.token_hash("x")):
            assert not security.validate_grant(cur, USER_A, bad)["ok"], bad

    def test_no_request_context_fails_closed(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        verdict = security.request_is_unlocked(cur, USER_A)
        assert verdict == {"ok": False, "setup_required": False}


class TestBindings:
    def test_grant_bound_to_session_and_device(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        result = security.verify_and_unlock(
            cur, USER_A, PASSCODE_A,
            session_binding="session-1", device_binding="device-1",
        )
        token = result["grant_token"]

        ok = security.validate_grant(
            cur, USER_A, token, session_binding="session-1", device_binding="device-1"
        )
        assert ok["ok"]

        for session, device in (
            ("session-2", "device-1"),   # different session
            ("session-1", "device-2"),   # different device
            ("", "device-1"),            # binding stripped
            ("session-1", ""),
            ("", ""),
        ):
            verdict = security.validate_grant(
                cur, USER_A, token, session_binding=session, device_binding=device
            )
            assert not verdict["ok"], (session, device)


def _request_bindings_for(bearer="", cookie="", device="device-1"):
    """request_bindings() as computed inside a synthetic Flask request."""
    from flask import Flask

    headers = {security.DEVICE_HEADER: device}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    if cookie:
        headers["Cookie"] = "session=" + cookie
    app = Flask(__name__)
    with app.test_request_context("/", headers=headers):
        return security.request_bindings()


class TestSessionFamilyBinding:
    """The mobile bearer rotates every ~15 minutes; a standing grant must
    survive that rotation (bind to the session family), yet still die with the
    sign-in itself (logout / revocation empties the resolver's answer)."""

    def teardown_method(self):
        security.register_session_family_resolver(None)

    def test_rotated_bearer_keeps_the_same_binding_and_the_grant_survives(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        family = {"token-epoch-1": "fam-A", "token-epoch-2": "fam-A"}
        security.register_session_family_resolver(lambda tok: family.get(tok, ""))

        minted_session, minted_device = _request_bindings_for(bearer="token-epoch-1")
        rotated_session, rotated_device = _request_bindings_for(bearer="token-epoch-2")
        assert minted_session == rotated_session
        assert minted_device == rotated_device == "device-1"

        result = security.verify_and_unlock(
            cur, USER_A, PASSCODE_A,
            session_binding=minted_session, device_binding=minted_device,
        )
        verdict = security.validate_grant(
            cur, USER_A, result["grant_token"],
            session_binding=rotated_session, device_binding=rotated_device,
        )
        assert verdict["ok"]

    def test_revoked_session_family_kills_the_grant(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        alive = {"token-epoch-1": "fam-A"}
        security.register_session_family_resolver(lambda tok: alive.get(tok, ""))

        minted_session, minted_device = _request_bindings_for(bearer="token-epoch-1")
        result = security.verify_and_unlock(
            cur, USER_A, PASSCODE_A,
            session_binding=minted_session, device_binding=minted_device,
        )

        # Logout/revocation: the next sign-in's bearer belongs to no known
        # family, so the binding falls back to the raw-token hash and the old
        # grant's family binding can never match again.
        later_session, later_device = _request_bindings_for(bearer="token-epoch-3")
        verdict = security.validate_grant(
            cur, USER_A, result["grant_token"],
            session_binding=later_session, device_binding=later_device,
        )
        assert not verdict["ok"]

    def test_without_resolver_each_bearer_hashes_alone(self):
        session_1, _ = _request_bindings_for(bearer="token-epoch-1")
        session_2, _ = _request_bindings_for(bearer="token-epoch-2")
        assert session_1 and session_2 and session_1 != session_2

    def test_resolver_failure_falls_back_to_raw_bearer_hash(self):
        baseline, _ = _request_bindings_for(bearer="token-epoch-1")

        def explode(_tok):
            raise RuntimeError("resolver down")

        security.register_session_family_resolver(explode)
        degraded, _ = _request_bindings_for(bearer="token-epoch-1")
        assert degraded == baseline

    def test_cookie_only_requests_are_untouched(self):
        security.register_session_family_resolver(lambda tok: "fam-A")
        with_resolver, _ = _request_bindings_for(cookie="web-session-1")
        security.register_session_family_resolver(None)
        without_resolver, _ = _request_bindings_for(cookie="web-session-1")
        assert with_resolver == without_resolver


class TestRateLimit:
    def test_escalating_cooldown_then_reset(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)

        # Free attempts: refused but not cooled down.
        for _ in range(security.FREE_ATTEMPTS):
            result = security.verify_and_unlock(cur, USER_A, PASSCODE_A2)
            assert result["error"] == security.ERR_WRONG_PASSCODE
        conn.commit()

        # Next failure starts the schedule.
        result = security.verify_and_unlock(cur, USER_A, PASSCODE_A2)
        conn.commit()
        assert result["error"] == security.ERR_COOLDOWN
        assert result["retry_after_seconds"] == security.COOLDOWN_STEPS_SECONDS[0]

        # While cooled down, even the CORRECT passcode is refused — the
        # cooldown protects the passcode from the attacker holding the phone.
        result = security.verify_and_unlock(cur, USER_A, PASSCODE_A)
        assert result["error"] == security.ERR_COOLDOWN
        assert "grant_token" not in result

        # Cooldown lapses (simulated), one more failure escalates the wait.
        _expire_cooldown(cur, conn, USER_A)
        cur.execute(
            f"UPDATE {schema.SECURITY_TABLE} SET failed_attempt_count = ? WHERE user_id = ?",
            (security.FREE_ATTEMPTS + 1, USER_A),
        )
        conn.commit()
        result = security.verify_and_unlock(cur, USER_A, PASSCODE_A2)
        conn.commit()
        assert result["error"] == security.ERR_COOLDOWN
        assert result["retry_after_seconds"] == security.COOLDOWN_STEPS_SECONDS[1]

        # Success resets the counters entirely.
        _expire_cooldown(cur, conn, USER_A)
        result = security.verify_and_unlock(cur, USER_A, PASSCODE_A)
        conn.commit()
        assert result["ok"]
        cur.execute(
            f"SELECT failed_attempt_count, locked_until FROM {schema.SECURITY_TABLE} "
            "WHERE user_id = ?",
            (USER_A,),
        )
        count, locked_until = tuple(cur.fetchone())
        assert count == 0 and locked_until == ""

    def test_external_failure_counts_against_same_cooldown(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        for _ in range(security.FREE_ATTEMPTS):
            security.register_external_failure(cur, USER_A)
        wait = security.register_external_failure(cur, USER_A)
        conn.commit()
        assert wait == security.COOLDOWN_STEPS_SECONDS[0]
        result = security.verify_and_unlock(cur, USER_A, PASSCODE_A)
        assert result["error"] == security.ERR_COOLDOWN


class TestRevocation:
    def _unlocked(self, cur, conn, user_id, passcode):
        result = security.verify_and_unlock(cur, user_id, passcode)
        conn.commit()
        assert result["ok"]
        return result["grant_token"]

    def test_lock_now_revokes(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        token = self._unlocked(cur, conn, USER_A, PASSCODE_A)
        revoked = security.revoke_grants(cur, USER_A, reason="lock_now")
        conn.commit()
        assert revoked >= 1
        assert not security.validate_grant(cur, USER_A, token)["ok"]

    def test_single_token_revocation_spares_others(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        token_1 = self._unlocked(cur, conn, USER_A, PASSCODE_A)
        token_2 = self._unlocked(cur, conn, USER_A, PASSCODE_A)
        security.revoke_grants(cur, USER_A, reason="lock_one", token=token_1)
        conn.commit()
        assert not security.validate_grant(cur, USER_A, token_1)["ok"]
        assert security.validate_grant(cur, USER_A, token_2)["ok"]

    def test_change_revokes_and_old_passcode_dies(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        token = self._unlocked(cur, conn, USER_A, PASSCODE_A)

        result = security.change_passcode(cur, USER_A, PASSCODE_A, PASSCODE_A2)
        conn.commit()
        assert result["ok"], result
        assert not security.validate_grant(cur, USER_A, token)["ok"]
        assert security.verify_and_unlock(cur, USER_A, PASSCODE_A)["error"] == \
            security.ERR_WRONG_PASSCODE
        _expire_cooldown(cur, conn, USER_A)
        assert security.verify_and_unlock(cur, USER_A, PASSCODE_A2)["ok"]

    def test_change_requires_current_passcode(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        result = security.change_passcode(cur, USER_A, PASSCODE_A2, PASSCODE_B)
        assert not result["ok"] and result["error"] == security.ERR_WRONG_PASSCODE

    def test_grant_issued_before_change_is_dead_even_unrevoked(self):
        """Belt-and-braces: un-revoke a pre-change grant; issued_at < changed_at
        still refuses it."""
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        token = self._unlocked(cur, conn, USER_A, PASSCODE_A)
        security.change_passcode(cur, USER_A, PASSCODE_A, PASSCODE_A2)
        cur.execute(
            f"UPDATE {schema.GRANTS_TABLE} SET revoked_at = '', revoke_reason = '' "
            "WHERE token_hash = ?",
            (security.token_hash(token),),
        )
        conn.commit()
        assert not security.validate_grant(cur, USER_A, token)["ok"]

    def test_account_security_event_relocks(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        token = self._unlocked(cur, conn, USER_A, PASSCODE_A)
        security.on_account_security_event(cur, USER_A, event="password_reset")
        conn.commit()
        assert not security.validate_grant(cur, USER_A, token)["ok"]
        cur.execute(
            f"SELECT revoke_reason FROM {schema.GRANTS_TABLE} WHERE token_hash = ?",
            (security.token_hash(token),),
        )
        assert tuple(cur.fetchone())[0] == "account_event:password_reset"


class TestReset:
    def test_reset_without_reverification_refused(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        result = security.reset_passcode(cur, USER_A, PASSCODE_A2, reverified=False)
        assert not result["ok"] and result["error"] == security.ERR_REVERIFY
        # The old passcode still stands.
        assert security.verify_and_unlock(cur, USER_A, PASSCODE_A)["ok"]

    def test_reset_rotates_and_revokes(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        token = security.verify_and_unlock(cur, USER_A, PASSCODE_A)["grant_token"]
        conn.commit()

        result = security.reset_passcode(cur, USER_A, PASSCODE_A2, reverified=True)
        conn.commit()
        assert result["ok"], result
        assert not security.validate_grant(cur, USER_A, token)["ok"]
        assert security.verify_and_unlock(cur, USER_A, PASSCODE_A2)["ok"]

    def test_reset_enforces_policy(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        result = security.reset_passcode(cur, USER_A, "123456", reverified=True)
        assert not result["ok"] and result["error"] == security.ERR_POLICY


class TestIsolation:
    def test_member_b_cannot_ride_member_a_grant(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        _fresh_user(cur, conn, USER_B, PASSCODE_B)
        token_a = security.verify_and_unlock(cur, USER_A, PASSCODE_A)["grant_token"]
        conn.commit()
        assert security.validate_grant(cur, USER_A, token_a)["ok"]
        assert not security.validate_grant(cur, USER_B, token_a)["ok"]

    def test_b_state_untouched_by_a_revocation(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        _fresh_user(cur, conn, USER_B, PASSCODE_B)
        security.verify_and_unlock(cur, USER_A, PASSCODE_A)
        token_b = security.verify_and_unlock(cur, USER_B, PASSCODE_B)["grant_token"]
        conn.commit()
        security.revoke_grants(cur, USER_A, reason="lock_now")
        conn.commit()
        assert security.validate_grant(cur, USER_B, token_b)["ok"]


class TestBiometricPreference:
    def test_requires_setup(self):
        conn, cur = _conn()
        cur.execute(f"DELETE FROM {schema.SECURITY_TABLE} WHERE user_id = ?", (USER_B,))
        conn.commit()
        result = security.set_biometric_preference(cur, USER_B, True)
        assert not result["ok"] and result["error"] == security.ERR_NOT_SET

    def test_flag_round_trip(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        assert security.set_biometric_preference(cur, USER_A, True)["biometric_preference"] == "enabled"
        assert security.security_state(cur, USER_A)["biometric_preference"] == "enabled"
        assert security.set_biometric_preference(cur, USER_A, False)["biometric_preference"] == "disabled"
        assert security.security_state(cur, USER_A)["biometric_preference"] == "disabled"


def _audit_watermark(cur, uid: int) -> int:
    """The highest audit id this owner has right now.

    Earlier revisions cleared the slate with a DELETE against the audit table,
    which the write-boundary guard rightly flags: a test that can erase audit
    rows is a worked example of the offence the table exists to prevent. Only
    rows *after* the watermark are asserted on instead — same isolation, zero
    writes outside the canonical writers.
    """
    cur.execute(
        f"SELECT COALESCE(MAX(id), 0) FROM {schema.AUDIT_TABLE} "
        f"WHERE owner_user_id = ?",
        (uid,),
    )
    row = cur.fetchone()
    return int(tuple(row)[0] if row is not None else 0)


class TestAudit:
    def test_every_transition_writes_a_row_and_none_leak_the_passcode(self):
        conn, cur = _conn()
        baseline = _audit_watermark(cur, USER_A)
        _fresh_user(cur, conn, USER_A, PASSCODE_A)               # CREATED
        security.verify_and_unlock(cur, USER_A, PASSCODE_A2)     # UNLOCK_FAILED
        _expire_cooldown(cur, conn, USER_A)
        security.verify_and_unlock(cur, USER_A, PASSCODE_A)      # UNLOCKED
        security.revoke_grants(cur, USER_A, reason="lock_now")   # LOCKED
        security.change_passcode(cur, USER_A, PASSCODE_A, PASSCODE_A2)  # CHANGED
        security.reset_passcode(cur, USER_A, PASSCODE_B, reverified=True)  # RESET
        security.set_biometric_preference(cur, USER_A, True)     # BIOMETRIC_ENABLED
        security.set_biometric_preference(cur, USER_A, False)    # BIOMETRIC_DISABLED
        conn.commit()

        cur.execute(
            f"SELECT action FROM {schema.AUDIT_TABLE} "
            f"WHERE owner_user_id = ? AND id > ?",
            (USER_A, baseline),
        )
        actions = {tuple(row)[0] for row in cur.fetchall()}
        for expected in (
            audit.ACTION_OFFICE_PASSCODE_CREATED,
            audit.ACTION_OFFICE_UNLOCK_FAILED,
            audit.ACTION_OFFICE_UNLOCKED,
            audit.ACTION_OFFICE_LOCKED,
            audit.ACTION_OFFICE_PASSCODE_CHANGED,
            audit.ACTION_OFFICE_PASSCODE_RESET,
            audit.ACTION_OFFICE_BIOMETRIC_ENABLED,
            audit.ACTION_OFFICE_BIOMETRIC_DISABLED,
        ):
            assert expected in actions, expected

        # Metadata-only: no passcode, in any column, in any row.
        cur.execute(
            f"SELECT * FROM {schema.AUDIT_TABLE} "
            f"WHERE owner_user_id = ? AND id > ?",
            (USER_A, baseline),
        )
        for row in cur.fetchall():
            for value in tuple(row):
                for secret in (PASSCODE_A, PASSCODE_A2, PASSCODE_B):
                    assert secret not in str(value)

    def test_cooldown_refusal_is_audited_denied(self):
        conn, cur = _conn()
        _fresh_user(cur, conn, USER_A, PASSCODE_A)
        future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        cur.execute(
            f"UPDATE {schema.SECURITY_TABLE} SET locked_until = ? WHERE user_id = ?",
            (future, USER_A),
        )
        baseline = _audit_watermark(cur, USER_A)
        conn.commit()
        security.verify_and_unlock(cur, USER_A, PASSCODE_A)
        conn.commit()
        cur.execute(
            f"SELECT action, outcome FROM {schema.AUDIT_TABLE} "
            f"WHERE owner_user_id = ? AND id > ?",
            (USER_A, baseline),
        )
        rows = [tuple(row) for row in cur.fetchall()]
        assert (audit.ACTION_OFFICE_UNLOCK_FAILED, audit.OUTCOME_DENIED) in rows


if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-q"]))
