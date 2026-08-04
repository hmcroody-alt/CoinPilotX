"""Storage for the fields a business profile needs and nothing else already holds.

Why new tables rather than more columns on the seller application
-----------------------------------------------------------------
The Business Profile screen currently reads its identity fields out of the *seller
application* (``services.seller_lifecycle``). That is the wrong store, and it is the
direct cause of the "Locked while your application is in review" banner: an
application is a point-in-time review artifact and it is **correct** for it to freeze
the moment it is submitted. A business profile is a living identity surface that has
to stay editable for the rest of the account's life, including — especially —
after approval. The two have different lifecycles, so they get different storage.

Why not ``business_os_business``
--------------------------------
``services.business_os.business.schema`` already defines a canonical business entity
with locations, members, policies and an audit trail, and it is the right long-term
home. It is also dark: every route behind it 404s unless ``BUSINESS_OS_BUSINESS`` is
explicitly set, and that variable appears in no deployment file in this repository.
Building the profile screen on top of it would ship a screen that returns 404 in
production. These tables are therefore keyed by ``user_id`` — which is what the app
actually has — and ``service.canonical_business_id`` records the link so a later
migration into the canonical row is a data move rather than a rewrite.

What is deliberately *not* stored here
--------------------------------------
* **Handle.** ``users.username`` owns it. Duplicating it would create two answers to
  "what is my handle" and guarantee they eventually disagree.
* **Logo, cover, display name, follower count.** The Pulse profile owns these. For a
  seller account the avatar *is* the business logo; adding a second image field would
  mean the screen has to decide which one a buyer sees, and it would eventually
  decide wrongly. The profile editor links to the existing image editor instead.
* **Seller type.** ``seller_lifecycle`` owns it. It is a classification the reviewer
  applied, not a category a buyer browses — which is precisely why the screen must
  stop printing "Individual" where a business category belongs.
* **Verification status.** ``verification_requests`` owns it; ``service`` resolves it
  but never writes a copy.
* **Legal address.** Deliberately absent — see ``business_os_seller_profile_addresses``
  below, which stores operational addresses only, and the note on ``legal`` there.

Portability: ``services.db`` is SQLite in development and PostgreSQL in production, so
every column here is TEXT or INTEGER, every timestamp is an ISO-8601 UTC string, and
no engine-specific default or type is used.
"""

from __future__ import annotations

from services import db


def ensure_schema(conn=None) -> None:
    """Create the profile tables if absent. Idempotent; safe to call at startup and
    from every test. Owns its connection unless one is passed in."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # One row per seller. Everything here is buyer-facing copy or a visibility
        # decision about buyer-facing copy; nothing here is a review artifact.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_seller_profile (
                user_id INTEGER PRIMARY KEY,
                business_name TEXT,
                legal_name TEXT,
                business_category TEXT,
                tagline TEXT,
                about TEXT,
                what_you_sell TEXT,
                service_area TEXT,
                shipping_summary TEXT,
                return_summary TEXT,
                response_expectations TEXT,
                languages_json TEXT,
                accessibility_json TEXT,
                public_city TEXT,
                public_region TEXT,
                public_country TEXT,
                support_email TEXT,
                support_email_visibility TEXT NOT NULL DEFAULT 'private',
                support_phone TEXT,
                support_phone_visibility TEXT NOT NULL DEFAULT 'private',
                preferred_contact TEXT NOT NULL DEFAULT 'message',
                response_hours TEXT,
                hours_mode TEXT NOT NULL DEFAULT 'unset',
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # Opening hours, one row per weekday actually configured. A missing row means
        # "not set", which is a different fact from "closed on Mondays" — the buyer
        # view says "Hours not provided" for the former and "Closed" for the latter,
        # so the two cannot share a representation.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_seller_profile_hours (
                user_id INTEGER NOT NULL,
                weekday TEXT NOT NULL,
                closed INTEGER NOT NULL DEFAULT 0,
                opens TEXT,
                closes TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, weekday)
            )
            """
        )

        # Dated overrides — public holidays, a week away, an early close. Checked
        # before the weekly pattern.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_seller_profile_hours_overrides (
                user_id INTEGER NOT NULL,
                on_date TEXT NOT NULL,
                closed INTEGER NOT NULL DEFAULT 1,
                opens TEXT,
                closes TEXT,
                label TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, on_date)
            )
            """
        )

        # Several links, not one. ``kind`` is validated against LINK_KINDS in service;
        # ``position`` is the display order the seller chose.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_seller_profile_links (
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                label TEXT,
                position INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, kind)
            )
            """
        )

        # Operational addresses, kept apart from the coarse public location on the
        # profile row. Splitting these is a privacy requirement, not tidiness: a
        # shipping origin is a warehouse, a pickup address is where a stranger is
        # invited to turn up, and neither should be published because the seller
        # once typed an address into an application form.
        #
        # No ``legal`` kind is offered. A legal/registered address is review evidence
        # and belongs with the verification documents, not in a table the profile
        # editor can write to.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_seller_profile_addresses (
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                line1 TEXT,
                line2 TEXT,
                city TEXT,
                region TEXT,
                postal_code TEXT,
                country TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, kind)
            )
            """
        )

        # Append-only. Identity edits on a verified business are exactly the changes a
        # reviewer will later be asked to justify, so the before/after of each one is
        # kept rather than reconstructed.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_seller_profile_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                before_value TEXT,
                after_value TEXT,
                requires_review INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seller_profile_audit_user "
            "ON business_os_seller_profile_audit (user_id, created_at)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
