"""Apple App Store Server Notifications v2 — JWS verification + projection.

Proves the REAL crypto path with a self-generated EC chain (no Apple secrets, no
network): a valid notification verifies and projects entitlements; a tampered
payload, a wrong signing key, an untrusted root, an expired leaf, and a non-ES256
alg are all rejected; and the notification lifecycle (grant / grace / refund /
expire) lands correctly on the canonical entitlement grants.

    python tests/business_os/test_iap_apple.py   # no pytest needed
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_iapapple_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import schema as ent_schema  # noqa: E402
from services.business_os.entitlements import service as ent_svc  # noqa: E402
from services.business_os.entitlements import iap_apple as apple  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from _iap_jws_util import Chain, build_notification, ms  # noqa: E402


def setup_module(module=None):
    ent_schema.ensure_schema()
    ent_schema.seed_catalog()


def _verifier(chain, *, now=None):
    now_fn = (lambda: now) if now else None
    return apple.AppleNotificationVerifier(trust_anchors=[chain.root_der()],
                                           now_fn=now_fn)


def _expect(fn, exc=apple.AppleJWSError):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


# --- (a) valid notification verifies + decodes nested JWS -------------------
def test_valid_notification_verifies():
    chain = Chain()
    tok = build_notification(chain, notification_type="SUBSCRIBED",
                             app_account_token="500")
    out = _verifier(chain).verify(tok)
    assert out["notificationType"] == "SUBSCRIBED", out
    # nested JWS decoded in place
    assert out["data"]["transactionInfo"]["productId"] == "com.pulsesoc.premium.monthly"
    assert out["data"]["renewalInfo"]["autoRenewStatus"] == 1


# --- (b) tampered payload is rejected ---------------------------------------
def test_tampered_payload_rejected():
    chain = Chain()
    tok = build_notification(chain)
    h, p, s = tok.split(".")
    # flip a character in the payload segment
    p2 = ("A" if p[0] != "A" else "B") + p[1:]
    _expect(lambda: _verifier(chain).verify(f"{h}.{p2}.{s}"))


# --- (c) signature by a key NOT in the chain is rejected --------------------
def test_wrong_signing_key_rejected():
    chain = Chain()
    other = Chain()  # different leaf key, but present chain's x5c
    tok = chain.sign_jws({"hello": "world"}, sign_key=other.leaf_key)
    _expect(lambda: apple.verify_and_decode_jws(
        tok, trust_anchors=[chain.root_der()], now=datetime.now(timezone.utc)))


# --- (d) chain that doesn't terminate in a trusted root is rejected ---------
def test_untrusted_root_rejected():
    chain = Chain()
    stranger = Chain()
    tok = build_notification(chain)
    # verify against a DIFFERENT root -> untrusted
    v = apple.AppleNotificationVerifier(trust_anchors=[stranger.root_der()])
    _expect(lambda: v.verify(tok))


# --- (e) expired leaf certificate is rejected -------------------------------
def test_expired_leaf_rejected():
    past = datetime.now(timezone.utc) - timedelta(days=2)
    chain = Chain(leaf_not_after=past)
    tok = build_notification(chain)
    _expect(lambda: _verifier(chain).verify(tok))


# --- (f) non-ES256 alg (alg confusion / 'none') is rejected -----------------
def test_bad_alg_rejected():
    chain = Chain()
    tok = chain.sign_jws({"x": 1}, alg="none")
    _expect(lambda: apple.verify_and_decode_jws(
        tok, trust_anchors=[chain.root_der()], now=datetime.now(timezone.utc)))


# --- (g) SUBSCRIBED projects active entitlements ----------------------------
def test_subscribed_grants_entitlements():
    chain = Chain()
    uid = "600"
    tok = build_notification(chain, notification_type="SUBSCRIBED",
                             app_account_token=uid,
                             product_id="com.pulsesoc.premium.monthly")
    res = apple.apply_apple_notification(tok, verifier=_verifier(chain))
    assert res["projected"] is True, res
    assert "premium.profile.customization" in res["granted_keys"], res
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True
    # provider_subs row landed
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_provider_subs "
            "WHERE provider='apple_app_store' AND subject_id=?", (uid,)).fetchone()[0]
        assert n == 1, n
    finally:
        conn.close()


# --- (h) DID_RENEW refreshes; GRACE_PERIOD keeps access ---------------------
def test_renew_and_grace():
    chain = Chain()
    uid = "601"
    otx = "1000000000000601"
    apple.apply_apple_notification(
        build_notification(chain, notification_type="SUBSCRIBED",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    res = apple.apply_apple_notification(
        build_notification(chain, notification_type="DID_RENEW",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    assert res["projected"] is True and res["intent"] == "grant", res
    # Apple signals grace as DID_FAIL_TO_RENEW + subtype GRACE_PERIOD. This test
    # used to build a bare "GRACE_PERIOD" notificationType, which Apple has never
    # sent — so it passed while grace was in fact never once detected in
    # production. The real payload shape is asserted here.
    grace = apple.apply_apple_notification(
        build_notification(chain, notification_type="DID_FAIL_TO_RENEW",
                           subtype="GRACE_PERIOD",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    assert grace["intent"] == "grace" and grace["projected"] is True, grace
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True
    assert _sub_row(uid)["status"] == "grace_period"


def _sub_row(uid):
    """The stored provider subscription row for a user, as a plain dict."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status, cancel_at_period_end FROM business_os_ent_provider_subs "
            "WHERE provider='apple_app_store' AND subject_id=? LIMIT 1", (uid,)).fetchone()
        assert row is not None, f"no subscription row for {uid}"
        return {"status": row[0], "cancel_at_period_end": row[1]}
    finally:
        conn.close()


# --- (h2) billing retry without a grace period ------------------------------
def test_billing_retry_is_distinguished_from_grace():
    """DID_FAIL_TO_RENEW with no subtype means Apple is retrying the card.

    Access has already lapsed at the expiry date, so this must not extend the
    grant — but it also must not read as a plain expiry, because the member can
    fix it by updating their payment method.
    """
    chain = Chain()
    uid, otx = "610", "1000000000000610"
    apple.apply_apple_notification(
        build_notification(chain, notification_type="SUBSCRIBED",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    res = apple.apply_apple_notification(
        build_notification(chain, notification_type="DID_FAIL_TO_RENEW",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    assert res["intent"] == "record", res
    assert _sub_row(uid)["status"] == "billing_retry"


# --- (h3) auto-renew is read from renewalInfo, in both directions -----------
def test_turning_auto_renew_off_then_on_tracks_both_directions():
    """DID_CHANGE_RENEWAL_STATUS fires for both directions.

    The old adapter matched on the notification type alone and set
    ``cancel_at_period_end`` unconditionally, so a member who turned auto-renew
    back ON was recorded as cancelling and was then told their subscription was
    ending while Apple was about to charge them again.
    """
    chain = Chain()
    uid, otx = "611", "1000000000000611"
    apple.apply_apple_notification(
        build_notification(chain, notification_type="SUBSCRIBED",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    assert _sub_row(uid)["cancel_at_period_end"] in (0, False)

    apple.apply_apple_notification(
        build_notification(chain, notification_type="DID_CHANGE_RENEWAL_STATUS",
                           subtype="AUTO_RENEW_DISABLED", auto_renew_status=0,
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    off = _sub_row(uid)
    assert off["cancel_at_period_end"] in (1, True)
    assert off["status"] == "canceled"

    apple.apply_apple_notification(
        build_notification(chain, notification_type="DID_CHANGE_RENEWAL_STATUS",
                           subtype="AUTO_RENEW_ENABLED", auto_renew_status=1,
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    back_on = _sub_row(uid)
    assert back_on["cancel_at_period_end"] in (0, False)
    assert back_on["status"] == "active"


# --- (h4) an informational notification asserts nothing ---------------------
def test_an_informational_notification_does_not_overwrite_known_facts():
    """Silence is not data.

    A PRICE_INCREASE carries no renewal info and no lifecycle meaning. The old
    adapter lowercased the notification type into the status column, so this
    member's status would have become the string "price_increase" — shown to
    them verbatim as their subscription status.
    """
    chain = Chain()
    uid, otx = "612", "1000000000000612"
    apple.apply_apple_notification(
        build_notification(chain, notification_type="DID_CHANGE_RENEWAL_STATUS",
                           subtype="AUTO_RENEW_DISABLED", auto_renew_status=0,
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    apple.apply_apple_notification(
        build_notification(chain, notification_type="PRICE_INCREASE",
                           app_account_token=uid, original_transaction_id=otx,
                           include_renewal_info=False),
        verifier=_verifier(chain))
    after = _sub_row(uid)
    assert after["status"] == "canceled", after
    assert after["cancel_at_period_end"] in (1, True), after


# --- (h5) the original purchase date survives into storage ------------------
def test_original_purchase_date_is_persisted_for_the_subscription_since_row():
    chain = Chain()
    uid, otx = "613", "1000000000000613"
    first = datetime(2025, 1, 1, tzinfo=timezone.utc)
    apple.apply_apple_notification(
        build_notification(chain, notification_type="SUBSCRIBED",
                           app_account_token=uid, original_transaction_id=otx,
                           original_purchase_dt=first),
        verifier=_verifier(chain))
    from services.business_os.entitlements import premium_api as papi
    conn = db.connect()
    try:
        raw = conn.execute(
            "SELECT raw_json FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id=?", (otx,)).fetchone()[0]
    finally:
        conn.close()
    assert (papi._original_purchase_at(raw) or "").startswith("2025-01-01")


# --- (i) REFUND revokes access immediately ----------------------------------
def test_refund_revokes():
    chain = Chain()
    uid = "602"
    otx = "1000000000000602"
    apple.apply_apple_notification(
        build_notification(chain, notification_type="SUBSCRIBED",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True
    res = apple.apply_apple_notification(
        build_notification(chain, notification_type="REFUND",
                           app_account_token=uid, original_transaction_id=otx),
        verifier=_verifier(chain))
    assert res["revoked"] is True, res
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is False


# --- (j) idempotent: same notification twice = one sub row, still granted ----
def test_idempotent_replay():
    chain = Chain()
    uid = "603"
    otx = "1000000000000603"
    tok = build_notification(chain, notification_type="SUBSCRIBED",
                             app_account_token=uid, original_transaction_id=otx)
    apple.apply_apple_notification(tok, verifier=_verifier(chain))
    apple.apply_apple_notification(tok, verifier=_verifier(chain))
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id=?", (otx,)).fetchone()[0]
        assert n == 1, n
    finally:
        conn.close()
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True


# --- (k) unmapped product records the sub but grants nothing -----------------
def test_unmapped_product_no_grant():
    chain = Chain()
    uid = "604"
    res = apple.apply_apple_notification(
        build_notification(chain, notification_type="SUBSCRIBED",
                           app_account_token=uid, product_id="com.pulsesoc.unknown"),
        verifier=_verifier(chain))
    assert res["recorded"] is True and res["projected"] is False, res
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is False


def test_authenticated_storekit_transaction_grants_once():
    chain = Chain()
    uid = "605"
    original = "1000000000000605"
    signed = chain.sign_jws({
        "transactionId": "2000000000000605",
        "originalTransactionId": original,
        "productId": "com.pulsesoc.premium.annual",
        "bundleId": "com.pulsesoc.app",
        "environment": "Production",
        "type": "Auto-Renewable Subscription",
        "expiresDate": ms(datetime.now(timezone.utc) + timedelta(days=365)),
    })
    first = apple.apply_verified_subscription_transaction(
        signed, verifier=_verifier(chain), subject_id=uid)
    second = apple.apply_verified_subscription_transaction(
        signed, verifier=_verifier(chain), subject_id=uid)
    assert first["verified"] is True and second["verified"] is True
    assert ent_svc.has_entitlement(uid, "premium.profile.customization") is True
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_provider_subs WHERE provider_subscription_id=?",
            (original,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_valid_notification_verifies,
        test_tampered_payload_rejected,
        test_wrong_signing_key_rejected,
        test_untrusted_root_rejected,
        test_expired_leaf_rejected,
        test_bad_alg_rejected,
        test_subscribed_grants_entitlements,
        test_renew_and_grace,
        test_billing_retry_is_distinguished_from_grace,
        test_turning_auto_renew_off_then_on_tracks_both_directions,
        test_an_informational_notification_does_not_overwrite_known_facts,
        test_original_purchase_date_is_persisted_for_the_subscription_since_row,
        test_refund_revokes,
        test_idempotent_replay,
        test_unmapped_product_no_grant,
        test_authenticated_storekit_transaction_grants_once,
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
