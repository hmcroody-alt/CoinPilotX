"""Subscription *history* must survive the canonical cutover (PREM-060..).

Context
-------

``business_os_ent_provider_subs`` only started being written when the Business OS
entitlement tables went canonical. Every member who subscribed — or trialled —
before that has real, recorded history that lives solely in the legacy
``subscriptions`` table and the premium columns on ``users``.

Reading only the canonical table reported all of those members as having *never*
subscribed. On the native Premium screen that is not a cosmetic difference: the
purchase surface switches on ``subscription != null`` to choose between
"Choose your plan" and "Start Premium again", so a lapsed member was greeted as a
brand-new one and nothing on the screen evidenced the subscription they had
actually held. That is what App Review could not verify.

The invariant these tests defend
--------------------------------

Subscription HISTORY and current ENTITLEMENT are different questions, and the
honest answer to the first is frequently "expired" while the honest answer to the
second is "inactive". Those two states are compatible — together they *are* the
lapsed member. Nothing here grants access; access is decided by
``premium.resolve`` alone.

And the line that must never be crossed: surfacing history is not the same as
inventing it. A legacy record has no verified Apple transaction behind it, so no
code path below is permitted to relabel one as an App Store subscription.
"""

import os
import sys
import tempfile

# Bind the engine to a throwaway database BEFORE ``services.db`` is imported, so
# an empty DATABASE_URL cannot fall back to the developer's real coinpilotx.db.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_premium_legacy_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import db  # noqa: E402
from services.business_os.entitlements import premium_api as papi  # noqa: E402
from services.business_os.entitlements import schema as sch  # noqa: E402


# The premium/subscription columns on ``users`` that the fallback reads. These
# are added additively rather than declared in a ``CREATE TABLE``: the
# directory's session-scoped conftest fixture defines ``users`` before any
# module-level setup runs, so a ``CREATE TABLE IF NOT EXISTS`` here is a no-op
# and every column below would be silently missing. Matching the conftest's
# additive approach is what keeps this suite order-independent.
_USER_PREMIUM_COLUMNS = (
    ("account_status", "TEXT DEFAULT 'active'"),
    ("access_enabled", "INTEGER DEFAULT 1"),
    ("premium_status", "TEXT"),
    ("subscription_status", "TEXT"),
    ("subscription_plan", "TEXT"),
    ("subscription_expires_at", "TEXT"),
    ("subscription_started_at", "TEXT"),
    ("premium_expires_at", "TEXT"),
    ("pro_expires_at", "TEXT"),
    ("trial_start_date", "TEXT"),
    ("trial_end_date", "TEXT"),
    ("lifetime_premium", "INTEGER DEFAULT 0"),
    ("premium_glow_manual_grant", "INTEGER DEFAULT 0"),
    ("premium_mark_override", "INTEGER DEFAULT 0"),
    ("is_pro", "INTEGER DEFAULT 0"),
    ("plan", "TEXT"),
    ("founder_number", "INTEGER DEFAULT 0"),
    ("founder_status", "TEXT"),
)


def setup_module():
    conn = db.connect()
    # Mirrors production's ``users`` table for the columns the fallback reads.
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    present = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for name, sql_type in _USER_PREMIUM_COLUMNS:
        if name in present:
            continue
        conn.execute(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
    # The legacy subscriptions ledger.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY, "
        "user_id INTEGER, status TEXT, plan TEXT, payment_type TEXT, "
        "provider TEXT, provider_subscription_id TEXT, current_period_end TEXT, "
        "current_period_start TEXT, pro_expires_at TEXT, trial_end_date TEXT, "
        "trial_start_date TEXT, cancel_at_period_end INTEGER DEFAULT 0, "
        "stripe_customer_id TEXT, stripe_subscription_id TEXT, plan_key TEXT, "
        "created_at TEXT, updated_at TEXT)"
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


def _mklegacy(uid, **cols):
    """A row in the legacy ``subscriptions`` ledger."""
    conn = db.connect()
    row = {
        "user_id": uid,
        "status": "expired",
        "plan": "free",
        "payment_type": "trial",
        "provider": None,
        "current_period_end": None,
        "pro_expires_at": "2026-06-14T10:15:12",
        "trial_end_date": "2026-06-14T10:15:12",
        "trial_start_date": "2026-06-07T10:15:12",
        "cancel_at_period_end": 0,
        "created_at": "2026-06-14T10:19:35",
        "updated_at": "2026-06-14T10:19:35",
    }
    row.update(cols)
    conn.execute(
        "INSERT INTO subscriptions (%s) VALUES (%s)"
        % (", ".join(row), ", ".join("?" for _ in row)),
        tuple(row.values()),
    )
    conn.commit()
    conn.close()


def _mkcanonical(uid, **cols):
    """A row in the canonical provider-subscription table (authority 1)."""
    conn = db.connect()
    row = {
        "provider": "apple_app_store",
        "provider_subscription_id": f"200000{uid}",
        "subject_type": "user",
        "subject_id": str(uid),
        "plan_key": "pulse_premium_annual",
        "status": "active",
        "current_period_end": "2027-01-01T00:00:00Z",
        "cancel_at_period_end": 0,
        "raw_json": "{}",
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


# --- authority 1: canonical still wins ---------------------------------------

def test_canonical_active_subscription_is_reported_active():
    _mkuser(9601)
    _mkcanonical(9601, status="active")
    summary = papi.subscription_summary(9601)
    assert summary is not None
    assert summary["state"] == "active"
    assert summary["provider"] == "apple_app_store"


def test_canonical_expired_subscription_is_reported_expired():
    _mkuser(9602)
    _mkcanonical(9602, status="expired",
                 current_period_end="2026-02-01T00:00:00Z")
    summary = papi.subscription_summary(9602)
    assert summary is not None
    assert summary["state"] == "expired"
    assert summary["renews_at"] is None
    assert summary["expires_at"] == "2026-02-01T00:00:00Z"


def test_a_canonical_record_overrides_legacy_history():
    """Authority order is not negotiable.

    A member who trialled years ago and now holds a verified App Store
    subscription must be described by the App Store record. Letting the older,
    weaker source win would show a paying member a lapsed card.
    """
    _mkuser(9603, subscription_status="expired",
            subscription_expires_at="2026-06-14T10:15:12")
    _mklegacy(9603, status="expired")
    _mkcanonical(9603, status="active")
    summary = papi.subscription_summary(9603)
    assert summary is not None
    assert summary["state"] == "active"
    assert summary["provider"] == "apple_app_store"
    assert summary["plan_key"] == "pulse_premium_annual"


# --- authority 2: legitimate legacy history ----------------------------------

def test_legacy_expired_subscription_is_surfaced_as_history():
    _mkuser(9610, subscription_status="expired",
            subscription_expires_at="2026-06-14T10:15:12")
    _mklegacy(9610, status="expired", payment_type="stripe", plan="pro")
    summary = papi.subscription_summary(9610)
    assert summary is not None
    assert summary["state"] == "expired"


def test_legacy_expired_trial_is_surfaced_as_history():
    """The exact shape of the App Review demo account."""
    _mkuser(9611, subscription_status="expired", subscription_plan="free",
            subscription_expires_at="2026-06-14T10:15:12",
            subscription_started_at="2026-06-07T10:15:12",
            premium_status="inactive", plan="free", is_pro=0)
    _mklegacy(9611, status="expired", payment_type="trial")
    summary = papi.subscription_summary(9611)
    assert summary is not None
    assert summary["state"] == "expired"
    assert summary["expires_at"] == "2026-06-14T10:15:12"
    # A lapsed record is never going to bill again.
    assert summary["auto_renew"] is False
    assert summary["renews_at"] is None


def test_never_subscribed_stays_never_subscribed():
    """The absence of history must not be dressed up as a lapsed subscription.

    This is the other half of the invariant. If every free account started
    reporting a subscription, "expired" would stop meaning anything and the
    purchase surface would tell brand-new users to *restart* a membership they
    never had.
    """
    _mkuser(9612)
    assert papi.subscription_summary(9612) is None


def test_a_free_plan_word_is_not_subscription_history():
    """``subscription_status`` carrying 'free'/'none'/'' is not evidence."""
    for uid, status in ((9613, "free"), (9614, "none"), (9615, "")):
        _mkuser(uid, subscription_status=status, plan="free")
        assert papi.subscription_summary(uid) is None, f"status={status!r}"


def test_legacy_history_expired_is_compatible_with_inactive_entitlement():
    """History says expired; entitlement says false. Both, simultaneously.

    This pairing is the lapsed member, and the whole point of the fallback is
    that reporting it does not grant anything. ``subscription_summary`` is not
    an entitlement check and must never be read as one.
    """
    _mkuser(9616, subscription_status="expired", premium_status="inactive",
            plan="free", is_pro=0,
            subscription_expires_at="2026-06-14T10:15:12")
    _mklegacy(9616, status="expired")
    summary = papi.subscription_summary(9616)
    assert summary is not None
    assert summary["state"] == "expired"

    from services import premium_identity_engine as pie
    conn = db.connect()
    row = conn.execute(
        "SELECT premium_status, subscription_status, lifetime_premium, "
        "premium_glow_manual_grant, premium_mark_override, premium_expires_at, "
        "is_pro, plan, subscription_plan, founder_number, founder_status "
        "FROM users WHERE user_id=?", (9616,)).fetchone()
    conn.close()
    assert pie.has_active_premium(dict(row)) is False


def test_a_stale_legacy_active_row_with_a_past_period_end_reports_expired():
    """The clock outranks the status word for legacy rows too."""
    _mkuser(9617, subscription_status="active")
    _mklegacy(9617, status="active", pro_expires_at="2026-01-01T00:00:00",
              trial_end_date=None)
    summary = papi.subscription_summary(9617)
    assert summary is not None
    assert summary["state"] == "expired"


# --- the line that must not be crossed ---------------------------------------

def test_legacy_history_is_never_labelled_as_an_apple_subscription():
    """No legacy record may claim an App Store identity.

    There is no verified Apple transaction behind a legacy row. Reporting one as
    ``apple_app_store`` — or handing back an Apple ``product_id`` — would be a
    fabricated purchase state, and would tell App Review that an App Store
    subscription lapsed when none ever existed.
    """
    _mkuser(9620, subscription_status="expired", subscription_plan="pro",
            subscription_expires_at="2026-06-14T10:15:12")
    _mklegacy(9620, status="expired", payment_type="trial", plan="pro")
    summary = papi.subscription_summary(9620)
    assert summary is not None
    assert "apple" not in summary["provider"].lower()
    assert summary["provider"] == "pulsesoc_trial"
    assert summary["product_id"] is None
    # Nor may it imply a monthly/annual App Store billing period it never had.
    assert summary["billing_period"] == ""
    for value in summary.values():
        assert "com.pulsesoc.premium" not in str(value)


def test_a_legacy_row_naming_its_own_provider_keeps_that_provider():
    _mkuser(9621, subscription_status="expired")
    _mklegacy(9621, status="expired", provider="stripe", payment_type="stripe")
    summary = papi.subscription_summary(9621)
    assert summary is not None
    assert summary["provider"] == "stripe"
    assert summary["product_id"] is None


def test_legacy_history_never_grants_an_active_entitlement():
    """Restore must not be able to convert a lapsed trial into membership.

    The restore path reconciles against StoreKit and writes canonical grants.
    Legacy history is read-only evidence and carries no grant, so there is no
    state in which surfacing it can produce an active entitlement.
    """
    _mkuser(9622, subscription_status="expired", premium_status="inactive")
    _mklegacy(9622, status="expired", payment_type="trial")

    summary = papi.subscription_summary(9622)
    assert summary is not None
    assert summary["state"] == "expired"

    # No canonical grant was created as a side effect of reading history.
    conn = db.connect()
    grants = conn.execute(
        "SELECT COUNT(*) FROM business_os_ent_grants WHERE subject_id=?",
        ("9622",)).fetchone()
    subs = conn.execute(
        "SELECT COUNT(*) FROM business_os_ent_provider_subs WHERE subject_id=?",
        ("9622",)).fetchone()
    conn.close()
    assert int(grants[0]) == 0
    assert int(subs[0]) == 0


# --- the pinned regression ----------------------------------------------------

def test_pinned_lapsed_member_resolves_to_expired_not_none():
    """THE regression this work exists to prevent.

    Given:
        canonical provider table has no row
        legacy subscription history exists
        legacy status = expired
        current Premium entitlement = false

    Expected:
        subscription exists historically  ->  experience = "expired"
    NOT:
        experience = "none"

    ``premiumExperience()`` in ``mobile-native/src/api/premiumCenter.ts`` reads
    ``payload.subscription ? "expired" : "none"``, so a null here is what put a
    lapsed member on the never-subscribed screen.
    """
    _mkuser(9630, subscription_status="expired", premium_status="inactive",
            subscription_plan="free", plan="free", is_pro=0,
            subscription_started_at="2026-06-07T10:15:12",
            subscription_expires_at="2026-06-14T10:15:12")
    _mklegacy(9630, status="expired", payment_type="trial")

    conn = db.connect()
    canonical = conn.execute(
        "SELECT COUNT(*) FROM business_os_ent_provider_subs WHERE subject_id=?",
        ("9630",)).fetchone()
    conn.close()
    assert int(canonical[0]) == 0, "precondition: canonical must be silent"

    summary = papi.subscription_summary(9630)

    assert summary is not None, (
        "a lapsed member with real history must not be reported as "
        "never-subscribed; this is the null that produced experience='none'"
    )
    assert summary["state"] == "expired"
    # And the client's own derivation, replicated: subscription non-null and
    # not premium => "expired".
    experience = "expired" if summary else "none"
    assert experience == "expired"
