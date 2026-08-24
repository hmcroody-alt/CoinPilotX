"""What the alert creation form is allowed to offer, and why that is not a constant.

Two of the things the form offers are not properties of the product, they are
properties of *right now*: whether compound rules are available depends on the
account, and which time windows are answerable depends on how long the sampled
series has actually been watching this asset. A client-side list of either goes
stale the moment the truth moves, and the failure is silent — the member creates
a 24h rule against six minutes of history, the rule is permanently undecidable,
and nothing anywhere says so.

So the claim under test is narrow and specific: ``alert_options`` offers exactly
what ``market_observations.coverage`` reports it can answer, and exactly the
watchlists ``create_alert_rule`` would accept. Not a superset. The registry note
on ``premium.crypto.advanced_alerts`` makes that equality the precondition for
ever advertising time windows at all.

The two loops that matter:

  * offer -> create: every window and every watchlist this endpoint offers is
    then actually accepted by creation, so the form cannot lead a member into a
    refusal;
  * coverage -> offer: a window the series cannot answer is never offered, a
    stale series offers nothing, and a list is limited by its *worst* covered
    asset rather than its best.

Run directly (no pytest required):

    python tests/test_crypto_alert_options.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_alert_options_"), "test.db")
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
from services import crypto_alert_conditions as conditions  # noqa: E402
from services import dashboard_crypto_command_center as command_center  # noqa: E402
from services import market_observations  # noqa: E402
from services import premium_crypto_access  # noqa: E402
from services import user_context  # noqa: E402


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
_PREMIUM_USERS: set[int] = set()


def _entitled(user_id) -> bool:
    return int(user_id or 0) in _PREMIUM_USERS


# The entitlement decision itself is proven elsewhere; here it only has to be
# steerable, so both entry points are pointed at the same set. ``allowed`` takes
# the row ``load_user_row`` returns, which is why the stub row carries the id.
premium_crypto_access.load_user_row = lambda user_id: {"user_id": int(user_id or 0)}
premium_crypto_access.allowed = lambda row, key: _entitled((row or {}).get("user_id"))
premium_crypto_access.allowed_for_user_id = lambda user_id, key: _entitled(user_id)
alert_engine.premium_crypto_access = premium_crypto_access
command_center.premium_crypto_access = premium_crypto_access
# Creation reports which of the chosen channels the account has not set up, which
# needs the ``users`` table this hermetic database has no reason to carry.
alert_engine.channel_warnings = lambda user_id, channels: []

FAILURES: list[str] = []


def check(label: str, actual, expected):
    if actual == expected:
        print(f"  PASS  {label}")
        return
    FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    raise AssertionError(label)


_NEXT_USER = [9200]


def premium_user() -> int:
    _NEXT_USER[0] += 1
    _PREMIUM_USERS.add(_NEXT_USER[0])
    return _NEXT_USER[0]


def free_user() -> int:
    _NEXT_USER[0] += 1
    return _NEXT_USER[0]


def seed_series(symbol: str, span_minutes: int, newest_age_minutes: float = 0.0,
                samples: int = 6) -> None:
    """Give ``symbol`` a sampled history of a stated shape.

    Rows are written straight into the table rather than through ``record_board``
    because what is under test is how coverage is *read*, and controlling the
    span to the minute is the whole point of these cases.
    """
    market_observations.ensure_schema()
    now = datetime.now(timezone.utc)
    newest = now - timedelta(minutes=newest_age_minutes)
    oldest = now - timedelta(minutes=span_minutes)
    step = (newest - oldest) / max(1, samples - 1)
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        for index in range(samples):
            moment = oldest + step * index
            stamp = moment.replace(tzinfo=None).isoformat(timespec="seconds")
            cur.execute(
                "INSERT INTO market_observations "
                "(symbol, observed_at, provider_updated_at, price, change_24h, volume_24h, market_cap, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol.upper(), stamp, stamp, 100.0 + index, 1.0, 5000.0, 90000.0, "test"),
            )
        conn.commit()
    finally:
        conn.close()


def make_watchlist(user_id, *symbols, name="Test list") -> int:
    conn = user_context.connect()
    try:
        command_center.ensure_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO crypto_watchlists (user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, name, "2026-08-23T00:00:00", "2026-08-23T00:00:00"),
        )
        watchlist_id = cur.lastrowid
        for position, symbol in enumerate(symbols):
            cur.execute(
                "INSERT INTO crypto_watchlist_assets (watchlist_id, user_id, asset_symbol, position, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (watchlist_id, user_id, symbol.upper(), position, "2026-08-23T00:00:00"),
            )
        conn.commit()
    finally:
        conn.close()
    return watchlist_id


def options(user_id, **kwargs) -> dict:
    conn = user_context.connect()
    try:
        return command_center.alert_options(conn, user_id, **kwargs)
    finally:
        conn.close()


def offered_minutes(payload: dict) -> list[int]:
    return [window["minutes"] for window in payload["windows"]]


def watchlist_entry(payload: dict, watchlist_id: int) -> dict:
    return next((w for w in payload["watchlists"] if w["id"] == watchlist_id), {})


# --------------------------------------------------------------------------
# Windows: offered only when the series can answer them
# --------------------------------------------------------------------------
def test_only_windows_the_series_can_answer_are_offered():
    """The defect this endpoint exists to prevent. ``WINDOW_CHOICES`` says what the
    rule vocabulary can express; coverage says what has been observed. A form that
    renders the first as though it were the second sells a 24h window to an asset
    with 40 minutes of history."""
    user_id = premium_user()
    seed_series("AAA", span_minutes=40)
    payload = options(user_id, symbol="AAA")
    check("only the short windows", offered_minutes(payload), [15, 30])
    check("nothing is being explained away", payload["window_reason"], "")
    check("the vocabulary still holds the longer ones",
          conditions.WINDOW_CHOICES[:4], (15, 30, 60, 120))


def test_a_series_that_stopped_arriving_offers_nothing():
    """A long history is worthless if the newest reading is hours old: every window
    would be measured against a stale endpoint. Coverage reports this as stale and
    the form must offer nothing rather than the windows the span would suggest."""
    user_id = premium_user()
    seed_series("BBB", span_minutes=1440, newest_age_minutes=60)
    payload = options(user_id, symbol="BBB")
    check("no windows at all", offered_minutes(payload), [])
    check("and it says why", payload["window_reason"], "stale")
    check("naming the asset", payload["window_limited_by"], "BBB")


def test_an_asset_never_sampled_offers_nothing():
    user_id = premium_user()
    payload = options(user_id, symbol="ZZZ")
    check("no windows", offered_minutes(payload), [])
    check("distinct from stale", payload["window_reason"], "no_samples")
    check("and readable", "has not been sampled yet" in payload["window_message"], True)


def test_an_asset_sampled_only_briefly_says_so():
    """Fresh samples, but not enough history to span even the shortest window. That
    is a third state, and collapsing it into "no samples" would tell a member to
    wait for something that is already happening."""
    user_id = premium_user()
    seed_series("CCC", span_minutes=5, samples=4)
    payload = options(user_id, symbol="CCC")
    check("no windows yet", offered_minutes(payload), [])
    check("but the reason is time, not absence", payload["window_reason"], "too_new")


def test_no_asset_named_means_no_window_claim():
    """Coverage is per asset, so without one there is no honest window list. Naming
    the whole vocabulary here would be the exact over-offer this endpoint exists to
    stop, and the client must re-ask when the symbol changes."""
    user_id = premium_user()
    payload = options(user_id)
    check("no windows", offered_minutes(payload), [])
    check("stated as a prompt, not a failure", payload["window_reason"], "no_symbol")
    check("no asset claimed", payload["symbol"], "")


# --------------------------------------------------------------------------
# Windows across a watchlist: the intersection, not the best member
# --------------------------------------------------------------------------
def test_a_list_is_limited_by_its_worst_covered_asset():
    """One rule, many assets. A window only some of them can answer would leave the
    rest permanently undecidable while the rule looked healthy, so the offer is the
    intersection."""
    user_id = premium_user()
    seed_series("DDD", span_minutes=1440)
    seed_series("EEE", span_minutes=40)
    watchlist_id = make_watchlist(user_id, "DDD", "EEE")
    payload = options(user_id, watchlist_id=watchlist_id)
    check("intersection, not union", offered_minutes(payload), [15, 30])

    solo = options(user_id, symbol="DDD")
    check("the well-covered asset alone still offers more",
          offered_minutes(solo), [15, 30, 60, 120, 240, 360, 720, 1440])


def test_one_unsampled_asset_empties_the_whole_list():
    user_id = premium_user()
    seed_series("FFF", span_minutes=1440)
    watchlist_id = make_watchlist(user_id, "FFF", "GGG")
    payload = options(user_id, watchlist_id=watchlist_id)
    check("no window is safe for the list", offered_minutes(payload), [])
    check("the blocking asset is named", payload["window_limited_by"], "GGG")
    check("and identified as a list problem", "on this watchlist" in payload["window_message"], True)


def test_an_unusable_list_reports_its_own_reason_not_a_coverage_one():
    """An empty list has no coverage to report. Saying "not sampled long enough"
    would send the member to wait for data when what they need is to add an asset."""
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, name="Empty")
    payload = options(user_id, watchlist_id=watchlist_id)
    check("no windows", offered_minutes(payload), [])
    check("the reason is the list", payload["window_reason"], "watchlist_empty")
    check("with the list's own words", "Add at least one asset" in payload["window_message"], True)


# --------------------------------------------------------------------------
# Watchlists: offered exactly when creation would accept them
# --------------------------------------------------------------------------
def test_an_offered_list_is_one_creation_accepts():
    """The loop that matters. Eligibility here comes from the same preflight the
    gate runs, so an offered list cannot be refused at the end of the form."""
    user_id = premium_user()
    seed_series("HHH", span_minutes=1440)
    watchlist_id = make_watchlist(user_id, "HHH")
    entry = watchlist_entry(options(user_id, watchlist_id=watchlist_id), watchlist_id)
    check("offered", entry["eligible"], True)
    check("with its members", entry["symbols"], ["HHH"])

    created = alert_engine.create_alert_rule(
        user_id, watchlist_id=watchlist_id, channels={"push": True},
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("and creation agrees", created.get("ok"), True)


def test_an_empty_list_is_shown_but_not_offered():
    user_id = premium_user()
    watchlist_id = make_watchlist(user_id, name="Nothing here")
    entry = watchlist_entry(options(user_id), watchlist_id)
    check("still listed", entry["name"], "Nothing here")
    check("but not usable", entry["eligible"], False)
    check("for the reason creation gives", entry["reason"], "watchlist_empty")

    refused = alert_engine.create_alert_rule(
        user_id, watchlist_id=watchlist_id, channels={"push": True},
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "above", "value": 100.0}]},
    )
    check("and creation refuses it identically", refused.get("code"), "watchlist_empty")


def test_an_oversized_list_is_shown_but_not_offered():
    user_id = premium_user()
    symbols = [f"C{index:03d}" for index in range(alert_engine.WATCHLIST_RULE_MAX_SYMBOLS + 3)]
    watchlist_id = make_watchlist(user_id, *symbols, name="Too many")
    entry = watchlist_entry(options(user_id), watchlist_id)
    check("not usable", entry["eligible"], False)
    check("named as too large", entry["reason"], "watchlist_too_large")
    check("no partial member list is implied", entry["symbols"], [])


def test_another_members_list_is_not_visible_at_all():
    """Not "listed but ineligible" — absent. Naming somebody else's list would leak
    that it exists, and the ineligibility reason would leak why."""
    owner = premium_user()
    other = premium_user()
    seed_series("III", span_minutes=1440)
    watchlist_id = make_watchlist(owner, "III", name="Private")
    payload = options(other)
    check("not offered to the other member", watchlist_entry(payload, watchlist_id), {})
    check("and not named", [w["name"] for w in payload["watchlists"]].count("Private"), 0)


# --------------------------------------------------------------------------
# Free accounts see the shape of the thing, locked
# --------------------------------------------------------------------------
def test_a_free_account_gets_the_vocabulary_marked_locked():
    """Not an error and not an empty payload. The form shows what Premium adds; the
    server refuses it regardless of what the client chooses to render."""
    user_id = free_user()
    payload = options(user_id, symbol="AAA")
    check("answered", payload["ok"], True)
    check("not premium", payload["premium"], False)
    check("advanced is locked", payload["advanced"]["locked"], True)
    check("but described", len(payload["advanced"]["metrics"]), len(conditions.METRIC_FIELDS))
    check("basic stays open", payload["basic"]["locked"], False)
    check("and names the capability to unlock",
          payload["capability"], premium_crypto_access.ADVANCED_ALERTS)


def test_a_free_accounts_lists_are_locked_not_hidden():
    user_id = free_user()
    watchlist_id = make_watchlist(user_id, "JJJ", name="Mine")
    entry = watchlist_entry(options(user_id), watchlist_id)
    check("still their own list", entry["name"], "Mine")
    check("not usable", entry["eligible"], False)
    check("for the premium reason", entry["reason"], "premium_required")
    check("no members disclosed", entry["symbols"], [])


# --------------------------------------------------------------------------
# The vocabulary itself
# --------------------------------------------------------------------------
def test_windowable_metrics_are_marked_and_the_deltas_are_not():
    """A percent change of a percent change is not a quantity anybody means to ask
    about, so the form must not let one be built."""
    user_id = premium_user()
    payload = options(user_id, symbol="AAA")
    windowable = {m["key"] for m in payload["advanced"]["metrics"] if m["windowable"]}
    check("exactly the series-backed metrics", windowable, set(conditions.WINDOWABLE_METRICS))
    check("24h change is not windowable", "change_24h" in windowable, False)


def test_crossing_comparators_are_distinguished_from_level_ones():
    user_id = premium_user()
    payload = options(user_id, symbol="AAA")
    kinds = {c["key"]: c["kind"] for c in payload["advanced"]["comparators"]}
    check("all four offered", sorted(kinds), sorted(conditions.COMPARATORS))
    check("crosses_above is an edge", kinds["crosses_above"], "crossing")
    check("above is a level", kinds["above"], "level")


def test_a_window_offers_only_the_level_comparators():
    """A window's baseline advances every sample, so a crossing over one fires on
    the baseline moving rather than the market moving. Validation refuses that
    pairing; the form must not offer it and then be told off for it."""
    user_id = premium_user()
    seed_series("MMM", span_minutes=1440)
    payload = options(user_id, symbol="MMM")
    check("levels only", payload["advanced"]["window_comparators"], ["above", "below"])

    refused = alert_engine.create_alert_rule(
        user_id, symbol="MMM", channels={"push": True},
        condition_spec={"logic": "and", "clauses": [
            {"metric": "price", "comparator": "crosses_above", "value": 1.0,
             "window_minutes": 60}]},
    )
    check("and creation agrees it is not offerable", refused.get("ok"), False)


def test_the_basic_conditions_keep_a_stable_order():
    """Published as a list, so an unordered set here would reshuffle the buttons
    between requests."""
    user_id = free_user()
    payload = options(user_id)
    check("ordered", payload["basic"]["conditions"], list(command_center.BASIC_ALERT_CONDITIONS))
    check("and complete", set(payload["basic"]["conditions"]), set(command_center.ALERT_CONDITIONS))


def test_every_offered_window_is_one_creation_accepts():
    """The second loop: an offered window must survive validation. A window the form
    offers and the validator rejects is a dead end the member cannot get out of."""
    user_id = premium_user()
    seed_series("KKK", span_minutes=1440)
    payload = options(user_id, symbol="KKK")
    check("something to check", len(payload["windows"]) > 0, True)
    for window in payload["windows"]:
        created = alert_engine.create_alert_rule(
            user_id, symbol="KKK", channels={"push": True},
            condition_spec={"logic": "and", "clauses": [
                {"metric": "price", "comparator": "above", "value": 1.0,
                 "window_minutes": window["minutes"]}]},
        )
        check(f"{window['label']} accepted", created.get("ok"), True)


def test_coverage_detail_is_reported_per_asset():
    """The form needs to be able to say "1 more hour of data unlocks the 2h window",
    which needs the span, not just the verdict."""
    user_id = premium_user()
    seed_series("LLL", span_minutes=90)
    payload = options(user_id, symbol="LLL")
    detail = payload["window_coverage"][0]
    check("named", detail["symbol"], "LLL")
    check("with a span", detail["span_minutes"] >= 89, True)
    check("and a sample count", detail["sample_count"] > 0, True)
    check("and fresh", detail["stale"], False)


TESTS = [
    test_only_windows_the_series_can_answer_are_offered,
    test_a_series_that_stopped_arriving_offers_nothing,
    test_an_asset_never_sampled_offers_nothing,
    test_an_asset_sampled_only_briefly_says_so,
    test_no_asset_named_means_no_window_claim,
    test_a_list_is_limited_by_its_worst_covered_asset,
    test_one_unsampled_asset_empties_the_whole_list,
    test_an_unusable_list_reports_its_own_reason_not_a_coverage_one,
    test_an_offered_list_is_one_creation_accepts,
    test_an_empty_list_is_shown_but_not_offered,
    test_an_oversized_list_is_shown_but_not_offered,
    test_another_members_list_is_not_visible_at_all,
    test_a_free_account_gets_the_vocabulary_marked_locked,
    test_a_free_accounts_lists_are_locked_not_hidden,
    test_windowable_metrics_are_marked_and_the_deltas_are_not,
    test_crossing_comparators_are_distinguished_from_level_ones,
    test_a_window_offers_only_the_level_comparators,
    test_the_basic_conditions_keep_a_stable_order,
    test_every_offered_window_is_one_creation_accepts,
    test_coverage_detail_is_reported_per_asset,
]


def main() -> int:
    alert_engine.ensure_alert_schema()
    market_observations.ensure_schema()
    for test in TESTS:
        print(f"\n{test.__name__}")
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
