"""Google Play RTDN — decode, verifier boundary, and projection.

Proves that (1) the Pub/Sub envelope decodes to the subscriptionNotification;
(2) an RTDN whose purchase the injected verifier CANNOT confirm grants nothing —
the core anti-forgery rule, since an RTDN alone is not proof; (3) a verified
PURCHASED/RENEWED projects entitlements; (4) GRACE keeps access; (5) REVOKED
strips access; (6) unmapped plan records but doesn't grant; (7) idempotent replay.

    python tests/business_os/test_iap_google.py   # no pytest needed
"""

import base64
import json
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_iapgoog_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import schema as ent_schema  # noqa: E402
from services.business_os.entitlements import service as ent_svc  # noqa: E402
from services.business_os.entitlements import iap_google as google  # noqa: E402


def setup_module(module=None):
    ent_schema.ensure_schema()
    ent_schema.seed_catalog()


def _envelope(notification_type, *, sub_id="pulsesoc_premium_monthly",
              token="tok-123", package="com.pulsesoc.app"):
    body = {
        "version": "1.0",
        "packageName": package,
        "eventTimeMillis": "1700000000000",
        "subscriptionNotification": {
            "version": "1.0",
            "notificationType": notification_type,
            "purchaseToken": token,
            "subscriptionId": sub_id,
        },
    }
    data = base64.b64encode(json.dumps(body).encode()).decode()
    return {"message": {"data": data, "messageId": "m1"}, "subscription": "s1"}


def _verifier_returning(purchase):
    return lambda pkg, sub_id, token: purchase


def _active_purchase(uid, *, product="pulsesoc_premium_monthly",
                     state="SUBSCRIPTION_STATE_ACTIVE"):
    return {"subscriptionState": state, "productId": product,
            "expiryTimeMillis": "1900000000000", "externalAccountId": str(uid)}


# --- (a) decode envelope -----------------------------------------------------
def test_decode_envelope():
    d = google.decode_rtdn(_envelope(4))
    assert d["notificationType"] == 4 and d["notificationName"] == "SUBSCRIPTION_PURCHASED"
    assert d["purchaseToken"] == "tok-123" and d["subscriptionId"] == "pulsesoc_premium_monthly"


def test_malformed_envelope_raises():
    try:
        google.decode_rtdn({"message": {}})
    except google.GoogleRTDNError:
        return
    raise AssertionError("expected GoogleRTDNError")


# --- (b) unverifiable purchase grants NOTHING (the core rule) ----------------
def test_unverified_grants_nothing():
    res = google.apply_rtdn(_envelope(4, token="tok-unverif"),
                            purchase_verifier=_verifier_returning(None))
    assert res["recorded"] is False and res["projected"] is False, res
    assert "could not be verified" in res["reason"], res


# --- (c) verified PURCHASED projects entitlements ---------------------------
def test_purchased_grants():
    uid = "700"
    res = google.apply_rtdn(
        _envelope(4, token="tok-700"),
        purchase_verifier=_verifier_returning(_active_purchase(uid)))
    assert res["projected"] is True and res["intent"] == "grant", res
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_provider_subs "
            "WHERE provider='google_play' AND provider_subscription_id='tok-700'"
        ).fetchone()[0]
        assert n == 1, n
    finally:
        conn.close()


# --- (d) grace keeps access -------------------------------------------------
def test_grace_keeps_access():
    uid = "701"
    google.apply_rtdn(_envelope(4, token="tok-701"),
                      purchase_verifier=_verifier_returning(_active_purchase(uid)))
    res = google.apply_rtdn(
        _envelope(6, token="tok-701"),
        purchase_verifier=_verifier_returning(
            _active_purchase(uid, state="SUBSCRIPTION_STATE_IN_GRACE_PERIOD")))
    assert res["intent"] == "grace" and res["projected"] is True, res
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True


# --- (e) revoked strips access ----------------------------------------------
def test_revoked_strips_access():
    uid = "702"
    google.apply_rtdn(_envelope(4, token="tok-702"),
                      purchase_verifier=_verifier_returning(_active_purchase(uid)))
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True
    res = google.apply_rtdn(
        _envelope(12, token="tok-702"),
        purchase_verifier=_verifier_returning(
            _active_purchase(uid, state="SUBSCRIPTION_STATE_CANCELED")))
    assert res["revoked"] is True, res
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is False


# --- (f) unmapped plan records but does not grant ---------------------------
def test_unmapped_plan_no_grant():
    uid = "703"
    res = google.apply_rtdn(
        _envelope(4, sub_id="pulsesoc_unknown", token="tok-703"),
        purchase_verifier=_verifier_returning(
            _active_purchase(uid, product="pulsesoc_unknown")))
    assert res["recorded"] is True and res["projected"] is False, res
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is False


# --- (g) idempotent replay --------------------------------------------------
def test_idempotent_replay():
    uid = "704"
    env = _envelope(2, token="tok-704")
    v = _verifier_returning(_active_purchase(uid))
    google.apply_rtdn(env, purchase_verifier=v)
    google.apply_rtdn(env, purchase_verifier=v)
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id='tok-704'").fetchone()[0]
        assert n == 1, n
    finally:
        conn.close()
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True


def _run_standalone():
    setup_module()
    tests = [
        test_decode_envelope,
        test_malformed_envelope_raises,
        test_unverified_grants_nothing,
        test_purchased_grants,
        test_grace_keeps_access,
        test_revoked_strips_access,
        test_unmapped_plan_no_grant,
        test_idempotent_replay,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
