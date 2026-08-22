#!/usr/bin/env python3
"""Detach generated media from PulseSoc Insight posts, keeping the posts intact.

Automated Insight posts are text-only. Some historical ones carry a generated
image whose asset no longer resolves, which the web feed paints as a large black
rectangle rather than nothing at all.

This detaches media from the automated account's posts. It never deletes a post,
a comment, a reaction or a storage object -- and it never touches a post authored
by a real user, because the whole target set is pinned to `user_id = 0`.

Three sources of truth decide whether the feed shows media for a post, and
`_media_for_posts` reads them with OR, not AND:

    WHERE ((context_type IN ('pulse','pulse_post') AND context_id IN (...))
           OR id IN (<media_ids_json>))

so clearing `media_ids_json` alone leaves the row reachable by `context_id` and
the black box survives. All three are neutralized here.

Media rows are retired by moving `context_type` to a parked value rather than by
deleting them: the row, its storage key and its `context_id` all survive, so the
change is auditable and reversible, and no storage object is orphaned by a
DELETE whose ownership was never proven.

Dry run by default. Pass --apply to write.

    python3 scripts/cleanup_automated_post_media.py            # report only
    python3 scripts/cleanup_automated_post_media.py --apply    # detach
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The DB handle is opened through `services.db` rather than `bot`. Importing
# `bot` runs `initialize_database_for_web_startup()` at module scope, so a
# read-only survey would boot the entire web app and create schema as a side
# effect -- and on a database that already contains the automated account it
# fails outright, because `pulse_id_service.ensure_schema` rejects `user_id=0`.
# `services.db.connect()` is the documented accessor, works against both SQLite
# and the production Postgres, and translates `?` placeholders for either.
#
# The import stays inside main() so survey() and apply_cleanup() remain pure
# cursor logic that tests can drive against an in-memory database.
AUTOMATED_USER_ID = 0
RETIRED_CONTEXT_TYPE = "pulse_detached_automated"
FEED_CONTEXT_TYPES = ("pulse", "pulse_post")


def _rows(cur):
    return [dict(row) for row in cur.fetchall()]


def _media_ids(raw):
    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    return [int(value) for value in parsed if str(value).isdigit()]


def survey(cur):
    """Read-only classification of the automated account's posts."""
    cur.execute(
        "SELECT id, post_type, media_ids_json FROM pulse_posts WHERE user_id=?",
        (AUTOMATED_USER_ID,),
    )
    posts = _rows(cur)
    total = len(posts)

    by_id = {}
    for post in posts:
        by_id[int(post["id"])] = {
            "post_type": str(post.get("post_type") or ""),
            "media_ids": _media_ids(post.get("media_ids_json")),
        }
    if not by_id:
        return {"total": 0, "with_media": [], "valid": 0, "broken": 0, "media_rows": []}

    placeholders = ",".join(["?"] * len(by_id))
    ids = list(by_id)
    # Same OR-shape the feed uses, so the survey sees exactly what a reader sees.
    cur.execute(
        f"""SELECT id, context_id, context_type, media_url, media_type
            FROM chat_media_uploads
            WHERE context_type IN ('pulse','pulse_post') AND context_id IN ({placeholders})""",
        [str(value) for value in ids],
    )
    media_rows = _rows(cur)

    attached_media_ids = {mid for entry in by_id.values() for mid in entry["media_ids"]}
    if attached_media_ids:
        id_placeholders = ",".join(["?"] * len(attached_media_ids))
        cur.execute(
            f"""SELECT id, context_id, context_type, media_url, media_type
                FROM chat_media_uploads WHERE id IN ({id_placeholders})""",
            sorted(attached_media_ids),
        )
        seen = {int(row["id"]) for row in media_rows}
        media_rows.extend(row for row in _rows(cur) if int(row["id"]) not in seen)

    # `valid` means "carries a non-empty URL", which is all a database can tell
    # you. It deliberately does NOT mean "renders": every one of these rows can
    # hold a well-formed URL whose object is long gone, and that is precisely
    # the failure being cleaned up here -- a present-but-dead URL clears the
    # feed's `valid_url` gate and paints an empty frame. Use --verify-urls for
    # the reachability answer; do not read this number as one.
    valid = sum(1 for row in media_rows if str(row.get("media_url") or "").strip())
    broken = len(media_rows) - valid

    with_media = sorted(
        {int(row["context_id"]) for row in media_rows if str(row.get("context_id") or "").isdigit() and int(row["context_id"]) in by_id}
        | {post_id for post_id, entry in by_id.items() if entry["media_ids"]}
    )
    return {
        "total": total,
        "with_media": with_media,
        "valid": valid,
        "broken": broken,
        "media_rows": media_rows,
        "posts": by_id,
    }


def apply_cleanup(cur, report):
    """Detach media. No DELETE, no post mutation beyond media fields."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    post_ids = report["with_media"]
    if not post_ids:
        return {"posts_detached": 0, "media_retired": 0}
    placeholders = ",".join(["?"] * len(post_ids))

    # 1. Drop the id list, and return the post to text. `updated_at` is left
    #    alone: the feed sorts and displays by it, so touching it would reorder
    #    history to make a cleanup look like new activity.
    cur.execute(
        f"""UPDATE pulse_posts SET media_ids_json='[]', post_type='text'
            WHERE user_id=? AND id IN ({placeholders})""",
        [AUTOMATED_USER_ID] + list(post_ids),
    )
    posts_detached = cur.rowcount if cur.rowcount is not None else 0

    # 2. Park the media rows so the context_id branch of the feed lookup stops
    #    matching them. Scoped to uploader 0 as well as the post id, so a real
    #    user's upload can never be swept in by a stray context_id.
    cur.execute(
        f"""UPDATE chat_media_uploads SET context_type=?, updated_at=?
            WHERE uploader_user_id=? AND context_type IN ('pulse','pulse_post')
              AND context_id IN ({placeholders})""",
        [RETIRED_CONTEXT_TYPE, now, AUTOMATED_USER_ID] + [str(value) for value in post_ids],
    )
    media_retired = cur.rowcount if cur.rowcount is not None else 0

    # 3. Record the outcome in the provenance table so a later run, or an
    #    engineer reading it, can tell "detached on purpose" from "never ran".
    try:
        cur.execute(
            f"""UPDATE pulse_generated_media SET state='detached', updated_at=?
                WHERE source_post_id IN ({placeholders})""",
            [now] + list(post_ids),
        )
    except Exception:
        # Table only exists where the pipeline has run. Absence is not an error.
        pass
    return {"posts_detached": posts_detached, "media_retired": media_retired}


def check_urls(media_rows, origin, timeout=20):
    """Ask the origin whether each media URL actually resolves.

    The database cannot distinguish a live image from a dead link, and that
    distinction is the whole defect: these rows were written to the app
    container's own `/static/uploads` tree, which does not survive a redeploy.
    Reachability is therefore reported from the network or not at all.
    """
    import urllib.request  # noqa: PLC0415 -- only needed for this optional pass

    counts = {}
    for row in media_rows:
        url = str(row.get("media_url") or "").strip()
        if not url:
            counts["EMPTY"] = counts.get("EMPTY", 0) + 1
            continue
        target = url if url.startswith("http") else origin.rstrip("/") + url
        try:
            with urllib.request.urlopen(urllib.request.Request(target, method="HEAD"), timeout=timeout) as resp:
                code = resp.status
        except Exception as exc:  # noqa: BLE001 -- any failure is "does not render"
            code = getattr(exc, "code", "UNREACHABLE")
        counts[code] = counts.get(code, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default is dry run)")
    parser.add_argument(
        "--verify-urls",
        action="store_true",
        help="HEAD each media URL against --origin to see which actually resolve",
    )
    parser.add_argument("--origin", default="https://pulsesoc.com", help="origin for --verify-urls")
    args = parser.parse_args()

    from services import db as db_service  # noqa: PLC0415 -- see module docstring

    conn = db_service.connect()
    cur = conn.cursor()
    report = survey(cur)

    print("PULSESOC AUTOMATED POST MEDIA CLEANUP")
    print(f"DATABASE:                  {db_service.masked_database_url()}")
    print(f"MODE:                      {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"TOTAL AUTOMATED POSTS:     {report['total']}")
    print(f"AUTOMATED POSTS WITH MEDIA:{len(report['with_media'])}")
    print(f"ROWS WITH A URL:           {report['valid']}")
    print(f"ROWS WITH NO URL:          {report['broken']}")
    print(f"ROWS TO DETACH:            {len(report['media_rows'])}")
    if report["with_media"]:
        print(f"TARGET POST IDS:           {report['with_media'][:50]}")

    if args.verify_urls and report["media_rows"]:
        counts = check_urls(report["media_rows"], args.origin)
        rendered = counts.get(200, 0)
        print(f"URLS THAT RESOLVE (200):   {rendered}")
        print(f"URLS THAT DO NOT RESOLVE:  {len(report['media_rows']) - rendered}")
        print(f"RESPONSE BREAKDOWN:        {counts}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to detach.")
        conn.close()
        return

    result = apply_cleanup(cur, report)
    conn.commit()
    print(f"\nPOSTS DETACHED:            {result['posts_detached']}")
    print(f"MEDIA ROWS RETIRED:        {result['media_retired']}")
    print("POSTS DELETED:             0")
    conn.close()


if __name__ == "__main__":
    main()
