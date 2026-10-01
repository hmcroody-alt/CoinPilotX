"""Premium 7-day trial — eligibility, idempotency, and clock-based expiry.

Stdlib-only (unittest + sqlite3 via services.db): runs without flask/stripe/pytest.

    PYTHONPATH=. python3 -m unittest tests/crypto_premium/test_premium_trial_lock.py

Covers the mission's trial invariants end-to-end over a fresh sqlite DB:

* one trial per account, EVER — any prior trial grant row (including revoked)
  makes the account permanently ineligible (durable, account-keyed abuse check);
* a replayed signup can never EXTEND the trial (idempotent grant, unchanged
  ``expires_at``);
* prospective-only — ``is_new_signup`` must be asserted by the caller;
* server-clock expiry with no cleanup job — the same grant rows stop answering
  "yes" at T+7d because every canonical read compares ``expires_at`` to now;
* granting fails CLOSED (storage errors → no trial, signup unbroken);
* the legacy ``_trial_window_open`` time-bounds legacy trial statuses, failing
  closed on missing/unparseable end dates.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

logging.getLogger("business_os.entitlements.trial").setLevel(logging.CRITICAL)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_UID = 515151

_ENV_KEYS = ("BUSINESS_OS_ENTITLEMENTS", "PULSESOC_OWNER_USER_IDS", "DATABASE_URL")


class _EnvIsolatedCase(unittest.TestCase):
    """Save/restore the env flags the entitlement stack reads per call."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FreshDbCase(_EnvIsolatedCase):
    """Fresh sqlite DB per test class run (services.db uses ./coinpilotx.db
    when no DATABASE_URL is set, so chdir into a tempdir isolates it)."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tmp.name)
        from services.business_os.entitlements import schema
        schema.ensure_ready()

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()
        super().tearDown()


class ProspectiveOnlyAndInputValidation(_EnvIsolatedCase):
    """The two refusals that happen before any storage is touched."""

    def test_not_new_signup_is_refused_without_touching_storage(self):
        from services.business_os.entitlements import trial
        with mock.patch.object(trial, "has_ever_had_trial") as never_called:
            result = trial.start_trial_if_eligible(_UID, is_new_signup=False)
        self.assertEqual(result, {"started": False, "reason": "not_new_signup",
                                  "trial_end": None})
        never_called.assert_not_called()

    def test_default_is_not_new_signup(self):
        # The prospective-only rule must be opt-in at the call site.
        from services.business_os.entitlements import trial
        result = trial.start_trial_if_eligible(_UID)
        self.assertEqual(result["reason"], "not_new_signup")

    def test_invalid_subject_is_refused(self):
        from services.business_os.entitlements import trial
        for bad in ("abc", None, "", object()):
            result = trial.start_trial_if_eligible(bad, is_new_signup=True)
            self.assertFalse(result["started"])
            self.assertEqual(result["reason"], "invalid_subject")


class TrialLifecycleEndToEnd(_FreshDbCase):
    """Grant → entitled → replay refused → revocation still counts → expiry."""

    def _grants(self):
        from services import db
        conn = db.connect()
        try:
            cur = conn.execute(
                "SELECT entitlement_key, status, expires_at FROM business_os_ent_grants "
                "WHERE subject_type='user' AND subject_id=? AND source='trial' "
                "ORDER BY entitlement_key",
                (str(_UID),))
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def test_full_lifecycle(self):
        from services import db
        from services import crypto_premium_gate as gate
        from services.business_os.entitlements import trial

        # --- T+0: a genuinely new signup starts the trial -------------------
        before = datetime.now(timezone.utc)
        result = trial.start_trial_if_eligible(
            _UID, source_reference=f"signup:{_UID}", is_new_signup=True)
        after = datetime.now(timezone.utc)

        self.assertTrue(result["started"])
        self.assertEqual(result["reason"], "started")
        end = datetime.fromisoformat(result["trial_end"])
        # Server clock, exactly TRIAL_DAYS out (bounded by call duration).
        self.assertGreaterEqual(end, before + timedelta(days=trial.TRIAL_DAYS))
        self.assertLessEqual(end, after + timedelta(days=trial.TRIAL_DAYS))

        first_grants = self._grants()
        self.assertTrue(first_grants, "trial must write canonical grant rows")
        self.assertTrue(all(g["expires_at"] for g in first_grants),
                        "every trial grant must be time-bounded")

        # The grant is effective through the canonical gate (the same path the
        # API hard locks use), not just present as rows.
        os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
        self.assertTrue(
            gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_ADVANCED_ALERTS))
        self.assertTrue(
            gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_PORTFOLIO))

        # --- Replay: refused, and the trial is NOT extended -----------------
        replay = trial.start_trial_if_eligible(
            _UID, source_reference=f"signup:{_UID}", is_new_signup=True)
        self.assertFalse(replay["started"])
        self.assertEqual(replay["reason"], "already_used")
        self.assertEqual(self._grants(), first_grants,
                         "a replayed signup must not touch the grant rows")

        # --- Revocation does not reset eligibility (durable abuse check) ----
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE business_os_ent_grants SET status='revoked' "
                "WHERE subject_type='user' AND subject_id=? AND source='trial'",
                (str(_UID),))
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(trial.has_ever_had_trial(_UID))
        again = trial.start_trial_if_eligible(_UID, is_new_signup=True)
        self.assertEqual(again["reason"], "already_used")

        # --- T+7d: the clock, not a job, ends access ------------------------
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE business_os_ent_grants "
                "SET status='active', expires_at=? "
                "WHERE subject_type='user' AND subject_id=? AND source='trial'",
                (past, str(_UID)))
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(
            gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_ADVANCED_ALERTS),
            "expired trial grants must stop answering yes with no cleanup job")
        self.assertFalse(
            gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_PORTFOLIO))
        # ...and the rows still EXIST: expiry is not deletion, and the used
        # trial still blocks a second one.
        self.assertTrue(self._grants())
        self.assertEqual(
            trial.start_trial_if_eligible(_UID, is_new_signup=True)["reason"],
            "already_used")


class FailClosedPaths(_FreshDbCase):
    """Storage trouble must never grant and never raise into signup."""

    def test_grant_failure_reports_and_grants_nothing(self):
        from services.business_os.entitlements import trial
        with mock.patch.object(trial._svc, "sync_subscription_entitlements",
                               side_effect=RuntimeError("db down")):
            result = trial.start_trial_if_eligible(_UID, is_new_signup=True)
        self.assertEqual(result, {"started": False, "reason": "grant_failed",
                                  "trial_end": None})
        self.assertFalse(trial.has_ever_had_trial(_UID))

    def test_unprovable_eligibility_grants_nothing(self):
        from services.business_os.entitlements import trial
        with mock.patch.object(trial, "has_ever_had_trial",
                               side_effect=RuntimeError("db down")):
            result = trial.start_trial_if_eligible(_UID, is_new_signup=True)
        self.assertFalse(result["started"])
        self.assertEqual(result["reason"], "eligibility_unknown")


class LegacyTrialUsedSignal(_FreshDbCase):
    """The legacy ``users.trial_used`` reader fails to 'used' whenever it
    cannot prove otherwise."""

    def _make_users(self, rows):
        from services import db
        conn = db.connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS users "
                "(user_id INTEGER PRIMARY KEY, trial_used INTEGER)")
            for uid, used in rows:
                conn.execute("INSERT INTO users (user_id, trial_used) VALUES (?, ?)",
                             (uid, used))
            conn.commit()
        finally:
            conn.close()

    def test_reads_the_flag_and_fails_to_used(self):
        from services.business_os.entitlements import trial
        self._make_users([(_UID, 1), (_UID + 1, 0)])
        self.assertTrue(trial._legacy_trial_used(_UID))
        self.assertFalse(trial._legacy_trial_used(_UID + 1))
        # Unknown user → not eligible.
        self.assertTrue(trial._legacy_trial_used(_UID + 99))

    def test_missing_table_fails_to_used(self):
        from services.business_os.entitlements import trial
        self.assertTrue(trial._legacy_trial_used(_UID))


class LegacyTrialWindowTimeBounding(_EnvIsolatedCase):
    """``_trial_window_open``: legacy trial statuses only count while the
    recorded window is open; no parseable end date → closed (an unbounded
    trial is exactly the bug this check removes)."""

    @staticmethod
    def _svc():
        from services import premium_entitlement_service as svc
        return svc

    def test_open_window(self):
        future = (datetime.now() + timedelta(days=3)).isoformat()
        self.assertTrue(self._svc()._trial_window_open({"trial_end_date": future}))

    def test_closed_at_t_plus_7d(self):
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        self.assertFalse(self._svc()._trial_window_open({"trial_end_date": past}))

    def test_falls_back_to_pro_expires_at(self):
        future = (datetime.now() + timedelta(days=1)).isoformat()
        self.assertTrue(self._svc()._trial_window_open(
            {"trial_end_date": "", "pro_expires_at": future}))

    def test_fails_closed_without_a_parseable_date(self):
        svc = self._svc()
        self.assertFalse(svc._trial_window_open({}))
        self.assertFalse(svc._trial_window_open({"trial_end_date": None}))
        self.assertFalse(svc._trial_window_open({"trial_end_date": "not-a-date"}))
        self.assertFalse(svc._trial_window_open(
            {"trial_end_date": "garbage", "pro_expires_at": "also-garbage"}))

    def test_timezone_aware_end_dates(self):
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.assertTrue(self._svc()._trial_window_open({"trial_end_date": future}))
        self.assertFalse(self._svc()._trial_window_open({"trial_end_date": past}))


if __name__ == "__main__":
    unittest.main()
