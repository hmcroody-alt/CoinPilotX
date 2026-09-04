"""Premium hard-lock at DELIVERY time — alerts and briefings.

Stdlib-only (unittest):

    PYTHONPATH=. python3 -m unittest tests/crypto_premium/test_premium_delivery_stop.py

The mission's expiry contract: when premium/trial lapses, alert delivery STOPS
and briefing generation STOPS — but nothing is deleted. Rules and preferences
stay exactly as configured so renewal resumes both with zero re-setup. These
tests pin the worker-side gates (the API-side gates are covered by
``test_crypto_premium_gate.py``) and their fail-closed behaviour.
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from unittest import mock

logging.getLogger().setLevel(logging.CRITICAL)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services import alert_engine  # noqa: E402
from services.pulse_briefings import engine as briefings  # noqa: E402

_UID = 616161

_RULE = {
    "id": 9001,
    "user_id": _UID,
    "status": "active",
    "asset_symbol": "BTC",
    "rule_type": "price_above",
    "target_value": 1,
}


class AlertDeliveryStopsOnLapse(unittest.TestCase):
    def test_lapsed_owner_rule_is_not_evaluated_or_dispatched(self):
        with mock.patch.object(alert_engine, "_advanced_alerts_capability",
                               return_value=False) as cap, \
             mock.patch.object(alert_engine, "_mark_checked") as marked, \
             mock.patch.object(alert_engine, "_dispatch_alert_notification",
                               create=True) as dispatched:
            result = alert_engine.evaluate_alert_rule(dict(_RULE))
        cap.assert_called_once_with(_UID)
        self.assertTrue(result["ok"])
        self.assertFalse(result["triggered"])
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("status"), "premium_required")
        # The rule is marked checked (observable status) — NOT deleted or
        # deactivated; evaluate_alert_rule has no delete path and this test
        # asserts nothing was dispatched either.
        marked.assert_called_once()
        dispatched.assert_not_called()

    def test_capability_check_fails_closed(self):
        # Resolver outage → no capability → no delivery (never fail open).
        with mock.patch.object(alert_engine, "crypto_premium_gate", create=True):
            with mock.patch(
                "services.crypto_premium_gate.has_crypto_capability",
                side_effect=RuntimeError("resolver down")):
                self.assertFalse(alert_engine._advanced_alerts_capability(_UID))

    def test_inactive_rule_short_circuits_before_the_premium_gate(self):
        # Paused rules answer "not active" without spending a resolver call.
        with mock.patch.object(alert_engine, "_advanced_alerts_capability") as cap:
            result = alert_engine.evaluate_alert_rule({**_RULE, "status": "paused"})
        self.assertFalse(result["triggered"])
        cap.assert_not_called()


class BriefingGenerationStopsOnLapse(unittest.TestCase):
    def _prefs(self):
        return {"enabled": True, "frequency": "daily"}

    def test_lapsed_member_gets_premium_required_before_claim(self):
        conn = mock.MagicMock()
        with mock.patch.object(briefings, "get_preferences",
                               return_value=self._prefs()), \
             mock.patch.object(briefings, "_premium_briefings_allowed",
                               return_value=False) as gate:
            result = briefings.evaluate_user_briefing(conn, {"user_id": _UID})
        self.assertEqual(result, {"status": "premium_required"})
        gate.assert_called_once_with(_UID)
        # Returns BEFORE the CLAIM stage: nothing written to the connection.
        conn.commit.assert_not_called()

    def test_disabled_prefs_short_circuit_first(self):
        conn = mock.MagicMock()
        with mock.patch.object(briefings, "get_preferences",
                               return_value={"enabled": False, "frequency": "off"}), \
             mock.patch.object(briefings, "_premium_briefings_allowed") as gate:
            result = briefings.evaluate_user_briefing(conn, {"user_id": _UID})
        self.assertEqual(result, {"status": "disabled"})
        gate.assert_not_called()

    def test_gate_resolves_through_the_one_crypto_gate(self):
        with mock.patch("services.crypto_premium_gate.has_crypto_capability",
                        return_value=True) as cap:
            self.assertTrue(briefings._premium_briefings_allowed(_UID))
        from services import crypto_premium_gate as gate
        cap.assert_called_once_with(_UID, gate.CAP_CRYPTO_INTELLIGENCE)

    def test_gate_fails_closed_on_resolver_error(self):
        with mock.patch("services.crypto_premium_gate.has_crypto_capability",
                        side_effect=RuntimeError("resolver down")):
            self.assertFalse(briefings._premium_briefings_allowed(_UID))


if __name__ == "__main__":
    unittest.main()
