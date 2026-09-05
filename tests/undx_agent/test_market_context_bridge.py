"""Market Pulse → UNDX live crypto context bridge (mission test matrix).

What these tests claim, and why each claim needs a real component behind it:

* The envelope is **validated, not trusted** — a symbol that is not plain
  ``[A-Z0-9]`` never reaches provider matching or model grounding.
* A fresh envelope **replaces** the stored one (opening Solana after asking
  about Ethereum must not leave two assets fighting over "it"), while a turn
  with no envelope **preserves** it, so context survives ordinary follow-ups.
* Coreference: an explicit mention always beats the screen context; deixis
  ("it", "this coin") falls back to the context; an expired context steers
  nothing.
* Grounding is **present-or-honest**: a crypto turn gets a grounding block even
  when the live layer is down — carrying the unavailability — so the model can
  disclose instead of inventing a price or misrouting into the company-metric
  refusal. A non-crypto turn gets no block and pays nothing.
* Context grants **READ, never WRITE**: overlays are recomputed owner-scoped at
  answer time, write capabilities keep their declared confirmation, and a
  context-filled write target is flagged ``agent_chose_target``.

Live provider calls are stubbed at this module's own seams (``_board_assets``,
``quote``, ``market_pulse.asset_history``) because these tests assert routing
and governance, not CoinGecko's uptime. The owner-scoping tests run against the
real ``alert_engine`` SQL on the harness database — a mocked engine would
happily "scope" anything.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.undx_agent.harness import OTHER_ID, OWNER_ID, AgentFixture

BOARD = [
    {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "price": 90000.0},
    {"id": "ethereum", "symbol": "ETH", "name": "Ethereum", "price": 3200.0},
    {"id": "solana", "symbol": "SOL", "name": "Solana", "price": 150.0},
]


def envelope(symbol: str = "ETH", **overrides):
    base = {
        "source": "asset_detail",
        "context_type": "asset_focus",
        "asset": {"id": symbol.lower(), "symbol": symbol, "name": symbol.title(), "rank": 2},
        "market_snapshot": {"price": 3200.5, "change24h": -1.2, "marketCap": 4.1e11,
                            "volume24h": 1.9e10, "observedAt": "2026-08-31T00:00:00Z",
                            "source": "coingecko", "stale": False},
        "chart": {"selected_range": "24H"},
        "related_market": {"totalMarketCap": 3.2e12, "btcDominance": 52.1},
        "user_overlay": {"watchlisted": True, "alert_count": 1},
    }
    base.update(overrides)
    return base


def aged(context: dict, seconds: int) -> dict:
    stamped = dict(context)
    stamped["attached_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat(timespec="seconds")
    return stamped


class EnvelopeSanitization(unittest.TestCase):
    """The client is trusted for which screen, never for what is true."""

    def _module(self):
        from services import undx_market_context

        return undx_market_context

    def test_valid_envelope_is_normalised(self):
        ctx = self._module().sanitize_market_context(envelope())
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["asset"]["symbol"], "ETH")
        self.assertEqual(ctx["source"], "asset_detail")
        self.assertEqual(ctx["chart"]["selected_range"], "24H")
        self.assertEqual(ctx["market_snapshot"]["price"], 3200.5)

    def test_rejects_missing_or_hostile_asset_identity(self):
        m = self._module()
        self.assertIsNone(m.sanitize_market_context(None))
        self.assertIsNone(m.sanitize_market_context("ETH"))
        self.assertIsNone(m.sanitize_market_context({"asset": None}))
        self.assertIsNone(m.sanitize_market_context({"asset": {"symbol": ""}}))
        # About to be echoed into grounding — injection shapes are rejected outright.
        self.assertIsNone(m.sanitize_market_context(
            {"asset": {"symbol": "ETH; DROP TABLE users"}}))
        self.assertIsNone(m.sanitize_market_context(
            {"asset": {"symbol": "<script>"}}))

    def test_snapshot_numbers_are_coerced_never_passed_through(self):
        ctx = self._module().sanitize_market_context(
            envelope(market_snapshot={"price": "not a number", "change24h": "2.5"}))
        self.assertIsNone(ctx["market_snapshot"]["price"])
        self.assertEqual(ctx["market_snapshot"]["change24h"], 2.5)

    def test_unknown_source_and_range_fall_to_safe_defaults(self):
        ctx = self._module().sanitize_market_context(
            envelope(source="evil_screen", chart={"selected_range": "17Q"}))
        self.assertEqual(ctx["source"], "market_pulse")
        self.assertEqual(ctx["chart"]["selected_range"], "24H")

    def test_asset_id_is_resolved_here_not_accepted_from_the_client(self):
        """The screens have no canonical id to send, so the board supplies it.

        The mobile quote shape carries a symbol and a name and no id, so the
        best a client could offer is ``symbol.lower()`` — "btc", which is not
        what the price layer calls Bitcoin. Resolving against the same board
        that answers for prices makes the id deterministic and consistent with
        the thing that will later be asked for one.
        """
        m = self._module()
        with mock.patch.object(m, "_board_assets", return_value=BOARD):
            ctx = m.sanitize_market_context(envelope("BTC"))
            self.assertEqual(ctx["asset"]["id"], "bitcoin")
            self.assertEqual(ctx["asset"]["symbol"], "BTC")
            # A client claiming otherwise does not get to rename the asset.
            liar = m.sanitize_market_context(
                envelope("BTC", asset={"symbol": "BTC", "name": "Bitcoin", "id": "ethereum"}))
            self.assertEqual(liar["asset"]["id"], "bitcoin")

    def test_identity_does_not_depend_on_the_board_window(self):
        """A major outside the top-N display window still gets its provider id.

        The board is bounded (``market_rows("all", 80)``), so "listed right
        now" and "canonical" are different properties. ZEC is deliberately
        absent from the BOARD fixture: if identity leaned only on the board,
        this would come back "zec" — a lowercased ticker labelled as an id,
        which is exactly the collision the id exists to prevent.
        """
        m = self._module()
        with mock.patch.object(m, "_board_assets", return_value=BOARD):
            asset = m.sanitize_market_context(envelope("ZEC"))["asset"]
        self.assertEqual(asset["id"], "zcash")
        self.assertEqual(asset["id_resolution"], "canonical")

    def test_unlisted_symbol_keeps_a_usable_id_but_is_marked_unresolved(self):
        # symbol.lower() remains a workable lookup key, but it is never
        # presented as canonical — downstream must be able to tell "bitcoin"
        # from a guess shaped like an id.
        m = self._module()
        with mock.patch.object(m, "_board_assets", return_value=BOARD):
            asset = m.sanitize_market_context(envelope("XYZ"))["asset"]
        self.assertEqual(asset["id"], "xyz")
        self.assertEqual(asset["id_resolution"], "unresolved")

    def test_a_claimed_id_the_server_cannot_verify_is_recorded_as_a_claim(self):
        m = self._module()
        with mock.patch.object(m, "_board_assets", return_value=BOARD):
            asset = m.sanitize_market_context(
                envelope("XYZ", asset={"symbol": "XYZ", "id": "xyz-network"}))["asset"]
        self.assertEqual(asset["id"], "xyz-network")
        self.assertEqual(asset["id_resolution"], "claimed")

    def test_board_outage_does_not_degrade_a_major_asset_identity(self):
        # The majors table is static, so "the board is down" must not turn
        # Bitcoin into "btc" — that id would 404 on every /coins/{id} call.
        m = self._module()
        with mock.patch.object(m.market_pulse, "market_rows", side_effect=RuntimeError("down")):
            asset = m.sanitize_market_context(envelope("BTC"))["asset"]
        self.assertEqual(asset["id"], "bitcoin")
        self.assertEqual(asset["id_resolution"], "canonical")

    def test_board_outage_degrades_an_unknown_symbol_honestly(self):
        m = self._module()
        with mock.patch.object(m.market_pulse, "market_rows", side_effect=RuntimeError("down")):
            asset = m.sanitize_market_context(envelope("XYZ"))["asset"]
        self.assertEqual(asset["id"], "xyz")
        self.assertEqual(asset["id_resolution"], "unresolved")

    def test_coinbase_fallback_ticker_ids_are_not_canonised(self):
        # During Coinbase fallback the board's id field carries a lowercased
        # *ticker*. For a non-major that id equals symbol.lower() and must not
        # be labelled canonical; it is indistinguishable from a guess.
        m = self._module()
        fallback_board = [{"id": "xyz", "symbol": "XYZ", "name": "XYZ Coin"}]
        with mock.patch.object(m, "_board_assets", return_value=fallback_board):
            asset = m.sanitize_market_context(envelope("XYZ"))["asset"]
        self.assertEqual(asset["id"], "xyz")
        self.assertEqual(asset["id_resolution"], "unresolved")


class EnvelopePersistence(unittest.TestCase):
    """Replacement on a new asset, survival across plain turns, honest expiry."""

    def setUp(self):
        self.fx = AgentFixture().start()
        self.addCleanup(self.fx.stop)
        from services import undx_market_context

        self.m = undx_market_context

    def test_fresh_envelope_replaces_stored_one(self):
        stored = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=60)
        incoming = self.m.sanitize_market_context(envelope("SOL"))
        _, active = self.m.merge_for_persist({}, incoming, stored)
        self.assertEqual(active["asset"]["symbol"], "SOL")

    def test_plain_turn_preserves_stored_context(self):
        stored = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=60)
        combined, active = self.m.merge_for_persist({"screen": "chat"}, None, stored)
        self.assertEqual(active["asset"]["symbol"], "ETH")
        self.assertEqual(combined[self.m.CONTEXT_KEY]["asset"]["symbol"], "ETH")
        self.assertEqual(combined["screen"], "chat")

    def test_dismissal_drops_the_stored_context_not_just_this_turn(self):
        """The chip is state, so dismissing it has to reach the state.

        Before the clear rode the request, tapping X removed the words from the
        screen and left this side still resolving "how is it doing?" to the coin
        the member had just said they were finished with — a chip that was
        decoration rather than a control.
        """
        stored = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=60)
        combined, active = self.m.merge_for_persist({"screen": "chat"}, None, stored, cleared=True)
        self.assertIsNone(active)
        self.assertNotIn(self.m.CONTEXT_KEY, combined)
        # The rest of the hint context is untouched: a dismissal ends a subject,
        # it is not a licence to reset anything else about the conversation.
        self.assertEqual(combined["screen"], "chat")

    def test_a_dismissal_that_arrives_with_a_new_asset_is_a_replacement(self):
        # Leaving Ethereum's screen for Solana's can produce both signals on one
        # send. The member's newest intent is the new topic, not an empty one.
        stored = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=60)
        incoming = self.m.sanitize_market_context(envelope("SOL"))
        _, active = self.m.merge_for_persist({}, incoming, stored, cleared=True)
        self.assertEqual(active["asset"]["symbol"], "SOL")

    def test_dismissal_with_nothing_stored_is_a_harmless_no_op(self):
        combined, active = self.m.merge_for_persist({}, None, None, cleared=True)
        self.assertIsNone(active)
        self.assertNotIn(self.m.CONTEXT_KEY, combined)

    def test_expired_stored_context_is_dropped_not_reused(self):
        stored = aged(self.m.sanitize_market_context(envelope("ETH")),
                      seconds=self.m.CONTEXT_TTL_SECONDS + 60)
        combined, active = self.m.merge_for_persist({}, None, stored)
        self.assertIsNone(active)
        self.assertNotIn(self.m.CONTEXT_KEY, combined)

    def test_load_stored_round_trip_and_garbage_tolerance(self):
        ctx = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=5)
        self.fx.cur.execute(
            "INSERT INTO pulse_ai_client_contexts (user_id, conversation_id, context_json, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (OWNER_ID, 1, json.dumps({self.m.CONTEXT_KEY: ctx})),
        )
        self.fx.commit()
        loaded = self.m.load_stored(self.fx.cur, OWNER_ID, 1)
        self.assertEqual(loaded["asset"]["symbol"], "ETH")
        # Another conversation, corrupt JSON: colour, never a crash.
        self.fx.cur.execute(
            "INSERT INTO pulse_ai_client_contexts (user_id, conversation_id, context_json, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))", (OWNER_ID, 2, "{not json"))
        self.fx.commit()
        self.assertIsNone(self.m.load_stored(self.fx.cur, OWNER_ID, 2))
        self.assertIsNone(self.m.load_stored(self.fx.cur, OTHER_ID, 1))


class Coreference(unittest.TestCase):
    """Explicit mention wins; deixis leans on the envelope; expiry ends it."""

    def setUp(self):
        from services import undx_market_context

        self.m = undx_market_context
        patcher = mock.patch.object(self.m, "_board_assets", return_value=BOARD)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.eth = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=30)

    def test_explicit_name_beats_screen_context(self):
        got = self.m.resolve_asset("how is solana doing today?", self.eth)
        self.assertEqual((got["symbol"], got["via"]), ("SOL", "explicit"))

    def test_explicit_symbol_beats_screen_context(self):
        got = self.m.resolve_asset("compare BTC to gold", self.eth)
        self.assertEqual((got["symbol"], got["via"]), ("BTC", "explicit"))

    def test_deixis_resolves_to_the_viewed_asset(self):
        for text in ("what's it at right now?", "is this coin overvalued?",
                     "why did the chart dip?"):
            got = self.m.resolve_asset(text, self.eth)
            self.assertEqual((got["symbol"], got["via"]), ("ETH", "context"), text)

    def test_expired_context_steers_nothing(self):
        old = aged(self.eth, seconds=self.m.CONTEXT_TTL_SECONDS + 60)
        self.assertIsNone(self.m.resolve_asset("what's it at right now?", old))

    def test_no_context_and_no_mention_resolves_nothing(self):
        self.assertIsNone(self.m.resolve_asset("what's it at right now?", None))

    def test_range_words_win_then_context_then_default(self):
        self.assertEqual(self.m.resolve_range("and over 30 days?", self.eth), "1M")
        self.assertEqual(self.m.resolve_range("what about this week", None), "7D")
        week = dict(self.eth, chart={"selected_range": "7D"})
        self.assertEqual(self.m.resolve_range("what was the high?", week), "7D")
        self.assertEqual(self.m.resolve_range("what was the high?", None), "24H")


def grant_crypto_premium(case: unittest.TestCase) -> None:
    """Hold ``premium.crypto.intelligence`` open for the duration of one test.

    The crypto executors and ``grounding_block`` are gated on the canonical
    resolver, and that gate fails CLOSED — correctly. But this fixture's SQLite
    schema has no ``premium_entitlements`` table, so the resolver raises, the
    gate reads the raise as "not entitled", and every test below refuses before
    reaching the behaviour it was written to check.

    What is under test in this file is identity resolution, coreference and
    grounding — never entitlement. The entitlement axis has its own exhaustive
    home in ``tests/crypto_premium/test_premium_expiry_crypto_lock.py``, which
    walks all fourteen states against these same executors. Pinning the gate
    open here keeps the two concerns from silently standing in for each other:
    a regression in the lock fails there loudly, instead of turning this file
    green for the wrong reason.
    """
    from services import crypto_premium_gate

    patcher = mock.patch.object(crypto_premium_gate, "has_crypto_capability",
                                return_value=True)
    patcher.start()
    case.addCleanup(patcher.stop)


class Grounding(unittest.TestCase):
    """Grounded or honestly absent — never fabricated, never the company-metric path."""

    def setUp(self):
        self.fx = AgentFixture().start()
        self.addCleanup(self.fx.stop)
        grant_crypto_premium(self)
        from services import undx_market_context

        self.m = undx_market_context
        for name, value in (("_board_assets", mock.Mock(return_value=BOARD)),
                            ("quote", mock.Mock(return_value={
                                "symbol": "ETH", "price": 3201.0,
                                "freshness": {"stale": False, "as_of": "now"}}))):
            patcher = mock.patch.object(self.m, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        history = mock.patch.object(self.m, "history_pack", return_value={
            "ok": True, "symbol": "ETH", "range": "24H", "start": 3100.0, "end": 3201.0,
            "high": 3250.0, "low": 3080.0, "changePct": 3.26, "points": 288,
            "source": "coingecko", "stale": False})
        history.start()
        self.addCleanup(history.stop)
        self.eth = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=30)

    def _body(self, block):
        self.assertIsNotNone(block)
        self.assertEqual(block["category"], "crypto_market")
        return json.loads(block["body"])

    def test_crypto_turn_with_context_is_grounded(self):
        payload = self._body(self.m.grounding_block(OWNER_ID, "what's it at right now?", self.eth))
        self.assertEqual(payload["asset"]["symbol"], "ETH")
        self.assertEqual(payload["asset"]["resolved_via"], "context")
        self.assertEqual(payload["asset"]["live_quote"]["price"], 3201.0)
        self.assertEqual(payload["viewing"]["asset"]["symbol"], "ETH")
        # READ context must say so: the instructions deny write authority in words.
        self.assertIn("no permission", payload["instructions"])
        self.assertIn("disclose", payload["instructions"])

    def test_non_crypto_turn_gets_no_block(self):
        self.assertIsNone(self.m.grounding_block(
            OWNER_ID, "summarise my meeting notes from friday", None))

    def test_live_layer_down_grounds_the_unavailability(self):
        self.m.quote.return_value = None
        payload = self._body(self.m.grounding_block(OWNER_ID, "price of ETH?", self.eth))
        self.assertFalse(payload["asset"]["live_quote"]["available"])
        self.assertIn("unavailable", payload["asset"]["live_quote"]["note"])

    def test_stale_snapshot_is_withheld_but_live_quote_remains(self):
        old = aged(self.eth, seconds=self.m.SNAPSHOT_TTL_SECONDS + 60)
        payload = self._body(self.m.grounding_block(OWNER_ID, "what's it at?", old))
        self.assertIsNone(payload["viewing"]["screen_snapshot"])
        self.assertEqual(payload["asset"]["live_quote"]["price"], 3201.0)

    def test_history_facts_attach_for_chart_questions(self):
        payload = self._body(self.m.grounding_block(
            OWNER_ID, "what were the high and low today?", self.eth))
        self.assertEqual(payload["history"]["high"], 3250.0)
        self.assertEqual(payload["history"]["range"], "24H")

    def test_overlay_is_recomputed_owner_scoped(self):
        self.fx.make_alert(OWNER_ID, symbol="ETH", condition="above", threshold=4000.0)
        owner = self._body(self.m.grounding_block(OWNER_ID, "do I have alerts on it?", self.eth))
        other = self._body(self.m.grounding_block(OTHER_ID, "do I have alerts on it?", self.eth))
        self.assertEqual(owner["your_account"]["alert_count"], 1)
        self.assertEqual(other["your_account"]["alert_count"], 0)

    def test_overlay_absent_without_account_words(self):
        payload = self._body(self.m.grounding_block(OWNER_ID, "what's it at?", self.eth))
        self.assertNotIn("your_account", payload)

    def test_telemetry_carries_no_user_text_or_account_data(self):
        block = self.m.grounding_block(OWNER_ID, "what's it at right now?", self.eth)
        emitted = self.m.telemetry(self.eth, block)
        self.assertEqual(set(emitted), {"context_attached", "context_source",
                                        "context_symbol", "context_age_seconds", "grounded"})
        self.assertTrue(emitted["context_attached"])
        self.assertTrue(emitted["grounded"])
        self.assertEqual(emitted["context_symbol"], "ETH")


class HistoryFactPack(unittest.TestCase):
    """A chart becomes facts the series actually asserts, or an honest warning."""

    def setUp(self):
        from services import undx_market_context

        self.m = undx_market_context

    def _with_series(self, series):
        return mock.patch.object(self.m.market_pulse, "asset_history", return_value=series)

    def test_pack_summarises_rather_than_dumping_points(self):
        points = [{"price": p} for p in (100.0, 120.0, 90.0, 110.0)]
        with self._with_series({"ok": True, "points": points, "source": "coingecko", "stale": False}):
            pack = self.m.history_pack("ETH", "24H")
        self.assertEqual((pack["start"], pack["end"], pack["high"], pack["low"]),
                         (100.0, 110.0, 120.0, 90.0))
        self.assertEqual(pack["changePct"], 10.0)
        self.assertEqual(pack["points"], 4)
        self.assertNotIn("points_raw", pack)

    def test_too_few_points_is_a_warning_not_a_guess(self):
        with self._with_series({"ok": True, "points": [{"price": 100.0}]}):
            pack = self.m.history_pack("ETH", "7D")
        self.assertFalse(pack["ok"])
        self.assertIn("unavailable", pack["warning"].lower())

    def test_provider_exception_degrades_to_warning(self):
        with mock.patch.object(self.m.market_pulse, "asset_history",
                               side_effect=RuntimeError("provider down")):
            pack = self.m.history_pack("ETH", "24H")
        self.assertFalse(pack["ok"])


class AgentExecutors(unittest.TestCase):
    """The governed agent path: context fills the target, never invents one."""

    def setUp(self):
        self.fx = AgentFixture().start()
        self.addCleanup(self.fx.stop)
        grant_crypto_premium(self)
        from services import undx_agent_tools, undx_market_context

        self.tools = undx_agent_tools
        self.m = undx_market_context
        self.eth = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=30)

    def _with_context(self, context):
        return mock.patch.object(self.m, "active_context_for_user", return_value=context)

    def _with_quote(self, record):
        return mock.patch.object(self.m, "quote", return_value=record)

    def test_no_symbol_and_no_context_asks_never_defaults(self):
        with self._with_context(None):
            result = self.tools.crypto_market_quote(OWNER_ID, {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "missing_arguments")
        self.assertIn("which coin", result.error_message)
        self.assertNotIn("BTC", result.error_message.replace('"price of BTC"', ""))

    def test_context_fills_the_symbol_and_says_so(self):
        record = {"symbol": "ETH", "price": 3201.0, "freshness": {"stale": False}}
        with self._with_context(self.eth), self._with_quote(record):
            result = self.tools.crypto_market_quote(OWNER_ID, {})
        self.assertTrue(result.ok)
        self.assertEqual(result.canonical_resource_id, "asset:ETH")
        self.assertEqual(result.data["resolved_via"], "context")

    def test_explicit_argument_beats_context(self):
        record = {"symbol": "SOL", "price": 150.0, "freshness": {}}
        with self._with_context(self.eth), self._with_quote(record):
            result = self.tools.crypto_market_quote(OWNER_ID, {"symbol": "sol"})
        self.assertTrue(result.ok)
        self.assertEqual(result.canonical_resource_id, "asset:SOL")
        self.assertEqual(result.data["resolved_via"], "argument")

    def test_unknown_asset_is_not_found_not_fabricated(self):
        with self._with_context(None), self._with_quote(None):
            result = self.tools.crypto_market_quote(OWNER_ID, {"symbol": "NOCOIN"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "resource_not_found")

    def test_compare_rejects_comparing_an_asset_to_itself(self):
        with self._with_context(self.eth):
            result = self.tools.crypto_market_compare(OWNER_ID, {"versus": "ETH"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "invalid_arguments")

    def test_history_unavailable_is_retryable_honesty(self):
        with self._with_context(self.eth), \
                mock.patch.object(self.m, "history_pack",
                                  return_value={"ok": False, "warning": "History for this range is unavailable right now."}):
            result = self.tools.crypto_market_history(OWNER_ID, {"range": "7D"})
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_code, "history_unavailable")


class RuntimeContextFallback(unittest.TestCase):
    """resolve_arguments: context supplies knowledge of the target, never authority."""

    def setUp(self):
        self.fx = AgentFixture().start()
        self.addCleanup(self.fx.stop)
        from services import undx_agent_runtime, undx_market_context
        from services.undx_capability_registry import REGISTRY

        self.runtime = undx_agent_runtime
        self.m = undx_market_context
        self.registry = REGISTRY
        self.eth = aged(self.m.sanitize_market_context(envelope("ETH")), seconds=30)

    def _with_context(self, context):
        return mock.patch.object(self.m, "active_context_for_user", return_value=context)

    def test_read_capability_inherits_the_viewed_symbol(self):
        with self._with_context(self.eth):
            got = self.runtime.resolve_arguments(
                OWNER_ID, self.registry["crypto.market.quote"],
                "what is it trading at right now?", {})
        self.assertEqual(got.arguments.get("symbol"), "ETH")
        self.assertFalse(got.agent_chose_target)

    def test_explicit_argument_is_never_overwritten(self):
        with self._with_context(self.eth):
            got = self.runtime.resolve_arguments(
                OWNER_ID, self.registry["crypto.market.quote"],
                "price please", {"symbol": "SOL"})
        self.assertEqual(got.arguments["symbol"], "SOL")

    def test_context_filled_write_target_is_flagged_agent_chosen(self):
        with self._with_context(self.eth):
            got = self.runtime.resolve_arguments(
                OWNER_ID, self.registry["crypto.alerts.create"],
                "alert me when it goes above 4000", {})
        self.assertEqual(got.arguments.get("symbol"), "ETH")
        self.assertTrue(got.agent_chose_target)

    def test_alerts_list_is_never_narrowed_by_context(self):
        # "Show my alerts" while viewing Ethereum means *all* alerts. The
        # capability is deliberately excluded from context symbol fill.
        self.assertNotIn("crypto.alerts.list",
                         self.runtime._MARKET_CONTEXT_SYMBOL_CAPABILITIES)
        with self._with_context(self.eth):
            got = self.runtime.resolve_arguments(
                OWNER_ID, self.registry["crypto.alerts.list"], "show my alerts", {})
        self.assertNotIn("symbol", got.arguments)

    def test_no_context_leaves_the_field_missing_for_an_honest_askback(self):
        with self._with_context(None):
            got = self.runtime.resolve_arguments(
                OWNER_ID, self.registry["crypto.market.quote"],
                "what is it trading at right now?", {})
        self.assertFalse(got.arguments.get("symbol"))


class RegistryGovernance(unittest.TestCase):
    """The four market reads are declared, read-only, and audit-registered."""

    CAPABILITIES = ("crypto.market.quote", "crypto.market.history",
                    "crypto.market.compare", "crypto.market.overview")

    def test_specs_are_read_only_and_never_confirm(self):
        from services.undx_capability_registry import REGISTRY
        from services.undx_agent_contracts import ConfirmationPolicy, RiskLevel

        for cid in self.CAPABILITIES:
            spec = REGISTRY.get(cid)
            self.assertIsNotNone(spec, cid)
            self.assertEqual(spec.risk, RiskLevel.READ_ONLY, cid)
            self.assertEqual(spec.confirmation, ConfirmationPolicy.NEVER, cid)
            self.assertFalse(spec.is_write, cid)
            self.assertEqual(spec.audit_category, "crypto_market_read", cid)
            self.assertEqual(spec.native_route, "/pulse/crypto", cid)

    def test_tools_are_in_the_production_registry_as_in_process_reads(self):
        from services.undx_capability_registry import REGISTRY
        from services.undx_policy import PRODUCTION_TOOL_REGISTRY

        for cid in self.CAPABILITIES:
            entry = PRODUCTION_TOOL_REGISTRY.get(REGISTRY[cid].tool_name)
            self.assertIsNotNone(entry, cid)
            self.assertIsNone(entry.get("method"), cid)
            self.assertEqual(entry.get("risk"), "read_only", cid)
            self.assertFalse(entry.get("confirmation"), cid)

    def test_every_executor_exists_and_follows_the_convention(self):
        from services import undx_agent_tools
        from services.undx_capability_registry import REGISTRY

        for cid in self.CAPABILITIES:
            executor = getattr(undx_agent_tools, REGISTRY[cid].executor, None)
            self.assertTrue(callable(executor), cid)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
