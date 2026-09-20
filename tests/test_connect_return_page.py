"""The page Stripe Connect onboarding actually hands the seller back to.

`tests/test_stripe_onboarding_return.py` proves the *decision* — five states
from one snapshot — against a pure function. This file proves the **route**:
that the decision is reached from a fresh Stripe read rather than from the URL,
that what the seller is told matches what was persisted, and that the handoff
into the native app is offered exactly when it will work.

Those are different failure modes and neither file can see the other's. A
correct classifier wired to a route that never calls it produces a page that is
confidently wrong; a correct route over a classifier that mislabels UNDER_REVIEW
produces a page that is wrong in a way no route test would notice.

The scenarios below are the mission's twelve, in order:

     1. live account                      -> READY, auto handoff, overview layer
     2. submitted, Stripe still deciding  -> UNDER REVIEW, auto, overview
     3. Stripe wants a document           -> MORE INFO, auto, onboarding layer
     4. Stripe is holding the account     -> MORE INFO, persisted as restricted
     5. seller closed the tab early       -> INCOMPLETE, auto, onboarding layer
     6. the link expired (refresh leg)    -> FAILED, no auto, Stripe not called
     7. no connected account on file      -> FAILED, Stripe not called
     8. Stripe retrieve raises            -> FAILED, stored status untouched
     9. not signed in                     -> redirect, Stripe not called
    10. query parameters claiming success -> ignored entirely
    11. the stale-status bug              -> onboarding_started repaired to complete
    12. nothing Stripe-ish reaches the browser

Stripe cannot be called from here — Connect is not enabled in this environment
and there is no `sk_test_` key — so `get_account_status` is patched at the
provider boundary. That makes these tests about the URL PulseSoc builds, the
page it renders and the row it writes; not about Stripe's own behaviour.

    .venv/bin/python -m pytest tests/test_connect_return_page.py
"""

import os
import re
import tempfile

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="connect_return_page_"), "test.db")

import bot  # noqa: E402
from services import payment_provider  # noqa: E402
from services import stripe_onboarding_return as sor  # noqa: E402

USER_ID = 7788
ACCOUNT_ID = "acct_1UHj4KJv8wTiG1Gc"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def stripe_says(**over):
    """A `get_account_status` result for a fully live account, overridable."""
    base = {
        "ok": True,
        "provider_account_id": ACCOUNT_ID,
        "details_submitted": True,
        "charges_enabled": True,
        "payouts_enabled": True,
        "disabled_reason": "",
        "requirements": {"currently_due": [], "past_due": []},
        "currently_due": [],
        "past_due": [],
        "capabilities": {"card_payments": "active", "transfers": "active"},
        "card_payments_capability": "active",
        "transfers_capability": "active",
        "account": {"id": ACCOUNT_ID, "details_submitted": True},
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def schema():
    bot.init_db()


@pytest.fixture
def seller_row():
    """A seller mid-onboarding: the exact shape production user 1 is stuck in.

    `onboarding_status` is the word the money path refuses, while the capability
    flags say the account is live. Writing it as the *starting* state for most
    of these tests means any test that ends with a working seller had to have
    repaired it.
    """
    conn = bot.db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM seller_payout_accounts WHERE user_id=?", (USER_ID,))
        # Both lanes, so a teacher-lane test exercises a *live* account. With
        # only a merchant row the teacher route takes the no-account path and
        # lands on FAILED, where the handoff is suppressed for a different
        # reason entirely — the test would pass while proving nothing about
        # the unresolvable-path behaviour it claims to be about.
        for seller_type in ("merchant", "teacher"):
            cur.execute(
                "INSERT INTO seller_payout_accounts"
                " (user_id, seller_type, provider, connected_account_id,"
                "  provider_account_id, onboarding_status, charges_enabled,"
                "  payouts_enabled)"
                " VALUES (?, ?, 'stripe', ?, ?, 'onboarding_started', 1, 1)",
                (USER_ID, seller_type, ACCOUNT_ID, ACCOUNT_ID),
            )
        conn.commit()
    finally:
        conn.close()
    return ACCOUNT_ID


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(
        bot, "require_account", lambda: {"user_id": USER_ID, "email": "s@x.com"})


@pytest.fixture
def stripe(monkeypatch):
    """Patch the provider and record every retrieve, so 'never called' is provable."""
    calls = []

    def recorder(account_id):
        calls.append(account_id)
        return recorder.result

    recorder.result = stripe_says()
    monkeypatch.setattr(payment_provider, "get_account_status", recorder)
    return recorder, calls


def stored_status(user_id=USER_ID, seller_type="merchant"):
    conn = bot.db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT onboarding_status FROM seller_payout_accounts"
            " WHERE user_id=? AND seller_type=?",
            (user_id, seller_type),
        )
        row = cur.fetchone()
        return str((row or [""])[0] or "")
    finally:
        conn.close()


def get(path):
    return bot.app.test_client().get(path)


def page(path):
    response = get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    return response.get_data(as_text=True)


def app_href(html):
    """The handoff URL the page offers, or "" if it offers none."""
    match = re.search(r"id='openApp' href='([^']*)'", html)
    return match.group(1) if match else ""


#: The one string on the page that belongs to the auto-handoff and nothing
#: else. `setTimeout` is not usable as a marker: the social shell schedules its
#: own toasts with it, so it is present on every page this app renders and an
#: assertion against it silently passes in both directions.
AUTO_SCRIPT = "getElementById('openApp')"


def says(state, field="headline"):
    """Copy for `state`, escaped exactly as the page escapes it.

    Taken from `_COPY` rather than retyped. Several of these lines contain an
    apostrophe, which reaches the browser as `&#39;` — a literal `"isn't"` in a
    test would not match the rendered page, and the obvious "fix" of loosening
    the assertion until it passes would stop it distinguishing the states at
    all.
    """
    return bot.html_escape(sor._COPY[state][field])


def native_handoff_possible(seller_type):
    """Whether `app_links` can resolve this lane's payouts path to a screen.

    The two seller lanes are not symmetric: the native app has a money layer at
    `pulse/merchant/payouts` and nothing at `pulse/teacher/payouts`. Asking
    `app_links` rather than hardcoding that asymmetry means these tests state a
    property — *offer the app exactly when the app can take it* — instead of
    pinning today's route table, and they stay honest on the day a teacher
    screen is added.
    """
    from services import app_links

    try:
        app_links.app_scheme_url(f"/pulse/{seller_type}/payouts")
        return True
    except app_links.AppLinkError:
        return False


# --------------------------------------------------------------------------
# A control, so the assertions below cannot be vacuous
# --------------------------------------------------------------------------

def test_the_harness_can_tell_the_states_apart(signed_in, seller_row, stripe):
    """Two different snapshots must produce two different pages.

    Without this, every assertion in this file could be passing against one
    generic page that happens to contain the words being looked for.
    """
    recorder, _ = stripe
    recorder.result = stripe_says()
    ready = page("/pulse/merchant/payouts/return")
    recorder.result = stripe_says(currently_due=["individual.id_number"])
    due = page("/pulse/merchant/payouts/return")
    assert ready != due
    assert sor._COPY[sor.RETURN_READY]["headline"] in ready
    assert sor._COPY[sor.RETURN_READY]["headline"] not in due


# --------------------------------------------------------------------------
# 1-5: the five states, as the seller sees them
# --------------------------------------------------------------------------

def test_1_a_live_account_gets_the_success_page_and_the_overview_layer(
        signed_in, seller_row, stripe):
    html = page("/pulse/merchant/payouts/return")
    assert says(sor.RETURN_READY) in html
    assert "connected successfully" in html
    assert "Open PulseSoc" in html
    # The auto-return promise and something to return *to* are one decision.
    assert sor.AUTO_RETURN_NOTE in html
    assert AUTO_SCRIPT in html
    href = app_href(html)
    assert href.startswith("pulsesoc://"), href
    assert "layer=payout_overview" in href


def test_2_an_account_under_review_is_not_told_to_finish_setting_up(
        signed_in, seller_row, stripe):
    recorder, _ = stripe
    recorder.result = stripe_says(charges_enabled=False, payouts_enabled=False)
    html = page("/pulse/merchant/payouts/return")
    assert says(sor.RETURN_UNDER_REVIEW) in html
    # The specific wrong page: this seller has nothing left to do, and the
    # INCOMPLETE copy would send them back through a flow they finished.
    assert says(sor.RETURN_INCOMPLETE) not in html
    assert sor.AUTO_RETURN_NOTE in html
    assert "layer=payout_overview" in app_href(html)


def test_3_an_outstanding_requirement_routes_to_the_setup_layer(
        signed_in, seller_row, stripe):
    recorder, _ = stripe
    recorder.result = stripe_says(
        charges_enabled=False, payouts_enabled=False,
        currently_due=["individual.id_number"])
    html = page("/pulse/merchant/payouts/return")
    assert says(sor.RETURN_MORE_INFO) in html
    # `payout_onboarding` is the layer that renders the specific next step.
    # Sending this seller to the overview would hide the work from them.
    assert "layer=payout_onboarding" in app_href(html)


def test_4_a_held_account_persists_a_status_the_money_path_refuses(
        signed_in, seller_row, stripe):
    recorder, _ = stripe
    recorder.result = stripe_says(
        charges_enabled=False, payouts_enabled=False,
        disabled_reason="requirements.past_due")
    page("/pulse/merchant/payouts/return")
    # Not `requirements_due`: that word is in no refusal set, so a held account
    # carrying it would have its transfers approved by a gate that should have
    # stopped them.
    assert stored_status() == "restricted"
    assert "restricted" in sor.MONEY_PATH_REFUSED_STATUSES


def test_5_leaving_the_flow_early_is_not_reported_as_failure(
        signed_in, seller_row, stripe):
    recorder, _ = stripe
    recorder.result = stripe_says(
        details_submitted=False, charges_enabled=False, payouts_enabled=False)
    html = page("/pulse/merchant/payouts/return")
    assert says(sor.RETURN_INCOMPLETE) in html
    assert "layer=payout_onboarding" in app_href(html)
    # Still a handoff: the app is where they resume.
    assert sor.AUTO_RETURN_NOTE in html


# --------------------------------------------------------------------------
# 6-9: the ways this can go wrong
# --------------------------------------------------------------------------

def test_6_the_refresh_leg_means_expired_and_never_calls_stripe(
        signed_in, seller_row, stripe):
    """`refresh_url` fires when the link went stale *before* it was used.

    There is nothing to retrieve, because the seller never reached Stripe's
    form on this attempt. Calling Stripe here would be a request whose answer
    describes a previous attempt, and rendering it would tell a seller whose
    link expired whatever their last session happened to leave behind.
    """
    _, calls = stripe
    html = page("/pulse/merchant/payouts/refresh")
    assert calls == []
    assert says(sor.RETURN_FAILED) in html
    # A failure must never claim an automatic return.
    assert sor.AUTO_RETURN_NOTE not in html
    assert AUTO_SCRIPT not in html
    # And it must leave the stored status exactly as it found it.
    assert stored_status() == "onboarding_started"


def test_7_no_connected_account_is_a_failure_not_a_blank_success(
        signed_in, stripe):
    conn = bot.db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM seller_payout_accounts WHERE user_id=?", (USER_ID,))
        conn.commit()
    finally:
        conn.close()
    _, calls = stripe
    html = page("/pulse/merchant/payouts/return")
    # Stripe cannot have returned a seller from a flow that was never started.
    assert calls == []
    assert says(sor.RETURN_FAILED) in html
    assert sor.AUTO_RETURN_NOTE not in html


def test_8_a_stripe_outage_does_not_demote_a_seller(
        signed_in, seller_row, monkeypatch):
    """The important half of "not knowing is not a reason to write".

    A transient retrieve failure says nothing about the account. Overwriting a
    stored status with a guess here is how a live seller gets knocked back to
    onboarding by a network blip.
    """
    conn = bot.db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE seller_payout_accounts SET onboarding_status='complete'"
            " WHERE user_id=?", (USER_ID,))
        conn.commit()
    finally:
        conn.close()

    def boom(account_id):
        raise RuntimeError("stripe is unreachable")

    monkeypatch.setattr(payment_provider, "get_account_status", boom)
    html = page("/pulse/merchant/payouts/return")
    assert says(sor.RETURN_FAILED) in html
    assert stored_status() == "complete"


def test_8b_the_no_op_guard_is_tested_where_it_lives(signed_in, seller_row):
    """`_persist_onboarding_status` must refuse to write an empty verdict.

    Split out from the test above deliberately. That one drives the route, and
    the route never *reaches* this guard on a failed retrieve —
    `_connect_return_snapshot` returns early, so the write is skipped one level
    up. A mutation that made this function invent a status therefore survived
    the route test completely.

    The guard is still the thing that has to hold if the early return is ever
    refactored away, so it is asserted directly rather than through a caller
    that happens to shield it.
    """
    conn = bot.db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE seller_payout_accounts SET onboarding_status='complete'"
            " WHERE user_id=? AND seller_type='merchant'", (USER_ID,))
        conn.commit()
    finally:
        conn.close()

    for unknowable in ({"ok": False}, {"ok": False, "reason": "retrieve_failed"}, {}, None):
        assert sor.onboarding_status_for(unknowable) == ""
        bot._persist_onboarding_status(USER_ID, "merchant", unknowable)
        assert stored_status() == "complete", unknowable

    # Control: the same function does write when the verdict is knowable, so
    # the assertions above are about the guard and not about a function that
    # never writes anything.
    bot._persist_onboarding_status(USER_ID, "merchant", stripe_says(
        details_submitted=False, charges_enabled=False, payouts_enabled=False))
    assert stored_status() == "onboarding_started"


def test_9_an_unauthenticated_return_redirects_and_never_calls_stripe(
        monkeypatch, seller_row, stripe):
    monkeypatch.setattr(bot, "require_account", lambda: None)
    _, calls = stripe
    response = get("/pulse/merchant/payouts/return")
    assert response.status_code in (301, 302, 303, 307, 308)
    assert calls == []


# --------------------------------------------------------------------------
# 10-12: the mission's explicit prohibitions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "?charges_enabled=1&payouts_enabled=1",
    "?status=complete",
    "?onboarding_status=complete&details_submitted=1",
    f"?account={ACCOUNT_ID}",
])
def test_10_query_parameters_claiming_success_are_ignored(
        signed_in, seller_row, stripe, query):
    """"Do not trust URL query parameters alone" — here, not at all.

    Stripe appends nothing to `return_url`: no status, no account id, no
    signature. Anything in the query string was put there by whoever typed the
    URL, and this is a page that writes to the seller's payout row. The test
    hands it a query string that says "finished" over an account Stripe says
    is not, and requires the page to believe Stripe.
    """
    recorder, _ = stripe
    recorder.result = stripe_says(
        details_submitted=False, charges_enabled=False, payouts_enabled=False)
    html = page("/pulse/merchant/payouts/return" + query)
    assert says(sor.RETURN_INCOMPLETE) in html
    assert says(sor.RETURN_READY) not in html
    assert stored_status() == "onboarding_started"


def test_11_the_stale_status_that_blocked_every_transfer_is_repaired(
        signed_in, seller_row, stripe):
    """The production bug, end to end.

    User 1's row: `onboarding_status='onboarding_started'` with both capability
    flags set and nothing due. Buyer checkout worked, because the card gates
    fall through to the flags. Transfers did not, because
    `seller_destination_account_id` refuses on the *word* regardless of them —
    so every sale booked as `ledger_pending_onboarding`.
    """
    assert stored_status() == "onboarding_started"
    assert "onboarding_started" in sor.MONEY_PATH_REFUSED_STATUSES

    page("/pulse/merchant/payouts/return")

    assert stored_status() == "complete"
    assert "complete" not in sor.MONEY_PATH_REFUSED_STATUSES
    # The real consumer, not a restatement of it: the money path must now hand
    # back an account id where before it returned "".
    conn = bot.db()
    try:
        conn.row_factory = __import__("sqlite3").Row
        cur = conn.cursor()
        row = bot.seller_payout_account(cur, USER_ID, "merchant")
    finally:
        conn.close()
    assert bot.seller_destination_account_id(row) == ACCOUNT_ID


@pytest.mark.parametrize("seller_type", ["merchant", "teacher"])
@pytest.mark.parametrize("leg", ["return", "refresh"])
def test_12_nothing_stripe_ish_reaches_the_browser(
        signed_in, seller_row, stripe, seller_type, leg):
    recorder, _ = stripe
    recorder.result = stripe_says(
        charges_enabled=False, payouts_enabled=False,
        disabled_reason="requirements.past_due",
        currently_due=["individual.id_number", "individual.verification.document"])
    html = page(f"/pulse/{seller_type}/payouts/{leg}")
    assert ACCOUNT_ID not in html
    assert "acct_" not in html
    assert "sk_" not in html and "rk_" not in html
    # Requirement names are Stripe's vocabulary and describe the seller's
    # identity documents. The app is where that detail belongs, behind auth.
    assert "individual.id_number" not in html
    assert "verification.document" not in html
    assert "requirements.past_due" not in html


# --------------------------------------------------------------------------
# Both lanes, and the shape of the handoff
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seller_type", ["merchant", "teacher"])
def test_a_lane_offers_the_app_exactly_when_the_app_can_take_it(
        signed_in, seller_row, stripe, seller_type):
    """The handoff is offered iff the shipped binary has a screen for it.

    Merchants have a money layer; teachers do not. The tempting shortcut is to
    emit `pulsesoc://pulse/teacher/payouts` anyway and let iOS sort it out — but
    an unhandled custom scheme does not error, it does nothing, so that would
    present as a button that looks live and silently fails, alongside an
    "opening automatically…" note that never opens anything.

    `app_scheme_url` refusing an unroutable path is what prevents that, and
    this asserts the page honours the refusal rather than routing around it.
    """
    html = page(f"/pulse/{seller_type}/payouts/return")
    href = app_href(html)
    if native_handoff_possible(seller_type):
        assert href.startswith("pulsesoc://")
        assert f"/pulse/{seller_type}/payouts" in href
        assert sor.AUTO_RETURN_NOTE in html
    else:
        # No link, and — critically — no promise of one.
        assert href == ""
        assert sor.AUTO_RETURN_NOTE not in html
        assert AUTO_SCRIPT not in html
    # Either way there is always somewhere to go.
    assert f"href='/pulse/{seller_type}/payouts'" in html


def test_the_two_lanes_really_do_differ(signed_in, seller_row, stripe):
    """Control for the test above: it would be vacuous if both branches agreed.

    If a teacher destination is ever registered this fails, which is the right
    time to revisit the asymmetry rather than discover it in production.
    """
    assert native_handoff_possible("merchant")
    assert not native_handoff_possible("teacher")


def test_the_handoff_is_the_custom_scheme_not_a_same_domain_link(
        signed_in, seller_row, stripe):
    """Why this cannot be a pulsesoc.com universal link.

    iOS does not consult the associated-domains file for a tap on a
    same-domain link — from a pulsesoc.com page, a pulsesoc.com href is in-site
    navigation. A universal link here would reload this very page, which is
    indistinguishable from the handoff silently doing nothing.
    """
    html = page("/pulse/merchant/payouts/return")
    href = app_href(html)
    assert href.startswith("pulsesoc://")
    assert "https://pulsesoc.com" not in href


def test_the_web_route_is_always_offered_as_well(signed_in, seller_row, stripe):
    """The scheme may not resolve — no app installed, desktop browser.

    `location.href` on an unhandled scheme leaves the page where it is, so the
    page must still contain somewhere to go that does not depend on the app.
    """
    html = page("/pulse/merchant/payouts/return")
    assert "Stay on the web" in html
    assert "href='/pulse/merchant/payouts'" in html


def test_every_page_this_route_renders_is_internally_consistent(
        signed_in, seller_row, stripe):
    """The pairing rule, checked against the route rather than the function.

    `presentation_is_consistent` is the same predicate the unit tests use, but
    here it is applied to presentations produced by driving the real endpoint,
    so a route that assembled the page from two separate calls to the decision
    module would be caught.
    """
    recorder, _ = stripe
    snapshots = [
        stripe_says(),
        stripe_says(charges_enabled=False, payouts_enabled=False),
        stripe_says(currently_due=["individual.id_number"]),
        stripe_says(disabled_reason="requirements.past_due"),
        stripe_says(details_submitted=False, charges_enabled=False,
                    payouts_enabled=False),
        {"ok": False, "reason": "retrieve_failed"},
    ]
    for snapshot in snapshots:
        recorder.result = snapshot
        html = page("/pulse/merchant/payouts/return")
        presentation = sor.return_presentation(snapshot)
        assert sor.presentation_is_consistent(presentation)
        # The promise on the page and the promise in the decision agree. The
        # merchant lane resolves, so `auto_handoff` is the whole condition here
        # — see the lane test above for the case where it is not.
        assert (sor.AUTO_RETURN_NOTE in html) == bool(presentation["auto_handoff"])
        assert (AUTO_SCRIPT in html) == bool(presentation["auto_handoff"])


# --------------------------------------------------------------------------
# Capability persistence
# --------------------------------------------------------------------------
#
# `classify_return` will demote a green-flagged account to UNDER REVIEW when
# Stripe is holding a capability, but only if it is *handed* the capabilities.
# Everything above patches the provider, so those tests would still pass if the
# capabilities never survived the trip to disk. These read them back out.

def test_the_capabilities_survive_the_round_trip_to_storage(
        signed_in, seller_row, stripe):
    """Driving the route must leave the capabilities readable from the row.

    The decision module treats a missing `capabilities` block as silence rather
    than refusal, which is the right reading of an old row but means a dropped
    column fails *open*: the next reader sees no block, falls back to the
    summary flags, and calls a held account ready. So the storage leg needs its
    own assertion — a classifier that is never given the data cannot be wrong.
    """
    from services.business_os.payments import connect_accounts as ca

    recorder, _ = stripe
    recorder.result = stripe_says(
        capabilities={"card_payments": "active", "transfers": "pending"})
    page("/pulse/merchant/payouts/return")

    state = ca.get_state(USER_ID)
    assert state is not None
    assert state["capabilities"] == {
        "card_payments": "active", "transfers": "pending"}
    # ...and the value that came back is the one the decision module acts on.
    assert sor.capabilities_verdict(state) is False


def test_a_stored_snapshot_without_capabilities_stays_silent_not_refused(
        signed_in, seller_row, stripe):
    """The fail-open direction, pinned deliberately rather than by accident.

    An account Stripe reports no capabilities for must read back as *unstated*,
    so `classify_return` keeps deferring to the summary flags. If this ever
    returned False instead, every seller whose stored snapshot predates the
    column would be demoted to UNDER REVIEW on their next return.
    """
    from services.business_os.payments import connect_accounts as ca

    recorder, _ = stripe
    recorder.result = stripe_says(capabilities={})
    page("/pulse/merchant/payouts/return")

    state = ca.get_state(USER_ID)
    assert state["capabilities"] == {}
    assert sor.capabilities_verdict(state) is None
    assert stored_status() == "complete"


def test_an_account_updated_webhook_does_not_blank_the_capabilities(
        signed_in, seller_row, stripe):
    """The webhook writes the whole row, so a field it omits is a field erased.

    This is the ordering that matters in production: the seller returns from
    onboarding (recording capabilities), then Stripe sends `account.updated`
    moments later. If that second write dropped the block, the account would go
    back to reading as silent — and silence falls through to the summary flags,
    which are exactly the flags that say a held account is fine.
    """
    from services.business_os.payments import connect_accounts as ca

    recorder, _ = stripe
    recorder.result = stripe_says(
        capabilities={"card_payments": "active", "transfers": "pending"})
    page("/pulse/merchant/payouts/return")
    assert ca.get_state(USER_ID)["capabilities"]["transfers"] == "pending"

    ca.apply_account_updated_event({
        "id": "evt_test",
        "type": "account.updated",
        "data": {"object": {
            "id": ACCOUNT_ID,
            "object": "account",
            "metadata": {"user_id": str(USER_ID)},
            "charges_enabled": True,
            "payouts_enabled": True,
            "details_submitted": True,
            "requirements": {"currently_due": [], "past_due": []},
            "capabilities": {"card_payments": "active", "transfers": "active"},
        }},
    })

    state = ca.get_state(USER_ID)
    assert state["capabilities"] == {
        "card_payments": "active", "transfers": "active"}
    assert sor.capabilities_verdict(state) is True


def test_the_column_is_not_leaked_to_callers_as_raw_json(
        signed_in, seller_row, stripe):
    """`capabilities` is the contract; `capabilities_json` is storage detail.

    `get_state` feeds API responses, so the encoded column must not ride along
    beside the decoded value — a client reading the wrong one would get a
    string where every consumer expects a mapping.
    """
    from services.business_os.payments import connect_accounts as ca

    page("/pulse/merchant/payouts/return")
    state = ca.get_state(USER_ID)
    assert "capabilities_json" not in state
    assert isinstance(state["capabilities"], dict)
