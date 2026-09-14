"""Why a product is priced the way it is, and who decided to publish it.

What this is for
----------------
"Import to Store" now completes a listing and publishes it without the merchant
touching a price. That is the point of the feature and it is also the reason it
needs a record: every economic decision on that path is made by the server, from
inputs the merchant never sees at the moment of the tap. A month later they open
a product that is selling at a loss and ask a fair question -- *who set this
price?* Before this module the honest answer was "we cannot tell you."

So each imported item writes one row naming the inputs that decided it: which
pricing rule applied and which of §8's three tiers supplied it, what freight
figure the margin was measured against and who declared it, whether auto-publish
was on, and what the gate concluded. Not "imported product X" -- that is already
knowable from the listing. The row exists to answer *why*, which is the one thing
the listing itself does not store.

Where it goes
-------------
``business_os_store_audit``, through :func:`store_service._audit`, which is the
same append-only trail :mod:`connections` writes to when a merchant connects CJ.
Reusing it is deliberate: a merchant asking "what happened to my store" should
get one timeline, not one per subsystem.

That table is read back by ``store_service.get_timeline``, gated on
``store.read``. Every value here is therefore **merchant-facing**, and §27
applies to the payload contents directly. This is why :func:`_facts` copies from
an explicit allowlist rather than spreading the importer's payload: the importer
is free to grow its per-item dict, and a future key holding a credential
reference, a raw provider response or a vault pointer would otherwise be
published into the timeline by a change nobody thought was about audit. The
allowlist means a new fact has to be *chosen* before it is disclosed.

Nothing in here is buyer-facing. ``get_timeline`` has no anonymous path.

The second family
-----------------
An import is not the only time this system decides a price. :mod:`revisions`
reprices a live listing when a supplier moves their cost, on a worker cadence,
with nobody awake — and that is the harder record to do without. A merchant who
finds an unexpected price at import can at least remember importing; a merchant
whose price changed at 3am has nothing to reason from at all.

So there is a second vocabulary here, filed under its own subject type, with its
own allowlist and a system actor. It shares this module rather than living in
:mod:`revisions` for one reason: the disclosure decision above is a single
decision about a single merchant-facing table, and splitting it across two files
is how the second copy stops matching the first.

On the shape of a failure
-------------------------
The two call sites treat errors differently on purpose, and the difference is not
an oversight:

*A success is audited inside the item's own transaction*, before the commit that
makes the listing real. If that insert fails the item rolls back with it, and a
published-but-unexplained listing never exists. There is no defensive swallow
because the failure it would guard against does not occur in isolation -- the
audit insert is a parameterised write into a table :func:`ensure_schema` has just
established, on the same connection that has been writing listing rows for the
last few milliseconds. If it fails, the database is broken and the listing write
would have failed too.

*A failure is audited afterwards, on its own connection, best-effort.* Here the
reasoning inverts. The item has already failed; the work is already lost. Letting
a logging error escape from inside an ``except`` block would turn one unreachable
product into a dead batch of twenty, which is precisely the partial-success
guarantee the importer is built around. So it is caught and logged. A gap in the
trail is the lesser harm when the alternative is discarding nineteen good imports
to record one bad one.
"""

from __future__ import annotations

import logging
import re

from services import db
from services.business_os.store import schema as store_schema
from services.business_os.store import service as store_service

_log = logging.getLogger(__name__)

#: One subject type for the whole import family, matching how ``connections``
#: files everything under ``supplier_connection``.
#:
#: The tempting alternative -- ``listing`` for items that produced one, something
#: else for items that did not -- was rejected because it splits the answer to
#: "show me every import this store has attempted" across two queries and makes
#: the failures, which are the interesting rows, the ones you have to know to ask
#: for. ``subject_ref`` carries the listing id when there is a listing and is
#: NULL when there is not; ``external_product_id`` is in the payload either way,
#: so a refused import is still identifiable.
SUBJECT = "supplier_import"

#: Prefix for the action verb. The verb itself is the outcome, lowercased, so
#: ``PUBLISHED`` files as ``supplier.import.published`` and a new outcome
#: constant is covered the day it is added rather than the day somebody
#: remembers to extend a mapping here. ``test_every_outcome_has_an_action``
#: pins that this stays mechanical.
ACTION_PREFIX = "supplier.import."

_OUTCOME_TOKEN = re.compile(r"\A[A-Z][A-Z_]*\Z")

#: The only keys that may reach the timeline, and the reason each is here.
#:
#: Read this as the disclosure decision it is. Everything in it is already shown
#: to the merchant in the import result screen, so nothing new is being revealed
#: -- it is being made durable. Anything absent is absent deliberately: no
#: credential reference, no vault pointer, no raw provider response, no supplier
#: account or shop id, no access token expiry, nothing that would let the trail
#: become a second, unguarded copy of the connection record.
_ALLOWED = (
    # --- what was imported ------------------------------------------------
    "provider",
    "external_product_id",
    "listing_id",
    # Which supplier read the listing was built from. The provenance pointer
    # that makes "the cost was $4.10 at the time" checkable rather than
    # asserted.
    "snapshot_id",
    "variant_count",

    # --- what it cost ------------------------------------------------------
    # The merchant's own cost of goods, which they are already shown and are
    # the party paying. ``None`` when no variant had a readable cost, and
    # recorded as ``None`` rather than omitted -- see ``_facts``.
    "cost_low_cents",
    "cost_high_cents",

    # --- why it is priced that way ----------------------------------------
    # The heart of the row. Rule plus source answers §8's "which tier decided",
    # and the allowance pair plus basis answers "was freight in the margin".
    # A 45% rule means two different prices depending on the basis, so storing
    # the rule without the basis would record a number that cannot be checked.
    "pricing_rule",
    "pricing_source",
    "shipping_allowance_cents",
    "shipping_allowance_source",
    "margin_basis",
    "price_label",

    # --- who decided to show it -------------------------------------------
    # ``auto_publish`` is the setting; ``published`` is what actually happened;
    # ``status`` is where the listing came to rest. All three, because they
    # disagree in the interesting cases: auto-publish on and published false is
    # exactly a NEEDS_ATTENTION, and that disagreement is the record.
    "auto_publish",
    "marketplace_autolist",
    "published",
    "status",
    "awaiting_moderation",
    "quantity",
    "sellable_variants",

    # --- why it did not go through ----------------------------------------
    # ``problems`` is ``drafts``' own code list, unmodified. ``detail`` is the
    # refusal code from a failed item. Codes, not prose: the trail should not
    # be the place a second vocabulary for the same refusal grows.
    "problems",
    "detail",
)

#: A supplier moved their price and the listing followed. Filed apart from the
#: import family because it is a different question with a different answer: an
#: import row says why a price was *chosen*, a reprice row says why it *changed*,
#: and a merchant looking for the second does not want to page through the first.
REPRICE_SUBJECT = "supplier_reprice"

#: Two verbs, not one per outcome code. ``applied`` means the number a buyer's card
#: is charged actually moved; ``attention`` means it did not but something about the
#: margin now needs a human. They are fixed strings rather than derived from a
#: revision action because :func:`action_for`'s mechanical derivation is right for a
#: closed set of importer outcomes and wrong here: ``revisions`` reports per-variant
#: actions, and a listing's row summarises several of them.
REPRICE_APPLIED = "supplier.reprice.applied"
REPRICE_ATTENTION = "supplier.reprice.attention"

#: Nobody tapped anything. The trail's ``actor`` column holds a user id
#: everywhere else, and putting the store owner's id here would be a small lie
#: with a specific cost: it would make an automatic overnight reprice
#: indistinguishable from the merchant having done it themselves, which is the
#: one distinction a reprice row exists to draw.
SYSTEM_ACTOR = "system:supplier_sync"

#: The reprice vocabulary. Separate from :data:`_ALLOWED` rather than a union with
#: it, because a union stops being readable as a description: you could no longer
#: tell from the list what an import row contains and what a reprice row does, and
#: a key would silently become disclosable on both paths the moment it was needed
#: on one.
_ALLOWED_REPRICE = (
    # --- what moved --------------------------------------------------------
    # ``price_label`` is the number a stranger's card is charged, and it appears
    # in both ``before`` and ``after``. That pair *is* the row: "your price went
    # from $14.99 to $21.50" is the sentence the merchant needs, and either half
    # alone is unreadable.
    "price_label",
    "supplier_cost_cents",
    "listing_id",
    "provider",
    "external_product_id",

    # --- why it moved ------------------------------------------------------
    # The same five as the import path, and deliberately the same spellings, so
    # "the rule that priced it at import" and "the rule that repriced it in
    # September" are comparable rows rather than two vocabularies.
    "pricing_rule",
    "pricing_source",
    "shipping_allowance_cents",
    "shipping_allowance_source",
    "margin_basis",

    # --- what it means now -------------------------------------------------
    # ``margin_state`` is the honest verdict on the new price: a rule-held
    # reprice can still land on a bad margin, and recording only that the rule
    # was applied would file a loss as a success. ``attention`` carries
    # ``revisions``' own codes, unmodified.
    "margin_state",
    "attention",
    "variants_written",
)


def action_for(outcome) -> str:
    """``PUBLISHED`` -> ``supplier.import.published``.

    Refuses anything that is not an outcome-shaped token. The action string ends
    up in an indexed column that operators filter on, and deriving it from a
    value rather than a fixed mapping means a malformed outcome would otherwise
    quietly create a new action name nobody can search for.
    """
    token = str(outcome or "").strip()
    if not _OUTCOME_TOKEN.match(token):
        return ACTION_PREFIX + "unknown"
    return ACTION_PREFIX + token.lower()


def _facts(allowed, *sources) -> dict:
    """Merge the given dicts down to ``allowed``, in order, last one wins.

    Iterates the allowlist rather than the sources, which is the whole security
    property: a key the importer starts emitting tomorrow does not appear here
    until it is named above.

    The allowlist is a parameter and not a module lookup so the two vocabularies
    share one filter. Copying the comprehension per event family would give the
    "keep an explicit null" rule below two places to drift apart in, and that rule
    is the one carrying §12 into the trail.

    A key present with the value ``None`` is kept, not dropped. That is the same
    rule §12 turns on and it matters just as much in the trail: a row with no
    ``shipping_allowance_cents`` at all is ambiguous between "nobody declared a
    freight cost" and "this row predates the field", while an explicit ``null``
    says which. Zero, likewise, is a declaration -- freight is inside the item
    price -- and must never be normalised away as though it were absence.
    """
    merged: dict = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update(source)
    return {key: merged[key] for key in allowed if key in merged}


def record_import(conn, *, business_id, actor_user_id, outcome, facts) -> None:
    """Write one import event on the caller's transaction. Not committed here.

    Deliberately takes the connection rather than opening one: on the success
    path this row has to land or not land with the listing it describes, and a
    second connection could only ever give it a different fate.
    """
    store_service._audit(
        conn,
        business_id=business_id,
        subject_type=SUBJECT,
        subject_ref=facts.get("listing_id"),
        action=action_for(outcome),
        actor=actor_user_id,
        after=_facts(_ALLOWED, facts),
    )


def record_import_safely(*, business_id, actor_user_id, outcome, facts) -> bool:
    """Write one import event on a fresh connection and commit it. Never raises.

    For the branches that have already rolled back. Returns whether the row
    landed, so a test can assert on the swallow instead of taking it on trust --
    an exception handler nobody can observe is how a permanently broken audit
    trail looks from the outside.
    """
    conn = None
    try:
        conn = db.connect()
        record_import(conn, business_id=business_id, actor_user_id=actor_user_id,
                      outcome=outcome, facts=facts)
        conn.commit()
        return True
    except Exception:
        # See the module docstring: the item is already lost, and the batch is
        # not. Logged with the identifying facts so a gap in the trail is
        # findable in the application log rather than merely absent.
        _log.exception("supplier import audit failed: outcome=%s product=%s",
                       outcome, (facts or {}).get("external_product_id"))
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def record_reprice(conn, *, business_id, listing_id, action, before, after) -> None:
    """Write one reprice event on the caller's transaction. Not committed here.

    Takes the connection for the same reason :func:`record_import` does, and with
    more at stake: the write this accompanies is the ``UPDATE marketplace_listings
    SET price_label`` that changes what a stranger's card is charged. A row on a
    second connection could commit while that update rolled back, and the trail
    would then claim a price moved that never did.

    ``before``/``after`` both go through :func:`_facts`, so a reprice row is subject
    to the same disclosure decision as an import row -- and the same "an explicit
    null is a fact" rule, which here distinguishes "the supplier reported no cost"
    from "this row predates the field".

    ``actor`` is :data:`SYSTEM_ACTOR`, not a user. Nobody was present.

    Deliberately *not* rate-limited or deduplicated here. The decision about which
    supplier reads are worth a row belongs to the caller, which is the only place
    that knows whether anything buyer-visible moved -- a trail with a row per
    fifteen-minute tick is a trail with no findable rows at all.
    """
    store_service._audit(
        conn,
        business_id=business_id,
        subject_type=REPRICE_SUBJECT,
        subject_ref=listing_id,
        action=action,
        actor=SYSTEM_ACTOR,
        before=_facts(_ALLOWED_REPRICE, before),
        after=_facts(_ALLOWED_REPRICE, after),
    )


def ensure_schema() -> None:
    """Make sure the trail's table exists before anything tries to write to it.

    ``business_os_store_audit`` is created by ``store.schema``, which in
    production is reached through the commerce route pack's registration hook.
    That hook runs inside one of the ``except Exception`` blocks the app boots
    its optional route packs in, so "the table exists" is a fact this module
    would otherwise inherit from a subsystem that is allowed to fail quietly.

    :func:`store_schema.ensure_audit_table` and not ``ensure_schema``, and the
    difference is load-bearing rather than tidy. The full routine issues DDL for
    twenty tables, and ``CREATE TABLE IF NOT EXISTS`` does nothing to a table
    that already exists in an older shape -- so the index statement after it can
    raise ``no such column`` on a database whose storefront table predates a
    column this file now expects. That is survivable once at boot. On the import
    path it would be a 500 per import, thrown by drift in a table no import
    reads. The narrow call cannot fail for a reason that has nothing to do with
    auditing.
    """
    store_schema.ensure_audit_table()
