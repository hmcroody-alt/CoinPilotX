"""What a seller is told when Stripe hands them back, and what we persist.

## The defect this exists for

A seller finished Stripe Connect onboarding and landed on a generic web page
reading "Merchant Payouts / Approval Required / Open Application" — a page about
an approval they already had, offering an application they had already
submitted, with no mention of Stripe and no way back to the app they started in.

Two separate causes, and the page was only the visible one.

**The return URL was never a return page.** `create_onboarding_link` was called
with `refresh_url` and `return_url` set to the *same* string,
`/pulse/{seller_type}/payouts`. Stripe uses those for opposite events — a link
that expired before it was used, and a flow the seller actually completed — so
collapsing them into one URL throws away the only signal distinguishing "come
back, your link went stale" from "you're done". That page then re-derived the
seller's approval from `marketplace_sellers.status` directly, while the route
that had *issued* the onboarding link gated on `seller_access_state`. Two
authorities, and a seller the second let through could be refused by the first.

**Nothing re-read Stripe on the way back.** The onboarding route writes
`onboarding_status = 'onboarding_started'` when it mints the link and no path
ever revisited it on return. In production that left exactly the row you would
least want:

    charges_enabled = 1, payouts_enabled = 1, requirements: nothing due,
    onboarding_status = 'onboarding_started'

Stripe considered the account fully live. The stored status still said the
seller had merely *begun*. That is not cosmetic, because the two halves of the
product read different columns:

  * the buyer-facing card gate (`marketplace_card_capability`,
    `seller_access_state.card_payment_status`) tolerates the stale status and
    falls through to the capability flags — so checkout correctly opened the
    card lane;
  * the money path (`seller_destination_account_id`) refuses any row whose
    `onboarding_status` is `onboarding_started`, *regardless* of those flags —
    so every sale would be booked `ledger_pending_onboarding` instead of
    `transfer_eligible`.

Buyers could pay. The seller could not be paid. Neither half is wrong on its own
terms; they simply disagree about which column carries the truth, and nothing
reconciled them because nothing refreshed the row after onboarding.

## What this module is

The decision, as a pure function over a Stripe account snapshot: which of the
five return states the seller is in, what they are told, and — separately — what
`onboarding_status` the row should now carry.

It is deliberately not the page and not the Stripe call. A snapshot goes in, a
verdict comes out, so every state is one dict literal away in a test. The states
that matter most here are the ones a human cannot reach on demand: nobody can
make Stripe hold an account in `pending_verification` to order, and a return
flow that is only ever exercised in its happy path is a return flow whose other
four branches rot.

## What it does NOT decide

Not whether a transfer may be sent. `seller_destination_account_id` owns that
and is untouched — this module only makes sure the column it reads reflects what
Stripe actually said. Writing a truthful status is the fix; reinterpreting it at
the money layer would be a second opinion about the same fact.

Not seller approval. Whether someone may sell at all is `seller_access_state`'s,
and it is a different axis from whether Stripe will take their card payments.
Conflating the two is what produced "Approval Required" in front of an approved
seller.
"""

from __future__ import annotations

from typing import Any, Mapping

# --------------------------------------------------------------------------
# The five states
# --------------------------------------------------------------------------

#: Stripe is done and the account is live. Both capabilities granted, nothing
#: outstanding.
RETURN_READY = "ready"

#: Everything asked for has been submitted and Stripe is still deciding. The
#: seller has no action to take, which is the whole reason this is distinct from
#: MORE_INFO — telling someone to "finish setting up" when they already have is
#: how a completed flow gets restarted from the beginning.
RETURN_UNDER_REVIEW = "under_review"

#: Stripe wants something specific. `currently_due`/`past_due` name it, or a
#: `disabled_reason` explains why the account is held.
RETURN_MORE_INFO = "more_info"

#: The seller left Stripe's flow before submitting. Not a failure — the link
#: still works and the remaining steps are the ones they skipped.
RETURN_INCOMPLETE = "incomplete"

#: We could not establish the account's state at all: the link expired, Stripe
#: refused the retrieve, or there is no connected account on file. Distinct from
#: every state above because it is a statement about *our* knowledge, not about
#: the seller's account — and it must never be rendered as progress.
RETURN_FAILED = "failed"

RETURN_STATES = (
    RETURN_READY,
    RETURN_UNDER_REVIEW,
    RETURN_MORE_INFO,
    RETURN_INCOMPLETE,
    RETURN_FAILED,
)

#: The states in which handing the seller straight back to the app is right.
#:
#: Every state hands off, including the unhappy ones, and that is the point: the
#: app's Payments & Payouts screen renders all seven card states with the
#: specific next step for each (`mobile-native/src/marketplace/cardPaymentState`),
#: so it is a better destination than this page for a seller who still has work
#: to do. The exception is `RETURN_FAILED`, where we do not know what the app
#: would be showing and sending them there would present whatever stale state
#: the row happens to hold as though it were the outcome of what they just did.
AUTO_HANDOFF_STATES = frozenset(
    {RETURN_READY, RETURN_UNDER_REVIEW, RETURN_MORE_INFO, RETURN_INCOMPLETE}
)


# --------------------------------------------------------------------------
# Persisted vocabulary
# --------------------------------------------------------------------------

#: The `onboarding_status` values this system already writes, mapped from the
#: return states that map cleanly onto one of them.
#:
#: These strings are not new. `complete` and `requirements_due` are what the
#: `account.updated` webhook writes, `restricted` is what the Connect projection
#: writes, and `onboarding_started` is the onboarding route's. Reusing them
#: rather than minting a parallel vocabulary is not a tidiness preference — the
#: three consumers branch on the exact spellings, in sets, with no default
#: branch between them:
#:
#:   * `seller_access_state.card_payment_status` — `{restricted, disabled,
#:     rejected, disconnected}`, `{pending, in_progress, started}`,
#:     `{completed, complete, done}`
#:   * `marketplace_card_capability._decide` — `{restricted, disabled, rejected,
#:     disconnected}`
#:   * `seller_destination_account_id` — `{onboarding_started, pending,
#:     restricted, disabled, rejected}`
#:
#: A word outside all of those does not mean "unknown" to them; it falls through
#: every set to whatever the flags say. So a sixth word is not merely unread, it
#: is read as *absence of the thing it was meant to record*.
#:
#: Note what is missing from this table: `RETURN_UNDER_REVIEW`. There is no
#: status string for "Stripe is deciding", because in this schema that is not
#: something the status column says. `onboarding_status` records what the
#: *seller* did; `charges_enabled`/`payouts_enabled` record what *Stripe*
#: granted; and "under review" is the combination of the two — finished, not yet
#: enabled. `card_payment_status` derives it exactly that way and says so at its
#: `{completed, complete, done}` branch. Writing the literal `"under_review"`
#: there would land outside every set above and be read as SETUP_IN_PROGRESS,
#: sending a seller who has finished back round a flow they completed — which is
#: the specific harm that branch's comment exists to prevent.
_STATUS_FOR_RETURN_STATE = {
    RETURN_READY: "complete",
    RETURN_UNDER_REVIEW: "complete",
    RETURN_INCOMPLETE: "onboarding_started",
}

#: `MORE_INFO` is the one state whose persisted status depends on more than the
#: state, because two different things wear that label. Stripe *asking* for a
#: document (`currently_due`) leaves the account working; Stripe *holding* the
#: account (`requirements.disabled_reason`) does not. Only the second belongs in
#: the refusal sets, and `restricted` is the word already in all three of them.
#: Collapsing both into `requirements_due` would leave a held account's status
#: outside every refusal set, so the money path would clear a transfer Stripe
#: has already said it will not honour.
_STATUS_MORE_INFO_HELD = "restricted"
_STATUS_MORE_INFO_DUE = "requirements_due"

#: Every spelling at least one consumer branches on, transcribed from the three
#: call sites listed above. Present so the invariant "never persist a word
#: nobody reads" is checkable rather than a matter of having been careful, and
#: so that if a consumer's set is ever narrowed, the test that fails names this
#: module instead of surfacing as a seller whose dashboard quietly went blank.
CONSUMED_ONBOARDING_STATUSES = frozenset(
    {
        # refusal / restriction, read by all three
        "restricted", "disabled", "rejected", "disconnected",
        # in-progress, read by card_payment_status and the money path
        "pending", "in_progress", "started", "onboarding_started",
        # finished, read by card_payment_status
        "completed", "complete", "done",
        # requirements outstanding, written by the account.updated webhook and
        # read via requirements_json rather than the word itself
        "requirements_due",
    }
)

#: The statuses `seller_destination_account_id` refuses to send a transfer to.
#: Transcribed rather than imported because importing it would mean importing
#: `bot`, and this module stays free of Flask on purpose. The test asserts the
#: two stay equal.
MONEY_PATH_REFUSED_STATUSES = frozenset(
    {"onboarding_started", "pending", "restricted", "disabled", "rejected"}
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "t", "on"}


def _listish(value: Any) -> list[str]:
    """Stripe's requirement arrays, defensively.

    Stripe sends lists; a replayed row sends whatever JSON survived a round
    trip. A string here must not be iterated character by character into a
    "requirement" called `"c"`.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Mapping):
        return []
    try:
        return [str(item).strip() for item in value if str(item).strip()]
    except TypeError:
        return []


#: The two capabilities a PulseSoc seller needs, and what each one buys.
#:
#: `card_payments` is what lets a charge be created against the account at all.
#: `transfers` is what lets the seller's share move to them — PulseSoc pays
#: sellers by transfer, so an account with `card_payments` active and
#: `transfers` still pending can take a buyer's money and not be able to
#: receive its own.
#:
#: This matters because `charges_enabled` and `payouts_enabled` do *not* cover
#: it. Stripe can hold `transfers` for a specific account while both of those
#: summary flags read true, which presents as a fully green account that cannot
#: actually be paid — the same shape as the stale-status bug this module exists
#: to fix, arriving by a different route.
REQUIRED_CAPABILITIES = ("card_payments", "transfers")


def _capability_granted(snapshot: Mapping[str, Any], name: str) -> bool:
    """Whether a named Stripe capability is `active`.

    `pending` and `inactive` are both *not granted*, and the distinction between
    them belongs to the requirements list rather than here.
    """
    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return False
    return str(capabilities.get(name) or "").strip().lower() == "active"


def capabilities_verdict(snapshot: Mapping[str, Any] | None) -> bool | None:
    """True if both capabilities are active, False if not, None if unstated.

    The third answer is the point. A snapshot with no `capabilities` block is
    not an account whose capabilities were refused; it is an answer that did not
    mention them — an older stored payload, or a caller that assembled the
    snapshot by hand. Reading that silence as "refused" would demote live
    sellers on the strength of a missing key, so absence returns None and the
    caller falls back to the summary flags.

    Only a capabilities block that is *present and not granted* overrides them.
    """
    capabilities = (snapshot or {}).get("capabilities")
    if not isinstance(capabilities, Mapping) or not capabilities:
        return None
    return all(_capability_granted(snapshot or {}, name) for name in REQUIRED_CAPABILITIES)


def classify_return(snapshot: Mapping[str, Any] | None) -> str:
    """Which of the five states a returning seller is in.

    Order matters and is the opposite of the old nesting. Availability of the
    *answer* is asked first (`ok`), then whether Stripe is holding the account,
    then whether it wants something, then whether the seller finished — so a
    disabled account is reported as disabled even when the seller also has
    outstanding requirements, rather than being described by whichever branch
    happened to be checked first.
    """
    snapshot = dict(snapshot or {})
    if not snapshot or not snapshot.get("ok"):
        return RETURN_FAILED

    disabled_reason = str(snapshot.get("disabled_reason") or "").strip()
    currently_due = _listish(snapshot.get("currently_due"))
    past_due = _listish(snapshot.get("past_due"))
    details_submitted = _truthy(snapshot.get("details_submitted"))
    charges = _truthy(snapshot.get("charges_enabled"))
    payouts = _truthy(snapshot.get("payouts_enabled"))

    if disabled_reason or past_due or currently_due:
        # `requirements.disabled_reason` is Stripe saying the account is held.
        # It is asked before `details_submitted` because a held account that has
        # submitted everything is still held, and "incomplete" would read as the
        # seller's fault.
        return RETURN_MORE_INFO

    if charges and payouts:
        # The summary flags say live. Before promising that, check the two
        # capabilities they do not cover: Stripe can hold `transfers` on an
        # account whose `charges_enabled` and `payouts_enabled` both read true,
        # and READY is the one state whose copy tells the seller there is
        # nothing left to wait for. `capabilities_verdict` returns None when the
        # snapshot never mentioned capabilities, which is silence rather than
        # refusal and must not demote anyone.
        if capabilities_verdict(snapshot) is False:
            return RETURN_UNDER_REVIEW
        return RETURN_READY

    if not details_submitted:
        # Nothing outstanding and nothing submitted: they closed the tab part
        # way through. Stripe has not asked for anything yet because it has not
        # been given enough to ask about.
        return RETURN_INCOMPLETE

    # Submitted, nothing due, not yet enabled. Stripe is reading it.
    return RETURN_UNDER_REVIEW


def onboarding_status_for(snapshot: Mapping[str, Any] | None) -> str:
    """The `onboarding_status` to persist for this snapshot, or "" to leave it.

    Empty for `RETURN_FAILED`, and that is the important case. A failed retrieve
    means we do not know the account's state, and overwriting a good stored
    status with a guess is how a live seller gets demoted by a transient Stripe
    timeout. Not knowing is a reason to leave the column alone, never a reason
    to write something into it.
    """
    state = classify_return(snapshot)
    if state == RETURN_MORE_INFO:
        held = str((snapshot or {}).get("disabled_reason") or "").strip()
        return _STATUS_MORE_INFO_HELD if held else _STATUS_MORE_INFO_DUE
    return _STATUS_FOR_RETURN_STATE.get(state, "")


# --------------------------------------------------------------------------
# Copy
# --------------------------------------------------------------------------

#: Headline, body, and the label on the button back to the app.
#:
#: `RETURN_READY`'s wording is fixed by the brief. The other four follow its
#: shape: state what happened, state what it means, and name the next step
#: without inventing a timeline — "we'll email you within 2 days" is a promise
#: this system has no way to keep.
_COPY: dict[str, dict[str, str]] = {
    RETURN_READY: {
        "headline": "Stripe setup complete",
        "body": (
            "Your payment account has been connected successfully. You can now "
            "return to PulseSoc to finish setting up your store and payments."
        ),
        "cta": "Open PulseSoc",
    },
    RETURN_UNDER_REVIEW: {
        "headline": "Stripe is reviewing your details",
        "body": (
            "Everything Stripe asked for has been submitted. There is nothing "
            "else for you to do — we'll enable card payments on your store as "
            "soon as Stripe confirms."
        ),
        "cta": "Open PulseSoc",
    },
    RETURN_MORE_INFO: {
        "headline": "Stripe needs a little more",
        "body": (
            "Stripe could not finish verifying your account with what it has so "
            "far. Your store and your other payment methods are unaffected — "
            "open PulseSoc to see exactly what's outstanding and continue."
        ),
        "cta": "Open PulseSoc",
    },
    RETURN_INCOMPLETE: {
        "headline": "Your Stripe setup isn't finished",
        "body": (
            "It looks like you left Stripe before finishing. Nothing was lost — "
            "open PulseSoc to pick up where you stopped."
        ),
        "cta": "Open PulseSoc",
    },
    RETURN_FAILED: {
        "headline": "We couldn't confirm your Stripe setup",
        "body": (
            "This can happen if the setup link expired before it was used. "
            "Nothing has changed on your account. Open PulseSoc and start "
            "payment setup again to get a fresh link."
        ),
        "cta": "Open PulseSoc",
    },
}

#: Shown under the button while the handoff is pending. Only truthful in the
#: states that actually hand off.
AUTO_RETURN_NOTE = "Opening PulseSoc automatically…"


def return_presentation(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Everything the return page renders, from one snapshot.

    `auto_handoff` and `state` are produced by the same call so a page cannot
    show the "opening automatically" note while sitting on a state that does not
    open anything — the same pairing rule the checkout's settlement line
    follows.
    """
    state = classify_return(snapshot)
    copy = _COPY[state]
    auto = state in AUTO_HANDOFF_STATES
    return {
        "state": state,
        "headline": copy["headline"],
        "body": copy["body"],
        "cta": copy["cta"],
        "auto_handoff": auto,
        "auto_note": AUTO_RETURN_NOTE if auto else "",
        "onboarding_status": onboarding_status_for(snapshot),
    }


def presentation_is_consistent(presentation: Mapping[str, Any]) -> bool:
    """The rules the page must never break, as a predicate.

    Stated here rather than restated in the test, so there is one wording of
    each: a page that promises an automatic return must have somewhere to go; a
    failed retrieve must never be rendered as an automatic success; and a state
    we could not determine must not write a status.
    """
    state = str(presentation.get("state") or "")
    if state not in RETURN_STATES:
        return False
    auto = bool(presentation.get("auto_handoff"))
    if auto != (state in AUTO_HANDOFF_STATES):
        return False
    if bool(presentation.get("auto_note")) != auto:
        return False
    status = str(presentation.get("onboarding_status") or "")
    if state == RETURN_FAILED and status:
        return False
    if state != RETURN_FAILED and not status:
        return False
    if status and status not in CONSUMED_ONBOARDING_STATUSES:
        # A word outside every consumer's sets does not read as "unknown"; it
        # reads as none of them, which is how a held account would pass the
        # money path's status check.
        return False
    return True
