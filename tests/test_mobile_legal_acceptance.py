"""One account, one acceptance ledger, asked the same way on both platforms.

`/login` learned to ask a member about a rewritten document. `/api/mobile/auth/login`
did not -- so an iPhone-only member could authenticate, use PulseSoc and never
appear in `user_legal_acceptances` at any version. These are the assertions that
make the two surfaces one contract:

* **The gate sits before admission, not beside it.** The mobile endpoint also
  writes `session["account_user_id"]`, so a refusal that lands after it would
  hand a gated client a working web cookie. `test_a_gated_login_grants_nothing`
  checks the absence of all three grants, because any one of them is the bypass.

* **The client does not get a vote.** Nothing on the wire names a version as an
  input. A ticket carries the versions the member *was shown* so the server can
  refuse an acceptance answering text that changed underneath -- the opposite of
  letting a client choose.

* **Accepting anywhere counts everywhere.** Web and iOS write the same rows in
  the same table, so the cross-platform tests here are not integration sugar;
  they are the reason a second consent system was not built.

The mutation block at the bottom is the part that earns the rest. Every test
above it would stay green against an implementation that trusted the client, so
each mutation names one way to get this wrong and proves this file notices.

Run: python3 -m pytest tests/test_mobile_legal_acceptance.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mobile_legal_acceptance_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("FLASK_SECRET_KEY", "mobile-legal-acceptance")

import bot  # noqa: E402
from services import cache_engine  # noqa: E402
from services import db as db_service  # noqa: E402
from services import legal_acceptance  # noqa: E402
from services import pulse_security_core  # noqa: E402

PASSWORD = "correct horse battery staple"
LOGIN = "/api/mobile/auth/login"
ACCEPT = "/api/mobile/auth/legal-acceptance"
SESSION = "/api/mobile/auth/session"
REFRESH = "/api/mobile/auth/refresh"

#: A version nothing is on file at. Used instead of editing a document, because
#: it is the same input `outstanding()` reads and it cannot leave a published
#: page disagreeing with a constant.
NEXT_TERMS = "PULSESOC_TERMS_2027_01"


@pytest.fixture(scope="module", autouse=True)
def schema():
    bot.init_db()


@pytest.fixture(autouse=True)
def quiet_limiters():
    """Three independent limiters accumulate per *process*, not per test.

    A file that signs in this many times passes test-by-test and fails as a
    file, and it fails as `login_challenge_required` or a bare 429 -- correct
    answers about a state no test here is exercising. Keeping the request count
    under the thresholds instead would decay the first time somebody adds a
    case, so all three are reset:
    `login_security_preflight`'s velocity rows, `pulse_security_core`'s bucket
    dict (mirrored into the cache engine), and `basic_abuse_guard`'s.
    """

    pulse_security_core._RATE_BUCKETS.clear()
    cache_engine._MEMORY.clear()
    bot.RATE_LIMIT_BUCKETS.clear()
    conn = db_service.connect()
    try:
        for table in ("auth_events", "failed_login_controls", "failed_login_safe_list"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture
def client():
    return bot.webhook_app.test_client()


def _member(source="web_signup", verified=True):
    email = f"mla-{os.urandom(6).hex()}@example.com"
    user, error = bot.create_account(
        "Consent Parity",
        email,
        PASSWORD,
        username=f"mla{os.urandom(4).hex()}",
        age_confirmed=True,
        accepted_terms_source=source,
    )
    assert not error, f"signup failed: {error}"
    if verified:
        _sql("UPDATE users SET email_verified=1 WHERE user_id=?", (user["user_id"],))
    return user["user_id"], email


def _sql(statement, params=()):
    conn = db_service.connect()
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _login(client, email, password=PASSWORD, **extra):
    return client.post(LOGIN, json={"identifier": email, "password": password, **extra})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _row_count(user_id):
    return len(legal_acceptance.accepted(user_id))


# ---------------------------------------------------------------------------
# The gate: what a refused sign-in is and is not given
# ---------------------------------------------------------------------------


def test_a_member_on_file_at_the_current_versions_signs_in_untouched(client):
    """§7's first requirement, asserted as the default path.

    Most members are already current, and for them this mission has to be
    invisible. A gate that charges everybody for the few is not acceptable
    friction, it is a regression.
    """

    user_id, email = _member()
    assert legal_acceptance.outstanding(user_id) == []
    response = _login(client, email)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["authenticated"] is True
    assert body.get("access_token")
    assert "legal_acceptance" not in body


def test_an_account_with_nothing_on_file_is_asked(client):
    """The population this mission exists for: created before the ledger, or by
    an admin, and never recorded at any version."""

    user_id, email = _member(source=None)
    assert legal_acceptance.outstanding(user_id) == sorted(legal_acceptance.DOCUMENTS)
    response = _login(client, email)
    assert response.status_code == 403
    body = response.get_json()
    assert body["error_code"] == "legal_acceptance_required"
    assert {entry["document"] for entry in body["legal_acceptance"]["documents"]} == set(legal_acceptance.DOCUMENTS)


def test_a_gated_login_grants_nothing(client):
    """The whole point of the gate's *position*.

    Three grants live in this endpoint -- a bearer token, a refresh cookie and
    `session["account_user_id"]` -- and the last is the one easy to forget,
    because it is a web cookie handed out by a mobile endpoint. Any one of them
    surviving a refusal is a partial session with full authorisation, which §14
    refuses.
    """

    _user_id, email = _member(source=None)
    response = _login(client, email)
    assert response.status_code == 403
    body = response.get_json()
    assert body.get("access_token") is None
    assert body.get("refresh_token") is None
    assert bot.PERSISTENT_SESSION_COOKIE not in response.headers.get("Set-Cookie", "")
    # The cookie session is the escape route a UI cannot cover: if it were set,
    # every browser-authenticated route would admit this member.
    with client.session_transaction() as flask_session:
        assert "account_user_id" not in flask_session


def test_the_refusal_still_refuses_a_wrong_password_first(client):
    """Ordering, not just presence.

    `legal_acceptance_required` names a real account. Answered before the
    password it would be an enumeration oracle -- exactly what the shared
    `invalid_credentials` sentence above it exists to prevent.
    """

    _user_id, email = _member(source=None)
    response = _login(client, email, password="not the password")
    assert response.status_code == 401
    assert response.get_json()["error_code"] == "invalid_credentials"


def test_an_unconfirmed_account_is_told_about_email_not_the_documents(client):
    """Two refusals both sit behind the password; this pins which comes first.

    Asking an unconfirmed member to accept documents would walk them into a
    screen that cannot admit them, and `complete_mobile_login` would still be
    unreachable. The earlier, more actionable refusal wins.
    """

    _user_id, email = _member(source=None, verified=False)
    response = _login(client, email)
    assert response.status_code == 403
    assert response.get_json()["error_code"] == "email_not_confirmed"


def test_a_restricted_account_is_refused_before_the_documents(client):
    _user_id, email = _member(source=None)
    _sql("UPDATE users SET login_enabled=0 WHERE email=?", (email,))
    response = _login(client, email)
    assert response.status_code == 403
    assert response.get_json()["error_code"] == "account_restricted"


# ---------------------------------------------------------------------------
# Accepting: the ticket path
# ---------------------------------------------------------------------------


def test_accepting_with_the_ticket_records_the_row_and_admits(client):
    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]

    response = client.post(ACCEPT, json={"ticket": ticket})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["authenticated"] is True
    assert body["access_token"]
    assert body["refresh_token"]

    on_file = {entry["document"]: entry for entry in legal_acceptance.accepted(user_id)}
    assert set(on_file) == set(legal_acceptance.DOCUMENTS)
    for document, version in legal_acceptance.DOCUMENTS.items():
        assert on_file[document]["document_version"] == version
        assert on_file[document]["acceptance_source"] == "mobile_login"
    assert legal_acceptance.outstanding(user_id) == []
    # And the next sign-in is ordinary. §8: once validly accepted, do not ask
    # again until a new required version actually exists.
    assert _login(client, email).status_code == 200


def test_the_admitted_session_is_a_real_one(client):
    """A token that is minted but not honoured is a cosmetic fix.

    `complete_mobile_login` is shared with the ungated path precisely so this
    cannot be true on one and false on the other.
    """

    _user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    token = client.post(ACCEPT, json={"ticket": ticket}).get_json()["access_token"]

    probe = client.get(SESSION, headers=_bearer(token))
    assert probe.status_code == 200
    body = probe.get_json()
    assert body["authenticated"] is True
    assert body["legal_acceptance_required"] is False


def test_accepting_twice_leaves_one_row_per_document(client):
    """§16. A double tap, or a retry after a response that never arrived, is one
    logical acceptance -- and both are ordinary on a phone."""

    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]

    first = client.post(ACCEPT, json={"ticket": ticket})
    after_first = legal_acceptance.accepted(user_id)
    second = client.post(ACCEPT, json={"ticket": ticket})

    assert first.status_code == 200
    assert second.status_code == 200, second.get_json()
    assert legal_acceptance.accepted(user_id) == after_first
    assert len(after_first) == len(legal_acceptance.DOCUMENTS)
    # Still admitted, so a client that retried into a success does not then have
    # to explain an error to a member who did nothing wrong.
    assert second.get_json()["authenticated"] is True


def test_a_retry_after_a_lost_response_still_admits(client):
    """§17's network case, from the server's side.

    The client cannot distinguish "the write never happened" from "the reply was
    lost", so the only safe server behaviour is for the second attempt to be
    indistinguishable from the first.
    """

    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    conn = db_service.connect()
    try:
        legal_acceptance.record(conn.cursor(), user_id, source="mobile_login")
        conn.commit()
    finally:
        conn.close()

    response = client.post(ACCEPT, json={"ticket": ticket})
    assert response.status_code == 200
    assert response.get_json()["access_token"]
    assert len(legal_acceptance.accepted(user_id)) == len(legal_acceptance.DOCUMENTS)


def test_two_simultaneous_acceptances_do_not_double_write(client):
    """Interleaved rather than threaded, which is the race that actually happens:
    two devices, or a retry overlapping the original."""

    user_id, email = _member(source=None)
    one = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    two = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    assert client.post(ACCEPT, json={"ticket": one}).status_code == 200
    assert client.post(ACCEPT, json={"ticket": two}).status_code == 200
    assert len(legal_acceptance.accepted(user_id)) == len(legal_acceptance.DOCUMENTS)


def test_the_documents_changing_mid_flow_refuses_rather_than_records(client):
    """§17's nastiest case, and the reason the ticket carries versions at all.

    A member reading the Terms while the Terms are replaced must not be recorded
    as having agreed to the replacement. The refusal carries a fresh challenge,
    so the honest outcome is "read it again", not "sign in again".
    """

    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]

    original = dict(legal_acceptance.DOCUMENTS)
    legal_acceptance.DOCUMENTS["terms"] = NEXT_TERMS
    try:
        response = client.post(ACCEPT, json={"ticket": ticket})
        assert response.status_code == 403
        body = response.get_json()
        assert body["error_code"] == "legal_acceptance_required"
        assert body["legal_acceptance"]["ticket"] != ticket
        versions = {entry["document"]: entry["version"] for entry in body["legal_acceptance"]["documents"]}
        assert versions["terms"] == NEXT_TERMS
        assert _row_count(user_id) == 0, "an acceptance was recorded for text the member never saw"
    finally:
        legal_acceptance.DOCUMENTS.clear()
        legal_acceptance.DOCUMENTS.update(original)


def test_an_expired_ticket_is_refused(client):
    """The ticket stands in for a password check, so it has to stop standing in."""

    user_id, _email = _member(source=None)
    stale, _expires = bot.mobile_legal_acceptance_ticket(
        user_id,
        legal_acceptance.versions_in_force(),
        issued_at=int(time.time()) - bot.MOBILE_LEGAL_TICKET_TTL_SECONDS - 5,
    )
    response = client.post(ACCEPT, json={"ticket": stale})
    assert response.status_code == 401
    assert _row_count(user_id) == 0


# ---------------------------------------------------------------------------
# Accepting: the restored-session path
# ---------------------------------------------------------------------------


def test_a_live_session_is_told_when_a_new_version_arrives(client):
    """§12. A refresh token lives about ten years, so checking only at the
    password would never reach a member who is already signed in."""

    user_id, email = _member()
    token = _login(client, email).get_json()["access_token"]
    assert client.get(SESSION, headers=_bearer(token)).get_json()["legal_acceptance_required"] is False

    original = dict(legal_acceptance.DOCUMENTS)
    legal_acceptance.DOCUMENTS["terms"] = NEXT_TERMS
    try:
        body = client.get(SESSION, headers=_bearer(token)).get_json()
        assert body["authenticated"] is True, "informing a live session must not sever it"
        assert body["legal_acceptance_required"] is True
        documents = {entry["document"]: entry["version"] for entry in body["legal_acceptance"]["documents"]}
        assert documents == {"terms": NEXT_TERMS}
        # No ticket: the caller already holds a bearer token and needs no second
        # capability to prove the same thing.
        assert "ticket" not in body["legal_acceptance"]

        accepted = client.post(ACCEPT, json={}, headers=_bearer(token))
        assert accepted.status_code == 200, accepted.get_json()
        assert accepted.get_json()["legal_acceptance_required"] is False
        on_file = {entry["document_version"] for entry in legal_acceptance.accepted(user_id)}
        assert NEXT_TERMS in on_file
    finally:
        legal_acceptance.DOCUMENTS.clear()
        legal_acceptance.DOCUMENTS.update(original)


def test_refresh_reports_the_same_answer(client):
    """The second of the two moments a native client asks "am I still signed in".

    Both carry it so the detection ceiling is one access-token lifetime rather
    than one cold start -- a member who never force-quits still finds out.
    """

    _user_id, email = _member()
    refresh_token = _login(client, email).get_json()["refresh_token"]
    original = dict(legal_acceptance.DOCUMENTS)
    legal_acceptance.DOCUMENTS["terms"] = NEXT_TERMS
    try:
        body = client.post(REFRESH, json={"refresh_token": refresh_token}).get_json()
        assert body["authenticated"] is True
        assert body["legal_acceptance_required"] is True
        assert body["access_token"], "a refresh that reports acceptance must still refresh"
    finally:
        legal_acceptance.DOCUMENTS.clear()
        legal_acceptance.DOCUMENTS.update(original)


# ---------------------------------------------------------------------------
# Cross-platform: §10
# ---------------------------------------------------------------------------


def test_accepting_on_the_web_makes_the_ios_login_ordinary(client):
    """One ledger, read by both. The web form's own write is the input here --
    nothing in this test knows how the mobile endpoint stores anything."""

    user_id, email = _member(source=None)
    assert _login(client, email).status_code == 403

    conn = db_service.connect()
    try:
        legal_acceptance.record(conn.cursor(), user_id, source="web_login")
        conn.commit()
    finally:
        conn.close()

    assert _login(client, email).status_code == 200
    assert _row_count(user_id) == len(legal_acceptance.DOCUMENTS), (
        "the mobile login wrote a second record for an acceptance that already existed"
    )


def test_accepting_on_ios_satisfies_the_web_form(client):
    """The other direction, asserted against `outstanding()` because that is the
    single predicate `/login` consults."""

    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    assert client.post(ACCEPT, json={"ticket": ticket}).status_code == 200
    assert legal_acceptance.outstanding(user_id) == []


def test_a_member_on_an_old_version_is_asked_for_the_new_one(client):
    """§9's real versioning contract, end to end and with no version in a client.

    The only thing that moved is `DOCUMENTS`. If a version had been hard-coded
    into the app or into the wire contract as an input, this could not pass.
    """

    user_id, email = _member()
    assert _login(client, email).status_code == 200

    original = dict(legal_acceptance.DOCUMENTS)
    legal_acceptance.DOCUMENTS["terms"] = NEXT_TERMS
    try:
        refused = _login(client, email)
        assert refused.status_code == 403
        challenge = refused.get_json()["legal_acceptance"]
        assert [entry["document"] for entry in challenge["documents"]] == ["terms"], (
            "only the revised document should be asked about"
        )
        assert client.post(ACCEPT, json={"ticket": challenge["ticket"]}).status_code == 200
        assert legal_acceptance.outstanding(user_id) == []
    finally:
        legal_acceptance.DOCUMENTS.clear()
        legal_acceptance.DOCUMENTS.update(original)
    # The old row survives. An acceptance ledger that rewrote history would not
    # be able to answer which text a member agreed to and when.
    versions = {entry["document_version"] for entry in legal_acceptance.accepted(user_id)}
    assert {original["terms"], NEXT_TERMS} <= versions


# ---------------------------------------------------------------------------
# Security: §13, §22
# ---------------------------------------------------------------------------


def test_a_ticket_cannot_be_edited_to_name_another_account(client):
    """IDOR, and the reason the account is signed rather than sent.

    `user_id` appears nowhere in the request body. The only statement of who is
    accepting is inside the signature, so substituting an account means forging
    one.
    """

    victim_id, _victim_email = _member(source=None)
    _attacker_id, attacker_email = _member(source=None)
    ticket = _login(client, attacker_email).get_json()["legal_acceptance"]["ticket"]

    # The obvious attempt: name the victim alongside a ticket that is genuinely
    # signed, for the attacker.
    response = client.post(ACCEPT, json={"ticket": ticket, "user_id": victim_id, "uid": victim_id})
    assert response.status_code == 200
    assert _row_count(victim_id) == 0, "an acceptance was written for an account that did not ask"


def test_a_forged_ticket_is_refused(client):
    user_id, _email = _member(source=None)
    good, _expires = bot.mobile_legal_acceptance_ticket(user_id, legal_acceptance.versions_in_force())
    body, signature = good.rsplit(".", 1)
    forged = f"{body}.{'0' * len(signature)}"
    assert client.post(ACCEPT, json={"ticket": forged}).status_code == 401
    assert _row_count(user_id) == 0


def test_an_access_token_is_not_a_ticket_and_a_ticket_is_not_an_access_token(client):
    """Both directions of the confusion that sharing a key invites.

    An access token carries no purpose claim, so it cannot be read as a ticket.
    A ticket creates no `mobile_security_sessions` row, so it cannot authenticate
    as a bearer -- which is what makes one derived key safe here rather than a
    sixth key family nobody would have a second reason to add.
    """

    user_id, email = _member()
    access_token = _login(client, email).get_json()["access_token"]
    assert bot.read_mobile_legal_acceptance_ticket(access_token) == {}

    ticket, _expires = bot.mobile_legal_acceptance_ticket(user_id, legal_acceptance.versions_in_force())
    # A *fresh* client, because the sign-in above also left a cookie session on
    # `client` -- which would authenticate the probe and make this pass for a
    # reason that has nothing to do with the ticket.
    probe = bot.webhook_app.test_client().get(SESSION, headers=_bearer(ticket))
    assert probe.get_json()["authenticated"] is False


def test_the_endpoint_refuses_a_caller_with_neither_credential(client):
    assert client.post(ACCEPT, json={}).status_code == 401
    assert client.post(ACCEPT, json={"ticket": "nonsense"}).status_code == 401


def test_a_cookie_session_cannot_accept_on_its_own(client):
    """The CSRF answer, asserted rather than argued.

    A cross-site request can carry the member's cookie but not their bearer
    token. An acceptance a member never made grants an attacker nothing and
    still corrupts the one record this whole mission exists to produce, so the
    cookie is not accepted here even though it is accepted nearly everywhere
    else in the app.
    """

    user_id, _email = _member(source=None)
    with client.session_transaction() as flask_session:
        flask_session["account_user_id"] = user_id
    response = client.post(ACCEPT, json={})
    assert response.status_code == 401
    assert _row_count(user_id) == 0


def test_a_ticket_does_not_outlive_the_restriction_it_predates(client):
    """§13. The ticket is minted after the password and before admission, so a
    restriction arriving in that window has to still bite."""

    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    _sql("UPDATE users SET login_enabled=0 WHERE user_id=?", (user_id,))
    response = client.post(ACCEPT, json={"ticket": ticket})
    assert response.status_code == 403
    assert response.get_json()["error_code"] == "account_restricted"
    assert _row_count(user_id) == 0


def test_a_ticket_does_not_bypass_email_confirmation(client):
    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    _sql("UPDATE users SET email_verified=0 WHERE user_id=?", (user_id,))
    response = client.post(ACCEPT, json={"ticket": ticket})
    assert response.status_code == 403
    assert response.get_json()["error_code"] == "email_not_confirmed"
    assert _row_count(user_id) == 0


def test_a_client_cannot_name_the_version_it_is_accepting(client):
    """§5 and §28. The version is read from `DOCUMENTS` by `record()` itself, so
    there is no field here through which an older one could be offered."""

    user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    response = client.post(
        ACCEPT,
        json={
            "ticket": ticket,
            "document_version": "PULSESOC_TERMS_2020_01",
            "version": "PULSESOC_TERMS_2020_01",
            "versions": {"terms": "PULSESOC_TERMS_2020_01"},
            "accepted_at": "1999-01-01T00:00:00.000000Z",
            "source": "web_signup",
        },
        headers=_bearer("nonsense"),
    )
    assert response.status_code == 200
    on_file = {entry["document"]: entry for entry in legal_acceptance.accepted(user_id)}
    assert on_file["terms"]["document_version"] == legal_acceptance.DOCUMENTS["terms"]
    assert on_file["terms"]["acceptance_source"] == "mobile_login"
    assert not on_file["terms"]["accepted_at"].startswith("1999")


# ---------------------------------------------------------------------------
# Privacy: §23, §24
# ---------------------------------------------------------------------------


def test_the_wire_contract_carries_no_acceptance_history(client):
    """An acceptance screen asks what is outstanding now. When a member last
    agreed, from which surface, and what else is on file are account data and
    answer a question nothing on this screen is asking."""

    _user_id, email = _member(source=None)
    challenge = _login(client, email).get_json()["legal_acceptance"]
    for entry in challenge["documents"]:
        assert set(entry) == {"document", "version", "title", "path"}
    assert set(challenge) == {"ticket", "ttl_seconds", "expires_at", "documents", "accept_url"}


def test_the_documents_link_to_the_canonical_pages(client):
    """§18. The app bundles its own copy of this text under a different date, so
    an acceptance step that rendered the bundle would record agreement to the
    version in force while showing the version before it."""

    _user_id, email = _member(source=None)
    challenge = _login(client, email).get_json()["legal_acceptance"]
    paths = {entry["document"]: entry["path"] for entry in challenge["documents"]}
    assert paths == {"terms": "/terms", "privacy": "/privacy"}
    for path in paths.values():
        assert client.get(path).status_code == 200, f"{path} is not readable before admission"


def test_a_required_acceptance_is_friction_not_an_attack(client):
    """§24. Every event this mission emits has to be classified, and classifying
    an ordinary Terms revision as security would make each one look like a
    coordinated attack on the entire install base."""

    for name in ("mobile_login_legal_acceptance_required", "mobile_legal_acceptance_stale"):
        assert bot.AUTH_EVENT_CLASS[name] == "friction", name
    for name in ("mobile_legal_acceptance_recorded", "mobile_legal_acceptance_noop"):
        assert bot.AUTH_EVENT_CLASS[name] == "neutral", name
    # Reaching the endpoint with no credential at all is not something a working
    # client does, so that one is security.
    assert bot.AUTH_EVENT_CLASS["mobile_legal_acceptance_unauthorised"] == "security"


def test_no_event_detail_carries_a_document_or_a_credential(client):
    """The events exist to count acceptances, not to archive them."""

    _user_id, email = _member(source=None)
    ticket = _login(client, email).get_json()["legal_acceptance"]["ticket"]
    client.post(ACCEPT, json={"ticket": ticket})
    conn = db_service.connect()
    try:
        rows = conn.execute(
            "SELECT details FROM auth_events WHERE event_type LIKE '%legal_acceptance%'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "the acceptance emitted no events at all"
    for row in rows:
        details = (db_service.row_values(row)[0] or "").lower()
        assert PASSWORD not in details
        assert ticket.lower() not in details
        for version in legal_acceptance.DOCUMENTS.values():
            assert version.lower() not in details


# ---------------------------------------------------------------------------
# Backward compatibility: §26
# ---------------------------------------------------------------------------


def test_the_refusal_is_readable_by_a_build_that_predates_it(client):
    """A shipped build renders the server's sentence verbatim for a 401/403
    carrying a code it does not recognise. For those members this response *is*
    the acceptance screen, so it has to name somewhere they can actually agree --
    and accepting there satisfies this gate, which is what makes an old client
    recoverable without being handed a bypass.
    """

    _user_id, email = _member(source=None)
    body = _login(client, email).get_json()
    message = body["message"]
    assert bot.CANONICAL_HTTPS_ORIGIN in message
    assert "/login" in message
    assert "Terms of Service" in message and "Privacy Policy" in message
    assert body["legal_acceptance"]["accept_url"] == f"{bot.CANONICAL_HTTPS_ORIGIN}/login"
    # No version string and no internal identifier in the prose a member reads.
    for version in legal_acceptance.DOCUMENTS.values():
        assert version not in message


# ---------------------------------------------------------------------------
# Mutations: §28. Each one is a way to get this wrong that the tests above,
# on their own, would not notice.
# ---------------------------------------------------------------------------


def test_mutation_a_login_that_skips_outstanding_is_caught(client, monkeypatch):
    """The headline defect, restored. This is iOS before this mission."""

    _user_id, email = _member(source=None)
    monkeypatch.setattr(legal_acceptance, "outstanding", lambda *a, **k: [])
    monkeypatch.setattr(legal_acceptance, "pending", lambda *a, **k: [])
    assert _login(client, email).status_code == 200, (
        "fixture error: the mutation did not take"
    )
    monkeypatch.undo()
    assert _login(client, email).status_code == 403, (
        "the login gate does not consult legal_acceptance.outstanding()"
    )


def test_mutation_an_old_version_counting_as_current_is_caught(client):
    """`outstanding()` compares versions, not presence. A boolean column -- or a
    membership test on documents rather than on versions -- passes every test
    that does not revise a document."""

    user_id, _email = _member()
    assert legal_acceptance.outstanding(user_id) == []
    original = dict(legal_acceptance.DOCUMENTS)
    legal_acceptance.DOCUMENTS["terms"] = NEXT_TERMS
    try:
        assert legal_acceptance.outstanding(user_id) == ["terms"], (
            "an acceptance of a superseded version is being read as current"
        )
    finally:
        legal_acceptance.DOCUMENTS.clear()
        legal_acceptance.DOCUMENTS.update(original)


def test_mutation_a_client_chosen_version_is_caught(client):
    """Proves the assertion in `test_a_client_cannot_name_the_version` has teeth
    by showing the ledger refuses the value even when handed it directly."""

    user_id, _email = _member(source=None)
    conn = db_service.connect()
    try:
        with pytest.raises(TypeError):
            legal_acceptance.record(conn.cursor(), user_id, source="mobile_login", version="whatever")
    finally:
        conn.close()
    assert _row_count(user_id) == 0


def test_mutation_an_unauthorised_write_is_caught():
    """`record()` refuses to run without an account. A row with no owner is not
    a weaker record, it is an unattributable one."""

    conn = db_service.connect()
    try:
        with pytest.raises(ValueError):
            legal_acceptance.record(conn.cursor(), 0, source="mobile_login")
        with pytest.raises(ValueError):
            legal_acceptance.record(conn.cursor(), None, source="mobile_login")
    finally:
        conn.close()


def test_mutation_a_partial_session_with_full_access_is_caught(client):
    """§14. The gated response carries no credential, so there is nothing to
    replay -- asserted by trying every field it does return."""

    _user_id, email = _member(source=None)
    body = _login(client, email).get_json()
    for field in ("access_token", "refresh_token", "session_token", "token"):
        candidate = body.get(field)
        assert not candidate, f"a gated login returned {field}"
    probe = client.get(SESSION, headers=_bearer(body["legal_acceptance"]["ticket"]))
    assert probe.get_json()["authenticated"] is False, (
        "the acceptance ticket authenticates as a session token"
    )


def test_mutation_two_authorities_is_caught(client):
    """§5 and §28's last item. Both surfaces must write the same rows in the same
    table -- a parallel `ios_terms_accepted` column would pass every behavioural
    test in this file while splitting the record in two."""

    web_id, _web_email = _member(source="web_signup")
    ios_id, ios_email = _member(source=None)
    ticket = _login(client, ios_email).get_json()["legal_acceptance"]["ticket"]
    client.post(ACCEPT, json={"ticket": ticket})

    conn = db_service.connect()
    try:
        rows = conn.execute(
            "SELECT user_id, document, document_version FROM user_legal_acceptances "
            "WHERE user_id IN (?,?) ORDER BY user_id, document",
            (web_id, ios_id),
        ).fetchall()
    finally:
        conn.close()
    recorded = {(values[0], values[1], values[2]) for values in (db_service.row_values(row) for row in rows)}
    for user_id in (web_id, ios_id):
        for document, version in legal_acceptance.DOCUMENTS.items():
            assert (user_id, document, version) in recorded, (
                f"{user_id} is not on file at {document}={version} in the shared ledger"
            )
    assert "mobile_login" in legal_acceptance.SOURCES


def test_mutation_a_restored_session_ignoring_a_new_version_is_caught(client):
    """The §12 failure: gate the password and nothing else, and a member who
    never signs out is never asked again."""

    _user_id, email = _member()
    token = _login(client, email).get_json()["access_token"]
    original = dict(legal_acceptance.DOCUMENTS)
    legal_acceptance.DOCUMENTS["terms"] = NEXT_TERMS
    try:
        body = client.get(SESSION, headers=_bearer(token)).get_json()
        assert body.get("legal_acceptance_required") is True, (
            "a restored session is never told a new version is required"
        )
    finally:
        legal_acceptance.DOCUMENTS.clear()
        legal_acceptance.DOCUMENTS.update(original)
