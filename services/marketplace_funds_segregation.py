"""How much of the platform's Stripe balance is not the platform's money.

Under separate charges and transfers the buyer always pays PulseSoc, so a single
Stripe balance holds several different kinds of money at once: PulseSoc's own
revenue, tax collected on someone else's behalf, buyer money captured into escrow
that is still refundable, advertiser funds paid in advance, and earnings owed to
sellers that have not been transferred yet. Stripe does not distinguish them. A
payout from the platform account to PulseSoc's bank draws on the same pooled
balance that seller transfers will later draw on.

Nothing in this repository could previously see that. ``ops.reconcile`` checks
that the commercial snapshots agree with themselves, which is internal
consistency and says nothing about whether the money is actually there. There was
no reader for the Stripe balance at all.

The rule this module exists to make checkable:

    withdrawable = available - designated

``designated`` is money PulseSoc holds but does not own. PulseSoc may withdraw
the remainder and no more. If ``available`` ever falls below ``designated``, the
platform has already spent other people's money — the seller transfers have not
failed yet, but they will, and the shortfall is the amount by which PulseSoc is
short.

WHY THE CLASSIFICATION IS INVERTED
----------------------------------
The obvious way to write this is a list of designated prefixes — ``seller_payable:``
and friends — and treat everything else as PulseSoc's. That fails in the dangerous
direction. An account type added next year is money PulseSoc does not own but this
module has never heard of, so it would be silently counted as withdrawable and the
first symptom would be a failed seller transfer.

So the list runs the other way. :data:`PLATFORM_OWNED` and :data:`NOT_A_HELD_BALANCE`
enumerate what is *not* designated, and everything else is designated by default. An
unrecognised account raises the reserve rather than lowering it, which is wrong in the
direction that costs PulseSoc a withdrawal rather than costing a seller their money.
``tests/marketplace/test_funds_segregation.py`` walks the AST of every ledger posting
in the repository and fails if any account prefix is not explicitly classified here,
so "unrecognised" is caught in CI rather than discovered in production.

This module reads and reports. It moves nothing and it blocks nothing, because
the withdrawal it is about does not happen in this codebase: PulseSoc's own
payouts are Stripe's platform payout schedule, configured in the Dashboard. The
segregation decision that setting represents is recorded in
``docs/payments/FUNDS_SEGREGATION.md`` and is the owner's to make.
"""

from __future__ import annotations

from typing import Any, Mapping

from services import db
from services.business_os.ledger import ledger

#: Accounts holding PulseSoc's own money. Revenue it has earned, expenses it has
#: incurred, and ``platform:payouts_settled``, which is not held at all — it is the
#: running total of money that has already left the platform balance for a seller's
#: bank, recorded so the ledger stays balanced.
#:
#: The intake accounts are the counterparty side of a capture: money enters the
#: system from them, so they run negative and represent no held balance either.
PLATFORM_OWNED = frozenset({
    "platform:marketplace_revenue",
    "platform:events_revenue",
    # Earned out of `ad_campaign_escrow:` as impressions and clicks are delivered.
    # The escrow side stays designated until that happens, which is what makes the
    # advertiser's unspent budget refundable.
    "platform:advertising_revenue",
    "platform:rewards_expense",
    "platform:payouts_settled",
    "platform:marketplace_intake",
    "platform:events_intake",
})

#: Bookkeeping counterparties for money crossing the system boundary. These are
#: never a balance PulseSoc holds; they are where a capture came from and where a
#: refund went. Counting them either way would double-count the real balance.
NOT_A_HELD_BALANCE = ("external:",)

#: Everything below is money PulseSoc holds for someone else. Named here for
#: reporting only — the arithmetic does not consult this list, because a prefix
#: missing from it must still be treated as designated. Each entry maps a prefix
#: to the bucket it is reported under.
DESIGNATED_BUCKETS = (
    ("seller_payable:", "seller_payable_minor"),
    ("seller_payout_pending:", "seller_payout_pending_minor"),
    ("liability:", "tax_liability_minor"),
    ("mkt_order_escrow:", "order_escrow_minor"),
    ("ad_campaign_escrow:", "ad_escrow_minor"),
    ("advertiser:", "advertiser_wallet_minor"),
)

#: Bucket for a designated account whose prefix is not in DESIGNATED_BUCKETS. It
#: is counted in the total regardless; this exists so the report can say which
#: accounts were classified by the default rather than by name.
UNCLASSIFIED_BUCKET = "unclassified_minor"


def is_designated(account: str) -> bool:
    """Is this account money PulseSoc holds for someone else?

    The default is yes. Only an account explicitly known to be PulseSoc's own, or
    explicitly known not to be a held balance at all, is excluded.
    """
    name = str(account)
    if name in PLATFORM_OWNED:
        return False
    if name.startswith(NOT_A_HELD_BALANCE):
        return False
    return True


def _balances(currency: str) -> list[tuple[str, int]]:
    """Every ledger account balance in one currency.

    Partitioned in Python rather than aggregated with ``LIKE 'seller_payable:%'``
    because ``_`` is a single-character wildcard in SQL ``LIKE`` and several
    prefixes here contain one — the pattern would also match accounts this must
    not count. Escaping that correctly differs between SQLite and Postgres, and
    the table holds one row per account per currency, so the scan is cheap and
    the dialect question disappears.
    """
    ledger.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT account, balance_cents FROM ledger_balances WHERE currency = ?",
            (str(currency).lower(),)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        record = dict(row)
        out.append((str(record["account"]), int(record["balance_cents"] or 0)))
    return out


def designated_funds(currency: str = "usd") -> dict:
    """Money the platform is holding for someone else.

    Only *positive* balances are summed. A negative ``seller_payable:<uid>`` means
    that seller was paid more than they earned, and letting it reduce the total
    would be netting one seller's debt against another seller's credit — which is
    precisely the thing segregation forbids. PulseSoc still has to hold the full
    amount owed to the sellers in credit, regardless of what a different seller
    owes back. Overdrawn accounts are reported separately instead, because they
    are a real receivable and someone has to decide how to recover them.
    """
    buckets: dict[str, int] = {name: 0 for _, name in DESIGNATED_BUCKETS}
    buckets[UNCLASSIFIED_BUCKET] = 0
    unclassified: list[str] = []
    overdrawn: dict[str, int] = {}
    total = 0

    for account, cents in _balances(currency):
        if not is_designated(account):
            continue
        if cents < 0:
            overdrawn[account] = cents
            continue
        for prefix, bucket in DESIGNATED_BUCKETS:
            if account.startswith(prefix):
                buckets[bucket] += cents
                break
        else:
            buckets[UNCLASSIFIED_BUCKET] += cents
            unclassified.append(account)
        total += cents

    return {
        "currency": str(currency).lower(),
        "designated_minor": total,
        **buckets,
        # Accounts counted as designated by the default rule rather than by name.
        # Not an error — the default is the safe direction — but it means an
        # account type exists that nobody has classified.
        "unclassified_accounts": sorted(unclassified),
        # Negative balances, kept out of the total on purpose. Their presence is
        # a finding: someone was overpaid and nothing in this system recovers it.
        "overdrawn_accounts": dict(sorted(overdrawn.items())),
        "overdrawn_minor": -sum(overdrawn.values()),
    }


def platform_owned(currency: str = "usd") -> int:
    """PulseSoc's own revenue net of expenses, for context only.

    Not part of the segregation arithmetic. ``withdrawable`` is derived from what
    Stripe actually holds minus what is owed, never from what the ledger thinks
    PulseSoc earned — those two disagree the moment a Stripe processing fee is
    deducted, and the fee comes out of the real balance while the ledger knows
    nothing about it.

    ``platform:payouts_settled`` is excluded because that money is already gone,
    and the intake accounts are excluded because they are a capture's counterparty
    rather than a balance.
    """
    excluded = {"platform:payouts_settled", "platform:marketplace_intake",
                "platform:events_intake"}
    total = 0
    for account, cents in _balances(currency):
        if account in PLATFORM_OWNED and account not in excluded:
            total += cents
    return total


def read_platform_balance(currency: str = "usd") -> dict:
    """The platform account's available balance at Stripe.

    A read. It retrieves a balance and cannot move money, which is why it is safe
    to call without the authorization every mutating path here demands.

    Returns ``{"available": False, ...}`` rather than raising when Stripe is not
    configured, because the caller's next question — "am I solvent?" — has a
    meaningful answer of "unknown", and an exception would make a missing key
    indistinguishable from a shortfall.
    """
    from services import payment_provider

    if not payment_provider._stripe_ready():
        return {"available": False, "reason": "stripe_not_configured"}
    try:
        import stripe

        balance = stripe.Balance.retrieve()
    except Exception as exc:  # pragma: no cover - network path
        return {"available": False, "reason": f"stripe_error: {str(exc)[:200]}"}

    code = str(currency).lower()
    return {
        "available": True,
        "currency": code,
        "available_minor": _sum_balance_buckets(balance, "available", code),
        # Pending funds are real money that has not cleared. A transfer cannot
        # draw on them, so solvency is judged on `available` alone and this is
        # reported only so a near-term shortfall is visible before it bites.
        "pending_minor": _sum_balance_buckets(balance, "pending", code),
    }


def _sum_balance_buckets(balance: Any, bucket: str, currency: str) -> int:
    """Stripe returns a list per bucket, one entry per currency."""
    entries = []
    if isinstance(balance, Mapping):
        entries = balance.get(bucket) or []
    else:
        entries = getattr(balance, bucket, None) or []
    total = 0
    for entry in entries:
        record = entry if isinstance(entry, Mapping) else getattr(entry, "__dict__", {})
        if str(record.get("currency") or "").lower() == currency:
            total += int(record.get("amount") or 0)
    return total


def assess(currency: str = "usd", *, available_minor: int | None = None) -> dict:
    """Is the platform holding enough to cover what it owes?

    ``available_minor`` is injectable so this can be evaluated against a figure an
    operator already has, and so the arithmetic is testable without a Stripe key.
    When it is omitted the balance is read from Stripe; when that is unavailable
    the status is ``unknown`` rather than a guess in either direction.
    """
    designated = designated_funds(currency)
    owed = int(designated["designated_minor"])

    source = "caller"
    if available_minor is None:
        reading = read_platform_balance(currency)
        if not reading.get("available"):
            return {
                "status": "unknown",
                "reason": reading.get("reason"),
                "designated_minor": owed,
                # Still useful without a balance: it is the floor PulseSoc must
                # leave behind whatever the balance turns out to be.
                "must_retain_minor": owed,
                **designated,
            }
        available_minor = int(reading["available_minor"])
        source = "stripe"

    available_minor = int(available_minor)
    shortfall = max(0, owed - available_minor)
    return {
        "status": "shortfall" if shortfall else "solvent",
        "balance_source": source,
        "available_minor": available_minor,
        "designated_minor": owed,
        "must_retain_minor": owed,
        # Clamped at zero: a negative withdrawable is a shortfall, and reporting
        # it as a negative amount PulseSoc may withdraw invites it being used as
        # one.
        "withdrawable_minor": max(0, available_minor - owed),
        "shortfall_minor": shortfall,
        "platform_owned_minor": platform_owned(currency),
        **{k: v for k, v in designated.items() if k != "designated_minor"},
    }
