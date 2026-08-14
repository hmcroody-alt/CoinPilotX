"""Premium canonicalization invariants (PREM-001..).

These tests defend the property that made this migration necessary: there must
be exactly ONE authority for "is this user Premium", and the machinery that
measures the migration must not be able to lie about it.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("DATABASE_URL", "")

from services import db  # noqa: E402
from services.business_os.entitlements import premium as prem  # noqa: E402
from services.business_os.entitlements import schema as sch  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.business_os.entitlements import facade as fac  # noqa: E402

KEY = prem.PREMIUM_ACCESS


def setup_module():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["COINPILOTX_DB_PATH"] = path
    db.reset_for_tests() if hasattr(db, "reset_for_tests") else None
    conn = db.connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, "
        "account_status TEXT DEFAULT 'active', access_enabled INTEGER DEFAULT 1, "
        "premium_status TEXT, subscription_status TEXT, lifetime_premium INTEGER DEFAULT 0, "
        "premium_glow_manual_grant INTEGER DEFAULT 0, premium_mark_override INTEGER DEFAULT 0, "
        "premium_expires_at TEXT, is_pro INTEGER DEFAULT 0, plan TEXT, "
        "subscription_plan TEXT, founder_number INTEGER DEFAULT 0, founder_status TEXT, "
        "email TEXT, display_name TEXT)"
    )
    conn.commit()
    conn.close()
    sch.ensure_ready()


def _mkuser(uid, **cols):
    conn = db.connect()
    conn.execute("INSERT OR REPLACE INTO users (user_id) VALUES (?)", (uid,))
    for k, v in cols.items():
        conn.execute(f"UPDATE users SET {k}=? WHERE user_id=?", (v, uid))
    conn.commit()
    conn.close()


# PREM-001 -------------------------------------------------------------------
def test_every_premium_plan_grants_the_membership_key():
    """Membership must be one canonical fact, not inferred by OR-ing features."""
    conn = db.connect()
    try:
        for plan in prem.PREMIUM_PLAN_KEYS:
            row = conn.execute(
                "SELECT 1 FROM business_os_ent_catalog WHERE plan_key=? AND entitlement_key=?",
                (plan, KEY),
            ).fetchone()
            assert row is not None, f"plan {plan} does not grant {KEY}"
    finally:
        conn.close()


# PREM-002 -------------------------------------------------------------------
def test_flag_off_is_zero_behaviour_change():
    """With the flag off the canonical grant must NOT change access."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    _mkuser(9001)
    svc.grant_entitlement(9001, KEY, source="admin", source_reference="t2")
    state = prem.resolve(9001)
    assert state["flag_mode"] == "off"
    assert state["source"] == "legacy"
    # Legacy has no record of this user, so access stays False despite the grant.
    assert state["is_premium"] is False


# PREM-003 -------------------------------------------------------------------
def test_canonical_mode_honours_the_grant():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9002)
    svc.grant_entitlement(9002, KEY, source="admin", source_reference="t3")
    state = prem.resolve(9002)
    assert state["is_premium"] is True
    assert state["source"] == "canonical_grant"
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-004 -------------------------------------------------------------------
def test_account_hold_beats_a_paid_grant():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9003, account_status="suspended")
    svc.grant_entitlement(9003, KEY, source="stripe", source_reference="t4")
    state = prem.resolve(9003)
    assert state["is_premium"] is False
    assert state["source"] == "account_hold"
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-005 -------------------------------------------------------------------
def test_restore_five_times_creates_one_grant():
    """RESTORE x5 MUST NOT CREATE 5 GRANTS."""
    _mkuser(9004)
    for _ in range(5):
        svc.sync_subscription_entitlements(
            9004, "pulse_premium_annual", status="active",
            source="apple_app_store", source_reference="orig_txn_777",
            period_end="2099-01-01T00:00:00Z",
        )
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_grants WHERE subject_id=? AND entitlement_key=?",
            ("9004", KEY),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1, f"expected 1 grant after 5 restores, got {n}"


# PREM-006 -------------------------------------------------------------------
def test_grandfathered_status_survives_projection():
    """Founders must land as 'grandfathered', not silently downgraded to active."""
    _mkuser(9005)
    svc.sync_subscription_entitlements(
        9005, prem.FOUNDER_PLAN_KEY, status="grandfathered",
        source="admin", source_reference="founder:1",
    )
    conn = db.connect()
    try:
        status = conn.execute(
            "SELECT status FROM business_os_ent_grants WHERE subject_id=? AND entitlement_key=?",
            ("9005", KEY),
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "grandfathered", f"founder grant status was {status!r}"


# PREM-007 -------------------------------------------------------------------
def test_grandfathered_grant_never_expires():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9006)
    svc.sync_subscription_entitlements(
        9006, prem.FOUNDER_PLAN_KEY, status="grandfathered",
        source="admin", source_reference="founder:2",
    )
    assert prem.canonical_mode(9006) == "grandfathered"
    assert prem.resolve(9006)["is_premium"] is True
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-008 -------------------------------------------------------------------
def test_parity_uses_an_independent_legacy_reader():
    """The shim must not be able to fake agreement.

    ``is_premium_user`` now resolves through canonical. If the parity check
    called it, canonical would be compared against itself and every account
    would look in-sync. The facade and the resolver must both reach for the RAW
    legacy reader instead.
    """
    from services import premium_entitlement_service as pes
    assert hasattr(pes, "_is_premium_user_raw"), "raw legacy reader was removed"
    code = fac._legacy_premium_customization.__code__
    referenced = set(code.co_names) | {c for c in code.co_consts if isinstance(c, str)}
    assert "_is_premium_user_raw" in referenced, (
        "facade legacy reader no longer prefers the raw implementation; "
        "shadow mode would compare canonical against itself")

    # And the resolver's own legacy reader must do the same.
    code = prem.legacy_premium.__code__
    referenced = set(code.co_names) | {c for c in code.co_consts if isinstance(c, str)}
    assert "_is_premium_user_raw" in referenced


# PREM-009 -------------------------------------------------------------------
def test_split_brain_is_reported_not_hidden():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9007)
    svc.grant_entitlement(9007, KEY, source="admin", source_reference="t9")
    row = prem.parity(9007)
    # canonical says yes, legacy has never heard of them -> must be visible.
    assert row["canonical"] is True
    assert row["action"] == "verify_canonical"
    assert row["differs"] is True
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-010 -------------------------------------------------------------------
def test_founder_allocation_state_is_explicit():
    """Sequential numbering alone is not a cap; the state must be declarable."""
    os.environ.pop("PULSE_FOUNDER_ALLOCATION_LIMIT", None)
    os.environ.pop("PULSE_FOUNDER_ALLOCATION_CLOSED", None)
    st = prem.founder_allocation_status()
    assert st["state"] == "open_uncapped"
    assert st["limit"] is None
    assert st["accepting_new"] is True
    assert st["price_cents"] == 499

    os.environ["PULSE_FOUNDER_ALLOCATION_CLOSED"] = "1"
    st = prem.founder_allocation_status()
    assert st["state"] == "closed" and st["accepting_new"] is False
    allowed, reason = prem.founder_allocation_available()
    assert allowed is False and reason == "founder_allocation_closed"
    os.environ.pop("PULSE_FOUNDER_ALLOCATION_CLOSED", None)


# PREM-011 -------------------------------------------------------------------
def test_cross_provider_resolves_to_one_account_entitlement():
    """Apple and Stripe for the same user must not double-grant membership."""
    _mkuser(9008)
    svc.sync_subscription_entitlements(
        9008, "pulse_premium_monthly", status="active", source="stripe",
        source_reference="sub_abc", period_end="2099-01-01T00:00:00Z")
    svc.sync_subscription_entitlements(
        9008, "pulse_premium_annual", status="active", source="apple_app_store",
        source_reference="orig_888", period_end="2099-06-01T00:00:00Z")
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    # Two provider rows, but ONE resolved membership answer.
    assert prem.resolve(9008)["is_premium"] is True
    assert prem.canonical_mode(9008) == "active"
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-012 -------------------------------------------------------------------
def test_premium_is_not_verification():
    """Premium must never be presented as identity verification."""
    from services import premium_identity_engine as pie
    for row in ({"premium_status": "active", "is_pro": 1, "plan": "premium"},
                {"founder_number": 7, "founder_status": "active"}):
        mark = pie.identity_mark(row)
        assert mark is not None
        title = str(mark.get("title", "")).lower()
        assert "verified" not in title, (
            f"premium mark title {mark.get('title')!r} claims verification; "
            "a paid subscription is not identity verification")


# PREM-013 -------------------------------------------------------------------
def test_production_features_must_name_their_gate():
    """A capability cannot claim PRODUCTION/BETA without naming what enforces it."""
    from services.business_os.entitlements import readiness as rd
    for f in rd.all_features():
        if f["status"] in ("PRODUCTION", "BETA"):
            assert f["enforced_by"], (
                f"{f['key']} claims {f['status']} but names no enforcing gate; "
                "either point at the code that delivers it or downgrade it")
        else:
            assert not f["enforced_by"], (
                f"{f['key']} is {f['status']} yet names an enforcer — "
                "if it is really enforced, promote it")


# PREM-014 -------------------------------------------------------------------
def test_unimplemented_claims_are_not_sellable():
    """The four audited-as-absent claims must never be advertisable."""
    from services.business_os.entitlements import readiness as rd
    for key in ("premium.ads.free", "premium.undx.credits",
                "premium.security.timeline", "priority_verification"):
        assert rd.get(key) is not None, f"{key} missing from the registry"
        assert rd.sellable(key) is False, (
            f"{key} became sellable; it is not implemented (or is disallowed) "
            "and must not appear on a Premium sales surface")


# PREM-015 -------------------------------------------------------------------
def test_granted_but_unenforced_keys_are_declared():
    """Keys granted on purchase that gate nothing must be declared, not implied."""
    from services.business_os.entitlements import readiness as rd
    unenforced = {f["key"] for f in rd.unenforced_features()}
    for key in ("creator_studio_pro", "founder_hub_access",
                "premium_upload_limits", "priority_support",
                "early_access_features"):
        assert key in unenforced, (
            f"{key} is granted on purchase but no gate reads it; it must stay "
            "declared as not-yet-a-benefit")


def _run_standalone():
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all premium canonicalization tests passed")


if __name__ == "__main__":
    _run_standalone()
