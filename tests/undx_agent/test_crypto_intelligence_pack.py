"""What UNDX is allowed to say about a member's crypto activity.

These tests are about *claims*, not plumbing. Each read model composes systems that
already own the data — the alert engine, the portfolio service, the sampled observation
series — so there is nothing here to test about how a holding is fetched. What there is
to test is the labelling: which sentences the return value makes sayable, and which it
makes structurally impossible to say.

Three assertions carry the mission's honesty rules and should be read as the point of
the file:

* a member without Premium is told the capability is locked, not that they have nothing
  (:meth:`locked_is_not_empty_and_not_an_error`);
* a total with nothing priced behind it is unknown, not zero
  (:meth:`an_unpriced_portfolio_reports_no_total_rather_than_zero`);
* a window the series cannot measure is a refusal, not a flat market
  (:meth:`an_unmeasurable_window_is_not_reported_as_no_movement`).

The service seams are patched rather than driven through a schema on purpose. Every
underlying call is already covered by its own suite, and a fixture here would only
re-certify those queries while making the labelling — the part that is new and the part
that can lie — harder to see.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.undx_agent import bootstrap as _bootstrap  # noqa: F401

from services import premium_crypto_access
from services import undx_personal_intelligence_service as personal
from services.crypto_alert_conditions import WINDOW_CHOICES, WINDOWABLE_METRICS
from services.undx_agent_tools import EXECUTORS, _alert_record
from services.undx_capability_registry import REGISTRY, RiskLevel


USER = 4242


def _entitled(value: bool):
    """Patch the one module that decides entitlement, not the read models."""
    return patch.object(premium_crypto_access, "allowed_for_user_id",
                        return_value=value)


class CryptoIntelligenceEntitlement(unittest.TestCase):
    """A member who is not entitled gets a true sentence about their account."""

    def test_locked_is_not_empty_and_not_an_error(self) -> None:
        with _entitled(False):
            portfolio = personal.crypto_portfolio_summary(USER)
            window = personal.crypto_market_window(USER, "BTC")

        for section, result in (("portfolio", portfolio), ("market_window", window)):
            with self.subTest(section=section):
                # Not an error: nothing went wrong, so nothing should send the
                # member to support over a working paywall.
                self.assertIsNotNone(result)
                self.assertEqual(result["reason"], "premium_required")
                self.assertEqual(result["section"], section)
                self.assertIs(result["entitled"], False)
                self.assertEqual(result["capability"],
                                 premium_crypto_access.INTELLIGENCE)
                # ``items`` is present so a caller that iterates facts needs no
                # special case, and empty because there is nothing to show. It is
                # ``entitled`` that says why — a reader that looked only at the
                # list would narrate "you have no holdings", which is a confident
                # false statement about an account that may hold a great deal.
                self.assertEqual(result["items"], [])
                self.assertIn("Premium", result["message"])
                # The lock is about the capability, not about the data.
                self.assertIn("The data exists on this account", result["message"])

    def test_entitlement_is_resolved_through_the_single_authority(self) -> None:
        """No read model may form its own opinion about who is Premium.

        The alert engine, the portfolio service, the HTTP routes and this layer all
        have to reach the same answer for the same account. A second check here
        would be a fourth place for that answer to be decided, and the first place
        it would drift.
        """
        with _entitled(True) as gate:
            with patch("services.portfolio_service.calculate_user_portfolio",
                       return_value={}):
                personal.crypto_portfolio_summary(USER)
        gate.assert_called_once_with(USER, premium_crypto_access.INTELLIGENCE)


class CryptoPortfolioSummaryClaims(unittest.TestCase):

    def _summary(self, portfolio: dict):
        with _entitled(True):
            with patch("services.portfolio_service.calculate_user_portfolio",
                       return_value=portfolio):
                return personal.crypto_portfolio_summary(USER)

    def test_an_unpriced_portfolio_reports_no_total_rather_than_zero(self) -> None:
        """``calculate_user_portfolio`` accumulates from 0.0; a narrator must not.

        On the portfolio screen that zero is read beside the valuation block that
        explains it. Here the number travels alone into a sentence, where "your
        portfolio is worth $0" is a confident falsehood.
        """
        result = self._summary({
            "holdings": [{"id": 1, "symbol": "BTC", "amount": 2.0, "price": None,
                          "value": None, "cost": None, "pnl_value": None,
                          "pnl_percent": None, "priced": False}],
            "total_value": 0.0, "total_cost": 0.0,
            "pnl_value": 0.0, "pnl_percent": 0.0,
            "valuation": {"complete": False, "holdings": 1, "priced": 0,
                          "unpriced": 1, "unpriced_symbols": ["BTC"],
                          "basis_known": 0},
            "warning": "BTC could not be priced.",
        })
        self.assertIsNone(result["total_value"])
        self.assertIsNone(result["total_cost"])
        self.assertIsNone(result["unrealized_pnl_value"])
        self.assertIsNone(result["unrealized_pnl_percent"])
        # The holding is still reported — the member does hold it, and that is a
        # different fact from what it is worth.
        self.assertEqual(result["count"], 1)
        self.assertIs(result["items"][0]["data"]["priced"], False)
        self.assertIsNone(result["items"][0]["data"]["value"])
        # The sentence that makes an incomplete answer readable rather than wrong.
        self.assertEqual(result["valuation_warning"], "BTC could not be priced.")

    def test_a_priced_portfolio_reports_its_totals(self) -> None:
        result = self._summary({
            "holdings": [{"id": 1, "symbol": "BTC", "amount": 2.0, "price": 100.0,
                          "value": 200.0, "cost": 150.0, "pnl_value": 50.0,
                          "pnl_percent": 33.3, "priced": True}],
            "total_value": 200.0, "total_cost": 150.0,
            "pnl_value": 50.0, "pnl_percent": 33.3,
            "valuation": {"complete": True, "holdings": 1, "priced": 1,
                          "unpriced": 0, "unpriced_symbols": [], "basis_known": 1},
            "warning": "",
        })
        self.assertEqual(result["total_value"], 200.0)
        self.assertEqual(result["unrealized_pnl_value"], 50.0)
        self.assertEqual(result["unrealized_pnl_percent"], 33.3)

    def test_profit_is_labelled_unrealized_and_realized_is_unavailable(self) -> None:
        """``portfolio_items`` holds an amount and an average buy price.

        There is no transaction ledger, so there is no record of what was sold or
        at what price. A realized figure could only be invented, and the return
        value must not carry a field a narrator could mistake for one.
        """
        result = self._summary({"holdings": [], "valuation": {}, "warning": ""})
        realized = [key for key in result if "realiz" in key and "unrealiz" not in key]
        self.assertEqual(realized, [], f"realized-looking fields present: {realized}")
        self.assertIn("unrealized_pnl_value", result)
        self.assertIn("average buy price", result["pnl_basis"])
        self.assertIn("must not be stated", result["pnl_basis"])

    def test_a_failing_portfolio_read_is_recorded_as_degraded(self) -> None:
        """A read that failed must not be narrated as an empty portfolio."""
        with _entitled(True):
            with personal.collecting() as degraded:
                with patch("services.portfolio_service.calculate_user_portfolio",
                           side_effect=RuntimeError("boom")):
                    result = personal.crypto_portfolio_summary(USER)
        self.assertIn("portfolio_service.calculate_user_portfolio", degraded)
        self.assertEqual(result["items"], [])


class CryptoMarketWindowClaims(unittest.TestCase):

    def _window(self, reading: dict, span: dict | None = None, **kwargs):
        with _entitled(True):
            with patch("services.market_observations.window_reading",
                       return_value=reading):
                with patch("services.market_observations.coverage",
                           return_value=span or {"available_windows": (15, 30),
                                                 "stale": False, "span_minutes": 40,
                                                 "sample_count": 9}):
                    return personal.crypto_market_window(USER, "BTC", **kwargs)

    def test_an_unmeasurable_window_is_not_reported_as_no_movement(self) -> None:
        """"Not measurable" and "did not move" are opposite statements.

        Only one of them is true, and a zero here would assert the wrong one about
        a market the system simply has not watched long enough.
        """
        result = self._window({
            "ok": False, "symbol": "BTC", "metric": "price", "window_minutes": 60,
            "change_percent": None, "latest": None, "baseline": None,
            "reason": "window_not_covered",
            "message": "BTC has only been sampled for 40 minutes.",
        }, minutes=60)

        self.assertIs(result["ok"], False)
        self.assertEqual(result["reason"], "window_not_covered")
        # Passed through untouched rather than converted to a zero or a shrug.
        self.assertIsNone(result["change_percent"])
        self.assertIn("40 minutes", result["message"])
        # No fact is emitted, so nothing downstream can cite a movement that was
        # never measured.
        self.assertEqual(result["items"], [])
        # A refusal that can be acted on: these are the windows that *are*
        # answerable, which turns "no" into "ask me for 15 minutes instead".
        self.assertEqual(tuple(result["available_windows"]), (15, 30))

    def test_a_measured_window_becomes_one_grounded_fact(self) -> None:
        result = self._window({
            "ok": True, "symbol": "BTC", "metric": "price", "window_minutes": 60,
            "change_percent": -2.5, "latest": 97.5, "latest_at": "2026-08-23T10:00:00Z",
            "baseline": 100.0, "baseline_at": "2026-08-23T09:00:00Z",
            "baseline_age_seconds": 3600, "sample_count": 12,
            "reason": "", "message": "",
        })
        self.assertIs(result["ok"], True)
        self.assertEqual(result["change_percent"], -2.5)
        self.assertEqual(len(result["items"]), 1)
        fact = result["items"][0]
        self.assertEqual(fact["source"], "market_observations")
        self.assertIn("-2.50%", fact["detail"])
        # The window actually measured, which is what copy should quote — the
        # member asked for 60 minutes and the series compared against a reading
        # this many seconds old, and the two are not always the same.
        self.assertEqual(result["baseline_age_seconds"], 3600)

    def test_a_missing_symbol_is_a_refusal_not_a_default_coin(self) -> None:
        with _entitled(True):
            result = personal.crypto_market_window(USER, "")
        self.assertIs(result["ok"], False)
        self.assertEqual(result["reason"], "no_symbol")
        self.assertEqual(result["items"], [])

    def test_a_failing_series_read_is_recorded_as_degraded(self) -> None:
        with _entitled(True):
            with personal.collecting() as degraded:
                with patch("services.market_observations.window_reading",
                           side_effect=RuntimeError("boom")):
                    result = personal.crypto_market_window(USER, "BTC")
        self.assertIn("market_observations.window_reading", degraded)
        self.assertIs(result["ok"], False)
        self.assertEqual(result["reason"], "series_unavailable")


class CryptoIntelligenceRegistration(unittest.TestCase):

    def test_the_registry_can_only_offer_windows_the_series_can_answer(self) -> None:
        """A hardcoded copy would drift, and the drift would be silent.

        The registry would keep offering a window the series refuses, and the
        refusal reads like a statement about the market rather than about our own
        sampling.
        """
        fields = {f.name: f for f in REGISTRY["crypto.market.window"].fields}
        self.assertEqual(tuple(fields["minutes"].choices),
                         tuple(str(w) for w in WINDOW_CHOICES))
        self.assertEqual(tuple(fields["metric"].choices),
                         tuple(sorted(WINDOWABLE_METRICS)))

    def test_every_registered_crypto_read_is_wired_end_to_end(self) -> None:
        for cid in ("crypto.portfolio.summary", "crypto.market.window"):
            with self.subTest(capability=cid):
                spec = REGISTRY[cid]
                self.assertIn(spec.executor, EXECUTORS)
                self.assertTrue(hasattr(personal, spec.executor))
                self.assertEqual(spec.risk, RiskLevel.READ_ONLY)

    def test_there_is_no_second_reader_of_the_alert_rules(self) -> None:
        """Rules are read by ``crypto.alerts.list`` and by nothing else.

        A second projection of the same rows is how "what alerts do i have" ends up
        with two answers that disagree about what a rule watches.
        """
        readers = [cid for cid, spec in REGISTRY.items()
                   if cid.startswith("crypto.alerts.")
                   and spec.risk == RiskLevel.READ_ONLY
                   and "list" in cid]
        self.assertEqual(readers, ["crypto.alerts.list"])
        self.assertNotIn("crypto.alerts.overview", REGISTRY)


class AlertProjectionSeesWhatTheRuleWatches(unittest.TestCase):
    """The canonical reader must not describe a compound rule as a broken basic one."""

    SCOPED_COMPOUND = {
        "id": 7, "symbol": "", "asset_symbol": "", "condition": "price_above",
        "threshold_value": None, "status": "active", "active": 1,
        "condition_summary": "price rises 5% in 60 minutes and volume is above 1B",
        "is_advanced": True, "portfolio_scope": 1, "is_portfolio_rule": True,
        "watchlist_id": None, "is_watchlist_rule": False,
    }

    def test_a_compound_rule_carries_its_rendered_summary(self) -> None:
        record = _alert_record(self.SCOPED_COMPOUND)
        # Carried, never re-derived: ``alert_engine`` renders this once so the web
        # UI, the native UI, the notification copy and this projection cannot
        # describe one rule four different ways.
        self.assertEqual(record["condition_summary"],
                         self.SCOPED_COMPOUND["condition_summary"])
        self.assertIs(record["is_advanced"], True)
        self.assertIs(record["is_portfolio_rule"], True)

    def test_a_scoped_rule_is_not_named_after_a_coin_it_does_not_have(self) -> None:
        """Every scoped rule used to come back called "Crypto alert".

        ``resolve_alert_reference`` is required to find *exactly one* matching rule,
        and an account with several identically-named rules made that impossible.
        """
        record = _alert_record(self.SCOPED_COMPOUND)
        self.assertEqual(record["display_name"], "Portfolio alert")

        watchlist_rule = dict(self.SCOPED_COMPOUND,
                              portfolio_scope=0, is_portfolio_rule=False,
                              watchlist_id=3, is_watchlist_rule=True)
        self.assertEqual(_alert_record(watchlist_rule)["display_name"],
                         "Watchlist alert")

    def test_an_unscoped_rule_reports_no_watchlist_rather_than_an_empty_one(self) -> None:
        record = _alert_record(self.SCOPED_COMPOUND)
        self.assertIsNone(record["watchlist_id"])

    def test_a_members_own_note_still_wins(self) -> None:
        named = dict(self.SCOPED_COMPOUND, metadata={"note": "Rescue plan"})
        self.assertEqual(_alert_record(named)["display_name"], "Rescue plan")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
