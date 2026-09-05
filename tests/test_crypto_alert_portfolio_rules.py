"""One Premium alert watching every asset the member actually holds.

The fan-out machinery is shared with watchlist rules and is proven in
tests/test_crypto_alert_watchlist_rules.py — per-asset latches, per-asset
cooldowns, one asset's failure not silencing the rest. Repeating those here
would only assert that `_evaluate_scoped_rule` is called twice.

What is different about a portfolio, and therefore what this file is for:

  * **Membership nobody curated.** A watchlist changes when the member edits
    it. A portfolio changes when they buy something on an entirely different
    screen — so "read membership every cycle" has to hold against a set that
    moves without the alert ever being touched, and the cap has to be reported
    when it is crossed *after* creation rather than only refused before it.

  * **Two lots of the same asset.** `portfolio_items` has no uniqueness
    constraint on (user_id, symbol): buying BTC twice is two rows. Evaluating
    BTC twice in one cycle would race its own latch — the second pass reads the
    state the first just wrote and sees a crossing that never happened. And
    counting it twice against the cap would truncate a portfolio comfortably
    inside it.

  * **The line this feature must not cross.** A portfolio rule knows what you
    hold. It must not start reporting what it is worth: `portfolio_items`
    stores an average buy price and no transaction ledger, so any "you're up
    $4,200" is a number the data cannot support. The scope answers *which*
    assets; the market conditions answer the rest. `test_the_alert_reports_a
    _price_not_a_position` is the one that pins this, and it is the reason the
    holdings in these fixtures have deliberately awkward amounts.

Run directly (no pytest required):

    python tests/test_crypto_alert_portfolio_rules.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_alert_portfolio_"), "test.db")
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

# Three seams, because the code reaches the same decision by three routes:
# `create_alert_rule` asks by id, and `alert_options` loads the row once and then
# asks about the row. Patching only the first would leave the creation form
# answering "not entitled" for an account creation itself accepts — which is a
# real class of bug, so the suite must be able to see both answers.
premium_crypto_access.load_user_row = lambda user_id: {"user_id": int(user_id or 0)}
premium_crypto_access.allowed = lambda row, key: int((row or {}).get("user_id") or 0) in _PREMIUM_USERS
premium_crypto_access.allowed_for_user_id = lambda user_id, key: int(user_id or 0) in _PREMIUM_USERS
# Delivery-time premium hard-lock: `evaluate_alert_rule` asks
# `crypto_premium_gate`, not the `premium_crypto_access` gate stubbed above.
# Same `_PREMIUM_USERS` set so free users stay genuinely free.
crypto_premium_gate.has_crypto_capability = lambda user_id, key: int(user_id or 0) in _PREMIUM_USERS


def _schema():
    """The one table `bot.init_db()` owns that this suite reads, which it does
    not import — 111k lines and a Flask app to get at it.

    ``notification_delivery_logs`` is deliberately absent: ``list_alert_rules``
    decorates every rule from it, and this suite standing in for a database that
    has never run ``init_db()`` is what holds that decoration to degrading
    instead of raising.
    """
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, symbol TEXT,
            coin_name TEXT, amount REAL, average_buy_price REAL, notes TEXT,
            created_at TEXT, updated_at TEXT)""")
    conn.commit()
    conn.close()


# At import, not only in `main()`: pytest collects the test functions directly
# and never reaches the runner below.
alert_engine.ensure_alert_schema()
_schema()


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


_NEXT_USER = [9100]


def premium_user() -> int:
    _NEXT_USER[0] += 1
    _PREMIUM_USERS.add(_NEXT_USER[0])
    return _NEXT_USER[0]


def free_user() -> int:
    _NEXT_USER[0] += 1
    return _NEXT_USER[0]


_CLOCK = [0]


def hold(user_id, symbol, amount=1.0, basis=0.0):
    """Add a lot. ``created_at`` advances so ordering is deterministic — the
    reader sorts newest first and two rows written in the same second would
    otherwise fall back to insertion id in a way this suite should not rely on.
    """
    _CLOCK[0] += 1
    stamp = f"2026-08-23T00:00:{_CLOCK[0]:02d}"
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO portfolio_items (user_id, symbol, coin_name, amount,"
            " average_buy_price, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '', ?, ?)",
            (user_id, symbol.upper(), symbol.upper(), amount, basis, stamp, stamp),
        )
        conn.commit()
    finally:
        conn.close()


def sell_out_of(user_id, symbol):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM portfolio_items WHERE user_id=? AND symbol=?",
                    (user_id, symbol.upper()))
        conn.commit()
    finally:
        conn.close()


def make_rule(user_id, comparator="above", value=100.0, cooldown=0):
    result = alert_engine.create_alert_rule(
        user_id, alert_type="coin_price", channels={"push": True, "in_app": True},
        cooldown_seconds=cooldown, portfolio_scope=True,
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


def fired_symbols() -> list[str]:
    return [entry["symbol"] for entry in DISPATCHED]


def clear_dispatched():
    DISPATCHED.clear()


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------
def test_watching_your_portfolio_is_premium():
    """Enforced in the service, not the route. The worker, UNDX and the admin
    tools all create rules through this function, so a gate in one HTTP handler
    would be a gate on one door of several.

    Deliberately a *basic* rule, with no ``condition_spec``. A compound spec is
    separately Premium, so a test that sent one would pass with the portfolio
    gate deleted — it would be watching the wrong door close. Single-threshold
    rules are free and unlimited; the scope is the only thing being refused
    here.
    """
    user_id = free_user()
    hold(user_id, "BTC")
    result = alert_engine.create_alert_rule(
        user_id, alert_type="coin_price", condition="above", threshold=100.0,
        portfolio_scope=True,
    )
    check("refused", result.get("ok"), False)
    check("named the reason", result.get("code"), "premium_required")
    check("named the capability", result.get("capability"),
          premium_crypto_access.ADVANCED_ALERTS)

    # The same member, same threshold, without the scope: still free.
    allowed = alert_engine.create_alert_rule(
        user_id, alert_type="coin_price", symbol="BTC", condition="above", threshold=100.0,
    )
    check("a plain single-symbol rule is untouched", allowed.get("ok"), True)


def test_an_empty_portfolio_is_refused_at_creation():
    """Not created-and-silent. A rule that watches nothing looks active on the
    alerts screen and never fires, which reads as a broken alert rather than as
    an empty portfolio."""
    user_id = premium_user()
    result = alert_engine.create_alert_rule(
        user_id, portfolio_scope=True,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("refused", result.get("ok"), False)
    check("named the reason", result.get("code"), "portfolio_empty")


def test_a_portfolio_larger_than_the_cap_is_refused_not_trimmed():
    """A rule that quietly watches 25 of 40 holdings is one the member believes
    covers all 40, and they would only discover otherwise by not being told
    about the other fifteen."""
    user_id = premium_user()
    for index in range(alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS + 1):
        hold(user_id, f"C{index:03d}")
    result = alert_engine.create_alert_rule(
        user_id, portfolio_scope=True,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("refused", result.get("ok"), False)
    check("named the reason", result.get("code"), "portfolio_too_large")
    check("said the number once", str(alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS)
          in (result.get("message") or ""), True)


def test_a_rule_is_a_list_or_a_portfolio_never_both():
    """Refused rather than resolved by precedence. Both are a complete answer to
    "which assets", and honouring one silently would hand the member a rule
    watching a set they did not ask for."""
    user_id = premium_user()
    hold(user_id, "BTC")
    result = alert_engine.create_alert_rule(
        user_id, watchlist_id=1, portfolio_scope=True,
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("refused", result.get("ok"), False)
    check("named the reason", result.get("code"), "conflicting_scope")


def test_a_portfolio_rule_carries_no_ticker():
    """``_normalize_symbol`` defaults to BTC. A rule that watches everything you
    hold must not carry a symbol that says it watches Bitcoin — the alerts list,
    the notification copy and UNDX all read that column."""
    user_id = premium_user()
    hold(user_id, "ETH")
    set_prices(ETH=1.0)
    rule_id = make_rule(user_id)
    stored = load_rule(rule_id)
    check("stored symbol is blank", (stored.get("symbol") or ""), "")
    public = alert_engine._public_rule(dict(stored))
    check("public symbol is blank", public.get("asset_symbol"), "")
    check("flagged as a portfolio rule", public.get("is_portfolio_rule"), True)
    check("not flagged as a watchlist rule", public.get("is_watchlist_rule"), False)


# --------------------------------------------------------------------------
# Membership, which moves on its own
# --------------------------------------------------------------------------
def test_an_asset_bought_later_arms_before_it_can_fire():
    """The whole reason membership is read every cycle instead of frozen at
    creation — and the reason a new member starts with an empty latch. Buying
    DOGE today must not report a level it has been sitting at all week as a
    fresh crossing."""
    user_id = premium_user()
    hold(user_id, "BTC")
    set_prices(BTC=50.0, DOGE=900.0)
    rule_id = make_rule(user_id, value=100.0)
    observe(rule_id)
    clear_dispatched()

    hold(user_id, "DOGE")
    result = observe(rule_id)
    check("DOGE was evaluated", "DOGE" in (result.get("symbols") or []), True)
    check("but did not fire on its first sighting", fired_symbols(), [])

    result = observe(rule_id)
    check("and fires on the next observation", fired_symbols(), ["DOGE"])
    check("triggered", result.get("triggered"), True)


def test_an_asset_sold_out_of_stops_being_evaluated():
    user_id = premium_user()
    hold(user_id, "BTC")
    hold(user_id, "ETH")
    set_prices(BTC=50.0, ETH=50.0)
    rule_id = make_rule(user_id, value=100.0)
    first = observe(rule_id)
    check("both watched", sorted(first.get("symbols") or []), ["BTC", "ETH"])

    sell_out_of(user_id, "ETH")
    second = observe(rule_id)
    check("only the remaining holding", second.get("symbols"), ["BTC"])


def test_a_portfolio_that_grows_past_the_cap_after_creation_says_so():
    """Creation refuses an oversized portfolio, but a portfolio grows on a
    screen the alert has nothing to do with. The evaluation result is where that
    becomes visible; silently watching the first N would be the same quiet lie
    creation refuses to tell."""
    user_id = premium_user()
    hold(user_id, "BTC")
    # Every asset quotes. A missing quote is its own failure and would replace
    # the truncation notice in the result message, hiding the thing under test
    # behind an unrelated error.
    set_prices(BTC=1.0, **{f"D{index:03d}": 1.0
                           for index in range(alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS)})
    rule_id = make_rule(user_id, value=1000.0)
    check("not truncated yet", observe(rule_id).get("truncated"), False)

    for index in range(alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS):
        hold(user_id, f"D{index:03d}")
    result = observe(rule_id)
    check("truncated", result.get("truncated"), True)
    check("watched exactly the cap", len(result.get("symbols") or []),
          alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS)
    check("and said so", "grown past that" in (result.get("message") or ""), True)


def test_an_emptied_portfolio_is_a_quiet_no_op_not_an_error():
    """Selling everything is not a malfunction. Reporting `ok: False` would put
    the rule in the worker's error count and page somebody."""
    user_id = premium_user()
    hold(user_id, "BTC")
    set_prices(BTC=1.0)
    rule_id = make_rule(user_id, value=1000.0)
    observe(rule_id)

    sell_out_of(user_id, "BTC")
    result = observe(rule_id)
    check("ok", result.get("ok"), True)
    check("nothing triggered", result.get("triggered"), False)
    check("no symbols", result.get("symbols"), [])


# --------------------------------------------------------------------------
# Two lots of the same asset
# --------------------------------------------------------------------------
def test_two_lots_of_one_asset_are_evaluated_once():
    """`portfolio_items` has no uniqueness constraint on (user_id, symbol).
    Evaluating BTC twice in a cycle would race its own latch: the second pass
    reads the state the first just wrote and sees a crossing that never
    happened."""
    user_id = premium_user()
    hold(user_id, "BTC", amount=0.5)
    hold(user_id, "BTC", amount=1.5)
    set_prices(BTC=900.0)
    rule_id = make_rule(user_id, value=100.0)
    first = observe(rule_id)
    check("one symbol, not two", first.get("symbols"), ["BTC"])
    clear_dispatched()
    observe(rule_id)
    check("one notification for one crossing", fired_symbols(), ["BTC"])


def test_duplicate_lots_do_not_consume_the_cap():
    """Deduplicated before the slice, not by a SQL LIMIT. Twenty-six rows
    covering twenty-five assets is a portfolio inside the cap, and refusing it
    would tell the member to shrink something that is already small enough."""
    user_id = premium_user()
    for index in range(alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS):
        hold(user_id, f"E{index:03d}")
    hold(user_id, "E000", amount=3.0)  # a second lot of one they already hold
    gate = alert_engine.portfolio_rule_preflight(user_id)
    check("accepted", gate.get("ok"), True)
    check("distinct assets", len(gate.get("symbols") or []),
          alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS)


# --------------------------------------------------------------------------
# The line this feature must not cross
# --------------------------------------------------------------------------
def test_the_alert_reports_a_price_not_a_position():
    """`portfolio_items` holds an average buy price and no transaction ledger,
    so realized P/L is not computable from it and unrealized value is a
    different claim from the one an alert makes. The scope answers *which*
    assets; the market condition answers the rest.

    The fixture is chosen so the three wrong answers are all distinguishable:
    the market price (900), the position's value (7 x 900 = 6300) and its
    unrealized gain (6300 - 7 x 100 = 5600). Only the first is a number this
    system observed.
    """
    user_id = premium_user()
    hold(user_id, "BTC", amount=7.0, basis=100.0)
    set_prices(BTC=900.0)
    rule_id = make_rule(user_id, value=500.0)
    observe(rule_id)
    clear_dispatched()
    observe(rule_id)
    check("fired once", len(DISPATCHED), 1)
    check("on the market price", DISPATCHED[0]["observed_value"], 900.0)
    message = DISPATCHED[0]["message"] or ""
    for forbidden in ("6300", "5600", "6,300", "5,600"):
        check(f"says nothing about {forbidden}", forbidden in message, False)


def test_only_your_own_holdings_are_watched():
    """Scoped by user_id in the query, not filtered afterwards. There is no id
    to tamper with here — which is precisely why the scoping has to be pinned:
    nothing in the request would reveal that it had been dropped."""
    owner = premium_user()
    stranger = premium_user()
    hold(owner, "BTC")
    hold(stranger, "SOL")
    set_prices(BTC=900.0, SOL=900.0)
    rule_id = make_rule(owner, value=100.0)
    result = observe(rule_id)
    check("watched only the owner's holding", result.get("symbols"), ["BTC"])


# --------------------------------------------------------------------------
# The creation form and the command centre
# --------------------------------------------------------------------------
def test_the_command_center_creates_a_portfolio_rule():
    user_id = premium_user()
    hold(user_id, "BTC")
    conn = user_context.connect()
    try:
        result = command_center.create_alert(conn, user_id, {
            "portfolioScope": True, "condition": "above", "targetValue": 100,
        })
    finally:
        conn.close()
    check("created", result.get("ok"), True)
    check("reported the scope", result.get("portfolio_scope"), True)
    stored = load_rule(int(result["alert_id"]))
    check("stored the scope", int(stored.get("portfolio_scope") or 0), 1)
    check("and no ticker", (stored.get("symbol") or ""), "")


def test_a_symbol_sent_alongside_the_portfolio_does_not_win():
    """The client can send both. Only one of them can be what the rule means,
    and letting the symbol through would create a BTC alert the member believes
    covers everything they hold."""
    user_id = premium_user()
    hold(user_id, "ETH")
    conn = user_context.connect()
    try:
        result = command_center.create_alert(conn, user_id, {
            "portfolioScope": True, "assetSymbol": "BTC",
            "condition": "above", "targetValue": 100,
        })
    finally:
        conn.close()
    stored = load_rule(int(result["alert_id"]))
    check("no ticker survived", (stored.get("symbol") or ""), "")
    check("portfolio scope did", int(stored.get("portfolio_scope") or 0), 1)


def test_the_alerts_list_labels_a_portfolio_rule():
    """A portfolio rule has a blank symbol and no name to borrow. Without a
    label it renders as the same anonymous card as a watchlist rule."""
    user_id = premium_user()
    hold(user_id, "BTC")
    make_rule(user_id)
    conn = user_context.connect()
    try:
        alerts = command_center.list_alerts(conn, user_id)
    finally:
        conn.close()
    check("one alert", len(alerts), 1)
    check("labelled", alerts[0].get("scope_label"), "Your portfolio")


def test_the_creation_form_offers_the_portfolio_only_when_creation_would():
    """Eligibility comes from the very function creation runs. Offering a scope
    creation would refuse is worse than offering none: the member picks it,
    completes the form, and only then is turned away."""
    entitled = premium_user()
    hold(entitled, "BTC")
    conn = user_context.connect()
    try:
        options = command_center.alert_options(conn, entitled)
    finally:
        conn.close()
    check("eligible", options["portfolio"]["eligible"], True)
    check("named the assets", options["portfolio"]["symbols"], ["BTC"])
    check("published the cap", options["advanced"]["max_portfolio_symbols"],
          alert_engine.PORTFOLIO_RULE_MAX_SYMBOLS)


def test_the_creation_form_explains_an_empty_portfolio():
    """And distinguishes it from the Premium lock, because the two need opposite
    things from the member."""
    empty = premium_user()
    locked = free_user()
    hold(locked, "BTC")
    conn = user_context.connect()
    try:
        for_empty = command_center.alert_options(conn, empty)
        for_locked = command_center.alert_options(conn, locked)
    finally:
        conn.close()
    check("empty is not eligible", for_empty["portfolio"]["eligible"], False)
    check("and says why", for_empty["portfolio"]["reason"], "portfolio_empty")
    check("locked is not eligible", for_locked["portfolio"]["eligible"], False)
    check("and says why", for_locked["portfolio"]["reason"], "premium_required")


def test_an_unusable_portfolio_is_not_reported_as_thin_sampling():
    """The window block answers "how long have we been sampling these assets".
    With no assets to sample there is no coverage question, and answering as
    though there were would send the member to wait for a series that was never
    the problem."""
    user_id = premium_user()
    conn = user_context.connect()
    try:
        options = command_center.alert_options(conn, user_id, portfolio_scope=True)
    finally:
        conn.close()
    check("no windows", options["windows"], [])
    check("blamed the portfolio", options["window_reason"], "portfolio_empty")
    check("echoed the scope", options["portfolio_scope"], True)
    check("and claimed no symbol", options["symbol"], "")


TESTS = [
    test_watching_your_portfolio_is_premium,
    test_an_empty_portfolio_is_refused_at_creation,
    test_a_portfolio_larger_than_the_cap_is_refused_not_trimmed,
    test_a_rule_is_a_list_or_a_portfolio_never_both,
    test_a_portfolio_rule_carries_no_ticker,
    test_an_asset_bought_later_arms_before_it_can_fire,
    test_an_asset_sold_out_of_stops_being_evaluated,
    test_a_portfolio_that_grows_past_the_cap_after_creation_says_so,
    test_an_emptied_portfolio_is_a_quiet_no_op_not_an_error,
    test_two_lots_of_one_asset_are_evaluated_once,
    test_duplicate_lots_do_not_consume_the_cap,
    test_the_alert_reports_a_price_not_a_position,
    test_only_your_own_holdings_are_watched,
    test_the_command_center_creates_a_portfolio_rule,
    test_a_symbol_sent_alongside_the_portfolio_does_not_win,
    test_the_alerts_list_labels_a_portfolio_rule,
    test_the_creation_form_offers_the_portfolio_only_when_creation_would,
    test_the_creation_form_explains_an_empty_portfolio,
    test_an_unusable_portfolio_is_not_reported_as_thin_sampling,
]


def main() -> int:
    alert_engine.ensure_alert_schema()
    _schema()
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
