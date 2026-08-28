"""Executable evidence that the Stage 6 capabilities act, and only on their own account.

Two questions are being answered here and they need different kinds of proof.

"Can UNDX act?" is answered by driving each capability through the real runtime and
then reading the row back through the *service*, not through the agent's own receipt.
A receipt is the agent's claim about what it did; the service read is the evidence.
Every happy-path assertion below therefore ends at a ``portfolio_service`` or
``pulsesoc_notification_system`` call rather than at ``response.card``.

"Can UNDX act on somebody else?" is answered adversarially. The fixture seeds resources
owned by ``OTHER_ID`` and then asks the runtime, as ``OWNER_ID``, to change them by
id — the exact shape a confused or steered planner would emit. Each of those tests
asserts twice: that the turn refused, and that the victim's row is byte-for-byte what
it was before. The second assertion is the one that matters. A refusal that arrives
after the write has already landed is not a refusal.

The database is a real temporary SQLite file, never a mock, because the isolation
being tested is a property of ``WHERE user_id=?`` — mock it and the test passes for
reasons that have nothing to do with production.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services import portfolio_service  # noqa: E402
from services import pulsesoc_notification_system as notifications  # noqa: E402
from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


# ``portfolio_service`` has no ``ensure_schema`` and no ``CREATE TABLE`` anywhere — it
# has always run against tables built by ``bot.init_db()``. The fixture therefore owns
# these definitions. They are copied from the columns the service's own INSERT and
# SELECT statements name, and the UNIQUE on watchlist rows is what makes the service's
# ``INSERT OR IGNORE`` idempotent rather than duplicating.
_PORTFOLIO_SCHEMA = (
    """CREATE TABLE portfolio_items (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         user_id INTEGER, symbol TEXT, coin_name TEXT,
         amount REAL DEFAULT 0, average_buy_price REAL DEFAULT 0,
         notes TEXT, created_at TEXT, updated_at TEXT
       )""",
    """CREATE TABLE watchlist_items (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         user_id INTEGER, symbol TEXT, coin_name TEXT, created_at TEXT,
         UNIQUE(user_id, symbol)
       )""",
    """CREATE TABLE user_activity (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         user_id INTEGER, event_type TEXT, event_label TEXT,
         metadata TEXT, created_at TEXT
       )""",
)


class Stage6Base(unittest.TestCase):
    """Fixture and drive helpers shared by the action and attack suites."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        for statement in _PORTFOLIO_SCHEMA:
            self.fx.cur.execute(statement)
        self.fx.commit()
        notifications.ensure_schema(self.fx.conn)
        self.fx.commit()

        from services import pulse_ai_service, undx_agent_runtime

        self.svc = pulse_ai_service
        self.runtime = undx_agent_runtime
        bootstrap.stub_bot(pulse_ai_service)
        self.svc.ensure_schema(self.fx.cur, self.fx.conn)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    # -- driving the runtime ----------------------------------------------

    def act(self, capability_id: str, arguments: dict, *, text: str = "",
            user_id: int = OWNER_ID, request_id: str = ""):
        """Run one turn to completion, walking the confirmation gate if one appears.

        Six of these capabilities confirm unconditionally and six confirm contextually,
        so a helper that only handled one shape would silently skip half the pack. The
        approval is spent through ``confirm_action`` — the same endpoint the native
        client calls — rather than by re-driving the executor, so what is exercised is
        the real two-phase path and not a shortcut around it.
        """
        response = self.runtime.handle(
            self.fx.cur, user_id=user_id,
            text=text or f"run {capability_id}",
            capability_id=capability_id, arguments=dict(arguments),
            request_id=request_id or f"stage6-{capability_id}",
        )
        self.fx.commit()
        if response.status != "confirmation_required":
            return response.status, response.card, response
        token = response.card.get("confirmation_token")
        self.assertTrue(token, f"{capability_id} asked to confirm without minting a token")
        result = self.svc.confirm_action(user_id, {"confirmation_token": token})
        self.fx.commit()
        return result.get("status"), result, response

    # -- seeding ----------------------------------------------------------

    def seed_holding(self, user_id: int, symbol: str = "BTC", amount: float = 1.0,
                     price: float = 20000.0) -> int:
        portfolio_service.add_portfolio_item(
            user_id, symbol, coin_name=symbol, amount=amount, average_buy_price=price)
        row = next(row for row in portfolio_service.list_portfolio_items(user_id)
                   if str(row["symbol"]).upper() == symbol.upper())
        return int(row["id"])

    def seed_notification(self, user_id: int, category: str = "system_announcement") -> int:
        self.fx.cur.execute(
            """INSERT INTO notifications
                 (user_id, recipient_user_id, notification_type, type, category,
                  title, message, body, status, created_at)
               VALUES (?, ?, 'system', 'system', ?, 'Seeded', 'Seeded', 'Seeded',
                       'unread', datetime('now'))""",
            (int(user_id), int(user_id), category),
        )
        self.fx.commit()
        return int(self.fx.cur.lastrowid)


class Stage6ActionPack(Stage6Base):
    """UNDX can act. Each assertion lands on a service read, not on a receipt."""

    # -- watchlist --------------------------------------------------------

    def test_watchlist_add_lands_and_verifies(self) -> None:
        status, card, _ = self.act("crypto.watchlist.add", {"symbol": "BTC"})
        self.assertEqual(status, "verified_success", card)
        self.assertIn("BTC", portfolio_service.watchlist_symbols(OWNER_ID))

    def test_watchlist_add_is_idempotent(self) -> None:
        self.act("crypto.watchlist.add", {"symbol": "ETH"}, request_id="wl-1")
        status, card, _ = self.act("crypto.watchlist.add", {"symbol": "ETH"}, request_id="wl-2")
        self.assertEqual(status, "verified_success", card)
        symbols = portfolio_service.watchlist_symbols(OWNER_ID)
        self.assertEqual(list(symbols).count("ETH"), 1)

    def test_watchlist_remove_resolves_symbol_to_own_row(self) -> None:
        self.act("crypto.watchlist.add", {"symbol": "SOL"}, request_id="wl-add")
        status, card, _ = self.act("crypto.watchlist.remove", {"symbol": "SOL"},
                                   request_id="wl-remove")
        self.assertEqual(status, "verified_success", card)
        self.assertNotIn("SOL", portfolio_service.watchlist_symbols(OWNER_ID))

    def test_watchlist_list_reads_back_what_was_written(self) -> None:
        self.act("crypto.watchlist.add", {"symbol": "ADA"}, request_id="wl-ada")
        status, card, _ = self.act("crypto.watchlist.list", {})
        self.assertIn(status, {"answered", "verified_success"}, card)
        self.assertIn("ADA", [r["symbol"] for r in card["records"]])

    # -- portfolio --------------------------------------------------------

    def test_holding_add_stores_amount_and_cost_basis(self) -> None:
        status, card, _ = self.act(
            "crypto.portfolio.holding.add",
            {"symbol": "BTC", "amount": 2.5, "average_buy_price": 31000.0})
        self.assertEqual(status, "verified_success", card)
        rows = portfolio_service.list_portfolio_items(OWNER_ID)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "BTC")
        self.assertAlmostEqual(float(rows[0]["amount"]), 2.5)
        self.assertAlmostEqual(float(rows[0]["average_buy_price"]), 31000.0)

    def test_holding_update_changes_only_the_named_fields(self) -> None:
        item_id = self.seed_holding(OWNER_ID, "ETH", amount=4.0, price=1800.0)
        status, card, _ = self.act("crypto.portfolio.holding.update",
                                   {"item_id": item_id, "amount": 6.0})
        self.assertEqual(status, "verified_success", card)
        row = portfolio_service.get_portfolio_item(OWNER_ID, item_id)
        self.assertAlmostEqual(float(row["amount"]), 6.0)
        # The cost basis was not named, so it must not have moved. An update that
        # quietly resets unnamed columns would still verify against the field it was
        # asked about, which is exactly the failure ``verified_fields`` exists to catch.
        self.assertAlmostEqual(float(row["average_buy_price"]), 1800.0)

    def test_holding_delete_removes_the_row(self) -> None:
        item_id = self.seed_holding(OWNER_ID, "SOL", amount=10.0)
        status, card, _ = self.act("crypto.portfolio.holding.delete", {"item_id": item_id})
        self.assertEqual(status, "verified_success", card)
        self.assertIsNone(portfolio_service.get_portfolio_item(OWNER_ID, item_id))

    def test_holdings_list_returns_own_rows_only(self) -> None:
        self.seed_holding(OWNER_ID, "BTC")
        self.seed_holding(OTHER_ID, "DOGE")
        status, card, _ = self.act("crypto.portfolio.holdings.list", {})
        self.assertIn(status, {"answered", "verified_success"}, card)
        symbols = {r["symbol"] for r in card["records"]}
        self.assertEqual(symbols, {"BTC"})

    # -- notifications ----------------------------------------------------

    def test_mark_read_flips_one_notification(self) -> None:
        notification_id = self.seed_notification(OWNER_ID)
        status, card, _ = self.act("notifications.mark_read",
                                   {"notification_id": notification_id})
        self.assertEqual(status, "verified_success", card)
        row = notifications.get_notification(OWNER_ID, notification_id)
        self.assertTrue(row["read"])

    def test_mark_all_read_clears_the_account_and_not_the_neighbour(self) -> None:
        mine = self.seed_notification(OWNER_ID)
        theirs = self.seed_notification(OTHER_ID)
        status, card, _ = self.act("notifications.mark_all_read", {"category": "global"})
        self.assertEqual(status, "verified_success", card)
        self.assertTrue(notifications.get_notification(OWNER_ID, mine)["read"])
        self.assertFalse(notifications.get_notification(OTHER_ID, theirs)["read"])

    # -- presence, localization, settings ---------------------------------

    def test_presence_privacy_update_lands(self) -> None:
        from services import presence_service

        status, card, _ = self.act("presence.privacy.update",
                                   {"setting": "hide_last_seen", "enabled": True})
        self.assertEqual(status, "verified_success", card)
        stored = presence_service.get_privacy(self.fx.cur, OWNER_ID)
        self.assertTrue(stored["hide_last_seen"])

    def test_region_preference_update_lands(self) -> None:
        from services.pulse_region_preferences import get_preferences

        status, card, _ = self.act("localization.region.update",
                                   {"setting": "currency", "value": "EUR"})
        self.assertEqual(status, "verified_success", card)
        # The capability's argument is ``currency``; the service reads it back as
        # ``preferred_currency``. The asymmetry is real and the verifier has to bridge
        # it — this assertion is written against the service's own vocabulary so it
        # would still fail if the verifier were bridging to the wrong column.
        self.assertEqual(str(get_preferences(OWNER_ID)["preferred_currency"]).upper(), "EUR")

    def test_region_update_verifies_rather_than_degrading(self) -> None:
        """Regression: every settable region preference must be readable back.

        ``get_preferences`` prefixes its keys, so a verifier that looked them up by the
        argument name found nothing and returned ``impossible_to_verify`` — a receipt
        that says "I changed it but could not check" for a change that is trivially
        checkable. That is the failure mode this whole verification layer exists to
        prevent, so all four settings are exercised, not just the one that broke.
        """
        for setting, value in (("currency", "GBP"), ("locale", "en-GB"),
                               ("time_zone", "Europe/London"), ("date_format", "dmy")):
            with self.subTest(setting=setting):
                status, card, _ = self.act(
                    "localization.region.update", {"setting": setting, "value": value},
                    request_id=f"region-{setting}")
                self.assertEqual(status, "verified_success", card)
                self.assertNotEqual(card.get("verification_state"), "impossible_to_verify")

    def test_translation_preference_update_lands(self) -> None:
        from services.content_translation import get_preference

        status, card, _ = self.act("localization.translation.update",
                                   {"target_language": "fr", "policy": "always"})
        self.assertEqual(status, "verified_success", card)
        # The policy is stored per language pair, so the read has to name the same
        # pair the write named. Reading the default ``en`` row would report ``ask``
        # and look like the write had failed.
        self.assertEqual(get_preference(OWNER_ID, "auto", "fr")["policy"], "always")
        self.assertEqual(get_preference(OWNER_ID, "auto", "de")["policy"], "ask")

    def test_privacy_audience_update_lands_in_the_settings_document(self) -> None:
        from services.pulse_settings_routes import load_preferences

        status, card, _ = self.act("settings.privacy.audience.update",
                                   {"setting": "storyAudience", "audience": "followers"})
        self.assertEqual(status, "verified_success", card)
        stored, _revision, _ = load_preferences(self.fx.cur, OWNER_ID)
        self.assertEqual(stored["privacy"]["storyAudience"], "followers")

    def test_theme_update_lands_in_the_settings_document(self) -> None:
        from services.pulse_settings_routes import load_preferences

        status, card, _ = self.act("settings.appearance.theme.update", {"theme": "dark"})
        self.assertEqual(status, "verified_success", card)
        stored, _revision, _ = load_preferences(self.fx.cur, OWNER_ID)
        self.assertEqual(stored["appearance"]["theme"], "dark")


class Stage6CrossAccountAttacks(Stage6Base):
    """The mission's hard line: never modify another user's private resources.

    Every test here names a real resource id belonging to ``OTHER_ID`` and asks the
    runtime, authenticated as ``OWNER_ID``, to change it. The id is real, so nothing
    is being caught by a validity check — the only thing standing between the request
    and the row is ownership.
    """

    def assert_refused(self, status, card) -> None:
        self.assertNotEqual(status, "verified_success", card)
        blob = str(card).lower()
        self.assertNotIn("holding updated", blob)
        self.assertNotIn("holding deleted", blob)

    def test_holding_update_cannot_reach_another_account(self) -> None:
        victim = self.seed_holding(OTHER_ID, "BTC", amount=3.0, price=25000.0)
        status, card, _ = self.act("crypto.portfolio.holding.update",
                                   {"item_id": victim, "amount": 999.0})
        self.assert_refused(status, card)
        row = portfolio_service.get_portfolio_item(OTHER_ID, victim)
        self.assertAlmostEqual(float(row["amount"]), 3.0)
        self.assertAlmostEqual(float(row["average_buy_price"]), 25000.0)

    def test_holding_delete_cannot_reach_another_account(self) -> None:
        victim = self.seed_holding(OTHER_ID, "ETH", amount=8.0)
        status, card, _ = self.act("crypto.portfolio.holding.delete", {"item_id": victim})
        self.assert_refused(status, card)
        self.assertIsNotNone(portfolio_service.get_portfolio_item(OTHER_ID, victim))

    def test_foreign_holding_is_indistinguishable_from_a_missing_one(self) -> None:
        """A refusal that leaks existence is a membership oracle.

        If "belongs to someone else" and "does not exist" produced different messages,
        this capability would let one account enumerate another's rows one id at a
        time without ever writing anything.
        """
        victim = self.seed_holding(OTHER_ID, "SOL", amount=1.0)
        _, foreign_card, _ = self.act("crypto.portfolio.holding.delete",
                                      {"item_id": victim}, request_id="attack-foreign")
        _, absent_card, _ = self.act("crypto.portfolio.holding.delete",
                                     {"item_id": victim + 4242}, request_id="attack-absent")
        self.assertEqual(str(foreign_card.get("message") or foreign_card.get("reply") or ""),
                         str(absent_card.get("message") or absent_card.get("reply") or ""))

    def test_mark_read_cannot_reach_another_account(self) -> None:
        victim = self.seed_notification(OTHER_ID)
        status, card, _ = self.act("notifications.mark_read", {"notification_id": victim})
        self.assert_refused(status, card)
        self.assertFalse(notifications.get_notification(OTHER_ID, victim)["read"])

    def test_watchlist_remove_cannot_reach_another_account(self) -> None:
        """The symbol-not-id design is what makes this safe, so it is tested directly.

        ``OTHER_ID`` holds DOGE and ``OWNER_ID`` does not. Asking to remove DOGE is a
        legitimate-looking request that would delete the neighbour's row if the symbol
        were resolved against the table rather than against the caller's own rows.
        """
        portfolio_service.add_watchlist_item(OTHER_ID, "DOGE")
        status, card, _ = self.act("crypto.watchlist.remove", {"symbol": "DOGE"})
        self.assert_refused(status, card)
        self.assertIn("DOGE", portfolio_service.watchlist_symbols(OTHER_ID))

    def test_writes_land_on_the_caller_even_when_the_argument_names_someone_else(self) -> None:
        """An injected ``user_id`` must be inert, not authoritative.

        The gateway refuses self-scoped capabilities that *declare* an actor-naming
        field, so no planner can legitimately emit one. This asserts the other half:
        that an argument smuggled in anyway changes nothing about whose row moves.
        """
        status, card, _ = self.act(
            "crypto.watchlist.add",
            {"symbol": "LINK", "user_id": OTHER_ID, "on_behalf_of": OTHER_ID})
        self.assertIn("LINK", portfolio_service.watchlist_symbols(OWNER_ID), card)
        self.assertNotIn("LINK", portfolio_service.watchlist_symbols(OTHER_ID))
        self.assertEqual(status, "verified_success", card)


class Stage6StructuralGuards(unittest.TestCase):
    """Invariants that hold before any row exists, so they are checked without a database."""

    NEW_CAPABILITIES = (
        "crypto.watchlist.list", "crypto.watchlist.add", "crypto.watchlist.remove",
        "crypto.portfolio.holdings.list", "crypto.portfolio.holding.add",
        "crypto.portfolio.holding.update", "crypto.portfolio.holding.delete",
        "notifications.mark_read", "notifications.mark_all_read",
        "presence.privacy.update", "localization.region.update",
        "localization.translation.update", "settings.privacy.audience.update",
        "settings.appearance.theme.update",
    )

    def test_no_new_capability_declares_an_actor_naming_field(self) -> None:
        from services.undx_capability_registry import REGISTRY
        from services.undx_tool_gateway import _ACTOR_NAMING_FIELDS

        for capability_id in self.NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            with self.subTest(capability=capability_id):
                self.assertEqual(str(spec.permission), "self_account_only")
                named = {f.name for f in spec.fields} & set(_ACTOR_NAMING_FIELDS)
                self.assertEqual(named, set(),
                                 f"{capability_id} would let an argument choose the account")

    def test_security_settings_are_unreachable_by_construction(self) -> None:
        """Two-factor and biometric unlock live in ``security``. No capability may go there."""
        from services.undx_agent_tools import SETTINGS_WRITABLE_GROUPS

        self.assertNotIn("security", SETTINGS_WRITABLE_GROUPS)
        self.assertEqual(SETTINGS_WRITABLE_GROUPS, frozenset({"appearance", "privacy"}))

    def test_settings_patch_refuses_a_group_outside_the_allowlist(self) -> None:
        """The enum guards the argument; this guards the call. Both have to hold."""
        import time

        from services.undx_agent_tools import _settings_patch

        result = _settings_patch(OWNER_ID, "security", {"two_factor": False},
                                 capability="settings.appearance.theme.update",
                                 started=time.perf_counter())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "group_not_writable")

    def test_every_new_write_declares_a_verifier_and_a_target(self) -> None:
        from services.undx_capability_registry import REGISTRY
        from services.undx_verification import VERIFIERS

        for capability_id in self.NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            if str(spec.risk) == "read_only":
                continue
            with self.subTest(capability=capability_id):
                self.assertTrue(spec.target_field, "a write with no subject cannot be confirmed")
                self.assertIn(spec.verifier, VERIFIERS,
                              "a write whose verifier is not registered reports its own claim")

    def test_consequential_writes_always_confirm(self) -> None:
        from services.undx_capability_registry import REGISTRY

        for capability_id in self.NEW_CAPABILITIES:
            spec = REGISTRY[capability_id]
            if str(spec.risk) != "consequential_write":
                continue
            with self.subTest(capability=capability_id):
                self.assertEqual(str(spec.confirmation), "always")

    def test_every_new_capability_is_in_the_production_tool_ledger(self) -> None:
        """Absent from the ledger, the gateway raises ``tool_not_registered`` deep enough
        that the transport turns a governed action into chit-chat."""
        from services.undx_capability_registry import REGISTRY
        from services.undx_policy import PRODUCTION_TOOL_REGISTRY

        for capability_id in self.NEW_CAPABILITIES:
            with self.subTest(capability=capability_id):
                self.assertIn(REGISTRY[capability_id].tool_name, PRODUCTION_TOOL_REGISTRY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
