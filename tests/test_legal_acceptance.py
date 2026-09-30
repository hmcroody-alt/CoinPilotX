"""An account may not exist without a record of what it agreed to.

Signup has always required the member to agree to the Terms, the Privacy Policy
and the no-tolerance rules, refused to create the account without it, and then
discarded the answer -- so no member had ever accepted anything, as far as this
platform could show. ``services/legal_acceptance.py`` records it. These are the
assertions that make the record worth having:

* The stored version has to name the document the member actually read. A
  constant in a Python file can drift from the page that renders; the pin below
  fails when it does, and without it the column records which string was in
  ``legal_acceptance.py``, not which text was on screen.
* Every caller of ``create_account`` has to say what was agreed. One of the three
  must answer "nothing" -- ``/admin/users/new`` creates an account for someone who
  was never shown the documents -- so the parameter is keyword-only with no
  default and a fourth caller that forgets it raises rather than recording a
  consent nobody gave.
* ``outstanding()`` has to go red for everybody when a document is revised. That
  is the entire reason a version is stored instead of a boolean, and a version
  column nothing reads is a version column that proves nothing.

Run: python3 -m pytest tests/test_legal_acceptance.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="legal_acceptance_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import db as db_service  # noqa: E402
from services import legal_acceptance  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def schema():
    bot.init_db()


@pytest.fixture
def client():
    return bot.webhook_app.test_client()


def _visible(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _new_member(source="web_signup"):
    user, error = bot.create_account(
        "Acceptance Case",
        f"acceptance-{os.urandom(6).hex()}@example.com",
        "correct horse battery staple",
        username=f"acc{os.urandom(4).hex()}",
        age_confirmed=True,
        accepted_terms_source=source,
    )
    assert not error, f"signup failed: {error}"
    return user["user_id"]


# ---------------------------------------------------------------------------
# The pin: a stored version has to mean the text the member saw
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("document,path", [("terms", "/terms"), ("privacy", "/privacy")])
def test_each_version_agrees_with_the_date_the_page_publishes(client, document, path):
    """Revising a document without bumping its constant fails here.

    That is the whole contract. Every acceptance row already on file claims the
    member read the version named in it; if the page changes underneath and the
    constant does not, those rows become assertions about text nobody ever saw and
    nothing anywhere would notice.
    """

    published = _visible(client.get(path).get_data(as_text=True))
    assert f"Last updated: {legal_acceptance.STATED_LAST_UPDATED}" in published, (
        f"{path} no longer says 'Last updated: {legal_acceptance.STATED_LAST_UPDATED}'. "
        f"If it was revised, bump legal_acceptance.DOCUMENTS[{document!r}] -- which "
        "will correctly make every member outstanding again."
    )
    assert legal_acceptance.DOCUMENTS[document].endswith("2026_05"), (
        f"{document} version {legal_acceptance.DOCUMENTS[document]!r} no longer "
        f"matches the date {path} publishes"
    )


def test_the_unversioned_document_really_has_no_version_to_record(client):
    """Justifies the omission rather than letting it be an oversight.

    The signup checkbox names the no-tolerance rules alongside the Terms and the
    Privacy Policy, but `/community-rules` publishes no revision date, so there is
    nothing to record and nothing to detect a rewrite against. If a date appears
    there, this fails and the document joins DOCUMENTS -- which is the fix.
    """

    assert "community_rules" in legal_acceptance.UNVERSIONED_DOCUMENTS
    published = _visible(client.get("/community-rules").get_data(as_text=True))
    assert "Last updated" not in published, (
        "/community-rules now publishes a revision date. Add it to "
        "legal_acceptance.DOCUMENTS so acceptance of it can be recorded."
    )


# ---------------------------------------------------------------------------
# Default-deny: no caller may create an account without answering
# ---------------------------------------------------------------------------


def test_create_account_refuses_to_run_without_being_told_what_was_agreed():
    """A fourth caller cannot inherit silence.

    Keyword-only and no default, so the failure is a TypeError at the call site
    rather than an account quietly created with no record -- which is precisely
    the defect being closed, and would otherwise reappear the first time somebody
    adds another signup path.
    """

    with pytest.raises(TypeError):
        bot.create_account("No Consent", "no-consent@example.com", "correct horse battery staple")


def test_an_unrecognised_source_is_refused():
    conn = db_service.connect()
    try:
        with pytest.raises(ValueError):
            legal_acceptance.record(conn.cursor(), 1, source="somewhere")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# What is actually stored
# ---------------------------------------------------------------------------


def test_a_signup_leaves_every_current_document_on_file():
    user_id = _new_member()
    on_file = {entry["document"]: entry for entry in legal_acceptance.accepted(user_id)}
    assert set(on_file) == set(legal_acceptance.DOCUMENTS)
    for document, version in legal_acceptance.DOCUMENTS.items():
        assert on_file[document]["document_version"] == version
        assert on_file[document]["acceptance_source"] == "web_signup"
        assert on_file[document]["accepted_at"]
    assert legal_acceptance.outstanding(user_id) == []


def test_an_account_an_admin_created_has_accepted_nothing():
    """`/admin/users/new` never shows the documents, so the record must be empty.

    Recording an acceptance here would be the worst available outcome: it would
    look exactly like a member who agreed, and it is the reason the parameter
    exists rather than a default.
    """

    user, error = bot.create_account(
        "Admin Made",
        f"admin-made-{os.urandom(6).hex()}@example.com",
        "correct horse battery staple",
        username=f"adm{os.urandom(4).hex()}",
        accepted_terms_source=None,
    )
    assert not error, f"admin signup failed: {error}"
    assert legal_acceptance.accepted(user["user_id"]) == []
    assert legal_acceptance.outstanding(user["user_id"]) == sorted(legal_acceptance.DOCUMENTS)


def test_accepting_the_same_version_twice_is_not_a_second_row():
    """The login form asks on every sign-in. Without this, a member who signs in
    daily accumulates a row a day and the record stops being readable."""

    user_id = _new_member()
    before = legal_acceptance.accepted(user_id)
    conn = db_service.connect()
    try:
        legal_acceptance.record(conn.cursor(), user_id, source="web_login")
        conn.commit()
    finally:
        conn.close()
    assert legal_acceptance.accepted(user_id) == before


def test_revising_a_document_makes_every_member_outstanding_again(monkeypatch):
    """The reason the column holds a version and not a yes.

    A member fully on file today has to become outstanding the moment the Terms
    are rewritten, because their consent was to the old text. Asserted through a
    bumped version rather than by editing the document, which is the same input
    `outstanding()` sees.
    """

    user_id = _new_member()
    assert legal_acceptance.outstanding(user_id) == []
    monkeypatch.setitem(legal_acceptance.DOCUMENTS, "terms", "PULSESOC_TERMS_2027_01")
    assert legal_acceptance.outstanding(user_id) == ["terms"]


def test_the_stored_source_says_which_surface_asked():
    """Provenance, because the three surfaces did not ask the same question.

    Web signup has two separate checkboxes; the iPhone app has one reading "I'm
    16+ and agree to the ...". A reviewer asking what a given member was shown can
    only answer it from this column.
    """

    user_id = _new_member(source="mobile_register")
    sources = {entry["acceptance_source"] for entry in legal_acceptance.accepted(user_id)}
    assert sources == {"mobile_register"}
    assert set(legal_acceptance.SOURCES) == {"web_signup", "web_login", "mobile_register"}
