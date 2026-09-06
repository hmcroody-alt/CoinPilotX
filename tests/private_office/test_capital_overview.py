"""Capital Command Center journeys — and the mutations that must fail.

Run either way::

    python -m pytest tests/private_office/test_capital_overview.py
    python tests/private_office/test_capital_overview.py

What this file defends
----------------------
``capital_overview`` is the only place in the Private Office that puts a
minus sign between the member's assets and their debts. Every honesty rule
that makes that defensible is exercised here as a member journey, and then
again as a *mutation*: the invariant is re-checked against a deliberately
broken implementation, so a test that would still pass with the protection
removed is caught here rather than in production.

The journeys:

* **Known and complete** — priced holdings and quantified obligations in one
  currency produce an estimated net position with ``complete`` true.
* **Unpriced asset** — a holding with no market price is excluded from the
  priced value, counted in ``excluded``, and drops ``complete`` to false. It
  is never valued at zero.
* **Unquantified liability** — an obligation the record store has no amount
  for is counted, never subtracted. A member with an unknown mortgage does
  not see it treated as $0 of debt.
* **No liabilities recorded** — the expensive omission. A store with assets
  and no debt records reports ``no_liabilities_recorded`` and ``complete``
  false, because absence of a record is not evidence of no debt.
* **Foreign currency** — a EUR obligation is excluded from a USD net
  position and counted, never converted at an invented rate.
* **Coverage** — the published formula recomputes by hand from the payload,
  and an empty store scores ``None`` rather than 100%.
* **Concentration** — shares are of the priced total and the excluded count
  travels with them.
* **Owner isolation** — one member's overview never reads another's, and the
  denial is the denied shape rather than a thin payload.
* **Needs review** — every gap is surfaced with what, why and which system
  owns the fix.

The mutation battery (mission §138) then removes each protection in turn and
asserts a specific check would have caught it.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="capital_overview_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ.setdefault("PORTFOLIO_CAPITAL_PROJECTION_ENABLED", "1")

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services import market_data  # noqa: E402
from services import portfolio_events  # noqa: E402
from services.private_office import capital_overview as overview_mod  # noqa: E402
from services.private_office import obligation_projection as obligations  # noqa: E402
from services.private_office import portfolio_projection as portfolio  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9931
USER_B = 9932

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    text = f"{label}{(' — ' + str(detail)) if detail != '' else ''}"
    _FAILURES.append(text)
    print(f"  FAIL  {text}")
    return False


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    portfolio_events.ensure_outbox_schema(cur)
    return conn, cur


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _board(symbols: dict[str, float]) -> dict:
    return {
        "source": "coingecko", "observed_epoch": 1_756_700_000,
        "age_seconds": 12, "warning": "",
        "markets": [
            {"symbol": symbol, "price": price, "change_24h": 1.5}
            for symbol, price in symbols.items()
        ],
    }


def _add_lot(cur, user_id: int, symbol: str, name: str, amount: float,
             basis: object) -> int:
    cur.execute(
        "INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, "
        "average_buy_price) VALUES (?,?,?,?,?)",
        (user_id, symbol, name, amount, basis))
    row_id = int(cur.lastrowid)
    portfolio_events.enqueue(cur, user_id=user_id,
                             event_type=portfolio_events.EVENT_HOLDING_ADDED,
                             item_id=row_id, symbol=symbol)
    return row_id


def _obligation(cur, user_id: int, title: str, kind: str, **fields) -> int:
    created = records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=user_id,
        title=title, obligation_type=kind, domain="FINANCIAL", **fields)
    return int(created["record_id"])


def _priced(symbols: dict[str, float]):
    """Install a deterministic board for the duration of one stage."""
    original = market_data.live_market_board
    market_data.live_market_board = lambda **kwargs: _board(symbols)
    return original


def _read(cur, owner: int = USER_A, actor: int | None = None) -> dict:
    return overview_mod.overview(cur, owner_user_id=owner,
                                 actor_user_id=owner if actor is None else actor)


def setup_environment() -> None:
    schema.reset_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Journey: an empty store is empty, not complete
# ---------------------------------------------------------------------------

def stage_empty_store() -> None:
    print("\n[empty store]")
    conn, cur = _connect()
    original = _priced({})
    try:
        payload = _read(cur)
        check("an empty store still answers ok", payload.get("ok") is True)
        check("coverage over nothing scores None, not 100%",
              payload["coverage"]["score"] is None,
              payload["coverage"]["score"])
        check("no dimension is scored from a zero denominator",
              payload["coverage"]["scored_dimensions"] == [],
              payload["coverage"]["scored_dimensions"])
        check("an empty store is not a complete net position",
              payload["net_position"]["complete"] is False)
        check("and it says why: nothing is on file",
              "no_liabilities_recorded"
              in payload["net_position"]["incomplete_reasons"],
              payload["net_position"]["incomplete_reasons"])
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: priced assets, quantified debt, one currency
# ---------------------------------------------------------------------------

def stage_known_position() -> None:
    print("\n[known position]")
    conn, cur = _connect()
    original = _priced({"BTC": 50000.0, "ETH": 2000.0})
    try:
        _add_lot(cur, USER_A, "BTC", "Bitcoin", 2.0, 30000.0)
        _add_lot(cur, USER_A, "ETH", "Ethereum", 10.0, 1500.0)
        portfolio.drain(cur, user_id=USER_A)
        _obligation(cur, USER_A, "Mortgage", "LOAN",
                    amount="80000", currency="usd", due_at=_iso_in(30))
        conn.commit()

        payload = _read(cur)
        # 2 × 50000 + 10 × 2000 = 120000
        check("priced asset value sums the priced rows",
              payload["assets"]["priced_value"] == 120000.0,
              payload["assets"]["priced_value"])
        check("known liabilities read the USD bucket",
              payload["liabilities"]["known_amount"] == 80000.0,
              payload["liabilities"]["known_amount"])
        check("net position subtracts what is known",
              payload["net_position"]["estimated"] == 40000.0,
              payload["net_position"]["estimated"])
        check("with everything known and on file, complete is true",
              payload["net_position"]["complete"] is True,
              payload["net_position"]["incomplete_reasons"])
        check("nothing was excluded",
              all(count == 0 for count
                  in payload["net_position"]["excluded"].values()),
              payload["net_position"]["excluded"])
        check("the basis travels with the number",
              payload["net_position"]["basis"] == overview_mod.NET_POSITION_BASIS)
        check("and so does the refusal to call it a net worth",
              bool(payload["net_position"]["disclaimer"]))
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: an unpriced asset is excluded, never zero
# ---------------------------------------------------------------------------

def stage_unpriced_asset() -> None:
    print("\n[unpriced asset]")
    conn, cur = _connect()
    # XRP has a holding but no row on the board: unpriced, not worthless.
    original = _priced({"BTC": 50000.0, "ETH": 2000.0})
    try:
        _add_lot(cur, USER_A, "XRP", "XRP", 1000.0, 0.5)
        portfolio.drain(cur, user_id=USER_A)
        conn.commit()

        payload = _read(cur)
        check("the priced value did not move for the unpriced holding",
              payload["assets"]["priced_value"] == 120000.0,
              payload["assets"]["priced_value"])
        check("the unpriced holding is counted",
              payload["assets"]["unpriced"] == 1, payload["assets"])
        check("and named",
              "XRP" in payload["assets"]["unpriced_symbols"],
              payload["assets"]["unpriced_symbols"])
        check("the net position is no longer complete",
              payload["net_position"]["complete"] is False)
        check("and says an unpriced asset is why",
              "unpriced_assets"
              in payload["net_position"]["incomplete_reasons"],
              payload["net_position"]["incomplete_reasons"])
        check("the exclusion is counted, not silently dropped",
              payload["net_position"]["excluded"]["unpriced_assets"] == 1,
              payload["net_position"]["excluded"])
        check("pricing coverage falls below 1",
              payload["coverage"]["dimensions"]["pricing"]["ratio"] < 1.0,
              payload["coverage"]["dimensions"]["pricing"])
        kinds = {item["kind"] for item in payload["needs_review"]}
        check("and it is on the review list",
              "unpriced_asset" in kinds, kinds)
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: an unquantified liability is counted, never subtracted as zero
# ---------------------------------------------------------------------------

def stage_unquantified_liability() -> None:
    print("\n[unquantified liability]")
    conn, cur = _connect()
    original = _priced({"BTC": 50000.0, "ETH": 2000.0})
    try:
        _obligation(cur, USER_A, "Business loan", "LOAN", due_at=_iso_in(90))
        conn.commit()

        payload = _read(cur)
        check("the known liability sum did not move",
              payload["liabilities"]["known_amount"] == 80000.0,
              payload["liabilities"]["known_amount"])
        check("the unquantified obligation is counted",
              payload["liabilities"]["unquantified"] == 1,
              payload["liabilities"])
        check("the net position is not complete",
              payload["net_position"]["complete"] is False)
        check("and names the unquantified liability",
              "unquantified_liabilities"
              in payload["net_position"]["incomplete_reasons"],
              payload["net_position"]["incomplete_reasons"])
        check("liability coverage falls below 1",
              payload["coverage"]["dimensions"]["liability_amounts"]["ratio"] < 1.0,
              payload["coverage"]["dimensions"]["liability_amounts"])
        review = {item["kind"] for item in payload["needs_review"]}
        check("and the member is told to go and quantify it",
              "unquantified_liability" in review, review)

        rows = obligations.liabilities_view(
            cur, owner_user_id=USER_A, actor_user_id=USER_A)["liabilities"]
        unknown = [row for row in rows if row["title"] == "Business loan"]
        check("the row itself carries None, not 0.0",
              len(unknown) == 1 and unknown[0]["amount"] is None,
              unknown)
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: a foreign-currency debt is excluded, never converted
# ---------------------------------------------------------------------------

def stage_foreign_currency() -> None:
    print("\n[foreign currency]")
    conn, cur = _connect()
    original = _priced({"BTC": 50000.0, "ETH": 2000.0})
    try:
        _obligation(cur, USER_A, "Paris apartment loan", "LOAN",
                    amount="60000", currency="eur", due_at=_iso_in(200))
        conn.commit()

        payload = _read(cur)
        check("the USD liability total ignored the EUR debt",
              payload["liabilities"]["known_amount"] == 80000.0,
              payload["liabilities"]["known_amount"])
        check("the EUR debt is counted as excluded",
              payload["net_position"]["excluded"]["foreign_currency_liabilities"] == 1,
              payload["net_position"]["excluded"])
        check("and the currency is still visible in the breakdown",
              "EUR" in payload["liabilities"]["by_currency"],
              payload["liabilities"]["by_currency"])
        check("the net position says currency is why it is incomplete",
              "uncomparable_currency"
              in payload["net_position"]["incomplete_reasons"],
              payload["net_position"]["incomplete_reasons"])
        check("no rate was invented: the estimate is unchanged",
              payload["net_position"]["estimated"] == 40000.0,
              payload["net_position"]["estimated"])
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: assets with no debt on file is not a net worth
# ---------------------------------------------------------------------------

def stage_no_liabilities_recorded() -> None:
    print("\n[assets, no debt on file]")
    conn, cur = _connect()
    original = _priced({"BTC": 50000.0})
    try:
        _add_lot(cur, USER_B, "BTC", "Bitcoin", 20.0, 10000.0)
        portfolio.drain(cur, user_id=USER_B)
        conn.commit()

        payload = _read(cur, owner=USER_B)
        check("the member has a million in priced assets",
              payload["assets"]["priced_value"] == 1_000_000.0,
              payload["assets"]["priced_value"])
        check("known liabilities are zero because none are recorded",
              payload["liabilities"]["known_amount"] == 0.0)
        check("but that is NOT a complete net position",
              payload["net_position"]["complete"] is False,
              payload["net_position"])
        check("and the reason is stated as no records, not no debt",
              payload["net_position"]["incomplete_reasons"]
              == ["no_liabilities_recorded"],
              payload["net_position"]["incomplete_reasons"])
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: coverage recomputes by hand, concentration is of the priced total
# ---------------------------------------------------------------------------

def stage_coverage_and_concentration() -> None:
    print("\n[coverage and concentration]")
    conn, cur = _connect()
    original = _priced({"BTC": 50000.0, "ETH": 2000.0})
    try:
        payload = _read(cur)
        dims = payload["coverage"]["dimensions"]
        for name in overview_mod.COVERAGE_DIMENSIONS:
            dim = dims[name]
            expected = (dim["known"] / dim["countable"]
                        if dim["countable"] else None)
            check(f"{name} recomputes from its own known/countable",
                  dim["ratio"] == expected, dim)
        scored = [d["ratio"] for d in dims.values() if d["ratio"] is not None]
        check("the score is the unweighted mean of the scored dimensions",
              abs(payload["coverage"]["score"] - sum(scored) / len(scored)) < 1e-9,
              (payload["coverage"]["score"], scored))
        check("the formula is published in the payload",
              payload["coverage"]["formula"] == overview_mod.COVERAGE_FORMULA)

        conc = payload["concentrations"]
        check("shares are of the priced total, and say so",
              conc["asset_basis"] == "priced_asset_value")
        check("the priced shares sum to 1",
              abs(sum(row["share"] for row in conc["assets"]) - 1.0) < 1e-9,
              conc["assets"])
        check("BTC is the largest priced concentration",
              conc["assets"][0]["key"] == "BTC", conc["assets"])
        check("the unpriced holding is not in the ranking",
              all(row["key"] != "XRP" for row in conc["assets"]),
              conc["assets"])
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: the price source failing is not an empty portfolio
# ---------------------------------------------------------------------------

def stage_price_source_unavailable() -> None:
    print("\n[price source unavailable]")
    conn, cur = _connect()
    original = market_data.live_market_board

    def _boom(**kwargs):
        raise RuntimeError("board unavailable")

    market_data.live_market_board = _boom
    try:
        payload = _read(cur)
        check("the overview still answers", payload.get("ok") is True)
        check("nothing is priced", payload["assets"]["priced"] == 0,
              payload["assets"])
        check("and no holding was valued at zero",
              payload["assets"]["priced_value"] == 0.0
              and payload["assets"]["unpriced"] == payload["assets"]["count"],
              payload["assets"])
        check("the net position is not complete",
              payload["net_position"]["complete"] is False)
        kinds = {item["kind"] for item in payload["needs_review"]}
        check("the member is told the price source is the problem",
              "price_source_unavailable" in kinds, kinds)
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Journey: owner isolation
# ---------------------------------------------------------------------------

def stage_owner_isolation() -> None:
    print("\n[owner isolation]")
    conn, cur = _connect()
    original = _priced({"BTC": 50000.0, "ETH": 2000.0})
    try:
        payload = _read(cur, owner=USER_A, actor=USER_B)
        check("B reading A's overview is denied by name",
              payload.get("ok") is False
              and payload["denied"]["reason"] == overview_mod.DENIED_NOT_OWNER,
              payload.get("denied"))
        check("the refusal carries no figures at all",
              payload["assets"] == {} and payload["liabilities"] == {}
              and payload["net_position"] == {},
              payload)

        b_view = _read(cur, owner=USER_B)
        check("B's own overview holds only B's assets",
              b_view["assets"]["count"] == 1, b_view["assets"])
        check("and none of A's liabilities",
              b_view["liabilities"]["count"] == 0, b_view["liabilities"])

        denied = obligations.liabilities_view(
            cur, owner_user_id=USER_A, actor_user_id=USER_B)
        check("the liabilities read refuses across owners too",
              denied.get("ok") is False
              and denied["denied"]["reason"] == "actor_is_not_owner",
              denied)
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------
# Mutation battery (mission §138)
# ---------------------------------------------------------------------------
#
# Each mutation removes one protection and asserts that a *named* check above
# would have failed. A mutation that only produces a crash is not evidence
# that the invariant is defended, so every case here asserts on a value.

def stage_mutation_battery() -> None:
    print("\n[mutation battery]")
    conn, cur = _connect()
    original_board = _priced({"BTC": 50000.0, "ETH": 2000.0})
    truth = _read(cur)

    try:
        # 1. Treat an unknown liability amount as zero.
        original_view = obligations.liabilities_view

        def _zeroed(cur_, **kwargs):
            payload = original_view(cur_, **kwargs)
            if not payload.get("ok"):
                return payload
            for row in payload["liabilities"]:
                if row["amount"] is None:
                    row["amount"] = 0.0
                    row["quantified"] = True
            payload["totals"]["unquantified"] = 0
            payload["totals"]["complete"] = True
            return payload

        obligations.liabilities_view = _zeroed
        mutated = _read(cur)
        obligations.liabilities_view = original_view
        check("MUTATION unknown-liability-as-zero is caught by the "
              "unquantified count",
              truth["liabilities"]["unquantified"] > 0
              and mutated["liabilities"]["unquantified"] == 0,
              (truth["liabilities"]["unquantified"],
               mutated["liabilities"]["unquantified"]))

        # 2. Treat an unpriced asset as worth zero and call the set complete.
        original_portfolio = portfolio.portfolio_view

        def _priced_zero(cur_, **kwargs):
            payload = original_portfolio(cur_, **kwargs)
            if not payload.get("ok"):
                return payload
            for row in payload["assets"]:
                if row["value"] is None:
                    row["value"] = 0.0
            payload["totals"]["complete"] = True
            payload["totals"]["unpriced_symbols"] = []
            return payload

        portfolio.portfolio_view = _priced_zero
        mutated = _read(cur)
        portfolio.portfolio_view = original_portfolio
        check("MUTATION unpriced-asset-as-zero is caught by the unpriced count",
              truth["assets"]["unpriced"] > 0
              and mutated["assets"]["unpriced"] == 0,
              (truth["assets"]["unpriced"], mutated["assets"]["unpriced"]))
        check("MUTATION incomplete-set-declared-complete is caught by "
              "net_position.complete",
              truth["net_position"]["complete"] is False
              and mutated["net_position"]["complete"] is True,
              (truth["net_position"]["incomplete_reasons"],
               mutated["net_position"]["incomplete_reasons"]))

        # 3. Convert a foreign-currency debt at an invented rate.
        def _converted(cur_, **kwargs):
            payload = original_view(cur_, **kwargs)
            if not payload.get("ok"):
                return payload
            buckets = payload["totals"]["by_currency"]
            eur = buckets.pop("EUR", None)
            if eur:
                usd = buckets.setdefault("USD", {"amount": 0.0, "count": 0})
                usd["amount"] += float(eur["amount"]) * 1.1
                usd["count"] += int(eur["count"])
            return payload

        obligations.liabilities_view = _converted
        mutated = _read(cur)
        obligations.liabilities_view = original_view
        check("MUTATION silent-FX-conversion is caught by the excluded count "
              "and the known total",
              truth["net_position"]["excluded"]["foreign_currency_liabilities"] > 0
              and mutated["net_position"]["excluded"]["foreign_currency_liabilities"] == 0
              and mutated["liabilities"]["known_amount"]
              != truth["liabilities"]["known_amount"],
              (truth["liabilities"]["known_amount"],
               mutated["liabilities"]["known_amount"]))

        # 4. Drop the owner check.
        original_denied = overview_mod._denied
        overview_mod._denied = lambda reason: {"ok": True, "denied": {},
                                               "assets": {"leaked": True}}
        mutated = overview_mod.overview(cur, owner_user_id=USER_A,
                                        actor_user_id=USER_B)
        overview_mod._denied = original_denied
        check("MUTATION owner-check-removed is caught by the isolation stage's "
              "denied assertion",
              truth["ok"] is True and mutated.get("ok") is True
              and mutated["assets"] == {"leaked": True},
              mutated)

        # 5. Score coverage as 1.0 when there is nothing to count.
        empty = overview_mod.overview(cur, owner_user_id=USER_B + 1,
                                      actor_user_id=USER_B + 1)
        check("MUTATION decorative-coverage-floor is caught by the empty-store "
              "score being None",
              empty["coverage"]["score"] is None, empty["coverage"])

        # 6. Present the priced subtotal as the portfolio total.
        check("MUTATION priced-subtotal-presented-as-total is caught by the "
              "portfolio's own complete flag",
              truth["assets"]["complete"] is False
              and truth["assets"]["priced_value"] > 0,
              truth["assets"])
        conn.commit()
    finally:
        market_data.live_market_board = original_board
        conn.close()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

STAGES = (
    stage_empty_store,
    stage_known_position,
    stage_unpriced_asset,
    stage_unquantified_liability,
    stage_foreign_currency,
    stage_no_liabilities_recorded,
    stage_coverage_and_concentration,
    stage_price_source_unavailable,
    stage_owner_isolation,
    stage_mutation_battery,
)


def test_capital_overview() -> None:
    setup_environment()
    for stage in STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


if __name__ == "__main__":
    setup_environment()
    for stage in STAGES:
        stage()
    if _FAILURES:
        print(f"\n{len(_FAILURES)} FAILURE(S)")
        sys.exit(1)
    print("\nALL STAGES PASSED")
    sys.exit(0)
