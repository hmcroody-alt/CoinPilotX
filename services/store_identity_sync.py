"""Keep the one public store name in one place when several screens edit it.

``services/marketplace_seller_identity`` settles *which column* is the public
store name: ``marketplace_sellers.display_name``, and nothing else. That answers
reads. It does not answer writes, and writes are where the name actually split.

Three surfaces let an owner type a public name:

* the merchant application's "Your storefront" step, which writes
  ``marketplace_sellers.display_name`` — the canonical column;
* the Business Profile screen, whose field is labelled "Business name" with the
  placeholder "What buyers should call you" and which writes
  ``business_os_profiles.business_name`` — a different table entirely;
* Business OS, which carries its own ``business_os_business.display_name``.

Each one is a reasonable place to rename a shop, so an owner renames in
whichever they happen to open, and the marketplace keeps showing whatever the
application captured months earlier. Nothing errors. The names simply drift, and
the buyer-facing one is the one nobody edits.

This module makes the Business Profile edit a write *through* to the canonical
column rather than a write *beside* it. Two deliberate limits:

``adopt_store_name`` only ever UPDATEs. Creating a ``marketplace_sellers`` row
here would mint a seller account as a side effect of editing a profile, which is
exactly the "silently divergent second authority" this is meant to remove — and
it would hand someone a seller row without an approved application behind it.

It also does not touch ``business_name``/``legal_name`` anywhere. Those are the
registered legal identity, kept private-side on purpose; promoting one to a shop
sign is the leak that ``marketplace_seller_identity`` documents at length.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

#: Matches ``NAME_MAX`` in ``services/business_os/profile/service.py``. The
#: profile service has already clamped anything arriving from that screen; this
#: is the backstop for any other caller.
STORE_NAME_MAX = 120


def normalize_store_name(value: Any) -> str:
    """Collapse to the exact string the canonical column should hold."""
    text = " ".join(str(value or "").split())
    return text[:STORE_NAME_MAX].strip()


def canonical_store_name(cur, user_id: Any) -> str:
    """The public store name of record, or "" when there is none."""
    try:
        cur.execute(
            "SELECT display_name FROM marketplace_sellers WHERE user_id=? LIMIT 1",
            (int(user_id or 0),),
        )
        row = cur.fetchone()
    except Exception:
        logging.exception("[store-identity] canonical read failed")
        return ""
    if not row:
        return ""
    try:
        value = row["display_name"]
    except (TypeError, KeyError, IndexError):
        value = row[0] if row else ""
    return normalize_store_name(value)


def adopt_store_name(cur, user_id: Any, name: Any) -> Optional[str]:
    """Point the canonical column at ``name``. Returns the stored value, or None.

    None means "nothing was changed", and covers three different situations on
    purpose — an empty name, a user with no seller row, and a name that already
    matches. None of them is an error the caller should surface: the owner's
    profile edit succeeded either way, and refusing it because they have no
    seller account yet would block the ordinary case of writing a profile before
    applying to sell.

    Blank input never clears the canonical name. An empty store name is the one
    state that takes listings off sale (``marketplace_listing_lifecycle``), so a
    screen that submits a partial payload must not be able to cause it.
    """
    cleaned = normalize_store_name(name)
    if not cleaned:
        return None
    try:
        current = canonical_store_name(cur, user_id)
        if current == cleaned:
            return None
        cur.execute(
            "UPDATE marketplace_sellers SET display_name=? WHERE user_id=?",
            (cleaned, int(user_id or 0)),
        )
        if not getattr(cur, "rowcount", 0):
            return None
    except Exception:
        logging.exception("[store-identity] canonical write failed")
        return None
    return cleaned
