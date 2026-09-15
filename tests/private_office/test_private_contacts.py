"""Relationship Intelligence as a contact system — identity, not names.

Run either way::

    python -m pytest tests/private_office/test_private_contacts.py
    python tests/private_office/test_private_contacts.py

What these tests defend
-----------------------
* **One person, many identifiers.** The same human arriving by email, by
  phone, by handle or by meeting invite lands on one record. Every new
  identifier attaches to the person already there instead of starting another.
* **A name is not an identity.** Two contacts called Dana Whitfield stay two
  contacts. This is the one merge the resolver must never make, and it has its
  own test because it is the one a "helpful" refactor would add.
* **A link is confirmed before it is made.** A username that names no account
  is refused, not stored — the alternative is a contact card pointing at
  whoever registers that handle next.
* **Read-back is the truth.** Every write answers with what the store holds,
  so a value that was trimmed, lowercased or refused shows as stored.
* **Scheduling is idempotent.** Inviting the same person to the same meeting
  twice leaves one meeting and one contact.
* **Removal cascades into nothing.** The meeting survives, the account
  survives, the facts survive with their provenance.
* **Nothing crosses an owner.** Another member's contact is indistinguishable
  from one that never existed — on read, edit, delete, favourite and search.
  An email address that is one member's contact resolves to nobody for anyone
  else, so the resolver is not an existence oracle.
* **Notes are not an index.** They are the most private field here and they do
  not appear in search results, in another owner's anything, or in an audit row.
"""

import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_contacts_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

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
from services import private_office_meetings_routes as meet_routes  # noqa: E402
from services import private_office_relationships_routes as rel_routes  # noqa: E402
from services.private_office import graph as graph_mod  # noqa: E402
from services.private_office import meetings as meetings_mod  # noqa: E402
from services.private_office import records as records_mod  # noqa: E402
from services.private_office import relationships as rel  # noqa: E402
from services.private_office import schema  # noqa: E402

OWNER = 7701          # the member whose office this is
OTHER = 7702          # a second Private member, for isolation
INVITEE = 7703        # a PulseSoc account the owner invites to a meeting
LINKED = 7704         # a PulseSoc account the owner links a contact to

PASSCODE = "410973"

_FAILURES: list[str] = []
_GRANTS: dict[int, str] = {}
_STATE: dict = {}


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


class _GrantClient(FlaskClient):
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
    rel_routes.register(app)
    meet_routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active",
                        "access_enabled": 1}


def _unlock(user_id):
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


#: The accounts this office can see. Real usernames and avatars, because the
#: photo-priority rule is only testable against an account that has one.
_ACCOUNTS = (
    (OWNER, "owner_one", "Office Owner", ""),
    (OTHER, "other_one", "Other Member", ""),
    (INVITEE, "marcus_reed", "Marcus Reed", "https://cdn.example/marcus.jpg"),
    (LINKED, "dana_w", "Dana W.", "https://cdn.example/dana.jpg"),
)


def setup_environment():
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1, username TEXT, "
            "display_name TEXT, avatar_url TEXT)"
        )
        conn.execute("DELETE FROM users")
        for uid, username, display, avatar in _ACCOUNTS:
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled, "
                "username, display_name, avatar_url) VALUES (?, 'active', 1, ?, ?, ?)",
                (uid, username, display, avatar),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        records_mod.ensure_records_schema(cur, force=True)
        meetings_mod.ensure_meetings_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (OWNER, OTHER):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (OWNER, OTHER):
        _unlock(uid)
    # Meetings default OFF; §19's automatic contact only exists behind them.
    os.environ["PRIVATE_MEETINGS_ENABLED"] = "1"
    _stub._test_user = None


def _query_all(sql, params=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _post(client, path, body):
    return client.post(path, json=body)


# ---------------------------------------------------------------------------
# Normalization — the shapes identity is compared in
# ---------------------------------------------------------------------------

def stage_normalization():
    print("\n[contacts: normalization]")
    check("email folds case and whitespace",
          rel.normalize_email("  Dana@Example.COM ") == "dana@example.com")
    check("an address with no domain dot is not an address",
          rel.normalize_email("dana@example") == "")
    check("phone folds punctuation",
          rel.normalize_phone("+1 (415) 555-0134") == "+14155550134")
    check("a bare number keeps no invented country code",
          rel.normalize_phone("4155550134") == "4155550134"
          and rel.normalize_phone("4155550134") != rel.normalize_phone("+14155550134"))
    check("a number too short to be one is refused",
          rel.normalize_phone("123") == "")
    check("a handle loses its @ and its case",
          rel.normalize_username(" @Dana_W ") == "dana_w")
    check("an unknown source falls back to OTHER, never to MANUAL",
          rel.normalize_source("scraped") == rel.SOURCE_OTHER)
    check("the external ref scheme round-trips",
          rel.account_id_from_ref(rel.external_ref_for(LINKED)) == LINKED)
    check("a node with no ref reports no account",
          rel.account_id_from_ref("") == 0)


# ---------------------------------------------------------------------------
# §1-4: the full contact, stored and read back
# ---------------------------------------------------------------------------

def stage_add_full_contact():
    print("\n[contacts: add a whole person]")
    client = _app().test_client()
    _as(OWNER)

    resp = _post(client, "/api/private-office/relationships", {
        "name": "  Dana   Whitfield ", "role": "Attorney",
        "phone": "+1 (415) 555-0134", "email": "Dana@Example.COM",
        # A word that appears in no other field, so a search hit on it could
        # only have come from the notes.
        "notes": "Handles the Dorchester paperwork.",
    })
    body = resp.get_json() or {}
    person = body.get("person") or {}
    _STATE["dana"] = person.get("node_id")
    check("a full contact is created", resp.status_code == 201
          and body.get("status") == rel.SAVE_CREATED, f"{resp.status_code} {body}")
    check("the response is the stored value, not the typed one",
          person.get("name") == "Dana Whitfield"
          and person.get("phone") == "+14155550134"
          and person.get("email") == "dana@example.com", str(person))
    check("a person id comes back and is the node id",
          person.get("person_id") == person.get("node_id") and person.get("node_id"),
          str(person))
    check("an unlinked contact with no photo renders as initials",
          person.get("photo_source") == "initials"
          and person.get("linked_account") is None, str(person))
    check("the source is recorded as MANUAL", person.get("source") == rel.SOURCE_MANUAL,
          str(person.get("source")))

    resp = _post(client, "/api/private-office/relationships",
                 {"name": "Nobody", "email": "not-an-address"})
    check("an unusable email is refused rather than stored",
          resp.status_code == 400, str(resp.status_code))
    check("and no person was created for it",
          not [p for p in _directory(client) if p["name"] == "Nobody"])


def _directory(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    resp = client.get("/api/private-office/relationships" + (f"?{query}" if query else ""))
    return (resp.get_json() or {}).get("people") or []


# ---------------------------------------------------------------------------
# §6-7: identity resolution and the merge that must never happen
# ---------------------------------------------------------------------------

def stage_identity_resolution():
    print("\n[contacts: identity]")
    client = _app().test_client()
    _as(OWNER)
    before = len(_directory(client))

    # The same email, typed differently, with a different name on it.
    resp = _post(client, "/api/private-office/relationships",
                 {"name": "D. Whitfield", "email": "  DANA@example.com  "})
    body = resp.get_json() or {}
    check("the same email resolves onto the person already there",
          resp.status_code == 200 and body.get("person", {}).get("node_id") == _STATE["dana"]
          and body.get("matched_on") == rel.MATCH_EMAIL, str(body))
    check("and it is not reported as a creation",
          body.get("status") != rel.SAVE_CREATED, str(body.get("status")))
    check("the directory did not grow", len(_directory(client)) == before,
          f"{before} → {len(_directory(client))}")

    # The same *name*, and nothing else in common. Two people.
    resp = _post(client, "/api/private-office/relationships",
                 {"name": "Dana Whitfield", "phone": "+1 212 555 9000"})
    body = resp.get_json() or {}
    _STATE["dana_two"] = body.get("person", {}).get("node_id")
    check("a shared name is never a merge",
          resp.status_code == 201 and _STATE["dana_two"] != _STATE["dana"],
          f"{resp.status_code} {body}")
    check("the directory grew by exactly one",
          len(_directory(client)) == before + 1, str(len(_directory(client))))

    # Phone match, on the second Dana.
    resp = _post(client, "/api/private-office/relationships",
                 {"name": "Dana (NY)", "phone": "+12125559000"})
    body = resp.get_json() or {}
    check("a matching phone resolves onto the right one of the two",
          body.get("person", {}).get("node_id") == _STATE["dana_two"]
          and body.get("matched_on") == rel.MATCH_PHONE, str(body))

    check("still two Danas and no third",
          len(_directory(client)) == before + 1, str(len(_directory(client))))


# ---------------------------------------------------------------------------
# §8, §44-45: linking an account, and linking one later
# ---------------------------------------------------------------------------

def stage_account_linking():
    print("\n[contacts: linking a PulseSoc account]")
    client = _app().test_client()
    _as(OWNER)

    resp = client.get("/api/private-office/relationships/lookup?username=nobody_at_all")
    check("a handle nobody has is a 404 from lookup", resp.status_code == 404,
          str(resp.status_code))

    resp = client.get("/api/private-office/relationships/lookup?username=@Dana_W")
    account = (resp.get_json() or {}).get("account") or {}
    check("a real handle resolves to the account behind it",
          resp.status_code == 200 and account.get("user_id") == LINKED
          and account.get("display_name") == "Dana W.", str(account))

    resp = _post(client, "/api/private-office/relationships",
                 {"name": "Phantom", "username": "nobody_at_all"})
    check("saving against a handle nobody has is refused, not stored",
          resp.status_code == 400, str(resp.status_code))
    check("and no dangling contact was left behind",
          not [p for p in _directory(client) if p["name"] == "Phantom"])

    # Link the first Dana to an account she turns out to have. §45.
    resp = client.patch(f"/api/private-office/relationships/{_STATE['dana']}",
                        json={"username": "@Dana_W"})
    person = (resp.get_json() or {}).get("person") or {}
    check("an existing contact takes the account without becoming a second one",
          resp.status_code == 200 and person.get("node_id") == _STATE["dana"]
          and person.get("pulsesoc_user_id") == LINKED, str(person))
    check("the handle is stored in its canonical spelling",
          person.get("username") == "dana_w", str(person.get("username")))
    check("a linked contact draws the account's profile photo",
          person.get("photo_source") == "pulsesoc_profile"
          and (person.get("linked_account") or {}).get("avatar_url"), str(person))

    nodes = _query_all(
        f"SELECT external_ref FROM {schema.NODES_TABLE} WHERE owner_user_id=? "
        f"AND external_ref != ''", (OWNER,))
    check("the account id is the node's external reference, not a loose fact",
          [n["external_ref"] for n in nodes] == [rel.external_ref_for(LINKED)],
          str(nodes))

    # The same account again, from scratch. Must land on the same person.
    resp = _post(client, "/api/private-office/relationships",
                 {"name": "Dana Again", "username": "dana_w"})
    body = resp.get_json() or {}
    check("the account id outranks everything and resolves to the same person",
          body.get("person", {}).get("node_id") == _STATE["dana"]
          and body.get("matched_on") == rel.MATCH_ACCOUNT, str(body))

    # §44: a second contact cannot quietly take an account another one holds.
    conn = db.connect()
    try:
        cur = conn.cursor()
        collided = False
        try:
            graph_mod.attach_external_ref(
                cur, owner_user_id=OWNER, node_id=_STATE["dana_two"],
                external_ref=rel.external_ref_for(LINKED))
        except graph_mod.PrivateGraphRejected:
            collided = True
        conn.rollback()
    finally:
        conn.close()
    check("an identifier another contact already holds is a refusal, not a merge",
          collided)


# ---------------------------------------------------------------------------
# §3, §26: editing the private record, and what is not editable
# ---------------------------------------------------------------------------

def stage_edit():
    print("\n[contacts: edit]")
    client = _app().test_client()
    _as(OWNER)

    resp = client.patch(f"/api/private-office/relationships/{_STATE['dana']}",
                        json={"role": "Estate attorney"})
    person = (resp.get_json() or {}).get("person") or {}
    check("a field the body names is changed",
          person.get("role") == "Estate attorney", str(person.get("role")))
    check("and a field it does not name is left alone",
          person.get("phone") == "+14155550134" and person.get("email") == "dana@example.com",
          str(person))

    resp = client.patch(f"/api/private-office/relationships/{_STATE['dana']}",
                        json={"phone": ""})
    person = (resp.get_json() or {}).get("person") or {}
    check("an explicit empty string clears the field",
          person.get("phone") == "", str(person.get("phone")))
    check("clearing retires the fact rather than deleting it",
          _query_all(
              f"SELECT id FROM {schema.FACTS_TABLE} WHERE owner_user_id=? "
              f"AND typed_value=?", (OWNER, "+14155550134")))

    resp = client.patch(f"/api/private-office/relationships/{_STATE['dana']}",
                        json={"phone": "+14155550134"})
    check("and the member can put it back",
          (resp.get_json() or {}).get("person", {}).get("phone") == "+14155550134")

    # An untouched save must not march the observation dates forward.
    before = _query_all(
        f"SELECT id, observed_at FROM {schema.FACTS_TABLE} "
        f"WHERE owner_user_id=? AND fact_type=?", (OWNER, rel.FACT_EMAIL))
    client.patch(f"/api/private-office/relationships/{_STATE['dana']}",
                 json={"email": "dana@example.com"})
    after = _query_all(
        f"SELECT id, observed_at FROM {schema.FACTS_TABLE} "
        f"WHERE owner_user_id=? AND fact_type=?", (OWNER, rel.FACT_EMAIL))
    check("re-saving an unchanged value writes nothing at all", before == after,
          f"{before} → {after}")


# ---------------------------------------------------------------------------
# §17-19, §50: meetings create contacts, and doing it twice changes nothing
# ---------------------------------------------------------------------------

def stage_meeting_invitees():
    print("\n[contacts: meeting invitees]")
    client = _app().test_client()
    _as(OWNER)

    resp = _post(client, "/api/private-office/meetings",
                 {"title": "Estate review", "scheduled_start_at": "2030-01-05T15:00:00",
                  "duration_minutes": 30})
    meeting = (resp.get_json() or {}).get("meeting") or {}
    ref = meeting.get("public_id") or ""
    _STATE["meeting"] = ref
    check("a meeting is scheduled", resp.status_code in (200, 201) and ref,
          f"{resp.status_code} {meeting}")

    before = len(_directory(client))
    resp = _post(client, f"/api/private-office/meetings/{ref}/invites",
                 {"user_ids": [INVITEE]})
    body = resp.get_json() or {}
    check("the invite succeeds", resp.status_code == 200 and INVITEE in (body.get("invited") or []),
          f"{resp.status_code} {body}")
    check("and the invitee became a contact",
          INVITEE in ((body.get("directory") or {}).get("created") or []), str(body.get("directory")))

    people = _directory(client)
    marcus = [p for p in people if p["pulsesoc_user_id"] == INVITEE]
    check("exactly one contact for the invitee", len(marcus) == 1, str(people))
    check("named from their account, sourced as a meeting invitee",
          marcus and marcus[0]["name"] == "Marcus Reed"
          and marcus[0]["source"] == rel.SOURCE_MEETING_INVITEE, str(marcus))
    check("and wearing their PulseSoc profile photo",
          marcus and marcus[0]["photo_source"] == "pulsesoc_profile", str(marcus))
    _STATE["marcus"] = marcus[0]["node_id"] if marcus else 0

    # The member renames them. A second invite must not rename them back.
    client.patch(f"/api/private-office/relationships/{_STATE['marcus']}",
                 json={"name": "Marcus (accountant)"})

    resp = _post(client, f"/api/private-office/meetings/{ref}/invites",
                 {"user_ids": [INVITEE, INVITEE]})
    body = resp.get_json() or {}
    people = _directory(client)
    marcus = [p for p in people if p["pulsesoc_user_id"] == INVITEE]
    check("re-inviting creates no second contact", len(marcus) == 1, str(people))
    check("the directory is the same size as before the repeat",
          len(people) == before + 1, f"{before + 1} vs {len(people)}")
    check("and the member's own name for them survives",
          marcus and marcus[0]["name"] == "Marcus (accountant)", str(marcus))

    meetings = _query_all(
        "SELECT id FROM private_meetings WHERE owner_user_id=? AND title=?",
        (OWNER, "Estate review"))
    check("and there is still exactly one meeting", len(meetings) == 1, str(meetings))

    invites = _query_all(
        "SELECT id FROM private_meeting_invites WHERE invitee_user_id=?", (INVITEE,))
    check("and exactly one invite row", len(invites) == 1, str(invites))


# ---------------------------------------------------------------------------
# §28-30: search, sort, favourites
# ---------------------------------------------------------------------------

def stage_search_and_order():
    print("\n[contacts: search, sort, favourites]")
    client = _app().test_client()
    _as(OWNER)

    hits = _directory(client, q="attorney")
    check("search reaches the role", [h["node_id"] for h in hits] == [_STATE["dana"]],
          str([h["name"] for h in hits]))

    hits = _directory(client, q="dana%40example.com")
    check("search reaches the email", [h["node_id"] for h in hits] == [_STATE["dana"]],
          str([h["name"] for h in hits]))

    hits = _directory(client, q="dorchester")
    check("search does not reach into notes",
          not [h for h in hits if h["node_id"] == _STATE["dana"]],
          str([h["name"] for h in hits]))

    hits = _directory(client, q="zzz-nobody")
    check("a query that matches nobody is empty, not everybody", hits == [], str(hits))

    resp = _post(client, f"/api/private-office/relationships/{_STATE['marcus']}/favorite",
                 {"favorite": True})
    check("a person can be pinned",
          resp.status_code == 200
          and (resp.get_json() or {}).get("person", {}).get("favorite") is True,
          str(resp.get_json()))

    people = _directory(client)
    check("favourites lead the list",
          people and people[0]["node_id"] == _STATE["marcus"],
          str([p["name"] for p in people]))
    check("favourites-only returns only them",
          [p["node_id"] for p in _directory(client, favorites=1)] == [_STATE["marcus"]])

    by_name = _directory(client, sort="name")
    unpinned = [p["name"] for p in by_name if not p["favorite"]]
    check("name order is name order", unpinned == sorted(unpinned), str(unpinned))

    check("an unknown sort falls back rather than failing",
          _directory(client, sort="by-vibes"))

    _post(client, f"/api/private-office/relationships/{_STATE['marcus']}/favorite",
          {"favorite": False})
    check("and can be unpinned",
          not _directory(client, favorites=1))


# ---------------------------------------------------------------------------
# §27: removal cascades into nothing
# ---------------------------------------------------------------------------

def stage_removal():
    print("\n[contacts: removal]")
    client = _app().test_client()
    _as(OWNER)

    resp = client.delete(f"/api/private-office/relationships/{_STATE['dana_two']}")
    check("a person can be removed", resp.status_code == 200, str(resp.status_code))
    check("and is gone from the directory",
          not [p for p in _directory(client) if p["node_id"] == _STATE["dana_two"]])
    resp = client.get(f"/api/private-office/relationships/{_STATE['dana_two']}")
    check("and reads as not found", resp.status_code == 404, str(resp.status_code))

    check("the node was archived, not deleted",
          _query_all(f"SELECT lifecycle_state FROM {schema.NODES_TABLE} WHERE id=?",
                     (_STATE["dana_two"],)))
    check("removing them a second time is a clean 404",
          client.delete(f"/api/private-office/relationships/{_STATE['dana_two']}"
                        ).status_code == 404)

    # Now remove somebody who is in a meeting. The meeting must survive.
    resp = client.delete(f"/api/private-office/relationships/{_STATE['marcus']}")
    check("a contact who is a meeting invitee can be removed too",
          resp.status_code == 200, str(resp.status_code))
    check("their meeting survives",
          _query_all("SELECT id FROM private_meetings WHERE owner_user_id=? AND title=?",
                     (OWNER, "Estate review")))
    check("their invite survives",
          _query_all("SELECT id FROM private_meeting_invites WHERE invitee_user_id=?",
                     (INVITEE,)))
    check("their account survives",
          _query_all("SELECT user_id FROM users WHERE user_id=?", (INVITEE,)))
    check("and their facts survive with their provenance",
          _query_all(
              f"SELECT id FROM {schema.FACTS_TABLE} WHERE owner_user_id=? "
              f"AND subject_id=?", (OWNER, str(_STATE["marcus"]))))


# ---------------------------------------------------------------------------
# §35-37, §47-48: isolation, the second lock, and no notification
# ---------------------------------------------------------------------------

def stage_isolation():
    print("\n[contacts: isolation]")
    client = _app().test_client()

    # What the owner's record says before another member touches anything, so
    # the comparison at the end is against the truth rather than a literal that
    # an earlier stage can quietly invalidate.
    _as(OWNER)
    was = ((client.get(f"/api/private-office/relationships/{_STATE['dana']}").get_json()
            or {}).get("person") or {}).get("name")

    _as(OTHER)

    check("another member's directory holds none of the owner's people",
          _directory(client) == [])

    dana = _STATE["dana"]
    check("reading is not found", client.get(
        f"/api/private-office/relationships/{dana}").status_code == 404)
    check("editing is not found", client.patch(
        f"/api/private-office/relationships/{dana}", json={"name": "x"}).status_code == 404)
    check("deleting is not found", client.delete(
        f"/api/private-office/relationships/{dana}").status_code == 404)
    check("favouriting is not found", _post(
        client, f"/api/private-office/relationships/{dana}/favorite",
        {"favorite": True}).status_code == 404)

    # The resolver must not be an existence oracle: the owner's email address
    # creates a *new* person here rather than resolving onto theirs.
    resp = _post(client, "/api/private-office/relationships",
                 {"name": "Someone", "email": "dana@example.com"})
    body = resp.get_json() or {}
    check("the owner's identifier resolves to nobody for another member",
          resp.status_code == 201 and body.get("status") == rel.SAVE_CREATED
          and body.get("person", {}).get("node_id") != dana, str(body))

    check("and the owner's person is untouched by it",
          _query_all(f"SELECT id FROM {schema.NODES_TABLE} WHERE id=? AND owner_user_id=?",
                     (dana, OWNER)))

    _as(OWNER)
    person = (client.get(f"/api/private-office/relationships/{dana}").get_json()
              or {}).get("person") or {}
    check("the owner still sees their own person unchanged",
          person.get("name") == was, f"{was} → {person.get('name')}")


def stage_second_lock():
    print("\n[contacts: the second lock]")
    app = _app()
    _as(OWNER)
    was = ((_app().test_client().get(
        f"/api/private-office/relationships/{_STATE['dana']}").get_json()
        or {}).get("person") or {}).get("name")
    locked = app.test_client()  # no grant header
    token = _GRANTS.pop(OWNER, "")

    paths = (
        ("GET", "/api/private-office/relationships"),
        ("GET", "/api/private-office/relationships/lookup?username=dana_w"),
        ("POST", "/api/private-office/relationships"),
        ("PATCH", f"/api/private-office/relationships/{_STATE['dana']}"),
        ("DELETE", f"/api/private-office/relationships/{_STATE['dana']}"),
        ("POST", f"/api/private-office/relationships/{_STATE['dana']}/favorite"),
    )
    for method, path in paths:
        resp = locked.open(path, method=method, json={})
        check(f"{method} {path.split('?')[0]} is refused while the office is locked",
              resp.status_code == 423, str(resp.status_code))

    _GRANTS[OWNER] = token
    now = ((_app().test_client().get(
        f"/api/private-office/relationships/{_STATE['dana']}").get_json()
        or {}).get("person") or {}).get("name")
    check("and the person survived every locked attempt", now == was and bool(now),
          f"{was} → {now}")


def stage_no_notification():
    print("\n[contacts: nobody is told]")
    # The linked account is a real member. Being recorded in someone else's
    # office must leave no trace anywhere they can see.
    rows = _query_all(
        f"SELECT id FROM {schema.AUDIT_TABLE} WHERE owner_user_id=?", (LINKED,))
    check("the linked member has no audit rows from being added", not rows, str(rows))
    rows = _query_all(
        f"SELECT id FROM {schema.NODES_TABLE} WHERE owner_user_id=?", (LINKED,))
    check("and no nodes were created in an office that is not theirs", not rows, str(rows))
    rows = _query_all(
        f"SELECT id FROM {schema.FACTS_TABLE} WHERE owner_user_id=?", (INVITEE,))
    check("nor for the meeting invitee", not rows, str(rows))

    names = _query_all(
        f"SELECT object_id, object_type FROM {schema.AUDIT_TABLE} WHERE owner_user_id=?",
        (OWNER,))
    leaked = [r for r in names if "Dana" in str(r.get("object_id"))
              or "@" in str(r.get("object_id"))]
    check("and no audit row carries a name or an address", not leaked, str(leaked))


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

STAGES = (
    stage_normalization,
    stage_add_full_contact,
    stage_identity_resolution,
    stage_account_linking,
    stage_edit,
    stage_meeting_invitees,
    stage_search_and_order,
    stage_removal,
    stage_isolation,
    stage_second_lock,
    stage_no_notification,
)


def run_all():
    setup_environment()
    for stage in STAGES:
        _as(OWNER)
        stage()
    _stub._test_user = None
    return list(_FAILURES)


def test_everything():
    failures = run_all()
    assert not failures, "\n".join(failures)


if __name__ == "__main__":
    failures = run_all()
    print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S)"))
    for line in failures:
        print("  -", line)
    sys.exit(1 if failures else 0)
