"""Acceptance tests for persistent crypto alerts.

A price alert is a *standing* request. "Tell me when BTC goes above $100,000"
does not mean "tell me once and then go quiet forever" — the user wants to hear
about the move as it develops. In production two live rules had gone silent for
16 and 20 days each (`BTC above 61000`, latched 2026-08-02; `BTC above 50000`,
latched 2026-08-06), still being polled every 45 seconds, still reporting
`trigger_count = 1`.

What is proven here, end to end through the public API
(`create_alert_rule` / `pause_alert` / `resume_alert` / `delete_alert`) rather
than through hand-written rows:

  * a rule keeps notifying on every new qualifying value, however small the move;
  * the same observation repeated never notifies twice, no matter how many
    cycles or workers see it;
  * the rule stays ACTIVE throughout — it is never retired by having fired;
  * pause / resume / delete remain the only things that stop it;
  * a provider outage or a delivery failure does not silently deactivate it.

The anti-duplicate guarantee is a *value comparison*, not a timer: a repeat needs
a value strictly further into the breach than the one already notified. That is
what lets the engine be both persistent and non-spammy, and it is why these
tests never need to manipulate the clock.

Run directly (no pytest required):

    python tests/test_crypto_alert_persistence.py
"""

from __future__ import annotations

import functools
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_alert_persist_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
# Keep every external provider unconfigured so nothing can reach the network and
# no real device can be notified by a test run.
for _key in (
    "WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY",
    "FCM_SERVER_KEY", "FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY",
    "APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_BUNDLE_ID",
    "BREVO_API_KEY", "BREVO_SMS_API_KEY", "TELEGRAM_BOT_TOKEN",
):
    os.environ.pop(_key, None)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import alert_engine  # noqa: E402
from services import user_context  # noqa: E402


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

TEST_USER_ID = 990401

DISPATCHED: list[dict] = []
_PRICE = {"value": 0.0, "ok": True}
_DELIVERY = {"mode": "ok"}


def _fake_observed_value(rule):
    if not _PRICE["ok"]:
        return {"ok": False, "status": "error", "message": "Quote unavailable."}
    return {"ok": True, "value": float(_PRICE["value"]), "status": "ok"}


def _fake_dispatch(event, rule=None):
    if _DELIVERY["mode"] == "raise":
        raise RuntimeError("push provider exploded")
    DISPATCHED.append({
        "trigger_key": event.get("trigger_key") or event.get("trigger_bucket"),
        "observed_value": event.get("observed_value"),
        "message": event.get("message"),
    })
    if _DELIVERY["mode"] == "fail":
        return {"ok": False, "status": "failed", "channels": {}}
    return {"ok": True, "channels": {"push": "sent"}}


alert_engine.current_observed_value = _fake_observed_value
alert_engine.dispatch_alert_event = _fake_dispatch


def set_price(value):
    _PRICE["value"] = float(value)
    _PRICE["ok"] = True


def set_quote_failure():
    _PRICE["ok"] = False


def _load_rule(rule_id):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM alert_rules WHERE id=? LIMIT 1", (rule_id,))
        return user_context.row_to_dict(cur.fetchone()) or {}
    finally:
        conn.close()


def observe(rule_id):
    """One worker observation against the rule's current persisted state."""
    return alert_engine.evaluate_alert_rule(_load_rule(rule_id))


def rule_status(rule_id):
    return str(_load_rule(rule_id).get("status") or "")


def triggered_event_count(rule_id):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM alert_events WHERE alert_rule_id=? AND status='triggered'",
            (rule_id,),
        )
        row = cur.fetchone()
        return int((row[0] if row else 0) or 0)
    finally:
        conn.close()


def new_rule(threshold=100000.0, condition="above", symbol="BTC", cooldown=900,
             arm_at=99000.0, user_id=TEST_USER_ID,
             repeat_mode=alert_engine.REPEAT_MODE_PROGRESS):
    """Create a rule through the public API and arm it below the threshold.

    Arming is a real step, not test scaffolding: a brand new rule records where
    the market sits without firing, so creating "BTC above $100,000" while BTC
    already trades at $104,000 does not immediately notify. ``arm_at`` is the
    price at that first observation.

    ``repeat_mode`` defaults to ``progress`` because that is the behaviour this
    whole file is about — a standing alert that keeps reporting a developing
    move. It is stated on the rule rather than inherited from
    ``DEFAULT_REPEAT_MODE``, which is ``once``: repeats are a policy a member
    opts into (mobile ``frequency: "recurring"``), not something every rule
    acquires by existing. Passing ``repeat_mode=None`` exercises the default.
    """
    DISPATCHED.clear()
    _DELIVERY["mode"] = "ok"
    created = alert_engine.create_alert_rule(
        user_id=user_id,
        alert_type="coin_price",
        symbol=symbol,
        condition=condition,
        threshold=threshold,
        channels={"push": True, "in_app": True},
        cooldown_seconds=cooldown,
    )
    assert created.get("ok"), created
    rule_id = created.get("alert_id") or (created.get("alert") or {}).get("id")
    assert rule_id, created
    if repeat_mode is not None:
        conn = user_context.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE alert_rules SET repeat_mode=? WHERE id=?",
                (repeat_mode, int(rule_id)),
            )
            conn.commit()
        finally:
            conn.close()
    if arm_at is not None:
        set_price(arm_at)
        observe(rule_id)
    return int(rule_id)


def ensure_support_schema():
    """Stand up the non-alert tables the engine touches.

    `ensure_alert_schema` owns only `alert_rules` / `alert_events`. `users` (read
    by the channel-readiness check on create) and `alert_worker_heartbeat`
    (written by `evaluate_all_active_alerts`) are both created by
    `bot.init_db()`, which cannot be imported here — it builds the Flask app and
    pulls in stripe. Minimal equivalents are enough.
    """
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, email TEXT, telegram_id TEXT, phone_number TEXT)"
        )
        cur.execute(
            "INSERT OR REPLACE INTO users (user_id, email) VALUES (?, ?)",
            (TEST_USER_ID, "alert-persistence@example.test"),
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_worker_heartbeat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_name TEXT UNIQUE,
                last_run_at TEXT,
                last_success_at TEXT,
                checked_count INTEGER DEFAULT 0,
                triggered_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                last_error TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def own_db(fn):
    """Pin this file to its own database for the duration of one test.

    `services.db` resolves DATABASE_URL lazily on every connection and
    `ensure_alert_schema` memoises readiness in a process global, while pytest
    imports every selected module during collection. So without this the module
    imported last decides where *all* the alert test files read and write, and
    the earlier ones find their tables missing. Clearing the readiness flag is
    the other half: the flag is global but the database it refers to is not.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
        alert_engine._ALERT_SCHEMA_READY = False
        # Re-install this file's stubs too: they are module attributes on
        # alert_engine, so the neighbouring alert test file overwrote them when
        # pytest imported it.
        alert_engine.current_observed_value = _fake_observed_value
        alert_engine.dispatch_alert_event = _fake_dispatch
        alert_engine.ensure_alert_schema()
        ensure_support_schema()
        return fn(*args, **kwargs)
    return wrapper


# Run at import too, so the database exists before the first test is collected.
alert_engine.ensure_alert_schema()
ensure_support_schema()


FAILURES: list[str] = []


def check(condition, message):
    """Assert, and record the failure for the standalone runner's summary."""
    if not condition:
        FAILURES.append(message)
        print(f"  FAIL {message}")
        raise AssertionError(message)
    print(f"  ok   {message}")


# --------------------------------------------------------------------------
# 1-3: the rule is created, fires, and survives firing
# --------------------------------------------------------------------------

@own_db
def test_alert_can_be_created():
    rule_id = new_rule()
    rule = _load_rule(rule_id)
    check(bool(rule), "the rule was persisted")
    check(rule_status(rule_id) == "active", "a new alert is active")
    check(float(rule.get("threshold_value")) == 100000.0, "threshold stored as given")
    check(len(DISPATCHED) == 0, "creating and arming an alert notifies nobody")


@own_db
def test_first_qualifying_change_fires():
    rule_id = new_rule()
    set_price(100010.0)
    result = observe(rule_id)
    check(result["triggered"] is True, "crossing the threshold fires")
    check(len(DISPATCHED) == 1, "exactly one notification")
    check(DISPATCHED[0]["observed_value"] == 100010.0, "the notification carries the crossing value")


@own_db
def test_alert_remains_active_after_firing():
    """The core defect: firing must not retire the rule."""
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    check(rule_status(rule_id) == "active", "still active after firing")
    check(int(_load_rule(rule_id).get("active") or 0) == 1, "the legacy active flag agrees")

    # And it is still selected by the worker's own batch query, which is what
    # actually decides whether it gets polled again.
    batch = alert_engine.evaluate_all_active_alerts(limit=500, worker_name="test")
    check(int(batch.get("checked_count") or 0) >= 1, "the worker still picks the rule up")


# --------------------------------------------------------------------------
# 4-6: persistence across further changes, without duplicates
# --------------------------------------------------------------------------

@own_db
def test_same_value_observed_again_does_not_duplicate():
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification")

    for cycle in range(10):
        check(not observe(rule_id)["triggered"], f"identical observation {cycle} silent")
    check(len(DISPATCHED) == 1, "re-observing the same price never duplicates")
    check(triggered_event_count(rule_id) == 1, "and records no extra event")


@own_db
def test_small_new_change_fires_again():
    """A $1 move on a $100,000 asset is 0.001%, and it still counts."""
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    set_price(100011.0)
    result = observe(rule_id)
    check(result["triggered"] is True, "a $1 further move notified")
    check(len(DISPATCHED) == 2, "two notifications")
    check(rule_status(rule_id) == "active", "still active")


@own_db
def test_each_further_change_fires_again():
    rule_id = new_rule()
    for price, expected in (
        (100010.0, 1), (100011.0, 2), (100012.0, 3), (100020.0, 4),
    ):
        set_price(price)
        check(observe(rule_id)["triggered"], f"{price} notified")
        check(len(DISPATCHED) == expected, f"{expected} notifications after {price}")
    check(rule_status(rule_id) == "active", "still active after four notifications")
    check(triggered_event_count(rule_id) == 4, "four triggered events recorded")


# --------------------------------------------------------------------------
# 7-9: the user's controls
# --------------------------------------------------------------------------

@own_db
def test_pause_stops_notifications():
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification")

    paused = alert_engine.pause_alert(rule_id, TEST_USER_ID)
    check(bool(paused.get("ok")), "pause_alert reported success")
    check(rule_status(rule_id) == "paused", "status is paused")

    set_price(100025.0)
    check(not observe(rule_id)["triggered"], "a paused alert does not notify")
    check(len(DISPATCHED) == 1, "still one notification while paused")


@own_db
def test_resume_restores_notifications():
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    alert_engine.pause_alert(rule_id, TEST_USER_ID)
    set_price(100025.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: silent while paused")

    resumed = alert_engine.resume_alert(rule_id, TEST_USER_ID)
    check(bool(resumed.get("ok")), "resume_alert reported success")
    check(rule_status(rule_id) == "active", "status is active again")

    set_price(100026.0)
    check(observe(rule_id)["triggered"], "a resumed alert notifies again")
    check(len(DISPATCHED) == 2, "two notifications")


@own_db
def test_delete_stops_notifications_permanently():
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification")

    deleted = alert_engine.delete_alert(rule_id, TEST_USER_ID)
    check(bool(deleted.get("ok")), "delete_alert reported success")
    check(rule_status(rule_id) == "deleted", "status is deleted")

    for price in (100030.0, 100040.0, 100050.0):
        set_price(price)
        check(not observe(rule_id)["triggered"], f"a deleted alert stays silent at {price}")
    check(len(DISPATCHED) == 1, "delete is permanent")


# --------------------------------------------------------------------------
# 10-11: failures must not silently retire a standing alert
# --------------------------------------------------------------------------

@own_db
def test_provider_failure_keeps_alert_active():
    """A quote outage is the provider's problem, not the user's alert."""
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)

    set_quote_failure()
    for _ in range(3):
        check(not observe(rule_id)["triggered"], "no notification while quotes are down")
    check(rule_status(rule_id) == "active", "the alert survived the outage active")
    check(len(DISPATCHED) == 1, "the outage produced no notification")

    # And it resumes normally once quotes come back.
    set_price(100050.0)
    check(observe(rule_id)["triggered"], "the alert notifies again after recovery")


@own_db
def test_notification_failure_keeps_alert_active():
    """A push that fails to deliver must not deactivate the rule, or one bad
    device token would silently end a standing alert."""
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)

    _DELIVERY["mode"] = "fail"
    set_price(100020.0)
    observe(rule_id)
    check(rule_status(rule_id) == "active", "a failed delivery left the alert active")

    _DELIVERY["mode"] = "ok"
    set_price(100030.0)
    check(observe(rule_id)["triggered"], "the alert notifies again after delivery recovers")
    check(rule_status(rule_id) == "active", "still active")


@own_db
def test_dispatch_exception_keeps_alert_active():
    """Same guarantee when the delivery layer raises rather than returning."""
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)

    _DELIVERY["mode"] = "raise"
    set_price(100020.0)
    try:
        observe(rule_id)
    except RuntimeError:
        pass  # the worker's own try/except owns this; the rule must not change
    check(rule_status(rule_id) == "active", "a raising dispatcher left the alert active")

    _DELIVERY["mode"] = "ok"
    set_price(100030.0)
    check(observe(rule_id)["triggered"], "the alert still works afterwards")


# --------------------------------------------------------------------------
# 12-13: durability and concurrency
# --------------------------------------------------------------------------

@own_db
def test_state_survives_worker_restart():
    """Everything the repeat decision needs lives in the database.

    A worker that restarts mid-move re-reads the row and must reach the same
    conclusion; nothing may depend on in-process memory, or a restart would
    replay a notification the user already received.
    """
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    set_price(100020.0)
    observe(rule_id)
    check(len(DISPATCHED) == 2, "baseline: two notifications")

    persisted = _load_rule(rule_id)
    check(float(persisted.get("last_notified_value") or 0) == 100020.0,
          "the notified value is persisted")
    check(str(persisted.get("condition_state") or "") == alert_engine.STATE_LATCHED,
          "the latch is persisted")

    # Simulate the restart: drop every module-level cache and re-read the row.
    alert_engine._ALERT_SCHEMA_READY = False
    check(not observe(rule_id)["triggered"], "the restarted worker did not replay")
    check(len(DISPATCHED) == 2, "still two notifications")

    set_price(100030.0)
    check(observe(rule_id)["triggered"], "and it still notifies on a genuinely new value")
    check(len(DISPATCHED) == 3, "three notifications")


@own_db
def test_concurrent_same_observation_notifies_once():
    """Several workers evaluating one rule on the same tick send one push.

    Two threads start together with the same pre-repeat snapshot, so without
    the persisted compare-and-set both would see a qualifying move and dispatch.
    """
    rule_id = new_rule()
    set_price(100010.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification")

    set_price(100031.0)
    snapshot = _load_rule(rule_id)  # taken before any worker claims the repeat
    barrier = threading.Barrier(2)
    def evaluate():
        barrier.wait(timeout=10)
        return alert_engine.evaluate_alert_rule(dict(snapshot))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: evaluate(), range(2)))

    fired = [r for r in results if r.get("triggered")]
    check(len(fired) == 1, f"exactly one of two concurrent workers fired (got {len(fired)})")
    check(len(DISPATCHED) == 2, "one extra notification, not two")
    check(triggered_event_count(rule_id) == 2, "one extra event, not two")


# --------------------------------------------------------------------------
# The full acceptance sequence, as one continuous story
# --------------------------------------------------------------------------

@own_db
def test_full_acceptance_sequence():
    """The end-to-end scenario this work exists to make true."""
    rule_id = new_rule(threshold=100000.0, arm_at=99000.0)
    check(rule_status(rule_id) == "active", "created active")

    set_price(100010.0)
    check(observe(rule_id)["triggered"], "100,010 -> ALERT #1")
    check(len(DISPATCHED) == 1, "one notification so far")

    check(not observe(rule_id)["triggered"], "100,010 again -> NO DUPLICATE")
    check(len(DISPATCHED) == 1, "still one notification")

    set_price(100011.0)
    check(observe(rule_id)["triggered"], "100,011 -> ALERT #2")
    set_price(100012.0)
    check(observe(rule_id)["triggered"], "100,012 -> ALERT #3")
    set_price(100020.0)
    check(observe(rule_id)["triggered"], "100,020 -> ALERT #4")
    check(len(DISPATCHED) == 4, "four notifications")
    check(rule_status(rule_id) == "active", "STILL ACTIVE after four alerts")

    alert_engine.pause_alert(rule_id, TEST_USER_ID)
    set_price(100025.0)
    check(not observe(rule_id)["triggered"], "paused + 100,025 -> NO ALERT")
    check(len(DISPATCHED) == 4, "still four notifications")

    alert_engine.resume_alert(rule_id, TEST_USER_ID)
    set_price(100026.0)
    check(observe(rule_id)["triggered"], "resumed + 100,026 -> ALERT #5")
    check(len(DISPATCHED) == 5, "five notifications")

    # A fresh interpreter has no inherited engine/cache state, as after deployment.
    script = """
import json, sys
from services import alert_engine as ae
ae.current_observed_value = lambda rule: {"ok": True, "value": float(sys.argv[2]), "status": "ok"}
ae.dispatch_alert_event = lambda event, rule=None: {"ok": True, "channels": {"push": "sent"}}
rule = ae.get_alert_rule(int(sys.argv[1]))
print(json.dumps(ae.evaluate_alert_rule(rule)))
"""
    def restarted_observe(price):
        result = subprocess.run([sys.executable, "-c", script, str(rule_id), str(price)],
                                env=dict(os.environ), capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout.strip().splitlines()[-1])
    check(not restarted_observe(100026)["triggered"], "restart does not replay #5")
    check(restarted_observe(100027)["triggered"], "restart + 100,027 -> ALERT #6")
    check(triggered_event_count(rule_id) == 6, "six durable trigger-history rows")
    persisted = _load_rule(rule_id)
    check(persisted["last_notified_value"] == 100027, "restart advances persisted notified value")
    check(persisted["trigger_count"] == 6, "restart advances persisted trigger count")
    check(rule_status(rule_id) == "active", "still active after process restart")

    alert_engine.delete_alert(rule_id, TEST_USER_ID)
    check(not restarted_observe(100030)["triggered"], "deleted + 100,030 -> NO ALERT after restart")
    check(triggered_event_count(rule_id) == 6, "six notifications, final")



@own_db
def test_stale_evaluation_cannot_fire_after_user_stops_rule():
    for stop in (alert_engine.pause_alert, alert_engine.delete_alert):
        for latched in (False, True):
            rule_id = new_rule()
            if latched:
                set_price(100010)
                observe(rule_id)
            snapshot = _load_rule(rule_id)
            before = len(DISPATCHED)
            stop(rule_id, TEST_USER_ID)
            set_price(100011)
            result = alert_engine.evaluate_alert_rule(snapshot)
            check(not result["triggered"], "stale crossing/repeat cannot fire after pause/delete")
            check(len(DISPATCHED) == before, "no notification escaped the persisted stop")


@own_db
def test_deleted_rule_cannot_be_resumed():
    rule_id = new_rule()
    alert_engine.delete_alert(rule_id, TEST_USER_ID)
    check(not alert_engine.resume_alert(rule_id, TEST_USER_ID)["ok"], "delete cannot be reversed by resume")
    check(rule_status(rule_id) == "deleted", "deleted remains permanent")


@own_db
def test_explicit_disabled_flag_suppresses_stale_evaluation():
    rule_id = new_rule()
    set_price(100010)
    observe(rule_id)
    snapshot = _load_rule(rule_id)
    conn = user_context.connect()
    conn.execute("UPDATE alert_rules SET active=0 WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
    set_price(100011)
    check(not alert_engine.evaluate_alert_rule(snapshot)["triggered"], "disabled flag stops stale repeat")
    check(len(DISPATCHED) == 1, "disabled rule stays silent")


@own_db
def test_progress_mode_carries_no_hidden_suppression():
    """Once a member opts into repeats, nothing silently throttles them.

    The two knobs that could are both off by default, and this is what keeps the
    "went quiet for 16 days" defect from coming back *for rules that asked for
    repeats*: a materiality floor of 0.25% demanded a ~$250 move at BTC prices,
    and borrowing the user-facing ``cooldown_seconds`` for repeats swallowed
    every qualifying move inside a 15-minute window.
    """
    rule_id = new_rule(repeat_mode=alert_engine.REPEAT_MODE_PROGRESS)
    row = _load_rule(rule_id)
    check(row.get("repeat_mode") == "progress", "the opt-in is stored on the rule")
    check(alert_engine.DEFAULT_REPEAT_STEP_PERCENT == 0, "no implicit 0.25 percent floor")
    check(alert_engine.DEFAULT_REPEAT_MIN_SECONDS == 0, "no implicit long repeat cooldown")


@own_db
def test_repeats_are_opt_in_not_the_default():
    """A rule created through the public API acquires no repeat policy.

    ``create_alert_rule`` does not write ``repeat_mode``; only
    ``_apply_repeat_mode`` does, from the mobile ``frequency`` field. Production
    had 43 rules and zero of them came from that path, yet every one read
    ``progress`` — because the column shipped as ``TEXT DEFAULT 'progress'`` and
    PostgreSQL wrote that into every existing row at ALTER time. The column no
    longer carries a default, so the policy resolves in exactly one place.
    """
    rule_id = new_rule(repeat_mode=None)
    row = _load_rule(rule_id)
    check(row.get("repeat_mode") in (None, ""), "the API stores no repeat policy")
    check(alert_engine.DEFAULT_REPEAT_MODE == alert_engine.REPEAT_MODE_ONCE,
          "and the default it falls back to is once-per-crossing")

    # So the standing alert stays latched and quiet through a developing move
    # until the threshold clears and is crossed again.
    set_price(100010.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "the crossing notified once")
    for price in (100011.0, 100500.0, 110000.0):
        set_price(price)
        observe(rule_id)
    check(len(DISPATCHED) == 1, "no repeats without the opt-in")


TESTS = [
    test_stale_evaluation_cannot_fire_after_user_stops_rule,
    test_deleted_rule_cannot_be_resumed,
    test_explicit_disabled_flag_suppresses_stale_evaluation,
    test_progress_mode_carries_no_hidden_suppression,
    test_repeats_are_opt_in_not_the_default,
    test_alert_can_be_created,
    test_first_qualifying_change_fires,
    test_alert_remains_active_after_firing,
    test_same_value_observed_again_does_not_duplicate,
    test_small_new_change_fires_again,
    test_each_further_change_fires_again,
    test_pause_stops_notifications,
    test_resume_restores_notifications,
    test_delete_stops_notifications_permanently,
    test_provider_failure_keeps_alert_active,
    test_notification_failure_keeps_alert_active,
    test_dispatch_exception_keeps_alert_active,
    test_state_survives_worker_restart,
    test_concurrent_same_observation_notifies_once,
    test_full_acceptance_sequence,
]


def main():
    alert_engine.ensure_alert_schema()
    ensure_support_schema()
    for test in TESTS:
        print(f"\n{test.__name__}")
        try:
            test()
        except AssertionError:
            pass  # already recorded by `check`; report every failure in one pass
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"PASSED — {len(TESTS)} tests, all assertions green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
