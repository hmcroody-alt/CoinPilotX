"""A privacy control that does not show its own state cannot be used.

`/privacy-center` persists four choices into `privacy_preferences` on POST. On GET
it rendered the form with literal `checked` attributes and never queried the table
it had just written. Two consequences, and the second is worse:

* An HTML checkbox sends nothing when unchecked. A member who opted out of
  analytics came back to an unticked box, so the next save of *any* control on that
  form wrote their opt-out back to 0. The form could not be submitted without
  silently discarding a decision already made.
* `personalized_ads_opt_out` is the one of the four that is enforced --
  `pulse_ads_service.user_personalized_ads_opt_out` reads it on the ad path. A
  member who had consented to personalization was shown "Opt out of personalized
  ads" ticked, so the page asserted the opposite of what was in force. Telling
  someone they are protected when they are not is a worse failure than telling them
  nothing.

The assertions are therefore about agreement between three things: what the form
shows, what the table holds, and -- for the one enforced control -- what the ad
server reads. A test that only round-tripped the form would pass against a page
that stores a preference nothing acts on.

The defaults for an unsaved member are pinned to the enforcement default rather
than to a literal, because a page showing "you are opted out" while the ad server
reads "opted in" is the same defect as before, pointing the other way.

Run: python3 -m pytest tests/test_privacy_center_controls.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="privacy_center_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import pulse_ads_service  # noqa: E402

PATH = "/privacy-center"

#: Every control the form offers, in render order.
NAMES = [name for name, _default, _label in bot.PRIVACY_CENTER_CONTROLS]


@pytest.fixture(scope="module", autouse=True)
def schema():
    bot.init_db()
    bot.webhook_app.config["SECRET_KEY"] = "privacy-center-tests"


@pytest.fixture
def member():
    """A fresh signed-in member with no saved preferences.

    Fresh per test: these assertions are about the transition from "never saved"
    to "saved", and a member carried between tests would already have a row.
    """

    with bot.webhook_app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        suffix = os.urandom(4).hex()
        cur.execute(
            "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
            (f"pc{suffix}", f"pc-{suffix}@example.com", "Privacy Case"),
        )
        conn.commit()
        user_id = cur.lastrowid
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client, user_id


def _checked(html: str) -> dict[str, bool]:
    """Which of the four boxes the page rendered ticked.

    Parsed from the rendered input tags rather than by searching for the word
    "checked" anywhere, so the `checked` on one control cannot satisfy an
    assertion about another.
    """

    state = {}
    for match in re.finditer(r"<input\b[^>]*>", html):
        tag = match.group(0)
        name = re.search(r"name='([^']+)'", tag)
        if name and name.group(1) in NAMES:
            state[name.group(1)] = " checked" in tag
    assert set(state) == set(NAMES), f"form rendered {sorted(state)}, expected {sorted(NAMES)}"
    return state


def _save(client, **submitted):
    """Submit the form with exactly the named boxes ticked.

    Anything omitted is genuinely absent from the POST body, which is what a
    browser sends for an unticked box -- and the mechanism by which the old page
    erased preferences.
    """

    response = client.post(PATH, data={name: "on" for name in submitted if submitted[name]})
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _ads_opt_out(user_id) -> bool:
    """What the ad path itself concludes for this member."""

    conn = bot.db()
    try:
        return pulse_ads_service.user_personalized_ads_opt_out(conn, user_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The defect: a saved choice has to survive being looked at
# ---------------------------------------------------------------------------


def test_a_saved_opt_out_is_still_ticked_when_the_member_returns(member):
    client, _user_id = member
    _save(client, analytics_opt_out=True)
    assert _checked(client.get(PATH).get_data(as_text=True))["analytics_opt_out"] is True


def test_saving_one_control_does_not_erase_another(member):
    """The failure a member would actually hit.

    They opt out of analytics, come back later to change something unrelated, and
    submit the form as rendered. With hardcoded `checked` attributes the analytics
    box arrived unticked, so that second save revoked the opt-out -- without any
    interaction with it, and without saying so.
    """

    client, _user_id = member
    chosen = {"analytics_opt_out": True, "personalized_ads_opt_out": True,
              "public_profile": False, "creator_visibility": False}
    _save(client, **chosen)

    # Re-submit the form exactly as the page renders it, touching nothing.
    _save(client, **_checked(client.get(PATH).get_data(as_text=True)))

    # Compared against what the member chose, not against what the page rendered
    # a moment ago. Render-to-render is stable even when the render is a constant,
    # so that comparison would hold while every choice was being thrown away.
    after = _checked(client.get(PATH).get_data(as_text=True))
    assert {name: after[name] for name in chosen} == chosen


def test_every_control_round_trips_in_both_directions(member):
    """Each box, on and off, one at a time.

    Every control individually, because a page that reads the table for one field
    and hardcodes the other three would pass a test that only ever set one of
    them.
    """

    client, _user_id = member
    for name in NAMES:
        for wanted in (True, False):
            _save(client, **{name: wanted})
            shown = _checked(client.get(PATH).get_data(as_text=True))
            assert shown[name] is wanted, f"{name} saved as {wanted} but rendered as {shown[name]}"


# ---------------------------------------------------------------------------
# Agreement with the one control that is enforced
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("consented", [True, False])
def test_the_ads_box_shows_what_the_ad_server_reads(member, consented):
    """The page and the ad path must not disagree about personalization.

    `user_personalized_ads_opt_out` is what actually decides whether a member is
    personalized. Asserting against it rather than against the stored column means
    this stays true if the ad path ever changes how it reads the preference -- and
    catches the original defect from the other side, where the page claimed an
    opt-out that was not in force.
    """

    client, user_id = member
    _save(client, personalized_ads_opt_out=not consented)
    shown = _checked(client.get(PATH).get_data(as_text=True))["personalized_ads_opt_out"]
    assert shown is _ads_opt_out(user_id)


def test_a_member_who_never_saved_is_shown_the_state_that_is_enforced(member):
    """No row yet, so the page has to show what the absence means.

    `user_personalized_ads_opt_out` treats a missing row as opted out. A page
    defaulting that box to unticked would tell a member they are being personalized
    while the ad server excludes them, which is the same class of lie as the
    original bug.
    """

    client, user_id = member
    shown = _checked(client.get(PATH).get_data(as_text=True))
    assert shown["personalized_ads_opt_out"] is _ads_opt_out(user_id)

    # And the rest match the column defaults, so an unsaved member and a member
    # who saved those same values read identically.
    for name, default, _label in bot.PRIVACY_CENTER_CONTROLS:
        assert shown[name] is bool(default), f"{name} defaults to {shown[name]}, column says {default}"


def test_the_form_offers_exactly_the_controls_the_post_handler_accepts(member):
    """A fifth checkbox the POST ignores would be a control that does nothing.

    Read out of the handler's own source rather than restated, so adding a box to
    the form without teaching the write about it fails here.
    """

    client, _user_id = member
    rendered = set(_checked(client.get(PATH).get_data(as_text=True)))
    source = bot.privacy_center_page.__wrapped__ if hasattr(bot.privacy_center_page, "__wrapped__") else bot.privacy_center_page
    import inspect

    body = inspect.getsource(source)
    written = set(re.findall(r'request\.form\.get\("(\w+)"\)', body))
    assert rendered == written, (
        f"the form renders {sorted(rendered)} but the POST reads {sorted(written)}; "
        "a box the handler ignores is a control that silently does nothing"
    )
