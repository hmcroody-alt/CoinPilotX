"""Paid Premium and admin-granted Premium must unlock the same things.

That sentence is the whole commercial promise of a grant, and nothing in the
code says it out loud. It is an *emergent* property of two readers that do not
know about each other:

* a paying member arrives through ``business_os_ent_grants`` — a real row, with
  provenance, written by the purchase path;
* an admin-granted member usually arrives with **no canonical row at all**, and
  is recognised only because ``users.premium_glow_manual_grant`` (or
  ``lifetime_premium``) is set. The resolver reaches them through the premium
  bridge, which fires exactly when canonical is *silent*.

Two different columns, in two different tables, read by two different modules,
and the product's promise is that they land in the same place. Nothing enforces
that today except the fact that both happen to hand back the string
``"PREMIUM"``. If one path ever resolved a tier the other did not — or resolved
the same tier but a different feature map — the symptom would be a member who
was *given* Premium, can see the badge, and still finds a feature locked. That
is the complaint this mission was opened for, so it is worth an assertion rather
than an inference.

Three things are pinned, and the third is what keeps the first two honest:

1. every route into Premium resolves to the same *tier*;
2. every route into Premium resolves to a byte-identical *feature map* — same
   feature ids, same verdicts, same reasons;
3. that map is not vacuous: Premium actually unlocks something Free does not.

Without (3), (1) and (2) would pass just as happily on a matrix that had
stopped unlocking anything at all, which is the most expensive way for this
suite to be green.

Expiry is pinned alongside, in both dialects, because "granted" and "never
runs out" are not the same statement and the bridge is the easiest place to
confuse them.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="premium_parity_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import premium_entitlement_service as _legacy  # noqa: E402
from services.business_os.entitlements import facade as _facade  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.private_office import feature_matrix as fm  # noqa: E402
from services.private_office import tiers  # noqa: E402

UID_PAID = 920            # canonical grant, the App Store path
UID_GRANTED_GLOW = 921    # admin grant via premium_glow_manual_grant
UID_GRANTED_LIFETIME = 922  # admin grant via lifetime_premium
UID_GRANTED_CANONICAL = 926  # admin grant written as a real grant row
UID_EXPIRED_CANONICAL = 923  # a paid grant whose period ended
UID_EXPIRED_LEGACY = 924  # premium_status says active, the clock says otherwise
UID_FREE = 925

# Every mode the flag can be in. Production is `canonical`, despite a number of
# docstrings in this tree still describing `off` as the production value — so
# parity is proved in all three rather than in the one that happens to be set.
ALL_MODES = (_facade.MODE_OFF, _facade.MODE_SHADOW, _facade.MODE_CANONICAL)

# The legacy reader looks at eight columns across the users table. Seeding a
# narrower table would make every legacy lookup raise and be caught, which reads
# as "not premium" and would let this file pass while proving nothing.
#
# Written as one literal rather than assembled from a tuple, because
# `test_private_meeting_persistence.py` parses every users fixture in this
# directory to prove none of them invents an `id` primary key — production keys
# on `user_id` and a fixture that disagrees proves things about a table that
# does not exist. That parser reads source, so a `% ", ".join(...)` here is a
# fixture it cannot check, which is the same as a fixture nobody checked.
_USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    account_status TEXT DEFAULT 'active',
    access_enabled INTEGER DEFAULT 1,
    premium_status TEXT,
    subscription_status TEXT,
    lifetime_premium INTEGER DEFAULT 0,
    premium_glow_manual_grant INTEGER DEFAULT 0,
    trial_end_date TEXT,
    pro_expires_at TEXT,
    premium_expires_at TEXT,
    subscription_expires_at TEXT
)
"""

_PAST = "2020-01-01T00:00:00"
_FUTURE = "2099-01-01T00:00:00"


def setup_module(module=None):
    svc.ensure_schema()
    # The legacy reader's own schema bootstrap, run here so the bridge is
    # exercised for real rather than through its exception handler.
    _legacy.ensure_founder_schema()
    conn = db.connect()
    try:
        conn.execute(_USERS_DDL)
        # `premium_entitlements` is read by `has_entitlement` and created by
        # neither `ensure_founder_schema` nor anything else in `services/` — it
        # lives in `bot.init_db()`. Without it the legacy read raises, the
        # bridge catches, and every granted member silently resolves FREE: the
        # suite would then be asserting on the error path while reporting on the
        # product. Created here for that reason, with only the columns the read
        # names.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS premium_entitlements ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
            "entitlement_key TEXT, status TEXT, starts_at TEXT, ends_at TEXT, "
            "metadata_json TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_user(uid, **columns):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
        keys = ["user_id"] + list(columns)
        values = [uid] + [columns[k] for k in columns]
        conn.execute(
            "INSERT INTO users (%s) VALUES (%s)"
            % (", ".join(keys), ", ".join("?" for _ in keys)),
            tuple(values),
        )
        conn.commit()
    finally:
        conn.close()


def _reset_grants():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_ent_grants")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_slate():
    _reset_grants()
    # Every population, rebuilt per test. Shared seed state across tests in a
    # file like this is how "granted" quietly becomes "granted and also still
    # holding last test's canonical row", which would collapse the two paths
    # being compared into one.
    _seed_user(UID_PAID)
    _seed_user(UID_GRANTED_GLOW, premium_glow_manual_grant=1)
    _seed_user(UID_GRANTED_LIFETIME, lifetime_premium=1)
    _seed_user(UID_EXPIRED_CANONICAL)
    _seed_user(UID_EXPIRED_LEGACY, premium_status="active", premium_expires_at=_PAST)
    _seed_user(UID_FREE)
    _seed_user(UID_GRANTED_CANONICAL)
    # "apple_app_store", not "app_store" — the service validates the source
    # against a fixed vocabulary and raises on anything else, so the literal
    # here is a contract with `_VALID_SOURCES` and not decoration.
    svc.grant_entitlement(UID_PAID, "premium.access", source="apple_app_store",
                          source_reference="txn_parity")
    # An admin grant has two legal spellings — a legacy column, and a real grant
    # row with source="admin". Both are in the parametrised set below, because
    # which one an admin action produces depends on which surface issued it, and
    # a member cannot tell from the outside which kind they were given.
    svc.grant_entitlement(UID_GRANTED_CANONICAL, "premium.access", source="admin",
                          source_reference="parity_admin")
    yield


@pytest.fixture(autouse=True)
def _restore_mode():
    saved = os.environ.get("BUSINESS_OS_ENTITLEMENTS")
    yield
    if saved is None:
        os.environ.pop("BUSINESS_OS_ENTITLEMENTS", None)
    else:
        os.environ["BUSINESS_OS_ENTITLEMENTS"] = saved


def _resolve(uid, mode):
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = mode
    return tiers.resolve_tier(uid, include_features=True)


# --- the promise ------------------------------------------------------------
@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("granted_uid", [UID_GRANTED_GLOW, UID_GRANTED_LIFETIME, UID_GRANTED_CANONICAL])
def test_a_granted_member_reaches_the_same_tier_as_a_paying_one(mode, granted_uid):
    paid = _resolve(UID_PAID, mode)
    granted = _resolve(granted_uid, mode)
    assert paid["effective_tier"] == tiers.TIER_PREMIUM
    assert granted["effective_tier"] == tiers.TIER_PREMIUM, (
        "an admin-granted member resolved %r while a paying member resolved %r "
        "under mode %r" % (granted["effective_tier"], paid["effective_tier"], mode)
    )


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("granted_uid", [UID_GRANTED_GLOW, UID_GRANTED_LIFETIME, UID_GRANTED_CANONICAL])
def test_a_granted_member_gets_the_same_features_as_a_paying_one(mode, granted_uid):
    """Same tier is not the same thing as same access.

    The tier is one string; the feature map is the answer every gate in the app
    actually reads. A regression that unlocked the tier and withheld a feature
    would show a Premium badge above a locked screen, which is precisely the
    report that opened this mission — so the comparison is over the whole map,
    not over the tier that produces it.
    """
    paid = _resolve(UID_PAID, mode)["features"]
    granted = _resolve(granted_uid, mode)["features"]
    assert set(paid) == set(granted)
    for feature_id in sorted(paid):
        assert granted[feature_id] == paid[feature_id], (
            "feature %r differs between paid and granted Premium under mode %r: "
            "paid=%r granted=%r" % (feature_id, mode, paid[feature_id], granted[feature_id])
        )


@pytest.mark.parametrize("mode", ALL_MODES)
def test_premium_actually_unlocks_something_free_does_not(mode):
    """The guard that stops the two tests above from passing vacuously.

    If the matrix ever stopped granting Premium anything, paid and granted would
    still match each other perfectly — both empty-handed — and this file would
    report parity on a product with no Premium in it.
    """
    premium = _resolve(UID_PAID, mode)["features"]
    free = _resolve(UID_FREE, mode)["features"]
    unlocked = [fid for fid in premium if premium[fid] != free[fid]]
    assert unlocked, "PREMIUM unlocked nothing that FREE does not already have"
    # Named, so that a feature silently leaving the Premium tier is a diff here
    # rather than a smaller number nobody reads.
    assert sorted(unlocked) == ["advanced_undx", "market_pulse"], sorted(unlocked)


# --- and the promise has an end date ----------------------------------------
@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("uid", [UID_EXPIRED_CANONICAL, UID_EXPIRED_LEGACY])
def test_expired_premium_relocks_in_both_dialects(mode, uid):
    """Expiry has to hold on both sides of the bridge.

    ``UID_EXPIRED_CANONICAL`` runs out of a grant row; ``UID_EXPIRED_LEGACY``
    still *says* ``premium_status='active'`` and is only expired because the
    clock column disagrees. The second is the one worth pinning: it is a row
    that reads as a paying member to anything that trusts the status word.
    """
    if uid == UID_EXPIRED_CANONICAL:
        svc.grant_entitlement(uid, "premium.access", source="apple_app_store",
                              expires_at=_PAST)
    out = _resolve(uid, mode)
    assert out["effective_tier"] == tiers.TIER_FREE
    assert not fm.is_entitled("advanced_undx", out["effective_tier"])


@pytest.mark.parametrize("mode", ALL_MODES)
def test_a_free_member_is_free_everywhere(mode):
    out = _resolve(UID_FREE, mode)
    assert out["effective_tier"] == tiers.TIER_FREE
    for feature_id in fm.FEATURES:
        assert not fm.is_entitled(feature_id, out["effective_tier"]), feature_id


# --- the separately-entitled features ---------------------------------------
def test_private_meetings_is_not_a_premium_feature_and_says_so():
    """Recorded rather than fixed, because it is a decision and not a defect.

    The mission brief names Private Meetings as a Premium feature that Premium
    members cannot reach. The first half is not true: the feature is declared at
    ``TIER_PRIVATE``, one rung above Premium, and the premium bridge tops out at
    ``TIER_PREMIUM`` by construction — so no amount of Premium, paid or granted,
    has ever reached it. It is the brief's own clause 3 exception ("unless a
    feature has an intentional separate entitlement"), and lowering its tier to
    satisfy the report would hand every Premium member a Private Office feature.

    Pinned here so that the day someone *does* decide to move it, they have to
    come through this test and say so.
    """
    spec = fm.get("private_meetings")
    assert spec.minimum_tier == tiers.TIER_PRIVATE
    assert not fm.is_entitled("private_meetings", tiers.TIER_PREMIUM)
    assert tiers.TIER_RANK[tiers.TIER_PRIVATE] > tiers.TIER_RANK[tiers.TIER_PREMIUM]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
