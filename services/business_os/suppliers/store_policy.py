"""One store, one import policy: how imports get priced and whether they publish.

Why this module exists
----------------------
Before it, a pricing rule reached the importer exactly one way -- as
``pricing_rule`` on the HTTP request -- and its absence meant
:data:`pricing.MANUAL_PRICE`, which proposes nothing. That was the correct
default for a screen where the merchant is looking at the product and can type a
price. It is the wrong default for "Import to Store", where the merchant has
asked for a finished listing and is not on a pricing screen at all: the rule
resolved to MANUAL_PRICE, every variant was written with ``price_cents = None``,
and :func:`drafts._validate` answered ``MISSING_PRICE`` for a product nobody had
declined to price. The merchant then had to open an editor to supply a number
the store could have supplied for them.

So the rule now has a *store*, and a store has a policy. The request may still
override it -- the Import Cart's rule picker still works and still wins -- but
its silence no longer means "price nothing".

Not a second pricing engine
---------------------------
This module chooses a rule. It does not compute a price. :mod:`pricing` remains
the only thing that turns a cost into a number, and everything here returns a
dict that ``pricing.normalize_rule`` has validated, so a policy row cannot smuggle
in a rule shape the engine would reject. That split is the whole point of §9's
"do not scatter pricing formulas across import code": there is one formula
implementation, and this decides which of its named strategies a given store uses.

The platform default is a real number with a real derivation
------------------------------------------------------------
See :data:`PLATFORM_DEFAULT_TARGET_MARGIN`.

Shipping is declared by the store and by nobody else
----------------------------------------------------
A store may also declare a per-unit shipping allowance, which :mod:`pricing`
adds to the item cost so that a target margin is a margin on what the merchant
actually pays the supplier. Without it, a 45% margin on a cheap heavy product is
not 45% -- CJ bills freight on every order -- and the merchant is shown
``HEALTHY`` for a number that is arithmetically correct about the wrong quantity.

Unlike the pricing rule, its ladder has exactly **one rung**. Both the tiers
that are missing are missing on purpose:

* **No platform default.** A default *margin* is the platform stating its own
  policy, which it is entitled to do and which the merchant can see and change. A
  default *freight cost* would be the platform stating what a supplier charges to
  ship a product it has never quoted, to a destination nobody has named -- a fact
  about a supplier, invented, which is exactly what §1 forbids. So it stays
  ``None``, quotes stay on :data:`pricing.ITEM` basis, and the 45% default keeps
  doing the job its own docstring already claims: leaving room for a shipping
  estimate we could not read.
* **No request override.** A rule is a strategy and may be named per request; an
  allowance is a *cost*, and :mod:`importer`'s trust boundary is that the client
  sends ids, not costs. This was not reasoned out in advance -- the override was
  written, and ``test_import_selected_accepts_no_economic_input_from_the_caller``
  failed on the new parameter before it could reach a price, which is the
  sentence in that test's own comment.

There is also no import-time *estimate*, and there cannot be one: a CJ freight
quote needs a destination (``destAreaCode``, province, city, address), and at
import time no buyer exists. Freight becomes genuinely knowable only at
checkout, where :func:`fulfillment.estimate_shipping` quotes it for real. So this
is a declaration the merchant stands behind, never a figure PulseSoc guessed.
"""

from __future__ import annotations

import time

from services import db
from services.business_os.suppliers import connections, policy as feature_policy, pricing
from services.business_os.suppliers.errors import SupplierError

TABLE = "business_os_store_import_policy"

#: The margin the platform assumes when a store has expressed no preference.
#:
#: Derived, not picked. A sale at retail ``R`` on an item costing ``C`` pays,
#: before the merchant sees anything:
#:
#: * the PulseSoc platform fee -- ``bot.seller_fee_bps`` returns 1000 bps (10% of
#:   ``R``) for a non-teacher seller when ``platform_fee_rules`` has no active
#:   row, which is the state of the table for an ordinary merchant;
#: * card processing -- Stripe's usual 2.9% + $0.30.
#:
#: That is roughly 13% of retail gone before cost of goods. A target margin of
#: 45% sets ``R = C / 0.55`` (about 1.82x cost) and leaves the merchant near 32%
#: of retail after fees and before shipping -- positive with room for a shipping
#: estimate we could not read, which is what "conservative" has to mean here.
#: Conservative in this direction is *higher*, not lower: the failure this
#: default exists to avoid is an auto-published listing that loses money on every
#: sale, and a thin margin is how that happens silently.
#:
#: It also clears :data:`pricing.LOW_BELOW` (25), so an automatically priced
#: product reports ``HEALTHY`` rather than arriving in the merchant's store
#: wearing a LOW_MARGIN badge it did not earn.
#:
#: A merchant who wants keystone, or who sells a category where 45% is
#: uncompetitive, changes it per store. This number is only the answer to "nobody
#: has said", and it is defined once, here, so §10's "do NOT hardcode the same
#: percentage in random services" stays true.
PLATFORM_DEFAULT_TARGET_MARGIN = 45.0

#: The rule applied when neither the request nor the store names one.
PLATFORM_DEFAULT_RULE = {"type": pricing.TARGET_MARGIN,
                         "value": PLATFORM_DEFAULT_TARGET_MARGIN}

#: Where a resolved rule came from. Returned alongside the rule so the merchant's
#: import result can say *why* a product is priced the way it is, and so a test
#: can tell "the store chose 45" from "nobody chose and the platform did".
SOURCE_REQUEST = "REQUEST"
SOURCE_STORE = "STORE"
SOURCE_PLATFORM = "PLATFORM_DEFAULT"

#: Pass as ``shipping_allowance_cents`` to un-declare a store's allowance.
#:
#: ``set_policy`` reads ``None`` as "leave this field alone" for every field, so
#: the allowance -- whose cleared value *is* ``None`` -- needs a third value to
#: mean "clear it". A string rather than a module-level ``object()`` sentinel
#: because this one has to survive a JSON round trip: the settings screen sends
#: it in the PATCH body, and the route passes the body value straight through.
CLEAR_ALLOWANCE = "UNKNOWN"


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_schema(conn=None):
    """Create the policy table.

    ``business_id``/``store_id`` are TEXT to match every other Business OS table
    (``business_os_supplier_connections`` and friends). They are *not* the
    integer ``seller_user_id`` used by the marketplace tables, and mixing the two
    is a class of bug SQLite hides and Postgres raises at runtime.
    """
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            business_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            pricing_type TEXT NOT NULL DEFAULT '{pricing.TARGET_MARGIN}',
            pricing_value REAL,
            shipping_allowance_cents INTEGER,
            auto_publish INTEGER NOT NULL DEFAULT 1,
            marketplace_autolist INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (business_id, store_id)
        )""")
        if owned:
            conn.commit()
            _add_shipping_allowance_column(conn)
    except Exception:
        if owned:
            conn.rollback()
        raise
    finally:
        if owned:
            conn.close()


def _add_shipping_allowance_column(conn):
    """Add the allowance column to a table that predates it.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op on every database that already has
    this table, which is every database that has ever imported a product -- so
    the column in the DDL above only ever reaches a fresh one, and the test suite
    (fresh SQLite every run) would prove a shape production does not have.

    **Only ever called on a connection this module owns.** ``ensure_schema`` is
    also called with a borrowed connection from inside ``get_policy``, and both
    ways of running this there are traps: a failed ``ALTER`` poisons the whole
    transaction on PostgreSQL, and the ``commit`` that would clear it ends a
    transaction the caller is still using. The importer calls ``resolve_rule``
    mid-write, so that caller is real.

    A database that therefore never runs this reads as a store with no allowance
    -- ``_row``'s ``SELECT *`` simply has no such key -- which is the correct and
    safe answer rather than an error. The only path that *needs* the column is
    :func:`write`, which owns its connection and migrates before using it.
    """
    try:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN shipping_allowance_cents INTEGER")
        conn.commit()
    except Exception:
        # `ADD COLUMN IF NOT EXISTS` is PostgreSQL-only (`db._translate_alter_table`
        # injects it), so on SQLite the duplicate column is caught rather than
        # declared away.
        conn.rollback()


def _row(conn, business_id, store_id):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {TABLE} WHERE business_id=? AND store_id=? LIMIT 1",
                (str(business_id), str(store_id)))
    row = cur.fetchone()
    return dict(row) if row is not None else None


def get_policy(conn, business_id, store_id) -> dict:
    """This store's import policy, falling back to the platform's.

    A store that has never been configured is not an error and does not get a
    half-populated dict: it gets the platform answer, flagged as such.
    """
    ensure_schema(conn)
    row = _row(conn, business_id, store_id)
    if row is None:
        return {
            "pricing_rule": dict(PLATFORM_DEFAULT_RULE),
            "pricing_source": SOURCE_PLATFORM,
            # None, not 0. An unconfigured store has not told us shipping is
            # free; it has told us nothing, and those two produce very different
            # margins on a heavy product.
            "shipping_allowance_cents": None,
            "shipping_allowance_source": SOURCE_PLATFORM,
            "auto_publish": True,
            "marketplace_autolist": False,
            "configured": False,
        }
    try:
        rule = pricing.normalize_rule({"type": row.get("pricing_type"),
                                       "value": row.get("pricing_value")})
    except pricing.PricingRejected:
        # A stored rule that no longer validates -- a value written before a
        # bound changed, or a hand-edited row. Fall back rather than raise: the
        # merchant asked to import a product, and refusing the whole import
        # because their saved preference is stale would be the wrong casualty.
        rule = dict(PLATFORM_DEFAULT_RULE)
    # `normalize_shipping` rather than a cast, and `.get` rather than `[...]`:
    # the column is absent on a database that has not been migrated, and a stored
    # value can be stale in the same way a stored rule can. Both answer None,
    # which downgrades the store to the item basis rather than failing the read.
    allowance = pricing.normalize_shipping(row.get("shipping_allowance_cents"))
    return {
        "pricing_rule": rule,
        "pricing_source": SOURCE_STORE,
        "shipping_allowance_cents": allowance,
        "shipping_allowance_source": SOURCE_STORE if allowance is not None
        else SOURCE_PLATFORM,
        "auto_publish": bool(row.get("auto_publish", 1)),
        "marketplace_autolist": bool(row.get("marketplace_autolist", 0)),
        "configured": True,
    }


def resolve_rule(conn, business_id, store_id, requested=None) -> tuple[dict, str]:
    """The rule this import prices with, and where it came from.

    Priority is §8's, exactly: the merchant's explicit request, then the store's
    configured policy, then the platform default.

    An explicitly requested ``MANUAL_PRICE`` is honoured as a request and is not
    replaced by the store default. A merchant who picks "I'll price these myself"
    in the cart means it, and quietly pricing their import anyway because the
    store has a policy would be the system overruling a choice the merchant just
    made on screen. That import lands unpriced and stops at needs-attention,
    which is the correct outcome for someone who asked to set the prices.
    """
    if requested is not None:
        return pricing.normalize_rule(requested), SOURCE_REQUEST
    current = get_policy(conn, business_id, store_id)
    return current["pricing_rule"], current["pricing_source"]


def resolve_shipping_allowance(conn, business_id, store_id) -> tuple[int | None, str]:
    """The per-unit shipping allowance this store prices with, and its source.

    §8's ladder with **both** end rungs missing, and each absence has its own
    reason.

    There is no platform rung because a default freight cost would be the
    platform inventing a fact about a supplier -- see the module docstring.

    There is no *request* rung, which is the asymmetry with
    :func:`resolve_rule` and the more surprising of the two. A pricing rule is a
    strategy and may be named per request; an allowance is a **cost**, and
    :mod:`importer`'s trust boundary is that the client does not send costs. So
    this takes no ``requested`` parameter at all rather than taking one and
    documenting that nobody should pass it -- a parameter that exists is a
    parameter a route will eventually forward.

    That leaves one rung, so the signature has no override and the answer is
    always the store's saved policy: set on the settings screen, authenticated,
    persisted, attributable.
    """
    current = get_policy(conn, business_id, store_id)
    return current["shipping_allowance_cents"], current["shipping_allowance_source"]


def set_policy(conn, business_id, store_id, *, pricing_rule=None,
               auto_publish=None, marketplace_autolist=None,
               shipping_allowance_cents=None) -> dict:
    """Write this store's policy. Only the named fields change.

    ``None`` means "leave alone" rather than "clear", so a caller updating the
    Marketplace toggle cannot blank the pricing rule by omission. Which leaves
    the allowance with no way to be *un*-declared, since its cleared value is
    also ``None`` -- hence :data:`CLEAR_ALLOWANCE`, a distinct and
    JSON-representable third thing.

    An unusable allowance raises rather than resolving to the current value. That
    is the opposite of :func:`resolve_shipping_allowance`'s judgement and for the
    opposite reason: there, a merchant is importing products and a bad number
    should not cost them the import; here, a merchant is on a settings screen
    typing that number, and silently keeping the old one would show them a saved
    policy that is not the one they just entered.
    """
    ensure_schema(conn)
    existing = _row(conn, business_id, store_id)
    current = get_policy(conn, business_id, store_id)

    rule = current["pricing_rule"] if pricing_rule is None \
        else pricing.normalize_rule(pricing_rule)
    publish = current["auto_publish"] if auto_publish is None else bool(auto_publish)
    autolist = current["marketplace_autolist"] if marketplace_autolist is None \
        else bool(marketplace_autolist)
    if shipping_allowance_cents is None:
        allowance = current["shipping_allowance_cents"]
    elif shipping_allowance_cents == CLEAR_ALLOWANCE:
        allowance = None
    else:
        allowance = pricing.normalize_shipping(shipping_allowance_cents)
        if allowance is None:
            raise pricing.PricingRejected(
                "shipping allowance must be a whole number of cents, zero or more")

    now = _iso()
    if existing is None:
        conn.execute(
            f"INSERT INTO {TABLE} (business_id, store_id, pricing_type, pricing_value, "
            "shipping_allowance_cents, auto_publish, marketplace_autolist, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (str(business_id), str(store_id), rule.get("type"), rule.get("value"),
             allowance, 1 if publish else 0, 1 if autolist else 0, now, now))
    else:
        conn.execute(
            f"UPDATE {TABLE} SET pricing_type=?, pricing_value=?, "
            "shipping_allowance_cents=?, auto_publish=?, "
            "marketplace_autolist=?, updated_at=? WHERE business_id=? AND store_id=?",
            (rule.get("type"), rule.get("value"), allowance, 1 if publish else 0,
             1 if autolist else 0, now, str(business_id), str(store_id)))
    return {
        "pricing_rule": rule,
        "pricing_source": SOURCE_STORE,
        "shipping_allowance_cents": allowance,
        "shipping_allowance_source": SOURCE_STORE if allowance is not None
        else SOURCE_PLATFORM,
        "auto_publish": publish,
        "marketplace_autolist": autolist,
        "configured": True,
    }


# ---------------------------------------------------------------------------
# Authorized entry points
#
# The functions above take a connection and trust it: they are called from
# inside :func:`importer.import_selected`, which has already proved the caller
# owns the store. A route has proved nothing, so it may not call them.
#
# Ownership is proved with ``connections._authorize`` -- the same call the
# importer makes, with the same ``write`` flag semantics -- rather than a fresh
# permission check written here. A store's import policy decides how that
# store's products are priced and whether they go live, so "may I edit this
# policy" has to be exactly "may I import into this store"; two implementations
# of that question is how one of them ends up laxer than the other.
# ---------------------------------------------------------------------------

def read(business_id, store_id, actor_user_id, *, context=None) -> dict:
    """This store's import policy, for a caller that has proved nothing yet."""
    feature_policy.require_enabled()
    conn = db.connect()
    try:
        connections._authorize(conn, business_id, store_id, actor_user_id,
                               context=context, write=False)
        return get_policy(conn, business_id, store_id)
    finally:
        conn.close()


def write(business_id, store_id, actor_user_id, *, pricing_rule=None, auto_publish=None,
          marketplace_autolist=None, shipping_allowance_cents=None, context=None) -> dict:
    """Update this store's import policy. Omitted fields keep their current value.

    A rule the pricing engine rejects is a 400 naming the rule, not a generic
    failure: the merchant typed a number and is entitled to know it was the
    number that was refused. ``pricing.PricingRejected`` carries no HTTP code of
    its own, so left untranslated it would surface as ``supplier_unavailable``
    and the settings screen would tell them the supplier was down.

    The allowance gets its own code for the same reason one level down. Both it
    and the rule raise ``PricingRejected`` from ``set_policy``, so catching that
    alone would tell a merchant who mistyped their shipping cost that their
    *pricing rule* was invalid -- and the rule is the field they did not touch.
    It is therefore checked here, before anything is opened, rather than
    disentangled from the exception afterwards.
    """
    feature_policy.require_enabled()
    if shipping_allowance_cents is not None \
            and shipping_allowance_cents != CLEAR_ALLOWANCE \
            and pricing.normalize_shipping(shipping_allowance_cents) is None:
        raise SupplierError("invalid_shipping_allowance", http_status=400)
    # Owned connection, so the column migration can run. This is the only path
    # that writes the column, and `_add_shipping_allowance_column` may not run on
    # the borrowed connections `get_policy` is called with.
    ensure_schema()
    conn = db.connect()
    try:
        connections._authorize(conn, business_id, store_id, actor_user_id,
                               context=context, write=True)
        try:
            result = set_policy(conn, business_id, store_id, pricing_rule=pricing_rule,
                                auto_publish=auto_publish,
                                marketplace_autolist=marketplace_autolist,
                                shipping_allowance_cents=shipping_allowance_cents)
        except pricing.PricingRejected as exc:
            raise SupplierError("invalid_pricing_rule", http_status=400) from exc
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
