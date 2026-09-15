"""The Private Office landing endpoint, at the wire.

Same stub-the-monolith pattern as ``test_entitlement_routes.py``::

    python -m pytest tests/private_office/test_private_office_routes.py
    python tests/private_office/test_private_office_routes.py

What this file is now, and what it was
--------------------------------------
This was the Private Facts + Capital Graph HTTP suite. Both features were
withdrawn when the Office was reduced to Relationship Intelligence, Private
Meetings and Office Security, and most of this file had been written to
self-skip behind a ``_facts_are_live()`` predicate that is now permanently
False. That is the file-scale version of the vacuity failure this whole effort
is about: a suite that runs, reports green, and exercises nothing.

It was not deleted, because one thing in it had no other home —
``/api/private-office/overview`` is the Office's landing endpoint and the only
route in this pack that stands behind the second lock *and* returns product
state. It is also the exact route whose lock check was gated on the retired
``private_facts`` id, which would have silently dropped the lock. So the facts
and graph stages are gone and the overview is what remains, asserted harder
than it was.

What these tests are actually defending
---------------------------------------
* **The second lock is in front of the landing screen.** An entitled member —
  including one holding the top of the ladder — who has not proved possession
  of the passcode on this session gets ``locked: true`` and no Office data.
  This is the mission's named invariant: entitlement opens the room's *door*,
  never its *safe*.
* **Three refusals stay three.** A degraded resolve is its own answer, and in
  particular is not rendered as a lock (an unlock screen cannot fix an outage)
  and not as FREE (which would tell a paying member they never paid).
* **A withdrawn surface is gone rather than quietly empty.** The retired paths
  are absent from the URL map, so they 404 at the router. A 200 with an empty
  body would be a confident lie about a feature that used to hold real data.
* **Isolation is a property of the URL.** No member route takes an owner.
"""

import json
import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_member_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route pack can import it ------------------
_stub = types.ModuleType("bot")
_stub._test_user = None


def _api_account_user():
    return _stub._test_user


def _require_admin_api(permission):
    return (None, ("DENIED", 403))


_stub.api_account_user = _api_account_user
_stub.require_admin_api = _require_admin_api
sys.modules["bot"] = _stub

from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import office  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

#: Entitled, passcode set, grant held — the ordinary unlocked member.
USER_A = 9301
#: Entitled and unlocked too. Kept so "no owner parameter" is asserted against
#: a surface where a second real member's data genuinely exists to leak.
USER_B = 9302
#: Entitled, and deliberately never given a passcode. The overview must send
#: this member to *setup* rather than to an unlock prompt they cannot satisfy.
USER_C = 9303

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


#: Unlock grants, per member, minted once in ``setup_environment``.
#:
#: The Office carries a second lock in front of its data: a passcode, and a
#: bounded grant proving it was entered on this session. Most stages here are
#: about what the overview says once that lock is open, so the grant is minted
#: once and injected by the client below. The lock itself is asserted directly
#: in ``stage_the_second_lock_stands_in_front_of_the_room``, so "unlocked" stays
#: a thing the suite had to obtain rather than a thing it assumed.
_GRANTS: dict[int, str] = {}
PASSCODE = "849271"


class _GrantClient(FlaskClient):
    """A test client that presents the current member's unlock grant.

    ``setdefault`` rather than assignment on purpose: a caller that passes the
    header explicitly — including an empty one, to exercise a locked request —
    keeps what it passed.
    """

    def open(self, *args, **kwargs):
        user = _stub._test_user or {}
        token = _GRANTS.get(int(user.get("user_id") or 0), "")
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(routes.GRANT_HEADER, token)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def _app():
    app = Flask(__name__)
    app.test_client_class = _GrantClient
    routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active", "access_enabled": 1}


def _unlock(user_id):
    """Set a passcode and take a grant, both over HTTP.

    Through the real endpoints rather than by writing a grant row directly: a
    grant is bound to the session that earned it, and minting one out of band
    would produce a binding no later request could match.
    """
    app = Flask(__name__)
    routes.register(app)
    client = app.test_client()
    _as(user_id)
    client.post("/api/private-office/security/setup",
                json={"passcode": PASSCODE, "confirm_passcode": PASSCODE})
    resp = client.post("/api/private-office/security/unlock",
                       json={"passcode": PASSCODE})
    token = (resp.get_json() or {}).get("grant_token") or ""
    if token:
        _GRANTS[int(user_id)] = token
    return token


def setup_environment():
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid in (USER_A, USER_B, USER_C):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)",
                (uid, "active"),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B, USER_C):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    # USER_C is left without a passcode on purpose — see its definition.
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None


#: Paths this pack served until the Office was reduced. They are listed by hand
#: rather than derived from ``RETIRED_FEATURE_IDS`` because a feature id and a
#: URL are different facts: it is entirely possible to retire the id, leave the
#: handler registered, and serve a member data the rest of the system agrees is
#: gone. Deriving this from the matrix would assume exactly the thing under
#: test.
WITHDRAWN_PATHS = (
    "/api/private-office/facts",
    "/api/private-office/capital-graph",
    "/api/private-office/entities/1",
    "/api/private-office/entities/1/relationships",
    "/api/private-office/records/holdings",
    "/api/private-office/attention",
    "/api/private-office/operations/overview",
    "/api/private-office/briefings",
    "/api/private-office/shield/posture",
    "/api/private-office/documents",
    "/api/private-office/concierge/requests",
    "/api/private-office/conversations",
)


# ---------------------------------------------------------------------------
def stage_authentication():
    print("\n[authentication]")
    _stub._test_user = None
    client = _app().test_client()
    resp = client.get("/api/private-office/overview")
    check("GET /api/private-office/overview requires login",
          resp.status_code == 401, str(resp.status_code))
    check("the 401 carries no product state",
          "private_office" not in (resp.get_json() or {}), str(resp.get_json()))


def stage_withdrawn_paths_are_gone():
    """A retired feature must lose its URL, not just its entitlement.

    Asserted signed *out* as well as signed in, and that ordering is the point:
    a 404 from the router happens before any handler runs, so an anonymous
    caller sees it too. If one of these ever came back as a 401, that would mean
    a handler had been re-registered and was reaching the auth check — the
    surface would be alive again with only the gate holding it shut.
    """
    print("\n[withdrawn paths]")
    _stub._test_user = None
    anon = _app().test_client()
    _as(USER_A)
    member = _app().test_client()
    for path in WITHDRAWN_PATHS:
        _stub._test_user = None
        anon_status = anon.get(path).status_code
        _as(USER_A)
        member_status = member.get(path).status_code
        check(f"GET {path} is gone for an anonymous caller",
              anon_status == 404, str(anon_status))
        check(f"GET {path} is gone for an entitled, unlocked member",
              member_status == 404, str(member_status))


def stage_no_owner_parameter_exists():
    """Isolation as a property of the URL, not of a check inside a handler."""
    print("\n[shape]")
    app = _app()
    pack = [str(r.rule) for r in app.url_map.iter_rules()
            if str(r.rule).startswith("/api/private-office")]
    check("the overview is registered",
          "/api/private-office/overview" in pack, str(sorted(pack)))
    for rule in app.url_map.iter_rules():
        if str(rule.rule) == "/api/private-office/overview":
            check("the overview takes no URL argument", rule.arguments == set(),
                  str(rule.arguments))
    check("no route in the pack names a user",
          not any("user" in str(r.rule) for r in app.url_map.iter_rules()))
    check("no route in the pack names an owner",
          not any("owner" in str(r.rule) for r in app.url_map.iter_rules()))


def stage_the_second_lock_stands_in_front_of_the_room():
    """The mission's named invariant, asserted at the wire on the landing route.

    Entitlement and possession are different questions. This member holds the
    top of the ladder and a valid session; the lock must still refuse, because
    the threat it exists for is precisely a valid session in the wrong hands.

    The lock check on this route was gated on ``private_facts`` until that
    feature retired. The failure mode was quiet and in the dangerous direction:
    ``is_entitled`` answers a tier-and-implementation question and knows nothing
    about retirement, so it kept saying True — but had it said False, the gate
    would simply have stopped running and the landing screen would have lost its
    unlock prompt with nothing anywhere to show for it. Pinning the behaviour
    here means the next such repointing has to survive a test.
    """
    print("\n[second lock]")
    _as(USER_A)
    resolved = tiers.resolve_tier(USER_A)
    check("the member really is at the top of the ladder",
          resolved["effective_tier"] == tiers.TIER_PRIVATE_OFFICE,
          str(resolved.get("effective_tier")))
    check("the member really is entitled to the room itself",
          feature_matrix.is_entitled(office.OFFICE_FEATURE_ID,
                                     resolved["effective_tier"]),
          "an unentitled member would never reach the lock, so this would pass "
          "for the wrong reason")

    client = _app().test_client()
    locked = client.get("/api/private-office/overview",
                        headers={routes.GRANT_HEADER: ""})
    body = locked.get_json()
    check("an entitled member with no grant is told the room is locked",
          body.get("locked") is True, str(body))
    check("the locked answer is still a 200, not an error",
          locked.status_code == 200, str(locked.status_code))
    check("a member who has a passcode is not sent to setup",
          body.get("setup_required") is False, str(body.get("setup_required")))
    check("the locked answer carries no Office data",
          body.get("domains") == [], str(body.get("domains")))
    check("the locked answer is never cached",
          "no-store" in locked.headers.get("Cache-Control", ""))

    # The same request, with the grant this suite had to earn.
    opened = client.get("/api/private-office/overview")
    opened_body = opened.get_json()
    check("presenting the grant opens the room",
          not opened_body.get("locked"), str(opened_body.get("locked")))
    check("the unlocked answer still carries the product entry state",
          opened_body["private_office"]["feature_id"] == office.OFFICE_FEATURE_ID)

    # A member who never set a passcode must be sent to setup, not to an unlock
    # prompt. Both are 423-shaped refusals of the same data; only one of them is
    # an instruction the member can act on.
    _as(USER_C)
    fresh = _app().test_client().get("/api/private-office/overview").get_json()
    check("a member with no passcode is also locked out",
          fresh.get("locked") is True, str(fresh))
    check("and is sent to setup rather than to an unlock prompt",
          fresh.get("setup_required") is True, str(fresh.get("setup_required")))

    # Another member's grant is not a key to this member's room. Bindings are
    # per-member; if this passed, a grant would be a bearer token for the Office.
    _as(USER_B)
    foreign = _app().test_client().get(
        "/api/private-office/overview",
        headers={routes.GRANT_HEADER: _GRANTS[USER_A]},
    ).get_json()
    check("one member's grant does not unlock another's room",
          foreign.get("locked") is True, str(foreign))


def stage_degraded_resolve_is_its_own_answer():
    """'Could not look' and 'you may not' must not share a shape.

    Three wrong answers are available here and the route must give none of
    them: a 500 leaves the client nothing to distinguish; a confident FREE tells
    a paying member they never paid; and ``locked: true`` sends them to an
    unlock screen that cannot possibly help, which is the most misleading of the
    three because the member can *act* on it and still get nowhere.
    """
    print("\n[degraded resolve]")
    _as(USER_A)
    original = svc.get_entitlements
    svc.get_entitlements = lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    try:
        resp = _app().test_client().get("/api/private-office/overview")
    finally:
        svc.get_entitlements = original

    body = resp.get_json()
    check("a degraded resolve is still a 200", resp.status_code == 200,
          str(resp.status_code))
    check("the overview reports ok=False during a degraded resolve",
          body.get("ok") is False, str(body.get("ok")))
    check("the entry state reads UNKNOWN",
          body["private_office"]["state"] == office.ENTRY_UNKNOWN,
          str(body["private_office"]["state"]))
    check("it does not claim the member is on FREE",
          body["private_office"]["effective_tier"] in ("", None),
          str(body["private_office"]["effective_tier"]))
    check("it offers no upgrade for a tier it failed to read",
          body["private_office"]["upgrade_tier"] is None,
          str(body["private_office"]["upgrade_tier"]))
    check("it names no children it could not rank",
          body["private_office"]["available"] == []
          and body["private_office"]["unavailable"] == [],
          str(body["private_office"]))
    check("an outage is not reported as a lock", "locked" not in body, str(body))
    check("the degraded answer is never cached",
          "no-store" in resp.headers.get("Cache-Control", ""))


def stage_overview_is_honest_about_the_room():
    print("\n[overview]")
    _as(USER_A)
    resp = _app().test_client().get("/api/private-office/overview")
    body = resp.get_json()
    check("the overview answers 200", resp.status_code == 200, str(resp.status_code))
    check("it reports a trustworthy answer", body.get("ok") is True, str(body.get("ok")))
    check("it carries the product entry state",
          body["private_office"]["feature_id"] == office.OFFICE_FEATURE_ID)

    product = body["private_office"]
    check("a member at the top of the ladder finds the room open",
          product["state"] == office.ENTRY_AVAILABLE, product["state"])
    check("an open room offers nothing further to buy",
          product["upgrade_tier"] is None, str(product["upgrade_tier"]))

    named = {child["feature_id"] for child in product["available"]}
    named |= {child["feature_id"] for child in product["unavailable"]}
    check("the overview names exactly the Office's declared children",
          named == set(office.OFFICE_CHILD_IDS), str(sorted(named)))
    check("no retired feature is advertised on the landing screen",
          not (named & feature_matrix.RETIRED_FEATURE_IDS),
          str(sorted(named & feature_matrix.RETIRED_FEATURE_IDS)))
    check("at least one child actually opens — otherwise the state above is "
          "passing for the wrong reason",
          bool(product["available"]), str(product))

    check("every unavailable child carries a reason",
          all(child.get("reason") for child in product["unavailable"]))
    check("no unavailable child is marked as opening",
          all(child["opens"] is False for child in product["unavailable"]))
    check("the overview is never cached",
          "no-store" in resp.headers.get("Cache-Control", ""))

    # The key survives the feature that filled it. A shipped client reads
    # ``domains`` positionally, so dropping it would crash an app that is still
    # in the wild; an empty list is a true statement about a retired surface.
    check("the domains key is still present", "domains" in body, str(sorted(body)))
    check("and is empty now that there is nothing left to count",
          body["domains"] == [], str(body["domains"]))

    blob = json.dumps(body, default=str)
    for leaked in ("owner_user_id", "fact_key", "subject_id", "provenance_ref",
                   "grant_token", "passcode"):
        check(f"{leaked} never appears in the overview", leaked not in blob)


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_authentication()
    stage_withdrawn_paths_are_gone()
    stage_no_owner_parameter_exists()
    stage_the_second_lock_stands_in_front_of_the_room()
    stage_degraded_resolve_is_its_own_answer()
    stage_overview_is_honest_about_the_room()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_office_member_routes():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
