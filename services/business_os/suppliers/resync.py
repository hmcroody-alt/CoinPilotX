"""The merchant asking for their supplier data to be re-read, now.

Why a merchant can ask at all
-----------------------------
The background worker already re-reads every provider-backed product on a
cadence -- an hour for product data, fifteen minutes for stock. That is fine
until something has visibly gone wrong, at which point the merchant is looking
at a screen that says "Last sync failed" and has no way to say "try again". The
only honest options on such a screen are a control that really retries or no
control at all, and a screen with no control leaves them to wait out a cadence
they cannot see.

So this exists to make "Sync now" true. It does not fetch anything itself and
it does not call the provider: it moves the merchant's existing jobs to the
front of the queue the worker is already draining. The merchant gets the work
started, and the quota controller, the lease, the failure backoff and the
network gate that every other read passes through are all still in the path.

What it deliberately cannot do
------------------------------
*Create* work. Every job scheduled here is keyed on a row the merchant already
owns -- their connection, and the products they already imported through it.
There is no parameter through which a caller could name a product they have not
imported, or another store's connection, because the only ids that reach
:func:`worker.schedule` come out of a query scoped by
:func:`drafts._scope`'s verdict. That is the same boundary
``worker.schedule``'s own docstring asks its callers to hold.

Why it is bounded
-----------------
``MAX_PRODUCTS`` caps how many products one request enqueues. Without it a
merchant with a large catalogue could, by tapping a button twice, put more work
in front of the worker than it drains in a tick -- delaying the very health
check they pressed the button to get. The cap is reported back rather than
hidden, because a request that silently did part of the job is the failure this
whole endpoint exists to stop.
"""

from __future__ import annotations

import time

from services import db
from services.business_os.suppliers import drafts, policy, worker


#: Per request, not per connection. A second tap re-queues the same rows, which
#: is a no-op by the ``ON CONFLICT`` in ``worker.schedule``; it does not walk
#: further down a catalogue.
MAX_PRODUCTS = 200

#: Connection-level jobs, which are what actually answer "is this supplier
#: reachable". Scheduled first and always, including for a merchant with no
#: imported products at all -- their connection is exactly the thing they are
#: asking about.
_CONNECTION_KINDS = ("health", "shops", "subscriptions")

#: Per product. ``product`` re-reads cost and availability, ``inventory`` the
#: stock level; a merchant pressing one button means both, because the screen
#: that button sits on shows both.
_PRODUCT_KINDS = ("product", "inventory")


def request_resync(business_id, store_id, actor_user_id, connection_id, *, context=None, now=None):
    """Pull this connection's sync jobs forward. Returns what was queued.

    ``now`` is a parameter so a test can prove the jobs really moved rather than
    reading a clock that had advanced anyway.
    """
    policy.require_enabled()
    now = time.time() if now is None else now
    # On its own connection, before one is borrowed below: this DDL takes a lock,
    # and running it inside a transaction that has not committed is what leaves
    # the next connection waiting on it.
    worker.ensure_schema()
    conn = db.connect()
    try:
        # Ownership of the store *and* of the connection, decided by the same
        # function the status payload uses, so a connection a merchant cannot
        # read is also one they cannot schedule work against.
        _, seller_user_id = drafts._scope(conn, business_id, store_id, actor_user_id,
                                          connection_id, context=context, write=True)
        rows = conn.execute(
            "SELECT DISTINCT s.provider_product_id AS pid FROM marketplace_product_sources s "
            "WHERE s.seller_user_id=? AND s.supplier_connection_id=? "
            "AND s.business_id=? AND s.store_id=? AND s.provider_product_id IS NOT NULL "
            "AND s.provider_product_id<>'' ORDER BY pid LIMIT ?",
            (int(seller_user_id), connection_id, business_id, store_id, MAX_PRODUCTS + 1)
        ).fetchall()
        # One past the cap was asked for, so "there is more" is a fact rather
        # than an inference from a full page.
        truncated = len(rows) > MAX_PRODUCTS
        product_ids = [str(row["pid"]) for row in rows[:MAX_PRODUCTS]]

        for kind in _CONNECTION_KINDS:
            worker.schedule(connection_id=connection_id, business_id=business_id,
                            store_id=store_id, kind=kind, now=now, dirty=True, conn=conn)
        for pid in product_ids:
            for kind in _PRODUCT_KINDS:
                worker.schedule(connection_id=connection_id, business_id=business_id,
                                store_id=store_id, kind=kind, resource_id=pid,
                                now=now, dirty=True, conn=conn)
        conn.commit()
    finally:
        conn.close()

    return {
        "connection_id": connection_id,
        "queued_connection_checks": len(_CONNECTION_KINDS),
        "queued_products": len(product_ids),
        # Named for what the merchant is waiting on, not for rows written: the
        # `ON CONFLICT` above means a re-tap writes nothing and this still has to
        # report that the work is queued, because it is.
        "queued_jobs": len(_CONNECTION_KINDS) + len(product_ids) * len(_PRODUCT_KINDS),
        "truncated": truncated,
        "max_products": MAX_PRODUCTS,
        "requested_at": now,
    }
