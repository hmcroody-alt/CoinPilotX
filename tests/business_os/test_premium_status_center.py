"""Premium Status Center + Admin Control Center invariants (PREM-016..).

These tests defend the honesty properties of the *surfaces*. The registry can be
correct and the resolver can be correct, and the product can still lie if the
presentation layer is free to assemble its own feature list. So the assertions
here are mostly of the form "there is no input for which this surface can say X".
"""

import os
import sys
import tempfile

# Bind the engine to a throwaway database BEFORE ``services.db`` is imported.
# An empty DATABASE_URL would fall back to the local ``coinpilotx.db`` — the
# developer's real database — and this suite would seed users and grants into it.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_premium_sc_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import db  # noqa: E402
from services.business_os.entitlements import premium as prem  # noqa: E402
from services.business_os.entitlements import premium_api as papi  # noqa: E402
from services.business_os.entitlements import readiness as rd  # noqa: E402
from services.business_os.entitlements import schema as sch  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402

KEY = prem.PREMIUM_ACCESS


def setup_module():
    # Database is the throwaway bound at import time.
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


def teardown_module():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


def _mkuser(uid, **cols):
    conn = db.connect()
    conn.execute("INSERT OR REPLACE INTO users (user_id) VALUES (?)", (uid,))
    for k, v in cols.items():
        conn.execute(f"UPDATE users SET {k}=? WHERE user_id=?", (v, uid))
    conn.commit()
    conn.close()


def _strings(obj):
    """Every string anywhere in a nested payload."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


# PREM-016 -------------------------------------------------------------------
def test_status_center_requires_a_user():
    status, body = papi.status_center(0)
    assert status == 401
    assert body["ok"] is False


def test_status_center_bootstraps_its_schema_before_first_canonical_read(monkeypatch):
    """The /api/premium route must not depend on /api/business-os being opened."""
    calls = []
    monkeypatch.setattr(papi, "_schema_ready", False)
    monkeypatch.setattr(papi._schema, "ensure_ready", lambda: calls.append("schema"))
    monkeypatch.setattr(papi._prem, "resolve", lambda *_a, **_k: {
        "is_premium": False,
        "membership_mode": "none",
        "source": "canonical_grant",
        "account_hold": False,
        "account_status": "active",
    })
    monkeypatch.setattr(papi, "_founder_facts", lambda _uid: {
        "is_founder": False, "founder_number": 0, "price_cents": None})
    monkeypatch.setattr(papi, "subscription_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(papi._rd, "all_features", lambda: [])
    monkeypatch.setattr(papi._rd, "unenforced_features", lambda: [])

    status, body = papi.status_center(9199)

    assert status == 200 and body["membership"]["is_premium"] is False
    assert body["membership"]["mode"] == "none"
    assert calls == ["schema"]


# PREM-017 -------------------------------------------------------------------
def test_advertised_benefits_are_always_sellable():
    """The surface must not be able to advertise an unenforced capability."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9101)
    svc.grant_entitlement(9101, KEY, source="admin", source_reference="sc1")
    status, body = papi.status_center(9101)
    assert status == 200
    assert body["benefits"], "a member should see at least one benefit"
    for b in body["benefits"]:
        assert rd.sellable(b["key"]), (
            f"{b['key']} was advertised as a benefit but the readiness registry "
            "says it is not sellable")
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-018 -------------------------------------------------------------------
def test_making_a_feature_unsellable_removes_it_from_the_surface(monkeypatch):
    """Proves the benefit list is derived, not hand-maintained.

    If this fails, the list has drifted into a hardcoded copy and the registry
    has stopped being the authority it claims to be.
    """
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9102)
    svc.grant_entitlement(9102, KEY, source="admin", source_reference="sc2")
    _, before = papi.status_center(9102)
    keys_before = {b["key"] for b in before["benefits"]}
    assert KEY in keys_before

    real_all = rd.all_features

    def demoted():
        out = []
        for f in real_all():
            f = dict(f)
            if f["key"] == KEY:
                f["sellable"] = False
            out.append(f)
        return out

    monkeypatch.setattr(rd, "all_features", demoted)
    _, after = papi.status_center(9102)
    assert KEY not in {b["key"] for b in after["benefits"]}
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-019 -------------------------------------------------------------------
def test_no_allowance_is_invented_for_boolean_capabilities():
    """A capability with no limit_value must report no number at all."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9103)
    svc.grant_entitlement(9103, KEY, source="admin", source_reference="sc3")
    _, body = papi.status_center(9103)
    for b in body["benefits"]:
        if "allowance" in b:
            assert b["allowance"]["limit"] is not None, (
                f"{b['key']} reported an allowance with no real limit behind it")
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-020 -------------------------------------------------------------------
def test_status_center_never_claims_verification():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9104)
    svc.grant_entitlement(9104, KEY, source="admin", source_reference="sc4")
    _, body = papi.status_center(9104)
    haystack = " ".join(_strings(body)).lower()
    # The disclaimer itself contains the word, so check the claim shape instead.
    assert "premium verified" not in haystack
    assert "verified badge" not in haystack
    assert body["not_verification"]
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-021 -------------------------------------------------------------------
def test_a_held_account_is_never_shown_usable_access():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9105, account_status="suspended")
    svc.grant_entitlement(9105, KEY, source="stripe", source_reference="sc5")
    _, body = papi.status_center(9105)
    assert body["membership"]["usable_now"] is False
    assert body["membership"]["on_hold"] is True
    assert any(n["code"] == "account_hold" for n in body["notices"])
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-022 -------------------------------------------------------------------
def test_granted_but_unenforced_keys_are_declared_not_hidden():
    """Hiding them would make the grant look like a benefit."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9106)
    svc.grant_entitlement(9106, KEY, source="admin", source_reference="sc6")
    _, body = papi.status_center(9106)
    declared = {f["key"] for f in body["not_yet"]}
    assert "creator_studio_pro" in declared
    assert "priority_support" in declared
    # ...and they must never appear as benefits.
    assert not declared & {b["key"] for b in body["benefits"]}
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-023 -------------------------------------------------------------------
def test_blocked_claims_never_reach_the_user_surface():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9107)
    svc.grant_entitlement(9107, KEY, source="admin", source_reference="sc7")
    _, body = papi.status_center(9107)
    surfaced = {b["key"] for b in body["benefits"]} | {f["key"] for f in body["not_yet"]}
    assert "priority_verification" not in surfaced, (
        "a BLOCKED claim reached a user-facing surface")
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# PREM-024 -------------------------------------------------------------------
def test_admin_explain_shows_every_authority():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9108)
    svc.grant_entitlement(9108, KEY, source="admin", source_reference="sc8")
    status, body = papi.admin_explain_user(9108)
    assert status == 200
    assert set(body["resolved"]["authorities"]) == {
        "legacy", "canonical", "identity_columns"}
    assert body["parity"]["action"] in {
        "in_sync", "backfill_grant", "repair_grant", "verify_canonical"}
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


def test_admin_explain_requires_a_user():
    status, body = papi.admin_explain_user(None)
    assert status == 400 and body["ok"] is False


# PREM-025 -------------------------------------------------------------------
def test_admin_overview_reports_cutover_safety():
    status, body = papi.admin_overview(parity_limit=10)
    assert status == 200
    assert "safe_to_cut_over" in body["cutover"]
    assert body["readiness"]["counts"]
    assert body["founder_allocation"]["state"]


# PREM-026 -------------------------------------------------------------------
def test_a_failed_parity_sweep_is_not_reported_as_safe(monkeypatch):
    """Absent evidence must not read as evidence of safety."""
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prem, "parity_report", boom)
    status, body = papi.admin_overview()
    assert status == 200
    assert body["cutover"]["safe_to_cut_over"] is False, (
        "a cutover was reported safe when the check that proves it could not run")


# PREM-027 -------------------------------------------------------------------
def test_health_degrades_rather_than_raising(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prem, "parity_report", boom)
    out = papi.health()
    assert out["ok"] is False
    assert out["safe_to_cut_over"] is False
    names = {c["name"] for c in out["checks"]}
    assert "premium_parity" in names


def test_health_reports_checks_when_healthy():
    out = papi.health()
    assert isinstance(out["checks"], list) and out["checks"]
    for c in out["checks"]:
        assert set(c) == {"name", "ok", "detail"}


# PREM-028..031 -- safe billing facts ----------------------------------------
#
# ``business_os_ent_provider_subs`` stores two things a member must never see:
# ``provider_subscription_id`` (Apple's original transaction id, which is a
# stable cross-app identifier) and ``raw_json`` (the decoded transaction). The
# Control Center shows a plan, a provider, a status and a renewal date, and the
# tests below are the reason a future ``SELECT *`` cannot quietly publish the
# rest of the row.
def _mksub(uid, **cols):
    conn = db.connect()
    row = {
        "provider": "apple_iap",
        "provider_subscription_id": f"200000{uid}",
        "subject_type": "user",
        "subject_id": str(uid),
        "plan_key": "pulse_premium_annual",
        "status": "active",
        "current_period_end": "2027-01-01T00:00:00Z",
        "cancel_at_period_end": 0,
        "raw_json": '{"transactionId": "200000%s", "receipt": "SECRET"}' % uid,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    row.update(cols)
    conn.execute(
        "INSERT INTO business_os_ent_provider_subs (%s) VALUES (%s)"
        % (", ".join(row), ", ".join("?" for _ in row)),
        tuple(row.values()),
    )
    conn.commit()
    conn.close()


def test_subscription_summary_returns_only_safe_columns():
    _mkuser(9130)
    _mksub(9130)
    summary = papi.subscription_summary(9130)
    assert set(summary) == {
        "provider", "plan_key", "billing_period", "status",
        "current_period_end", "cancel_at_period_end",
        # The member-facing subscription card needs more than the provider's own
        # bookkeeping. Every one of these is derived from the row, and none of
        # them is a secret — see the leak tests immediately below.
        "state", "auto_renew", "renews_at", "expires_at",
        "product_id", "original_purchase_at",
    }
    assert summary["billing_period"] == "annual"


def test_subscription_summary_never_publishes_a_column_by_accident():
    """The allowlist is the point.

    The summary is assembled by naming fields, never by copying the row, so
    adding a column to ``business_os_ent_provider_subs`` cannot quietly publish
    it. This asserts that boundary directly rather than trusting the set literal
    above to be kept honest by whoever edits it next.
    """
    _mkuser(9138)
    _mksub(9138)
    summary = papi.subscription_summary(9138)
    for forbidden in ("raw_json", "provider_subscription_id", "id",
                      "subject_id", "subject_type", "created_at", "updated_at"):
        assert forbidden not in summary


def test_subscription_summary_never_leaks_the_provider_token_or_receipt():
    _mkuser(9131)
    _mksub(9131, provider_subscription_id="1000000999888777",
           raw_json='{"receipt": "LEAK", "originalTransactionId": "1000000999888777"}')
    summary = papi.subscription_summary(9131)
    blob = " ".join(_strings(summary))
    assert "1000000999888777" not in blob
    assert "LEAK" not in blob
    assert "receipt" not in blob.lower()


# PREM-032..039 -- the facts the member's subscription card is built from ------
#
# Each of these corresponds to one row on the Premium screen. The rule they
# share: a fact we do not have is reported as absent, never as a default that
# happens to read well.

def test_auto_renewing_subscription_reports_a_renewal_date_not_an_end_date():
    _mkuser(9140)
    _mksub(9140, cancel_at_period_end=0, current_period_end="2027-01-01T00:00:00Z")
    summary = papi.subscription_summary(9140)
    assert summary["auto_renew"] is True
    assert summary["renews_at"] == "2027-01-01T00:00:00Z"
    # Both dates are the same instant; only one of them is the truth about what
    # happens when it arrives, and the screen must not be able to show both.
    assert summary["expires_at"] is None


def test_cancelled_subscription_reports_an_end_date_not_a_renewal_date():
    """Auto-renew off is the case the old screen got wrong.

    A member who cancels keeps access until the period ends. Telling them it
    "renews" on that date is the opposite of what will happen.
    """
    _mkuser(9141)
    _mksub(9141, cancel_at_period_end=1, status="canceled",
           current_period_end="2027-01-01T00:00:00Z")
    summary = papi.subscription_summary(9141)
    assert summary["auto_renew"] is False
    assert summary["renews_at"] is None
    assert summary["expires_at"] == "2027-01-01T00:00:00Z"
    assert summary["state"] == "canceled"


def test_a_lapsed_row_still_marked_active_reports_expired():
    """The clock outranks a stale status string.

    A dropped or delayed ``EXPIRED`` notification would otherwise leave this row
    reading ``active`` forever, showing a lapsed member a live subscription.
    """
    _mkuser(9142)
    _mksub(9142, status="active", current_period_end="2020-01-01T00:00:00Z")
    summary = papi.subscription_summary(9142)
    assert summary["state"] == "expired"
    assert summary["renews_at"] is None
    assert summary["expires_at"] == "2020-01-01T00:00:00Z"


def test_an_unrecognised_provider_status_becomes_unknown_not_itself():
    """Raw provider jargon must never reach a member's screen.

    Rows written before the Apple adapter had a fixed status vocabulary hold
    strings like ``did_change_renewal_status``. ``state`` is what the screen
    switches on, and it is closed over a known set.
    """
    _mkuser(9143)
    _mksub(9143, status="did_change_renewal_status")
    summary = papi.subscription_summary(9143)
    assert summary["state"] == "unknown"


def test_original_purchase_date_is_read_from_a_signed_transaction():
    _mkuser(9144)
    _mksub(9144, raw_json='{"productId": "com.pulsesoc.premium.annual",'
                          ' "originalPurchaseDate": 1735689600000}')
    summary = papi.subscription_summary(9144)
    assert (summary["original_purchase_at"] or "").startswith("2025-01-01")


def test_original_purchase_date_is_read_from_a_notification_payload():
    """Two shapes land in ``raw_json`` and both have to work.

    A purchase stores the bare transaction; an App Store notification stores the
    whole envelope. A member whose row was last written by a renewal
    notification must still see when they first subscribed.
    """
    _mkuser(9145)
    _mksub(9145, raw_json='{"notificationType": "DID_RENEW", "data":'
                          ' {"transactionInfo": {"originalPurchaseDate": 1735689600000}}}')
    summary = papi.subscription_summary(9145)
    assert (summary["original_purchase_at"] or "").startswith("2025-01-01")


def test_a_missing_original_purchase_date_is_absent_not_invented():
    _mkuser(9146)
    _mksub(9146, raw_json='{"productId": "com.pulsesoc.premium.annual"}')
    assert papi.subscription_summary(9146)["original_purchase_at"] is None


def test_product_id_round_trips_from_the_verified_plan_key():
    """The app needs the identifier to ask StoreKit for the member's own price.

    It is derived from ``plan_key``, which was itself set from a verified
    ``productId``, so this is still reporting what Apple signed.
    """
    _mkuser(9147)
    _mksub(9147, plan_key="pulse_premium_annual")
    assert papi.subscription_summary(9147)["product_id"] == "com.pulsesoc.premium.annual"


def test_status_center_carries_the_same_safe_subscription():
    """The screen reads this through ``status_center``, so assert it there too."""
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    _mkuser(9132)
    svc.grant_entitlement(9132, KEY, source="admin", source_reference="sc9132")
    _mksub(9132, provider_subscription_id="3000009132", raw_json='{"receipt": "LEAK9132"}')
    _, body = papi.status_center(9132)
    blob = " ".join(_strings(body))
    assert "3000009132" not in blob
    assert "LEAK9132" not in blob
    assert body["subscription"]["provider"] == "apple_iap"
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


def test_a_granted_member_with_no_provider_row_is_not_an_error():
    """``None`` is a real answer: founders and admin grants have no billing.

    The entitlement is real and the billing row is absent, which is exactly the
    founder/grandfathered shape. The Control Center has to render that as "no
    billing on file" rather than treating the missing row as a failed read.
    """
    _mkuser(9133)
    svc.grant_entitlement(9133, KEY, source="admin", source_reference="sc9133")
    assert papi.subscription_summary(9133) is None


def test_the_newest_subscription_wins_after_resubscribing():
    """A member who lapsed and came back has two rows; only one is current."""
    _mkuser(9134)
    _mksub(9134, provider_subscription_id="4000009134a", plan_key="pulse_premium_monthly",
           status="expired", updated_at="2026-01-01T00:00:00Z")
    _mksub(9134, provider_subscription_id="4000009134b", plan_key="pulse_premium_annual",
           status="active", updated_at="2026-06-01T00:00:00Z")
    summary = papi.subscription_summary(9134)
    assert summary["status"] == "active"
    assert summary["billing_period"] == "annual"


def _run_standalone():
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            if fn.__code__.co_argcount:
                continue  # needs pytest fixtures
            fn()
            print(f"ok  {name}")
    print("premium status center tests passed")


if __name__ == "__main__":
    _run_standalone()
