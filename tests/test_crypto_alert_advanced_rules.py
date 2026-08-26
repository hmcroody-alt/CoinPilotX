"""Advanced (Premium) crypto alert rules, evaluated by the real alert engine.

``test_crypto_alert_conditions.py`` proves the decision logic in isolation. This
file proves the part that could still be wrong once that logic is correct: that
an advanced rule runs through the *existing* engine — same armed/latched state
machine, same crossing claim, same dedupe — rather than a second one bolted
alongside it, and that adding it changed nothing for the basic rules that were
already there.

What is locked in here:

  * a rule without a ``condition_spec`` takes the original code path, so free
    single-threshold alerts are unaffected by every line of the advanced work;
  * creating an advanced rule is gated on the Premium crypto capability at the
    *service*, not only at the HTTP route, because the worker, UNDX and the
    admin tools all create rules through the same function;
  * compound AND/OR rules arm on first observation and fire once on a genuine
    crossing, exactly like a basic rule;
  * an undecidable rule — a metric the market source did not publish, or a
    crossing with nothing to cross from — is treated like a failed quote: it
    never fires, and critically it never re-arms a latched rule, because a
    provider gap is not an observation that the condition became false;
  * the readings behind each cycle are persisted, which is the only reason a
    crossing clause can ever see an edge;
  * a stored spec that no longer validates degrades to the basic condition
    rather than taking the rule out of service.

Run directly (no pytest required):

    python tests/test_crypto_alert_advanced_rules.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_alert_advanced_"), "test.db")
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
from services import premium_crypto_access  # noqa: E402
from services import user_context  # noqa: E402


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
DISPATCHED: list[dict] = []

#: The board row `get_crypto_quote` should report on the next observation.
_QUOTE = {"asset": {}, "ok": True}

#: User ids the stubbed entitlement gate should treat as Premium.
_PREMIUM_USERS: set[int] = set()


def _fake_quote(symbol, *args, **kwargs):
    if not _QUOTE["ok"]:
        return {"ok": False, "asset": {}, "message": "Quote unavailable."}
    asset = dict(_QUOTE["asset"])
    asset.setdefault("symbol", symbol)
    return {"ok": True, "asset": asset, "source": "test", "stale": False}


def _fake_dispatch(event, rule=None):
    DISPATCHED.append({
        "event_id": event.get("id"),
        "trigger_key": event.get("trigger_key") or event.get("trigger_bucket"),
        "observed_value": event.get("observed_value"),
        "message": event.get("message"),
    })
    return {"ok": True, "channels": {"push": "sent"}}


def _fake_allowed(user_id, key):
    return int(user_id or 0) in _PREMIUM_USERS


#: Measured windows the stubbed series should report, keyed ``BTC:price@60m``.
#: A key that is absent is a window the series cannot answer, which is the
#: normal state for the first hour after a symbol is first sampled.
_WINDOWS: dict[str, float] = {}


def _fake_window_reading(symbol, metric, minutes, now=None):
    key = f"{str(symbol).upper()}:{metric}@{int(minutes)}m"
    if key not in _WINDOWS:
        return {"ok": False, "symbol": symbol, "metric": metric,
                "window_minutes": minutes, "change_percent": None,
                "baseline_age_seconds": None, "reason": "window_not_covered",
                "message": "not watched long enough"}
    return {"ok": True, "symbol": symbol, "metric": metric,
            "window_minutes": minutes, "change_percent": _WINDOWS[key],
            "baseline_age_seconds": int(minutes) * 60 + 90,
            "latest": None, "baseline": None, "sample_count": 40,
            "reason": "", "message": ""}


def set_window(symbol="BTC", metric="price", minutes=60, change=None):
    key = f"{symbol.upper()}:{metric}@{int(minutes)}m"
    if change is None:
        _WINDOWS.pop(key, None)
    else:
        _WINDOWS[key] = change


alert_engine.live_market_service.get_crypto_quote = _fake_quote
alert_engine.market_observations.window_reading = _fake_window_reading
alert_engine.dispatch_alert_event = _fake_dispatch
# Channel readiness reads account rows this hermetic database does not carry, and
# is not what any assertion here is about.
alert_engine.channel_warnings = lambda user_id, channels: []
premium_crypto_access.allowed_for_user_id = _fake_allowed


FAILURES: list[str] = []


def check(label: str, actual, expected):
    if actual == expected:
        print(f"  PASS  {label}")
        return
    FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    raise AssertionError(label)


def set_market(**fields):
    """Publish one board row. Any metric left out is reported as unavailable,
    which is what the Coinbase fallback actually does."""
    asset = {"price": None, "change_24h": None, "price_change_24h": None,
             "volume_24h": None, "market_cap": None}
    asset.update(fields)
    _QUOTE["asset"] = asset
    _QUOTE["ok"] = True


def set_quote_failure():
    _QUOTE["ok"] = False


def observe(rule_id):
    return alert_engine.evaluate_alert_rule(load_rule(rule_id))


def load_rule(rule_id):
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM alert_rules WHERE id=? LIMIT 1", (rule_id,))
        return user_context.row_to_dict(cur.fetchone()) or {}
    finally:
        conn.close()


def set_rule_columns(rule_id, **columns):
    assignments = ", ".join(f"{name}=?" for name in columns)
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE alert_rules SET {assignments} WHERE id=?",
                    (*columns.values(), rule_id))
        conn.commit()
    finally:
        conn.close()


def clear_cooldown(rule_id):
    set_rule_columns(rule_id, last_triggered_at="2000-01-01T00:00:00")


_NEXT_USER = [7100]


def premium_user() -> int:
    _NEXT_USER[0] += 1
    _PREMIUM_USERS.add(_NEXT_USER[0])
    return _NEXT_USER[0]


def free_user() -> int:
    _NEXT_USER[0] += 1
    return _NEXT_USER[0]


def make_advanced_rule(*clauses, logic="and", user_id=None, symbol="BTC", cooldown=0):
    user_id = premium_user() if user_id is None else user_id
    result = alert_engine.create_alert_rule(
        user_id, alert_type="coin_price", symbol=symbol,
        channels={"push": True, "in_app": True}, cooldown_seconds=cooldown,
        condition_spec={"logic": logic, "clauses": list(clauses)},
    )
    if not result.get("ok"):
        raise AssertionError(f"rule creation failed: {result}")
    return result["alert_id"]


def clause(metric="price", comparator="above", value=0.0) -> dict:
    return {"metric": metric, "comparator": comparator, "value": value}


def rule_count(user_id) -> int:
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM alert_rules WHERE user_id=?", (user_id,))
        return int(cur.fetchone()[0])
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Creation + entitlement
# --------------------------------------------------------------------------
def test_free_user_cannot_create_an_advanced_rule():
    user_id = free_user()
    result = alert_engine.create_alert_rule(
        user_id, symbol="BTC", condition_spec={"clauses": [clause("price", "above", 61000)]})
    check("refused", result.get("ok"), False)
    check("names the reason", result.get("code"), "premium_required")
    check("names the capability", result.get("capability"),
          premium_crypto_access.ADVANCED_ALERTS)
    check("wrote no rule", rule_count(user_id), 0)


def test_free_user_can_still_create_a_basic_rule():
    """The entire point of gating on the spec rather than on the endpoint."""
    user_id = free_user()
    result = alert_engine.create_alert_rule(
        user_id, symbol="BTC", condition="above", threshold=61000)
    check("allowed", result.get("ok"), True)
    check("no spec stored", load_rule(result["alert_id"]).get("condition_spec"), None)


def test_premium_user_creates_an_advanced_rule():
    user_id = premium_user()
    result = alert_engine.create_alert_rule(
        user_id, symbol="BTC", channels={"push": True},
        condition_spec={"logic": "and", "clauses": [
            clause("price", "above", 61000),
            clause("volume_24h", "above", 30_000_000_000)]})
    check("allowed", result.get("ok"), True)
    stored = json.loads(load_rule(result["alert_id"])["condition_spec"])
    check("logic persisted", stored["logic"], "and")
    check("clauses persisted", len(stored["clauses"]), 2)


def test_advanced_rule_backfills_the_legacy_columns():
    """Readers that predate this feature — the dashboard, the Telegram surfaces,
    ``_central_crypto_alert_type`` — read ``condition``/``threshold_value``, so an
    advanced rule must not look malformed to them."""
    rule_id = make_advanced_rule(clause("price", "crosses_below", 58000))
    row = load_rule(rule_id)
    check("condition derived", row["condition"], "below")
    check("threshold derived", row["threshold_value"], 58000.0)


def test_invalid_spec_is_refused_even_for_premium():
    user_id = premium_user()
    result = alert_engine.create_alert_rule(
        user_id, symbol="BTC",
        condition_spec={"clauses": [clause("moon_factor", "above", 1)]})
    check("refused", result.get("ok"), False)
    check("named as a condition problem", result.get("code"), "invalid_condition")
    check("wrote no rule", rule_count(user_id), 0)


# --------------------------------------------------------------------------
# Compound evaluation through the engine
# --------------------------------------------------------------------------
def test_and_rule_arms_first_then_fires_once_on_a_crossing():
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("volume_24h", "above", 30_000_000_000))
    # Already past both thresholds on the very first observation: it must arm,
    # not fire, exactly as a basic rule does.
    set_market(price=62_000, volume_24h=31_000_000_000)
    result = observe(rule_id)
    check("first observation arms", result.get("armed"), True)
    check("first observation is silent", len(DISPATCHED), 0)

    # Drop below one clause, then cross back: that is the genuine edge.
    set_market(price=60_000, volume_24h=31_000_000_000)
    check("re-armed while unmatched", observe(rule_id).get("triggered"), False)
    set_market(price=62_000, volume_24h=31_000_000_000)
    check("crossing fires", observe(rule_id).get("triggered"), True)
    check("one notification", len(DISPATCHED), 1)


def test_and_rule_stays_silent_while_one_clause_is_false():
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("volume_24h", "above", 30_000_000_000))
    set_market(price=50_000, volume_24h=1)
    observe(rule_id)  # arm
    # Price qualifies, volume does not: an AND is not satisfied.
    set_market(price=62_000, volume_24h=1_000_000)
    check("not triggered", observe(rule_id).get("triggered"), False)
    check("nothing dispatched", len(DISPATCHED), 0)


def test_or_rule_fires_on_a_single_clause():
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 90_000),
                                 clause("change_24h", "below", -5),
                                 logic="or")
    set_market(price=62_000, change_24h=1.0)
    observe(rule_id)  # arm, neither clause true
    set_market(price=62_000, change_24h=-8.0)
    result = observe(rule_id)
    check("or fires", result.get("triggered"), True)
    check("one notification", len(DISPATCHED), 1)


def test_advanced_rule_does_not_refire_while_latched_and_flat():
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("volume_24h", "above", 30_000_000_000))
    set_market(price=50_000, volume_24h=31_000_000_000)
    observe(rule_id)
    set_market(price=62_000, volume_24h=31_000_000_000)
    check("fires once", observe(rule_id).get("triggered"), True)
    clear_cooldown(rule_id)
    for _ in range(3):
        check("stays silent on the same reading", observe(rule_id).get("triggered"), False)
    check("still one notification", len(DISPATCHED), 1)


def test_latched_advanced_rule_repeats_on_a_further_move():
    """The repeat follows the primary clause's direction, which is the number the
    notification quotes."""
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("volume_24h", "above", 30_000_000_000))
    set_market(price=50_000, volume_24h=31_000_000_000)
    observe(rule_id)
    set_market(price=62_000, volume_24h=31_000_000_000)
    observe(rule_id)
    clear_cooldown(rule_id)
    set_market(price=65_000, volume_24h=31_000_000_000)
    check("further move repeats", observe(rule_id).get("triggered"), True)
    clear_cooldown(rule_id)
    set_market(price=63_000, volume_24h=31_000_000_000)
    check("retreat is silent", observe(rule_id).get("triggered"), False)
    check("two notifications", len(DISPATCHED), 2)


def test_crossing_rule_fires_per_crossing_not_per_further_move():
    """A crossing rule is stricter than a level rule, and stays stricter.

    "Tell me when BTC crosses below 58,000" is answered by the edge, so once the
    edge has passed the rule releases its latch on the very next observation and
    waits for the *next* crossing. Sinking further while already below is not a
    second crossing, and reporting it as one would quietly turn the rule the
    member wrote into the level rule they chose not to write.
    """
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "crosses_below", 58_000))
    set_market(price=59_000)
    check("first cycle arms", observe(rule_id).get("triggered"), False)
    set_market(price=57_000)
    check("crossing down fires", observe(rule_id).get("triggered"), True)

    clear_cooldown(rule_id)
    set_market(price=55_000)
    check("sinking further is not a new crossing", observe(rule_id).get("triggered"), False)
    check("latch released", load_rule(rule_id)["condition_state"], "armed")

    clear_cooldown(rule_id)
    set_market(price=59_500)
    check("climbing back is silent", observe(rule_id).get("triggered"), False)
    clear_cooldown(rule_id)
    set_market(price=57_500)
    check("the next genuine crossing fires", observe(rule_id).get("triggered"), True)
    check("exactly two notifications", len(DISPATCHED), 2)


# --------------------------------------------------------------------------
# Undecidability
# --------------------------------------------------------------------------
def test_missing_metric_never_fires_and_never_rearms():
    """The defect this guards: a fallback quote with no volume must not read as
    "volume did not qualify" (silently downgrading an AND rule) nor as a cleared
    condition (re-arming a latched rule so the next full quote fires again)."""
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("volume_24h", "above", 30_000_000_000))
    set_market(price=50_000, volume_24h=31_000_000_000)
    observe(rule_id)
    set_market(price=62_000, volume_24h=31_000_000_000)
    check("fires on the crossing", observe(rule_id).get("triggered"), True)
    check("latched", load_rule(rule_id)["condition_state"], "latched")

    # Provider degrades to a price-only quote.
    set_market(price=62_500)
    result = observe(rule_id)
    check("undecidable does not fire", result.get("triggered"), False)
    check("latch survives", load_rule(rule_id)["condition_state"], "latched")
    check("no second notification", len(DISPATCHED), 1)


def test_undecidable_rule_records_no_error_event():
    """An unavailable metric is a gap, not a failure; recording it as an error
    would make provider coverage look like an engine fault in the event log."""
    rule_id = make_advanced_rule(clause("market_cap", "above", 1))
    set_market(price=62_000)
    observe(rule_id)
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM alert_events WHERE alert_rule_id=? AND status='error'",
                    (rule_id,))
        check("no error event", int(cur.fetchone()[0]), 0)
    finally:
        conn.close()


def test_a_failed_quote_is_still_an_error():
    rule_id = make_advanced_rule(clause("price", "above", 61000))
    set_quote_failure()
    result = observe(rule_id)
    check("not triggered", result.get("triggered"), False)
    check("reported as failure", result.get("ok"), False)
    set_market(price=62_000)


# --------------------------------------------------------------------------
# Observation persistence
# --------------------------------------------------------------------------
def test_observations_persist_between_cycles():
    rule_id = make_advanced_rule(clause("price", "above", 1),
                                 clause("volume_24h", "above", 1))
    set_market(price=62_000, volume_24h=31_000_000_000)
    observe(rule_id)
    stored = json.loads(load_rule(rule_id)["last_observations"])
    check("price recorded", stored["price"], 62_000.0)
    check("volume recorded", stored["volume_24h"], 31_000_000_000.0)
    check("only what the rule reads", sorted(stored), ["price", "volume_24h"])


def test_a_crossing_rule_records_its_first_reading_even_though_undecidable():
    """Without this the rule could never see an edge: it would be undecidable on
    every cycle for want of the prior reading it declined to write."""
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "crosses_above", 61000))
    set_market(price=60_000)
    result = observe(rule_id)
    check("first cycle silent", result.get("triggered"), False)
    check("but the reading was kept",
          json.loads(load_rule(rule_id)["last_observations"])["price"], 60_000.0)
    set_market(price=62_000)
    check("second cycle sees the edge", observe(rule_id).get("triggered"), True)


def test_unavailable_metric_is_recorded_as_a_gap_not_a_stale_value():
    rule_id = make_advanced_rule(clause("market_cap", "above", 1),
                                 clause("price", "above", 1))
    set_market(price=62_000, market_cap=1_000_000)
    observe(rule_id)
    set_market(price=62_000)
    observe(rule_id)
    stored = json.loads(load_rule(rule_id)["last_observations"])
    check("gap recorded, not carried forward", stored["market_cap"], None)


# --------------------------------------------------------------------------
# Notification copy
# --------------------------------------------------------------------------
def test_compound_notification_states_every_condition_and_reading():
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("volume_24h", "above", 30_000_000_000))
    set_market(price=50_000, volume_24h=31_000_000_000)
    observe(rule_id)
    set_market(price=62_000, volume_24h=31_000_000_000)
    observe(rule_id)
    message = DISPATCHED[-1]["message"]
    check("names the first condition", "price is above 61,000" in message, True)
    check("names the second condition", "24h volume is above 30,000,000,000" in message, True)
    check("joins with the rule's logic", " and " in message, True)
    check("quotes the price reading", "$62,000" in message, True)
    check("quotes the volume reading", "$31,000,000,000" in message, True)


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------
def test_a_spec_that_no_longer_validates_falls_back_to_the_basic_condition():
    """A rule must not be taken out of service by a spec it can no longer parse.
    It still carries a real single-threshold condition; use it."""
    DISPATCHED.clear()
    rule_id = make_advanced_rule(clause("price", "above", 61000))
    set_rule_columns(rule_id, condition_spec=json.dumps(
        {"logic": "and", "clauses": [{"metric": "retired_metric", "comparator": "above", "value": 1}]}))
    rule = alert_engine._public_rule(load_rule(rule_id))
    check("spec dropped", rule["condition_spec"], None)
    check("not flagged advanced", rule["is_advanced"], False)
    check("basic condition intact", rule["condition"], "above")
    check("basic threshold intact", rule["threshold_value"], 61000.0)


def test_basic_rules_take_the_original_evaluation_path():
    """`evaluate_rule_condition` must delegate, not reimplement: a rule with no
    spec has to produce the same answer `condition_matches` always gave."""
    user_id = free_user()
    result = alert_engine.create_alert_rule(user_id, symbol="BTC", condition="above",
                                            threshold=61000, channels={"push": True})
    rule = alert_engine._public_rule(load_rule(result["alert_id"]))
    set_market(price=62_000)
    evaluated = alert_engine.evaluate_rule_condition(rule)
    check("ok", evaluated["ok"], True)
    check("matched", evaluated["matched"], True)
    check("value", evaluated["value"], 62_000.0)
    check("no observations recorded for a basic rule", evaluated["observations"], {})


def test_spec_round_trips_through_the_public_rule():
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("change_24h", "below", -5), logic="or")
    rule = alert_engine._public_rule(load_rule(rule_id))
    check("advanced flagged", rule["is_advanced"], True)
    check("logic", rule["condition_spec"]["logic"], "or")
    check("described",
          conditions.describe_spec(rule["condition_spec"], "BTC"),
          "BTC price is above 61,000.0 or BTC 24h change is below -5.0%")


# --------------------------------------------------------------------------
# The API surface the clients actually call
# --------------------------------------------------------------------------
def test_command_center_creates_an_advanced_rule():
    user_id = premium_user()
    conn = user_context.connect()
    try:
        result = command_center.create_alert(conn, user_id, {
            "assetSymbol": "BTC",
            "logic": "and",
            "conditions": [
                {"metric": "price", "comparator": "above", "value": 61000},
                {"metric": "volume_24h", "comparator": "above", "value": 30_000_000_000},
            ],
        })
    finally:
        conn.close()
    check("created", result.get("ok"), True)
    check("reported as advanced", result.get("advanced"), True)
    rule = alert_engine._public_rule(load_rule(result["alert_id"]))
    check("spec round-tripped", len(rule["condition_spec"]["clauses"]), 2)
    check("summary rendered server-side", rule["condition_summary"],
          "BTC price is above 61,000.0 and BTC 24h volume is above 30,000,000,000.0")


def test_command_center_refuses_an_advanced_rule_for_a_free_user():
    """The denial has to be machine-readable: the client opens an upgrade sheet
    for this and highlights a field for a bad threshold."""
    user_id = free_user()
    conn = user_context.connect()
    try:
        command_center.create_alert(conn, user_id, {
            "assetSymbol": "BTC",
            "conditions": [{"metric": "price", "comparator": "above", "value": 61000}],
        })
        raised = None
    except command_center.PremiumRequired as exc:
        raised = exc
    except ValueError as exc:
        raised = exc
    finally:
        conn.close()
    check("raised PremiumRequired", isinstance(raised, command_center.PremiumRequired), True)
    check("carries a code", getattr(raised, "code", ""), "premium_required")
    check("carries a payment status", getattr(raised, "http_status", 0), 402)
    check("names the capability", getattr(raised, "capability", ""),
          premium_crypto_access.ADVANCED_ALERTS)
    check("wrote no rule", rule_count(user_id), 0)


def test_command_center_basic_payload_is_unchanged():
    user_id = free_user()
    conn = user_context.connect()
    try:
        result = command_center.create_alert(conn, user_id, {
            "assetSymbol": "BTC", "condition": "above", "targetValue": 61000})
    finally:
        conn.close()
    check("created", result.get("ok"), True)
    check("not advanced", result.get("advanced"), False)
    rule = alert_engine._public_rule(load_rule(result["alert_id"]))
    check("no spec", rule["condition_spec"], None)
    check("threshold intact", rule["threshold_value"], 61000.0)


def test_command_center_rejects_an_unusable_advanced_payload_as_a_field_error():
    user_id = premium_user()
    conn = user_context.connect()
    try:
        command_center.create_alert(conn, user_id, {
            "assetSymbol": "BTC",
            "conditions": [{"metric": "price", "comparator": "above", "value": "soon"}]})
        raised = None
    except ValueError as exc:
        raised = exc
    finally:
        conn.close()
    check("rejected", isinstance(raised, ValueError), True)
    check("not a paywall", isinstance(raised, command_center.PremiumRequired), False)


def test_duplicating_an_advanced_rule_keeps_its_conditions():
    """A duplicate that silently became a basic rule would watch less than the
    original while looking identical in the list."""
    user_id = premium_user()
    rule_id = make_advanced_rule(clause("price", "above", 61000),
                                 clause("volume_24h", "above", 30_000_000_000),
                                 user_id=user_id)
    result = alert_engine.duplicate_alert_rule(rule_id, user_id)
    check("duplicated", result.get("ok"), True)
    copy = alert_engine._public_rule(load_rule(result["alert_id"]))
    check("spec copied", len(copy["condition_spec"]["clauses"]), 2)
    check("logic copied", copy["condition_spec"]["logic"], "and")


def test_duplicating_an_advanced_rule_recheck_the_entitlement():
    """A member whose Premium lapsed must not be able to mint new advanced rules
    by duplicating one they already own."""
    user_id = premium_user()
    rule_id = make_advanced_rule(clause("price", "above", 61000), user_id=user_id)
    _PREMIUM_USERS.discard(user_id)
    result = alert_engine.duplicate_alert_rule(rule_id, user_id)
    check("refused", result.get("ok"), False)
    check("named as a paywall", result.get("code"), "premium_required")


# --------------------------------------------------------------------------
# Time windows
# --------------------------------------------------------------------------
def wclause(metric="price", comparator="below", value=-5.0, minutes=60) -> dict:
    return {"metric": metric, "comparator": comparator, "value": value,
            "window_minutes": minutes}


def test_a_windowed_rule_fires_on_the_measured_change():
    DISPATCHED.clear()
    rule_id = make_advanced_rule(wclause("price", "below", -5.0, 60))
    set_market(price=60_000)
    set_window(change=-1.0)
    check("a shallow dip is silent", observe(rule_id).get("triggered"), False)
    clear_cooldown(rule_id)
    set_market(price=56_000)
    set_window(change=-6.5)
    check("fires once the fall passes the threshold",
          observe(rule_id).get("triggered"), True)


def test_a_windowed_rule_reads_the_change_not_the_price():
    """The threshold is a percentage. If the engine handed the level to the
    comparator instead, "price fell more than 5% in an hour" would be answered
    as "price is below -5", which is false for every asset that has ever
    existed — a rule that could not fire, with nothing to show why."""
    DISPATCHED.clear()
    rule_id = make_advanced_rule(wclause("price", "below", -5.0, 60))
    set_market(price=56_000)
    set_window(change=-1.0)
    observe(rule_id)  # arms: the fall has not happened yet
    clear_cooldown(rule_id)
    set_window(change=-9.0)
    check("fires", observe(rule_id).get("triggered"), True)
    check("the reported value is the change",
          load_rule(rule_id)["last_observations"], json.dumps({"price@60m": -9.0}))


def test_a_window_the_series_cannot_answer_never_fires_and_never_rearms():
    """The core honesty case. "We have not been watching long enough" must not
    resolve to "it did not fall", which would both silence the alert during the
    fall and re-arm a latched rule so it fires again on the next real reading."""
    DISPATCHED.clear()
    rule_id = make_advanced_rule(wclause("price", "below", -5.0, 60),
                                 clause("price", "below", 60_000))
    set_market(price=61_000)
    set_window(change=-1.0)
    observe(rule_id)  # arms
    clear_cooldown(rule_id)
    set_market(price=56_000)
    set_window(change=-8.0)
    check("fires while the window is measurable", observe(rule_id).get("triggered"), True)
    check("latched", load_rule(rule_id)["condition_state"], "latched")

    set_window(change=None)  # sampler restarted; the window is gone
    clear_cooldown(rule_id)
    set_market(price=55_000)
    result = observe(rule_id)
    check("undecidable does not fire", result.get("triggered"), False)
    check("latch survives", load_rule(rule_id)["condition_state"], "latched")
    check("no second notification", len(DISPATCHED), 1)


def test_an_unmeasurable_window_is_a_gap_not_an_error():
    """A series that is merely young is not an engine fault, and logging it as
    one would make normal startup look like a broken alert."""
    rule_id = make_advanced_rule(wclause("price", "below", -5.0, 60))
    set_market(price=56_000)
    set_window(change=None)
    observe(rule_id)
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM alert_events WHERE alert_rule_id=? AND status='error'",
                    (rule_id,))
        check("no error event", int(cur.fetchone()[0]), 0)
    finally:
        conn.close()


def test_a_windowed_notification_names_the_window_and_what_it_measured():
    """The requested window is what the member asked for; the baseline age is
    what the sampler could compare. Quoting the first as the second would be a
    precision claim the series does not support."""
    DISPATCHED.clear()
    rule_id = make_advanced_rule(wclause("price", "below", -5.0, 60))
    set_market(price=56_000)
    set_window(change=-1.0)
    observe(rule_id)  # arms
    clear_cooldown(rule_id)
    set_window(change=-7.25)
    observe(rule_id)
    message = DISPATCHED[-1]["message"]
    check("names the window", "BTC price over 1h is below -5.0%" in message, True)
    check("quotes the measured change", "price over 1h -7.25%" in message, True)
    # The stub reports a baseline 90 seconds past the boundary, so the honest
    # statement is 62 minutes, not 60.
    check("quotes the interval actually compared", "(measured over 62m)" in message, True)


def test_a_windowed_rule_is_premium_gated_like_any_other_advanced_rule():
    result = alert_engine.create_alert_rule(
        free_user(), alert_type="coin_price", symbol="BTC",
        channels={"push": True}, condition_spec={"clauses": [wclause()]})
    check("refused", result.get("ok"), False)
    check("named as a paywall", result.get("code"), "premium_required")


def test_a_windowed_crossing_is_refused_at_creation():
    """Rejected here rather than at evaluation: the baseline advances every
    sample, so a crossing would fire on the window sliding forward."""
    result = alert_engine.create_alert_rule(
        premium_user(), alert_type="coin_price", symbol="BTC",
        channels={"push": True},
        condition_spec={"clauses": [wclause(comparator="crosses_below")]})
    check("refused", result.get("ok"), False)


def test_only_the_windows_a_rule_uses_are_measured():
    """Measuring every window of every metric on every cycle would turn one
    rule into eight series queries per sweep."""
    asked: list = []
    original = alert_engine.market_observations.window_reading

    def _recording(symbol, metric, minutes, now=None):
        asked.append((metric, minutes))
        return original(symbol, metric, minutes, now)

    alert_engine.market_observations.window_reading = _recording
    try:
        rule_id = make_advanced_rule(clause("price", "above", 1),
                                     wclause("price", "below", -5.0, 60),
                                     wclause("volume_24h", "above", 40.0, 240))
        set_market(price=56_000, volume_24h=1)
        set_window(change=-8.0)
        set_window(metric="volume_24h", minutes=240, change=50.0)
        observe(rule_id)
    finally:
        alert_engine.market_observations.window_reading = original
    check("exactly the rule's windows", asked, [("price", 60), ("volume_24h", 240)])


TESTS = [
    test_free_user_cannot_create_an_advanced_rule,
    test_free_user_can_still_create_a_basic_rule,
    test_premium_user_creates_an_advanced_rule,
    test_advanced_rule_backfills_the_legacy_columns,
    test_invalid_spec_is_refused_even_for_premium,
    test_and_rule_arms_first_then_fires_once_on_a_crossing,
    test_and_rule_stays_silent_while_one_clause_is_false,
    test_or_rule_fires_on_a_single_clause,
    test_advanced_rule_does_not_refire_while_latched_and_flat,
    test_latched_advanced_rule_repeats_on_a_further_move,
    test_crossing_rule_fires_per_crossing_not_per_further_move,
    test_missing_metric_never_fires_and_never_rearms,
    test_undecidable_rule_records_no_error_event,
    test_a_failed_quote_is_still_an_error,
    test_observations_persist_between_cycles,
    test_a_crossing_rule_records_its_first_reading_even_though_undecidable,
    test_unavailable_metric_is_recorded_as_a_gap_not_a_stale_value,
    test_compound_notification_states_every_condition_and_reading,
    test_a_spec_that_no_longer_validates_falls_back_to_the_basic_condition,
    test_basic_rules_take_the_original_evaluation_path,
    test_spec_round_trips_through_the_public_rule,
    test_command_center_creates_an_advanced_rule,
    test_command_center_refuses_an_advanced_rule_for_a_free_user,
    test_command_center_basic_payload_is_unchanged,
    test_command_center_rejects_an_unusable_advanced_payload_as_a_field_error,
    test_duplicating_an_advanced_rule_keeps_its_conditions,
    test_duplicating_an_advanced_rule_recheck_the_entitlement,
    test_a_windowed_rule_fires_on_the_measured_change,
    test_a_windowed_rule_reads_the_change_not_the_price,
    test_a_window_the_series_cannot_answer_never_fires_and_never_rearms,
    test_an_unmeasurable_window_is_a_gap_not_an_error,
    test_a_windowed_notification_names_the_window_and_what_it_measured,
    test_a_windowed_rule_is_premium_gated_like_any_other_advanced_rule,
    test_a_windowed_crossing_is_refused_at_creation,
    test_only_the_windows_a_rule_uses_are_measured,
]


def main():
    alert_engine.ensure_alert_schema()
    for test in TESTS:
        print(f"\n{test.__name__}")
        try:
            test()
        except AssertionError:
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
