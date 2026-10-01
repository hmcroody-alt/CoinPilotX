"""The structured-record HTTP surface — templates, CRUD, search, step-up reveal.

Same stub-the-monolith pattern as ``test_operations_routes.py``::

    python -m pytest tests/private_office/test_structured_record_routes.py
    python tests/private_office/test_structured_record_routes.py

What these tests are actually defending
---------------------------------------
* **No plaintext leaves by accident.** The store has its own tests proving a
  RESTRICTED value is masked in the projection and encrypted at rest. Those
  tests inspect columns. These inspect *response bodies* — every one of them,
  serialised whole and searched for the sample passport number — because the
  column being right does not prove the route did not put the value somewhere
  else on the way out. Create, read, list, search, history, patch, archive and
  a failed reveal each get that sweep. Exactly one response in this file is
  allowed to contain the number, and it is the successful reveal.
* **The reveal needs a second proof.** A valid unlock grant is not enough: the
  grant says the Office was opened recently, the passcode says the person
  holding the device knows it now. Missing passcode is 401 ``step_up_required``,
  wrong passcode is 401 ``step_up_failed``, and neither carries the value.
* **The step-up is not an existence oracle.** A wrong passcode against a
  stranger's record id must not answer differently from a wrong passcode
  against the member's own, or the failed-attempt counter becomes a way to
  enumerate other members' record ids for free.
* **Owner isolation over HTTP, with two real members**, on every route that
  takes an id — read, patch, archive, history and reveal. A foreign id and an
  id that was never issued return the same status *and the same body bytes*.
* **The body allowlist holds.** ``verification_state``, ``provenance_type``,
  ``owner_user_id`` and ``office_id`` in a POST body change nothing. A client
  that could name its own verification state could label its own typing
  "verified", which is the one claim the provenance model exists to keep honest.
* **Both locks in front of every route**: the tier gate and the office grant.
  Signed in and entitled but presenting no grant is 423 with no data.
* **The prefix collision stays fixed.** ``/structured-records`` and not
  ``/records``, because Operations already owns ``/records/<view>`` and
  Werkzeug matches by registration order. There is a direct test for this,
  because the failure mode is a silent 404 from the wrong handler.
"""

import json
import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_records_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route packs can import it -----------------
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
from services import private_office_routes as base_routes  # noqa: E402
from services import private_office_structured_records_routes as routes  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import field_crypto as crypto  # noqa: E402
from services.private_office import record_template_catalog as catalog  # noqa: E402
from services.private_office import record_templates as templates  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import structured_records as store  # noqa: E402

USER_A = 9501
USER_B = 9502

#: Six digits: the passcode policy is digits-only and this is what the security
#: endpoints will actually accept. A word here fails setup with
#: ``{"error": "passcode_policy", "reason": "digits_only"}`` and every later
#: stage then fails for the wrong reason.
PASSCODE = "849271"
WRONG_PASSCODE = "111999"

#: The one string this file hunts for. Distinctive on purpose — a value like
#: "12345678" could appear in a timestamp or an id and make the sweep below
#: fail for a reason that has nothing to do with a leak.
DOC_NUMBER = "X9912341234"

_KEY = crypto.generate_key()

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def check_no_plaintext(label: str, response) -> None:
    """The sweep. Serialises the whole body and looks for the passport number.

    Whole-body rather than field-by-field because the interesting leak is
    always the one nobody thought to assert on: a debug echo, an error message
    quoting the input, a duplicate-detection hint naming what it matched.
    """
    body = json.dumps(response.get_json() or {})
    check(f"no plaintext in {label}", DOC_NUMBER not in body,
          f"HTTP {response.status_code}")


#: Unlock grants, minted once through the real security endpoints and injected
#: by the client below. The lock gets its own direct 423 assertion in
#: ``stage_second_lock`` so "unlocked" stays something the suite had to obtain.
_GRANTS: dict[int, str] = {}


class _GrantClient(FlaskClient):
    """Presents the current member's unlock grant. ``setdefault`` on purpose:
    a caller passing the header explicitly — including empty, to exercise a
    locked request — keeps what it passed."""

    def open(self, *args, **kwargs):
        user = _stub._test_user or {}
        token = _GRANTS.get(int(user.get("user_id") or 0), "")
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(base_routes.GRANT_HEADER, token)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def _app():
    """Both packs on one app, in the order ``bot.py`` registers them.

    The base pack first, deliberately: that is what production does, and it is
    the ordering under which the ``/records/<view>`` collision would bite. An
    app carrying only this pack would route ``/records/1`` happily and the
    collision test would pass while production 404s.
    """
    app = Flask(__name__)
    app.test_client_class = _GrantClient
    base_routes.register(app)
    routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active",
                        "access_enabled": 1}


def _unlock(user_id):
    app = Flask(__name__)
    base_routes.register(app)
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


#: A complete, valid passport. ``issuance.document_number`` is the RESTRICTED
#: field every security assertion in this file turns on.
PASSPORT = {
    "identification.surname": "Okonkwo",
    "identification.given_names": "Amara Chidi",
    "identification.date_of_birth": "1988-04-11",
    "identification.nationality": "US",
    "issuance.document_number": DOC_NUMBER,
    "issuance.issuing_country": "US",
    "issuance.issue_date": "2020-12-01",
    "issuance.expiry_date": "2030-12-01",
}


def setup_environment():
    os.environ["PRIVATE_OFFICE_FIELD_KEYS"] = f"k1:{_KEY}"
    os.environ["PRIVATE_OFFICE_FIELD_KEY_ACTIVE"] = "k1"
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid in (USER_A, USER_B):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)",
                (uid, "active"),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        store.ensure_structured_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None


# ---------------------------------------------------------------------------
def stage_authentication():
    print("\n[authentication]")
    _stub._test_user = None
    client = _app().test_client()
    for path, method in (
        ("/api/private-office/record-templates", "get"),
        ("/api/private-office/record-domains", "get"),
        ("/api/private-office/structured-records", "get"),
        ("/api/private-office/structured-records", "post"),
        ("/api/private-office/structured-records/search", "get"),
        ("/api/private-office/structured-records/expiring", "get"),
        ("/api/private-office/structured-records/1", "get"),
        ("/api/private-office/structured-records/1", "patch"),
        ("/api/private-office/structured-records/1", "delete"),
        ("/api/private-office/structured-records/1/history", "get"),
        ("/api/private-office/structured-records/1/reveal", "post"),
    ):
        resp = getattr(client, method)(path, json={})
        check(f"anonymous {method.upper()} {path} is 401",
              resp.status_code == 401, str(resp.status_code))


def stage_second_lock():
    print("\n[the second lock]")
    _as(USER_A)
    client = _app().test_client()
    # Empty header rather than no header: the client injects the grant unless
    # the caller supplies one, so passing "" is how a locked request is spelled.
    headers = {base_routes.GRANT_HEADER: ""}
    for path in (
        "/api/private-office/record-templates",
        "/api/private-office/structured-records",
        "/api/private-office/structured-records/search",
    ):
        resp = client.get(path, headers=headers)
        check(f"locked GET {path} is 423", resp.status_code == 423,
              str(resp.status_code))
        check("a locked response carries no records",
              "records" not in (resp.get_json() or {}))
    resp = client.post("/api/private-office/structured-records",
                       headers=headers,
                       json={"template_key": "passport", "payload": PASSPORT})
    check("locked create is 423", resp.status_code == 423, str(resp.status_code))
    check_no_plaintext("a locked create", resp)


def stage_templates_and_domains():
    print("\n[templates and domains]")
    _as(USER_A)
    client = _app().test_client()

    resp = client.get("/api/private-office/record-templates")
    body = resp.get_json() or {}
    check("the manifest is 200", resp.status_code == 200, str(resp.status_code))
    check("the manifest states its contract version",
          body.get("contract_version") == templates.CONTRACT_VERSION,
          str(body.get("contract_version")))
    check("the manifest carries templates", len(body.get("templates") or []) > 0,
          str(len(body.get("templates") or [])))

    narrowed = client.get(
        "/api/private-office/record-templates?domain=identity_government")
    narrow_keys = {t["key"] for t in (narrowed.get_json() or {}).get("templates") or []}
    all_keys = {t["key"] for t in body.get("templates") or []}
    check("a domain filter narrows the manifest",
          narrow_keys and narrow_keys < all_keys,
          f"{len(narrow_keys)} of {len(all_keys)}")

    bad = client.get("/api/private-office/record-templates?domain=not_a_domain")
    check("an unknown domain is 400, not silently everything",
          bad.status_code == 400, str(bad.status_code))

    resp = client.get("/api/private-office/record-domains")
    body = resp.get_json() or {}
    domains = body.get("domains") or []
    check("every IA domain gets a heading",
          [d["key"] for d in domains] == list(catalog.IA_DOMAIN_KEYS),
          str([d["key"] for d in domains]))
    check("every heading carries a label key and a fallback",
          all(d.get("label_key") and d.get("label_fallback") for d in domains))
    # The server does not know the member's locale. A translated string here
    # would put an English heading on a French screen; the key plus fallback is
    # how the client renders in the language it actually knows.
    check("no heading ships a pre-translated label",
          all("label" not in d for d in domains))
    check("a domain with no records shows zero rather than vanishing",
          all(isinstance(d.get("record_count"), int) for d in domains))


def stage_create():
    print("\n[create]")
    _as(USER_A)
    client = _app().test_client()

    resp = client.post("/api/private-office/structured-records",
                       json={"template_key": "passport", "payload": PASSPORT,
                             "title": "US Passport"})
    body = resp.get_json() or {}
    check("a valid create is 201", resp.status_code == 201, str(resp.status_code))
    check("the response names the new record", bool(body.get("record_id")),
          str(body.get("record_id")))
    check_no_plaintext("the create response", resp)

    record = body.get("record") or {}
    check("the summary is masked, not the number",
          DOC_NUMBER not in str(record.get("summary") or ""),
          str(record.get("summary")))
    check("the summary still says something useful",
          "Okonkwo" in str(record.get("summary") or ""),
          str(record.get("summary")))

    missing = client.post("/api/private-office/structured-records",
                          json={"template_key": "passport",
                                "payload": {"identification.surname": "X"}})
    invalid_body = missing.get_json() or {}
    # 422 and not 400: the request was well-formed and the *content* failed the
    # template's rules, and the client renders the errors beside the fields
    # that produced them.
    check("an incomplete payload is 422", missing.status_code == 422,
          str(missing.status_code))
    check("the refusal names the paths that failed",
          all(e.get("path") for e in invalid_body.get("errors") or []),
          str(invalid_body.get("errors")))

    unknown = client.post("/api/private-office/structured-records",
                          json={"template_key": "no_such_template", "payload": {}})
    check("an unknown template is 400", unknown.status_code == 400,
          str(unknown.status_code))

    nothing = client.post("/api/private-office/structured-records", json={})
    check("a create with no template is 400", nothing.status_code == 400,
          str(nothing.status_code))

    return int(body.get("record_id") or 0)


def stage_body_allowlist():
    print("\n[body allowlist]")
    _as(USER_A)
    client = _app().test_client()
    payload = dict(PASSPORT)
    payload["issuance.document_number"] = "Z0001112222"
    resp = client.post("/api/private-office/structured-records",
                       json={
                           "template_key": "passport",
                           "payload": payload,
                           "title": "Second passport",
                           "allow_duplicate": True,
                           # Everything below is the client trying to write a
                           # field it does not get to write.
                           "owner_user_id": USER_B,
                           "office_id": 4242,
                           "verification_state": "VERIFIED",
                           "provenance_type": "GOVERNMENT_VERIFIED",
                           "source_type": "DOCUMENT_EXTRACTED",
                           "revision": 99,
                       })
    body = resp.get_json() or {}
    record = body.get("record") or {}
    check("the smuggling create still succeeds", resp.status_code == 201,
          str(resp.status_code))
    check("a client cannot declare its own typing verified",
          record.get("verification_state") != "VERIFIED",
          str(record.get("verification_state")))
    check("a client cannot claim a government verified the value",
          record.get("provenance_type") != "GOVERNMENT_VERIFIED",
          str(record.get("provenance_type")))
    check("a client cannot set its own revision",
          int(record.get("revision") or 0) == 1, str(record.get("revision")))
    check("the record never leaks the owner column",
          "owner_user_id" not in json.dumps(record), json.dumps(record)[:200])

    # The one that matters most: the tenant boundary is not a suggestion.
    _as(USER_B)
    stolen = _app().test_client().get(
        f"/api/private-office/structured-records/{body.get('record_id')}")
    check("the smuggled owner_user_id did not hand the record to B",
          stolen.status_code == 404, str(stolen.status_code))
    _as(USER_A)


def stage_duplicates_and_idempotency():
    print("\n[duplicates and idempotency]")
    _as(USER_A)
    client = _app().test_client()

    dupe = client.post("/api/private-office/structured-records",
                       json={"template_key": "passport", "payload": PASSPORT,
                             "title": "Again"})
    check("a repeat of the same passport is 409", dupe.status_code == 409,
          str(dupe.status_code))
    # The duplicate hint is exactly the kind of place a plaintext value leaks:
    # "you already recorded X9912341234" is the most natural sentence to write
    # and the wrong one.
    check_no_plaintext("the duplicate refusal", dupe)
    check("the refusal tells the client how to proceed",
          (dupe.get_json() or {}).get("retry_with", {}).get("allow_duplicate") is True)

    forced = client.post("/api/private-office/structured-records",
                         json={"template_key": "passport", "payload": PASSPORT,
                               "title": "Renewal", "allow_duplicate": True})
    check("the member can override the duplicate refusal",
          forced.status_code == 201, str(forced.status_code))

    body = {"template_key": "residence",
            "payload": {"address.line1": "12 Rue Lepic", "address.city": "Paris",
                        "address.country": "FR"},
            "idempotency_key": "req-1", "allow_duplicate": True}
    first = client.post("/api/private-office/structured-records", json=body)
    second = client.post("/api/private-office/structured-records", json=body)
    check("the first submission creates", first.status_code == 201,
          str(first.status_code))
    # 200 rather than 201 so a client retrying after a dropped response can
    # tell that nothing was created this time.
    check("the replay is 200, not a second create", second.status_code == 200,
          str(second.status_code))
    check("the replay returns the same record",
          (first.get_json() or {}).get("record_id")
          == (second.get_json() or {}).get("record_id"))


def stage_read_and_list(record_id):
    print("\n[read, list, search, expiry]")
    _as(USER_A)
    client = _app().test_client()

    resp = client.get(f"/api/private-office/structured-records/{record_id}")
    check("a read of one record is 200", resp.status_code == 200,
          str(resp.status_code))
    check_no_plaintext("a record read", resp)
    fields = (resp.get_json() or {}).get("record", {}).get("fields") or []
    restricted = [f for f in fields
                  if f.get("path") == "issuance.document_number"]
    check("the restricted field is present but masked",
          bool(restricted) and restricted[0].get("value") != DOC_NUMBER,
          str(restricted[0].get("value") if restricted else None))
    check("the mask still shows enough to recognise the document",
          "1234" in str(restricted[0].get("value") if restricted else ""),
          str(restricted[0].get("value") if restricted else None))

    listing = client.get("/api/private-office/structured-records")
    check("the list is 200", listing.status_code == 200, str(listing.status_code))
    check("the list states its page size",
          isinstance((listing.get_json() or {}).get("count"), int),
          str((listing.get_json() or {}).get("count")))
    check_no_plaintext("the list", listing)

    found = client.get("/api/private-office/structured-records/search?q=okonkwo")
    check("a search on an unmasked field finds the record",
          found.status_code == 200 and (found.get_json() or {}).get("results"),
          str(found.status_code))
    check_no_plaintext("a search response", found)

    # The index holds the *masked* form, so the four visible digits are
    # searchable and the number is not. This is the property, not an accident.
    tail = client.get("/api/private-office/structured-records/search?q=1234")
    check("the masked tail of an identifier is searchable",
          bool((tail.get_json() or {}).get("results")))
    whole = client.get(
        f"/api/private-office/structured-records/search?q={DOC_NUMBER}")
    check("the whole identifier is not, because it was never indexed",
          not (whole.get_json() or {}).get("results"),
          str((whole.get_json() or {}).get("results")))

    expiring = client.get(
        "/api/private-office/structured-records/expiring?before=2031-01-01")
    check("expiry lookup is 200", expiring.status_code == 200,
          str(expiring.status_code))
    check("expiry lookup finds the passport",
          int((expiring.get_json() or {}).get("count") or 0) > 0)
    check_no_plaintext("the expiry list", expiring)
    check("expiry without a date is 400, not every record",
          client.get("/api/private-office/structured-records/expiring"
                     ).status_code == 400)


def stage_reveal(record_id):
    print("\n[the step-up reveal]")
    _as(USER_A)
    client = _app().test_client()
    path = f"/api/private-office/structured-records/{record_id}/reveal"

    # The request carries a valid grant. That is deliberately not enough.
    none = client.post(path, json={"field_path": "issuance.document_number"})
    check("a valid grant alone does not reveal", none.status_code == 401,
          str(none.status_code))
    check("the refusal names what is missing",
          (none.get_json() or {}).get("state") == "step_up_required",
          str((none.get_json() or {}).get("state")))
    check_no_plaintext("a reveal with no passcode", none)

    wrong = client.post(path, json={"field_path": "issuance.document_number",
                                    "passcode": WRONG_PASSCODE})
    check("a wrong passcode is 401", wrong.status_code == 401,
          str(wrong.status_code))
    check("the refusal is a step-up failure, not a lockout",
          (wrong.get_json() or {}).get("state") == "step_up_failed",
          str((wrong.get_json() or {}).get("state")))
    check_no_plaintext("a reveal with a wrong passcode", wrong)

    no_field = client.post(path, json={"passcode": PASSCODE})
    check("a reveal naming no field is 400", no_field.status_code == 400,
          str(no_field.status_code))

    ok = client.post(path, json={"field_path": "issuance.document_number",
                                 "passcode": PASSCODE})
    body = ok.get_json() or {}
    check("the right passcode reveals", ok.status_code == 200,
          str(ok.status_code))
    # The one response in this file allowed to contain the number.
    check("the revealed value is the stored value",
          body.get("value") == DOC_NUMBER, str(body.get("value"))[:20])

    # A reveal is a read of the audit trail's most important kind, so it must
    # appear in history — and history must still not carry the value.
    history = client.get(
        f"/api/private-office/structured-records/{record_id}/history")
    kinds = [e.get("change_type") for e in (history.get_json() or {}).get("history") or []]
    check("the reveal is on the record's history", "REVEALED" in kinds, str(kinds))
    check("the record's creation is still on its history",
          "CREATED" in kinds, str(kinds))
    check_no_plaintext("the history", history)


def stage_step_up_is_not_an_oracle(record_id):
    print("\n[the step-up is not an existence oracle]")
    _as(USER_B)
    client = _app().test_client()
    # B knows the same passcode string — every member in this fixture set the
    # same one — so the only thing separating these two requests is whether
    # the record id belongs to the caller.
    foreign = client.post(
        f"/api/private-office/structured-records/{record_id}/reveal",
        json={"field_path": "issuance.document_number", "passcode": PASSCODE})
    absent = client.post(
        "/api/private-office/structured-records/98765432/reveal",
        json={"field_path": "issuance.document_number", "passcode": PASSCODE})
    check("revealing another member's record is 404",
          foreign.status_code == 404, str(foreign.status_code))
    check("an id that was never issued is the same 404",
          absent.status_code == foreign.status_code,
          f"{absent.status_code} vs {foreign.status_code}")
    check("and byte-identical, so the pair says nothing about existence",
          absent.get_data() == foreign.get_data(),
          f"{absent.get_data()[:80]!r} vs {foreign.get_data()[:80]!r}")
    check_no_plaintext("a foreign reveal", foreign)

    # And the mirror: a *wrong* passcode must not distinguish either, or the
    # failure counter becomes a free enumeration of other members' record ids.
    bad_foreign = client.post(
        f"/api/private-office/structured-records/{record_id}/reveal",
        json={"field_path": "issuance.document_number",
              "passcode": WRONG_PASSCODE})
    bad_absent = client.post(
        "/api/private-office/structured-records/98765432/reveal",
        json={"field_path": "issuance.document_number",
              "passcode": WRONG_PASSCODE})
    check("a wrong passcode answers the same for a foreign id as an absent one",
          bad_foreign.get_data() == bad_absent.get_data(),
          f"{bad_foreign.status_code} vs {bad_absent.status_code}")
    _as(USER_A)


def stage_patch_and_concurrency(record_id):
    print("\n[patch and concurrency]")
    _as(USER_A)
    client = _app().test_client()
    path = f"/api/private-office/structured-records/{record_id}"

    before = (client.get(path).get_json() or {}).get("record", {})
    revision = int(before.get("revision") or 0)

    resp = client.patch(path, json={"payload": {"issuance.expiry_date": "2031-06-30"},
                                    "expected_revision": revision})
    body = resp.get_json() or {}
    record = body.get("record") or {}
    check("a patch under the right revision is 200", resp.status_code == 200,
          str(resp.status_code))
    check("the patch moved the expiry", record.get("expires_at") == "2031-06-30",
          str(record.get("expires_at")))
    check("the revision advanced", int(record.get("revision") or 0) == revision + 1,
          str(record.get("revision")))
    check_no_plaintext("a patch response", resp)

    stale = client.patch(path, json={"title": "Stale write",
                                     "expected_revision": revision})
    check("a stale write is 409, not a silent overwrite",
          stale.status_code == 409, str(stale.status_code))
    check("the conflict says so", (stale.get_json() or {}).get("state") == "conflict",
          str((stale.get_json() or {}).get("state")))

    cleared = client.patch(path, json={"payload": {"issuance.expiry_date": ""}})
    check("clearing a required field is 422", cleared.status_code == 422,
          str(cleared.status_code))
    # The refusal must not have taken effect: a rejected write that half-applied
    # would be worse than one that failed loudly.
    after = (client.get(path).get_json() or {}).get("record", {})
    check("the rejected clear changed nothing",
          after.get("expires_at") == "2031-06-30", str(after.get("expires_at")))

    bad_revision = client.patch(path, json={"title": "x",
                                            "expected_revision": "not a number"})
    check("a non-numeric expected_revision is 400", bad_revision.status_code == 400,
          str(bad_revision.status_code))


def stage_owner_isolation(record_id):
    print("\n[owner isolation]")
    _as(USER_B)
    client = _app().test_client()
    path = f"/api/private-office/structured-records/{record_id}"
    absent_path = "/api/private-office/structured-records/98765432"

    pairs = (
        ("read", client.get(path), client.get(absent_path)),
        ("patch", client.patch(path, json={"title": "mine now"}),
         client.patch(absent_path, json={"title": "mine now"})),
        ("archive", client.delete(path), client.delete(absent_path)),
        ("history", client.get(f"{path}/history"),
         client.get(f"{absent_path}/history")),
    )
    for label, foreign, absent in pairs:
        check(f"a foreign {label} is 404", foreign.status_code == 404,
              str(foreign.status_code))
        check(f"an absent {label} is the same 404 body",
              foreign.get_data() == absent.get_data(),
              f"{foreign.get_data()[:60]!r} vs {absent.get_data()[:60]!r}")
        check_no_plaintext(f"a foreign {label}", foreign)

    # B's own search must reach B's own rows and nothing else. Asserting that
    # B's search is *empty* would pass for the wrong reason — and would stop
    # testing isolation the day the fixture stopped colliding.
    b_search = client.get("/api/private-office/structured-records/search?q=okonkwo")
    reachable = [r.get("record_id") for r in (b_search.get_json() or {}).get("results") or []]
    check("A's record is not in B's search", record_id not in reachable,
          str(reachable))

    _as(USER_A)
    still_mine = _app().test_client().get(path)
    check("B's failed writes did not touch A's record",
          still_mine.status_code == 200, str(still_mine.status_code))
    check("and did not rename it",
          (still_mine.get_json() or {}).get("record", {}).get("title") != "mine now")


def stage_route_prefix_does_not_collide():
    print("\n[route prefix]")
    _as(USER_A)
    client = _app().test_client()
    # Operations owns /api/private-office/records/<view> and is registered
    # first. If this pack ever moved back to /records, Werkzeug would match
    # these against the Operations rule and this pack's handlers would simply
    # never run — a silent 404 from the wrong handler.
    ops = client.get("/api/private-office/records/obligations")
    check("Operations still answers its own prefix", ops.status_code == 200,
          str(ops.status_code))
    listing = client.get("/api/private-office/structured-records")
    check("and the structured store answers its own", listing.status_code == 200,
          str(listing.status_code))
    check("the two surfaces return different shapes, so nothing is shadowed",
          "records" in (ops.get_json() or {})
          and "count" in (listing.get_json() or {}))
    endpoints = {r.endpoint for r in _app().url_map.iter_rules()
                 if str(r.rule).startswith("/api/private-office/structured-records")}
    check("every structured route belongs to this pack",
          all(e.startswith("private_office_structured_records.") for e in endpoints),
          str(sorted(endpoints)))


def stage_archive(record_id):
    print("\n[archive]")
    _as(USER_A)
    client = _app().test_client()
    before = int((client.get("/api/private-office/structured-records"
                             ).get_json() or {}).get("count") or 0)
    resp = client.delete(f"/api/private-office/structured-records/{record_id}")
    body = resp.get_json() or {}
    check("archive is 200", resp.status_code == 200, str(resp.status_code))
    # "archived", never "deleted": nothing downstream should be able to believe
    # the data is gone, because it is not — the revisions and the audit rows
    # that make the record accountable are still there.
    check("the response says archived, not deleted",
          body.get("status") == "archived", str(body.get("status")))
    check_no_plaintext("the archive response", resp)
    after = int((client.get("/api/private-office/structured-records"
                            ).get_json() or {}).get("count") or 0)
    check("the archived record leaves the default list", after == before - 1,
          f"{before} -> {after}")
    history = client.get(
        f"/api/private-office/structured-records/{record_id}/history")
    check("its history survives the archive", history.status_code == 200,
          str(history.status_code))


def stage_kill_switch():
    print("\n[kill switch]")
    _as(USER_A)
    client = _app().test_client()
    spec = feature_matrix.get(routes.RECORDS_FEATURE_ID)
    check("the feature is in the matrix", spec is not None)
    check("and names an environment switch", bool(spec and spec.flag_env),
          str(spec.flag_env if spec else None))
    previous = os.environ.get(spec.flag_env)
    os.environ[spec.flag_env] = "false"
    try:
        off = client.get("/api/private-office/structured-records")
        check("the switch closes the read", off.status_code == 404,
              str(off.status_code))
        check("the switched-off route sells nothing",
              "minimum_tier" not in (off.get_json() or {}), str(off.get_json()))
        off_write = client.post("/api/private-office/structured-records",
                                json={"template_key": "passport",
                                      "payload": PASSPORT})
        check("the switch closes the write too", off_write.status_code == 404,
              str(off_write.status_code))
        # Two stores, two switches. A template problem must not be able to take
        # a member's recorded facts down with it.
        check("the switch does not take the fact routes down with it",
              client.get("/api/private-office/facts").status_code == 200)
    finally:
        if previous is None:
            os.environ.pop(spec.flag_env, None)
        else:
            os.environ[spec.flag_env] = previous


def stage_responses_are_not_stored():
    print("\n[caching]")
    _as(USER_A)
    client = _app().test_client()
    for path in ("/api/private-office/record-templates",
                 "/api/private-office/structured-records",
                 "/api/private-office/structured-records/search?q=okonkwo"):
        cache = client.get(path).headers.get("Cache-Control") or ""
        check(f"{path} is no-store", "no-store" in cache, cache)


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_authentication()
    stage_second_lock()
    stage_templates_and_domains()
    record_id = stage_create()
    if not record_id:
        _FAILURES.append("create returned no record id; later stages skipped")
        print("\nFAIL — create returned no record id")
        return 1
    stage_body_allowlist()
    stage_duplicates_and_idempotency()
    stage_read_and_list(record_id)
    stage_reveal(record_id)
    stage_step_up_is_not_an_oracle(record_id)
    stage_patch_and_concurrency(record_id)
    stage_owner_isolation(record_id)
    stage_route_prefix_does_not_collide()
    stage_archive(record_id)
    stage_kill_switch()
    stage_responses_are_not_stored()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_office_structured_record_routes():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
