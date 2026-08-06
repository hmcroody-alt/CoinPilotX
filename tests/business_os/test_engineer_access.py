"""Security tests for the Engineer Access gate.

These target `services/business_os/engineer_access.py`, which is deliberately
pure (no Flask, no DB, no `bot` import) so the whole authorization decision can
be exercised directly.

The theme throughout: it is not enough that the correct passcode works. What
matters is that every *incorrect* path is indistinguishable from every other
incorrect path, that a lockout cannot be shortened by anything the client
controls, and that identity can never be asserted by a mutable, client-visible
field.
"""

from __future__ import annotations

import json
import time

import pytest

from services.business_os import engineer_access as ea


# A passcode used only by this test module. It is not the production value,
# which exists solely as a salted hash in the deployment environment.
TEST_PASSCODE = "13572468"
OWNER_ID = 4242
OUTSIDER_ID = 9001

# Hashing at production cost (600k iterations) would make this suite crawl.
# The iteration count is a tunable stored inside the encoded hash, so a lower
# cost here exercises exactly the same code path.
TEST_ITERATIONS = 1000


@pytest.fixture(scope="module")
def encoded_hash() -> str:
    return ea.hash_passcode(TEST_PASSCODE, iterations=TEST_ITERATIONS)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """No test may accidentally read real deployment configuration."""
    for name in (ea.PASSCODE_HASH_ENV, ea.GRANT_SECRET_ENV, ea.ENABLED_ENV, ea.ROLES_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PULSESOC_BUSINESS_OS_OWNER_USER_IDS", str(OWNER_ID))


def attempt(**overrides):
    """Run one verification with sane defaults, overriding only what matters."""
    kwargs = {
        "user": {"user_id": OWNER_ID},
        "passcode": TEST_PASSCODE,
        "enabled": True,
        "secret": "test-grant-secret",
    }
    kwargs.update(overrides)
    return ea.evaluate_engineer_access(**kwargs)


# ---------------------------------------------------------------------------
# Passcode handling
# ---------------------------------------------------------------------------

class TestPasscodeHashing:
    def test_hash_does_not_contain_the_passcode(self, encoded_hash):
        assert TEST_PASSCODE not in encoded_hash

    def test_same_passcode_hashes_differently_each_time(self):
        first = ea.hash_passcode(TEST_PASSCODE, iterations=TEST_ITERATIONS)
        second = ea.hash_passcode(TEST_PASSCODE, iterations=TEST_ITERATIONS)
        # Distinct salts. Without this, a leaked hash would confirm that two
        # deployments share a passcode.
        assert first != second
        assert ea.verify_passcode(TEST_PASSCODE, first)
        assert ea.verify_passcode(TEST_PASSCODE, second)

    def test_wrong_passcode_rejected(self, encoded_hash):
        assert not ea.verify_passcode("13572469", encoded_hash)
        assert not ea.verify_passcode("00000000", encoded_hash)

    @pytest.mark.parametrize(
        "bad_hash",
        [
            "",
            None,
            "not-a-hash",
            "pbkdf2_sha256$notanumber$c2FsdA==$ZGlnZXN0",
            "md5$1000$c2FsdA==$ZGlnZXN0",          # wrong scheme
            "pbkdf2_sha256$0$c2FsdA==$ZGlnZXN0",   # zero iterations
            "pbkdf2_sha256$1000$$ZGlnZXN0",        # empty salt
            "pbkdf2_sha256$1000$c2FsdA==$",        # empty digest
            "pbkdf2_sha256$1000$!!!!$ZGlnZXN0",    # non-base64 salt
        ],
    )
    def test_malformed_configuration_fails_closed(self, bad_hash):
        """A configuration mistake must deny, never raise and never allow."""
        assert ea.verify_passcode(TEST_PASSCODE, bad_hash) is False

    def test_unconfigured_hash_denies_everything(self):
        # os.getenv path with the env var absent (see _isolate_env).
        assert ea.verify_passcode(TEST_PASSCODE) is False

    @pytest.mark.parametrize(
        "candidate,expected",
        [
            ("13572468", True),
            ("1357246", False),    # 7 digits
            ("135724688", False),  # 9 digits
            ("1357246a", False),   # non-numeric
            ("", False),
            (None, False),
            ("  135724", False),
        ],
    )
    def test_shape_check(self, candidate, expected):
        assert ea.passcode_is_well_formed(candidate) is expected


# ---------------------------------------------------------------------------
# Identity — §9 DO_NOT_AUTHORIZE_BY
# ---------------------------------------------------------------------------

class TestIdentity:
    def test_configured_owner_id_authorizes(self):
        assert ea.engineer_identity_authorized({"user_id": OWNER_ID}, owner_ids={OWNER_ID})

    def test_unrelated_account_does_not(self):
        assert not ea.engineer_identity_authorized({"user_id": OUTSIDER_ID}, owner_ids={OWNER_ID})

    def test_approved_admin_role_authorizes(self):
        assert ea.engineer_identity_authorized(
            {"user_id": OUTSIDER_ID}, admin_role="internal_supertester", owner_ids={OWNER_ID}
        )

    def test_suspended_admin_does_not(self):
        """Role alone is not enough — the admin record must still be active."""
        assert not ea.engineer_identity_authorized(
            {"user_id": OUTSIDER_ID},
            admin_role="internal_supertester",
            admin_status="suspended",
            owner_ids={OWNER_ID},
        )

    def test_unapproved_role_does_not(self):
        assert not ea.engineer_identity_authorized(
            {"user_id": OUTSIDER_ID}, admin_role="moderator", owner_ids={OWNER_ID}
        )

    @pytest.mark.parametrize("user", [None, {}, {"user_id": 0}, {"user_id": -1}, {"user_id": None}])
    def test_missing_or_invalid_user_does_not(self, user):
        assert not ea.engineer_identity_authorized(user, owner_ids={OWNER_ID})

    def test_mutable_profile_fields_are_not_parameters(self):
        """The function cannot be told a display name, username, or email.

        This is the structural form of the §9 rule: rather than testing that
        those fields are ignored, we assert they are not accepted at all, so a
        future edit cannot quietly start honouring one.
        """
        import inspect

        params = set(inspect.signature(ea.engineer_identity_authorized).parameters)
        forbidden = {"email", "username", "display_name", "name", "profile", "avatar", "handle"}
        assert not (params & forbidden)

    def test_spoofed_identity_fields_on_the_user_object_are_ignored(self, encoded_hash):
        """A client that fabricates every plausible authorization field still fails."""
        impostor = {
            "user_id": OUTSIDER_ID,
            "email": "cherieroody@gmail.com",
            "username": "roody",
            "display_name": "Roody Cherie",
            "is_owner": True,
            "is_admin": True,
            "role": "owner",
            "engineer": True,
            "developer_mode": True,
        }
        result = attempt(user=impostor, encoded_hash=encoded_hash)
        assert result["authorized"] is False
        assert "grant" not in result


# ---------------------------------------------------------------------------
# Denial uniformity — §4 and §7
# ---------------------------------------------------------------------------

class TestDenialUniformity:
    def test_every_non_lockout_denial_is_byte_identical(self, encoded_hash):
        """Right account + wrong passcode must look exactly like wrong account
        + right passcode, and like a disabled feature. Any difference tells an
        attacker which half of the credential they already have.
        """
        variants = [
            attempt(passcode="00000000", encoded_hash=encoded_hash),                       # wrong passcode
            attempt(user={"user_id": OUTSIDER_ID}, encoded_hash=encoded_hash),             # wrong account
            attempt(user={"user_id": OUTSIDER_ID}, passcode="00000000", encoded_hash=encoded_hash),
            attempt(user={"user_id": OUTSIDER_ID}, admin_role="engineer",
                    admin_status="suspended", encoded_hash=encoded_hash),                  # suspended engineer
            attempt(enabled=False, encoded_hash=encoded_hash),                             # feature off
            attempt(passcode="123", encoded_hash=encoded_hash),                            # malformed
            attempt(encoded_hash=None),                                                    # unconfigured hash
        ]
        # Strip server-side bookkeeping; the route never sends those to the client.
        client_visible = [
            {k: v for k, v in v_.items() if k not in {"record_failure", "reset_failures", "lock_for_seconds", "failure_count"}}
            for v_ in variants
        ]
        serialized = {json.dumps(payload, sort_keys=True) for payload in client_visible}
        assert len(serialized) == 1, "denial responses differ between failure causes: {}".format(serialized)

    def test_denial_never_mentions_the_reason(self, encoded_hash):
        result = attempt(user={"user_id": OUTSIDER_ID}, passcode="00000000", encoded_hash=encoded_hash)
        blob = json.dumps(result).lower()
        for leak in ("passcode", "digit", "account", "owner", "role", "admin", "identity", "hash", "length"):
            assert leak not in blob

    def test_denial_never_echoes_the_submitted_passcode(self, encoded_hash):
        result = attempt(passcode="87654321", encoded_hash=encoded_hash)
        assert "87654321" not in json.dumps(result)


# ---------------------------------------------------------------------------
# Lockout ladder — §8
# ---------------------------------------------------------------------------

class TestLockout:
    def test_first_two_failures_warn_only(self):
        assert ea.lockout_seconds_for(1) == 0
        assert ea.lockout_seconds_for(2) == 0

    def test_third_failure_locks_for_sixty_seconds(self):
        assert ea.lockout_seconds_for(3) == 60

    def test_fourth_failure_locks_for_five_minutes(self):
        assert ea.lockout_seconds_for(4) == 300

    def test_escalation_is_monotonic(self):
        durations = [ea.lockout_seconds_for(n) for n in range(3, 8)]
        assert durations == sorted(durations), "lockout must never get shorter with more failures"

    def test_sustained_abuse_requires_a_fresh_session(self):
        assert ea.requires_fresh_session(ea.REAUTH_REQUIRED_AFTER_FAILURES)
        assert not ea.requires_fresh_session(ea.REAUTH_REQUIRED_AFTER_FAILURES - 1)

    def test_third_consecutive_attempt_returns_a_countdown(self, encoded_hash):
        result = attempt(passcode="00000000", consecutive_failures=2, encoded_hash=encoded_hash)
        assert result["authorized"] is False
        assert result["locked"] is True
        assert result["retry_after_seconds"] == 60
        assert result["lock_for_seconds"] == 60

    def test_correct_passcode_is_refused_while_locked(self, encoded_hash):
        """The lockout is not a hint filter — it stops verification outright."""
        now = time.time()
        result = attempt(locked_until=now + 45, now=now, encoded_hash=encoded_hash)
        assert result["authorized"] is False
        assert "grant" not in result
        assert result["retry_after_seconds"] == 46

    def test_a_locked_attempt_does_not_extend_the_lockout(self, encoded_hash):
        """Hammering during a lockout must not stack failures — otherwise an
        attacker could push a victim into a permanent lock from outside."""
        now = time.time()
        result = attempt(locked_until=now + 45, now=now, encoded_hash=encoded_hash)
        assert result["record_failure"] is False
        assert result["lock_for_seconds"] == 0

    def test_countdown_is_derived_from_the_server_clock(self):
        """§8: an app restart must not bypass the lockout. The remaining time is
        a function of a server-held absolute timestamp, so nothing the client
        does between attempts can shorten it."""
        locked_until = 1_000_000
        assert ea.lockout_remaining(locked_until, now=locked_until - 30) == 31
        assert ea.lockout_remaining(locked_until, now=locked_until) == 0
        assert ea.lockout_remaining(locked_until, now=locked_until + 5) == 0

    @pytest.mark.parametrize("value", [None, "", "abc", [], {}])
    def test_unreadable_lockout_value_means_not_locked(self, value):
        assert ea.lockout_remaining(value, now=1000) == 0

    def test_expired_lockout_lets_a_valid_attempt_through(self, encoded_hash):
        now = time.time()
        result = attempt(locked_until=now - 1, now=now, encoded_hash=encoded_hash)
        assert result["authorized"] is True


# ---------------------------------------------------------------------------
# Grants — §4, §5, §6
# ---------------------------------------------------------------------------

class TestGrant:
    SECRET = "grant-signing-secret"

    def test_grant_carries_no_passcode(self):
        grant = ea.issue_grant(OWNER_ID, session_id="s1", device_id="d1", secret=self.SECRET)
        assert TEST_PASSCODE not in grant["token"]
        payload = ea.verify_grant(grant["token"], secret=self.SECRET)
        assert "passcode" not in json.dumps(payload).lower()

    def test_round_trip(self):
        grant = ea.issue_grant(OWNER_ID, session_id="s1", secret=self.SECRET)
        payload = ea.verify_grant(grant["token"], user_id=OWNER_ID, session_id="s1", secret=self.SECRET)
        assert payload is not None
        assert payload["sub"] == OWNER_ID

    def test_tampered_payload_is_rejected(self):
        """Re-encoding the body with a different subject must not validate."""
        import base64 as b64

        grant = ea.issue_grant(OUTSIDER_ID, secret=self.SECRET)
        body, _, signature = grant["token"].partition(".")
        payload = json.loads(ea._unb64url(body))
        payload["sub"] = OWNER_ID  # privilege escalation attempt
        forged_body = b64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ).decode().rstrip("=")
        assert ea.verify_grant("{}.{}".format(forged_body, signature), secret=self.SECRET) is None

    def test_signature_from_a_different_secret_is_rejected(self):
        grant = ea.issue_grant(OWNER_ID, secret="attacker-secret")
        assert ea.verify_grant(grant["token"], secret=self.SECRET) is None

    def test_expired_grant_is_rejected(self):
        now = time.time()
        grant = ea.issue_grant(OWNER_ID, ttl_seconds=60, secret=self.SECRET, now=now)
        assert ea.verify_grant(grant["token"], secret=self.SECRET, now=now + 61) is None

    def test_replaying_an_expired_grant_after_a_new_one_still_fails(self):
        """Expiry is absolute, not relative to the newest issuance."""
        now = time.time()
        old = ea.issue_grant(OWNER_ID, ttl_seconds=10, secret=self.SECRET, now=now)
        ea.issue_grant(OWNER_ID, ttl_seconds=1800, secret=self.SECRET, now=now + 100)
        assert ea.verify_grant(old["token"], secret=self.SECRET, now=now + 100) is None

    def test_grant_is_bound_to_its_account(self):
        grant = ea.issue_grant(OWNER_ID, secret=self.SECRET)
        assert ea.verify_grant(grant["token"], user_id=OUTSIDER_ID, secret=self.SECRET) is None

    def test_grant_is_bound_to_its_session(self):
        """§6: a grant must not survive an account switch or a new session."""
        grant = ea.issue_grant(OWNER_ID, session_id="session-a", secret=self.SECRET)
        assert ea.verify_grant(grant["token"], session_id="session-b", secret=self.SECRET) is None

    @pytest.mark.parametrize("token", ["", None, "garbage", "no-dot", "a.b", "....", "x." + "y" * 40])
    def test_malformed_tokens_are_rejected(self, token):
        assert ea.verify_grant(token, secret=self.SECRET) is None

    def test_missing_secret_rejects_every_token(self):
        """An unconfigured signing secret must not silently accept an empty HMAC."""
        grant = ea.issue_grant(OWNER_ID, secret=self.SECRET)
        assert ea.verify_grant(grant["token"], secret="") is None

    def test_ttl_is_bounded(self):
        assert ea.DEFAULT_GRANT_TTL_SECONDS <= 3600

    def test_scope_covers_the_mission_systems(self):
        grant = ea.issue_grant(OWNER_ID, secret=self.SECRET)
        scope = set(grant["scope"])
        for system in ("business_os", "marketplace_selling", "advertising", "payments", "insights", "events"):
            assert system in scope


# ---------------------------------------------------------------------------
# The full decision
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_owner_with_correct_passcode_is_granted(self, encoded_hash):
        result = attempt(encoded_hash=encoded_hash)
        assert result["authorized"] is True
        assert result["grant"]
        assert result["reset_failures"] is True
        assert result["expires_at"] > time.time()

    def test_approved_role_holder_with_correct_passcode_is_granted(self, encoded_hash):
        result = attempt(
            user={"user_id": OUTSIDER_ID}, admin_role="internal_supertester", encoded_hash=encoded_hash
        )
        assert result["authorized"] is True

    def test_passcode_alone_is_never_sufficient(self, encoded_hash):
        """§9: the passcode must not be the only condition."""
        result = attempt(user={"user_id": OUTSIDER_ID}, encoded_hash=encoded_hash)
        assert result["authorized"] is False

    def test_identity_alone_is_never_sufficient(self, encoded_hash):
        result = attempt(passcode="00000000", encoded_hash=encoded_hash)
        assert result["authorized"] is False

    def test_disabled_feature_denies_the_owner(self, encoded_hash):
        """The master switch is checked server-side; production ships it off."""
        result = attempt(enabled=False, encoded_hash=encoded_hash)
        assert result["authorized"] is False

    def test_brute_force_never_stumbles_into_a_grant(self, encoded_hash):
        """A thousand wrong codes from the right account yield nothing, and the
        ladder locks the attacker out well before the space is explored."""
        granted = [
            attempt(passcode=str(n).zfill(8), encoded_hash=encoded_hash)
            for n in range(1000)
            if str(n).zfill(8) != TEST_PASSCODE
        ]
        assert not any(r.get("authorized") for r in granted)
        assert all("grant" not in r for r in granted)

    def test_no_result_ever_contains_the_passcode(self, encoded_hash):
        outcomes = [
            attempt(encoded_hash=encoded_hash),
            attempt(passcode="00000000", encoded_hash=encoded_hash),
            attempt(locked_until=time.time() + 60, encoded_hash=encoded_hash),
            attempt(user={"user_id": OUTSIDER_ID}, encoded_hash=encoded_hash),
        ]
        for outcome in outcomes:
            assert TEST_PASSCODE not in json.dumps(outcome)

    def test_source_contains_no_literal_passcode(self):
        """§3: the authorized value must not appear in the repository."""
        import inspect
        import re

        source = inspect.getsource(ea)
        # An 8-digit run in this module would be a candidate literal passcode.
        # (Iteration counts are written with underscores, e.g. 600_000.)
        assert not re.search(r"(?<![\d_])\d{8}(?![\d_])", source)
