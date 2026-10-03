"""Hold a product back from buyers, and release it once a person has chosen to.

Why this module exists
----------------------
`commerce_publication_enabled` was added to `marketplace_listings` because this
catalogue fuses approval and publication: `listing_review` maps APPROVE to
`(published, approved)` in one tuple, so there has never been a state meaning
"approved, prepared, and not yet in front of buyers". The column creates that
state.

This module owns the *decisions* written to it, and there are exactly three
other writers, each of which is here for a reason worth naming rather than a
convenience:

    `importer` (via :func:`hold_at_creation`)  closes the latch on an import it
        could not bind. A hold, never a release.
    `drafts._publish_core`                     releases, because pressing
        Publish is the decision this column records.
    `bot.init_db`                              creates the column, with no
        DEFAULT, so "released" cannot be manufactured by DDL.

`listing_review` is deliberately absent from that list. A moderator answering
"may this be sold" is not a person choosing to show a product now, and conflating
the two is how 153 unbound products came to be `published` + `approved`.

The problem it solves is specific and was measured, not supposed. All 196
supplier-backed listings in production are already `status='published'` and
`approval_status='approved'` -- including the 153 with no variant bound. What
keeps those 153 off sale is `quantity=0` and `price_minor=0`, and those are two
columns `revisions.apply_supplier_read` writes from the supplier feed on a worker
cadence. So binding a variant and waiting fifteen minutes would publish a product
to buyers with nobody having decided to publish anything. The binding is the
latch. This is the lock that has to be closed before the latch is touched.

Three questions, three answers
------------------------------
The control is orthogonal to the two columns beside it, and keeping the three
questions apart is the whole design:

    approval_status                 may this be sold?
    the readiness engine            can it be fulfilled safely?
    commerce_publication_enabled    has a person chosen to show it?

A product can be approved, fulfillable and still held -- that is
`READY_TO_GO_LIVE`, the state a merchant acts on. It can equally be released and
still invisible, because `in_stock` or `priced` is unmet. The control is a veto
and never a substitute for the other rules.

Why hold is cheap and release is expensive
------------------------------------------
:func:`hold` needs ownership and nothing else. Taking your own product off sale
is always safe: the worst case is a merchant hiding something they wanted
visible, which they can undo in one call, and a hold that could fail is a hold
somebody routes around in an incident.

:func:`release` is the opposite. It is the act §16 calls MAKE LIVE, and it is the
last point at which anything checks whether this product should be sold at all --
so it runs the real publish gate, `drafts.validate`, rather than a local opinion
about readiness. That matters more than the duplication it avoids: `_validate`
refuses `SUPPLIER_VARIANT_UNBOUND` and `NEGATIVE_MARGIN`, which are exactly the
two faults this mission exists to stop, and a second readiness predicate here
would be a second answer to "may this be sold" that nobody reviews. If the gate
grows a rule, release inherits it on the same commit.

Release does not publish
------------------------
Worth stating plainly because the name invites the wrong reading: releasing a
hold does not set `status='published'` and this module never writes `status` at
all. It clears a veto. A held draft that is released stays a draft -- it becomes
buyer-visible only when every other publication rule is already satisfied, which
is why `release` can refuse: a release granted to an unready product would be a
promise the predicate then silently declines to keep, and the merchant would be
left reading "Live" on a row no buyer can see.

Idempotence and the trail
-------------------------
Both calls are idempotent and report whether anything moved. An unchanged write
produces no audit row, on the same reasoning :func:`audit.record_reprice`
documents for not auditing every sync tick: a trail with a row per retry is a
trail with no findable rows. `changed` is in the return value so a caller can
tell a real decision from a no-op without consulting the trail.
"""

from __future__ import annotations

from services import db
from services import marketplace_listing_lifecycle as lifecycle
from services.business_os.suppliers import audit, drafts, policy
from services.business_os.suppliers.errors import SupplierError

#: Held and released, re-exported from the module that gives them meaning.
#:
#: `lifecycle` owns the predicate that reads this column, so it owns the
#: vocabulary too -- see :data:`lifecycle.PUBLICATION_HELD` for why the two
#: values do not carry the same authority, and why `drafts._publish_core` is
#: allowed to write the second one while `listing_review` is not.
HELD = lifecycle.PUBLICATION_HELD
RELEASED = lifecycle.PUBLICATION_RELEASED

#: Refused because the publish gate said the product is not ready. The gate's own
#: problem codes ride along in `problems` -- this module does not translate them,
#: because a second vocabulary for the same refusal is how two subsystems end up
#: disagreeing about why a product is blocked.
NOT_READY = "not_ready"


def _listing_facts(row) -> dict:
    """The audit payload for a listing row, before or after.

    Pulled through one function so a hold row and a release row describe the same
    fields. `audit._facts` filters to the allowlist regardless, so this is free to
    return more than it needs; what it must not do is return *different* shapes on
    the two paths, which is how a timeline becomes unreadable.
    """
    row = dict(row or {})
    return {
        "listing_id": row.get("id"),
        "commerce_publication_enabled": row.get("commerce_publication_enabled"),
        "status": row.get("status"),
        "approval_status": row.get("approval_status"),
        "price_label": row.get("price_label"),
        "quantity": row.get("quantity"),
        "provider": row.get("provider"),
        "external_product_id": row.get("provider_product_id"),
        "provider_variant_id": row.get("provider_variant_id"),
    }


def _scoped_listing(cur, listing_id, seller_user_id):
    """The listing plus its supplier mapping, if this seller owns it.

    Joins `marketplace_product_sources` rather than reading the listing alone,
    which does two jobs at once. It scopes the write to supplier-backed products
    -- a merchant's hand-authored listing has no source row and is not this
    module's business -- and it supplies the provenance the audit row needs
    without a second query.

    An absent listing and somebody else's listing raise the same 404, copying
    `drafts._owned_listing` deliberately: distinguishing them would turn this into
    an oracle for which listing ids exist.
    """
    # Returns `(coerced_id, row)` -- the coerced id is the one to trust, because
    # `coerce_listing_id` is what rejected the malformed inputs on the way in.
    listing_id, row = drafts._owned_listing(cur, listing_id, seller_user_id)
    cur.execute(
        "SELECT provider, provider_product_id, provider_variant_id "
        "FROM marketplace_product_sources WHERE listing_id=? LIMIT 1",
        (int(listing_id),))
    source = cur.fetchone()
    if source is None:
        # Not a supplier product. Refused rather than held, because a hold here
        # would be this module quietly taking authority over the manual
        # catalogue, whose publication story is not the one documented above.
        raise SupplierError("not_found", http_status=404)
    merged = dict(row)
    merged.update(dict(source))
    return merged


def _write(conn, cur, *, business_id, listing_id, seller_user_id, actor_user_id,
           value, action, reason, extra=None) -> dict:
    """Set the control, audit it, commit. The shared half of hold and release."""
    before = _scoped_listing(cur, listing_id, seller_user_id)
    listing_id = int(before["id"])

    current = before.get("commerce_publication_enabled")
    current = None if current is None else int(current)
    if current == value:
        # Nothing moved, so nothing is recorded. See the module docstring.
        return {"listing_id": listing_id, "changed": False,
                "commerce_publication_enabled": current}

    cur.execute(
        "UPDATE marketplace_listings SET commerce_publication_enabled=? WHERE id=?",
        (int(value), listing_id))

    after = dict(before)
    after["commerce_publication_enabled"] = int(value)
    payload = dict(extra or {})
    payload["reason"] = reason
    audit.record_publication(
        conn,
        business_id=business_id,
        listing_id=listing_id,
        action=action,
        actor=actor_user_id,
        before=_listing_facts(before),
        after=dict(_listing_facts(after), **payload),
    )
    conn.commit()
    return {"listing_id": listing_id, "changed": True,
            "commerce_publication_enabled": int(value)}


def hold_at_creation(cur, listing_id) -> None:
    """Close the latch on a listing being imported without a known binding.

    Lives here rather than in `importer` so the claim at the top of this module
    -- that it is the only thing that writes the column -- stays true, which is
    the property that makes the column auditable at all.

    Why an import writes anything
    -----------------------------
    `drafts._validate` already refuses to publish an unbound dropship listing, so
    the ordinary publish path needs no help. The path that needs it is the other
    one: moderator review. `listing_review` maps APPROVE to
    `(status='published', approval_status='approved')` in one tuple and consults
    no supplier fact, so a product that the import could not bind -- which
    `autopublish` correctly sent to review rather than publishing -- becomes
    `published` + `approved` the moment a moderator agrees it may be sold. From
    there the only things keeping it off sale are `quantity` and `price_minor`,
    and `revisions.apply_supplier_read` writes both from the supplier feed on a
    worker cadence. That is not a hypothesis about how the 152 happened; it is
    the shape all 153 unbound production rows are in.

    So the hold is written at the moment the listing is created, before anything
    downstream can bind it, because a hold applied after the bind is a hold with
    a race in front of it.

    Why only the unbound ones, and why 0 is not a decision
    ------------------------------------------------------
    Called only when :func:`importer._sole_orderable` returned ``None``. When the
    merchant's selection named one orderable variant, they chose the product, and
    holding that back would be this module overruling the import the same way a
    forced draft would overrule `auto_publish`.

    Writing 0 here is not manufacturing the decision the brief forbids
    manufacturing, and the asymmetry is the reason: a hold is a veto that the
    merchant lifts in one call, and the worst case is a product sitting invisible
    until somebody looks at it. The forbidden direction is the other one. This
    function cannot write 1 and there is no argument that would let it.

    Does not commit -- it writes inside the import's single transaction, so a
    failed import leaves no held row behind, and no audit row is recorded because
    no person acted. The trail is for decisions; this is the absence of one.
    """
    cur.execute(
        "UPDATE marketplace_listings SET commerce_publication_enabled=? WHERE id=?",
        (HELD, int(listing_id)))


def hold(business_id, store_id, actor_user_id, connection_id, listing_id, *,
         reason=None, context=None) -> dict:
    """Take a product out of buyer reach without disturbing anything else.

    Preserves what §1 requires preserved, by not touching it: the listing row,
    its supplier provenance, its pricing, its variants, its seller, its approval
    history and its diagnostic evidence are all untouched. One integer changes.

    This is the mechanism §1 asked for and could not find. `status='paused'` was
    the closest existing candidate and is lossy -- `/resume` returns the row to
    review rather than to `published`, and the moderator approval that follows
    NULLs `published_at`, so a round trip through pause costs the listing its
    publication history. A hold costs it nothing, and is reversed by
    :func:`release` restoring the row to precisely where it was.
    """
    policy.require_enabled()
    audit.ensure_schema()
    conn = db.connect()
    try:
        _, seller_user_id = drafts._scope(conn, business_id, store_id, actor_user_id,
                                          connection_id, context=context, write=True)
        cur = conn.cursor()
        return _write(conn, cur, business_id=business_id, listing_id=listing_id,
                      seller_user_id=seller_user_id, actor_user_id=actor_user_id,
                      value=HELD, action=audit.PUBLICATION_HELD, reason=reason)
    finally:
        conn.close()


def release(business_id, store_id, actor_user_id, connection_id, listing_id, *,
            reason=None, context=None) -> dict:
    """Clear the hold, if and only if the publish gate accepts the product.

    The MAKE LIVE of §16/§17, and the last gate before a stranger can be charged.
    Returns ``{"released": False, "refusal": NOT_READY, "problems": [...]}`` when
    the gate refuses, rather than raising: a bulk release of 152 products must be
    able to report "these 148 went live, these 4 still need a variant" without the
    first refusal discarding the rest.

    The verdict comes from `drafts.validate`, the same dry-run of the same gate the
    merchant's own Publish button runs. Deliberately not a readiness check written
    here -- see the module docstring on why a second answer to "may this be sold"
    is the wrong kind of duplication.
    """
    policy.require_enabled()
    audit.ensure_schema()

    # Asked before the write and on its own connection, because `validate` builds
    # a whole draft payload and is not a cheap predicate. A refusal here must cost
    # nothing and leave nothing open.
    verdict = drafts.validate(business_id, store_id, actor_user_id, connection_id,
                              listing_id, context=context) or {}
    if not verdict.get("publishable"):
        return {"listing_id": listing_id, "released": False, "changed": False,
                "refusal": NOT_READY,
                "problems": list(verdict.get("problems") or ())}

    conn = db.connect()
    try:
        _, seller_user_id = drafts._scope(conn, business_id, store_id, actor_user_id,
                                          connection_id, context=context, write=True)
        cur = conn.cursor()
        result = _write(
            conn, cur, business_id=business_id, listing_id=listing_id,
            seller_user_id=seller_user_id, actor_user_id=actor_user_id,
            value=RELEASED, action=audit.PUBLICATION_RELEASED, reason=reason,
            # The gate's verdict at the moment of release, so the trail records
            # what was true when the decision was made rather than what is true
            # when somebody later reads it.
            extra={"publishable": True, "problems": []})
        result["released"] = True
        return result
    finally:
        conn.close()
