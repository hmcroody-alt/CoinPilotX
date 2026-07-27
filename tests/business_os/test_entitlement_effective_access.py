"""R3.2 slice: effective (currently-usable) premium access for presentation payloads.

R3.2 migrates the Premium *status / display* callers (``/api/premium/status``,
``/pulse/premium``, creator studio panel, creator analytics) so the API/UI never
advertises *usable* Premium access to an account that is on hold, WITHOUT ever
describing an owner as lacking their underlying subscription.

The single server-side authority both the gates (R3/R3.1) and these presentation
callers share is ``facade.account_hold(subject_id, context)``. The bot.py helper
``_effective_premium_access(user, owns)`` is a thin flag-gated wrapper over it:

    off / shadow -> present the legacy ownership value unchanged (owns, None)
    canonical    -> effective = owns AND NOT account_hold; denial reason set on hold

bot.py itself is not importable in this hermetic sandbox (it pulls stripe / flask /
telegram and the full services import block, and PyPI is unavailable), so this suite
proves the shared authority directly and asserts the exact decision rule the wrapper
implements. The wrapper's flag-gating + the five rewired call sites are additionally
verified by byte-compilation and code inspection (see report EXPANSION VII).

    python -m pytest tests/business_os/test_entitlement_effective_access.py
    python tests/business_os/test_entitlement_effective_access.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ent_eff_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.business_os.entitlements import facade  # noqa: E402

UID = 300


def setup_module(module=None):
    svc.ensure_schema()
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


def _reset_users():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM users")
        conn.commit()
    finally:
        conn.close()


# Faithful reference implementation of bot.py::_effective_premium_access. Kept in
# lock-step with the shipped helper: ownership is never mutated; the hold is applied
# only under canonical mode; off/shadow return ownership unchanged; failure -> ownership.
def _effective_reference(user, owns_premium):
    owns = bool(owns_premium)
    raw = (os.getenv("BUSINESS_OS_ENTITLEMENTS", "") or "").strip().lower()
    if raw not in ("1", "true", "on", "yes", "canonical"):
        return owns, None
    if not owns:
        return False, None
    hold = facade.account_hold(int(user.get("user_id") or 0), context={
        "account_status": user.get("account_status"),
        "access_enabled": user.get("access_enabled"),
    })
    if hold.get("on_hold"):
        return False, (hold.get("reason") or "account_hold")
    return True, None


# 1 -- account_hold authority: active is not a hold --------------------------
def test_account_hold_active_not_hold():
    h = facade.account_hold(UID, context={"account_status": "active", "access_enabled": 1})
    assert h["on_hold"] is False and h["reason"] == ""


# 2 -- account_hold authority: suspended/banned/restricted/deleted are holds --
def test_account_hold_nonactive_statuses_hold():
    for status in ("suspended", "banned", "restricted", "disabled", "deleted", "frozen"):
        h = facade.account_hold(UID, context={"account_status": status, "access_enabled": 1})
        assert h["on_hold"] is True, status
        assert h["reason"] == f"account_{status}"


# 3 -- account_hold authority: access_enabled=0 is a hold even when active ----
def test_account_hold_access_disabled_hold():
    h = facade.account_hold(UID, context={"account_status": "active", "access_enabled": 0})
    assert h["on_hold"] is True and h["reason"] == "account_access_disabled"


# 4 -- account_hold DB-read path (no context) --------------------------------
def test_account_hold_db_read_path():
    _reset_users()
    _seed_user(UID, "suspended", 1)
    assert facade.account_hold(UID)["on_hold"] is True
    _seed_user(UID, "active", 1)
    assert facade.account_hold(UID)["on_hold"] is False
    _reset_users()


# 5 -- account_hold fails safe when account state unknown ---------------------
def test_account_hold_unknown_fails_safe():
    _reset_users()  # no row for UID
    h = facade.account_hold(999999)  # user not present
    assert h["on_hold"] is False and h["reason"] == "account_status_unavailable"


# 6 -- effective access: flag OFF presents ownership unchanged ----------------
def test_effective_off_presents_ownership():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    # suspended owner under off -> still shown as usable (legacy presentation)
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "suspended", "access_enabled": 1}, True)
    assert eff is True and reason is None
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "active", "access_enabled": 1}, False)
    assert eff is False and reason is None


# 7 -- effective access: flag SHADOW also presents ownership unchanged --------
def test_effective_shadow_presents_ownership():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "shadow"
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "suspended", "access_enabled": 1}, True)
    assert eff is True and reason is None
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 8 -- effective access: flag ON, active owner keeps usable access -----------
def test_effective_canonical_active_owner_usable():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "active", "access_enabled": 1}, True)
    assert eff is True and reason is None
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 9 -- effective access: flag ON, suspended owner loses usable access --------
def test_effective_canonical_suspended_owner_denied():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "suspended", "access_enabled": 1}, True)
    assert eff is False and reason == "account_suspended"
    # disabled access likewise
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "active", "access_enabled": 0}, True)
    assert eff is False and reason == "account_access_disabled"
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 10 -- effective access: non-owner is never granted, no denial reason -------
def test_effective_non_owner_no_reason():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    # A non-owner on a perfectly active account: not usable, but that is not a hold.
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "active", "access_enabled": 1}, False)
    assert eff is False and reason is None
    # A non-owner who is ALSO suspended: still no premium, still no hold-denial reason
    # (they have nothing to be held out of).
    eff, reason = _effective_reference(
        {"user_id": UID, "account_status": "suspended", "access_enabled": 1}, False)
    assert eff is False and reason is None
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# 11 -- ownership/subscription is never mutated by the effective computation --
def test_ownership_separate_from_effective():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "canonical"
    user = {"user_id": UID, "account_status": "suspended", "access_enabled": 1}
    owns = True  # subscription still exists
    eff, reason = _effective_reference(user, owns)
    # effective access denied ...
    assert eff is False and reason == "account_suspended"
    # ... but the ownership input is unchanged, and account_hold does not report the
    # subscription as gone — only that access is currently held.
    assert owns is True
    h = facade.account_hold(UID, context={"account_status": "suspended", "access_enabled": 1})
    assert h["account_status"] == "suspended"  # descriptive, not "no subscription"
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_account_hold_active_not_hold,
        test_account_hold_nonactive_statuses_hold,
        test_account_hold_access_disabled_hold,
        test_account_hold_db_read_path,
        test_account_hold_unknown_fails_safe,
        test_effective_off_presents_ownership,
        test_effective_shadow_presents_ownership,
        test_effective_canonical_active_owner_usable,
        test_effective_canonical_suspended_owner_denied,
        test_effective_non_owner_no_reason,
        test_ownership_separate_from_effective,
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
