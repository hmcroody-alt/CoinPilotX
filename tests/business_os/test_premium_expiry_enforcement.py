"""P0 — Premium expiry enforcement.

The bug this suite exists to prevent
------------------------------------
A member's Premium subscription lapsed. The Premium Center correctly displayed
"Expired". Every Premium benefit kept working.

Both halves of that came from the same place. Under the production default
(``BUSINESS_OS_ENTITLEMENTS=off``) the access authority is
``premium_entitlement_service._is_premium_user_raw``, and its column branch
returned True on the *word* ``active`` in ``users.premium_status`` /
``users.subscription_status`` without ever reading an expiry column — the
expiry columns were not even in its SELECT. The display authority,
``premium_identity_engine.has_active_premium``, reads the same columns and DOES
cross-check the clock. One set of columns, two readers, two answers: the member
got the honest label and kept the access.

So the invariant under test is not merely "expired means no access". It is:

    the reader that draws the badge and the reader that opens the door must
    agree, because they are looking at the same row.

What is asserted
----------------
* Boundary exactness (stage 17). ``ends_at = now + 1s`` is active; ``= now`` is
  expired; ``= now - 1s`` is expired. Tested against the shared clock helper
  with an injected ``now``, because a one-second window cannot be tested
  honestly through a database round trip — a slow query would make the suite
  flaky and a flaky suite gets deleted.
* The entitlement matrix (stage 18), through the real resolver, at the
  production default flag.
* Display/access agreement, per row of the matrix.
* Cancelled is not expired: auto-renew off ends the renewal, not the term.
* A lapsed subscription does not reopen the one-time trial.
* No implicit grace. An expiry an hour past is past.

Deliberately NOT asserted here: anything about audio, calls, live, or RTC. This
is an entitlement suite and touches no media path.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# Bind the engine to a throwaway database BEFORE ``services.db`` is imported
# (first import wins for the whole session; see tests/business_os/conftest.py).
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_premexp_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import db  # noqa: E402
from services import premium_entitlement_service as pes  # noqa: E402
from services import premium_identity_engine as pie  # noqa: E402
from services import pro_access  # noqa: E402
from services.business_os.entitlements import premium as prem  # noqa: E402
from services.business_os.entitlements import schema as sch  # noqa: E402

_USERS_COLUMNS = (
    ("account_status", "TEXT DEFAULT 'active'"),
    ("access_enabled", "INTEGER DEFAULT 1"),
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

# Mirrors what production's ``premium.identity_row`` selects, and must stay a
# superset of every column ``has_active_premium`` reads. Omitting
# ``trial_end_date`` is not a cosmetic gap: the reader's trial branch consults
# it FIRST, so its absence made an open trial read as no-trial-at-all. That
# produced a real display/access disagreement here — and the same column was
# missing from the production projection, which is the actual bug this row
# caught.
_IDENTITY_READ = (
    "premium_status, subscription_status, lifetime_premium, "
    "premium_glow_manual_grant, premium_mark_override, premium_expires_at, "
    "subscription_expires_at, pro_expires_at, trial_end_date, "
    "is_pro, plan, subscription_plan, founder_number, founder_status"
)


def setup_module():
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    os.environ.pop("PULSESOC_OWNER_USER_IDS", None)
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
    for key, value in cols.items():
        conn.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, uid))
    conn.commit()
    conn.close()


def _setcols(uid, **cols):
    """Update columns in place. Distinct from ``_mkuser`` on purpose:
    ``INSERT OR REPLACE`` deletes and reinserts the row, so using it to change
    one field silently resets every other field to its default — which would
    make a reactivation test pass for the wrong reason."""
    conn = db.connect()
    for key, value in cols.items():
        conn.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, uid))
    conn.commit()
    conn.close()


def _identity_row(uid):
    conn = db.connect()
    row = conn.execute(
        f"SELECT {_IDENTITY_READ} FROM users WHERE user_id=?", (uid,)
    ).fetchone()
    conn.close()
    return dict(row) if row is not None else {}


def _iso(delta):
    return (datetime.now() + delta).isoformat(timespec="seconds")


PAST = "2020-01-01T00:00:00"
FUTURE = "2099-01-01T00:00:00"
AN_HOUR_AGO = _iso(timedelta(hours=-1))
YESTERDAY = _iso(timedelta(days=-1))
TOMORROW = _iso(timedelta(days=1))


# --- stage 17: the clock boundary --------------------------------------------
# Injected ``now``, not wall-clock arithmetic. A one-second assertion evaluated
# after a database write is a coin flip, and a test that fails one run in fifty
# teaches the team to rerun rather than to look.
def test_period_end_boundary_is_exact():
    now = datetime(2026, 9, 3, 12, 0, 0)

    assert pie.period_ended((now + timedelta(seconds=1)).isoformat(), now) is False, \
        "one second before the period end is still inside the period"
    assert pie.period_ended(now.isoformat(), now) is True, \
        "the period end is the end; at ends_at the member is expired"
    assert pie.period_ended((now - timedelta(seconds=1)).isoformat(), now) is True

    # No timestamp is not evidence of expiry. An indefinite admin grant must not
    # be revoked by silence.
    assert pie.period_ended(None, now) is False
    assert pie.period_ended("", now) is False
    assert pie.period_ended("not a date", now) is False


def test_trial_boundary_is_exact():
    """The trial window closes on the same rule as a paid period."""
    now = datetime(2026, 9, 3, 12, 0, 0)
    assert pie.period_ended((now + timedelta(seconds=1)).isoformat(), now) is False
    assert pie.period_ended(now.isoformat(), now) is True

    # And through the legacy trial reader, which owns the users-column trial.
    assert pes._trial_window_open({"trial_end_date": TOMORROW}) is True
    assert pes._trial_window_open({"trial_end_date": YESTERDAY}) is False
    # Fails closed: a trial status with no readable end confers nothing.
    assert pes._trial_window_open({"trial_end_date": ""}) is False
    assert pes._trial_window_open({}) is False


def test_timezone_aware_and_naive_ends_compare_against_the_same_clock():
    """Server time, never device time, and never a naive/aware crash."""
    aware = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    assert pie.period_ended("2026-09-03T11:59:59+00:00", aware) is True
    assert pie.period_ended("2026-09-03T12:00:01+00:00", aware) is False
    assert pie.period_ended("2026-09-03T11:59:59Z", aware) is True


def test_no_implicit_grace_window_survives():
    """An hour past is past. This is a regression guard against the three-day
    implicit window that used to sit in front of every expiry check: it could
    not tell a late webhook from a real lapse, so it extended both."""
    stale = {"subscription_status": "active", "is_pro": 1, "plan": "premium",
             "premium_expires_at": AN_HOUR_AGO}
    assert pie.has_active_premium(stale) is False
    assert pro_access.pro_access_type(
        {"plan": "pro", "subscription_status": "active",
         "pro_expires_at": AN_HOUR_AGO}) == "none"


# --- stage 18: the entitlement matrix ----------------------------------------
# Each row: (uid, label, users-columns, expected effective premium).
# Read through prem.resolve(), i.e. the real resolver at the production default
# flag — not through a helper chosen because it gives the answer we want.
_MATRIX = (
    (8801, "never subscribed", {}, False),
    (8802, "active subscription, period end in the future",
     {"subscription_status": "active", "is_pro": 1, "plan": "premium",
      "premium_expires_at": FUTURE}, True),
    (8803, "active subscription, no period end recorded (indefinite grant)",
     {"subscription_status": "active", "is_pro": 1, "plan": "premium"}, True),
    (8804, "EXPIRED subscription, status frozen at 'active' by a missed webhook",
     {"subscription_status": "active", "is_pro": 1, "plan": "premium",
      "premium_expires_at": PAST}, False),
    (8805, "expired subscription, status honestly 'expired'",
     {"subscription_status": "expired", "plan": "free", "is_pro": 0,
      "premium_expires_at": PAST}, False),
    (8806, "cancelled, still inside the paid term",
     {"subscription_status": "cancelled", "is_pro": 1, "plan": "premium",
      "premium_expires_at": FUTURE}, True),
    (8807, "cancelled, term has ended",
     {"subscription_status": "cancelled", "is_pro": 1, "plan": "premium",
      "premium_expires_at": PAST}, False),
    (8808, "trial open",
     {"premium_status": "trial", "trial_end_date": TOMORROW}, True),
    (8809, "trial closed",
     {"premium_status": "trial", "trial_end_date": YESTERDAY}, False),
    (8810, "trial closed AND subscription expired — neither reopens the other",
     {"premium_status": "trial", "subscription_status": "expired",
      "trial_end_date": YESTERDAY, "premium_expires_at": PAST}, False),
    (8811, "lifetime premium", {"lifetime_premium": 1}, True),
    (8812, "manual admin grant", {"premium_glow_manual_grant": 1}, True),
    (8813, "past_due with a past period end",
     {"subscription_status": "past_due", "premium_expires_at": PAST}, False),
)


def test_entitlement_matrix():
    for uid, label, cols, expected in _MATRIX:
        _mkuser(uid, **cols)
        state = prem.resolve(uid)
        assert state["is_premium"] is expected, (
            f"{label}: resolver said is_premium={state['is_premium']}, "
            f"expected {expected} (reason={state.get('reason')})"
        )


def test_display_and_access_agree_on_every_matrix_row():
    """Stage 8. The Premium Center reads the identity columns; the gates read
    the resolver. If those two ever disagree the member is either told a lie or
    given something they did not pay for — and the reported bug was exactly the
    second kind, wearing the first kind as a disguise.

    Rows carrying only a founder/lifetime/manual flag are excluded: those are
    identity marks, not subscription state, and the badge deliberately treats
    them differently. Every subscription-shaped row must agree.
    """
    for uid, label, cols, expected in _MATRIX:
        if cols.get("lifetime_premium") or cols.get("premium_glow_manual_grant"):
            continue
        _mkuser(uid, **cols)
        displayed = pie.has_active_premium(_identity_row(uid))
        access = prem.resolve(uid)["is_premium"]
        assert bool(displayed) == bool(access), (
            f"{label}: badge says {displayed}, access says {access}"
        )


# --- the specific regression -------------------------------------------------
def test_the_reported_bug_expired_plan_with_live_benefits():
    """The exact reported shape: plan displays as expired, benefits still on."""
    _mkuser(8820, subscription_status="active", premium_status="active",
            is_pro=1, plan="premium", premium_expires_at=PAST)

    row = _identity_row(8820)
    assert pie.has_active_premium(row) is False, "badge should read expired"
    # Before the fix this line returned True and the two disagreed.
    assert pes._is_premium_user_raw(8820) is False, \
        "the legacy access authority must read the clock, not the status word"

    state = prem.resolve(8820)
    assert state["flag_mode"] == "off", "asserting at the production default"
    assert state["is_premium"] is False
    assert state["reason"] == prem.REASON_EXPIRED
    assert state["split_brain"] is False, \
        "the authorities must not disagree about a lapsed member"


def test_reactivation_restores_access_without_recreating_anything():
    """Stage 25/reactivation: the same row, a future period end, access back."""
    _mkuser(8821, subscription_status="active", is_pro=1, plan="premium",
            premium_expires_at=PAST)
    assert prem.resolve(8821)["is_premium"] is False

    _setcols(8821, premium_expires_at=FUTURE)
    state = prem.resolve(8821)
    assert state["is_premium"] is True
    assert state["reason"] == prem.REASON_ACTIVE_SUBSCRIPTION


def test_denial_reason_is_a_closed_enum_and_carries_no_billing_payload():
    """Stage 21. The reason is a label, never a copy of the evidence."""
    for uid, _label, cols, _expected in _MATRIX:
        _mkuser(uid, **cols)
        reason = prem.resolve(uid)["reason"]
        assert reason in prem.REASONS, f"{reason!r} is outside the enum"

    _mkuser(8830)
    assert prem.resolve(8830)["reason"] == prem.REASON_NO_ENTITLEMENT, \
        "never-subscribed is not 'expired'; the label must not invent history"


def _run_standalone():
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all premium expiry enforcement tests passed")


if __name__ == "__main__":
    _run_standalone()
