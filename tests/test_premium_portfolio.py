"""A portfolio may say "I don't know", and it may not guess.

Two defects lived in ``calculate_user_portfolio`` and they were the same defect
wearing different clothes: a missing number was written down as zero.

  * A holding whose live price did not arrive got ``value = 0`` and then
    ``pnl = value - cost``, i.e. **minus its entire cost basis**. An asset that
    had merely not been quoted was reported as a total loss, that loss was
    summed into the portfolio P/L, it could win the "top loser" slot, and
    ``save_snapshot`` wrote the short total into ``portfolio_snapshots`` — where
    it stays long after the outage that caused it, indistinguishable from a real
    drawdown.
  * A holding imported from the original CoinPilotX portfolio has an amount and
    no buy price. It was reported at exactly break-even, which is a claim about
    a cost basis that does not exist.

So the rule under test is: ``None`` means unknown, everywhere, and an unknown
never joins a total. The aggregates stay numeric — callers format them
directly — but they now sum only over the decidable subset, and ``valuation``
states what that subset was so no surface has to guess.

The second half of the file covers the gate. ``premium.crypto.portfolio`` was
granted on purchase and read by nothing; the free ceilings it is supposed to
lift were published to every client in the dashboard's ``limits`` block while
``_limit_check`` returned "allowed" unconditionally. The tests here pin both
halves of making that honest: the ceiling is enforced on *creation only* so an
account already over it loses nothing, and the limits the dashboard advertises
come from the same reader that refuses the add.

Run directly (no pytest required):

    python tests/test_premium_portfolio.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_portfolio_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import db  # noqa: E402
from services import portfolio_service as ps  # noqa: E402
from services import premium_crypto_access  # noqa: E402


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
#: Symbols the fake market can quote. Anything absent is unpriced, which is the
#: condition most of this file is about.
_PRICES: dict[str, float] = {}
_PREMIUM_USERS: set[int] = set()


def _fake_live_price(symbol):
    symbol = (symbol or "").upper()
    if symbol not in _PRICES:
        return None
    return {"symbol": symbol, "name": symbol, "price": _PRICES[symbol],
            "change_24h": 1.5, "volume_24h": 0, "market_cap": 0, "image": ""}


ps.get_live_price = _fake_live_price

# The entitlement decision is proven in tests/business_os/test_premium_crypto_access.py.
# Here it only has to be steerable, and it is reached through the module the
# service imports lazily, so patching the module attribute is enough.
premium_crypto_access.allowed_for_user_id = (
    lambda user_id, key: int(user_id or 0) in _PREMIUM_USERS
)


def _schema():
    conn = db.connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, symbol TEXT,
            coin_name TEXT, amount REAL, average_buy_price REAL, notes TEXT,
            created_at TEXT, updated_at TEXT)""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, symbol TEXT,
            coin_name TEXT, created_at TEXT, UNIQUE(user_id, symbol))""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            total_value REAL, total_cost REAL, pnl_value REAL, pnl_percent REAL,
            holdings_json TEXT, created_at TEXT)""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            event_type TEXT, event_label TEXT, metadata TEXT, created_at TEXT)""")
    conn.commit()
    conn.close()


_schema()

_NEXT_UID = [1000]


def _new_user(premium=False):
    """A fresh account id, so no test can inherit another's rows."""
    _NEXT_UID[0] += 1
    uid = _NEXT_UID[0]
    _PREMIUM_USERS.discard(uid)
    if premium:
        _PREMIUM_USERS.add(uid)
    return uid


def _hold(user_id, symbol, amount, basis=0.0):
    """Insert a holding directly, bypassing the ceiling under test."""
    conn = db.connect()
    conn.execute(
        "INSERT INTO portfolio_items (user_id, symbol, coin_name, amount,"
        " average_buy_price, notes, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, '', '2026-01-01', '2026-01-01')",
        (user_id, symbol.upper(), symbol.upper(), amount, basis),
    )
    conn.commit()
    conn.close()


def _snapshot_count(user_id):
    conn = db.connect()
    cur = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots WHERE user_id=?", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


# --------------------------------------------------------------------------
# Valuation: unknown is not zero
# --------------------------------------------------------------------------
def test_an_unpriced_holding_has_no_value_rather_than_a_value_of_zero():
    _PRICES.clear()
    uid = _new_user()
    _hold(uid, "GHOST", 5, basis=100.0)
    holding = ps.calculate_user_portfolio(uid)["holdings"][0]
    assert holding["price"] is None
    assert holding["value"] is None
    assert holding["priced"] is False


def test_an_unpriced_holding_is_not_reported_as_a_total_loss():
    """The headline defect. ``0 - cost`` is a 100% loss on an asset that has
    only failed to be quoted, and it used to reach the P/L, the percentage and
    the saved history."""
    _PRICES.clear()
    uid = _new_user()
    _hold(uid, "GHOST", 5, basis=100.0)   # 500 of cost basis
    result = ps.calculate_user_portfolio(uid)
    assert result["holdings"][0]["pnl_value"] is None
    assert result["pnl_value"] == 0.0
    assert result["pnl_percent"] == 0
    assert result["total_cost"] == 0.0


def test_totals_cover_the_priced_holdings_and_say_so():
    _PRICES.clear()
    _PRICES["BTC"] = 60000.0
    uid = _new_user()
    _hold(uid, "BTC", 2, basis=50000.0)   # value 120000, cost 100000
    _hold(uid, "GHOST", 5, basis=100.0)   # unpriced
    result = ps.calculate_user_portfolio(uid)
    assert result["total_value"] == 120000.0
    assert result["total_cost"] == 100000.0
    assert result["pnl_value"] == 20000.0
    valuation = result["valuation"]
    assert valuation["complete"] is False
    assert valuation["priced"] == 1
    assert valuation["unpriced"] == 1
    assert valuation["unpriced_symbols"] == ["GHOST"]


def test_the_warning_names_the_assets_the_total_leaves_out():
    """"Live price feed temporarily unavailable" told a member something was
    wrong but not that the number under it was short."""
    _PRICES.clear()
    _PRICES["BTC"] = 60000.0
    uid = _new_user()
    _hold(uid, "BTC", 1, basis=1.0)
    _hold(uid, "GHOST", 5, basis=100.0)
    warning = ps.calculate_user_portfolio(uid)["warning"]
    assert "GHOST" in warning
    assert "BTC" not in warning
    assert ps.calculate_user_portfolio(_new_user())["warning"] == ""


def test_an_unpriced_holding_cannot_win_top_loser():
    _PRICES.clear()
    _PRICES["BTC"] = 40000.0
    uid = _new_user()
    _hold(uid, "BTC", 1, basis=50000.0)   # a real -20%
    _hold(uid, "GHOST", 5, basis=100.0)   # unpriced, formerly -100%
    result = ps.calculate_user_portfolio(uid)
    assert result["top_loser"]["symbol"] == "BTC"
    assert result["top_gainer"]["symbol"] == "BTC"


def test_a_holding_with_no_cost_basis_reports_value_but_not_profit():
    """An imported holding has an amount and no buy price. Break-even would be
    a claim about a basis it does not have."""
    _PRICES.clear()
    _PRICES["DOGE"] = 0.25
    uid = _new_user()
    _hold(uid, "DOGE", 1000, basis=0.0)
    result = ps.calculate_user_portfolio(uid)
    holding = result["holdings"][0]
    assert holding["value"] == 250.0
    assert holding["cost"] is None
    assert holding["pnl_value"] is None
    assert holding["pnl_percent"] is None
    assert result["total_value"] == 250.0
    assert result["total_cost"] == 0.0
    assert result["valuation"]["basis_known"] == 0


# --------------------------------------------------------------------------
# History: no row beats a wrong row
# --------------------------------------------------------------------------
def test_an_incomplete_valuation_writes_no_snapshot():
    """A snapshot outlives the outage that produced it. A short total saved now
    is read back as a drawdown forever, with nothing left to contradict it."""
    _PRICES.clear()
    _PRICES["BTC"] = 60000.0
    uid = _new_user()
    _hold(uid, "BTC", 1, basis=50000.0)
    _hold(uid, "GHOST", 5, basis=100.0)
    ps.save_snapshot(uid, ps.calculate_user_portfolio(uid))
    assert _snapshot_count(uid) == 0


def test_a_complete_valuation_still_writes_one():
    _PRICES.clear()
    _PRICES["BTC"] = 60000.0
    uid = _new_user()
    _hold(uid, "BTC", 1, basis=50000.0)
    ps.save_snapshot(uid, ps.calculate_user_portfolio(uid))
    assert _snapshot_count(uid) == 1


def test_an_older_portfolio_shape_still_snapshots():
    """Callers that predate the valuation block keep working rather than
    silently never recording history again."""
    uid = _new_user()
    ps.save_snapshot(uid, {"total_value": 10, "total_cost": 5, "pnl_value": 5,
                           "pnl_percent": 100, "holdings": []})
    assert _snapshot_count(uid) == 1


# --------------------------------------------------------------------------
# The gate: premium.crypto.portfolio is read by something
# --------------------------------------------------------------------------
def test_a_free_account_may_add_up_to_the_advertised_ceiling():
    _PRICES.clear()
    uid = _new_user()
    for index in range(ps.FREE_LIMITS["holdings"]):
        assert ps.add_portfolio_item(uid, f"C{index}", amount=1)["ok"] is True
    refused = ps.add_portfolio_item(uid, "ONEMORE", amount=1)
    assert refused["ok"] is False
    assert refused["message"] == ps.LIMIT_MESSAGES["holdings"]


def test_the_refusal_names_premium_rather_than_a_bare_error():
    uid = _new_user()
    for index in range(ps.FREE_LIMITS["holdings"]):
        ps.add_portfolio_item(uid, f"D{index}", amount=1)
    assert "Premium" in ps.add_portfolio_item(uid, "NOPE", amount=1)["message"]


def test_a_ceiling_refusal_is_machine_readable_not_just_a_sentence():
    """A client has to tell "you have reached the free limit" apart from "that
    is not a symbol" without matching on English, or it cannot open the upgrade
    surface for the first and must not open it for the second. The shape mirrors
    the alert engine's capability denial so one client handler serves both."""
    uid = _new_user()
    for index in range(ps.FREE_LIMITS["holdings"]):
        ps.add_portfolio_item(uid, f"J{index}", amount=1)
    refused = ps.add_portfolio_item(uid, "OVER", amount=1)
    assert refused["code"] == ps.PREMIUM_REQUIRED
    assert refused["capability"] == premium_crypto_access.PORTFOLIO

    # A bad symbol is a different failure and must not offer an upgrade.
    assert ps.add_portfolio_item(_new_user(), "", amount=1).get("code") is None


def test_premium_has_no_ceiling():
    uid = _new_user(premium=True)
    for index in range(ps.FREE_LIMITS["holdings"] + 4):
        assert ps.add_portfolio_item(uid, f"E{index}", amount=1)["ok"] is True


def test_the_legacy_watchlist_is_not_capped():
    """PulseSoc has two watchlist systems. The one the native app uses and the
    one alert watchlist rules read is /api/crypto/watchlists, which has no size
    limit for anyone. Capping ``watchlist_items`` — the older table behind the
    web portfolio page — would tell one member two different numbers for "your
    watchlist" depending on which screen they opened."""
    uid = _new_user()
    for index in range(12):
        assert ps.add_watchlist_item(uid, f"W{index}")["ok"] is True
    assert ps._limit_check(uid, "watchlist") == (True, "", "")


def test_an_account_already_over_the_ceiling_keeps_everything_it_has():
    """The ceiling was advertised for a long time without being applied, so
    accounts exist above it. Enforcing on creation only means they lose nothing
    and can still read all of it — they simply cannot add a further one."""
    _PRICES.clear()
    uid = _new_user()
    for index in range(ps.FREE_LIMITS["holdings"] + 3):
        _hold(uid, f"F{index}", 1, basis=1.0)
    assert len(ps.calculate_user_portfolio(uid)["holdings"]) == ps.FREE_LIMITS["holdings"] + 3
    assert ps.add_portfolio_item(uid, "STILLNO", amount=1)["ok"] is False


def test_the_entitlement_lapsing_does_not_delete_anything():
    uid = _new_user(premium=True)
    for index in range(ps.FREE_LIMITS["holdings"] + 2):
        ps.add_portfolio_item(uid, f"G{index}", amount=1)
    _PREMIUM_USERS.discard(uid)
    assert len(ps.calculate_user_portfolio(uid)["holdings"]) == ps.FREE_LIMITS["holdings"] + 2
    assert ps.add_portfolio_item(uid, "AFTER", amount=1)["ok"] is False


def test_the_dead_legacy_alert_path_is_not_newly_restricted():
    """``create_price_alert`` writes to ``user_alerts``, which no live route
    reaches. Dropping its ceiling from FREE_LIMITS must leave it permitted, not
    accidentally refuse it."""
    assert ps._limit_check(_new_user(), "alerts") == (True, "", "")


def test_an_unresolvable_entitlement_does_not_lock_a_member_out():
    """This gate only ever removes a ceiling, so its two failure modes are not
    symmetric: failing open lets a free account add one more, failing closed
    tells a paying member they cannot touch their own portfolio."""
    original = premium_crypto_access.allowed_for_user_id

    def _explode(user_id, key):
        raise RuntimeError("entitlement backend down")

    premium_crypto_access.allowed_for_user_id = _explode
    try:
        assert ps.has_premium_portfolio(_new_user()) is True
    finally:
        premium_crypto_access.allowed_for_user_id = original


def test_the_gate_reads_the_portfolio_capability_key():
    """Not any premium key: the one the readiness registry now names as gated."""
    seen = []
    original = premium_crypto_access.allowed_for_user_id
    premium_crypto_access.allowed_for_user_id = lambda user_id, key: seen.append(key) or True
    try:
        ps.has_premium_portfolio(_new_user())
    finally:
        premium_crypto_access.allowed_for_user_id = original
    assert seen == [premium_crypto_access.PORTFOLIO]


def test_the_advertised_limits_come_from_the_gate_that_enforces_them():
    """A dashboard promising unlimited holdings while the add path refuses is
    the same split-brain this whole change exists to close."""
    free = ps._limit_check(_new_user(), "holdings")
    assert free == (True, "", "")  # an empty free account may still add

    uid = _new_user()
    for index in range(ps.FREE_LIMITS["holdings"]):
        _hold(uid, f"H{index}", 1, basis=1.0)
    assert ps._limit_check(uid, "holdings")[0] is False
    _PREMIUM_USERS.add(uid)
    assert ps._limit_check(uid, "holdings") == (True, "", "")


def test_the_readiness_registry_names_this_gate():
    """The registry entry and the code have to move together — that is the
    whole point of ``enforced_by``."""
    from services.business_os.entitlements import readiness as rd
    feature = rd.get(premium_crypto_access.PORTFOLIO)
    assert feature is not None
    assert feature.sellable is True
    assert feature.enforced_by == "services.portfolio_service._limit_check"


# --------------------------------------------------------------------------
# The line between what this store knows and what it may be sold as
#
# PulseSoc has two portfolio backends. `portfolio_items` — the one every live
# route reads — stores an amount and an average buy price and nothing else.
# `services/business_os/crypto` keeps an append-only transaction log with
# FIFO/average lots, which is the only place a *realized* gain could honestly
# come from, and it is dark behind BUSINESS_OS_CRYPTO with its routes 404.
#
# Keeping one live backend is settled. The hazard that outlives that decision is
# a promise that outruns the data: "tax lots", "trade history" and "realized
# P/L" are the three phrases somebody reaches for when writing marketing copy
# for a portfolio, and all three are unanswerable from an average basis. There
# is no amount of care in a valuation function that stops a benefits string, so
# the constraint is asserted here rather than left as a note.
# --------------------------------------------------------------------------
#: Phrases that require a transaction ledger to be true of a holding.
_UNBACKED_CLAIMS = ("realized", "realised", "tax lot", "cost basis history",
                    "trade history", "transaction history")


def test_nothing_advertises_a_number_this_store_cannot_produce():
    """No sellable entitlement may be *labelled* with a ledger claim.

    The label is what reaches a member. Notes are exempt on purpose — the
    portfolio note names all three phrases in order to forbid them, and a check
    that could not tell a prohibition from a promise would push the prohibition
    out of the file that needs it most.
    """
    from services.business_os.entitlements import readiness as rd
    offenders = [
        (feature["key"], feature["label"])
        for feature in rd.sellable_features()
        for claim in _UNBACKED_CLAIMS
        if claim in str(feature["label"]).lower()
    ]
    assert offenders == [], (
        f"These sellable labels promise a ledger that portfolio_items does not "
        f"have: {offenders}. Realized P/L becomes true by lighting up "
        f"services/business_os/crypto and reconciling the two stores, never by "
        f"deriving it from an average buy price."
    )


def test_the_portfolio_payload_states_only_unrealized_value():
    """A field named for a realized gain would be read as one.

    Every P/L key here is computed as live value minus average basis, which is
    an *unrealized* position and nothing else. The test pins the vocabulary, so
    that adding a realized figure has to be a deliberate act of naming rather
    than something that arrives by autocompletion next to `pnl_value`.
    """
    _PRICES.clear()
    _PRICES["BTC"] = 60000.0
    uid = _new_user()
    _hold(uid, "BTC", 1, basis=50000.0)
    portfolio = ps.calculate_user_portfolio(uid)
    keys = set(portfolio) | set(portfolio["holdings"][0])
    for key in keys:
        for claim in _UNBACKED_CLAIMS:
            assert claim.replace(" ", "_") not in key.lower(), (
                f"`{key}` names something portfolio_items cannot compute."
            )
    # And the P/L it does state is the unrealized one, still arithmetic.
    assert portfolio["holdings"][0]["pnl_value"] == 10000.0


def test_the_second_portfolio_backend_stays_dark_and_disjoint():
    """One live store, and the dark one does not quietly become a second.

    If `business_os/crypto` ever wrote to `portfolio_items`, the two would be
    one store with two cost-basis models and no owner. Asserting the table names
    are disjoint is cheap and catches the merge nobody announced.
    """
    from services.business_os.crypto import schema as bos_schema
    source = open(bos_schema.__file__, encoding="utf-8").read()
    for table in ps._LIMIT_TABLES.values():
        assert f" {table} " not in source and f"{table}(" not in source, (
            f"{table} is the live portfolio's table; the dark backend must not "
            f"share it."
        )
    # Dark by default: an unset flag is off, not on.
    assert bos_schema.FLAG_ENV == "BUSINESS_OS_CRYPTO"
    assert os.environ.get(bos_schema.FLAG_ENV) in (None, "", "0", "off", "false")


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")
            print(f"  FAIL  {name}: {exc}")
    print("\n%d failed / %d passed" % (
        len(failures), sum(1 for k in globals() if k.startswith("test_")) - len(failures)))
    sys.exit(1 if failures else 0)
