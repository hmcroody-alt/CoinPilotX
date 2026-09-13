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
            auto_publish INTEGER NOT NULL DEFAULT 1,
            marketplace_autolist INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (business_id, store_id)
        )""")
        if owned:
            conn.commit()
    except Exception:
        if owned:
            conn.rollback()
        raise
    finally:
        if owned:
            conn.close()


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
    return {
        "pricing_rule": rule,
        "pricing_source": SOURCE_STORE,
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


def set_policy(conn, business_id, store_id, *, pricing_rule=None,
               auto_publish=None, marketplace_autolist=None) -> dict:
    """Write this store's policy. Only the named fields change.

    ``None`` means "leave alone" rather than "clear", so a caller updating the
    Marketplace toggle cannot blank the pricing rule by omission.
    """
    ensure_schema(conn)
    existing = _row(conn, business_id, store_id)
    current = get_policy(conn, business_id, store_id)

    rule = current["pricing_rule"] if pricing_rule is None \
        else pricing.normalize_rule(pricing_rule)
    publish = current["auto_publish"] if auto_publish is None else bool(auto_publish)
    autolist = current["marketplace_autolist"] if marketplace_autolist is None \
        else bool(marketplace_autolist)

    now = _iso()
    if existing is None:
        conn.execute(
            f"INSERT INTO {TABLE} (business_id, store_id, pricing_type, pricing_value, "
            "auto_publish, marketplace_autolist, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(business_id), str(store_id), rule.get("type"), rule.get("value"),
             1 if publish else 0, 1 if autolist else 0, now, now))
    else:
        conn.execute(
            f"UPDATE {TABLE} SET pricing_type=?, pricing_value=?, auto_publish=?, "
            "marketplace_autolist=?, updated_at=? WHERE business_id=? AND store_id=?",
            (rule.get("type"), rule.get("value"), 1 if publish else 0,
             1 if autolist else 0, now, str(business_id), str(store_id)))
    return {
        "pricing_rule": rule,
        "pricing_source": SOURCE_STORE,
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
          marketplace_autolist=None, context=None) -> dict:
    """Update this store's import policy. Omitted fields keep their current value.

    A rule the pricing engine rejects is a 400 naming the rule, not a generic
    failure: the merchant typed a number and is entitled to know it was the
    number that was refused. ``pricing.PricingRejected`` carries no HTTP code of
    its own, so left untranslated it would surface as ``supplier_unavailable``
    and the settings screen would tell them the supplier was down.
    """
    feature_policy.require_enabled()
    conn = db.connect()
    try:
        connections._authorize(conn, business_id, store_id, actor_user_id,
                               context=context, write=True)
        try:
            result = set_policy(conn, business_id, store_id, pricing_rule=pricing_rule,
                                auto_publish=auto_publish,
                                marketplace_autolist=marketplace_autolist)
        except pricing.PricingRejected as exc:
            raise SupplierError("invalid_pricing_rule", http_status=400) from exc
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
