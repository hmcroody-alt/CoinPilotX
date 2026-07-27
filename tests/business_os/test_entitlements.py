"""Canonical entitlement service test matrix (Stage 2).

Hermetic: points services.db at a throwaway SQLite file before importing it.
Runs two ways:

    python -m pytest tests/business_os/test_entitlements.py
    python tests/business_os/test_entitlements.py   # no pytest needed

Covers precedence, grant/revoke/suspend, expiry+grace+reconcile, subscription
projection, provider adapters (Stripe real; Apple/Google interface-only), usage
quotas with atomic consumption, and the compatibility facade in all three modes
(off/shadow/canonical) using a stubbed legacy reader so the facade's own logic is
tested independently of the legacy DB coupling.
"""

import os
import tempfile
import time
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ent_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.business_os.entitlements import facade  # noqa: E402
from services.business_os.entitlements import usage  # noqa: E402
from services.business_os.entitlements import providers as prov  # noqa: E402

KEY = "premium.profile.customization"


def setup_module(module=None):
    svc.ensure_schema()


def _reset():
    conn = db.connect()
    for t in ("business_os_ent_grants", "business_os_ent_usage",
              "business_os_ent_audit", "business_os_ent_provider_subs"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _future(days=365):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z",
                         time.gmtime(time.time() + days * 86400))


def _past(days=365):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z",
                         time.gmtime(time.time() - days * 86400))


# 1 -------------------------------------------------------------------------
def test_no_grant_denies():
    _reset()
    assert svc.has_entitlement(1, KEY) is False


# 2 -------------------------------------------------------------------------
def test_active_grant_allows():
    _reset()
    svc.grant_entitlement(1, KEY, source="admin")
    assert svc.has_entitlement(1, KEY) is True


# 3 -------------------------------------------------------------------------
def test_grant_is_idempotent_on_natural_key():
    _reset()
    svc.grant_entitlement(1, KEY, source="admin")
    svc.grant_entitlement(1, KEY, source="admin")
    ex = svc.explain_entitlement(1, KEY)
    assert len(ex["grants"]) == 1


# 4 -------------------------------------------------------------------------
def test_suspension_beats_active():
    _reset()
    svc.grant_entitlement(1, KEY, source="admin")
    svc.suspend_entitlement(1, KEY, reason="fraud")
    assert svc.has_entitlement(1, KEY) is False
    assert svc.explain_entitlement(1, KEY)["mode"] == "suspended"


# 5 -------------------------------------------------------------------------
def test_revoke_denies():
    _reset()
    svc.grant_entitlement(1, KEY, source="admin")
    svc.revoke_entitlement(1, KEY, reason="chargeback")
    assert svc.has_entitlement(1, KEY) is False


# 6 -------------------------------------------------------------------------
def test_later_active_supersedes_revoked():
    _reset()
    # one source revoked, a different active source restores access
    svc.grant_entitlement(1, KEY, source="stripe", source_reference="old")
    svc.revoke_entitlement(1, KEY, reason="refund", source="stripe", source_reference="old")
    svc.grant_entitlement(1, KEY, source="promotion", source_reference="promo1")
    assert svc.has_entitlement(1, KEY) is True
    assert svc.explain_entitlement(1, KEY)["mode"] == "active"


# 7 -------------------------------------------------------------------------
def test_expired_by_clock_denies():
    _reset()
    svc.grant_entitlement(1, KEY, source="stripe", source_reference="s1",
                          expires_at=_past())
    assert svc.has_entitlement(1, KEY) is False


# 8 -------------------------------------------------------------------------
def test_grace_window_allows():
    _reset()
    svc.grant_entitlement(1, KEY, source="stripe", source_reference="s1",
                          expires_at=_past(1), grace_until=_future(3))
    assert svc.has_entitlement(1, KEY) is True
    assert svc.explain_entitlement(1, KEY)["mode"] == "grace"


# 9 -------------------------------------------------------------------------
def test_grandfathered_allows():
    _reset()
    svc.grant_entitlement(1, KEY, source="legacy_migration",
                          status=svc.STATUS_GRANDFATHERED)
    assert svc.has_entitlement(1, KEY) is True
    assert svc.explain_entitlement(1, KEY)["mode"] == "grandfathered"


# 10 ------------------------------------------------------------------------
def test_reconcile_expires_past_clock():
    _reset()
    svc.grant_entitlement(2, "premium.media.higher_quality", source="stripe",
                          source_reference="s2", expires_at=_past())
    out = svc.reconcile_entitlements()
    assert out["expired"] >= 1
    ex = svc.explain_entitlement(2, "premium.media.higher_quality")
    assert ex["grants"][0]["status"] == svc.STATUS_EXPIRED


# 11 ------------------------------------------------------------------------
def test_subscription_projection_grants_catalog_keys():
    _reset()
    res = svc.sync_subscription_entitlements(3, "pulse_premium_monthly",
                                             source="stripe", source_reference="sub_1",
                                             period_end=_future())
    assert KEY in res["granted_keys"]
    keys = [e["key"] for e in svc.get_entitlements(3)]
    assert "premium.undx.advanced" in keys


# 12 ------------------------------------------------------------------------
def test_subscription_sync_is_idempotent():
    _reset()
    svc.sync_subscription_entitlements(3, "pulse_premium_monthly", source="stripe",
                                       source_reference="sub_1", period_end=_future())
    svc.sync_subscription_entitlements(3, "pulse_premium_monthly", source="stripe",
                                       source_reference="sub_1", period_end=_future())
    ex = svc.explain_entitlement(3, KEY)
    assert len(ex["grants"]) == 1


# 13 ------------------------------------------------------------------------
def test_merchant_approval_cannot_grant_premium():
    _reset()
    try:
        svc.grant_entitlement(4, KEY, source="merchant_approval")
    except svc.EntitlementError:
        return
    raise AssertionError("merchant_approval should not grant premium.* keys")


# 14 ------------------------------------------------------------------------
def test_get_entitlements_lists_only_allowed():
    _reset()
    svc.grant_entitlement(5, KEY, source="admin")
    svc.grant_entitlement(5, "premium.undx.advanced", source="admin", expires_at=_past())
    keys = [e["key"] for e in svc.get_entitlements(5)]
    assert KEY in keys and "premium.undx.advanced" not in keys


# 15 ------------------------------------------------------------------------
def test_get_entitlement_limits():
    _reset()
    svc.grant_entitlement(6, "crypto.alerts.advanced", source="stripe",
                          source_reference="c1", limit_value=50, limit_period="day")
    lim = svc.get_entitlement_limits(6, "crypto.alerts.advanced")
    assert lim["limit_value"] == 50 and lim["limit_period"] == "day"


# 16 ------------------------------------------------------------------------
def test_explain_trace_has_decision_grant():
    _reset()
    svc.grant_entitlement(7, KEY, source="admin")
    ex = svc.explain_entitlement(7, KEY)
    assert ex["allowed"] and ex["decision_grant_id"] is not None
    assert ex["grants"][0]["phase"] == "active"


# 17 ------------------------------------------------------------------------
def test_usage_meter_consume_to_limit():
    _reset()
    svc.grant_entitlement(8, "crypto.alerts.advanced", source="stripe",
                          source_reference="c1", limit_value=3, limit_period="day")
    r1 = usage.check_and_consume(8, "crypto.alerts.advanced")
    r2 = usage.check_and_consume(8, "crypto.alerts.advanced", amount=2)
    assert r1["allowed"] and r2["allowed"] and r2["used"] == 3 and r2["remaining"] == 0


# 18 ------------------------------------------------------------------------
def test_usage_over_limit_denies_without_counting():
    _reset()
    svc.grant_entitlement(8, "crypto.alerts.advanced", source="stripe",
                          source_reference="c1", limit_value=1, limit_period="day")
    usage.check_and_consume(8, "crypto.alerts.advanced")
    r = usage.check_and_consume(8, "crypto.alerts.advanced")
    assert r["allowed"] is False and r["reason"] == "quota_exceeded"
    assert usage.get_usage(8, "crypto.alerts.advanced", period_key=r["period_key"]) == 1


# 19 ------------------------------------------------------------------------
def test_usage_unlimited_boolean_capability():
    _reset()
    svc.grant_entitlement(9, KEY, source="admin")
    r = usage.check_and_consume(9, KEY)
    assert r["allowed"] and r["limit"] is None and r["reason"] == "unlimited"


# 20 ------------------------------------------------------------------------
def test_usage_not_entitled_denies():
    _reset()
    r = usage.check_and_consume(999, "crypto.alerts.advanced")
    assert r["allowed"] is False and r["reason"] == "not_entitled"


# 21 ------------------------------------------------------------------------
def _with_stub_legacy(is_premium_uid):
    """Install a deterministic legacy stub; user==is_premium_uid is premium."""
    facade._legacy_module = types.SimpleNamespace(
        is_premium_user=lambda uid: int(uid) == is_premium_uid,
        has_entitlement=lambda uid, key: int(uid) == is_premium_uid,
    )
    facade._legacy_load_attempted = True


def test_facade_off_serves_legacy():
    _reset()
    _with_stub_legacy(100)
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    assert facade.get_mode() == "off"
    assert facade.check(100, KEY) is True
    assert facade.check(101, KEY) is False


# 22 ------------------------------------------------------------------------
def test_facade_shadow_serves_legacy_records_diff():
    _reset()
    _with_stub_legacy(100)
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "shadow"
    # canonical silent, legacy True -> serve legacy True, record a diff
    assert facade.check(100, KEY) is True
    cmp = facade.shadow_compare(100, KEY)
    assert cmp["legacy"] is True and cmp["canonical"] is False and cmp["differs"] is True
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM business_os_ent_audit WHERE action='shadow_diff'").fetchone()[0]
    conn.close()
    assert n >= 1


# 23 ------------------------------------------------------------------------
def test_facade_canonical_authoritative_with_fallback():
    _reset()
    _with_stub_legacy(100)
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    assert facade.get_mode() == "canonical"
    # canonical silent -> legacy fallback
    assert facade.check(100, KEY) is True
    assert facade.check(101, KEY) is False
    # canonical grant is authoritative
    svc.grant_entitlement(200, KEY, source="admin")
    assert facade.check(200, KEY) is True
    # canonical suspension denies with no silent fallback
    svc.suspend_entitlement(200, KEY, reason="hold")
    assert facade.check(200, KEY) is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"  # restore default


# 24 ------------------------------------------------------------------------
def test_stripe_adapter_maps_projects_idempotent():
    _reset()
    future = int(time.time()) + 86400 * 365
    evt = {"type": "customer.subscription.updated", "data": {"object": {
        "id": "sub_ABC", "status": "active", "current_period_end": future,
        "items": {"data": [{"price": {"id": "price_premium_monthly"}}]},
        "metadata": {"pulse_user_id": "77"}}}}
    res = prov.apply_stripe_subscription(evt)
    assert res["projected"] and KEY in res["granted_keys"]
    assert svc.has_entitlement(77, KEY) is True
    prov.apply_stripe_subscription(evt)  # replay
    conn = db.connect()
    subs = conn.execute("SELECT COUNT(*) FROM business_os_ent_provider_subs WHERE provider_subscription_id='sub_ABC'").fetchone()[0]
    grants = conn.execute("SELECT COUNT(*) FROM business_os_ent_grants WHERE subject_id='77' AND entitlement_key=?", (KEY,)).fetchone()[0]
    conn.close()
    assert subs == 1 and grants == 1


# 25 ------------------------------------------------------------------------
def test_stripe_adapter_unmapped_price_records_not_projects():
    _reset()
    evt = {"data": {"object": {"id": "sub_X", "status": "active",
           "items": {"data": [{"price": {"id": "price_unknown"}}]},
           "metadata": {"pulse_user_id": "88"}}}}
    r = prov.apply_stripe_subscription(evt)
    assert r["recorded"] and r["projected"] is False
    assert svc.has_entitlement(88, KEY) is False


# 26 ------------------------------------------------------------------------
def test_apple_google_refuse_to_fabricate_success():
    for Adapter in (prov.AppleAppStoreAdapter, prov.GooglePlayAdapter):
        a = Adapter()
        for meth in ("verify_notification", "apply"):
            try:
                getattr(a, meth)({"x": 1})
            except prov.ProviderNotImplemented:
                continue
            raise AssertionError(f"{Adapter.__name__}.{meth} must raise ProviderNotImplemented")


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_no_grant_denies,
        test_active_grant_allows,
        test_grant_is_idempotent_on_natural_key,
        test_suspension_beats_active,
        test_revoke_denies,
        test_later_active_supersedes_revoked,
        test_expired_by_clock_denies,
        test_grace_window_allows,
        test_grandfathered_allows,
        test_reconcile_expires_past_clock,
        test_subscription_projection_grants_catalog_keys,
        test_subscription_sync_is_idempotent,
        test_merchant_approval_cannot_grant_premium,
        test_get_entitlements_lists_only_allowed,
        test_get_entitlement_limits,
        test_explain_trace_has_decision_grant,
        test_usage_meter_consume_to_limit,
        test_usage_over_limit_denies_without_counting,
        test_usage_unlimited_boolean_capability,
        test_usage_not_entitled_denies,
        test_facade_off_serves_legacy,
        test_facade_shadow_serves_legacy_records_diff,
        test_facade_canonical_authoritative_with_fallback,
        test_stripe_adapter_maps_projects_idempotent,
        test_stripe_adapter_unmapped_price_records_not_projects,
        test_apple_google_refuse_to_fabricate_success,
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
