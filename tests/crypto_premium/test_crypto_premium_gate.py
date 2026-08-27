"""Crypto premium gate — canonical-registry alignment + fail-closed invariants.

Stdlib-only (unittest + sqlite3 via services.db): runs without flask/stripe/pytest.

    PYTHONPATH=. python3 -m unittest tests/crypto_premium/test_crypto_premium_gate.py

Covers:
* the gate's capability constants match the canonical registry
  (``services.business_os.entitlements.premium.PREMIUM_CAPABILITIES``) and the
  seed catalog attaches them to the EXISTING premium plans, so the existing
  Apple SKUs (com.pulsesoc.premium.monthly / .annual) inherit them;
* deny-on-ImportError and deny-on-any-resolution-failure (never fail open);
* the exact ``premium_required_response`` payload shape;
* the positive grant path via a stubbed ``facade.check``;
* the existing PULSESOC_OWNER_USER_IDS allowlist is respected (no second
  bypass mechanism);
* end-to-end over a fresh sqlite DB: projecting an Apple-sourced
  ``pulse_premium_monthly`` subscription grants both crypto capabilities.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

# The deny-closed tests intentionally trigger the gate's exception logging;
# keep the test run output clean.
logging.getLogger("crypto_premium_gate").setLevel(logging.CRITICAL)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services import crypto_premium_gate as gate  # noqa: E402

_UID = 424242

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


class CapabilityConstantsMatchRegistry(unittest.TestCase):
    def test_constants_are_registered_premium_capabilities(self):
        from services.business_os.entitlements import facade, premium
        self.assertIn(gate.CAP_CRYPTO_ADVANCED_ALERTS, premium.PREMIUM_CAPABILITIES)
        self.assertEqual(gate.CAP_CRYPTO_ADVANCED_ALERTS, "premium.crypto.advanced_alerts")
        self.assertEqual(gate.CAP_CRYPTO_PORTFOLIO, "premium.crypto.portfolio_intelligence")
        # PREMIUM_CAPABILITIES is the *presentation* list, and the portfolio key
        # is an alias for the portfolio/intelligence pair already advertised
        # there — listing it would sell one capability twice under two names.
        # What the gate actually needs is a legacy reader, because without one
        # the default ``off`` mode has no opinion and denies the key to every
        # paying member.
        self.assertNotIn(gate.CAP_CRYPTO_PORTFOLIO, premium.PREMIUM_CAPABILITIES)
        self.assertIn(gate.CAP_CRYPTO_PORTFOLIO, facade._LEGACY_READERS)

    def test_existing_premium_plans_confer_both_capabilities(self):
        """The seed catalog must attach the new keys to the EXISTING plans the
        Apple SKUs map to — that is what makes monthly/annual purchases inherit
        them with zero other changes (no new product, no new SKU)."""
        from services.business_os.entitlements import schema
        catalog = {(p, k) for (p, k, _lv, _lp) in schema._SEED_CATALOG}
        for plan in ("pulse_premium_monthly", "pulse_premium_annual",
                     "pulse_premium_grandfathered"):
            self.assertIn((plan, gate.CAP_CRYPTO_ADVANCED_ALERTS), catalog, plan)
            self.assertIn((plan, gate.CAP_CRYPTO_PORTFOLIO), catalog, plan)

    def test_apple_skus_map_to_those_plans(self):
        """Static source check (iap_apple imports ``cryptography``, which is not
        importable in the stdlib-only sandbox): the existing Apple product ids
        must still map onto the plans that now carry the crypto capabilities."""
        path = os.path.join(_REPO_ROOT, "services", "business_os",
                            "entitlements", "iap_apple.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertRegex(src, re.compile(
            r'"com\.pulsesoc\.premium\.monthly"\s*:\s*"pulse_premium_monthly"'))
        self.assertRegex(src, re.compile(
            r'"com\.pulsesoc\.premium\.annual"\s*:\s*"pulse_premium_annual"'))

    def test_registry_module_declares_no_new_plans_or_products(self):
        """Capabilities were attached to the existing premium tier: the plan
        list in the canonical resolver is unchanged."""
        from services.business_os.entitlements import premium
        self.assertEqual(premium.PREMIUM_PLAN_KEYS, (
            "pulse_premium_monthly",
            "pulse_premium_annual",
            "pulse_premium_trial",
            "pulse_premium_grandfathered",
            "pulse_business_monthly",
        ))


class DenyClosed(_EnvIsolatedCase):
    def test_import_error_denies(self):
        """If the entitlement stack cannot even be imported, the gate DENIES."""
        poisoned = {
            "services.business_os": None,
            "services.business_os.entitlements": None,
            "services.business_os.entitlements.facade": None,
        }
        with mock.patch.dict(sys.modules, poisoned):
            self.assertFalse(
                gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_ADVANCED_ALERTS))
            self.assertFalse(
                gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_PORTFOLIO))

    def test_resolution_failure_denies(self):
        with mock.patch("services.business_os.entitlements.facade.check",
                        side_effect=RuntimeError("db exploded")):
            self.assertFalse(
                gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_PORTFOLIO))

    def test_unknown_capability_denies_even_when_entitled(self):
        with mock.patch("services.business_os.entitlements.facade.check",
                        return_value=True):
            self.assertFalse(gate.has_crypto_capability(_UID, "crypto.everything"))
            self.assertFalse(gate.has_crypto_capability(_UID, ""))
            self.assertFalse(gate.has_crypto_capability(_UID, None))

    def test_invalid_user_ids_deny(self):
        with mock.patch("services.business_os.entitlements.facade.check",
                        return_value=True):
            for bad in (None, "", "abc", 0, -5):
                self.assertFalse(
                    gate.has_crypto_capability(bad, gate.CAP_CRYPTO_PORTFOLIO),
                    bad)


class PositiveGrantPath(_EnvIsolatedCase):
    def test_stubbed_entitlement_grants(self):
        with mock.patch("services.business_os.entitlements.facade.check",
                        return_value=True) as chk:
            self.assertTrue(
                gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_ADVANCED_ALERTS))
            self.assertTrue(
                gate.has_crypto_capability(str(_UID), gate.CAP_CRYPTO_PORTFOLIO))
        chk.assert_any_call(_UID, gate.CAP_CRYPTO_ADVANCED_ALERTS)
        chk.assert_any_call(_UID, gate.CAP_CRYPTO_PORTFOLIO)

    def test_stubbed_entitlement_denies(self):
        with mock.patch("services.business_os.entitlements.facade.check",
                        return_value=False):
            self.assertFalse(
                gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_ADVANCED_ALERTS))

    def test_owner_allowlist_is_respected(self):
        """The EXISTING PULSESOC_OWNER_USER_IDS bypass applies; no second
        mechanism (the gate delegates to premium_identity_engine.is_owner)."""
        os.environ["PULSESOC_OWNER_USER_IDS"] = f" {_UID} , junk"
        with mock.patch("services.business_os.entitlements.facade.check",
                        return_value=False):
            self.assertTrue(
                gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_PORTFOLIO))
            # A non-allowlisted user still resolves through entitlements.
            self.assertFalse(
                gate.has_crypto_capability(_UID + 1, gate.CAP_CRYPTO_PORTFOLIO))


class PremiumRequiredResponseShape(unittest.TestCase):
    def test_exact_shape(self):
        for cap in (gate.CAP_CRYPTO_ADVANCED_ALERTS, gate.CAP_CRYPTO_PORTFOLIO):
            body = gate.premium_required_response(cap)
            self.assertEqual(
                set(body.keys()), {"ok", "code", "capability", "message"})
            self.assertIs(body["ok"], False)
            self.assertEqual(body["code"], "premium_required")
            self.assertEqual(body["capability"], cap)
            self.assertIsInstance(body["message"], str)
            self.assertTrue(body["message"].strip())

    def test_unknown_capability_still_well_formed(self):
        body = gate.premium_required_response("crypto.unknown")
        self.assertEqual(body["code"], "premium_required")
        self.assertEqual(body["capability"], "crypto.unknown")
        self.assertTrue(body["message"])


class ApplePlanInheritanceEndToEnd(_EnvIsolatedCase):
    """Fresh sqlite DB: projecting an Apple-sourced monthly subscription must
    grant BOTH crypto capabilities through the canonical path — proving the
    existing SKUs inherit the new capabilities with no extra grant calls."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tmp.name)  # services.db uses ./coinpilotx.db when no DATABASE_URL

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()
        super().tearDown()

    def test_monthly_plan_projection_grants_crypto_capabilities(self):
        from services.business_os.entitlements import schema, service
        schema.ensure_ready()
        service.sync_subscription_entitlements(
            _UID, "pulse_premium_monthly",
            source="apple_app_store", source_reference="txn-e2e-1")
        os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
        self.assertTrue(
            gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_ADVANCED_ALERTS))
        self.assertTrue(
            gate.has_crypto_capability(_UID, gate.CAP_CRYPTO_PORTFOLIO))
        # A user with no subscription stays denied.
        self.assertFalse(
            gate.has_crypto_capability(_UID + 1, gate.CAP_CRYPTO_ADVANCED_ALERTS))


if __name__ == "__main__":
    unittest.main()
