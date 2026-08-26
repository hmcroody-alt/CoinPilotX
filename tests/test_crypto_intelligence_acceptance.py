"""Acceptance: the seams between the crypto subsystems, rather than the subsystems.

Every part of this feature already has a suite that proves it correct in
isolation, and each of those suites necessarily stubs the parts either side of
it. tests/test_market_observations.py calls ``record_board`` itself;
tests/test_crypto_alert_advanced_rules.py stubs ``window_reading``;
tests/test_premium_portfolio.py stubs the entitlement. That is the right way to
test a module, and it leaves a specific shape of bug invisible: one where every
module is correct and the *wiring between them* is not.

Those bugs are the dangerous ones here, because none of them announces itself.
A window alert whose series stopped being written does not error — it goes
undecidable, which is the same quiet non-answer it correctly gives for a window
that is genuinely too young. The subscription screen keeps selling it. The
creation form keeps offering fewer and fewer windows until it offers none, which
reads exactly like a new asset. Nothing turns red anywhere.

So this file asserts the joins:

  * **The series has one writer, that writer runs, and it writes before the
    sweep reads.** Three separate ways for time windows to die silently.
  * **A lapsed subscription does not retroactively break what it sold.** The
    portfolio settled this policy explicitly (see
    ``test_the_entitlement_lapsing_does_not_delete_anything``); alerts arrive at
    the same behaviour by having no evaluation-time gate at all, which is a very
    different thing from having decided it.

Run directly (no pytest required):

    python tests/test_crypto_intelligence_acceptance.py
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_crypto_acceptance_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
for _key in (
    "WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY",
    "FCM_SERVER_KEY", "FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY",
    "APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_BUNDLE_ID",
    "BREVO_API_KEY", "BREVO_SMS_API_KEY", "TELEGRAM_BOT_TOKEN",
):
    os.environ.pop(_key, None)

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from services import alert_engine  # noqa: E402
from services import premium_crypto_access  # noqa: E402
from services import user_context  # noqa: E402


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
DISPATCHED: list[dict] = []
_MARKET: dict[str, float | None] = {}
_PREMIUM_USERS: set[int] = set()


def _fake_quote(symbol, *args, **kwargs):
    if _MARKET.get("price") is None:
        return {"ok": False, "asset": {}, "message": "quote unavailable"}
    return {"ok": True, "source": "test", "stale": False,
            "asset": {"symbol": str(symbol or "").upper(),
                      "price": _MARKET.get("price"),
                      "change_24h": _MARKET.get("change_24h"),
                      "price_change_24h": _MARKET.get("change_24h"),
                      "volume_24h": _MARKET.get("volume_24h"),
                      "market_cap": _MARKET.get("market_cap")}}


def _fake_dispatch(event, rule=None):
    DISPATCHED.append({"symbol": event.get("symbol"), "message": event.get("message")})
    return {"ok": True, "channels": {"push": "sent"}}


alert_engine.live_market_service.get_crypto_quote = _fake_quote
alert_engine.dispatch_alert_event = _fake_dispatch
alert_engine.channel_warnings = lambda user_id, channels: []

# Both seams, for the reason tests/test_crypto_alert_portfolio_rules.py gives:
# creation asks by id and the options endpoint asks about an already-loaded row.
premium_crypto_access.load_user_row = lambda user_id: {"user_id": int(user_id or 0)}
premium_crypto_access.allowed = lambda row, key: int((row or {}).get("user_id") or 0) in _PREMIUM_USERS
premium_crypto_access.allowed_for_user_id = lambda user_id, key: int(user_id or 0) in _PREMIUM_USERS

alert_engine.ensure_alert_schema()

FAILURES: list[str] = []


def check(label: str, actual, expected):
    if actual == expected:
        print(f"  PASS  {label}")
        return
    FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    raise AssertionError(label)


def set_market(**fields):
    _MARKET.clear()
    _MARKET.update(fields)


_NEXT_USER = [9600]


def premium_user() -> int:
    _NEXT_USER[0] += 1
    _PREMIUM_USERS.add(_NEXT_USER[0])
    return _NEXT_USER[0]


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


def make_advanced_rule(user_id, *clauses, logic="and"):
    result = alert_engine.create_alert_rule(
        user_id, alert_type="coin_price", symbol="BTC",
        channels={"push": True, "in_app": True}, cooldown_seconds=0,
        condition_spec={"logic": logic, "clauses": list(clauses)},
    )
    if not result.get("ok"):
        raise AssertionError(f"rule creation failed: {result}")
    return result["alert_id"]


def clause(metric="price", comparator="above", value=0.0) -> dict:
    return {"metric": metric, "comparator": comparator, "value": value}


def _application_sources():
    """Root-level modules and ``services/`` — the code that runs in production.

    ``scripts/`` and ``tests/`` are excluded deliberately: a one-off audit
    script writing observations would be odd but harmless, whereas a second
    writer inside the app would mean the series no longer has a single cadence.
    """
    for path in sorted(REPO.glob("*.py")):
        yield path
    for path in sorted((REPO / "services").rglob("*.py")):
        yield path


# --------------------------------------------------------------------------
# The series, its writer, and the order they run in
# --------------------------------------------------------------------------
def test_the_observation_series_has_exactly_one_writer():
    """``market_observations`` is a series, and a series with two writers on
    two cadences is not one.

    Everything downstream reads the gaps between samples as meaning: a baseline
    older than its tolerance is undecidable, and ``coverage()`` offers a window
    only once the series can answer it. A second writer on a different schedule
    would not corrupt any single reading — it would change what the spacing
    *means*, which is worse, because every reading stays individually plausible.

    ``readiness.py`` states this as fact to justify selling time windows
    ("written only by alert_worker"). This is that sentence, executable.
    """
    writers = []
    for path in _application_sources():
        if path.name == "market_observations.py":
            continue  # defines it
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\brecord_board\s*\(", text):
            writers.append(str(path.relative_to(REPO)))
    check("the series' writers", writers, ["alert_worker.py"])


def test_a_worker_cycle_writes_the_series_before_it_sweeps_the_rules():
    """Ordering with no error case, which is why it needs pinning.

    ``_sample_market`` runs before ``evaluate_all_active_alerts`` so a window
    evaluated this cycle includes the reading this cycle took. Swap the two
    lines and nothing fails: every window simply answers from a series one
    cycle staler than it should be, forever, and a rule that should have fired
    at the moment of a crossing fires ~45 seconds late instead. There is no
    assertion anywhere else in the suite that would notice.

    This runs one real cycle of ``alert_worker.main`` rather than reading the
    source, so a refactor that keeps the ordering is free to move the code.
    """
    import alert_worker

    order: list[str] = []

    # main() imports bot to call init_db(). That is 111k lines and a Flask app
    # to set up a database this process already has, and it is wrapped in
    # try/except precisely because the worker must survive without it.
    stub_bot = types.ModuleType("bot")
    stub_bot.init_db = lambda: None  # type: ignore[attr-defined]
    saved_bot = sys.modules.get("bot")
    sys.modules["bot"] = stub_bot

    saved = {
        "auto": alert_worker.auto_signals_service.process_enabled_users,
        "board": alert_worker.live_market_service.get_crypto_market,
        "record": alert_worker.market_observations.record_board,
        "evaluate": alert_worker.alert_engine.evaluate_all_active_alerts,
        "sentinel": alert_worker.sentinel_runtime.run_scheduled_ingestion,
        "heartbeat": alert_worker.alert_engine.record_worker_heartbeat,
        "running": alert_worker.RUNNING,
    }

    def _record_board(board, now=None):
        # The write itself, not the helper around it: this stays true if
        # ``_sample_market`` is ever inlined or renamed.
        order.append("write-series")
        return {"ok": True, "recorded": 1}

    def _evaluate(limit=None, worker_name=None):
        order.append("sweep-rules")
        alert_worker.RUNNING = False  # one cycle, then fall out of the loop
        return {"checked_count": 0, "triggered_count": 0, "error_count": 0, "latency_ms": 0}

    alert_worker.auto_signals_service.process_enabled_users = lambda limit=None: {}
    alert_worker.live_market_service.get_crypto_market = lambda limit=None: {"data": []}
    alert_worker.market_observations.record_board = _record_board
    alert_worker.alert_engine.evaluate_all_active_alerts = _evaluate
    alert_worker.sentinel_runtime.run_scheduled_ingestion = lambda: []
    alert_worker.alert_engine.record_worker_heartbeat = lambda *a, **k: None
    try:
        alert_worker.main()
    finally:
        alert_worker.auto_signals_service.process_enabled_users = saved["auto"]
        alert_worker.live_market_service.get_crypto_market = saved["board"]
        alert_worker.market_observations.record_board = saved["record"]
        alert_worker.alert_engine.evaluate_all_active_alerts = saved["evaluate"]
        alert_worker.sentinel_runtime.run_scheduled_ingestion = saved["sentinel"]
        alert_worker.alert_engine.record_worker_heartbeat = saved["heartbeat"]
        alert_worker.RUNNING = saved["running"]
        if saved_bot is None:
            sys.modules.pop("bot", None)
        else:
            sys.modules["bot"] = saved_bot

    check("the cycle wrote then swept", order, ["write-series", "sweep-rules"])


def test_the_only_writer_is_a_declared_process():
    """A worker nobody starts writes nothing.

    Every other guard here assumes ``alert_worker`` is running. If its Procfile
    line is dropped the series is simply never written, and the failure is
    indistinguishable from a brand-new deployment: no coverage, no windows
    offered, no window rule ever decidable, no errors.
    """
    procfile = (REPO / "Procfile").read_text(encoding="utf-8")
    processes = {}
    for line in procfile.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            name, _, command = line.partition(":")
            processes[name.strip()] = command.strip()
    check("alert_worker is declared", "alert_worker" in processes, True)
    check("and it runs the writer", "alert_worker.py" in processes.get("alert_worker", ""), True)


# --------------------------------------------------------------------------
# What a lapsed subscription does to what it sold
# --------------------------------------------------------------------------
def test_an_advanced_rule_outlives_the_subscription_that_created_it():
    """The gate is on creating, not on keeping — the portfolio's policy, applied
    to the thing the portfolio's policy does not cover.

    ``test_the_entitlement_lapsing_does_not_delete_anything`` settled this for
    holdings: a lapse keeps what exists and refuses what is new. Alerts land in
    the same place by a different route — all three entitlement checks sit in
    ``create_alert_rule`` and none in the evaluation path — and behaviour that
    is merely *absent* is behaviour nobody chose. Someone tightening the gate
    would reasonably think adding a check to the sweep was a fix.

    It is not, and the reason is not generosity. A rule that stops firing while
    still displayed as active is a member being told they are being watched
    when they are not. Silence is this feature's only failure mode that looks
    exactly like success, so the rule keeps working and the ceiling is applied
    where the member can see it: the next time they try to make one.
    """
    DISPATCHED.clear()
    uid = premium_user()
    rule_id = make_advanced_rule(uid, clause("price", "above", 61_000),
                                 clause("volume_24h", "above", 30_000_000_000))

    set_market(price=62_000, volume_24h=31_000_000_000)
    check("first observation arms", observe(rule_id).get("armed"), True)
    set_market(price=60_000, volume_24h=31_000_000_000)
    check("re-armed below the threshold", observe(rule_id).get("triggered"), False)

    # The subscription lapses. Nothing touches the rule.
    _PREMIUM_USERS.discard(uid)

    set_market(price=62_000, volume_24h=31_000_000_000)
    check("the crossing still fires", observe(rule_id).get("triggered"), True)
    check("and still notifies", len(DISPATCHED), 1)


def test_a_lapsed_member_cannot_create_another_advanced_rule():
    """The other half of the same policy: keeping is not creating."""
    uid = premium_user()
    make_advanced_rule(uid, clause("price", "above", 10.0))
    _PREMIUM_USERS.discard(uid)
    result = alert_engine.create_alert_rule(
        uid, alert_type="coin_price", symbol="BTC",
        channels={"push": True}, cooldown_seconds=0,
        condition_spec={"logic": "and", "clauses": [clause("price", "above", 20.0)]},
    )
    check("a second advanced rule is refused", result.get("ok"), False)
    check("and it names the capability", result.get("capability"),
          premium_crypto_access.ADVANCED_ALERTS)


def test_a_lapsed_member_can_still_create_a_basic_rule():
    """The prohibition the whole mission is built around: free basic alerts
    keep working. A lapse returns the member to the free tier, not below it."""
    uid = premium_user()
    make_advanced_rule(uid, clause("price", "above", 10.0))
    _PREMIUM_USERS.discard(uid)
    result = alert_engine.create_alert_rule(
        uid, alert_type="coin_price", symbol="BTC",
        condition="above", threshold=70_000,
        channels={"push": True}, cooldown_seconds=0,
    )
    check("a basic rule is still allowed", result.get("ok"), True)
    # No spec stored means it takes the original evaluation path, which is the
    # actual guarantee: the free tier is not a restricted version of the new
    # code, it is the code that was always there.
    check("on the original path", load_rule(result["alert_id"]).get("condition_spec"), None)


def _run_standalone():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print("  " + failure)
        raise SystemExit(1)
    print("\nall crypto intelligence acceptance checks passed")


if __name__ == "__main__":
    _run_standalone()
