"""Anomalies, account health, and the gates that stop us training on bad data.

The properties worth pinning here are mostly about restraint:

* an anomaly detector must not be able to stop delivery,
* a brand-new account must not be able to have an anomaly, because it has no
  baseline and borrowing one from other advertisers reports what is unusual for
  somebody else,
* a health score must never be the only thing returned, and
* every readiness gate must actually be able to fail.

    python tests/business_os/test_ads_intelligence_health.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_health_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-health")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import events, health, ml_readiness  # noqa: E402
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402
from services.business_os.advertising.schema import ensure_schema as _ad_schema  # noqa: E402
from services.business_os.advertising import guardrails  # noqa: E402

_ADV = "adv-health-1"
_CAMPAIGN = "camp-health-1"


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def setup_module(module=None):
    ensure_schema()
    _ad_schema()
    guardrails.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO business_os_ad_campaigns (campaign_id, "
            "advertiser_user_id, name, objective, status, created_at, "
            "updated_at) VALUES (?, ?, 'Health', 'awareness', 'active', ?, ?)",
            (_CAMPAIGN, _ADV, _iso(datetime.now(timezone.utc)),
             _iso(datetime.now(timezone.utc))))
        conn.commit()
    finally:
        conn.close()


def _clear():
    conn = db.connect()
    try:
        for table in ("ads_intel_events", "business_os_ad_billing_events",
                      "business_os_ad_account_guardrails"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _events(count, *, name="ad_viewable", days_ago=0, validity="valid",
            quality="ok", version=1):
    """Seed delivery into the intelligence event log."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=1)
    conn = db.connect()
    try:
        for i in range(count):
            conn.execute(
                "INSERT INTO ads_intel_events (event_id, event_name, "
                "event_family, dedup_key, occurred_at, received_at, "
                "campaign_id, validity, billable, quality_status, "
                "processing_version, ingest_source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'client', ?)",
                (f"ev-{uuid.uuid4().hex[:14]}", name,
                 events.event_family(name), f"dk-{uuid.uuid4().hex[:14]}",
                 _iso(when), _iso(when), _CAMPAIGN, validity, quality,
                 version, _iso(when)))
        conn.commit()
    finally:
        conn.close()


def _charge(cents, *, days_ago=0):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=1)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_ad_billing_events (billing_event_id, "
            "advertiser_user_id, campaign_id, source_event_type, "
            "source_event_id, billing_model, unit_price_cents, "
            "total_amount_cents, currency, billing_status, idempotency_key, "
            "created_at) VALUES (?, ?, ?, 'impression', ?, 'cpm', ?, ?, "
            "'usd', 'charged', ?, ?)",
            (f"be-{uuid.uuid4().hex[:12]}", _ADV, _CAMPAIGN,
             f"src-{uuid.uuid4().hex[:8]}", cents, cents,
             f"idem-{uuid.uuid4().hex[:12]}", _iso(when)))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Anomalies
# --------------------------------------------------------------------------- #

def test_a_new_account_cannot_have_an_anomaly():
    """No baseline means no anomaly. The alternative is borrowing somebody
    else's baseline and reporting what is unusual for them."""
    _clear()
    _events(10)
    conn = db.connect()
    try:
        result = health.detect(conn, _ADV)
    finally:
        conn.close()
    _assert(result["comparable"] is False, result)
    _assert(result["anomalies"] == [], result)


def test_a_delivery_collapse_is_detected_against_the_accounts_own_baseline():
    _clear()
    for day in range(2, 9):
        _events(400, days_ago=day)
    _events(5, days_ago=0)
    conn = db.connect()
    try:
        result = health.detect(conn, _ADV)
    finally:
        conn.close()
    _assert(result["comparable"] is True, result)
    codes = [a["code"] for a in result["anomalies"]]
    _assert("DELIVERY_COLLAPSE" in codes, result)


def test_steady_delivery_raises_no_alarm():
    """The detector must be quiet when nothing is wrong, or it gets ignored."""
    _clear()
    for day in range(0, 9):
        _events(400, days_ago=day)
        _charge(1000, days_ago=day)
    conn = db.connect()
    try:
        result = health.detect(conn, _ADV)
    finally:
        conn.close()
    _assert(result["anomalies"] == [],
            f"steady delivery raised an alarm: {result['anomalies']}")


def test_an_anomaly_never_takes_an_action():
    """Detection is advisory. Stopping delivery is the guardrail's authority."""
    _clear()
    for day in range(2, 9):
        _events(400, days_ago=day)
    _events(5, days_ago=0)
    conn = db.connect()
    try:
        result = health.detect(conn, _ADV)
    finally:
        conn.close()
    for item in result["anomalies"]:
        _assert(item["action_taken"] == "none", item)
        _assert(item["requires_human"] is True, item)


def test_the_health_module_cannot_stop_delivery():
    """Structural: no write path to the guardrail or to campaign state."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(health))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        sql = node.value.upper()
        if any(w in sql for w in ("INSERT INTO", "UPDATE ", "DELETE FROM")):
            raise AssertionError(f"health.py writes to the database: {node.value}")
    source = inspect.getsource(health)
    for forbidden in ("halt_account_delivery", "set_daily_ceiling"):
        _assert(forbidden not in source,
                f"health.py can invoke the guardrail action {forbidden}")


def test_an_unreadable_event_log_reports_degraded_rather_than_healthy():
    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

        def close(self):
            pass

    result = health.detect(_Boom(), _ADV)
    _assert(result["degraded"] is True, result)
    _assert(result["anomalies"] == [], result)
    _assert(result["comparable"] is False, result)


# --------------------------------------------------------------------------- #
# Account health
# --------------------------------------------------------------------------- #

def test_health_always_returns_the_factors_not_only_a_score():
    _clear()
    for day in range(0, 9):
        _events(400, days_ago=day)
    conn = db.connect()
    try:
        result = health.account_health(conn, _ADV)
    finally:
        conn.close()
    _assert(result["factors"], result)
    _assert(result["score_is_advisory"] is True, result)
    for item in result["factors"]:
        _assert({"factor", "state", "detail", "evidence"} <= set(item), item)


def test_an_unknown_factor_does_not_count_against_the_score():
    """Not knowing something is not the advertiser's fault."""
    _assert(health._PENALTY[health.STATE_UNKNOWN] == 0)


def test_a_halted_account_reads_as_critical():
    _clear()
    guardrails.halt_account_delivery(_ADV, actor="admin-1", reason="policy")
    conn = db.connect()
    try:
        result = health.account_health(conn, _ADV)
    finally:
        conn.close()
    standing = [f for f in result["factors"] if f["factor"] == "account_standing"]
    _assert(standing and standing[0]["state"] == health.STATE_CRITICAL,
            result["factors"])
    _assert(result["worst_state"] == health.STATE_CRITICAL, result)


def test_health_reads_the_canonical_guardrail_not_a_second_standing_model():
    import inspect
    source = inspect.getsource(health._guardrail_factor)
    _assert("advertising import guardrails" in source, source)


def test_invalid_traffic_is_reported_without_blaming_the_advertiser():
    _clear()
    _events(200, validity="valid")
    _events(100, validity="invalid")
    conn = db.connect()
    try:
        result = health.account_health(conn, _ADV)
    finally:
        conn.close()
    quality = [f for f in result["factors"] if f["factor"] == "traffic_quality"]
    _assert(quality, result["factors"])
    _assert("not charged" in quality[0]["detail"].lower(),
            f"the advertiser is not told they were not charged: {quality[0]}")


def test_explain_names_a_factor_rather_than_a_score():
    _clear()
    guardrails.halt_account_delivery(_ADV, actor="admin-1")
    conn = db.connect()
    try:
        text = health.explain(health.account_health(conn, _ADV))
    finally:
        conn.close()
    _assert("stopped" in text.lower(), text)


# --------------------------------------------------------------------------- #
# ML readiness
# --------------------------------------------------------------------------- #

def test_an_empty_dataset_is_not_ready():
    """The single most important case. Silence must not read as consent."""
    _clear()
    conn = db.connect()
    try:
        result = ml_readiness.assess(conn)
    finally:
        conn.close()
    _assert(result["ready"] is False, result)
    _assert(result["blocking"], result)


def test_every_gate_is_reported_whether_it_passed_or_not():
    """An operator needs to see the gates that passed to trust the ones that
    did not."""
    _clear()
    conn = db.connect()
    try:
        result = ml_readiness.assess(conn)
    finally:
        conn.close()
    reported = {g["gate"] for g in result["gates"]}
    _assert(reported == set(ml_readiness.ALL_GATES),
            f"gates missing from the report: "
            f"{set(ml_readiness.ALL_GATES) - reported}")


def test_a_thin_dataset_fails_on_volume():
    _clear()
    _events(100)
    conn = db.connect()
    try:
        result = ml_readiness.assess(conn)
    finally:
        conn.close()
    _assert(ml_readiness.GATE_VOLUME in result["blocking"], result)


def test_labels_from_two_processing_versions_are_not_trainable():
    """The rules changed mid-window, so early and late labels are different
    quantities and a model would learn the change."""
    _clear()
    _events(50, version=1)
    _events(50, version=2)
    conn = db.connect()
    try:
        result = ml_readiness.assess(conn)
    finally:
        conn.close()
    _assert(ml_readiness.GATE_LABEL_STABILITY in result["blocking"], result)


def test_a_logging_outage_inside_the_window_is_caught():
    """Invisible in every total, which is exactly why it needs its own gate."""
    _clear()
    for day in (1, 2, 3, 40, 41, 42):
        _events(20, days_ago=day)
    conn = db.connect()
    try:
        result = ml_readiness.assess(conn)
    finally:
        conn.close()
    _assert(ml_readiness.GATE_CONTINUITY in result["blocking"],
            f"a 36-day gap passed the continuity gate: {result['blocking']}")


def test_readiness_has_no_override_argument():
    """An override parameter is how a gate that is inconvenient once becomes a
    gate that is always overridden."""
    import inspect
    params = set(inspect.signature(ml_readiness.assess).parameters)
    for forbidden in ("force", "override", "skip_gates", "ignore"):
        _assert(forbidden not in params,
                f"assess accepts an override: {forbidden}")


def test_readiness_trains_nothing():
    import ast
    import inspect
    source = inspect.getsource(ml_readiness)
    _assert(result_free(source), "ml_readiness contains a training call")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for lib in ("sklearn", "torch", "tensorflow", "xgboost"):
        _assert(not any(lib in name for name in imported),
                f"ml_readiness imports {lib}")


def result_free(source: str) -> bool:
    return not any(call in source for call in (".fit(", ".predict(",
                                               ".train("))


def test_an_unreadable_dataset_is_not_ready_rather_than_unknown():
    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

        def close(self):
            pass

    result = ml_readiness.assess(_Boom())
    _assert(result["ready"] is False, result)
    _assert(result["degraded"] is True, result)


def _main():
    setup_module()
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failed += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
