"""Regression tests for the crypto-alert trigger lifecycle.

These lock in the fix for the "notification banner never goes away" defect. The
alert engine used to be *level*-triggered: it re-fired whenever the condition
was true and the cooldown had lapsed, so "BTC above $61,000" kept re-notifying
every cooldown window for as long as BTC stayed above $61,000, each push
carrying a freshly observed price. On device that reads as a banner that keeps
coming back.

The engine is now *edge*-triggered and latched. What is proven here:

  * a brand new rule ARMS on its first observation and does not fire, so
    creating a rule while the market already sits past the threshold does not
    produce an immediate self-perpetuating alert;
  * a genuine crossing fires exactly once;
  * staying past the threshold does not re-fire, no matter how many
    observations or how much time passes (the old cooldown path);
  * clearing the threshold re-arms, and a fresh crossing fires again;
  * opposite-direction rules on the same symbol stay independent;
  * a duplicated worker cycle / retry is idempotent — the replayed crossing
    reuses the same stable trigger key and dispatches no second notification.

Run directly (no pytest required):

    python tests/test_crypto_alert_edge_trigger.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_alert_edge_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
# Keep every external provider unconfigured so nothing can reach the network.
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

#: Every notification the engine would have delivered, in order.
DISPATCHED: list[dict] = []

#: The next price `current_observed_value` should report.
_PRICE = {"value": 0.0, "ok": True}


def _fake_observed_value(rule):
    """Stub the market feed so tests drive the price deterministically."""
    if not _PRICE["ok"]:
        return {"ok": False, "status": "error", "message": "Quote unavailable."}
    return {"ok": True, "value": float(_PRICE["value"]), "status": "ok"}


def _fake_dispatch(event, rule=None):
    """Stub delivery; record what would have gone out to the device."""
    DISPATCHED.append({
        "event_id": event.get("id"),
        "trigger_key": event.get("trigger_key") or event.get("trigger_bucket"),
        "observed_value": event.get("observed_value"),
        "message": event.get("message"),
    })
    return {"ok": True, "channels": {"push": "sent"}}


alert_engine.current_observed_value = _fake_observed_value
alert_engine.dispatch_alert_event = _fake_dispatch


def set_price(value):
    _PRICE["value"] = float(value)
    _PRICE["ok"] = True


def set_quote_failure():
    _PRICE["ok"] = False


def observe(rule_id):
    """Run one worker observation against the rule's current stored state."""
    return alert_engine.evaluate_alert_rule(_load_rule(rule_id))


def _load_rule(rule_id):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM alert_rules WHERE id=? LIMIT 1", (rule_id,))
        return user_context.row_to_dict(cur.fetchone()) or {}
    finally:
        conn.close()


def make_rule(condition="above", threshold=61000.0, cooldown=900, symbol="BTC", user_id=9401):
    alert_engine.ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (user_id, alert_type, symbol, condition, threshold_value, target_value,
             channels_json, status, active, cooldown_seconds, trigger_count, created_at, updated_at)
            VALUES (?, 'coin_price', ?, ?, ?, ?, '{"push": true, "in_app": true}', 'active', 1, ?, 0, ?, ?)
            """,
            (user_id, symbol, condition, threshold, threshold, cooldown,
             alert_engine._now(), alert_engine._now()),
        )
        rule_id = cur.lastrowid
        conn.commit()
        return rule_id
    finally:
        conn.close()


def clear_cooldown(rule_id):
    """Push `last_triggered_at` far into the past to isolate the latch from the
    cooldown rate limiter."""
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE alert_rules SET last_triggered_at='2000-01-01T00:00:00' WHERE id=?", (rule_id,))
        conn.commit()
    finally:
        conn.close()


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


def rule_state(rule_id):
    return str(_load_rule(rule_id).get("condition_state") or "")


FAILURES: list[str] = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)
        print(f"  FAIL {message}")
    else:
        print(f"  ok   {message}")


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_first_observation_arms_without_firing():
    """A rule created while the market is already past the threshold must not
    fire. This is the exact shape of the reported defect: "BTC above $61,000"
    created while BTC trades at $64,446."""
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(64446.0)

    result = observe(rule_id)

    check(result["triggered"] is False, "first observation past threshold does not fire")
    check(result.get("armed") is True, "first observation reports armed")
    check(rule_state(rule_id) == alert_engine.STATE_ARMED, "state is armed after first observation")
    check(len(DISPATCHED) == 0, "no notification dispatched on arming")


def test_one_crossing_sends_exactly_one_notification():
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)  # arm below the threshold

    set_price(61500.0)
    result = observe(rule_id)

    check(result["triggered"] is True, "genuine crossing fires")
    check(len(DISPATCHED) == 1, "exactly one notification dispatched for one crossing")
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "state is latched after firing")
    check(triggered_event_count(rule_id) == 1, "exactly one triggered event recorded")
    check(
        "Value at crossing" in (DISPATCHED[0]["message"] or ""),
        "notification body describes the crossing, not a live feed",
    )


def test_staying_past_threshold_never_refires():
    """The core of the stuck-banner bug: repeated observations while the price
    stays past the threshold must produce no further notifications, even once
    the cooldown has lapsed."""
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)  # the one legitimate crossing
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    # 40 further observations at rising prices, with the cooldown expired every
    # time — under the old level-triggered engine each of these was a new push.
    for price in range(61500, 65500, 100):
        set_price(price)
        clear_cooldown(rule_id)
        result = observe(rule_id)
        if result["triggered"]:
            FAILURES.append(f"re-fired while latched at price {price}")

    check(len(DISPATCHED) == 1, "40 further in-region observations dispatched nothing")
    check(triggered_event_count(rule_id) == 1, "still exactly one triggered event")
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "state stays latched")


def test_clearing_threshold_rearms_and_next_crossing_fires():
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "first crossing fired")

    set_price(59000.0)  # falls back below
    rearm = observe(rule_id)
    check(rearm.get("rearmed") is True, "dropping below the threshold re-arms")
    check(rule_state(rule_id) == alert_engine.STATE_ARMED, "state is armed after clearing")
    check(len(DISPATCHED) == 1, "re-arming itself dispatches nothing")

    set_price(62000.0)  # crosses again
    clear_cooldown(rule_id)
    second = observe(rule_id)
    check(second["triggered"] is True, "second genuine crossing fires")
    check(len(DISPATCHED) == 2, "two separate crossings produced two notifications")
    check(
        DISPATCHED[0]["trigger_key"] != DISPATCHED[1]["trigger_key"],
        "separate crossings carry distinct stable trigger keys",
    )


def test_opposite_direction_rules_are_independent():
    DISPATCHED.clear()
    above_id = make_rule(condition="above", threshold=61000.0)
    below_id = make_rule(condition="below", threshold=59000.0)

    set_price(60000.0)  # between both thresholds: arms both, fires neither
    observe(above_id)
    observe(below_id)
    check(len(DISPATCHED) == 0, "arming both rules fires neither")

    set_price(61500.0)  # crosses only the 'above' rule
    observe(above_id)
    observe(below_id)
    check(len(DISPATCHED) == 1, "only the 'above' rule fires")
    check(rule_state(above_id) == alert_engine.STATE_LATCHED, "'above' rule latched")
    check(rule_state(below_id) == alert_engine.STATE_ARMED, "'below' rule still armed")

    set_price(58500.0)  # crosses only the 'below' rule; re-arms 'above'
    observe(above_id)
    observe(below_id)
    check(len(DISPATCHED) == 2, "'below' rule fires on its own crossing")
    check(rule_state(above_id) == alert_engine.STATE_ARMED, "'above' rule re-armed")
    check(rule_state(below_id) == alert_engine.STATE_LATCHED, "'below' rule latched")


def test_duplicate_worker_cycle_is_idempotent():
    """A duplicated worker, a retry, or a reconnect replaying the same cycle
    must not produce a second banner."""
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)

    set_price(61500.0)
    stale = _load_rule(rule_id)  # snapshot taken before the crossing is claimed

    first = alert_engine.evaluate_alert_rule(stale)
    # A second worker holding the *same* pre-crossing snapshot races in.
    second = alert_engine.evaluate_alert_rule(stale)

    check(first["triggered"] is True, "the winning worker fires")
    check(second["triggered"] is False, "the racing worker does not fire")
    check(second.get("latched") is True, "the racing worker reports the crossing already claimed")
    check(len(DISPATCHED) == 1, "a duplicated worker cycle dispatched exactly one notification")
    check(triggered_event_count(rule_id) == 1, "a duplicated worker cycle recorded one event")


def test_trigger_key_is_stable_not_time_bucketed():
    """The trigger key must identify the crossing, not the wall-clock window it
    happened to land in — that is what makes retries safely idempotent."""
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)

    key = DISPATCHED[0]["trigger_key"]
    check(key == f"{rule_id}:1", f"first crossing key is '<rule_id>:1' (got {key!r})")

    # Replaying the dispatch for the same crossing must reuse the same event.
    rule = _load_rule(rule_id)
    replay = alert_engine.trigger_alert(rule, 61500.0, trigger_seq=1)
    check(replay.get("deduped") is True, "replaying a recorded crossing is deduped")
    check(len(DISPATCHED) == 1, "replaying a recorded crossing dispatches nothing")


def test_quote_failure_does_not_disturb_latch():
    """A provider blip must not re-arm a latched rule, or the next successful
    poll would fire a duplicate."""
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "rule latched before the blip")

    set_quote_failure()
    observe(rule_id)
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "quote failure leaves the latch intact")

    set_price(62000.0)
    clear_cooldown(rule_id)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "recovery after a quote failure does not re-fire")


TESTS = [
    test_first_observation_arms_without_firing,
    test_one_crossing_sends_exactly_one_notification,
    test_staying_past_threshold_never_refires,
    test_clearing_threshold_rearms_and_next_crossing_fires,
    test_opposite_direction_rules_are_independent,
    test_duplicate_worker_cycle_is_idempotent,
    test_trigger_key_is_stable_not_time_bucketed,
    test_quote_failure_does_not_disturb_latch,
]


def main():
    alert_engine.ensure_alert_schema()
    for test in TESTS:
        print(f"\n{test.__name__}")
        test()
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
