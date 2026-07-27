"""IAP webhook controller (Stage 4 api layer).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload -> 400; a failed Apple signature surfaces as a flat verification_failed
(never crypto internals); a valid Apple notification projects; Google RTDN without
a configured verifier acknowledges but grants nothing; Google RTDN with a stub
verifier projects.

    python tests/business_os/test_iap_api.py   # no pytest needed
"""

import base64
import json
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_iapapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_IAP"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.entitlements import schema as ent_schema  # noqa: E402
from services.business_os.entitlements import service as ent_svc  # noqa: E402
from services.business_os.entitlements import iap_api as api  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from _iap_jws_util import Chain, build_notification  # noqa: E402


def setup_module(module=None):
    ent_schema.ensure_schema()
    ent_schema.seed_catalog()


def _apple_verifier(chain):
    from services.business_os.entitlements import iap_apple as apple
    return apple.AppleNotificationVerifier(trust_anchors=[chain.root_der()])


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_IAP"] = "0"
    try:
        assert api.apple_notification({"signedPayload": "x"})[0] == 404
        assert api.google_rtdn({"message": {"data": ""}})[0] == 404
    finally:
        os.environ["BUSINESS_OS_IAP"] = "on"


# --- (b) apple missing payload ----------------------------------------------
def test_apple_missing_payload():
    st, body = api.apple_notification({})
    assert st == 400 and body["code"] == "missing_payload", body


# --- (c) apple bad signature -> flat verification_failed --------------------
def test_apple_bad_signature():
    chain = Chain()
    tok = build_notification(chain)
    h, p, s = tok.split(".")
    tampered = f"{h}.{p}.{s[:-4]}AAAA"
    st, body = api.apple_notification({"signedPayload": tampered},
                                      apple_verifier=_apple_verifier(chain))
    assert st == 400 and body["code"] == "verification_failed", body


# --- (d) apple valid -> projects --------------------------------------------
def test_apple_valid_projects():
    chain = Chain()
    tok = build_notification(chain, notification_type="SUBSCRIBED",
                             app_account_token="800")
    st, body = api.apple_notification({"signedPayload": tok},
                                      apple_verifier=_apple_verifier(chain))
    assert st == 200 and body["ok"] and body["result"]["projected"] is True, body
    assert ent_svc.has_entitlement("800", "premium.profile.customization") is True


# --- (e) apple not configured (no anchors, no injection) -> 503 -------------
def test_apple_not_configured():
    chain = Chain()
    tok = build_notification(chain)
    prev = os.environ.pop("APPLE_ROOT_CA_CERTS", None)
    try:
        st, body = api.apple_notification({"signedPayload": tok})
        assert st == 503 and body["code"] == "not_configured", body
    finally:
        if prev is not None:
            os.environ["APPLE_ROOT_CA_CERTS"] = prev


# --- (f) google without verifier acks but grants nothing --------------------
def test_google_no_verifier():
    body_obj = {"packageName": "com.pulsesoc.app",
                "subscriptionNotification": {"notificationType": 4,
                                             "purchaseToken": "t", "subscriptionId": "s"}}
    env = {"message": {"data": base64.b64encode(json.dumps(body_obj).encode()).decode()}}
    st, body = api.google_rtdn(env)
    assert st == 200 and body["result"]["projected"] is False, body
    assert "not configured" in body["result"]["reason"], body


# --- (g) google with stub verifier projects ---------------------------------
def test_google_with_verifier():
    uid = "801"
    body_obj = {"packageName": "com.pulsesoc.app",
                "subscriptionNotification": {"notificationType": 4,
                                             "purchaseToken": "tok-801",
                                             "subscriptionId": "pulsesoc_premium_monthly"}}
    env = {"message": {"data": base64.b64encode(json.dumps(body_obj).encode()).decode()}}
    verifier = lambda pkg, sub, tok: {
        "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
        "productId": "pulsesoc_premium_monthly",
        "expiryTimeMillis": "1900000000000", "externalAccountId": uid}
    st, body = api.google_rtdn(env, google_purchase_verifier=verifier)
    assert st == 200 and body["result"]["projected"] is True, body
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_apple_missing_payload,
        test_apple_bad_signature,
        test_apple_valid_projects,
        test_apple_not_configured,
        test_google_no_verifier,
        test_google_with_verifier,
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
