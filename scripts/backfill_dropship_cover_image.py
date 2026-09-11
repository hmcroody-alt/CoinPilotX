"""Backfill ``cover_image_url`` on dropship listings imported before it was set.

DRY RUN BY DEFAULT. Pass ``--apply`` to write. Nothing here runs automatically.

Why a backfill exists at all
----------------------------
``importer._insert_listing`` writes the cover column and the metadata media list
from one local list, and ``update_draft`` writes both together. Rows created
before that landed have the media only in ``listing_metadata_json.media``, and
every reader of the *column* -- ``drafts.list_drafts`` (the merchant's
Dropshipping products list), the merchant store list, and the buyer grid via
``bot.pulse_marketplace_listing_payload`` -- sees NULL and draws a blank tile.

``drafts.publish`` now also writes the column, so such a row repairs itself the
moment it is published. This script is for the window before that: a merchant
looking at their own products list should not be shown a blank tile for a
product that has five photos.

What it will and will not touch
-------------------------------
Only rows where **both** are true: the metadata carries at least one media URL,
and the column is NULL or empty. It never overwrites a cover that is already
set, never invents a URL, and never touches any other column. The value written
is ``metadata["media"][0]`` -- the same expression ``get_draft`` already reports
as the cover and ``publish`` now writes, so the three agree by construction.

Usage:
    railway run --service Postgres -- .venv/bin/python3 \\
        scripts/backfill_dropship_cover_image.py            # dry run
    railway run --service Postgres -- .venv/bin/python3 \\
        scripts/backfill_dropship_cover_image.py --apply    # writes
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2
import psycopg2.extras


def candidates(cur):
    """Rows whose metadata has media and whose cover column does not."""
    cur.execute(
        "SELECT id, title, status, cover_image_url, listing_metadata_json "
        "FROM marketplace_listings "
        "WHERE COALESCE(cover_image_url,'') = '' "
        "  AND listing_metadata_json IS NOT NULL "
        "ORDER BY id")
    found = []
    for row in cur.fetchall():
        try:
            meta = json.loads(row["listing_metadata_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        media = [m for m in (meta.get("media") or []) if isinstance(m, str) and m.strip()]
        if not media:
            continue
        found.append((dict(row), media[0]))
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="actually write; omit for a dry run")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("No DATABASE_URL/DATABASE_PUBLIC_URL in the environment.")
        return 2

    conn = psycopg2.connect(url)
    conn.set_session(readonly=not args.apply, autocommit=False)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    rows = candidates(cur)
    print("%s -- %d listing(s) with metadata media and no cover column"
          % ("APPLY" if args.apply else "DRY RUN", len(rows)))
    for row, cover in rows:
        print("  listing %-6s status=%-10s %s" % (row["id"], row["status"], row["title"][:48]))
        print("           cover would become: %s" % cover)

    if not rows:
        conn.close()
        return 0
    if not args.apply:
        print("\nNothing written. Re-run with --apply to write these values.")
        conn.close()
        return 0

    for row, cover in rows:
        # Guarded on the empty cover a second time so a concurrent write between
        # the SELECT and here wins rather than being clobbered.
        cur.execute(
            "UPDATE marketplace_listings SET cover_image_url=%s "
            "WHERE id=%s AND COALESCE(cover_image_url,'')=''",
            (cover, row["id"]))
        print("  listing %s: %d row(s) updated" % (row["id"], cur.rowcount))
    conn.commit()
    print("\nCommitted.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
