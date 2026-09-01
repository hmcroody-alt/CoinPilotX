"""Stage 6 — structural guards on the Stripe webhook release wiring.

Why these are source-level assertions rather than request-level ones
-------------------------------------------------------------------
The behaviour of a release is proved in ``test_reservation_settlement.py``,
which drives the real service against a real database. What *that* cannot prove
is that ``bot.py`` still calls it — and the defect this mission exists to fix
was never a bug inside a release function. It was a webhook branch that did not
exist (``payment_intent.canceled``) and four branches that each carried their
own copy of the mutation, free to drift apart one edit at a time.

That is a wiring property, and wiring is what regresses silently. A future
change can add a fifth branch with a hand-rolled ``UPDATE marketplace_listings
SET quantity = quantity + ...`` and every behavioural test in this repository
will still pass, because the shared service it bypassed is still correct.

So these read the source. ``bot.py`` cannot be imported here anyway — it is a
111k-line monolith and this sandbox runs Python 3.10 while production pins
3.11 — but that constraint is not the reason for the approach. Even with a
working import, a request-level test would assert that one branch behaves, not
that no branch has escaped the shared path.
"""

import os
import pathlib
import re

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BOT_PY = REPO_ROOT / "bot.py"
CART_ROUTES = REPO_ROOT / "services" / "marketplace_cart_routes.py"


@pytest.fixture(scope="module")
def bot_source() -> str:
    return BOT_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cart_source() -> str:
    return CART_ROUTES.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The branch that was missing
# --------------------------------------------------------------------------

def test_payment_intent_canceled_is_handled(bot_source):
    """The abandoned-sheet signal. Its absence was the original leak.

    Stripe emits nothing at all when a buyer dismisses the Apple Pay or
    PaymentSheet UI. The cancellation of the underlying intent is the only
    event that ever arrives, and until this branch existed it was recorded and
    thrown away while the reservation stayed held forever.
    """
    assert 'event_type == "payment_intent.canceled"' in bot_source


def test_the_canceled_branch_releases_through_the_shared_service(bot_source):
    branch = bot_source.split('event_type == "payment_intent.canceled"', 1)[1][:4000]
    assert "settle_failed_transactions" in branch
    assert "REASON_PAYMENT_CANCELED" in branch


def test_the_canceled_branch_marks_the_event_processed(bot_source):
    """An unacknowledged event is retried by Stripe indefinitely."""
    branch = bot_source.split('event_type == "payment_intent.canceled"', 1)[1][:4000]
    assert "record_stripe_event(event" in branch
    assert 'update_webhook_event(event_id, "processed")' in branch


# --------------------------------------------------------------------------
# One mutation, many callers
# --------------------------------------------------------------------------

def test_stock_is_returned_in_exactly_one_place_in_the_codebase(cart_source):
    """The credit expression must have a single home.

    Every other invariant in this subsystem — release is idempotent, a captured
    reservation cannot be released, stock cannot be double-credited — is
    enforced by the compare-and-swap that guards this one statement. A second
    copy anywhere would be unguarded by construction, so the count is the
    invariant.
    """
    credit = "UPDATE marketplace_listings SET quantity=COALESCE(quantity,0)+"
    # Scoped to the runtime source tree. A repo-wide walk would also traverse
    # `.venv` and `.claude/worktrees`, which hold vendored packages and whole
    # snapshot copies of this repository — neither ships, and both would make
    # this a slow test that fails for reasons unrelated to the invariant.
    candidates = [BOT_PY, *sorted((REPO_ROOT / "services").rglob("*.py"))]
    matches = [
        str(path.relative_to(REPO_ROOT))
        for path in candidates
        if credit in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert matches == ["services/marketplace_cart_routes.py"], matches


def test_bot_never_mutates_reservation_rows_directly(bot_source):
    """The monolith must go through the service, not reach into the table.

    A direct ``UPDATE ... marketplace_inventory_reservations SET status`` in a
    webhook branch would skip the compare-and-swap that makes both terminal
    transitions idempotent, and would be invisible to every test that exercises
    the service.
    """
    direct = re.findall(
        r"UPDATE\s+marketplace_inventory_reservations\s+SET", bot_source, re.IGNORECASE)
    assert direct == [], f"{len(direct)} direct reservation mutation(s) in bot.py"


def test_all_three_checkout_lanes_settle_through_the_shared_service():
    """Cart, buy-now and offers must fail identically.

    Three checkout entry points is the structural reason this defect class
    keeps recurring: each one creates a reservation, each one can fail before
    Stripe, and each one used to undo its own reservation in its own way. The
    offers lane in particular was still hand-rolling the pairing after the
    other two had been consolidated — with an unguarded status write and a
    release carrying no reason — and it survived because the guard above only
    read ``bot.py``. Naming all three here means a fourth lane cannot be added
    with a private copy without this failing.
    """
    lanes = {
        "services/marketplace_cart_routes.py": CART_ROUTES,
        "services/marketplace_offers_routes.py": REPO_ROOT / "services" / "marketplace_offers_routes.py",
        "bot.py": BOT_PY,
    }
    for name, path in lanes.items():
        source = path.read_text(encoding="utf-8")
        assert "settle_failed_transactions" in source, name
        assert "REASON_CHECKOUT_ERROR" in source, name


def test_every_release_call_in_bot_records_why(bot_source):
    """A release with no reason is an unexplained inventory movement.

    ``release_inventory_reservation`` normalises an unknown reason rather than
    raising, precisely so it can be called safely from a webhook — which means
    an omitted reason fails silently and lands in the audit trail as
    ``manual``. Nothing at runtime would complain. This is what complains.
    """
    calls = re.findall(
        r"release_inventory_reservation\((?:[^()]|\([^()]*\))*\)", bot_source)
    # Zero is the ideal — it means every site went through the shared path.
    missing = [call for call in calls if "reason=" not in call]
    assert missing == [], missing


def test_failure_branches_do_not_pair_a_release_with_their_own_status_update():
    """The duplication Stage 6 forbids, stated as a countable property.

    Before this stage, four webhook branches each carried "release the hold,
    then move the transaction to a terminal status". The pairing now lives in
    ``settle_failed_transactions``; any branch that still writes both halves
    itself has re-forked the logic.

    Scoped to every runtime module, not only ``bot.py``. The first version of
    this test read the monolith alone, and a sixth copy of the pairing survived
    it in ``marketplace_offers_routes.py`` — carrying both defects the shared
    path exists to prevent: a status write with no settled guard, and a release
    with no reason, which normalises to ``manual`` rather than raising. A guard
    that only watches one file teaches the duplication where to hide.
    """
    sources = [BOT_PY, *sorted((REPO_ROOT / "services").rglob("*.py"))]
    for path in sources:
        source = path.read_text(encoding="utf-8", errors="ignore")
        # `settle_failed_transactions` is the one place the pairing is allowed
        # to exist — it *is* the pairing. Excluding its body by span rather than
        # by a substring exemption keeps the rule "exactly one implementation"
        # instead of "any implementation that mentions the right name".
        if path == CART_ROUTES:
            start = source.index("def settle_failed_transactions")
            end = source.index("\ndef ", start)
            source = source[:start] + source[end:]
        for match in re.finditer(r"release_inventory_reservation\(", source):
            window = source[match.end():match.end() + 600]
            assert "UPDATE seller_transactions SET status=" not in window, (
                f"{path.relative_to(REPO_ROOT)}: a release is still paired with a "
                "hand-rolled terminal-status update; use settle_failed_transactions")


# --------------------------------------------------------------------------
# The settled-order guard, at every call site
# --------------------------------------------------------------------------

def test_the_settled_guard_is_defined_once_and_used_by_the_shared_path(cart_source):
    """No failure branch may downgrade an order that already settled.

    Stripe does not guarantee event ordering, so a ``payment_failed`` for an
    earlier attempt can arrive after the ``succeeded`` that followed it.
    """
    assert 'SETTLED_TRANSACTION_STATUSES = ("paid", "refunded")' in cart_source
    settle = cart_source.split("def settle_failed_transactions", 1)[1]
    settle = settle.split("\ndef ", 1)[0]
    assert settle.count("status NOT IN ({placeholders})") == 2, (
        "both the metadata and no-metadata update paths must carry the guard")


def test_the_payment_pause_is_untouched():
    """This mission is explicitly forbidden from enabling real payments.

    Asserted here so that the guarantee is checked by CI on every run rather
    than resting on a claim in a report.
    """
    pause = (REPO_ROOT / "services" / "marketplace_payment_pause.py").read_text(encoding="utf-8")
    body = pause.split("def marketplace_card_payments_paused", 1)[1].split("\ndef ", 1)[0]
    assert "return True" in body


# --------------------------------------------------------------------------
# Owner-facing configuration
# --------------------------------------------------------------------------

def test_the_canceled_event_is_declared_required_for_the_webhook_endpoint():
    """The handler is inert unless the Stripe endpoint subscribes to the event.

    Endpoint subscriptions live in the Stripe Dashboard and cannot be set from
    this repository, so the audit script is the only place the requirement can
    be recorded where a human will be shown it.
    """
    audit = (REPO_ROOT / "scripts" / "stripe_webhook_recovery_audit.py").read_text(encoding="utf-8")
    required = audit.split("REQUIRED_EVENTS", 1)[1].split("}", 1)[0]
    assert '"payment_intent.canceled"' in required
