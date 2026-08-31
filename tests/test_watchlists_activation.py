"""Acceptance tests for the Watchlists workspace.

Watchlists were a working *storage* feature behind a dead screen: rows could be
created and assets appended, but nothing renamed a list, nothing deleted one,
the same asset could be added to a list ten times, and no read path ever joined
a stored symbol to a price. This proves the activated surface.

Two properties get the most attention here because they are the ones that are
expensive to be wrong about:

  * **Isolation.** Every mutation is authorized by the same predicate that
    scopes the read (`WHERE ... AND user_id = ?`). The tests assert on the
    *outcome for the victim* — B's list still exists, still has its assets,
    still has its name — rather than only on the exception A sees, because a
    handler that raises after having already written is still a breach.

  * **Honesty.** A missing price is None everywhere, never 0.0 and never a
    stale value presented as current. The market provider is stubbed rather
    than reached, so "no data" is a state the tests can actually enter; against
    a live CoinGecko this file would pass for the wrong reason on a good day
    and fail for the wrong reason on a bad one.

Deleting a watchlist deliberately does *not* delete alerts. A user who stops
tracking BTC on a list has not asked to stop being told when it crosses
$70,000; membership and standing alert rules are separate things they set up
separately.

Run directly (no pytest required):

    python tests/test_watchlists_activation.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_watchlists_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
# No provider may be reachable from a test run.
for _key in (
    "COINGECKO_API_KEY", "WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY",
    "FCM_SERVER_KEY", "APNS_TEAM_ID", "BREVO_API_KEY", "TELEGRAM_BOT_TOKEN",
):
    os.environ.pop(_key, None)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

from services import alert_engine  # noqa: E402
from services import db as db_service  # noqa: E402
from services import market_data as market_data_service  # noqa: E402
from services import dashboard_crypto_command_center as cc  # noqa: E402


USER_A = 9101
USER_B = 9102


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _pin_database_to_this_module():
    """Point the schema bootstrappers at *this* module's database, per test.

    Two pieces of process-global state have to be corrected together, and only
    under pytest — a direct run of this file never hits either.

    `db.connect()` re-reads `DATABASE_URL` on every call, and the sibling alert
    suites set it to their own temp files from inside their tests. An
    import-time assignment here therefore only holds until the first of those
    runs.

    The subtler one: `alert_engine.ensure_alert_schema` and the command centre's
    table bootstrap each guard on a module-level "already done" boolean that is
    not keyed by database. That is correct in production, where there is exactly
    one database for the life of the process. Across two temp databases in one
    test session it means whichever suite ran first marked the schema ready, so
    the tables are never created in the second — `no such table: alert_rules`
    raised by the very call that is supposed to ensure its own schema.

    Both failures hide well. The command centre creates its tables on demand, so
    the watchlist half keeps working; and `_live_alert_rules` degrades a failed
    alert lookup to `[]`, so the visible symptom is "the alert I just created is
    not on the asset" — which reads like a product bug rather than a harness one.
    """
    os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
    alert_engine._ALERT_SCHEMA_READY = False
    cc._TABLES_READY = False
    yield


def _connect():
    return db_service.connect()


def _ensure_support_schema():
    """Stand up the non-watchlist tables the alert engine reads on create.

    `create_alert_rule` runs a channel-readiness check that counts rows in
    `users`, and `list_alert_rules` attaches the latest delivery status from
    `notification_delivery_logs`. Both tables belong to `bot.init_db()`, which
    cannot be imported here — it builds the Flask app and pulls in stripe.

    Minimal equivalents are enough, but they have to be present: `_live_alert_rules`
    deliberately degrades a failing alert lookup to an empty list so a broken
    alert subsystem cannot take down the watchlist screen. That safety net also
    means a missing test table looks exactly like "this user has no alerts",
    which would let the alert-integration tests pass while asserting nothing.
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, email TEXT, telegram_chat_id TEXT, phone_number TEXT)"
        )
        for user_id in (USER_A, USER_B):
            cur.execute(
                "INSERT OR REPLACE INTO users (user_id, email) VALUES (?, ?)",
                (user_id, f"watchlists-{user_id}@example.test"),
            )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                alert_rule_id INTEGER,
                channel TEXT,
                status TEXT,
                error_message TEXT,
                created_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# At import, so the schema is present under pytest and under a direct run alike.
_ensure_support_schema()


#: A believable provider board. BTC/ETH/SOL are priced; ZZZ never appears, so
#: it is the asset used to test the "provider has never heard of this" path.
FAKE_BOARD = {
    "source": "coingecko",
    "updated_at": "2026-08-22T16:00:00",
    "warning": None,
    "markets": [
        {
            "id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "image": "",
            "price": 66834.21, "volume_24h": 24_000_000_000.0, "change_24h": 2.35,
            "market_cap": 1_310_000_000_000.0, "market_cap_rank": 1,
            "circulating_supply": 19_700_000.0, "max_supply": 21_000_000.0,
            "price_change_24h": 1534.02, "sparkline": [65000.0, 65500.0, 66834.21],
        },
        {
            "id": "ethereum", "name": "Ethereum", "symbol": "ETH", "image": "",
            "price": 3287.65, "volume_24h": 12_000_000_000.0, "change_24h": 1.28,
            "market_cap": 395_000_000_000.0, "market_cap_rank": 2,
            "circulating_supply": 120_000_000.0, "max_supply": None,
            "price_change_24h": 41.6, "sparkline": [3200.0, 3250.0, 3287.65],
        },
        {
            "id": "solana", "name": "Solana", "symbol": "SOL", "image": "",
            "price": 152.44, "volume_24h": 3_000_000_000.0, "change_24h": -3.10,
            "market_cap": 70_000_000_000.0, "market_cap_rank": 5,
            "circulating_supply": 460_000_000.0, "max_supply": None,
            "price_change_24h": -4.87, "sparkline": [158.0, 154.0, 152.44],
        },
    ],
    "summary": {"market_trend": "neutral", "risk_level": "Normal", "average_change_24h": 0.18},
}

DEAD_BOARD = {
    "source": "unavailable",
    "updated_at": "2026-08-22T16:00:00",
    "warning": "Market data temporarily unavailable.",
    "markets": [],
    "summary": {"market_trend": "neutral", "risk_level": "Medium", "fallback": True},
}


class _Board:
    """Swap the canonical provider for a fixed board for the duration of a test."""

    def __init__(self, board):
        self.board = board

    def __enter__(self):
        self._original = cc.market_board
        cc.market_board = lambda category="top_volume", limit=50: dict(self.board)
        return self

    def __exit__(self, *exc):
        cc.market_board = self._original
        return False


def _fresh_watchlist(user_id, name="My Crypto"):
    conn = _connect()
    try:
        return int(cc.create_watchlist(conn, user_id, {"name": name})["watchlist_id"])
    finally:
        conn.close()


def _list_names(user_id):
    conn = _connect()
    try:
        return [w["name"] for w in cc.list_watchlists(conn, user_id)]
    finally:
        conn.close()


def _assets(user_id, watchlist_id):
    conn = _connect()
    try:
        for watchlist in cc.list_watchlists(conn, user_id):
            if int(watchlist["id"]) == int(watchlist_id):
                return watchlist.get("assets") or []
        return []
    finally:
        conn.close()


def _expect_error(fn, needle=""):
    try:
        fn()
    except ValueError as exc:
        assert needle.lower() in str(exc).lower(), f"expected {needle!r} in {exc!r}"
        return str(exc)
    raise AssertionError("expected a ValueError, the call succeeded")


# --------------------------------------------------------------------------
# 1-4  Watchlist lifecycle
# --------------------------------------------------------------------------

def test_create_watchlist():
    watchlist_id = _fresh_watchlist(USER_A, "My Crypto")
    assert watchlist_id > 0
    assert "My Crypto" in _list_names(USER_A)


def test_list_only_own_watchlists():
    _fresh_watchlist(USER_A, "A Private List")
    _fresh_watchlist(USER_B, "B Private List")
    assert "A Private List" in _list_names(USER_A)
    assert "B Private List" not in _list_names(USER_A)
    assert "A Private List" not in _list_names(USER_B)


def test_rename_watchlist():
    watchlist_id = _fresh_watchlist(USER_A, "Old Name")
    conn = _connect()
    try:
        cc.rename_watchlist(conn, USER_A, watchlist_id, {"name": "New Name"})
    finally:
        conn.close()
    names = _list_names(USER_A)
    assert "New Name" in names and "Old Name" not in names


def test_rename_requires_a_name():
    watchlist_id = _fresh_watchlist(USER_A, "Keeps Its Name")
    conn = _connect()
    try:
        _expect_error(lambda: cc.rename_watchlist(conn, USER_A, watchlist_id, {"name": "   "}), "required")
    finally:
        conn.close()
    assert "Keeps Its Name" in _list_names(USER_A)


def test_delete_watchlist():
    watchlist_id = _fresh_watchlist(USER_A, "Temporary")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        result = cc.delete_watchlist(conn, USER_A, watchlist_id)
    finally:
        conn.close()
    assert result["assets_removed"] == 1
    assert "Temporary" not in _list_names(USER_A)


# --------------------------------------------------------------------------
# 5-7  Assets
# --------------------------------------------------------------------------

def test_add_asset():
    watchlist_id = _fresh_watchlist(USER_A, "Add Target")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "btc"})
    finally:
        conn.close()
    assets = _assets(USER_A, watchlist_id)
    assert [a["asset_symbol"] for a in assets] == ["BTC"]


def test_duplicate_asset_rejected():
    watchlist_id = _fresh_watchlist(USER_A, "Dupe Target")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        # The second add is the double-tap on "Add", and it must not produce a
        # second BTC row that then disagrees with the first about its position.
        _expect_error(lambda: cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"}), "already")
        # Same asset, different casing — still the same asset.
        _expect_error(lambda: cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "btc"}), "already")
    finally:
        conn.close()
    assert len(_assets(USER_A, watchlist_id)) == 1


def test_same_asset_allowed_on_a_different_list():
    first = _fresh_watchlist(USER_A, "List One")
    second = _fresh_watchlist(USER_A, "List Two")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, first, {"assetSymbol": "ETH"})
        cc.add_watchlist_asset(conn, USER_A, second, {"assetSymbol": "ETH"})
    finally:
        conn.close()
    assert len(_assets(USER_A, first)) == 1
    assert len(_assets(USER_A, second)) == 1


def test_remove_asset():
    watchlist_id = _fresh_watchlist(USER_A, "Remove Target")
    conn = _connect()
    try:
        asset_id = int(cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "SOL"})["asset_id"])
        cc.delete_watchlist_asset(conn, USER_A, watchlist_id, asset_id)
    finally:
        conn.close()
    assert _assets(USER_A, watchlist_id) == []


# --------------------------------------------------------------------------
# 8-10  Isolation
# --------------------------------------------------------------------------

def test_non_owner_cannot_read():
    watchlist_id = _fresh_watchlist(USER_B, "B Only")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_B, watchlist_id, {"assetSymbol": "BTC"})
    finally:
        conn.close()
    assert _assets(USER_A, watchlist_id) == []
    with _Board(FAKE_BOARD):
        conn = _connect()
        try:
            view = cc.watchlist_market_view(conn, USER_A)
        finally:
            conn.close()
    assert all(int(w["id"]) != watchlist_id for w in view["watchlists"])


def test_non_owner_cannot_edit():
    watchlist_id = _fresh_watchlist(USER_B, "B Name")
    conn = _connect()
    try:
        _expect_error(lambda: cc.rename_watchlist(conn, USER_A, watchlist_id, {"name": "Stolen"}), "not found")
        _expect_error(lambda: cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"}), "not found")
    finally:
        conn.close()
    # The victim's list is untouched: right name, still empty.
    assert "B Name" in _list_names(USER_B)
    assert "Stolen" not in _list_names(USER_B)
    assert _assets(USER_B, watchlist_id) == []


def test_non_owner_cannot_delete():
    watchlist_id = _fresh_watchlist(USER_B, "B Keeps This")
    conn = _connect()
    try:
        asset_id = int(cc.add_watchlist_asset(conn, USER_B, watchlist_id, {"assetSymbol": "ETH"})["asset_id"])
        _expect_error(lambda: cc.delete_watchlist(conn, USER_A, watchlist_id), "not found")
        _expect_error(lambda: cc.delete_watchlist_asset(conn, USER_A, watchlist_id, asset_id), "not found")
    finally:
        conn.close()
    assert "B Keeps This" in _list_names(USER_B)
    assert len(_assets(USER_B, watchlist_id)) == 1


# --------------------------------------------------------------------------
# 11-13  Market data mapping and honesty
# --------------------------------------------------------------------------

def test_current_price_maps_correctly():
    watchlist_id = _fresh_watchlist(USER_A, "Priced")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    asset = next(a for w in view["watchlists"] if int(w["id"]) == watchlist_id for a in w["assets"])
    assert asset["symbol"] == "BTC"
    assert asset["name"] == "Bitcoin"
    assert asset["price"] == 66834.21
    assert asset["market_cap"] == 1_310_000_000_000.0
    assert asset["market_cap_rank"] == 1
    assert asset["sparkline"] == [65000.0, 65500.0, 66834.21]


def test_24h_change_maps_correctly_including_negative():
    watchlist_id = _fresh_watchlist(USER_A, "Movers")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "SOL"})
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    by_symbol = {a["symbol"]: a for w in view["watchlists"] if int(w["id"]) == watchlist_id for a in w["assets"]}
    assert by_symbol["BTC"]["change_24h"] == 2.35
    # A loss must survive the mapping as a negative number, not an absolute one.
    assert by_symbol["SOL"]["change_24h"] == -3.10


def test_no_data_state_is_honest():
    watchlist_id = _fresh_watchlist(USER_A, "Unknown Asset")
    conn = _connect()
    try:
        # ZZZ is a valid symbol shape the provider has never heard of.
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "ZZZ"})
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    asset = next(a for w in view["watchlists"] if int(w["id"]) == watchlist_id for a in w["assets"])
    # None, not 0.0 — a price of $0.00 and a change of 0.00% both read as facts.
    assert asset["price"] is None
    assert asset["change_24h"] is None
    assert asset["market_cap"] is None
    assert asset["has_market_data"] is False
    assert asset["sparkline"] == []


def test_aggregate_change_ignores_unpriced_assets():
    watchlist_id = _fresh_watchlist(USER_A, "Mixed")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "ZZZ"})
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    watchlist = next(w for w in view["watchlists"] if int(w["id"]) == watchlist_id)
    # The average is over the one asset we actually have a number for, so an
    # unknown asset cannot drag a summary toward zero as if it were flat.
    assert watchlist["asset_count"] == 2
    assert watchlist["priced_asset_count"] == 1
    assert watchlist["average_change_24h"] == 2.35


def test_no_priced_assets_gives_no_aggregate():
    watchlist_id = _fresh_watchlist(USER_A, "All Unknown")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "ZZZ"})
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    watchlist = next(w for w in view["watchlists"] if int(w["id"]) == watchlist_id)
    assert watchlist["average_change_24h"] is None


# --------------------------------------------------------------------------
# 14  Favorites
# --------------------------------------------------------------------------

def test_favorite_persists_and_toggles():
    conn = _connect()
    try:
        cc.set_favorite_asset(conn, USER_A, "BTC", True)
    finally:
        conn.close()
    # A separate connection: this is persistence, not a value held in memory.
    conn = _connect()
    try:
        assert "BTC" in {f["asset_symbol"] for f in cc.list_favorite_assets(conn, USER_A)}
        cc.set_favorite_asset(conn, USER_A, "BTC", False)
    finally:
        conn.close()
    conn = _connect()
    try:
        assert "BTC" not in {f["asset_symbol"] for f in cc.list_favorite_assets(conn, USER_A)}
    finally:
        conn.close()


def test_favoriting_twice_does_not_duplicate():
    conn = _connect()
    try:
        cc.set_favorite_asset(conn, USER_A, "ETH", True)
        cc.set_favorite_asset(conn, USER_A, "ETH", True)
        rows = [f for f in cc.list_favorite_assets(conn, USER_A) if f["asset_symbol"] == "ETH"]
    finally:
        conn.close()
    assert len(rows) == 1


def test_favorites_are_per_user():
    conn = _connect()
    try:
        cc.set_favorite_asset(conn, USER_A, "SOL", True)
        b_favorites = {f["asset_symbol"] for f in cc.list_favorite_assets(conn, USER_B)}
    finally:
        conn.close()
    assert "SOL" not in b_favorites


def test_favorite_surfaces_in_the_watchlist_view():
    watchlist_id = _fresh_watchlist(USER_A, "Starred")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        cc.set_favorite_asset(conn, USER_A, "BTC", True)
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    asset = next(a for w in view["watchlists"] if int(w["id"]) == watchlist_id for a in w["assets"])
    assert asset["favorite"] is True
    assert "BTC" in {f["symbol"] for f in view["favorites"]}


# --------------------------------------------------------------------------
# 15-16  Asset detail and chart ranges
# --------------------------------------------------------------------------

def test_asset_detail_loads():
    watchlist_id = _fresh_watchlist(USER_A, "Detail Source")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        with _Board(FAKE_BOARD):
            detail = cc.asset_detail(conn, USER_A, "BTC")
    finally:
        conn.close()
    assert detail["ok"] is True
    assert detail["asset"]["symbol"] == "BTC"
    assert detail["asset"]["price"] == 66834.21
    assert detail["asset"]["circulating_supply"] == 19_700_000.0
    assert detail["asset"]["max_supply"] == 21_000_000.0
    # It reports which of the user's own lists this asset sits on.
    assert watchlist_id in {int(m["watchlist_id"]) for m in detail["watchlists"]}


def test_asset_detail_for_an_unknown_asset_is_honest():
    conn = _connect()
    try:
        with _Board(FAKE_BOARD):
            detail = cc.asset_detail(conn, USER_A, "ZZZ")
    finally:
        conn.close()
    assert detail["asset"]["price"] is None
    assert detail["asset"]["has_market_data"] is False
    # No range is offered, because every one of them would say "Unavailable".
    assert detail["ranges"] == []


def test_chart_timeframe_switches_between_real_ranges():
    calls = []

    def fake_history(coin_id, days):
        calls.append((coin_id, days))
        return [[1_755_000_000_000 + i * 60_000, 100.0 + i] for i in range(200)]

    original_fetch = market_data_service.fetch_coingecko_history
    original_get = market_data_service.get_symbol
    market_data_service.fetch_coingecko_history = fake_history
    market_data_service.get_symbol = lambda symbol: {"id": "bitcoin", "symbol": "BTC"}
    market_data_service.HISTORY_CACHE.clear()
    try:
        # "ALL" is HISTORY_MAX_DAYS, not the string "max": the Basic plan 401s
        # on days=max, so the old spelling made the ALL tab fail on every single
        # request. 3M/90D is the range the Market Pulse chart added.
        cases = (
            ("24H", 1), ("7D", 7), ("1M", 30), ("3M", 90), ("1Y", 365),
            ("ALL", market_data_service.HISTORY_MAX_DAYS),
        )
        for range_key, expected_days in cases:
            payload = market_data_service.asset_history("BTC", range_key)
            assert payload["ok"] is True, range_key
            assert payload["range"] == range_key
            assert payload["points"], range_key
            # Each range asks the provider a different question rather than
            # relabelling one series five times.
            assert calls[-1][1] == expected_days, (range_key, calls[-1])
            assert len(payload["points"]) <= market_data_service.HISTORY_MAX_POINTS
            assert payload["points"][0]["price"] == 100.0
    finally:
        market_data_service.fetch_coingecko_history = original_fetch
        market_data_service.get_symbol = original_get
        market_data_service.HISTORY_CACHE.clear()


def test_chart_history_is_cached_per_range():
    calls = []

    def fake_history(coin_id, days):
        calls.append(days)
        return [[1_755_000_000_000 + i * 60_000, 100.0 + i] for i in range(50)]

    original_fetch = market_data_service.fetch_coingecko_history
    original_get = market_data_service.get_symbol
    market_data_service.fetch_coingecko_history = fake_history
    market_data_service.get_symbol = lambda symbol: {"id": "bitcoin", "symbol": "BTC"}
    market_data_service.HISTORY_CACHE.clear()
    try:
        market_data_service.asset_history("BTC", "7D")
        market_data_service.asset_history("BTC", "7D")
        market_data_service.asset_history("BTC", "7D")
    finally:
        market_data_service.fetch_coingecko_history = original_fetch
        market_data_service.get_symbol = original_get
        market_data_service.HISTORY_CACHE.clear()
    # Three screen opens, one provider call.
    assert calls == [7]


def test_history_for_an_unknown_asset_returns_no_points():
    original_get = market_data_service.get_symbol
    market_data_service.get_symbol = lambda symbol: None
    market_data_service.HISTORY_CACHE.clear()
    try:
        payload = market_data_service.asset_history("ZZZ", "24H")
    finally:
        market_data_service.get_symbol = original_get
        market_data_service.HISTORY_CACHE.clear()
    assert payload["ok"] is False
    assert payload["points"] == []
    assert payload["source"] == "unavailable"


def test_sparkline_is_bounded_and_ends_on_the_latest_price():
    item = market_data_service.normalize_market_item({
        "id": "bitcoin", "name": "Bitcoin", "symbol": "btc", "current_price": 66834.21,
        "sparkline_in_7d": {"price": [float(i) for i in range(168)]},
    })
    assert len(item["sparkline"]) == market_data_service.SPARKLINE_MAX_POINTS
    # The right edge of the line is the newest price; a sparkline that stops
    # short reads as a lie next to the price printed beside it.
    assert item["sparkline"][-1] == 167.0


# --------------------------------------------------------------------------
# 17-19  Alert integration
# --------------------------------------------------------------------------

def _create_alert(user_id, symbol, threshold):
    result = alert_engine.create_alert_rule(
        user_id=user_id,
        alert_type="coin_price",
        symbol=symbol,
        condition="above",
        threshold=threshold,
        channels={"in_app": True},
    )
    return int((result or {}).get("alert_id") or 0)


def test_existing_alert_appears_in_asset_detail():
    alert_id = _create_alert(USER_A, "BTC", 70000)
    assert alert_id > 0
    conn = _connect()
    try:
        with _Board(FAKE_BOARD):
            detail = cc.asset_detail(conn, USER_A, "BTC")
    finally:
        conn.close()
    assert alert_id in {int(a.get("id") or 0) for a in detail["alerts"]}


def test_asset_detail_shows_only_that_assets_alerts():
    _create_alert(USER_A, "ETH", 4000)
    sol_alert = _create_alert(USER_A, "SOL", 200)
    conn = _connect()
    try:
        with _Board(FAKE_BOARD):
            detail = cc.asset_detail(conn, USER_A, "SOL")
    finally:
        conn.close()
    symbols = {str(a.get("asset_symbol") or a.get("symbol") or "").upper() for a in detail["alerts"]}
    # The positive half is what gives the negative half its meaning: without it,
    # an alert lookup that returned nothing at all would satisfy "no ETH here".
    assert sol_alert in {int(a.get("id") or 0) for a in detail["alerts"]}
    assert "ETH" not in symbols


def test_alert_badge_counts_on_the_watchlist_row():
    watchlist_id = _fresh_watchlist(USER_A, "Alerted")
    _create_alert(USER_A, "SOL", 200)
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "SOL"})
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    asset = next(a for w in view["watchlists"] if int(w["id"]) == watchlist_id for a in w["assets"])
    assert asset["alert_count"] >= 1


def test_deleting_a_watchlist_does_not_delete_alerts():
    watchlist_id = _fresh_watchlist(USER_A, "Doomed List")
    alert_id = _create_alert(USER_A, "BTC", 88000)
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        cc.delete_watchlist(conn, USER_A, watchlist_id)
    finally:
        conn.close()
    rule = alert_engine.get_alert_rule(alert_id, USER_A)
    assert rule, "deleting a watchlist must not delete a standing alert rule"
    assert str(rule.get("status") or "active").lower() not in {"deleted", "archived"}


def test_removing_an_asset_does_not_delete_alerts():
    watchlist_id = _fresh_watchlist(USER_A, "Remove Keeps Alerts")
    alert_id = _create_alert(USER_A, "ETH", 5000)
    conn = _connect()
    try:
        asset_id = int(cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "ETH"})["asset_id"])
        cc.delete_watchlist_asset(conn, USER_A, watchlist_id, asset_id)
    finally:
        conn.close()
    rule = alert_engine.get_alert_rule(alert_id, USER_A)
    assert rule
    assert str(rule.get("status") or "active").lower() not in {"deleted", "archived"}


def test_deleting_a_watchlist_does_not_delete_favorites():
    watchlist_id = _fresh_watchlist(USER_A, "Fav Survivor")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        cc.set_favorite_asset(conn, USER_A, "BTC", True)
        cc.delete_watchlist(conn, USER_A, watchlist_id)
        favorites = {f["asset_symbol"] for f in cc.list_favorite_assets(conn, USER_A)}
    finally:
        conn.close()
    assert "BTC" in favorites


# --------------------------------------------------------------------------
# 20  Provider failure
# --------------------------------------------------------------------------

def test_market_provider_failure_does_not_break_the_page():
    watchlist_id = _fresh_watchlist(USER_A, "Outage")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        with _Board(DEAD_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    # The page still renders: the user's own structure is intact and only the
    # provider-supplied numbers are missing.
    watchlist = next(w for w in view["watchlists"] if int(w["id"]) == watchlist_id)
    assert watchlist["name"] == "Outage"
    assert watchlist["asset_count"] == 1
    asset = watchlist["assets"][0]
    assert asset["symbol"] == "BTC"
    assert asset["price"] is None
    assert view["market"]["ready"] is False
    assert view["market"]["warning"]


def test_a_raising_provider_does_not_propagate():
    watchlist_id = _fresh_watchlist(USER_A, "Throwing Provider")

    def boom(category="top_volume", limit=50):
        raise RuntimeError("provider exploded")

    conn = _connect()
    original = cc.market_board
    cc.market_board = boom
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        try:
            view = cc.watchlist_market_view(conn, USER_A)
        except RuntimeError:
            raise AssertionError("a provider exception must not reach the caller")
    finally:
        cc.market_board = original
        conn.close()
    assert any(int(w["id"]) == watchlist_id for w in view["watchlists"])


def test_one_failed_asset_does_not_break_the_others():
    watchlist_id = _fresh_watchlist(USER_A, "Partial")
    conn = _connect()
    try:
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "BTC"})
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "ZZZ"})
        cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": "ETH"})
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, USER_A)
    finally:
        conn.close()
    by_symbol = {a["symbol"]: a for w in view["watchlists"] if int(w["id"]) == watchlist_id for a in w["assets"]}
    assert by_symbol["BTC"]["price"] == 66834.21
    assert by_symbol["ETH"]["price"] == 3287.65
    assert by_symbol["ZZZ"]["price"] is None


# --------------------------------------------------------------------------
# 24  One provider call per screen
# --------------------------------------------------------------------------

def test_no_duplicate_price_requests_for_the_same_asset():
    watchlist_id = _fresh_watchlist(USER_A, "Many Rows")
    calls = []

    def counting_board(category="top_volume", limit=50):
        calls.append(category)
        return dict(FAKE_BOARD)

    conn = _connect()
    original = cc.market_board
    try:
        for symbol in ("BTC", "ETH", "SOL"):
            cc.add_watchlist_asset(conn, USER_A, watchlist_id, {"assetSymbol": symbol})
        cc.market_board = counting_board
        cc.watchlist_market_view(conn, USER_A)
    finally:
        cc.market_board = original
        conn.close()
    # Three assets across every list the user owns, one snapshot.
    assert len(calls) == 1


def test_asset_search_only_offers_assets_the_provider_can_price():
    with _Board(FAKE_BOARD):
        result = cc.search_assets("bit")
    symbols = [a["symbol"] for a in result["assets"]]
    assert "BTC" in symbols
    assert all(a["has_market_data"] for a in result["assets"])

    with _Board(FAKE_BOARD):
        everything = cc.search_assets("")
    # An empty query lists the board, ranked, rather than nothing.
    assert everything["assets"][0]["symbol"] == "BTC"


def test_empty_state_is_a_real_state():
    conn = _connect()
    try:
        with _Board(FAKE_BOARD):
            view = cc.watchlist_market_view(conn, 99999)
    finally:
        conn.close()
    # A user with nothing gets a well-formed empty payload, not an error.
    assert view["ok"] is True
    assert view["watchlists"] == []
    assert view["favorites"] == []


# --------------------------------------------------------------------------

def _run_all():
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, exc))
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
