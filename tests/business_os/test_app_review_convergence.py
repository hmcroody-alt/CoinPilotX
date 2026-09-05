"""App Review item 11 — entitlement convergence + security fixes.

Defends the three fixes shipped for App Store review readiness:

1. **Apple IAP <-> legacy convergence.** Apple StoreKit verification writes ONLY
   to the canonical ``business_os_ent_grants`` store. Under the default
   ``BUSINESS_OS_ENTITLEMENTS=off`` flag the legacy path is authoritative, so a
   purchase used to grant an entitlement nothing read. The legacy read path now
   bridges to canonical PROVIDER grants (Apple/Google), evaluated live so
   expiry/refund/suspension revoke through the same path.
2. **Owner identity is no longer a display name.** ``is_owner`` used to grant
   permanent premium to any account whose display name matched the owner's —
   spoofable by renaming. It is now an env allowlist of user ids.
3. **Expiry cross-check.** ``subscription_status='active'`` frozen by a missed
   webhook no longer keeps premium alive once the recorded period end has
   passed. This originally allowed a three-day implicit grace window; that
   window was removed in the premium-expiry recovery, because an implicit
   window cannot distinguish a late webhook from a subscription that genuinely
   ended, and it therefore handed three free days to every lapsed account. A
   late webhook is covered without guessing (a live entitlement row is admitted
   before these columns are read) and a deliberate extension is recorded
   explicitly as ``grace_until`` on the canonical grant. See EXP-001.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

# Bind the engine to a throwaway database BEFORE ``services.db`` is imported
# (first import wins for the whole session; see tests/business_os/conftest.py).
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_appreview_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import db  # noqa: E402
from services import premium_entitlement_service as pes  # noqa: E402
from services import premium_identity_engine as pie  # noqa: E402
from services import pro_access  # noqa: E402
from services.business_os.entitlements import facade as fac  # noqa: E402
from services.business_os.entitlements import premium as prem  # noqa: E402
from services.business_os.entitlements import schema as sch  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402

KEY = prem.PREMIUM_ACCESS

_USERS_COLUMNS = (
    ("account_status", "TEXT DEFAULT 'active'"),
    ("access_enabled", "INTEGER DEFAULT 1"),
    ("premium_status", "TEXT"),
    ("subscription_status", "TEXT"),
    ("lifetime_premium", "INTEGER DEFAULT 0"),
    ("premium_glow_manual_grant", "INTEGER DEFAULT 0"),
    ("premium_mark_override", "INTEGER DEFAULT 0"),
    ("premium_expires_at", "TEXT"),
    # The other expiry columns the access authority reads.
    # ``_is_premium_user_raw`` SELECTs all four by name with no fallback, so a
    # fixture missing one does not quietly degrade — it raises
    # OperationalError, and the assertion it aborts reads like a premium leak.
    # Production's ``users`` carries all of them (bot.py filters and updates on
    # them directly); this table simply predated the clock cross-check.
    ("subscription_expires_at", "TEXT"),
    ("pro_expires_at", "TEXT"),
    ("trial_end_date", "TEXT"),
    ("is_pro", "INTEGER DEFAULT 0"),
    ("plan", "TEXT"),
    ("subscription_plan", "TEXT"),
    ("founder_number", "INTEGER DEFAULT 0"),
    ("founder_status", "TEXT"),
    ("email", "TEXT"),
    ("display_name", "TEXT"),
    ("full_name", "TEXT"),
)


def setup_module():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    conn = db.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    for name, sql_type in _USERS_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
        except Exception:  # noqa: BLE001 — column already present
            pass
    # Legacy tables the raw reader touches that ``ensure_founder_schema`` does
    # not itself create (production creates them in ``bot.init_db``).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS premium_entitlements ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
        "entitlement_key TEXT, status TEXT DEFAULT 'active', source TEXT, "
        "starts_at TEXT, ends_at TEXT, metadata_json TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pulse_premium_entitlements ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
        "entitlement_key TEXT, source TEXT, status TEXT DEFAULT 'active', "
        "starts_at TEXT, expires_at TEXT, created_at TEXT, updated_at TEXT, "
        "UNIQUE(user_id, entitlement_key))"
    )
    conn.commit()
    conn.close()
    sch.ensure_ready()
    pes.ensure_founder_schema()


def _mkuser(uid, **cols):
    conn = db.connect()
    conn.execute("INSERT OR REPLACE INTO users (user_id) VALUES (?)", (uid,))
    for k, v in cols.items():
        conn.execute(f"UPDATE users SET {k}=? WHERE user_id=?", (v, uid))
    conn.commit()
    conn.close()


def _apple_grant(uid, ref, period_end):
    svc.sync_subscription_entitlements(
        uid, "pulse_premium_monthly", status="active",
        source="apple_app_store", source_reference=ref,
        period_end=period_end,
    )


# CONV-001 ---------------------------------------------------------------------
def test_apple_canonical_only_grant_is_premium_under_off_flag():
    """A verified StoreKit purchase must be effective with the flag at its
    production default (off = legacy authoritative)."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    _mkuser(7101)
    _apple_grant(7101, "orig_7101", "2099-01-01T00:00:00Z")
    # Legacy umbrella key resolves through the bridge...
    assert pes.has_entitlement(7101, "premium_access") is True
    # ...so the raw legacy reader, the resolver in off mode, and the facade's
    # off-mode branch all see the buyer as Premium.
    assert pes._is_premium_user_raw(7101) is True
    state = prem.resolve(7101)
    assert state["flag_mode"] == "off"
    assert state["is_premium"] is True
    assert fac.check(7101, "premium.profile.customization") is True


# CONV-002 ---------------------------------------------------------------------
def test_expired_apple_grant_is_not_premium_through_the_bridge():
    """The bridge evaluates LIVE state: a lapsed subscription is not premium."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    _mkuser(7102)
    _apple_grant(7102, "orig_7102", "2000-01-01T00:00:00Z")
    assert pes.has_entitlement(7102, "premium_access") is False
    assert pes._is_premium_user_raw(7102) is False
    assert prem.resolve(7102)["is_premium"] is False


# CONV-003 ---------------------------------------------------------------------
def test_refund_and_suspension_revoke_through_the_bridge():
    """Revocation symmetry: a refunded or held Apple grant loses premium via the
    same read path that granted it — no boolean is ever copied."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    _mkuser(7103)
    _apple_grant(7103, "orig_7103", "2099-01-01T00:00:00Z")
    assert pes.has_entitlement(7103, "premium_access") is True
    svc.revoke_entitlement(
        7103, KEY, reason="apple:REFUND", source="apple_app_store",
        source_reference="orig_7103", actor="apple_adapter")
    assert pes.has_entitlement(7103, "premium_access") is False
    assert prem.resolve(7103)["is_premium"] is False

    _mkuser(7113)
    _apple_grant(7113, "orig_7113", "2099-01-01T00:00:00Z")
    assert pes.has_entitlement(7113, "premium_access") is True
    svc.suspend_entitlement(7113, KEY, reason="compliance_hold", actor="test")
    assert pes.has_entitlement(7113, "premium_access") is False


# CONV-004 ---------------------------------------------------------------------
def test_admin_canonical_grant_stays_dark_under_off_flag():
    """Scope guard: the bridge covers PROVIDER sources only. Non-provider
    canonical grants keep 'flag off = zero behaviour change' (PREM-002)."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    _mkuser(7104)
    svc.grant_entitlement(7104, KEY, source="admin", source_reference="t-admin")
    assert pes.has_entitlement(7104, "premium_access") is False
    assert prem.resolve(7104)["is_premium"] is False


# OWN-001 ----------------------------------------------------------------------
def test_display_name_spoof_no_longer_grants_owner_or_premium():
    os.environ.pop("PULSESOC_OWNER_USER_IDS", None)
    spoof = {
        "user_id": 7105,
        "display_name": "Roody Cherie",
        "full_name": "Roody Cherie",
        "email": "coinpilotxai@gmail.com",
    }
    assert pie.is_owner(spoof) is False
    assert pie.has_active_premium(spoof) is False


# OWN-002 ----------------------------------------------------------------------
def test_env_allowlist_grants_owner_by_user_id_only():
    os.environ["PULSESOC_OWNER_USER_IDS"] = " 42, 7106 , junk"
    try:
        assert pie.is_owner({"user_id": 7106}) is True
        assert pie.is_owner({"user_id": 42, "display_name": "anything"}) is True
        assert pie.has_active_premium({"user_id": 42}) is True
        # An allowlist for OTHER ids does not help the spoofer.
        assert pie.is_owner({"user_id": 7105, "display_name": "Roody Cherie"}) is False
        assert pie.is_owner({}) is False
    finally:
        os.environ.pop("PULSESOC_OWNER_USER_IDS", None)


# EXP-001 ----------------------------------------------------------------------
def test_stale_active_status_with_past_expiry_is_not_premium():
    """A missed webhook leaves status='active' forever; the recorded period end
    wins the moment it passes."""
    stale = {
        "subscription_status": "active",
        "is_pro": 1,
        "plan": "premium",
        "premium_expires_at": "2020-01-01T00:00:00",
    }
    assert pie.has_active_premium(stale) is False

    # Yesterday is expired. This assertion used to read ``is True``, on a
    # three-day implicit grace window meant to absorb a late provider webhook.
    # It absorbed lapsed subscriptions just as happily — the window has no way
    # to tell the two apart — so every genuinely expired member got three free
    # days of Premium. The window is gone; a real extension is recorded as
    # ``grace_until`` on the canonical grant, where it is visible and auditable
    # instead of being applied to everyone silently.
    yesterday = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    assert pie.has_active_premium(dict(stale, premium_expires_at=yesterday)) is False

    # No expiry recorded on the row -> status remains authoritative (we cannot
    # cross-check what is not there; only central helpers were changed).
    no_expiry = {"subscription_status": "active", "is_pro": 1, "plan": "premium"}
    assert pie.has_active_premium(no_expiry) is True


# EXP-002 ----------------------------------------------------------------------
def test_pro_access_paid_requires_unexpired_period_end():
    stale = {"plan": "pro", "subscription_status": "active",
             "pro_expires_at": "2020-01-01T00:00:00"}
    assert pro_access.pro_access_type(stale) == "none"
    assert pro_access.has_pro_access(stale) is False

    # Yesterday is expired — the implicit three-day window is gone here too.
    yesterday = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    assert pro_access.pro_access_type(
        {"plan": "pro", "subscription_status": "active",
         "subscription_expires_at": yesterday}) == "none"
    # Still inside the paid period.
    tomorrow = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    assert pro_access.pro_access_type(
        {"plan": "pro", "subscription_status": "active",
         "subscription_expires_at": tomorrow}) == "paid"
    # No period end recorded -> status remains authoritative.
    assert pro_access.pro_access_type(
        {"plan": "pro", "subscription_status": "active"}) == "paid"


def _run_standalone():
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all app-review convergence tests passed")


if __name__ == "__main__":
    _run_standalone()
