"""§17 — the four reservation control planes, and that they are really separate.

Two halves, and the second is the one that matters.

The first half is about the control surface: that an owner can authorize the
deadline backfill without authorizing a release, that every unrecognised value
fails toward less authority, that the configuration production is running on
today keeps meaning exactly what it means today, and — structurally — that no
configuration can ask for stock to be released without the payment being
checked first (§19). That last one is asserted over the whole cartesian product
of both controls rather than at a few sampled points, because the claim is that
the state is *unrepresentable*, and a claim about every state has to be checked
against every state.

The second half runs real sweeps against a real SQLite database and asserts on
side effects, because the bug this module exists to fix was invisible at the
control surface. ``dry_run=True`` read as "no side effects" and meant "no
database writes": ``_process_candidate`` called ``decide_for_reservation``
before its first ``if dry_run``, and the reconciler has no ``dry_run``
parameter to consult. So a dry run spent a live-key Stripe read on every
candidate it examined. Production never showed it because the only held rows
there have no deadline, so the candidate query cannot see them and nothing
reaches the provider — give those four rows the deadlines the backfill is
meant to write and a *dry* sweep starts calling Stripe.

That is why the provider-call assertions below are counts from an injected
fetcher rather than assertions about modes. A test that only checked
``authority.read_provider is False`` would have passed against the old code
too, since the old code never asked.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_cart_routes as cart  # noqa: E402
from services import marketplace_reservation_authority as authority  # noqa: E402
from services import marketplace_reservation_policy as policy  # noqa: E402
from services import marketplace_reservation_reconciler as reconciler  # noqa: E402
from services import marketplace_reservation_sweeper as sweeper  # noqa: E402

LISTING_ID = 7
STARTING_STOCK = 50

NOW = "2026-08-31T12:30:00+00:00"
LONG_EXPIRED = "2026-08-31T11:00:00+00:00"
EXPIRED = "2026-08-31T12:00:00+00:00"

#: The real production configuration on the ``coinpilotx-pulse-worker``
#: service, read from Railway on 2026-10-03. Pinned as a literal so that if an
#: operator changes it, the test that claims to describe production starts
#: failing instead of quietly describing something else.
PROD_ENV_TODAY = {"MARKETPLACE_RESERVATION_SWEEPER_ENABLED": "true",
                  "MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN": "true"}


@pytest.fixture(autouse=True)
def _reset_column_cache():
    cart._RESERVATION_COLUMN_CACHE = None
    yield
    cart._RESERVATION_COLUMN_CACHE = None


@pytest.fixture(autouse=True)
def _clear_env():
    """Both new controls and every tunable this subsystem re-reads per call.

    ``resolve()`` reads ``os.environ`` at call time by design, so a value left
    in the developer's shell would otherwise retune the authority under test.
    """
    names = (authority.SWEEP_MODE_ENV_VAR, authority.BACKFILL_MODE_ENV_VAR,
             authority.LEGACY_DRY_RUN_ENV_VAR,
             reconciler.MAX_DEFERRALS_ENV_VAR, sweeper.BATCH_LIMIT_ENV_VAR,
             sweeper.MIN_RECHECK_ENV_VAR, policy.TTL_ENV_VAR)
    previous = {name: os.environ.pop(name, None) for name in names}
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_listings "
                "(id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)")
    cur.execute("""CREATE TABLE marketplace_inventory_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_transaction_id INTEGER UNIQUE,
        buyer_user_id INTEGER, listing_id INTEGER, quantity INTEGER DEFAULT 1,
        status TEXT DEFAULT 'held', created_at TEXT, updated_at TEXT)""")
    cur.execute("""CREATE TABLE seller_transactions (
        id INTEGER PRIMARY KEY, buyer_user_id INTEGER, status TEXT,
        stripe_payment_intent_id TEXT, metadata_json TEXT, updated_at TEXT)""")
    cart._ensure_reservation_lifecycle_columns(cur)
    cur.execute("INSERT INTO marketplace_listings VALUES (?, ?, '')",
                (LISTING_ID, STARTING_STOCK))
    return cur


def _order(cur, tx_id, *, qty=2, intent=None, expires_at=EXPIRED,
           created_at=LONG_EXPIRED, tx_status="checkout_created"):
    """One transaction plus its held reservation, stock already decremented.

    ``expires_at=None`` reproduces the four stranded production rows: held,
    deadline-less, therefore invisible to the candidate query.
    """
    cur.execute(
        "INSERT INTO seller_transactions (id, buyer_user_id, status, "
        "stripe_payment_intent_id) VALUES (?, 1, ?, ?)",
        (tx_id, tx_status, intent))
    cur.execute(
        """INSERT INTO marketplace_inventory_reservations
        (seller_transaction_id, buyer_user_id, listing_id, quantity, status,
         created_at, updated_at, reserved_at, expires_at, reconcile_deferrals)
        VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (tx_id, LISTING_ID, qty, policy.STATUS_HELD, created_at, created_at,
         created_at, expires_at))
    cur.execute("UPDATE marketplace_listings SET quantity=quantity-? WHERE id=?",
                (qty, LISTING_ID))
    return tx_id


def _stock(cur):
    return cur.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                       (LISTING_ID,)).fetchone()[0]


def _res(cur, tx_id):
    return dict(cur.execute(
        "SELECT * FROM marketplace_inventory_reservations "
        "WHERE seller_transaction_id=?", (tx_id,)).fetchone())


def _counting_fetcher(status="canceled"):
    """A Stripe stub that records every intent it was asked about.

    The count is the whole point: plane C is "did we call the provider", and
    the only honest way to assert we did not is to hand the sweep something
    that would notice.
    """
    seen = []

    def fetch(intent_id):
        seen.append(intent_id)
        return status

    fetch.seen = seen
    return fetch


# --------------------------------------------------------------------------
# The control surface
# --------------------------------------------------------------------------

def test_01_the_owner_can_authorize_backfill_without_authorizing_release():
    """§17's literal requirement, and the reason this module exists.

    ``BACKFILL_MODE=apply`` on its own: deadlines get written, no Stripe read
    is spent, and nothing is released. Under the single legacy flag this
    combination could not be expressed at all — the only way to reach the
    backfill was ``DRY_RUN=false``, which granted all four planes at once.
    """
    resolved = authority.resolve(env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": "apply"})

    assert resolved.backfill_writes is True
    assert resolved.read_provider is False
    assert resolved.mutate_reservations is False
    assert resolved.sweep_mode == authority.SWEEP_MODE_OBSERVE


def test_02_the_owner_can_release_without_touching_the_legacy_rows():
    """The other direction, which a single ladder could not express.

    Backfill acts on a different population — holds no sweep can see — so an
    operator must be able to run the normal path while leaving those four
    August rows alone until they have been looked at by a human.
    """
    resolved = authority.resolve(env={
        "MARKETPLACE_RESERVATION_SWEEP_MODE": "release",
        "MARKETPLACE_RESERVATION_BACKFILL_MODE": "off"})

    assert resolved.mutate_reservations is True
    assert resolved.read_provider is True
    assert resolved.backfill_evaluates is False
    assert resolved.backfill_writes is False


@pytest.mark.parametrize("sweep_mode", ["observe", "reconcile", "release", "",
                                        "garbage", "releaase", "RELEASE",
                                        "re-lease", "true", "1", None])
@pytest.mark.parametrize("backfill_mode", ["off", "dry_run", "apply", "",
                                           "nonsense", "APPLY", "dry-run", None])
def test_03_no_configuration_releases_without_consulting_the_provider(
        sweep_mode, backfill_mode):
    """§19 as a structural property, over every reachable configuration.

    "Age alone is not proof payment failed" is enforced by the shape of the
    control, not by a code review: ``release`` sits above ``reconcile`` on one
    ladder, so plane D cannot be granted without plane C. Checked exhaustively
    because the claim is that the dangerous state is unrepresentable, and that
    is a claim about every state.

    The parameter lists deliberately include near-misses (``releaase``),
    case variants (``RELEASE``) and values from the *old* vocabulary
    (``true``, ``1``) — the realistic ways an operator reaches for more power
    than they spell correctly.
    """
    env = {}
    if sweep_mode is not None:
        env["MARKETPLACE_RESERVATION_SWEEP_MODE"] = sweep_mode
    if backfill_mode is not None:
        env["MARKETPLACE_RESERVATION_BACKFILL_MODE"] = backfill_mode

    resolved = authority.resolve(env=env)

    if resolved.mutate_reservations:
        assert resolved.read_provider, (
            f"sweep={sweep_mode!r} backfill={backfill_mode!r} grants release "
            "without provider authority — a hold could be returned for an "
            "order that paid")


@pytest.mark.parametrize("raw", ["", "garbage", "releaase", "re-lease",
                                 "true", "1", "yes", "release;", "relea se"])
def test_04_an_unrecognised_sweep_mode_fails_closed_to_observe(raw):
    """A typo must never be read as permission.

    ``observe`` rather than "keep the previous value" or "raise": the
    realistic mistake is in the direction of more power, and a worker that
    refused to boot on a bad flag would take the feed loop down with it.

    ``true``/``1``/``yes`` are here because they are the *old* vocabulary. An
    operator migrating from the boolean flag may reach for its values out of
    habit, and "truthy" must not be mistaken for "most authority" — under the
    legacy flag truthy meant the *safe* direction, so reading it as `release`
    would invert its meaning at exactly the moment of migration.
    """
    resolved = authority.resolve(env={"MARKETPLACE_RESERVATION_SWEEP_MODE": raw,
                                      "MARKETPLACE_RESERVATION_BACKFILL_MODE": "off"})

    assert resolved.sweep_mode == authority.SWEEP_MODE_OBSERVE
    assert resolved.mutate_reservations is False
    assert resolved.read_provider is False


@pytest.mark.parametrize("raw", ["", "garbage", "aply", "true", "1", "applyy"])
def test_05_an_unrecognised_backfill_mode_fails_closed_to_off(raw):
    resolved = authority.resolve(env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": raw,
                                      "MARKETPLACE_RESERVATION_SWEEP_MODE": "observe"})

    assert resolved.backfill_writes is False


@pytest.mark.parametrize("raw,expected", [
    ("RELEASE", "release"), ("Release", "release"), ("  release  ", "release"),
    ("OBSERVE", "observe"), ("reconcile\n", "reconcile"),
])
def test_05b_case_and_whitespace_are_tolerated_deliberately(raw, expected):
    """A value pasted into Railway carries stray case and whitespace.

    Tolerated rather than rejected because the failure mode of strictness here
    is the worse one: an operator who typed ``RELEASE``, read it back in the
    dashboard, and got ``observe`` would conclude the control is broken and
    reach for the legacy flag — which grants all four planes at once.

    Separated from the fail-closed tests above rather than folded in, because
    these two groups make opposite claims and a reader needs to see which
    inputs are *accepted* spellings and which are rejected ones.
    """
    resolved = authority.resolve(env={"MARKETPLACE_RESERVATION_SWEEP_MODE": raw})

    assert resolved.sweep_mode == expected


@pytest.mark.parametrize("raw,expected", [
    ("APPLY", "apply"), ("Apply ", "apply"),
    ("dry-run", "dry_run"), ("DRY_RUN", "dry_run"), ("Dry-Run", "dry_run"),
])
def test_05c_the_backfill_mode_accepts_both_hyphen_and_underscore(raw, expected):
    """``dry-run`` and ``dry_run`` are the same intent spelled two ways.

    Normalising the hyphen matters more than it looks: the surrounding
    environment variables and the log lines both use ``dry_run``, while prose
    and dashboards tend to write ``dry-run``. Rejecting one of them would make
    the safe middle rung the easiest one to miss, leaving ``off`` — which
    silently withholds the repair.
    """
    resolved = authority.resolve(env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": raw})

    assert resolved.backfill_mode == expected


def test_06_the_production_configuration_keeps_its_current_meaning():
    """The migration must not be a silent behaviour change.

    Production runs ``DRY_RUN=true``. Its image is ``reconcile``, not
    ``observe``, because a legacy dry run *does* call Stripe — mapping it to
    ``observe`` would be the tidier story and would quietly change what the
    live worker does on its next deploy. ``observe`` is therefore a genuinely
    new, stricter setting that an operator opts into.
    """
    resolved = authority.resolve(env=dict(PROD_ENV_TODAY))

    assert resolved.sweep_mode == authority.SWEEP_MODE_RECONCILE
    assert resolved.backfill_mode == authority.BACKFILL_MODE_DRY_RUN
    assert resolved.source == authority.SOURCE_LEGACY
    # The three planes, as they behave in production right now.
    assert resolved.backfill_writes is False
    assert resolved.read_provider is True
    assert resolved.mutate_reservations is False
    assert resolved.dry_run is True


def test_07_the_legacy_flip_still_grants_everything_it_used_to():
    """``DRY_RUN=false`` must keep meaning what an operator expects.

    Not narrowed on migration: someone who has already decided to flip the old
    flag gets the behaviour the old flag had, rather than a partially-granted
    state they did not ask for and would have to debug.
    """
    resolved = authority.resolve(
        env={"MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN": "false"})

    assert resolved.backfill_writes is True
    assert resolved.read_provider is True
    assert resolved.mutate_reservations is True
    assert resolved.source == authority.SOURCE_LEGACY


def test_08_a_new_control_takes_precedence_over_the_legacy_flag():
    """Otherwise the migration could not be completed.

    Setting only ``SWEEP_MODE`` must not leave the legacy flag silently
    granting the backfill: each control resolves on its own, and an unset one
    is its least rung.
    """
    resolved = authority.resolve(env={
        "MARKETPLACE_RESERVATION_SWEEP_MODE": "observe",
        "MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN": "false"})

    assert resolved.source == authority.SOURCE_EXPLICIT
    assert resolved.mutate_reservations is False
    assert resolved.backfill_writes is False


def test_09_an_unconfigured_worker_is_distinguishable_from_a_configured_one():
    """``source`` is the difference between "dry run" and "nobody has looked".

    Both report ``dry_run=True``, and only one of them is a decision. An
    operator reading a boot line needs to know which.
    """
    assert authority.resolve(env={}).source == authority.SOURCE_DEFAULT
    assert authority.resolve(
        env={"MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN": "true"}
    ).source == authority.SOURCE_LEGACY
    assert authority.resolve(
        env={"MARKETPLACE_RESERVATION_SWEEP_MODE": "observe"}
    ).source == authority.SOURCE_EXPLICIT


def test_10_the_authority_travels_into_the_sweep_summary():
    """§51: the planes must be readable from the result, not inferred.

    A summary carrying only ``dry_run`` cannot answer "was this sweep allowed
    to call Stripe", which is the question an operator asks when the provider
    bill moves.
    """
    cur = _db()
    resolved = authority.resolve(env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": "apply"})

    result = sweeper.run_reservation_expiry_sweep(cur, now=NOW, authority=resolved)

    assert result["sweep_mode"] == "observe"
    assert result["backfill_mode"] == "apply"
    assert result["may_backfill"] is True
    assert result["may_read_provider"] is False
    assert result["may_mutate"] is False
    assert result["authority_source"] == authority.SOURCE_EXPLICIT


# --------------------------------------------------------------------------
# Side effects — the half that would have caught the original bug
# --------------------------------------------------------------------------

def test_11_observe_mode_spends_no_provider_call_on_a_real_candidate():
    """The bug, pinned.

    A collectable candidate with a payment intent is exactly the row a legacy
    dry run would have spent a live-key Stripe read on. ``observe`` must not,
    and the only way to assert that is to hand the sweep a fetcher that
    records being called.
    """
    cur = _db()
    _order(cur, 501, intent="pi_observe")
    fetch = _counting_fetcher("canceled")

    result = sweeper.run_reservation_expiry_sweep(
        cur, now=NOW, fetch_status=fetch,
        authority=authority.resolve(env={"MARKETPLACE_RESERVATION_SWEEP_MODE": "observe"}))

    assert fetch.seen == [], "observe mode called the payment provider"
    assert result["provider_calls"] == 0
    assert result["candidates"] == 1
    # The candidate is reported as unevaluated, not as a quiet success.
    assert result["unevaluated"] == 1
    assert result["released"] == 0 and result["would_release"] == 0
    assert result["skipped"] == 0 and result["deferred"] == 0
    assert _stock(cur) == STARTING_STOCK - 2
    assert _res(cur, 501)["status"] == policy.STATUS_HELD


def test_12_reconcile_mode_does_call_the_provider_and_still_writes_nothing():
    """Plane C without plane D — and the preserved legacy behaviour.

    This is what production does today, so if this stopped calling the
    provider the migration would have silently changed the live worker.
    """
    cur = _db()
    _order(cur, 502, intent="pi_reconcile")
    fetch = _counting_fetcher("canceled")

    result = sweeper.run_reservation_expiry_sweep(
        cur, now=NOW, fetch_status=fetch,
        authority=authority.resolve(
            env={"MARKETPLACE_RESERVATION_SWEEP_MODE": "reconcile"}))

    assert fetch.seen == ["pi_reconcile"]
    assert result["provider_calls"] == 1
    assert result["would_release"] == 1, "the decision was not reached"
    assert result["released"] == 0, "reconcile mode released stock"
    assert result["unevaluated"] == 0
    assert _stock(cur) == STARTING_STOCK - 2
    assert _res(cur, 502)["status"] == policy.STATUS_HELD


def test_13_release_mode_is_the_only_one_that_returns_stock():
    cur = _db()
    _order(cur, 503, intent="pi_release")
    fetch = _counting_fetcher("canceled")

    result = sweeper.run_reservation_expiry_sweep(
        cur, now=NOW, fetch_status=fetch,
        authority=authority.resolve(
            env={"MARKETPLACE_RESERVATION_SWEEP_MODE": "release"}))

    assert result["released"] == 1
    assert _stock(cur) == STARTING_STOCK
    assert _res(cur, 503)["status"] == policy.STATUS_RELEASED


def test_14_backfill_apply_with_observe_repairs_without_releasing():
    """The §17 configuration, end to end against a database.

    The four stranded production rows, reproduced: held, deadline-less,
    invisible. ``BACKFILL_MODE=apply`` + ``SWEEP_MODE=observe`` must give them
    deadlines — making them visible to a future sweep — while releasing
    nothing and calling nobody. That separation is the whole point: a repair
    that both discovered and released rows would be releasing holds no live
    code path had ever evaluated.
    """
    cur = _db()
    for tx_id in (601, 602, 603, 604):
        _order(cur, tx_id, intent=f"pi_{tx_id}", expires_at=None)
    fetch = _counting_fetcher("canceled")

    result = sweeper.run_reservation_expiry_sweep(
        cur, now=NOW, fetch_status=fetch,
        authority=authority.resolve(
            env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": "apply",
                 "MARKETPLACE_RESERVATION_SWEEP_MODE": "observe"}))

    assert result["deadline_gap"] == 4
    assert result["backfilled"] == 4
    # Every row now carries a deadline, derived from its own created_at rather
    # than winning a fresh fifteen minutes it has not earned.
    for tx_id in (601, 602, 603, 604):
        row = _res(cur, tx_id)
        assert row["expires_at"], f"tx {tx_id} is still invisible to every sweep"
        assert row["status"] == policy.STATUS_HELD

    # Repaired, and untouched otherwise.
    assert fetch.seen == []
    assert result["released"] == 0
    assert _stock(cur) == STARTING_STOCK - 8


def test_15_backfill_off_leaves_the_stranded_rows_alone():
    """Including the counter that measures the leak, which must stay truthful."""
    cur = _db()
    _order(cur, 701, expires_at=None)

    result = sweeper.run_reservation_expiry_sweep(
        cur, now=NOW,
        authority=authority.resolve(
            env={"MARKETPLACE_RESERVATION_SWEEP_MODE": "release",
                 "MARKETPLACE_RESERVATION_BACKFILL_MODE": "off"}))

    # The gap is still *reported* — withholding the repair must not also
    # withhold the measurement, or an operator who turned backfill off would
    # see a clean sweep over leaking stock.
    assert result["deadline_gap"] == 1
    assert result["backfilled"] == 0
    assert result["would_backfill"] == 0
    assert _res(cur, 701)["expires_at"] in (None, "")


def test_16_backfill_dry_run_reports_what_it_would_do_and_writes_nothing():
    """§52: ``would_backfill`` and ``backfilled`` must not be the same number.

    The log line here once emitted ``backfilled=4`` for a cycle that wrote
    nothing, which is the observability failure §51 names: an operator reading
    it believed the four stranded rows had been repaired while they were still
    deadline-less in Postgres.
    """
    cur = _db()
    for tx_id in (801, 802):
        _order(cur, tx_id, expires_at=None)

    result = sweeper.run_reservation_expiry_sweep(
        cur, now=NOW,
        authority=authority.resolve(
            env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": "dry_run"}))

    assert result["would_backfill"] == 2
    assert result["backfilled"] == 0
    for tx_id in (801, 802):
        assert _res(cur, tx_id)["expires_at"] in (None, "")


def test_17_the_backfill_is_idempotent_and_multi_worker_safe():
    """§18/§21: a second pass must change nothing, including a concurrent one.

    The second ``backfill_missing_deadlines`` stands in for a second worker:
    its UPDATE re-asserts the no-deadline predicate, so whichever writes
    second matches no rows. Asserting the deadline is *unchanged* rather than
    just that the count is zero is what makes this a test about not
    overwriting rather than a test about not selecting.
    """
    cur = _db()
    _order(cur, 901, expires_at=None)
    grant = authority.resolve(env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": "apply"})

    first = sweeper.backfill_missing_deadlines(cur, now=NOW, authority=grant)
    deadline_after_first = _res(cur, 901)["expires_at"]

    second = sweeper.backfill_missing_deadlines(cur, now=NOW, authority=grant)

    assert first["backfilled"] == 1
    assert second["scanned"] == 0, "a repaired row was selected again"
    assert second["backfilled"] == 0
    assert _res(cur, 901)["expires_at"] == deadline_after_first


def test_18_the_backfill_is_bounded_by_the_batch_limit():
    """§18: a large legacy backlog is repaired across cycles, not in one go.

    Bounded so a repair cannot become an unbounded statement against a table
    checkout writes to — the limit is the same one the sweep uses, so one
    cycle's total database work stays predictable.
    """
    cur = _db()
    for tx_id in range(1001, 1008):
        _order(cur, tx_id, qty=1, expires_at=None)
    grant = authority.resolve(env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": "apply"})

    first = sweeper.backfill_missing_deadlines(cur, now=NOW, limit=3, authority=grant)
    second = sweeper.backfill_missing_deadlines(cur, now=NOW, limit=3, authority=grant)

    assert first["scanned"] == 3 and first["backfilled"] == 3
    assert second["scanned"] == 3 and second["backfilled"] == 3
    assert sweeper.count_deadline_gap(cur) == 1, "the backlog did not shrink by 6"


def test_19_a_terminal_row_is_never_given_a_deadline():
    """§20: the backfill is scoped to ``held``.

    A released or captured reservation with no deadline is not a leak — it is
    a finished row. Rewriting it would resurrect it into the candidate query.
    """
    cur = _db()
    _order(cur, 1101, expires_at=None)
    cur.execute("UPDATE marketplace_inventory_reservations SET status=? "
                "WHERE seller_transaction_id=?", (policy.STATUS_RELEASED, 1101))

    result = sweeper.backfill_missing_deadlines(
        cur, now=NOW,
        authority=authority.resolve(
            env={"MARKETPLACE_RESERVATION_BACKFILL_MODE": "apply"}))

    assert result["scanned"] == 0
    assert _res(cur, 1101)["expires_at"] in (None, "")


def test_20_an_unmigrated_caller_passing_dry_run_still_behaves():
    """§71: the old signature keeps working, with the legacy meaning.

    ``from_dry_run`` deliberately mirrors the legacy mapping rather than
    picking the stricter modes, so a test or caller that passes
    ``dry_run=True`` exercises the planes a legacy-configured worker actually
    has — including the provider call. Mapping it to ``observe`` would make
    such a test pass for a reason production does not share.
    """
    cur = _db()
    _order(cur, 1201, intent="pi_legacy")
    fetch = _counting_fetcher("canceled")

    result = sweeper.run_reservation_expiry_sweep(
        cur, now=NOW, dry_run=True, fetch_status=fetch)

    assert fetch.seen == ["pi_legacy"], "the legacy dry run stopped reconciling"
    assert result["would_release"] == 1
    assert result["released"] == 0
    assert result["sweep_mode"] == authority.SWEEP_MODE_RECONCILE
    assert _stock(cur) == STARTING_STOCK - 2
