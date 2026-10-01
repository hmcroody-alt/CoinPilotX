"""P0 — Owner lifetime Premium.

The bug this suite exists to prevent
------------------------------------
The owner account was Premium in the badge engine and Free at the gates.

``premium_identity_engine.has_active_premium`` — authority C, the reader behind
the badge — has always opened with ``if is_owner(row): return True``. Nothing
else did. Under the production default (``BUSINESS_OS_ENTITLEMENTS=off``) access
is decided by ``premium_entitlement_service._is_premium_user_raw``, which knows
nothing about an owner and reads the same expiry columns as everyone else. So
the owner's own subscription lapsing produced the exact split this repository
has been closing everywhere else: the diamond stayed on, the features went off.

The invariant under test is therefore not "the owner gets Premium". It is:

    the owner's Premium is a standing rule, not a term — so no clock, no
    provider event and no store refund is consulted to confirm it, and every
    decider that can gate a Premium feature reaches that conclusion on its own.

Boundaries asserted just as hard
--------------------------------
A rule that says "always yes" is only safe if its edges are nailed down, so the
matrix spends as much effort on what owner lifetime must NOT do:

* it must not outrank an account hold or a security suspension;
* it must not report itself as a subscription or a trial;
* it must confer MEMBERSHIP only — the Private Office's second lock is a
  separate question and stays shut (asserted end to end in
  ``tests/private_office/test_owner_office_membership.py``);
* and it must grant nobody at all when the allowlist is empty.

A boundary that moved
---------------------
This suite briefly asserted that owner lifetime must NOT confer PRIVATE or
PRIVATE_OFFICE, and the resolver capped the owner at PREMIUM to satisfy it. That
was wrong, and the way it was wrong is instructive: the owner opened Private
Office and was told "Membership required — renew membership". The cap had
correctly separated membership from access, then enforced the separation in the
wrong place — at the tier, which decides membership, instead of at the lock,
which decides access.

So the membership floor is now PRIVATE_OFFICE, and the boundary is asserted
where it actually lives: the Office lock never reads the tier, so the owner
reaches the door and still has to open it.

Deliberately NOT asserted here: anything about audio, calls, live or RTC. This
is an entitlement suite and touches no media path.

    python -m pytest tests/business_os/test_premium_owner_lifetime.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# Bind the engine to a throwaway database BEFORE ``services.db`` is imported
# (first import wins for the whole session; see tests/business_os/conftest.py).
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ownerlt_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import db  # noqa: E402
from services import premium_entitlement_service as pes  # noqa: E402
from services import premium_identity_engine as pie  # noqa: E402
from services.business_os.entitlements import facade  # noqa: E402
from services.business_os.entitlements import owner as own  # noqa: E402
from services.business_os.entitlements import premium as prem  # noqa: E402
from services.business_os.entitlements import premium_api  # noqa: E402
from services.business_os.entitlements import schema as sch  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.private_office import tiers  # noqa: E402

OWNER = 9001
MEMBER = 9002
OWNER_HELD = 9003
OWNER_DISABLED = 9004
OWNER_PRIVATE = 9005

_USERS_COLUMNS = (
    ("account_status", "TEXT DEFAULT 'active'"),
    ("access_enabled", "INTEGER DEFAULT 1"),
    ("display_name", "TEXT"),
    ("email", "TEXT"),
    ("premium_status", "TEXT"),
    ("subscription_status", "TEXT"),
    ("lifetime_premium", "INTEGER DEFAULT 0"),
    ("premium_glow_manual_grant", "INTEGER DEFAULT 0"),
    ("premium_mark_override", "INTEGER DEFAULT 0"),
    ("premium_expires_at", "TEXT"),
    ("subscription_expires_at", "TEXT"),
    ("pro_expires_at", "TEXT"),
    ("trial_end_date", "TEXT"),
    ("is_pro", "INTEGER DEFAULT 0"),
    ("plan", "TEXT"),
    ("subscription_plan", "TEXT"),
    ("founder_number", "INTEGER DEFAULT 0"),
    ("founder_status", "TEXT"),
)


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


PAST = _iso(timedelta(days=-30))
FUTURE = _iso(timedelta(days=30))


def setup_module(module=None):
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    conn = db.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    for name, sql_type in _USERS_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
        except Exception:  # noqa: BLE001 — column already present
            pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS premium_entitlements ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
        "entitlement_key TEXT, status TEXT DEFAULT 'active', source TEXT, "
        "starts_at TEXT, ends_at TEXT, metadata_json TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    conn.commit()
    conn.close()
    sch.ensure_ready()
    pes.ensure_founder_schema()

    # Every account starts LAPSED on purpose. If a test passes it must be
    # because owner lifetime decided, never because a live subscription was
    # sitting underneath doing the work.
    for uid in (OWNER, MEMBER, OWNER_HELD, OWNER_DISABLED, OWNER_PRIVATE):
        _mkuser(uid, premium_status="expired", subscription_status="expired",
                premium_expires_at=PAST, subscription_expires_at=PAST)
    _setcols(OWNER_HELD, account_status="suspended")
    _setcols(OWNER_DISABLED, access_enabled=0)


def _mkuser(uid, **cols):
    conn = db.connect()
    conn.execute("INSERT OR REPLACE INTO users (user_id) VALUES (?)", (uid,))
    for key, value in cols.items():
        conn.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, uid))
    conn.commit()
    conn.close()


def _setcols(uid, **cols):
    conn = db.connect()
    for key, value in cols.items():
        conn.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, uid))
    conn.commit()
    conn.close()


def _own(*uids):
    """Install the owner allowlist. Read per call in production, so a plain env
    assignment is the whole mechanism — no cache to invalidate."""
    os.environ["PULSESOC_OWNER_USER_IDS"] = ",".join(str(u) for u in uids)


def _disown():
    os.environ.pop("PULSESOC_OWNER_USER_IDS", None)


def teardown_function(function=None):
    _disown()
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"


# --- 1-3: identity comes from the allowlist, and from nothing else ----------
def test_owner_identity_is_the_allowlisted_user_id():
    _own(OWNER)
    assert own.is_owner_account(OWNER) is True
    assert own.is_owner_account(MEMBER) is False


def test_an_empty_allowlist_makes_nobody_the_owner():
    """The default. An unset variable must not mean "everyone" or "user 1"."""
    _disown()
    assert own.is_owner_account(OWNER) is False
    assert prem.resolve(OWNER)["reason"] != own.REASON_OWNER_LIFETIME


def test_owner_identity_ignores_display_name_and_email():
    """The scar tissue. ``is_owner`` used to match on display name, so any user
    could rename themselves into permanent Premium plus every owner bypass.
    Renaming a non-owner into the owner's exact name and email must change
    nothing."""
    _own(OWNER)
    _setcols(OWNER, display_name="PulseSoc", email="owner@pulsesoc.com")
    _setcols(MEMBER, display_name="PulseSoc", email="owner@pulsesoc.com")
    try:
        assert own.is_owner_account(MEMBER) is False
        assert prem.resolve(MEMBER)["is_premium"] is False
    finally:
        _setcols(MEMBER, display_name=None, email=None)


# --- 4-8: no clock, no provider, no store event ends it ---------------------
def test_owner_is_premium_with_a_long_expired_period():
    _own(OWNER)
    state = prem.resolve(OWNER)
    assert state["is_premium"] is True
    assert state["reason"] == own.REASON_OWNER_LIFETIME
    assert state["membership_mode"] == own.MODE_OWNER_LIFETIME
    assert state["source"] == own.SOURCE_OWNER_LIFETIME


def test_owner_survives_every_terminal_status_word():
    """`expired`, `canceled`, `past_due`, `unpaid`, `inactive`, `revoked` — the
    words a provider webhook writes when a subscription ends. None of them
    describe a standing rule, so none of them may end one."""
    _own(OWNER)
    for word in ("expired", "canceled", "cancelled", "past_due", "unpaid",
                 "inactive", "revoked"):
        _setcols(OWNER, premium_status=word, subscription_status=word)
        assert prem.resolve(OWNER)["is_premium"] is True, word
    _setcols(OWNER, premium_status="expired", subscription_status="expired")


def test_owner_with_an_ended_trial_is_not_reported_as_a_trial():
    """A closed trial window is the one state most likely to be mislabelled,
    because the honest answer for everyone else is "your trial ended"."""
    _own(OWNER)
    _setcols(OWNER, premium_status="trial", trial_end_date=PAST)
    try:
        state = prem.resolve(OWNER)
        assert state["is_premium"] is True
        assert state["reason"] == own.REASON_OWNER_LIFETIME
        assert state["reason"] != prem.REASON_ACTIVE_TRIAL
    finally:
        _setcols(OWNER, premium_status="expired", trial_end_date=None)


def test_owner_survives_an_explicitly_revoked_canonical_grant():
    """Revocation is the strongest negative signal in the canonical store: it is
    an operator or a provider saying "no", and everywhere else it beats a stale
    legacy column. It still does not reach a standing rule."""
    _own(OWNER)
    svc.grant_entitlement(OWNER, prem.PREMIUM_ACCESS, source="stripe")
    svc.revoke_entitlement(OWNER, prem.PREMIUM_ACCESS, reason="test")
    assert prem.resolve(OWNER)["is_premium"] is True
    assert prem.resolve(OWNER)["reason"] == own.REASON_OWNER_LIFETIME


def test_owner_is_premium_in_every_flag_mode():
    """`off` is the production default, so a rule that only holds in `canonical`
    is not a guarantee — it is a promise contingent on a migration nobody has
    scheduled."""
    _own(OWNER)
    for mode in ("off", "shadow", "canonical"):
        os.environ["BUSINESS_OS_ENTITLEMENTS"] = mode
        state = prem.resolve(OWNER)
        assert state["is_premium"] is True, mode
        assert state["reason"] == own.REASON_OWNER_LIFETIME, mode


# --- 9-11: holds and suspension still win ----------------------------------
def test_a_suspended_owner_does_not_get_owner_lifetime():
    """Owner lifetime stands aside rather than denying: the request falls
    through to the unchanged path. What is asserted is the absence of the
    bypass, not the presence of a new revocation."""
    _own(OWNER_HELD)
    hold = facade.account_hold(OWNER_HELD)
    assert hold["on_hold"] is True
    assert own.applies(OWNER_HELD, hold) is False
    assert prem.resolve(OWNER_HELD)["reason"] != own.REASON_OWNER_LIFETIME


def test_an_access_disabled_owner_does_not_get_owner_lifetime():
    _own(OWNER_DISABLED)
    hold = facade.account_hold(OWNER_DISABLED)
    assert hold["on_hold"] is True
    assert own.applies(OWNER_DISABLED, hold) is False


def test_a_held_owner_is_denied_at_the_tier_ladder():
    _own(OWNER_HELD)
    resolved = tiers.resolve_tier(OWNER_HELD)
    assert resolved["effective_tier"] == tiers.TIER_FREE
    assert resolved["status"] == tiers.STATUS_ACCOUNT_HOLD


# --- 12-15: the tier boundary ----------------------------------------------
def test_owner_reaches_the_top_of_the_ladder_with_no_expiry():
    _own(OWNER)
    resolved = tiers.resolve_tier(OWNER)
    assert resolved["effective_tier"] == tiers.TIER_PRIVATE_OFFICE
    assert resolved["source"] == own.SOURCE_OWNER_LIFETIME
    assert resolved["expires_at"] is None, "a permanent tier has no countdown"
    assert resolved["resolver_state"] == tiers.RESOLVER_OK


def test_the_floor_constant_names_a_real_rung():
    """``owner.FLOOR_TIER`` duplicates the literal rather than importing it, to
    break the cycle ``tiers -> owner -> tiers``. This is the assertion that keeps
    the duplication honest: a rename of the rung that misses ``owner.py`` fails
    here rather than silently dropping the owner to FREE, which is what
    ``tiers.rank`` does with a name it does not recognise."""
    assert own.FLOOR_TIER == tiers.TIER_PRIVATE_OFFICE
    assert own.FLOOR_TIER in tiers.TIER_RANK


def test_owner_lifetime_confers_membership_at_every_rung():
    """The correction. Owner lifetime answers the MEMBERSHIP question — "has
    this member got the room" — for all three umbrella keys, so no surface can
    offer the owner a renewal. It answers nothing about opening the door."""
    _own(OWNER)
    assert own.MEMBERSHIP_KEYS == {
        "premium.access", "private.access", "private_office.access"
    }
    for key in own.MEMBERSHIP_KEYS:
        assert own.confers(key) is True, key
        decision = facade.explain(OWNER, key)
        assert decision["allowed"] is True, key
        assert decision["decision_source"] == own.SOURCE_OWNER_LIFETIME, key
        assert decision["reason"] == own.REASON_OWNER_LIFETIME, key


def test_the_scope_check_is_still_a_scope_check():
    """``confers`` is asked about every key in the product. Widening it to the
    membership rungs must not have turned it into "always true" — that would
    hand the owner keys nobody has audited, which is the failure the original
    narrow scope was guarding against."""
    _own(OWNER)
    for key in ("", "chat.send", "ads.manage", "private.facts.list",
                "private_office.document.extraction", "not.a.real.key"):
        assert own.confers(key) is False, key


def test_the_ladder_lifts_the_owner_to_every_rung():
    _own(OWNER)
    for tier in tiers.TIER_ORDER:
        assert tiers.has_tier(OWNER, tier) is True, tier


def test_the_owner_floor_never_lowers_a_real_higher_grant():
    """The floor is applied only after a real umbrella grant has had its say, so
    an owner who genuinely holds PRIVATE_OFFICE keeps it."""
    _own(OWNER_PRIVATE)
    svc.grant_entitlement(OWNER_PRIVATE, "private_office.access", source="admin")
    resolved = tiers.resolve_tier(OWNER_PRIVATE)
    assert resolved["effective_tier"] == tiers.TIER_PRIVATE_OFFICE
    assert resolved["source"] != own.SOURCE_OWNER_LIFETIME


# --- 16-18: every Premium gate agrees, and says why -------------------------
def test_every_premium_capability_gate_admits_the_owner():
    """The point of the whole change. Each of these keys is read by a different
    call site through ``facade.check``; before this they consulted a legacy
    reader that has no notion of an owner."""
    _own(OWNER)
    keys = [prem.PREMIUM_ACCESS] + list(prem.PREMIUM_CAPABILITIES) + [
        "premium.identity.effects", "premium.crypto.portfolio_intelligence",
    ]
    for key in keys:
        assert facade.check(OWNER, key) is True, key
        assert facade.explain(OWNER, key)["reason"] == own.REASON_OWNER_LIFETIME, key


def test_a_lapsed_non_owner_is_unaffected_by_any_of_this():
    """The control. Every assertion above is worthless if the change simply
    granted Premium to everybody."""
    _own(OWNER)
    assert prem.resolve(MEMBER)["is_premium"] is False
    assert facade.check(MEMBER, prem.PREMIUM_ACCESS) is False
    assert tiers.resolve_tier(MEMBER)["effective_tier"] == tiers.TIER_FREE
    assert prem.resolve(MEMBER)["reason"] != own.REASON_OWNER_LIFETIME


def test_the_status_center_reports_a_permanent_membership_honestly():
    """The screen switches on these two fields. ``mode`` selects the layout and
    ``lifetime`` is what stops a lapsed provider row behind a permanent
    membership from being read as an expiry."""
    _own(OWNER)
    status, body = premium_api.status_center(OWNER)
    assert status == 200
    membership = body["membership"]
    assert membership["is_premium"] is True
    assert membership["usable_now"] is True
    assert membership["on_hold"] is False
    assert membership["lifetime"] is True
    assert membership["mode"] == own.MODE_OWNER_LIFETIME
    assert membership["reason"] == own.REASON_OWNER_LIFETIME
    assert membership["reason"] not in (prem.REASON_ACTIVE_SUBSCRIPTION,
                                        prem.REASON_ACTIVE_TRIAL,
                                        prem.REASON_EXPIRED)


def test_the_reason_is_in_the_closed_enum_and_a_member_is_never_given_it():
    _own(OWNER)
    assert own.REASON_OWNER_LIFETIME in prem.REASONS
    _, body = premium_api.status_center(MEMBER)
    assert body["membership"]["lifetime"] is False
    assert body["membership"]["reason"] != own.REASON_OWNER_LIFETIME


def test_the_badge_engine_and_the_gates_now_agree_about_the_owner():
    """The split this suite is named after, asserted directly: authority C (the
    reader behind the diamond) and the access path must return the same answer
    for the same row."""
    _own(OWNER)
    conn = db.connect()
    row = conn.execute(
        "SELECT user_id, premium_status, subscription_status, premium_expires_at "
        "FROM users WHERE user_id=?", (OWNER,)).fetchone()
    conn.close()
    badge = pie.has_active_premium(dict(zip(
        ("user_id", "premium_status", "subscription_status", "premium_expires_at"),
        tuple(row))))
    assert badge is True
    assert prem.resolve(OWNER)["is_premium"] is badge


if __name__ == "__main__":
    setup_module()
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                teardown_function(fn)
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures.append((name, exc))
                print("FAIL", name, "->", type(exc).__name__, exc)
    print(f"\n{'FAILED' if failures else 'OK'}: {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
