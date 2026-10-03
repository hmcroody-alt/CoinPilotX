"""Measure the social x commerce graph that actually exists.

Read-only. Prints one row per question so the numbers in
``docs/search_os/AGENT_10_SOCIAL_COMMERCE_GRAPH.md`` can be re-derived instead of
trusted -- they move every time Pulse Loop publishes, which is hourly.

    railway run --service Postgres .venv/bin/python \
        scripts/search_os/agent_10_graph_inventory.py

``DATABASE_URL`` points at ``postgres.railway.internal`` and does not resolve off
Railway, so the reachable DSN is ``DATABASE_PUBLIC_URL`` and it lives on the
``Postgres`` service rather than on the web service.

The session is opened read-only. That is the guard, not a comment asking for one:
every statement here is a SELECT, and a future edit that is not gets refused by
the server rather than by review.

Two edge tables, and the distinction is the whole point of the document:

``pulse_content_products``   a creator's statement that their post is about their
                             product. Written by the composer through
                             ``commerce_discovery.tagging.attach``, which refuses
                             any listing the author does not own.
``pulsedrop_publications``   PulseDrop's editorial decision to publish a promo
                             for a listing. Machine-authored. PulseDrop is the
                             publisher, never the merchant.

Both resolve to a canonical ``marketplace_listings.id``. Neither snapshots price.
"""

from __future__ import annotations

import json
import os
import sys

QUERIES: tuple[tuple[str, str], ...] = (
    (
        "totals",
        """
        SELECT (SELECT COUNT(*) FROM users) AS users,
               (SELECT COUNT(*) FROM marketplace_listings
                 WHERE status='published') AS published_listings,
               (SELECT COUNT(DISTINCT seller_user_id) FROM marketplace_listings
                 WHERE status='published') AS distinct_sellers,
               (SELECT COUNT(*) FROM pulse_content_products) AS creator_tag_edges,
               (SELECT COUNT(*) FROM pulsedrop_publications
                 WHERE state='published') AS pulsedrop_edges,
               (SELECT COUNT(DISTINCT listing_id) FROM pulsedrop_publications
                 WHERE state='published') AS listings_with_pulsedrop
        """,
    ),
    (
        # The ceiling on the creator-tagged graph. `tagging.attach` refuses a
        # listing the author does not own, so only a seller can draw this edge --
        # which makes this count, not user enthusiasm, the binding constraint.
        "users who could create a creator-tag edge at all (own >=1 published listing)",
        """
        SELECT u.user_id, u.username,
               COUNT(DISTINCT l.id) AS listings,
               COUNT(DISTINCT po.id) AS public_posts
        FROM users u
        JOIN marketplace_listings l
          ON l.seller_user_id = u.user_id AND l.status='published'
        LEFT JOIN pulse_posts po
          ON po.user_id = u.user_id
         AND po.deleted_at IS NULL
         AND po.visibility='public'
        GROUP BY 1, 2 ORDER BY listings DESC
        """,
    ),
    (
        "published listings carrying no social edge of any kind",
        """
        SELECT COUNT(*) AS listings_with_zero_social_edges
        FROM marketplace_listings l
        WHERE l.status='published'
          AND NOT EXISTS (SELECT 1 FROM pulsedrop_publications p
                           WHERE p.listing_id=l.id AND p.state='published')
          AND NOT EXISTS (SELECT 1 FROM pulse_content_products c
                           WHERE c.listing_id=l.id)
        """,
    ),
    (
        # Search quality, not integrity. A product whose only "social context" is
        # four machine promos of itself has no social context.
        "publications per listing",
        """
        SELECT pubs, COUNT(*) AS listings FROM (
            SELECT listing_id, COUNT(*) AS pubs
            FROM pulsedrop_publications WHERE state='published'
            GROUP BY listing_id
        ) t GROUP BY pubs ORDER BY pubs DESC
        """,
    ),
    (
        # The duplicate-content measurement. A promo whose title is the product
        # title verbatim is a second page competing with the PDP for the same
        # query, and these are served index,follow and sitemapped.
        "publications whose post title equals the listing title verbatim",
        """
        SELECT COUNT(*) AS exact_title_duplicates,
               (SELECT COUNT(*) FROM pulsedrop_publications
                 WHERE state='published') AS of_published_total
        FROM pulsedrop_publications p
        JOIN pulse_posts po ON po.id = p.post_id AND po.deleted_at IS NULL
        JOIN marketplace_listings l ON l.id = p.listing_id
        WHERE TRIM(COALESCE(po.title,'')) <> ''
          AND TRIM(po.title) = TRIM(COALESCE(l.title,''))
        """,
    ),
    (
        "ORPHAN: edge pointing at a listing row that is gone",
        """
        SELECT 'pulsedrop' AS edge, COUNT(*) AS orphans
        FROM pulsedrop_publications p
        LEFT JOIN marketplace_listings l ON l.id = p.listing_id
        WHERE p.listing_id > 0 AND l.id IS NULL
        UNION ALL
        SELECT 'creator_tag', COUNT(*)
        FROM pulse_content_products c
        LEFT JOIN marketplace_listings l ON l.id = c.listing_id
        WHERE l.id IS NULL
        """,
    ),
    (
        # A publication outliving its post is the case the PDP reverse projection
        # would have to filter, if one existed.
        "ORPHAN: publication whose post is deleted or not public",
        """
        SELECT COALESCE(po.visibility,'<<post row missing>>') AS visibility,
               (po.deleted_at IS NOT NULL) AS deleted,
               COUNT(*) AS n
        FROM pulsedrop_publications p
        LEFT JOIN pulse_posts po ON po.id = p.post_id
        WHERE p.post_id IS NOT NULL AND p.post_id > 0
        GROUP BY 1, 2 ORDER BY n DESC
        """,
    ),
    (
        # `hidden_from_discovery` means "keep out of in-product discovery". It has
        # never meant "do not index", and these posts are reachable anonymously at
        # /pulse/post/<id>. Reported so the difference stays deliberate.
        "public posts authored by a hidden or non-active account",
        """
        SELECT u.user_id, u.username, u.hidden_from_discovery,
               COALESCE(u.account_status,'') AS account_status, COUNT(*) AS public_posts
        FROM pulse_posts po JOIN users u ON u.user_id = po.user_id
        WHERE po.deleted_at IS NULL AND po.visibility='public'
          AND (COALESCE(u.hidden_from_discovery,0)=1
               OR COALESCE(u.account_status,'') IN
                  ('deleted','suspended','banned','disabled','disabled_qa'))
        GROUP BY 1, 2, 3, 4 ORDER BY public_posts DESC
        """,
    ),
)


def main() -> int:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "No DSN. Run under: railway run --service Postgres -- "
            "<venv>/python scripts/search_os/agent_10_graph_inventory.py\n"
        )
        return 2

    import psycopg2
    import psycopg2.extras

    with psycopg2.connect(dsn) as conn:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for label, sql in QUERIES:
            print(f"\n### {label}")
            try:
                cur.execute(sql)
                rows = [dict(row) for row in cur.fetchall()]
            except Exception as exc:  # a missing table is an answer, not a crash
                print(f"!! {type(exc).__name__}: {exc}")
                continue
            print(json.dumps(rows, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
