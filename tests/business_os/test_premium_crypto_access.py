"""Stage 1: the Premium crypto intelligence entitlement spine.

Hermetic — points ``services.db`` at a throwaway SQLite file before importing it,
so it runs the same under pytest or standalone:

    python -m pytest tests/business_os/test_premium_crypto_access.py
    python tests/business_os/test_premium_crypto_access.py

What is actually being defended here
------------------------------------
The gate has two halves and only one of them is obvious. The obvious half is the
capability tuple in ``entitlements.premium``. The half that decides production
behaviour is ``facade._LEGACY_READERS``: ``BUSINESS_OS_ENTITLEMENTS`` defaults to
``off``, and in that mode the facade answers *only* from the legacy readers. A
key missing from that map has no legacy opinion, which collapses to ``False`` for
every account — a paying member included. ``test_every_crypto_key_has_a_legacy_reader``
is the test that stops a future key from shipping into that hole.
"""

import os
import tempfile
import time
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="prem_crypto_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services import premium_crypto_access as pca  # noqa: E402
from services.business_os.entitlements import facade  # noqa: E402
from services.business_os.entitlements import premium as prem  # noqa: E402
from services.business_os.entitlements import readiness as rd  # noqa: E402
from services.business_os.entitlements import schema as ent_schema  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402


def setup_module(module=None):
    svc.ensure_schema()


def _reset():
    conn = db.connect()
    for t in ("business_os_ent_grants", "business_os_ent_audit"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()
    conn.close()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


def _stub_legacy(premium_uids):
    """Legacy answers True only for ``premium_uids``. Used by the facade paths."""
    ids = {int(u) for u in premium_uids}
    facade._legacy_module = types.SimpleNamespace(
        is_premium_user=lambda uid: int(uid) in ids,
        _is_premium_user_raw=lambda uid: int(uid) in ids,
        has_entitlement=lambda uid, key: int(uid) in ids,
    )
    facade._legacy_load_attempted = True


# --- account rows, shaped the way the routes hand them to the gate ----------
def _free(uid=1):
    return {"user_id": uid, "premium_status": "none", "subscription_status": "none",
            "account_status": "active", "access_enabled": 1}


def _premium(uid=2, plan="pulse-premium"):
    return {"user_id": uid, "premium_status": "active", "subscription_plan": plan,
            "subscription_status": "active", "account_status": "active",
            "access_enabled": 1}


def _expired(uid=3):
    return {"user_id": uid, "premium_status": "expired", "subscription_plan": "pulse-premium",
            "subscription_status": "canceled", "account_status": "active",
            "access_enabled": 1}


# PCRY-001 -------------------------------------------------------------------
def test_free_user_is_denied_every_crypto_capability():
    _reset()
    row = _free()
    for key in pca.CRYPTO_CAPABILITIES:
        assert pca.allowed(row, key) is False, f"{key} leaked to a free account"


# PCRY-002 -------------------------------------------------------------------
def test_premium_user_is_allowed_every_crypto_capability():
    _reset()
    row = _premium()
    for key in pca.CRYPTO_CAPABILITIES:
        assert pca.allowed(row, key) is True, f"{key} denied to a Premium member"


# PCRY-003 -------------------------------------------------------------------
def test_existing_subscriber_inherits_without_a_canonical_grant():
    """The compatibility case: production runs with the flag off and no crypto
    grant row has ever been written. A member who paid before this change must
    still be entitled, purely from the legacy reader."""
    _reset()
    _stub_legacy([2])
    conn = db.connect()
    n = conn.execute(
        "SELECT COUNT(*) FROM business_os_ent_grants WHERE entitlement_key LIKE 'premium.crypto.%'"
    ).fetchone()[0]
    conn.close()
    assert n == 0, "precondition: no canonical crypto grant exists"

    assert facade.get_mode() == "off"
    for key in pca.CRYPTO_CAPABILITIES:
        assert pca.allowed(_premium(uid=2), key) is True
        assert facade.check(2, key) is True


# PCRY-004 -------------------------------------------------------------------
def test_expired_premium_is_denied():
    _reset()
    row = _expired()
    for key in pca.CRYPTO_CAPABILITIES:
        assert pca.allowed(row, key) is False, f"{key} survived expiry"


# PCRY-005 -------------------------------------------------------------------
def test_restored_premium_is_accepted_again():
    _reset()
    uid = 4
    assert pca.allowed(_expired(uid), pca.PORTFOLIO) is False
    # Restore is a change of account state, not a new key: the same row read
    # after Apple restores the subscription must flip the answer back.
    assert pca.allowed(_premium(uid), pca.PORTFOLIO) is True


# PCRY-006 -------------------------------------------------------------------
def test_every_crypto_key_has_a_legacy_reader():
    """The correctness crux. Without a legacy reader the default ``off`` mode
    denies the key to everyone, including paying members."""
    for key in pca.CRYPTO_CAPABILITIES:
        assert key in facade._LEGACY_READERS, (
            f"{key} has no legacy reader; in the default off mode the facade "
            "would deny it to every account, paying members included")


# PCRY-007 -------------------------------------------------------------------
def test_crypto_keys_are_registered_as_canonical_premium_capabilities():
    for key in pca.CRYPTO_CAPABILITIES:
        assert key in prem.PREMIUM_CAPABILITIES, f"{key} missing from the registry"


# PCRY-008 -------------------------------------------------------------------
def test_crypto_keys_are_seeded_on_every_plan_that_confers_membership():
    """Membership-wide capability: any plan granting ``premium.access`` must
    grant the crypto keys too, or cutting over to canonical mode would revoke a
    capability the legacy reader already allows."""
    seeded: dict[str, set] = {}
    for plan_key, ent_key, _limit, _period in ent_schema._SEED_CATALOG:
        seeded.setdefault(plan_key, set()).add(ent_key)
    for plan_key, keys in seeded.items():
        if prem.PREMIUM_ACCESS not in keys:
            continue
        for key in pca.CRYPTO_CAPABILITIES:
            assert key in keys, f"plan {plan_key} grants premium.access but not {key}"


# PCRY-009 -------------------------------------------------------------------
#: Crypto capabilities a real gate reads today. Adding a key here is the
#: deliberate act of declaring it sellable, and belongs in the same change that
#: ships its gate — never ahead of it.
#:
#: INTELLIGENCE joined on the change that shipped its gate:
#: ``undx_personal_intelligence_service._crypto_entitled`` resolves this key
#: through ``premium_crypto_access``, and both grounded reads
#: (``crypto_portfolio_summary`` and ``crypto_market_window``) return the locked
#: outcome when it says no. It is BETA rather than PRODUCTION because of what
#: those reads cannot answer — portfolio performance over time and realized
#: profit and loss are structurally unavailable, having no versioned holdings
#: and no transaction ledger behind them — and ``readiness`` carries that bound
#: as the feature's note so the copy cannot outrun the capability.
GATED_TODAY = {pca.ADVANCED_ALERTS, pca.PORTFOLIO, pca.INTELLIGENCE}


def test_crypto_capabilities_are_not_advertised_before_a_gate_reads_them():
    """Honesty rule: the entitlement resolving is not the feature existing. A
    crypto capability stays out of the sold benefit list until the gate that
    enforces it ships, and joins it only when that gate is named."""
    for key in pca.CRYPTO_CAPABILITIES:
        feature = rd.get(key)
        assert feature is not None, f"{key} missing from the readiness registry"
        if key in GATED_TODAY:
            assert rd.sellable(key) is True, f"{key} has a gate but is not advertised"
            assert feature.enforced_by, f"{key} is sellable but names no gate"
        else:
            assert rd.sellable(key) is False, (
                f"{key} became sellable while no gate reads it")


# PCRY-010 -------------------------------------------------------------------
def test_canonical_mode_grant_is_authoritative():
    _reset()
    _stub_legacy([])  # legacy says no for everyone
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    uid = 20
    row = dict(_free(uid))
    assert pca.allowed(row, pca.ADVANCED_ALERTS) is False
    svc.grant_entitlement(uid, pca.ADVANCED_ALERTS, source="admin")
    assert pca.allowed(row, pca.ADVANCED_ALERTS) is True
    _reset()


# PCRY-011 -------------------------------------------------------------------
def test_account_hold_beats_a_canonical_grant():
    """A suspended account is denied even holding a paid grant."""
    _reset()
    _stub_legacy([21])
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    uid = 21
    svc.grant_entitlement(uid, pca.PORTFOLIO, source="admin")
    row = _premium(uid)
    assert pca.allowed(row, pca.PORTFOLIO) is True
    row["account_status"] = "suspended"
    assert pca.allowed(row, pca.PORTFOLIO) is False
    row["account_status"] = "active"
    row["access_enabled"] = 0
    assert pca.allowed(row, pca.PORTFOLIO) is False
    _reset()


# PCRY-012 -------------------------------------------------------------------
def test_expired_canonical_grant_is_denied():
    _reset()
    _stub_legacy([])
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    uid = 22
    past = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() - 86400 * 30))
    svc.grant_entitlement(uid, pca.INTELLIGENCE, source="stripe", expires_at=past)
    assert pca.allowed(dict(_free(uid)), pca.INTELLIGENCE) is False
    _reset()


# PCRY-013 -------------------------------------------------------------------
def test_missing_account_row_is_denied_not_defaulted():
    _reset()
    assert pca.allowed(None, pca.PORTFOLIO) is False
    assert pca.allowed({}, pca.PORTFOLIO) is False
    # An id with no users row resolves to an empty dict, which is a denial.
    assert pca.allowed_for_user_id(987654321, pca.PORTFOLIO) is False


# PCRY-014 -------------------------------------------------------------------
def test_capabilities_reports_all_three():
    _reset()
    caps = pca.capabilities(_premium())
    assert set(caps) == set(pca.CRYPTO_CAPABILITIES)
    assert all(caps.values())
    assert not any(pca.capabilities(_free()).values())


def _run_standalone():
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all premium crypto entitlement tests passed")


if __name__ == "__main__":
    _run_standalone()
