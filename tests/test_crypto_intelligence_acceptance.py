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
  * **A lapsed subscription pauses delivery without destroying the rule.** This
    file used to assert the opposite half of that sentence — that a lapse changed
    nothing at all, because alerts had no evaluation-time gate. That was never a
    decision; it was the absence of one, and it meant a lapsed account kept
    receiving a paid feature. The premium hard-lock supplied the missing
    decision, and it applies to *every* rule type, basic included.

    The authoritative policy is now one sentence: **the rule is preserved
    exactly as configured, and delivery is what stops.** The row is not deleted,
    not soft-deleted, and the member's stored ``status``/``active`` preference is
    never rewritten by the system — so restoring Premium resumes the existing
    rule with no re-setup and no recreation. What changes is ``delivery_state``,
    which the API exposes so a paused rule can never present itself as though it
    were still watching the market. See
    ``test_a_rule_survives_a_lapse_paused_not_deleted_and_resumes_on_renewal``,
    which pins that end to end, and ``alert_engine._set_delivery_state`` for why
    preference and delivery are deliberately two separate fields.

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
from services import crypto_premium_gate  # noqa: E402
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
# Delivery-time premium hard-lock: `evaluate_alert_rule` asks
# `crypto_premium_gate`, not the `premium_crypto_access` gate stubbed above.
# Same `_PREMIUM_USERS` set so free users stay genuinely free.
crypto_premium_gate.has_crypto_capability = lambda user_id, key: int(user_id or 0) in _PREMIUM_USERS

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
def test_a_rule_survives_a_lapse_paused_not_deleted_and_resumes_on_renewal():
    """A lapse PAUSES delivery. It does not delete the rule, and it does not
    withdraw the member's stored preference.

    This supersedes an earlier policy, and the reversal was deliberate. The
    original contract put every entitlement check in ``create_alert_rule`` and
    none in the evaluation path, on the reasoning that a rule which stops firing
    while still displayed as active tells a member they are being watched when
    they are not — silence being this feature's one failure mode that looks
    exactly like success.

    The concern was right; the remedy was not. Continuing to deliver a premium
    feature to a lapsed account is not the only way to avoid lying to someone.
    The authoritative contract now separates the two facts that the old
    single-flag model had to conflate:

        status / active   the member's stored preference — only they change it
        delivery_state    what the system is actually doing right now

    So a lapse pauses ``delivery_state`` and leaves ``status`` untouched, and
    the pause is *exposed* rather than inferred. The member is not told they are
    being watched, and they also do not lose the rule or its configuration.

    Proves A-J of the delivery lifecycle contract.
    """
    DISPATCHED.clear()
    # A. A premium member creates an active rule.
    uid = premium_user()
    rule_id = make_advanced_rule(uid, clause("price", "above", 61_000),
                                 clause("volume_24h", "above", 30_000_000_000))

    set_market(price=62_000, volume_24h=31_000_000_000)
    check("first observation arms", observe(rule_id).get("armed"), True)
    set_market(price=60_000, volume_24h=31_000_000_000)
    check("re-armed below the threshold", observe(rule_id).get("triggered"), False)

    stored = load_rule(rule_id)
    check("delivering while entitled",
          alert_engine._public_rule(stored).get("delivery_state"), "delivering")
    check("not flagged as paused while entitled",
          alert_engine._public_rule(stored).get("delivery_paused"), False)

    # B. The entitlement expires. Nothing else touches the rule.
    _PREMIUM_USERS.discard(uid)

    set_market(price=62_000, volume_24h=31_000_000_000)
    result = observe(rule_id)

    # E. Evaluation skips with premium_required.
    check("the crossing does not fire", result.get("triggered"), False)
    check("evaluation reports premium_required", result.get("status"), "premium_required")
    check("evaluation reports the skip", result.get("skipped"), True)
    # F. Zero delivery.
    check("nothing was dispatched", len(DISPATCHED), 0)

    # C. The rule row still exists. D. Configured state is preserved.
    lapsed = load_rule(rule_id)
    check("the rule row still exists", bool(lapsed), True)
    check("it was not soft-deleted", lapsed.get("deleted_at") or "", "")
    check("the stored status is untouched", lapsed.get("status") or "active", "active")

    public = alert_engine._public_rule(lapsed)
    check("the member's active preference is preserved", public.get("active"), 1)
    # G. The paused state is observable, and distinguishable from delivering.
    check("delivery is reported paused", public.get("delivery_paused"), True)
    check("with the reason", public.get("delivery_paused_reason"), "premium_required")
    check("delivery_state names the pause",
          public.get("delivery_state"), "premium_required")

    # H. Entitlement is restored.
    _PREMIUM_USERS.add(uid)

    # I. The SAME rule resumes. J. No recreation was required.
    set_market(price=60_000, volume_24h=31_000_000_000)
    check("re-arms below the threshold again", observe(rule_id).get("triggered"), False)
    set_market(price=62_000, volume_24h=31_000_000_000)
    resumed = observe(rule_id)
    check("the same rule fires again", resumed.get("triggered"), True)
    check("and notifies once", len(DISPATCHED), 1)

    restored = alert_engine._public_rule(load_rule(rule_id))
    check("delivery_state is back to delivering",
          restored.get("delivery_state"), "delivering")
    check("no longer flagged paused", restored.get("delivery_paused"), False)
    check("it is the same rule id", restored.get("id"), rule_id)


def test_a_lapsed_member_cannot_create_another_advanced_rule():
    """The other half of the same policy: keeping is not creating.

    Preserving the rules a member already configured is a promise about work
    they have already done. It is not a standing grant of the paid feature, so
    the create-time gate stays closed while the entitlement is gone.
    """
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
    """A lapse returns the member to the free tier, not below it — as far as
    CREATION is concerned. This is a create-time assertion only.

    Careful with the scope here, because the two gates disagree by design and
    reading this test as "basic alerts keep working" is now wrong. Creation of a
    basic rule is not gated: ``premium_crypto_access`` guards the advanced
    capability, so a lapsed member can still build an ordinary price alert and
    it is stored. Delivery is a separate gate that covers *every* rule type, so
    that stored rule sits in ``delivery_state='premium_required'`` until Premium
    returns. Pinning creation and delivery separately is deliberate: collapsing
    them is how a delivery pause would quietly turn into a creation ban, or a
    creation allowance into an un-gated dispatch.
    """
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
