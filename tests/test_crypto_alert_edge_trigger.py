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

import functools
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
from tests.support.premium_fixture import grant_premium  # noqa: E402
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
    # Alert delivery is premium-gated at evaluation time for every rule type.
    # This suite is about edge-triggering and latching, not entitlement, so the
    # owner must actually hold Premium or nothing here is ever evaluated.
    grant_premium(user_id)
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


def set_rule_columns(rule_id, **columns):
    """Write arbitrary columns on a rule (status, repeat policy, ...)."""
    if not columns:
        return
    assignments = ", ".join(f"{name}=?" for name in columns)
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE alert_rules SET {assignments} WHERE id=?",
            (*columns.values(), rule_id),
        )
        conn.commit()
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
        return fn(*args, **kwargs)
    return wrapper


FAILURES: list[str] = []


def check(condition, message):
    """Assert, and also record the failure for the standalone runner's summary.

    This used to only append to ``FAILURES`` and print. Under ``main()`` that
    still produced a correct non-zero exit, but under pytest — which is what CI
    runs — a failing check raised nothing, so every test in this file reported
    PASS regardless of what the engine actually did. Raising is what makes the
    file protective in both runners; ``main()`` catches per test below so it can
    still report every failure in one pass instead of stopping at the first.
    """
    if not condition:
        FAILURES.append(message)
        print(f"  FAIL {message}")
        raise AssertionError(message)
    print(f"  ok   {message}")


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@own_db
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


@own_db
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


@own_db
def test_flat_price_past_threshold_never_refires():
    """The stuck-banner bug, for a rule with a materiality floor configured.

    ``repeat_step_percent`` is opt-in noise suppression (the default is 0, i.e.
    every strictly-further value notifies — see
    ``test_every_further_move_notifies_by_default``). This test covers the
    deployment that *does* set a floor: observations holding the price flat, or
    nudging it by less than the step, must stay silent no matter how many cycles
    run or how long the cooldown has been clear.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_rule_columns(rule_id, repeat_step_percent=0.25)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)  # the one legitimate crossing
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    # 40 observations of noise around the notified value: ±0.05%, far under the
    # configured 0.25% step. Under the old level-triggered engine every one of
    # these was a new push.
    for tick in range(40):
        set_price(61500.0 + (tick % 5) * 5.0)
        clear_cooldown(rule_id)
        result = observe(rule_id)
        check(not result["triggered"], f"noise observation {tick} stayed silent")

    check(len(DISPATCHED) == 1, "40 immaterial in-region observations dispatched nothing")
    check(triggered_event_count(rule_id) == 1, "still exactly one triggered event")
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "state stays latched")


@own_db
def test_material_further_move_refires_while_latched():
    """The persistent-alert requirement: a latched rule keeps working.

    "BTC above $61,000" that fired at $61,500 must speak again when BTC reaches
    $64,000. The alert is a standing request to be told about this threshold,
    not a one-shot that retires itself after the first notification.

    Opts into ``progress`` explicitly rather than leaning on the global default,
    which is ``once``: this test is about the repeat feature, so it should say so
    and keep proving the feature even if the default moves again. Also uses an
    explicit 0.25% step so the "immaterial move" leg below is meaningful; with
    the shipped step of 0 every further value notifies instead.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_rule_columns(
        rule_id,
        repeat_mode=alert_engine.REPEAT_MODE_PROGRESS,
        repeat_step_percent=0.25,
    )
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    set_price(64000.0)
    clear_cooldown(rule_id)
    result = observe(rule_id)
    check(result["triggered"], "a 4% further climb re-notified")
    check(len(DISPATCHED) == 2, "a second notification was dispatched")
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "still latched, still monitoring")

    # And again, measured from the *newly notified* value rather than the
    # original crossing, so each notification resets the bar.
    set_price(64100.0)
    clear_cooldown(rule_id)
    check(not observe(rule_id)["triggered"], "+0.16% from the last notification is immaterial")
    set_price(67000.0)
    clear_cooldown(rule_id)
    check(observe(rule_id)["triggered"], "a further material climb notified again")
    check(len(DISPATCHED) == 3, "three notifications total")


@own_db
def test_every_further_move_notifies_in_progress_mode():
    """``progress`` mode has no materiality floor, so small moves are still news.

    A member who opts into "keep telling me how this threshold is developing"
    gets exactly that: a $1 climb is a different fact from the one already
    reported, so it notifies. The floor that used to suppress this
    (``repeat_step_percent=0.25``) demanded a ~$150 move at these prices and is
    opt-in.

    This is no longer the default — see
    ``test_default_repeat_mode_is_once_so_a_rule_speaks_once_per_crossing``.
    Production had 43 rules and every one of them read ``progress`` purely
    because the column shipped with ``DEFAULT 'progress'``, which is not the same
    thing as a member asking for it.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_rule_columns(rule_id, repeat_mode=alert_engine.REPEAT_MODE_PROGRESS)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    for price, expected in ((61501.0, 2), (61502.0, 3), (61503.0, 4), (61520.0, 5)):
        set_price(price)
        clear_cooldown(rule_id)
        check(observe(rule_id)["triggered"], f"a further move to {price} notified")
        check(len(DISPATCHED) == expected, f"{expected} notifications after {price}")

    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "still latched, still monitoring")
    check(
        float(_load_rule(rule_id).get("last_notified_value") or 0) == 61520.0,
        "the baseline tracks the most recently notified value",
    )


@own_db
def test_repeated_identical_observation_never_duplicates():
    """The anti-storm guarantee, and the reason it is a comparison not a timer.

    The worker re-reads every active rule on a fixed interval, so a price that
    simply does not move is observed over and over. Each of those is the same
    fact already reported and must be silent — indefinitely, not merely until a
    cooldown lapses.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    for cycle in range(25):
        clear_cooldown(rule_id)  # no time-based guard is doing this work
        check(not observe(rule_id)["triggered"], f"identical observation {cycle} stayed silent")
    check(len(DISPATCHED) == 1, "25 identical observations dispatched nothing")
    check(triggered_event_count(rule_id) == 1, "still exactly one triggered event")


@own_db
def test_retreat_toward_threshold_does_not_refire():
    """Only movement deeper into the breach counts.

    Falling from $64,000 back to $61,600 is still above $61,000, and it is a
    large move, but the user already knows they are above the threshold. Firing
    on the way back would double the noise for no new information.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_price(60000.0)
    observe(rule_id)
    set_price(64000.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    for price in (63000.0, 62000.0, 61600.0, 61100.0):
        set_price(price)
        clear_cooldown(rule_id)
        check(not observe(rule_id)["triggered"], f"retreat to {price} stayed silent")
    check(len(DISPATCHED) == 1, "no notification from the retreat")


@own_db
def test_repeat_respects_configured_rate_limit():
    """``ALERT_REPEAT_MIN_SECONDS`` still bounds a fast-moving market when set.

    Repeats are rate limited by their own knob rather than by the per-rule
    ``cooldown_seconds``. Those are different promises: ``cooldown_seconds`` is
    user-facing and means "don't notify me on every threshold crossing", and
    borrowing it for repeats made a 600-900s window swallow every qualifying move
    in between. The knob ships at 0; this test proves it still works when raised.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0, cooldown=900)
    set_rule_columns(rule_id, repeat_mode=alert_engine.REPEAT_MODE_PROGRESS)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    original = alert_engine.DEFAULT_REPEAT_MIN_SECONDS
    alert_engine.DEFAULT_REPEAT_MIN_SECONDS = 900
    try:
        # Further, but the crossing just happened — the rate limit holds it.
        set_price(70000.0)
        result = observe(rule_id)
        check(not result["triggered"], "a further move inside the rate limit did not fire")
        check(bool(result.get("cooldown")), "the response says why it was withheld")
        check(len(DISPATCHED) == 1, "still one notification")

        clear_cooldown(rule_id)
        check(observe(rule_id)["triggered"], "the same move fires once the window lapses")
    finally:
        alert_engine.DEFAULT_REPEAT_MIN_SECONDS = original


@own_db
def test_user_cooldown_does_not_suppress_repeats():
    """The regression that made alerts go quiet in production.

    Live rules carry ``cooldown_seconds`` of 600-900. While repeats were gated on
    that column, a user who asked to be told about a threshold got one
    notification and then nothing for the next 10-15 minutes of qualifying
    movement. The crossing cooldown must not reach into the repeat path.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0, cooldown=900)
    set_rule_columns(rule_id, repeat_mode=alert_engine.REPEAT_MODE_PROGRESS)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)  # crossing; sets last_triggered_at to *now*
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    # No clear_cooldown(): the 900s window is wide open, and these must still fire.
    for price in (61501.0, 61502.0, 61510.0):
        set_price(price)
        check(observe(rule_id)["triggered"], f"{price} notified despite the 900s user cooldown")
    check(len(DISPATCHED) == 4, "every qualifying move notified inside the cooldown window")


@own_db
def test_repeat_mode_once_keeps_strict_edge_trigger():
    """``once`` is one notification per crossing, stated explicitly on the rule."""
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_rule_columns(rule_id, repeat_mode=alert_engine.REPEAT_MODE_ONCE)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    for price in (64000.0, 70000.0, 90000.0):
        set_price(price)
        clear_cooldown(rule_id)
        check(not observe(rule_id)["triggered"], f"repeat_mode=once stayed silent at {price}")
    check(len(DISPATCHED) == 1, "repeat_mode=once never re-notifies within a crossing")


@own_db
def test_default_repeat_mode_is_once_so_a_rule_speaks_once_per_crossing():
    """A rule whose owner never chose a repeat policy speaks once per crossing.

    ``make_rule`` does not write ``repeat_mode``, which is the same situation as
    every rule in production: nothing had ever set the column on purpose. It must
    therefore resolve through ``DEFAULT_REPEAT_MODE``, and that default is
    ``once`` — "tell me when BTC crosses $61,000", not "narrate BTC afterwards".
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    check(
        _load_rule(rule_id).get("repeat_mode") in (None, ""),
        "a rule created without a stated policy stores no repeat_mode",
    )
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "the crossing notified once")

    for price in (61501.0, 62000.0, 64000.0, 70000.0):
        set_price(price)
        clear_cooldown(rule_id)
        check(not observe(rule_id)["triggered"], f"no repeat at {price} under the default")
    check(len(DISPATCHED) == 1, "the default never re-notifies inside one crossing")
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "still latched, still monitoring")


@own_db
def test_owner_production_rule_shape_does_not_walk_the_price_down():
    """Regression for the reported "alert spam" using the real rule that caused it.

    Owner rule 43 is "BTC below 79,000". On 2026-08-31 it emitted 23
    notifications in a day, walking the price down 78,996 -> 78,432 — the last of
    them reporting a $5 move. Every one carried a distinct ``trigger_key`` and
    was correctly deduplicated, so no duplicate-suppression test could ever have
    caught this: the rule was firing legitimately, under a repeat policy its
    owner never chose.

    Replayed here at the observed prices, the whole descent is one notification.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="below", threshold=79000.0, cooldown=900)
    set_price(79500.0)
    observe(rule_id)  # arms without firing
    check(len(DISPATCHED) == 0, "arming the rule notified nothing")

    observed = [
        78996.0, 78971.0, 78965.0, 78912.0, 78887.0, 78818.0, 78795.0, 78750.0,
        78992.0, 78952.0, 78904.0, 78873.0, 78784.0, 78761.0, 78745.0, 78675.0,
        78664.0, 78624.0, 78572.0, 78437.0, 78432.0,
    ]
    for price in observed:
        set_price(price)
        clear_cooldown(rule_id)
        observe(rule_id)

    check(len(DISPATCHED) == 1, f"21 production observations sent 1 notification (got {len(DISPATCHED)})")
    check(triggered_event_count(rule_id) == 1, "and recorded exactly one triggered event")

    # The alert is not retired — it is latched and still watching, so the user's
    # standing request survives. It simply has nothing new to say until BTC
    # climbs back above 79,000 and falls through it again.
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "still latched, still monitoring")
    set_price(79500.0)
    clear_cooldown(rule_id)
    observe(rule_id)
    check(rule_state(rule_id) == alert_engine.STATE_ARMED, "clearing the threshold re-arms it")
    set_price(78900.0)
    clear_cooldown(rule_id)
    check(observe(rule_id)["triggered"], "the next genuine crossing fires again")
    check(len(DISPATCHED) == 2, "two crossings, two notifications")


@own_db
def test_schema_does_not_hand_a_repeat_policy_to_rules_that_never_chose_one():
    """The schema must not write a repeat policy into rows on the member's behalf.

    ``ALTER TABLE ... ADD COLUMN repeat_mode TEXT DEFAULT 'progress'``
    materialises that value into every pre-existing row on PostgreSQL, which is
    how all 43 production rules — 37 created months before the column existed —
    came to read ``progress``. ``repeat_step_percent`` beside it already carries
    a comment warning about exactly this trap.

    Asserted behaviourally rather than by reading the DDL string: what matters is
    that a rule inserted without a policy still *has* no policy afterwards, so
    the single source of truth stays ``DEFAULT_REPEAT_MODE``.
    """
    rule_id = make_rule(condition="above", threshold=61000.0)
    stored = _load_rule(rule_id).get("repeat_mode")
    check(
        stored in (None, ""),
        f"a rule inserted without a repeat policy stores none (got {stored!r})",
    )
    check(
        _load_rule(rule_id).get("repeat_step_percent") in (None, ""),
        "and no materiality floor either",
    )
    # The repair that retires the historical backfill is PostgreSQL-only (SQLite
    # cannot drop a column default without rebuilding the table). It must be
    # wired into schema setup and must be a safe no-op here rather than raising.
    check(
        callable(getattr(alert_engine, "_retire_legacy_repeat_mode_default", None)),
        "the legacy-default repair is wired into the engine",
    )
    conn = user_context.connect()
    try:
        alert_engine._retire_legacy_repeat_mode_default(conn)
    finally:
        conn.close()
    check(
        _load_rule(rule_id).get("repeat_mode") in (None, ""),
        "the repair left the SQLite rule untouched",
    )


@own_db
def test_paused_rule_stops_and_resumes_monitoring():
    """Pause / resume / delete are the user's controls over a standing alert.

    Uses ``progress`` so that resuming has something observable to do: the rule
    is still latched from the first crossing, so under the ``once`` default a
    resumed rule is correctly silent until the threshold clears and re-crosses,
    which would prove nothing about pause/resume itself.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_rule_columns(rule_id, repeat_mode=alert_engine.REPEAT_MODE_PROGRESS)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "baseline: one notification from the crossing")

    set_rule_columns(rule_id, status="paused")
    set_price(70000.0)
    clear_cooldown(rule_id)
    check(not observe(rule_id)["triggered"], "a paused rule does not notify")
    check(len(DISPATCHED) == 1, "still one notification while paused")

    set_rule_columns(rule_id, status="active")
    clear_cooldown(rule_id)
    check(observe(rule_id)["triggered"], "resuming restores monitoring")
    check(len(DISPATCHED) == 2, "the resumed rule notified")

    set_rule_columns(rule_id, status="deleted")
    set_price(90000.0)
    clear_cooldown(rule_id)
    check(not observe(rule_id)["triggered"], "a deleted rule never notifies again")
    check(len(DISPATCHED) == 2, "delete stops monitoring permanently")


@own_db
def test_repeat_state_survives_a_worker_restart():
    """The repeat baseline lives in the database, not worker memory, so a
    restart mid-move cannot replay a notification the user already received.

    Needs ``progress`` to produce a repeat to persist a baseline for.
    """
    DISPATCHED.clear()
    rule_id = make_rule(condition="above", threshold=61000.0)
    set_rule_columns(rule_id, repeat_mode=alert_engine.REPEAT_MODE_PROGRESS)
    set_price(60000.0)
    observe(rule_id)
    set_price(61500.0)
    observe(rule_id)
    set_price(64000.0)
    clear_cooldown(rule_id)
    observe(rule_id)
    check(len(DISPATCHED) == 2, "baseline: crossing plus one repeat")

    persisted = _load_rule(rule_id)
    check(
        float(persisted.get("last_notified_value") or 0) == 64000.0,
        "the notified value is persisted for the next worker to read",
    )
    # A fresh worker re-reads the row and observes the very same price again.
    # Had the baseline lived in worker memory it would be gone, and this would
    # replay a notification the user already received.
    set_price(64000.0)
    clear_cooldown(rule_id)
    check(not observe(rule_id)["triggered"], "restart did not replay the repeat")
    check(len(DISPATCHED) == 2, "still two notifications after the restart")


@own_db
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


@own_db
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


@own_db
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


@own_db
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


@own_db
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

    # Recovering at the notified price must not produce a second notification for
    # the same crossing. (A recovery that lands higher is a real move and is
    # covered by the repeat tests above; what is protected here is that the blip
    # itself did not reset anything.)
    set_price(61500.0)
    clear_cooldown(rule_id)
    observe(rule_id)
    check(len(DISPATCHED) == 1, "recovery after a quote failure does not re-fire the same crossing")
    check(rule_state(rule_id) == alert_engine.STATE_LATCHED, "still latched after recovery")


TESTS = [
    test_first_observation_arms_without_firing,
    test_one_crossing_sends_exactly_one_notification,
    test_flat_price_past_threshold_never_refires,
    test_material_further_move_refires_while_latched,
    test_every_further_move_notifies_in_progress_mode,
    test_repeated_identical_observation_never_duplicates,
    test_retreat_toward_threshold_does_not_refire,
    test_repeat_respects_configured_rate_limit,
    test_user_cooldown_does_not_suppress_repeats,
    test_repeat_mode_once_keeps_strict_edge_trigger,
    test_default_repeat_mode_is_once_so_a_rule_speaks_once_per_crossing,
    test_owner_production_rule_shape_does_not_walk_the_price_down,
    test_schema_does_not_hand_a_repeat_policy_to_rules_that_never_chose_one,
    test_paused_rule_stops_and_resumes_monitoring,
    test_repeat_state_survives_a_worker_restart,
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
        try:
            test()
        except AssertionError:
            # Already recorded by `check`; keep going so one run reports every
            # broken invariant rather than only the first.
            pass
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
