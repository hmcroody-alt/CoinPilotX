"""Canonical buyer-facing seller identity for PulseSoc Marketplace.

A buyer transacts with a *store*, not with a person. The account holder's name
is private-side identity: it belongs in admin tooling, payouts, and the seller's
own dashboard, and nowhere on a buyer surface. Every marketplace query used to
select ``COALESCE(u.display_name, u.username, 'PulseSoc Seller')`` from the
``users`` table, which is why a shop trading as "Roody's Shop" was showing its
owner's legal name to buyers on the product page, in the cart, and on receipts.

Where the store name actually lives
-----------------------------------
``marketplace_sellers`` is the seller-account record: one row per user, created
by the merchant application in ``services/seller_lifecycle.py`` and the row whose
``status`` gates publication. Its ``display_name`` is the storefront name — the
application step that writes it is titled "Your storefront", summarised as "The
name buyers will see, and what you sell", and validated with "Enter the name
buyers will see."

So the authority is exactly one column:

    marketplace_sellers.display_name

and nothing else. Two fallbacks that look reasonable are deliberately absent.

``users.display_name``/``username`` is the original bug: falling back to a
personal name is not a safety net, it is the leak. A seller row with no store
name is a data defect to be surfaced and repaired (see
``scripts/marketplace_store_identity_audit.py``), not papered over.

``marketplace_sellers.business_name`` is the *registered legal* business name,
collected only from brand and agency seller types for compliance. It used to sit
in this chain as a second-choice public name, which quietly made a company's
legal identity a buyer-facing one: a sole trader registered as their own name
("Roody Cherie Ltd") leaks exactly what the ``users`` fallback leaked, just by a
longer route. Legal name is private-side identity — Stripe, tax, and review
evidence — and is never promoted to a shop sign automatically. Read it through
:func:`legal_business_name`, which is deliberately not usable by the buyer-facing
helpers below.

``pulsesoc_seller_stores.store_name`` exists but is deliberately not used here.
It is written and read only by ``services/pulsesoc_dashboard_centers.py``, is
never joined to ``marketplace_listings``, and its table is created lazily by
that module — joining it into hot buyer queries would risk "relation does not
exist" on any instance where the dashboard has not been opened.

The wire contract
-----------------
Buyer payloads carry ``seller_store_name`` explicitly. The native client reads
that one field and never has to guess which of a dozen name-shaped keys is the
business. ``seller_name`` is kept as an alias of the same value so existing
consumers keep working and cannot drift back to the personal name.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Shown when a seller row genuinely carries no store name. Deliberately
#: generic: a placeholder is a smaller failure than leaking a personal name.
FALLBACK_STORE_NAME = "PulseSoc Store"

#: The keys a row may carry *public* store identity under. Used to tell "this
#: seller has no store name" apart from "this query never selected one".
#: ``business_name`` is absent by design — see the module docstring.
IDENTITY_KEYS = ("seller_store_name", "store_name", "display_name")


def store_name_sql(seller_alias: str = "ms") -> str:
    """SQL for the canonical store name, or NULL when the seller has none.

    NULL rather than a fallback string, so callers can still tell "no store
    identity" apart from "a store literally named PulseSoc Store" — the
    publication invariant depends on that distinction.
    """
    return f"NULLIF(TRIM({seller_alias}.display_name),'')"


def store_name_select(seller_alias: str = "ms", column: str = "seller_store_name") -> str:
    """The ``SELECT`` fragment every buyer-facing marketplace query should use."""
    return f"{store_name_sql(seller_alias)} AS {column}"


def store_name(row: Mapping[str, Any] | None) -> str:
    """Resolve the store name from a row, in Python, with the same authority.

    Accepts either an already-projected ``seller_store_name`` or the raw
    ``marketplace_sellers`` columns, so callers that fetch the seller record
    directly get the identical answer as callers that joined it. Returns "" when
    there is no store identity — presentation is :func:`display_store_name`'s
    job, and keeping them separate is what lets the invariant check work.
    """
    row = dict(row or {})
    for key in IDENTITY_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def legal_business_name(row: Mapping[str, Any] | None) -> str:
    """The registered legal name, for private-side surfaces only.

    Separate function, separate column, no overlap with :func:`store_name` — so
    that "this is the legal name" is a decision a caller has to make on purpose
    and a reviewer can grep for. Never call this to fill a buyer-facing field.
    """
    return str((row or {}).get("business_name") or "").strip()


def display_store_name(row: Mapping[str, Any] | None) -> str:
    """The string a buyer sees. Never a personal name, never empty."""
    return store_name(row) or FALLBACK_STORE_NAME


def has_store_identity(row: Mapping[str, Any] | None) -> bool:
    """Whether this seller has the public identity a listing needs to go live."""
    return bool(store_name(row))


def store_identity_known(row: Mapping[str, Any] | None) -> bool:
    """Whether the row was projected with enough columns to judge identity.

    A row from a query that never selected a store-name column tells us nothing;
    treating that silence as "no store" would take healthy listings off sale on
    any code path that happens to fetch fewer columns. Callers enforcing the
    publication invariant check this first and skip the check when unknown — the
    SQL form in ``store_name_sql`` is where the invariant is actually binding.
    """
    return any(key in (row or {}) for key in IDENTITY_KEYS)
