"""One Premium alert watching every asset on a watchlist.

The interesting claim is not "it loops over symbols" — it is that each asset
gets its *own* latch. The engine's whole guarantee (arm on first observation,
one notification per genuine crossing, never fire twice on the same edge) is
written against a single rule row holding a single state. Fanning a rule out
over twenty assets while leaving that state shared would mean the first asset
to cross latched the rule and silenced the other nineteen until it re-armed —
a bug that produces no error, passes every existing test, and looks from the
outside like "the alert only ever tells me about Bitcoin".

What is locked in here:

  * membership is read every cycle, so adding an asset extends the rule and
    removing one stops it, without touching the rule the member created;
  * a newly added asset arms before it can fire, exactly like a new rule, so it
    cannot report a level it has been sitting at for a week as a crossing;
  * each asset latches, re-arms, cools down and dedupes independently;
  * an asset whose quote fails does not stop the rest of the list;
  * an oversized list is refused at creation rather than silently trimmed;
  * a watchlist rule carries no ticker, so nothing downstream can read it as an
    alert about BTC;
  * single-symbol rules still keep their state exactly where they always did.

Run directly (no pytest required):

    python tests/test_crypto_alert_watchlist_rules.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_alert_watchlist_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
for _key in (
    "WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY",
    "FCM_SERVER_KEY", "FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY",
    "APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_BUNDLE_ID",
    "BREVO_API_KEY", "BREVO_SMS_API_KEY", "TELEGRAM_BOT_TOKEN",
):
    os.environ.pop(_key, None)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import alert_engine  # noqa: E402
from services import dashboard_crypto_command_center as command_center  # noqa: E402
from services import premium_crypto_access  # noqa: E402
from services import crypto_premium_gate  # noqa: E402
from services import user_context  # noqa: E402


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
DISPATCHED: list[dict] = []

#: Price per symbol. A symbol absent from here has no quote at all, which is how
#: a provider that dropped one asset from its board actually presents.
_PRICES: dict[str, float] = {}

_PREMIUM_USERS: set[int] = set()


def _fake_quote(symbol, *args, **kwargs):
    symbol = str(symbol or "").upper()
    if symbol not in _PRICES:
        return {"ok": False, "asset": {}, "message": f"{symbol} quote unavailable."}
    return {"ok": True, "source": "test", "stale": False,
            "asset": {"symbol": symbol, "price": _PRICES[symbol],
                      "change_24h": None, "price_change_24h": None,
                      "volume_24h": None, "market_cap": None}}


def _fake_dispatch(event, rule=None):
    DISPATCHED.append({
        "symbol": event.get("symbol"),
        "trigger_key": event.get("trigger_key") or event.get("trigger_bucket"),
        "observed_value": event.get("observed_value"),
        "message": event.get("message"),
    })
    return {"ok": True, "channels": {"push": "sent"}}


alert_engine.live_market_service.get_crypto_quote = _fake_quote
alert_engine.dispatch_alert_event = _fake_dispatch
alert_engine.channel_warnings = lambda user_id, channels: []
premium_crypto_access.allowed_for_user_id = lambda user_id, key: int(user_id or 0) in _PREMIUM_USERS
# Delivery-time premium hard-lock: `evaluate_alert_rule` asks
# `crypto_premium_gate`, not the `premium_crypto_access` gate stubbed above.
# Same `_PREMIUM_USERS` set so free users stay genuinely free.
crypto_premium_gate.has_crypto_capability = lambda user_id, key: int(user_id or 0) in _PREMIUM_USERS


FAILURES: list[str] = []


def check(label: str, actual, expected):
    if actual == expected:
        print(f"  PASS  {label}")
        return
    FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    raise AssertionError(label)


def set_prices(**prices):
    _PRICES.clear()
    for symbol, price in prices.items():
        _PRICES[symbol.upper()] = price


_NEXT_USER = [8100]


def premium_user() -> int:
    _NEXT_USER[0] += 1
    _PREMIUM_USERS.add(_NEXT_USER[0])
    return _NEXT_USER[0]


def free_user() -> int:
    _NEXT_USER[0] += 1
    return _NEXT_USER[0]


def make_watchlist(user_id, *symbols) -> int:
    conn = user_context.connect()
    try:
        command_center.ensure_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO crypto_watchlists (user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, "Test list", "2026-08-23T00:00:00", "2026-08-23T00:00:00"),
        )
        watchlist_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    for position, symbol in enumerate(symbols):
        add_asset(user_id, watchlist_id, symbol, position)
    return watchlist_id


def add_asset(user_id, watchlist_id, symbol, position=0):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO crypto_watchlist_assets (watchlist_id, user_id, asset_symbol, position, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (watchlist_id, user_id, symbol.upper(), position, "2026-08-23T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def remove_asset(user_id, watchlist_id, symbol):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM crypto_watchlist_assets WHERE watchlist_id=? AND user_id=? AND asset_symbol=?",
            (watchlist_id, user_id, symbol.upper()),
        )
        conn.commit()
    finally:
        conn.close()


def make_rule(user_id, watchlist_id, comparator="above", value=100.0, cooldown=0):
    result = alert_engine.create_alert_rule(
        user_id, alert_type="coin_price", channels={"push": True, "in_app": True},
        cooldown_seconds=cooldown, watchlist_id=watchlist_id,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": comparator, "value": value}]},
    )
    if not result.get("ok"):
        raise AssertionError(f"rule creation failed: {result}")
    return result["alert_id"]


def load_rule(rule_id):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM alert_rules WHERE id=? LIMIT 1", (rule_id,))
        return user_context.row_to_dict(cur.fetchone()) or {}
    finally:
        conn.close()


def observe(rule_id):
    return alert_engine.evaluate_alert_rule(load_rule(rule_id))


def symbol_state(rule_id, symbol):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM alert_rule_symbol_state WHERE rule_id=? AND symbol=? LIMIT 1",
                    (rule_id, symbol.upper()))
        return user_context.row_to_dict(cur.fetchone()) or {}
    finally:
        conn.close()


def fired_symbols() -> list[str]:
    return [entry["symbol"] for entry in DISPATCHED]


def clear_dispatched():
    DISPATCHED.clear()


def clear_cooldown(rule_id, symbol=None):
    """Age the cooldown clock. Per asset when a symbol is named, because that is
    where a watchlist rule's rate limit actually lives."""
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        if symbol:
            cur.execute("UPDATE alert_rule_symbol_state SET last_triggered_at=? WHERE rule_id=? AND symbol=?",
                        ("2000-01-01T00:00:00", rule_id, symbol.upper()))
        else:
            cur.execute("UPDATE alert_rule_symbol_state SET last_triggered_at=? WHERE rule_id=?",
                        ("2000-01-01T00:00:00", rule_id))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------
def test_watching_a_whole_list_is_premium():
    """Same gate as every other advanced rule, and enforced in the service —
    the worker, UNDX and the admin tools all create rules through this function,
    so a gate in one HTTP handler would be a gate on one door of several."""
    user_id = free_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    result = alert_engine.create_alert_rule(
        user_id, watchlist_id=watchlist_id,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("refused", result.get("ok"), False)
    check("named the reason", result.get("code"), "premium_required")


def test_another_members_list_cannot_be_watched():
    """And the refusal is worded identically to a list that does not exist, so
    the error cannot be used to enumerate which watchlist ids are real."""
    owner = premium_user()
    intruder = premium_user()
    watchlist_id = make_watchlist(owner, "BTC")
    stolen = alert_engine.create_alert_rule(
        intruder, watchlist_id=watchlist_id,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    missing = alert_engine.create_alert_rule(
        intruder, watchlist_id=watchlist_id + 99_000,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("refused", stolen.get("ok"), False)
    check("not found", stolen.get("code"), "watchlist_not_found")
    check("indistinguishable from a missing list", stolen.get("message"), missing.get("message"))


def test_an_empty_list_is_refused_rather_than_created_silent():
    """A rule over nothing never fires, and would read as a broken alert."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id)
    result = alert_engine.create_alert_rule(
        user_id, watchlist_id=watchlist_id,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("refused", result.get("ok"), False)
    check("named the reason", result.get("code"), "watchlist_empty")


def test_an_oversized_list_is_refused_not_trimmed():
    """Trimming would leave the member believing the rule covers assets it does
    not, and the only way they could find out is by not being told about one."""
    user_id = premium_user()
    symbols = [f"C{index:02d}" for index in range(alert_engine.WATCHLIST_RULE_MAX_SYMBOLS + 1)]
    watchlist_id = make_watchlist(user_id, *symbols)
    result = alert_engine.create_alert_rule(
        user_id, watchlist_id=watchlist_id,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("refused", result.get("ok"), False)
    check("named the reason", result.get("code"), "watchlist_too_large")
    check("says how many fit", str(alert_engine.WATCHLIST_RULE_MAX_SYMBOLS) in result.get("message", ""), True)


def test_a_watchlist_rule_claims_no_ticker():
    """`_normalize_symbol` defaults to BTC. A rule over a list that carried that
    default would be rendered everywhere as an alert about Bitcoin."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "ETH", "SOL")
    rule_id = make_rule(user_id, watchlist_id)
    rule = alert_engine._public_rule(load_rule(rule_id))
    check("no symbol", rule["symbol"], "")
    check("no asset symbol", rule["asset_symbol"], "")
    check("knows it is a list rule", rule["is_watchlist_rule"], True)
    check("remembers which list", rule["watchlist_id"], watchlist_id)
    check("summary names no asset", "BTC" in (rule["condition_summary"] or ""), False)


# --------------------------------------------------------------------------
# Independent latches
# --------------------------------------------------------------------------
def test_every_asset_arms_on_its_own_first_observation():
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=150.0, ETH=150.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    clear_dispatched()
    result = observe(rule_id)
    check("nothing fired on the first sweep", result.get("triggered"), False)
    check("both armed", [symbol_state(rule_id, s)["condition_state"] for s in ("BTC", "ETH")],
          ["armed", "armed"])
    check("no notifications", fired_symbols(), [])


def test_one_asset_crossing_does_not_silence_the_others():
    """The bug this whole design exists to prevent: a shared latch would let BTC
    latch the rule and leave ETH unable to fire until BTC came back down."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    observe(rule_id)
    clear_dispatched()

    set_prices(BTC=150.0, ETH=50.0)
    observe(rule_id)
    check("only BTC spoke", fired_symbols(), ["BTC"])
    check("BTC latched", symbol_state(rule_id, "BTC")["condition_state"], "latched")
    check("ETH still armed", symbol_state(rule_id, "ETH")["condition_state"], "armed")

    clear_dispatched()
    set_prices(BTC=150.0, ETH=150.0)
    observe(rule_id)
    check("ETH speaks on its own crossing", fired_symbols(), ["ETH"])


def test_each_asset_owns_its_own_event_identity():
    """`trigger_key` is what makes a crossing unique across retries and restarts.
    Keyed on the rule alone, BTC's first crossing and ETH's first crossing are
    both "<id>:1" and the unique index drops one of them."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    observe(rule_id)
    clear_dispatched()
    set_prices(BTC=150.0, ETH=150.0)
    observe(rule_id)
    keys = sorted(entry["trigger_key"] for entry in DISPATCHED)
    check("two distinct crossings", keys, [f"{rule_id}:BTC:1", f"{rule_id}:ETH:1"])


def test_a_re_evaluated_crossing_notifies_once_per_asset():
    """Restart safety, per asset. Re-running the same cycle must not produce a
    second banner for either one."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0, cooldown=3600)
    observe(rule_id)
    clear_dispatched()
    set_prices(BTC=150.0, ETH=150.0)
    observe(rule_id)
    observe(rule_id)
    observe(rule_id)
    check("one notification each", sorted(fired_symbols()), ["BTC", "ETH"])


def test_cooldown_is_measured_per_asset():
    """A shared cooldown would mean a busy asset muted the rest of the list."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0, cooldown=3600)
    observe(rule_id)
    set_prices(BTC=150.0, ETH=50.0)
    observe(rule_id)  # BTC fires and starts cooling down.
    clear_dispatched()

    # BTC drops back and re-crosses inside its cooldown; ETH crosses for the
    # first time. ETH has never fired, so nothing about BTC applies to it.
    set_prices(BTC=50.0, ETH=50.0)
    observe(rule_id)
    set_prices(BTC=150.0, ETH=150.0)
    observe(rule_id)
    check("BTC held back, ETH heard", fired_symbols(), ["ETH"])
    check("BTC's own clock, not the rule's",
          symbol_state(rule_id, "BTC")["last_triggered_at"] is not None, True)


def test_a_failing_quote_on_one_asset_does_not_stop_the_list():
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    observe(rule_id)
    clear_dispatched()

    # The provider drops BTC from its board entirely.
    set_prices(ETH=150.0)
    result = observe(rule_id)
    check("ETH still decided and fired", fired_symbols(), ["ETH"])
    check("BTC's latch untouched", symbol_state(rule_id, "BTC")["condition_state"], "armed")
    check("the sweep reports the failure", result.get("ok"), False)


# --------------------------------------------------------------------------
# Membership is live
# --------------------------------------------------------------------------
def test_an_asset_added_later_arms_before_it_can_fire():
    """Otherwise adding an asset that already sits above the threshold would
    report a level it has held for a week as a fresh crossing."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC")
    set_prices(BTC=50.0, ETH=150.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    observe(rule_id)
    clear_dispatched()

    add_asset(user_id, watchlist_id, "ETH", position=1)
    observe(rule_id)
    check("ETH said nothing on its first sweep", fired_symbols(), [])
    check("ETH armed", symbol_state(rule_id, "ETH")["condition_state"], "armed")

    set_prices(BTC=50.0, ETH=160.0)
    observe(rule_id)
    check("ETH speaks once it has an edge to cross", fired_symbols(), ["ETH"])


def test_an_asset_removed_from_the_list_stops_being_evaluated():
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    observe(rule_id)
    clear_dispatched()

    remove_asset(user_id, watchlist_id, "ETH")
    set_prices(BTC=50.0, ETH=150.0)
    result = observe(rule_id)
    check("ETH is not watched any more", fired_symbols(), [])
    check("the sweep only covers what is on the list", result.get("symbols"), ["BTC"])


def test_a_list_that_grows_past_the_cap_says_so():
    """Creation refuses an oversized list, but a list can grow afterwards. The
    sweep then reports that it is not covering everything rather than pretending
    a shorter list is the whole one."""
    user_id = premium_user()
    symbols = [f"D{index:02d}" for index in range(alert_engine.WATCHLIST_RULE_MAX_SYMBOLS)]
    watchlist_id = make_watchlist(user_id, *symbols)
    set_prices(**{symbol: 50.0 for symbol in symbols})
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    check("not truncated yet", observe(rule_id).get("truncated"), False)

    add_asset(user_id, watchlist_id, "ZZZ", position=99)
    result = observe(rule_id)
    check("truncation is reported", result.get("truncated"), True)
    check("and said out loud", "grown past" in result.get("message", ""), True)
    check("still watching what it can", len(result.get("symbols") or []),
          alert_engine.WATCHLIST_RULE_MAX_SYMBOLS)


def test_an_empty_list_is_a_quiet_no_op_not_an_error():
    """A member can empty a watchlist a rule points at. That is not a failure
    the worker should log every 45 seconds."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC")
    set_prices(BTC=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    observe(rule_id)
    remove_asset(user_id, watchlist_id, "BTC")
    result = observe(rule_id)
    check("no error", result.get("ok"), True)
    check("nothing fired", result.get("triggered"), False)
    check("says what happened", "no assets" in result.get("message", ""), True)


# --------------------------------------------------------------------------
# Nothing changed for ordinary rules
# --------------------------------------------------------------------------
def test_a_single_symbol_rule_keeps_its_state_on_its_own_row():
    """The scope argument defaults to None everywhere, so an ordinary rule runs
    the identical SQL it always did and writes nowhere new."""
    user_id = premium_user()
    set_prices(BTC=50.0)
    created = alert_engine.create_alert_rule(
        user_id, symbol="BTC", condition="above", threshold=100.0,
        channels={"push": True}, cooldown_seconds=0)
    rule_id = created["alert_id"]
    observe(rule_id)
    clear_dispatched()
    set_prices(BTC=150.0)
    observe(rule_id)

    rule = load_rule(rule_id)
    check("latched on its own row", rule["condition_state"], "latched")
    check("kept its ticker", rule["symbol"], "BTC")
    check("no per-symbol row was created", symbol_state(rule_id, "BTC"), {})
    check("classic trigger key", DISPATCHED[0]["trigger_key"], f"{rule_id}:1")


def test_the_notification_names_the_asset_that_moved():
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, watchlist_id, "above", 100.0)
    observe(rule_id)
    clear_dispatched()
    set_prices(BTC=50.0, ETH=150.0)
    observe(rule_id)
    message = DISPATCHED[0]["message"]
    check("names ETH", message.startswith("ETH price is above"), True)
    check("does not name BTC", "BTC" in message, False)
    check("the event row carries the asset", DISPATCHED[0]["symbol"], "ETH")


def test_the_command_center_creates_a_list_rule():
    """The surface members actually use. It must reach the same gate and produce
    the same shape as the service-level call, not a parallel implementation."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "BTC", "ETH")
    conn = user_context.connect()
    try:
        result = command_center.create_alert(conn, user_id, {
            "watchlistId": watchlist_id,
            "conditions": [{"metric": "price", "comparator": "above", "value": 100.0}],
        })
    finally:
        conn.close()
    check("created", result.get("ok"), True)
    check("named as a list alert", result.get("message"), "Watchlist alert created.")
    rule = alert_engine._public_rule(load_rule(result["alert_id"]))
    check("stored against the list", rule["watchlist_id"], watchlist_id)
    check("and against no asset", rule["symbol"], "")


def test_a_symbol_sent_alongside_a_list_does_not_win():
    """A payload carrying both is ambiguous. Resolving it client-side would let
    two clients disagree about what the same request created."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, "ETH")
    conn = user_context.connect()
    try:
        result = command_center.create_alert(conn, user_id, {
            "watchlistId": watchlist_id,
            "assetSymbol": "DOGE",
            "conditions": [{"metric": "price", "comparator": "above", "value": 100.0}],
        })
    finally:
        conn.close()
    rule = alert_engine._public_rule(load_rule(result["alert_id"]))
    check("the list won", rule["watchlist_id"], watchlist_id)
    check("no stray ticker was kept", rule["symbol"], "")

    set_prices(ETH=50.0, DOGE=150.0)
    observe(result["alert_id"])
    clear_dispatched()
    set_prices(ETH=150.0, DOGE=150.0)
    observe(result["alert_id"])
    check("it watches the list, not the ticker", fired_symbols(), ["ETH"])


def test_the_command_center_surfaces_the_premium_gate():
    user_id = free_user()
    watchlist_id = make_watchlist(user_id, "BTC")
    conn = user_context.connect()
    try:
        command_center.create_alert(conn, user_id, {
            "watchlistId": watchlist_id,
            "conditions": [{"metric": "price", "comparator": "above", "value": 100.0}],
        })
        raised = ""
    except command_center.PremiumRequired as exc:
        raised = str(exc)
    except Exception as exc:  # pragma: no cover - would be the wrong error class
        raised = f"wrong error: {exc!r}"
    finally:
        conn.close()
    check("raised as a paywall, not a validation error", "Premium" in raised, True)


TESTS = [
    test_watching_a_whole_list_is_premium,
    test_another_members_list_cannot_be_watched,
    test_an_empty_list_is_refused_rather_than_created_silent,
    test_an_oversized_list_is_refused_not_trimmed,
    test_a_watchlist_rule_claims_no_ticker,
    test_every_asset_arms_on_its_own_first_observation,
    test_one_asset_crossing_does_not_silence_the_others,
    test_each_asset_owns_its_own_event_identity,
    test_a_re_evaluated_crossing_notifies_once_per_asset,
    test_cooldown_is_measured_per_asset,
    test_a_failing_quote_on_one_asset_does_not_stop_the_list,
    test_an_asset_added_later_arms_before_it_can_fire,
    test_an_asset_removed_from_the_list_stops_being_evaluated,
    test_a_list_that_grows_past_the_cap_says_so,
    test_an_empty_list_is_a_quiet_no_op_not_an_error,
    test_a_single_symbol_rule_keeps_its_state_on_its_own_row,
    test_the_notification_names_the_asset_that_moved,
    test_the_command_center_creates_a_list_rule,
    test_a_symbol_sent_alongside_a_list_does_not_win,
    test_the_command_center_surfaces_the_premium_gate,
]


def main() -> int:
    alert_engine.ensure_alert_schema()
    for test in TESTS:
        print(f"\n{test.__name__}")
        clear_dispatched()
        try:
            test()
        except AssertionError:
            pass
        except Exception as exc:  # pragma: no cover - harness
            FAILURES.append(f"{test.__name__}: {exc!r}")
            print(f"  ERROR {exc!r}")
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} problem(s)")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"PASSED — {len(TESTS)} tests, all assertions green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
