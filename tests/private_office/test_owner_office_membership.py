"""P0 — the owner reaches the Private Office door, and still has to open it.

The defect
----------
The canonical owner tapped Private Office and was shown:

    Membership required
    Private Office is part of premium membership.
    [ Renew membership ]

The owner cannot renew a membership that never ends, so this was not a prompt —
it was a dead end, on the account that by definition holds everything.

The cause was one rung. ``owner.applies`` fired correctly, in the right place,
after the account hold and after real grants; it simply resolved the owner to
PREMIUM while every Private Office capability requires PRIVATE or above. A floor
one rung below the thing being gated denies with total confidence, which is why
nothing anywhere logged an error.

What this suite pins
--------------------
Two claims that pull in opposite directions, which is exactly why they need to
be asserted together in one file:

* **Membership always passes.** No surface may ask the owner to renew,
  subscribe, restore a purchase, or start a trial. Every refusal below that is
  correct is a 423, never a 403.
* **The lock is untouched.** Membership answers "has this member got the room".
  The Office's second lock answers "did the person holding this phone just prove
  they are that member", reads no entitlement, and is the entire defence against
  a valid session in the wrong hands. Raising a tier must not move it a
  millimetre — so the owner arrives at a locked door like everybody else.

The matrix is 18 items in four groups: membership passes (1-6), the lock still
refuses (7-13), and the controls that prove the change is scoped to one account
and builds nothing (14-18). The controls matter as much as the rest: every
assertion here would also pass if the change had simply granted the Private
Office to the entire platform.

Deliberately absent: anything about audio, calls, live or RTC. This suite
touches no media path.

    python -m pytest tests/private_office/test_owner_office_membership.py

Run it in its OWN process. Like its neighbours it binds a throwaway database at
import time, and first import wins for the whole session.
"""

import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="po_owner_membership_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route pack imports it ---------------------
_stub = types.ModuleType("bot")
_stub._test_user = None
_stub.api_account_user = lambda: _stub._test_user
_stub.require_admin_api = lambda permission: (None, ("DENIED", 403))
sys.modules["bot"] = _stub

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

from services import db  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services.business_os.entitlements import facade  # noqa: E402
from services.business_os.entitlements import owner as own  # noqa: E402
from services.business_os.entitlements import premium as prem  # noqa: E402
from services.business_os.entitlements import premium_api  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.private_office import access as po_access  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402
from services.private_office import security as po_security  # noqa: E402
from services.private_office import tiers  # noqa: E402

OWNER = 9401          # the canonical owner; subscription long dead
LAPSED = 9402         # an ordinary member whose membership ended — the control
OWNER_HELD = 9403     # the owner, account under review
OWNER_GRANTED = 9404  # an owner who ALSO holds a real grant row

PASSCODE = "618402"


def _users_table():
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid, status in (
            (OWNER, "active"), (LAPSED, "active"),
            (OWNER_HELD, "suspended"), (OWNER_GRANTED, "active"),
        ):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)", (uid, status)
            )
        cur = conn.cursor()
        po_schema.ensure_private_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()


def setup_module(module=None):
    os.environ["BUSINESS_OS_ENTITLEMENTS"] = "off"
    svc.ensure_schema()
    _users_table()
    # Nobody is granted anything by default. Every pass below must be owner
    # lifetime doing the work, never a grant row sitting underneath.
    svc.grant_entitlement(OWNER_GRANTED, "private_office.access", source="admin")


def teardown_function(function=None):
    os.environ.pop("PULSESOC_OWNER_USER_IDS", None)
    _stub._test_user = None


def _own(*uids):
    """Install the owner allowlist. Read per call in production, so assigning
    the env var is the entire mechanism — there is no cache to invalidate."""
    os.environ["PULSESOC_OWNER_USER_IDS"] = ",".join(str(u) for u in uids)


def _as(user_id, status="active"):
    _stub._test_user = {
        "user_id": user_id, "account_status": status, "access_enabled": 1,
    }


@pytest.fixture()
def client():
    app = Flask(__name__)
    routes.register(app)
    return app.test_client()


def _unlock(client, user_id):
    """Set a passcode and take a real grant, both over HTTP.

    Never by writing a grant row directly: a grant is bound to the session that
    earned it, so one minted out of band would carry a binding no later request
    could match, and the test would be exercising a lock that cannot open.
    """
    _as(user_id)
    client.post("/api/private-office/security/setup",
                json={"passcode": PASSCODE, "confirm_passcode": PASSCODE})
    resp = client.post("/api/private-office/security/unlock",
                       json={"passcode": PASSCODE})
    return (resp.get_json() or {}).get("grant_token") or ""


# --- 1-6: membership passes, everywhere, permanently ------------------------
def test_01_owner_resolves_to_the_top_rung_with_a_dead_subscription():
    """The fix itself. Nothing about this account's billing state changed; the
    only difference from the failing build is which rung the floor names."""
    _own(OWNER)
    resolved = tiers.resolve_tier(OWNER)
    assert resolved["effective_tier"] == tiers.TIER_PRIVATE_OFFICE
    assert resolved["resolver_state"] == tiers.RESOLVER_OK


def test_02_the_owners_membership_has_no_end_and_names_its_authority():
    """``expires_at`` is rendered directly by the client, so a date here would
    put a countdown on a membership that does not run out. ``source`` must say
    ``owner_lifetime`` and not a grant provenance — owner lifetime writes no
    row, and a standing rule must never be recorded as a purchase."""
    _own(OWNER)
    resolved = tiers.resolve_tier(OWNER)
    assert resolved["expires_at"] is None
    assert resolved["source"] == own.SOURCE_OWNER_LIFETIME
    assert resolved["status"] == tiers.STATUS_ACTIVE


def test_03_the_canonical_entitlement_endpoint_reports_the_tier(client):
    """Stage 4: ``effective_tier`` is exposed on the surface that already owns
    it, rather than duplicated into the Premium payload. The client's
    ``parseTierAnswer`` reads exactly these fields and refuses the answer unless
    ``ok`` and ``resolver_state`` both agree it is trustworthy."""
    _own(OWNER)
    _as(OWNER)
    resp = client.get("/api/private-office/entitlement")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["resolver_state"] == "ok"
    assert body["effective_tier"] == tiers.TIER_PRIVATE_OFFICE
    assert body["expires_at"] is None


def test_04_the_owner_satisfies_every_rung_of_the_ladder():
    _own(OWNER)
    for tier in tiers.TIER_ORDER:
        assert tiers.has_tier(OWNER, tier) is True, tier


def test_05_every_membership_key_is_granted_by_the_one_authority():
    """One server-side authority, asked by each decider. No surface re-derives
    who the owner is, so there is no second place for this to go stale."""
    _own(OWNER)
    for key in ("premium.access", "private.access", "private_office.access"):
        decision = facade.explain(OWNER, key)
        assert decision["allowed"] is True, key
        assert decision["reason"] == own.REASON_OWNER_LIFETIME, key


def test_06_the_premium_centre_still_tells_the_truth_about_why():
    """Raising the tier must not have blurred the REASON. The Premium Center
    switches its whole layout on these two fields, and reporting the owner as
    ACTIVE_SUBSCRIPTION would reintroduce a renewal date."""
    _own(OWNER)
    status, body = premium_api.status_center(OWNER)
    assert status == 200
    membership = body["membership"]
    assert membership["reason"] == own.REASON_OWNER_LIFETIME
    assert membership["lifetime"] is True
    assert membership["is_premium"] is True
    assert membership["mode"] == own.MODE_OWNER_LIFETIME


# --- 7-13: and the second lock is exactly as shut as it was -----------------
def test_07_an_owner_without_a_passcode_is_sent_to_setup_not_to_checkout(client):
    """The screenshot, inverted. The security status route is the first call
    PrivateOfficeScreen makes, and its 403 is what the client turns into
    UPGRADE_REQUIRED. A 200 with ``setup_required`` is the whole difference
    between "create your passcode" and "renew your membership"."""
    _own(OWNER)
    _as(OWNER)
    resp = client.get("/api/private-office/security/status")
    assert resp.status_code == 200, "membership must never gate the lock screen"
    body = resp.get_json()
    assert body["setup_required"] is True
    assert body["passcode_set"] is False
    assert body["unlocked"] is False


def test_08_an_owner_with_no_passcode_is_locked_out_of_the_data(client):
    """Stage 9, the exact status code the mission names. Membership PASSED —
    that is why this is 423 and not 403. The Office is shut because nobody has
    proved who is holding the phone, which is a different sentence entirely."""
    _own(OWNER)
    _as(OWNER)
    resp = client.get("/api/private-office/facts")
    assert resp.status_code == 423
    body = resp.get_json()
    assert body["code"] == po_security.ERR_LOCKED
    assert body["setup_required"] is True


def test_09_an_owner_with_a_passcode_but_no_grant_stays_out(client):
    """Having set a passcode is not having entered one."""
    _own(OWNER)
    _unlock(client, OWNER)
    _as(OWNER)
    resp = client.get("/api/private-office/facts", headers={routes.GRANT_HEADER: ""})
    assert resp.status_code == 423
    assert resp.get_json()["setup_required"] is False


def test_10_a_forged_grant_does_not_open_the_office(client):
    _own(OWNER)
    _unlock(client, OWNER)
    _as(OWNER)
    resp = client.get("/api/private-office/facts",
                      headers={routes.GRANT_HEADER: "not-a-real-grant"})
    assert resp.status_code == 423


def test_11_the_owner_gets_in_by_unlocking_like_anybody_else(client):
    """The positive control for the whole security half: the door is shut, and
    it is a door — the owner opens it with the passcode, not with their tier."""
    _own(OWNER)
    token = _unlock(client, OWNER)
    assert token, "unlock must mint a grant"
    _as(OWNER)
    resp = client.get("/api/private-office/facts",
                      headers={routes.GRANT_HEADER: token})
    assert resp.status_code == 200


def test_12_a_locked_refusal_carries_no_office_data(client):
    """A refusal that leaks is not a refusal. The 423 body is the unlock screen's
    entire input: a code and a boolean."""
    _own(OWNER)
    _as(OWNER)
    body = client.get("/api/private-office/facts").get_json()
    assert set(body) <= {"ok", "state", "code", "setup_required", "message"}
    assert "facts" not in body and "items" not in body


def test_13_undx_reaches_the_same_locked_door():
    """Stage 10. The agent resolves the same tier and asks the same lock, so it
    cannot read out what the screen would show locked. Outside a request context
    there are no bindings at all, and ``request_is_unlocked`` fails closed —
    which is the property that matters, because a background caller is precisely
    the one with no proof of who is holding the phone."""
    _own(OWNER)
    resolved = tiers.resolve_tier(OWNER)
    assert po_access.decide(resolved, routes.FACTS_FEATURE_ID)["decision"] == \
        po_access.ALLOW, "membership must pass, or this proves nothing"

    conn = db.connect()
    try:
        cur = conn.cursor()
        po_schema.ensure_private_schema(cur)
        assert po_security.request_is_unlocked(cur, OWNER)["ok"] is False
    finally:
        conn.close()


# --- 14-18: the controls ----------------------------------------------------
def test_14_a_lapsed_member_is_still_told_to_renew(client):
    """The control that gives every test above its meaning. If this one flips,
    the change did not grant the owner the Private Office — it granted it to
    everybody, and did so through a commit about one account."""
    _own(OWNER)
    _as(LAPSED)
    resp = client.get("/api/private-office/security/status")
    assert resp.status_code == 403
    assert resp.get_json()["state"] == po_access.NOT_ENTITLED


def test_15_an_account_hold_still_beats_owner_lifetime(client):
    """Stage 12. Owner lifetime STANDS ASIDE under a hold rather than denying,
    so the account falls through to its ordinary resolution — which, for a
    suspended account with no grant, is FREE."""
    _own(OWNER_HELD)
    resolved = tiers.resolve_tier(OWNER_HELD)
    assert resolved["effective_tier"] == tiers.TIER_FREE
    assert resolved["status"] == tiers.STATUS_ACCOUNT_HOLD
    assert resolved["source"] != own.SOURCE_OWNER_LIFETIME

    _as(OWNER_HELD, status="suspended")
    assert client.get("/api/private-office/security/status").status_code == 403


def test_16_the_top_rung_does_not_build_anything(client):
    """Stage 8. ``feature_matrix`` resolves implementation BEFORE entitlement,
    so an unbuilt capability reports NOT_IMPLEMENTED to the person with the
    highest possible tier. A tier is not a construction crew, and "upgrade to
    unlock" pointed at nothing would be a lie told to take money."""
    _own(OWNER)
    resolved = tiers.resolve_tier(OWNER)
    unbuilt = [
        fid for fid, spec in feature_matrix.FEATURES.items()
        if spec.implementation != feature_matrix.IMPL_IMPLEMENTED
    ]
    assert unbuilt, "the matrix should still declare unbuilt features"
    for fid in unbuilt:
        decision = po_access.decide(resolved, fid)
        assert decision["decision"] != po_access.ALLOW, fid
        assert decision["minimum_tier"] == "", \
            f"{fid}: an unbuilt feature must offer no upgrade path"


def test_17_an_empty_allowlist_grants_this_to_nobody(client):
    """Owner identity is an allowlist of immutable user ids and nothing else.
    The helper behind it carries the scar of a real incident — it once matched
    on display name — so the unset case is asserted rather than assumed."""
    os.environ.pop("PULSESOC_OWNER_USER_IDS", None)
    assert tiers.resolve_tier(OWNER)["effective_tier"] == tiers.TIER_FREE
    assert facade.check(OWNER, prem.PREMIUM_ACCESS) is False
    _as(OWNER)
    assert client.get("/api/private-office/security/status").status_code == 403


def test_18_a_real_grant_keeps_its_own_provenance():
    """The floor is applied only after real grants have had their say, so an
    owner who genuinely holds PRIVATE_OFFICE is reported as holding it. Audit
    must be able to tell a purchase from a standing rule."""
    _own(OWNER_GRANTED)
    resolved = tiers.resolve_tier(OWNER_GRANTED)
    assert resolved["effective_tier"] == tiers.TIER_PRIVATE_OFFICE
    assert resolved["source"] != own.SOURCE_OWNER_LIFETIME
