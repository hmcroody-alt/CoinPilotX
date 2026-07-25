"""R3 slice: account-hold / suspension precedence for premium.profile.customization.

Proves the FIRST vertical entitlement slice: a paid Premium grant must never let
a suspended / disabled / banned / restricted account pass the profile-customization
gate, while an active eligible Premium account continues to pass — and the flag-off
path stays byte-for-byte legacy.

Hermetic. Points services.db at a throwaway SQLite file, seeds a minimal ``users``
table so both the context path and the DB-read path of the account-hold resolver
are exercised. Runs two ways:

    python -m pytest tests/business_os/test_entitlement_account_hold.py
    python tests/business_os/test_entitlement_account_hold.py   # no pytest needed
"""

import os
import tempfile
import time
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ent_hold_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.business_os.entitlements import facade  # noqa: E402
from services import pro_access  # noqa: E402

KEY = "premium.profile.customization"

# uids used across tests
UID_PREMIUM = 100      # legacy stub says premium
UID_FREE = 101         # legacy stub says not premium


def setup_module(module=None):
    svc.ensure_schema()
    # Minimal authoritative users table for the DB-read path of _account_hold.
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_user(uid, account_status="active", access_enabled=1):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
        conn.execute(
            "INSERT INTO users (user_id, account_status, access_enabled) VALUES (?, ?, ?)",
            (uid, account_status, access_enabled),
        )
        conn.commit()
    finally:
        conn.close()


def _reset():
    conn = db.connect()
    for t in ("business_os_ent_grants", "business_os_ent_usage",
              "business_os_ent_audit", "business_os_ent_provider_subs"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    try:
        conn.execute("DELETE FROM users")
    except Exception:
        pass
    conn.commit()
    conn.close()


def _with_stub_legacy(premium_uid=UID_PREMIUM):
    """Deterministic legacy stub: only ``premium_uid`` is premium. The stub
    ignores account_status exactly like the real legacy is_premium_user (that is
    the conflict this slice fixes at the facade layer, not in legacy)."""
    facade._legacy_module = types.SimpleNamespace(
        is_premium_user=lambda uid: int(uid) == premium_uid,
        has_entitlement=lambda uid, key: int(uid) == premium_uid,
    )
    facade._legacy_load_attempted = True


def _future(days=365):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() + days * 86400))


def _past(days=365):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() - days * 86400))


def _active_ctx():
    return {"account_status": "active", "access_enabled": 1}


# 1 -- Active Premium user with active account: allowed -----------------------
def test_active_premium_active_account_allowed():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    # via explicit context
    assert facade.check(UID_PREMIUM, KEY, context=_active_ctx()) is True
    # via DB-read path (no context)
    _seed_user(UID_PREMIUM, "active", 1)
    assert facade.check(UID_PREMIUM, KEY) is True
    ex = facade.explain(UID_PREMIUM, KEY, context=_active_ctx())
    assert ex["allowed"] is True and ex["account_hold"] is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 2 -- Non-Premium user: denied ----------------------------------------------
def test_non_premium_denied():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    assert facade.check(UID_FREE, KEY, context=_active_ctx()) is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 3 -- Premium user with suspended account: denied ----------------------------
def test_premium_suspended_denied():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    ctx = {"account_status": "suspended", "access_enabled": 1}
    assert facade.check(UID_PREMIUM, KEY, context=ctx) is False
    ex = facade.explain(UID_PREMIUM, KEY, context=ctx)
    assert ex["account_hold"] is True and ex["decision_source"] == "account_hold"
    assert ex["reason"] == "account_suspended"
    # DB-read path (banned string, no context) also denies
    _seed_user(UID_PREMIUM, "banned", 1)
    assert facade.check(UID_PREMIUM, KEY) is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 4 -- Premium user with disabled account: denied -----------------------------
def test_premium_disabled_access_denied():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    ctx = {"account_status": "active", "access_enabled": 0}  # active string but access off
    ex = facade.explain(UID_PREMIUM, KEY, context=ctx)
    assert ex["allowed"] is False and ex["reason"] == "account_access_disabled"
    # 'restricted' status likewise denies
    assert facade.check(UID_PREMIUM, KEY,
                        context={"account_status": "restricted", "access_enabled": 1}) is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 5 -- Premium user with expired grant: denied --------------------------------
def test_premium_expired_grant_denied():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    # canonical grant that has expired -> not silent, resolves to deny even though
    # account is active and legacy would say premium.
    svc.grant_entitlement(UID_PREMIUM, KEY, source="stripe",
                          source_reference="s1", expires_at=_past())
    assert facade.check(UID_PREMIUM, KEY, context=_active_ctx()) is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 6 -- Premium user with revoked grant: denied --------------------------------
def test_premium_revoked_grant_denied():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    svc.grant_entitlement(UID_PREMIUM, KEY, source="stripe", source_reference="s1")
    svc.revoke_entitlement(UID_PREMIUM, KEY, reason="chargeback")
    assert facade.check(UID_PREMIUM, KEY, context=_active_ctx()) is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 7 -- Flag off: legacy behavior unchanged ------------------------------------
def test_flag_off_legacy_unchanged():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    assert facade.get_mode() == "off"
    # Suspended premium STILL allowed under off — proves zero behaviour change
    # (the pre-existing suspension-blind legacy result is preserved exactly).
    assert facade.check(UID_PREMIUM, KEY,
                        context={"account_status": "suspended", "access_enabled": 0}) is True
    assert facade.check(UID_FREE, KEY, context=_active_ctx()) is False
    ex = facade.explain(UID_PREMIUM, KEY, context={"account_status": "suspended"})
    assert ex["flag_mode"] == "off" and ex["account_hold"] is False


# 8 -- Flag on: canonical precedence enforced ---------------------------------
def test_flag_on_canonical_precedence_enforced():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    # active eligible premium allowed; suspended premium denied -> the core rule
    assert facade.check(UID_PREMIUM, KEY, context=_active_ctx()) is True
    assert facade.check(UID_PREMIUM, KEY,
                        context={"account_status": "suspended", "access_enabled": 1}) is False
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 9 -- Shadow disagreement recorded correctly ---------------------------------
def test_shadow_records_hold_disagreement():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "shadow"
    ctx = {"account_status": "suspended", "access_enabled": 1}
    # Shadow serves legacy (access unchanged) -> suspended premium still allowed now
    assert facade.check(UID_PREMIUM, KEY, context=ctx) is True
    conn = db.connect()
    row = conn.execute(
        "SELECT after_json FROM business_os_ent_audit WHERE action='shadow_diff' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert "account_hold" in row[0] and "account_suspended" in row[0]
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 10 -- Repeated checks remain idempotent -------------------------------------
def test_repeated_checks_idempotent():
    _reset(); _with_stub_legacy()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    ctx = {"account_status": "suspended", "access_enabled": 1}
    results = [facade.check(UID_PREMIUM, KEY, context=ctx) for _ in range(5)]
    assert results == [False] * 5
    active = [facade.check(UID_PREMIUM, KEY, context=_active_ctx()) for _ in range(5)]
    assert active == [True] * 5
    # a read must not create grant rows
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM business_os_ent_grants").fetchone()[0]
    conn.close()
    assert n == 0
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 11 -- Existing pro_access behavior does not regress -------------------------
def test_pro_access_not_regressed():
    active_pro = {"account_status": "active", "plan": "pro", "subscription_status": "active"}
    suspended_pro = {"account_status": "suspended", "plan": "pro", "subscription_status": "active"}
    assert pro_access.pro_access_type(active_pro) == "paid"
    assert pro_access.has_pro_access(active_pro) is True
    assert pro_access.pro_access_type(suspended_pro) == "none"
    assert pro_access.has_pro_access(suspended_pro) is False


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_active_premium_active_account_allowed,
        test_non_premium_denied,
        test_premium_suspended_denied,
        test_premium_disabled_access_denied,
        test_premium_expired_grant_denied,
        test_premium_revoked_grant_denied,
        test_flag_off_legacy_unchanged,
        test_flag_on_canonical_precedence_enforced,
        test_shadow_records_hold_disagreement,
        test_repeated_checks_idempotent,
        test_pro_access_not_regressed,
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
