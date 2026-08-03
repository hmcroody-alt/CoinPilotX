"""Server-authoritative Stripe-event -> canonical-ledger handler.

This is the bridge between the durable webhook inbox (``webhook_inbox``) and the
canonical double-entry ledger (``business_os.ledger``). It is the *only* place a
provider event becomes money movement, and it obeys the Stage 0 non-negotiables:

* **Never trust the client.** Amounts and currency are read from the Stripe
  event object itself (``amount`` / ``amount_total`` / ``amount_refunded``),
  which is integer minor units straight from Stripe — never from any
  client-supplied field.
* **Idempotent on the entity, not the event.** A funding key is derived from the
  event id; a refund key is derived from the Stripe *refund* id. The difference
  matters: one refund is described by several event types (``refund.created``,
  ``charge.refunded``, ``charge.refund.updated``) carrying different event ids,
  so keying on the event admits the same refund several times over. Keying on
  the refund makes those events collapse onto one ledger entry. Either way, if
  the inbox's single-claim guarantee were bypassed the ledger still refuses to
  double-post.
* **Cumulative fields are never posted as deltas.** ``amount_refunded`` on a
  Charge is a running total, not the amount of the refund that triggered the
  event. It is read in exactly one place — the fallback for a Charge whose
  ``refunds.data`` list is unavailable — and even there the handler subtracts
  what it has already posted for that charge before moving any money. The
  subtraction runs **both ways**: once a fallback has asserted a total for a
  charge, an individual ``refund.created`` arriving afterwards for that same
  charge is netted against it rather than added to it. Netting only one way left
  the double-count intact in the ordering that current Stripe API versions
  actually produce, where ``refunds.data`` is unexpanded and the two events
  describe the same refund from opposite ends.
* **Never lose money.** If the event cannot be mapped to a known user account,
  the funds are posted to a ``platform:stripe_suspense`` holding account (which
  keeps the double-entry invariant intact and flags the row for manual
  reconciliation) instead of being silently dropped.
* **Unknown events are ignored, not failed.** Returning an ``ignored`` result
  lets the inbox mark the row processed so it is not retried forever.

The handler signature ``handle_stripe_event(payload: dict) -> dict`` matches what
``webhook_inbox.process_event`` / ``reconcile_pending`` expect. The pure mapper
``map_stripe_postings`` is factored out so the mapping logic is unit-testable
without a database; it returns a *list*, because one event can imply more than
one money movement. ``map_stripe_event`` is the older single-posting spelling,
kept for callers that only ever see one.

Engine-portable via the ledger module; does not import ``bot.py``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from services import db
from services.business_os import ledger

# Ledger accounts this handler posts against.
EXTERNAL_STRIPE = "external:stripe"          # funding source / refund destination
SUSPENSE = "platform:stripe_suspense"        # holding account for unmapped funds

# Stripe event types that credit a user (money in).
_FUNDING_EVENTS = {
    "payment_intent.succeeded",
    "charge.succeeded",
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
}
# Stripe event types that reverse a prior credit (money out).
_REFUND_EVENTS = {
    "charge.refunded",
    "refund.created",
    "charge.refund.updated",
}

# Metadata keys we will accept as a user identifier, in priority order. Kept
# conservative on purpose: an unrecognised event routes to suspense rather than
# guessing a target account.
_USER_ID_KEYS = ("pulse_user_id", "user_id", "app_user_id", "client_reference_id")


class StripeLedgerMappingError(ValueError):
    """Raised only for events that look fundable but are internally malformed
    (e.g. a positive-amount funding event with no resolvable currency). These
    should fail loudly so the inbox retries / a human looks."""


def _event_object(payload: Mapping[str, Any]) -> dict:
    """Return the Stripe ``data.object`` (the charge / intent / refund / session)."""
    data = payload.get("data") if isinstance(payload, Mapping) else None
    obj = data.get("object") if isinstance(data, Mapping) else None
    return dict(obj) if isinstance(obj, Mapping) else {}


def _resolve_user_account(obj: Mapping[str, Any]) -> Optional[str]:
    """Best-effort map from a Stripe object to a ``user:<id>`` ledger account.

    Checks top-level ``client_reference_id`` and the object ``metadata``. Returns
    ``None`` (caller falls back to suspense) when nothing usable is present.
    """
    ref = obj.get("client_reference_id")
    if ref not in (None, "", 0):
        return f"user:{ref}"
    meta = obj.get("metadata")
    if isinstance(meta, Mapping):
        for key in _USER_ID_KEYS:
            val = meta.get(key)
            if val not in (None, "", 0):
                return f"user:{val}"
    return None


def _coerce_amount_cents(*candidates: Any) -> Optional[int]:
    """First candidate that is a positive integer number of minor units.

    Stripe already sends integer minor units, so we accept ``int`` (and clean
    integer-valued strings) but reject floats/bools to keep money math exact.
    """
    for c in candidates:
        if isinstance(c, bool) or c is None:
            continue
        if isinstance(c, int) and c > 0:
            return c
        if isinstance(c, str) and c.strip().isdigit():
            n = int(c.strip())
            if n > 0:
                return n
    return None


def map_stripe_event(payload: Mapping[str, Any]) -> Optional[dict]:
    """Pure mapping: Stripe event -> intended ledger posting (or ``None``).

    Returns ``None`` for event types we intentionally ignore, or for
    funding/refund events whose amount is zero/absent (nothing to post).
    Raises :class:`StripeLedgerMappingError` only for a genuinely malformed
    fundable event so the inbox surfaces it instead of silently dropping money.
    """
    if not isinstance(payload, Mapping):
        return None
    event_type = str(payload.get("type") or "").strip()
    event_id = str(payload.get("id") or "").strip()
    if not event_type:
        return None

    is_funding = event_type in _FUNDING_EVENTS
    is_refund = event_type in _REFUND_EVENTS
    if not is_funding and not is_refund:
        return None  # not a money-moving event we handle -> ignore

    obj = _event_object(payload)
    currency = str(obj.get("currency") or "usd").lower()

    if is_funding:
        amount = _coerce_amount_cents(
            obj.get("amount_received"), obj.get("amount"), obj.get("amount_total")
        )
        if amount is None:
            return None  # e.g. a $0 session -> nothing to post
        user_account = _resolve_user_account(obj)
        destination = user_account or SUSPENSE
        if currency == "":
            raise StripeLedgerMappingError(
                f"funding event {event_id} has amount {amount} but no currency"
            )
        return {
            "kind": "funding",
            "idempotency_key": f"stripe:{event_id}:funding",
            "actor": "stripe",
            "amount_cents": amount,
            "currency": currency,
            "entry_type": "funding",
            "source": EXTERNAL_STRIPE,
            "destination": destination,
            "reason": f"stripe:{event_type}",
            "provider_reference": event_id,
            "unmapped": user_account is None,
        }

    # refund: money leaves the platform back to the customer.
    #
    # This used to read `amount_refunded` and post it as if it were the amount of
    # this refund. It is not. On a Charge object `amount_refunded` is the
    # *cumulative* total refunded against that charge, and `charge.refunded` fires
    # again on every partial refund with a larger running total. Two partials of
    # $5 and $3 produced one event saying 500 and a second saying 800; the two
    # events have different ids, so idempotency keyed on the event id correctly
    # admitted both, and $13 left the ledger against an $8 refund.
    #
    # The idempotency was never the broken part. The field was. So the mapping is
    # now keyed on the *refund*, not the event: one refund is one posting, no
    # matter how many event types describe it or how often they are replayed.
    # `refund.created` and `charge.refund.updated` for the same refund now
    # collapse onto the same key, which per-event-id keying could never do.
    postings = _refund_postings(obj, currency, event_type, event_id)
    return postings[0] if postings else None


def _refund_posting(refund_obj, charge_obj, currency, event_type, event_id):
    """One posting for one Stripe Refund object.

    `amount` on a Refund is that refund's own amount, which is the delta we
    actually want. The account is resolved from the refund first and the parent
    charge second, because a Refund created via the API often carries no metadata
    of its own while the charge that spawned it does.

    Every posting is tagged with its parent charge in ``related_object``. That is
    what lets :func:`_charge_refund_state` find refunds posted through this path
    when a later cumulative-total event arrives for the same charge. Keying on
    ``provider_reference`` would not: that holds the *refund* id here and the
    *charge* id there, so the sum would come back zero and the cumulative total
    would post in full on top of the individual refunds — the original defect,
    reintroduced through the back door.

    ``cap_to_charge`` asks the handler to run that same comparison in the other
    direction before posting. The tag alone only made the netting one-way: a
    cumulative event subtracted the individual refunds it could see, but an
    individual refund arriving *after* a cumulative event added on top of it,
    because nothing told the handler to look. On current Stripe API versions
    ``refunds.data`` is not expanded by default, so ``charge.refunded`` takes the
    cumulative fallback and ``refund.created`` follows for the same refund — the
    exact ordering that double-posted. See :func:`_charge_refund_state`.
    """
    amount = _coerce_amount_cents(refund_obj.get("amount"))
    if amount is None:
        return None
    refund_id = str(refund_obj.get("id") or "").strip()
    if not refund_id:
        # No stable refund identity to key on. Fall back to the event id, which
        # is at least idempotent against replays of this same event.
        refund_id = f"event:{event_id}"
    user_account = _resolve_user_account(refund_obj) or _resolve_user_account(charge_obj)
    charge_id = str(refund_obj.get("charge") or "").strip() or str((charge_obj or {}).get("id") or "").strip()
    return {
        "kind": "refund",
        "idempotency_key": f"stripe:refund:{refund_id}",
        "actor": "stripe",
        "amount_cents": amount,
        "currency": str(refund_obj.get("currency") or currency or "usd").lower(),
        "entry_type": "refund",
        "source": user_account or SUSPENSE,
        "destination": EXTERNAL_STRIPE,
        "reason": f"stripe:{event_type}",
        "provider_reference": refund_id,
        "related_object": _charge_tag(refund_obj, charge_obj),
        "unmapped": user_account is None,
        # Empty when the refund does not name its charge, in which case there is
        # nothing to compare against and the posting stands on its own.
        "cap_to_charge": charge_id,
    }


def _charge_tag(refund_obj: Mapping[str, Any], charge_obj: Mapping[str, Any]) -> str:
    """The ``related_object`` tag naming the charge a refund belongs to.

    Empty when neither object names a charge, in which case the cumulative
    fallback simply has nothing to subtract — which is the safe direction to
    fail only because the fallback is itself keyed on the running total, so a
    replay of the same total still deduplicates in the ledger.
    """
    charge_id = str(refund_obj.get("charge") or "").strip()
    if not charge_id:
        charge_id = str((charge_obj or {}).get("id") or "").strip()
    return f"stripe_charge:{charge_id}" if charge_id else ""


def _refund_postings(obj, currency, event_type, event_id):
    """Every ledger posting implied by one refund-ish Stripe event.

    A `refund.created` / `charge.refund.updated` event carries a single Refund
    object. A `charge.refunded` event carries a Charge whose `refunds.data` list
    holds the Refund objects — expanding that list is what turns a cumulative
    total back into the individual amounts, and keying each on its refund id is
    what makes the two event families deduplicate against each other.
    """
    obj_type = str(obj.get("object") or "").strip().lower()

    # A Refund object arrived directly.
    if obj_type == "refund" or ("charge" in obj and "amount_refunded" not in obj):
        posting = _refund_posting(obj, {}, currency, event_type, event_id)
        return [posting] if posting else []

    # A Charge object: expand its refund list.
    refunds = obj.get("refunds")
    rows = refunds.get("data") if isinstance(refunds, Mapping) else refunds
    out = []
    if isinstance(rows, (list, tuple)):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            posting = _refund_posting(row, obj, currency, event_type, event_id)
            if posting is not None:
                out.append(posting)
    if out:
        return out

    # A Charge with no usable refund list. `amount_refunded` is still cumulative
    # and still must not be posted as-is, so hand the handler what it needs to
    # work out the delta against what has already been posted for this charge.
    # Doing that here would need a database read, and this function is
    # deliberately pure so the mapping stays unit-testable without one.
    total = _coerce_amount_cents(obj.get("amount_refunded"))
    if total is None:
        return []
    charge_id = str(obj.get("id") or "").strip() or f"event:{event_id}"
    user_account = _resolve_user_account(obj)
    return [{
        "kind": "refund",
        "idempotency_key": f"stripe:charge_refund_total:{charge_id}:{total}",
        "actor": "stripe",
        "amount_cents": total,
        "currency": currency,
        "entry_type": "refund",
        "source": user_account or SUSPENSE,
        "destination": EXTERNAL_STRIPE,
        "reason": f"stripe:{event_type}",
        "provider_reference": charge_id,
        "related_object": f"stripe_charge:{charge_id}",
        "unmapped": user_account is None,
        # The handler must subtract what it has already posted for this charge
        # before posting this figure. Without that it is a cumulative total
        # masquerading as a delta, which is the original defect.
        "cumulative_for_charge": charge_id,
        "cumulative_total_cents": total,
    }]


def map_stripe_postings(payload: Mapping[str, Any]) -> list:
    """All postings implied by an event. The canonical mapper.

    `map_stripe_event` is the older single-posting spelling and is kept for
    callers that only ever see one; it returns the first of these.
    """
    if not isinstance(payload, Mapping):
        return []
    event_type = str(payload.get("type") or "").strip()
    event_id = str(payload.get("id") or "").strip()
    if not event_type:
        return []
    if event_type in _FUNDING_EVENTS:
        single = map_stripe_event(payload)
        return [single] if single else []
    if event_type not in _REFUND_EVENTS:
        return []
    obj = _event_object(payload)
    currency = str(obj.get("currency") or "usd").lower()
    return _refund_postings(obj, currency, event_type, event_id)


_CUMULATIVE_KEY_PREFIX = "stripe:charge_refund_total:"


def _charge_refund_state(charge_id: str, currency: str) -> tuple:
    """What one Stripe charge has already refunded, and what total was claimed.

    Returns ``(posted_cents, established_total_cents)``:

    * **posted_cents** — the money actually debited so far for this charge. This
      is what a cumulative event subtracts to turn its running total into a
      delta.
    * **established_total_cents** — the largest cumulative total any
      ``charge.refunded`` fallback has already *asserted* for this charge, or 0
      if none has. This is what an individual refund needs, and it is a different
      number: a cumulative posting's ``amount_cents`` is the delta it moved, not
      the total it stood for. The total is recovered from the idempotency key,
      which already encodes it (``stripe:charge_refund_total:<charge>:<total>``)
      — so the fact is read from the same string that guarantees its uniqueness,
      rather than from a second column that could disagree with it.

    Both come from one scan. Matched on ``related_object``, which every refund
    posting carries, rather than on ``provider_reference``, which holds the
    refund id on the expanded path and the charge id on the fallback. Voided
    transactions are excluded because money that was reversed is not money
    already refunded — which is also why ``established_total`` is read only from
    live rows: void the cumulative posting and its claim over the charge goes
    with it.
    """
    if not charge_id:
        return (0, 0)
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT amount_cents, idempotency_key FROM ledger_transactions "
            "WHERE entry_type = 'refund' AND status = 'posted' "
            "AND currency = ? AND related_object = ?",
            (str(currency).lower(), f"stripe_charge:{charge_id}"),
        )
        posted = 0
        established = 0
        for row in cur.fetchall() or []:
            posted += int(row[0] or 0)
            key = str(row[1] or "")
            if key.startswith(_CUMULATIVE_KEY_PREFIX):
                tail = key.rsplit(":", 1)[-1]
                if tail.isdigit():
                    established = max(established, int(tail))
        return (posted, established)
    except Exception:
        return (0, 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def handle_stripe_event(payload: Mapping[str, Any]) -> dict:
    """Inbox handler: post the mapped event(s) to the canonical ledger.

    One event can imply more than one posting — a `charge.refunded` carrying two
    partial refunds is two distinct money movements — so this walks the list.
    Idempotent: refunds are keyed on the Stripe refund id, so a replayed event,
    a `refund.created` and a `charge.refunded` describing the same refund all
    collapse onto one ledger entry.

    Ignored event types return ``{"ignored": True}`` so the inbox marks the row
    processed.
    """
    ledger.ensure_schema()
    postings = map_stripe_postings(payload)
    if not postings:
        return {"ignored": True, "type": str((payload or {}).get("type") or "")}

    results = []
    for posting in postings:
        amount = int(posting["amount_cents"])

        # Cumulative fallback: subtract what this charge has already refunded.
        charge_id = posting.get("cumulative_for_charge")
        if charge_id:
            already, _established = _charge_refund_state(charge_id, posting["currency"])
            amount = int(posting["cumulative_total_cents"]) - already
            if amount <= 0:
                results.append({"posted": False, "kind": posting["kind"],
                                "duplicate": True, "transaction_id": None,
                                "unmapped": bool(posting.get("unmapped"))})
                continue
        else:
            # The same comparison in the other direction. A cumulative event
            # that has already asserted a total for this charge has, by the
            # meaning of `amount_refunded`, accounted for *every* refund on it —
            # including ones it could not enumerate, which is precisely why the
            # fallback ran. So an individual refund arriving afterwards is not
            # new money; it is that same money, named.
            #
            # Without this the netting was one-way, and the ordering that
            # actually occurs on current Stripe API versions — where
            # `refunds.data` is not expanded, so `charge.refunded` takes the
            # fallback and `refund.created` follows for the same refund — posted
            # both, twice the money against one refund.
            #
            # Headroom rather than an outright skip: if the cumulative row is
            # voided, `posted` drops while the claim is withdrawn with it, and
            # the individual refund is free to post normally again.
            cap_charge = posting.get("cap_to_charge")
            if cap_charge:
                already, established = _charge_refund_state(cap_charge, posting["currency"])
                if established > 0:
                    headroom = established - already
                    if headroom <= 0:
                        results.append({"posted": False, "kind": posting["kind"],
                                        "duplicate": True, "transaction_id": None,
                                        "covered_by_charge_total": True,
                                        "unmapped": bool(posting.get("unmapped"))})
                        continue
                    amount = min(amount, headroom)

        # Suspense-routed postings (unmapped user) are allowed to move an
        # allow-negative account without overdraft objections; a resolved user
        # account keeps normal overdraft protection.
        result = ledger.post_entry(
            idempotency_key=posting["idempotency_key"],
            actor=posting["actor"],
            amount_cents=amount,
            currency=posting["currency"],
            entry_type=posting["entry_type"],
            source=posting["source"],
            destination=posting["destination"],
            reason=posting["reason"],
            related_object=posting.get("related_object") or "",
            provider_reference=posting["provider_reference"],
            metadata={"unmapped": bool(posting.get("unmapped"))},
        )
        results.append({
            "posted": True,
            "kind": posting["kind"],
            "duplicate": bool(result.get("duplicate")),
            "transaction_id": result.get("transaction_id"),
            "unmapped": bool(posting.get("unmapped")),
        })

    # Single-posting events keep their original result shape so existing callers
    # and the inbox do not have to learn a new one.
    if len(results) == 1:
        return results[0]
    return {
        "posted": any(r["posted"] for r in results),
        "kind": results[0]["kind"],
        "duplicate": all(r["duplicate"] for r in results),
        "postings": results,
        "unmapped": any(r["unmapped"] for r in results),
    }
