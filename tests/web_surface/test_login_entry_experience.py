"""The web sign-in page asks for credentials, and asks about the Terms once.

Two things are pinned here, and they are the same change seen from either end.

**The page.** `/login` used to render five account-management inputs inline --
resend-confirmation, the current unconfirmed address, a new address, and the
account password needed to swap them -- above the two fields anyone actually
came for. They are recovery tools for a state most visitors are not in. They now
live behind one `<details>` disclosure, so the default view is email, password,
sign in. The assertions below are about *position*, not existence: removing the
flows would be a regression, and so would putting them back in front.

**The checkbox.** The form also demanded a tick agreeing to the Terms, the
Privacy Policy and the no-tolerance rules on *every* sign-in, and then threw the
answer away -- `terms_accepted` was read, compared, and never written anywhere.
So it recorded nothing while asking everyone forever, which is the worst of both:
real friction, zero evidence. `services/legal_acceptance.py` already stores
acceptance per `(member, document, version)`, and signup already writes it.

Login now writes it too, and asks only the member whose row is missing at the
version now in force (`legal_acceptance.outstanding()`). For nearly everyone that
is silent -- strictly less friction than today. For a member who predates the
table, or who last agreed to superseded text, it is a one-time step that produces
an actual record. A rewrite of the Terms bumps the constant in that module and the
question comes back by itself, which is the versioned flow the generic per-session
tick was standing in for.

The interstitial is the part worth testing hard, because it is a pause in the
middle of an authentication. It sits *after* the password and after the email
confirmation check, so it never tells a stranger that an address has an account.
Its session marker is deliberately not `account_user_id` -- `require_account()`
cannot see it, so the half-finished state authorises nothing -- and the second
POST carries no credentials, so everything it may do has to come from that
marker.

Run: python3 -m pytest tests/web_surface/test_login_entry_experience.py
"""

import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="login_entry_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
# Without a stable key the session cannot be read back after a redirect.
os.environ.setdefault("FLASK_SECRET_KEY", "login-entry-experience-tests")

import bot  # noqa: E402
from services import cache_engine, pulse_security_core  # noqa: E402
from services import db as db_service  # noqa: E402
from services import legal_acceptance  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS,
# which would turn every assertion below into a redirect body.
HTTPS = {"X-Forwarded-Proto": "https"}
PASSWORD = "EntryExperience!123"
CSRF = "login-entry-csrf-token"

PRODUCT = "/pulse/marketplace/product/42"

#: The five inputs §13 of the brief names. Asserted by `id` because that is what
#: the labels point at, and a label whose `for` goes nowhere is its own defect.
RECOVERY_FIELD_IDS = (
    "resend-email",
    "change-old-email",
    "change-new-email",
    "change-password",
)

DETAILS_BLOCK = re.compile(
    r"<details class=\"auth-help\">(.*?)</details>", re.DOTALL
)


def reset_limiters():
    """Clear all three per-process gates.

    None of them is per-test. Without this the file fails as a whole while every
    test in it passes alone: `login_security_preflight` counts recent
    `auth_events` rows by IP and email, the `pulse_security_core` middleware
    allows ten requests per five minutes per path, and `basic_abuse_guard` holds
    its own dict with twelve `/login` POSTs per five minutes.
    """

    conn = db_service.connect()
    cur = conn.cursor()
    for table in ("auth_events", "failed_login_controls", "failed_login_safe_list"):
        try:
            cur.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()
    pulse_security_core._RATE_BUCKETS.clear()
    cache_engine._MEMORY.clear()
    bot.RATE_LIMIT_BUCKETS.clear()


@pytest.fixture(autouse=True)
def clean_limiters():
    reset_limiters()
    yield


@pytest.fixture
def client():
    bot.webhook_app.config["TESTING"] = True
    test_client = bot.webhook_app.test_client()
    with test_client.session_transaction() as sess:
        sess["csrf_token"] = CSRF
    return test_client


def make_user(*, confirmed=True, accepted=False):
    """A member, optionally already on file at the current document versions."""

    email = f"entry-{secrets.token_hex(6)}@example.com"
    now = bot.datetime.now().isoformat()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified,
         account_status, login_enabled, access_enabled, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', 1, 1, ?, ?, ?)
        """,
        (
            f"entry_{secrets.token_hex(4)}",
            "Entry Experience",
            "Entry Experience",
            email,
            bot.generate_password_hash(PASSWORD),
            1 if confirmed else 0,
            now,
            now,
            now,
        ),
    )
    user_id = cur.lastrowid
    if accepted:
        legal_acceptance.record(cur, user_id, source="web_signup")
    conn.commit()
    conn.close()
    return email, user_id


def login_post(client, email, password, **extra):
    """POST the real form. Nothing is sent that the browser would not send.

    In particular no `terms_accepted`: the rebuilt form has no such checkbox, so
    a test that supplied one would be exercising a field no visitor can produce
    and would pass against the old blanket gate too.
    """

    data = {"csrf_token": CSRF, "email": email, "password": password}
    data.update(extra)
    return client.post("/login", headers=HTTPS, data=data)


def signed_in_user_id(client):
    with client.session_transaction() as sess:
        return sess.get("account_user_id") or 0


def is_redirect(response):
    return response.status_code in (301, 302, 303, 307, 308)


def login_page(client, **params):
    query = ""
    if params:
        from urllib.parse import urlencode

        query = "?" + urlencode(params)
    response = client.get(f"/login{query}", headers=HTTPS)
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# The default view is a sign-in form (§12, §13, §14)
# ---------------------------------------------------------------------------


def test_the_page_leads_with_email_and_password(client):
    html = login_page(client)
    assert 'id="login-email"' in html
    assert 'id="login-password"' in html
    assert 'name="preferred_language"' in html, (
        "the language control writes to this field; without it a member who "
        "picks Kreyol here is still English to the server on the next page"
    )


@pytest.mark.parametrize("field_id", RECOVERY_FIELD_IDS)
def test_every_recovery_field_sits_behind_the_disclosure(client, field_id):
    html = login_page(client)
    marker = f'id="{field_id}"'
    assert marker in html, f"{field_id} disappeared; these flows must still exist"

    block = DETAILS_BLOCK.search(html)
    assert block, "the verification-help disclosure is gone"
    assert marker in block.group(1), (
        f"{field_id} is rendered outside <details class=\"auth-help\">, which "
        "puts account-management clutter back in front of the sign-in form"
    )


def test_the_disclosure_is_a_native_details_not_a_script(client):
    # A JS-driven accordion would hide these fields from anyone whose script
    # failed, which for a recovery flow is the worst possible audience.
    html = login_page(client)
    assert "<details class=\"auth-help\">" in html
    assert "<summary" in DETAILS_BLOCK.search(html).group(1) or "<summary" in html


def test_the_help_entry_point_is_offered_in_words(client):
    assert "trouble verifying your email" in login_page(client).lower()


def test_no_social_sign_in_buttons_are_displayed(client):
    # The mockup shows Apple and Google. No backend token validation, account
    # linking or private-relay handling exists, so shipping the buttons would
    # ship a dead end. Asserted rather than merely omitted, so that adding them
    # requires deleting a test that says why.
    html = login_page(client).lower()
    for claim in ("continue with apple", "continue with google", "sign in with apple",
                  "sign in with google"):
        assert claim not in html, f"{claim!r} is offered but no such flow is implemented"


def test_every_footer_link_goes_somewhere_that_exists(client):
    # The panel footer is the one place this page sends a visitor who cannot get
    # in, so a 404 there is worse than no link. Resolved against the app's own
    # url_map rather than eyeballed, because /contact and /help are registered
    # outside bot.py and a grep for their decorators finds nothing.
    footer = re.search(r"<p class=\"auth-legal\">(.*?)</p>", login_page(client), re.DOTALL)
    assert footer, "the login panel lost its legal and help footer"
    hrefs = re.findall(r"href=\"([^\"]+)\"", footer.group(1))
    assert set(hrefs) == {"/terms", "/privacy", "/help", "/contact"}, hrefs
    registered = {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}
    for href in hrefs:
        assert href in registered, f"the login footer links {href}, which is not a route"


# ---------------------------------------------------------------------------
# The ordinary sign-in no longer demands a tick (§18)
# ---------------------------------------------------------------------------


def test_a_member_already_on_file_signs_in_with_no_checkbox(client):
    email, user_id = make_user(accepted=True)
    response = login_post(client, email, PASSWORD)
    assert is_redirect(response), response.get_data(as_text=True)[:400]
    assert signed_in_user_id(client) == user_id


def test_the_form_no_longer_renders_a_terms_checkbox(client):
    html = login_page(client)
    assert 'name="terms_accepted"' not in html, (
        "the per-session acceptance tick is back; it recorded nothing and asked "
        "everyone forever"
    )


def test_a_valid_login_is_not_refused_for_a_missing_tick(client):
    # The defect in one line. This exact request used to be a 400 reading
    # "Agree to the Terms ... before logging in."
    email, _ = make_user(accepted=True)
    response = login_post(client, email, PASSWORD)
    assert response.status_code != 400
    assert "agree to the terms" not in response.get_data(as_text=True).lower()


def test_signing_in_records_acceptance_for_a_member_who_had_none(client):
    # The backfill. Web login was the only surface that could ever have recorded
    # this for members who predate the table, and it discarded the answer.
    email, user_id = make_user(accepted=False)
    assert legal_acceptance.outstanding(user_id), "fixture is not outstanding to begin with"
    login_post(client, email, PASSWORD, terms_accepted="on")
    assert legal_acceptance.outstanding(user_id) == []


def test_re_accepting_does_not_write_a_second_row(client):
    email, user_id = make_user(accepted=True)
    before = len(legal_acceptance.accepted(user_id))
    login_post(client, email, PASSWORD)
    after = legal_acceptance.accepted(user_id)
    assert len(after) == before
    assert {entry["document_version"] for entry in after} == set(
        legal_acceptance.DOCUMENTS.values()
    )


# ---------------------------------------------------------------------------
# The acceptance step (§19)
# ---------------------------------------------------------------------------


def test_an_outstanding_document_stops_the_sign_in_to_ask(client):
    email, user_id = make_user(accepted=False)
    response = login_post(client, email, PASSWORD)
    assert response.status_code == 200, "the question is a page, not a redirect"
    body = response.get_data(as_text=True)
    assert 'name="legal_acceptance_submit"' in body
    assert 'name="terms_accepted"' in body


def test_the_pause_authorises_nothing(client):
    # The whole safety argument for pausing mid-authentication: the marker is not
    # `account_user_id`, so `require_account()` cannot see it.
    email, user_id = make_user(accepted=False)
    login_post(client, email, PASSWORD)
    assert signed_in_user_id(client) == 0
    assert legal_acceptance.accepted(user_id) == []
    feed = client.get("/pulse", headers=HTTPS)
    assert is_redirect(feed), "a half-finished sign-in reached a members-only page"

    # The control for the line above: without it, a `/pulse` that bounced
    # everyone would make the assertion true no matter what the marker did.
    client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1", "terms_accepted": "on"},
    )
    assert signed_in_user_id(client) == user_id
    assert not is_redirect(client.get("/pulse", headers=HTTPS))


def test_accepting_completes_the_sign_in_and_records_it(client):
    email, user_id = make_user(accepted=False)
    login_post(client, email, PASSWORD)
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1", "terms_accepted": "on"},
    )
    assert is_redirect(response), response.get_data(as_text=True)[:400]
    assert signed_in_user_id(client) == user_id
    on_file = legal_acceptance.accepted(user_id)
    assert {entry["document_version"] for entry in on_file} == set(
        legal_acceptance.DOCUMENTS.values()
    )
    assert {entry["acceptance_source"] for entry in on_file} == {"web_login"}


def test_declining_at_the_step_does_not_sign_anyone_in(client):
    email, user_id = make_user(accepted=False)
    login_post(client, email, PASSWORD)
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1"},
    )
    assert response.status_code == 400
    assert signed_in_user_id(client) == 0
    assert legal_acceptance.accepted(user_id) == []
    # Still asked, rather than dropped back to a form that would look like the
    # sign-in silently failed.
    assert 'name="legal_acceptance_submit"' in response.get_data(as_text=True)


def test_the_acceptance_post_cannot_sign_anyone_in_on_its_own(client):
    # It carries no credentials at all. With no pending marker it must be inert,
    # or it is an authentication bypass.
    make_user(accepted=False)
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1", "terms_accepted": "on"},
    )
    assert response.status_code == 400
    assert signed_in_user_id(client) == 0


def test_a_wrong_password_never_reaches_the_acceptance_step(client):
    email, _ = make_user(accepted=False)
    response = login_post(client, email, "WrongPassword!987")
    body = response.get_data(as_text=True)
    assert "incorrect" in body.lower()
    assert 'name="legal_acceptance_submit"' not in body, (
        "the step would confirm to a stranger that this address has an account"
    )


def test_an_unconfirmed_email_is_still_asked_about_first(client):
    # Ordering, not preference: the unconfirmed page names a real account, so it
    # sits behind the password -- and the Terms question must not jump ahead of
    # it and leak the same thing one step earlier.
    email, _ = make_user(confirmed=False, accepted=False)
    body = login_post(client, email, PASSWORD).get_data(as_text=True).lower()
    assert "confirm your email" in body
    assert "legal_acceptance_submit" not in body


def test_a_stale_marker_is_refused_rather_than_honoured(client, monkeypatch):
    email, user_id = make_user(accepted=False)
    login_post(client, email, PASSWORD)
    monkeypatch.setattr(bot, "PENDING_LEGAL_TTL_SECONDS", -1)
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1", "terms_accepted": "on"},
    )
    assert response.status_code == 400
    assert signed_in_user_id(client) == 0


def test_a_fresh_get_abandons_a_half_finished_sign_in(client):
    email, _ = make_user(accepted=False)
    login_post(client, email, PASSWORD)
    login_page(client)
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1", "terms_accepted": "on"},
    )
    assert response.status_code == 400
    assert signed_in_user_id(client) == 0


def test_an_account_disabled_during_the_pause_is_refused(client):
    email, user_id = make_user(accepted=False)
    login_post(client, email, PASSWORD)
    conn = db_service.connect()
    conn.cursor().execute("UPDATE users SET login_enabled=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1", "terms_accepted": "on"},
    )
    assert response.status_code == 403
    assert signed_in_user_id(client) == 0


def test_the_acceptance_step_still_requires_csrf(client):
    email, _ = make_user(accepted=False)
    login_post(client, email, PASSWORD)
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"legal_acceptance_submit": "1", "terms_accepted": "on"},
    )
    assert "security check failed" in response.get_data(as_text=True).lower()
    assert signed_in_user_id(client) == 0


def test_the_destination_survives_the_acceptance_step(client):
    email, user_id = make_user(accepted=False)
    response = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "email": email, "password": PASSWORD, "next": PRODUCT},
    )
    assert response.status_code == 200
    values = re.findall(r"<input[^>]*name=\"next\"[^>]*value=\"([^\"]*)\"", response.get_data(as_text=True))
    assert set(values) == {PRODUCT}, values

    done = client.post(
        "/login",
        headers=HTTPS,
        data={"csrf_token": CSRF, "legal_acceptance_submit": "1",
              "terms_accepted": "on", "next": PRODUCT},
    )
    assert is_redirect(done)
    assert done.headers["Location"].endswith(PRODUCT), done.headers["Location"]
    assert signed_in_user_id(client) == user_id


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_the_question_is_asked_from_the_acceptance_ledger(client, monkeypatch):
    # Every assertion above about "already on file" would also pass if the
    # question were simply never asked. Forcing `outstanding()` to answer tells
    # the two apart.
    email, _ = make_user(accepted=True)
    assert is_redirect(login_post(client, email, PASSWORD))

    reset_limiters()
    other_email, _ = make_user(accepted=True)
    monkeypatch.setattr(legal_acceptance, "outstanding", lambda *a, **k: ["terms"])
    fresh = bot.webhook_app.test_client()
    with fresh.session_transaction() as sess:
        sess["csrf_token"] = CSRF
    response = login_post(fresh, other_email, PASSWORD)
    assert response.status_code == 200
    assert 'name="legal_acceptance_submit"' in response.get_data(as_text=True)


def test_mutation_a_rewritten_document_asks_everyone_again(client, monkeypatch):
    # The reason the version is stored instead of a boolean, exercised end to
    # end: bump the constant and a member who was on file a moment ago is
    # outstanding again.
    email, user_id = make_user(accepted=True)
    monkeypatch.setitem(legal_acceptance.DOCUMENTS, "terms", "PULSESOC_TERMS_2099_01")
    assert legal_acceptance.outstanding(user_id) == ["terms"]
    response = login_post(client, email, PASSWORD)
    assert response.status_code == 200
    assert 'name="legal_acceptance_submit"' in response.get_data(as_text=True)
