#!/usr/bin/env python3
"""Mark media rows whose backing bytes are irrecoverably gone as unavailable.

Some early rows were written while uploads still landed on the container's local
disk (``storage_provider='local'``, ``media_url='/static/uploads/...'``). That disk
is ephemeral on Railway, so the files went away with a deploy while the rows kept
claiming ``processing_status='ready'`` and ``is_available=1``. Nothing ever revisited
that claim, so the app faithfully handed the client a URL for media that no longer
exists and the player drew a black rectangle -- an outage wearing the empty state's
clothes. Measured 2026-09-14: ids 28 and 29, both ``context_type='pulse_status'``.

This does not delete anything. The row, its timestamps and its moderation state are
all preserved; only the two columns that make a false claim are corrected:

    processing_status  ready -> failed      (the canonical terminal state: it is what
                                             media_service.resolve_media falls back to
                                             for unavailable media, what bot.py writes
                                             for a failed Mux asset, and one of the
                                             FAILED_STATES the mobile client already
                                             treats as terminal. media_worker's
                                             'processing_blocked' is a worker-internal
                                             marker -- its only reader is the backlog
                                             query's own exclusion -- so it would leave
                                             the client none the wiser.)
    is_available       1 -> 0

with ``availability_error`` set to a reason code for operators. The code is internal:
it reaches the client only as ``error_message``, and the client renders its own
"Status media is unavailable." rather than echoing it, so no storage path or bucket
name is ever shown to a user.

A row is only touched when all four proofs hold:

    1. the database currently claims the media is ready and available
    2. no Mux playback exists to fall back to
    3. the URL the client would actually load does not serve
    4. no object sits behind the row's storage key in the bucket

Proof 4 needs R2 credentials, which live on the CoinPilotX / coinpilotx-media-engine
services rather than on Postgres. Without them the proof is *unobtained*, not passed,
and the row is skipped -- never assumed absent. Run read-only first:

    railway run --service Postgres ./.venv/bin/python scripts/repair_media_rows_with_lost_source.py

and to obtain proof 4 as well, supply both the public database URL and the bucket
credentials in one environment, then pass --apply:

    railway run --service coinpilotx-media-engine env DATABASE_PUBLIC_URL="$PUBLIC_URL" \
        ./.venv/bin/python scripts/repair_media_rows_with_lost_source.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request

REASON = "SOURCE_MEDIA_MISSING"

# Mirrors media_service/mediaContract: states that still promise playable media.
LIVE_STATES = ("", "ready", "asset_ready", "available", "completed", "complete", "published")
MUX_READY_STATES = ("ready", "asset_ready", "available")


def _database_url() -> str:
    for name in ("DATABASE_PUBLIC_URL", "DATABASE_URL"):
        value = os.getenv(name)
        if value:
            return value
    sys.exit("No DATABASE_PUBLIC_URL or DATABASE_URL in the environment.")


def _bucket():
    """The storage client, or None when this environment holds no credentials."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from services import media_storage
    except Exception:
        return None
    if not (os.getenv("R2_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID")):
        return None
    return media_storage


def _object_exists(storage, key: str) -> bool | None:
    """True/False when the bucket answers, None when it cannot be asked."""
    if not storage or not key:
        return None if not storage else False
    try:
        return bool(storage.head_object(key))
    except Exception as exc:
        if "404" in str(exc) or "NoSuchKey" in str(exc) or "Not Found" in str(exc):
            return False
        return None


def _serves(url: str, app_base: str, timeout: int) -> tuple[bool, str]:
    """Fetch exactly what the client would load. Only a real 200 counts as serving."""
    target = url if url.lower().startswith(("http://", "https://")) else f"{app_base.rstrip('/')}/{url.lstrip('/')}"
    try:
        request = urllib.request.Request(target, headers={"User-Agent": "pulsesoc-media-repair"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}"
            content_type = (response.headers.get("Content-Type") or "").lower()
            body = response.read(64)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__})"
    # A 200 that hands back an error page is not media. Flask serves the SPA shell
    # for plenty of unmatched paths, so the content type has to be checked too.
    if content_type.startswith("text/html"):
        return False, "HTML, not media"
    if not body:
        return False, "empty body"
    return True, f"serves ({content_type or 'unknown type'})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark media rows with irrecoverably lost sources as unavailable.")
    parser.add_argument("--apply", action="store_true", help="write the repair (default: report only)")
    parser.add_argument("--ids", default="", help="restrict to a comma-separated list of media ids")
    parser.add_argument("--app-base", default=os.getenv("PULSE_APP_BASE_URL", "https://pulsesoc.com"))
    parser.add_argument("--timeout", type=int, default=20, help="per-URL fetch timeout in seconds")
    args = parser.parse_args()

    only_ids = [int(value) for value in args.ids.replace(",", " ").split() if value.strip()]

    import psycopg2

    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, COALESCE(media_type,''), COALESCE(storage_provider,''),
               COALESCE(storage_key,''), COALESCE(object_key,''), COALESCE(media_url,''),
               COALESCE(playback_url,''), COALESCE(mux_status,''), COALESCE(mux_playback_id,''),
               COALESCE(processing_status,''), is_available
        FROM chat_media_uploads
        WHERE deleted_at IS NULL
          AND COALESCE(storage_provider,'') NOT IN ('r2','s3','mux')
          AND COALESCE(is_available, 1) <> 0
          AND LOWER(COALESCE(processing_status,'')) IN %s
        ORDER BY id
        """,
        (LIVE_STATES,),
    )
    rows = [row for row in cur.fetchall() if not only_ids or int(row[0]) in only_ids]

    storage = _bucket()
    if storage is None:
        print("NOTE: no bucket credentials in this environment - proof 4 cannot be obtained.\n")

    repairable: list[int] = []
    print(f"candidates={len(rows)}\n")
    for (media_id, media_type, provider, storage_key, object_key, media_url,
         playback_url, mux_status, mux_playback_id, processing_status, is_available) in rows:
        claims_live = bool(is_available) and processing_status.lower() in LIVE_STATES
        has_mux = bool(mux_playback_id) and mux_status.lower() in MUX_READY_STATES
        primary = playback_url or media_url or storage_key
        served, serve_reason = _serves(primary, args.app_base, args.timeout) if primary else (False, "no url at all")
        in_bucket = _object_exists(storage, storage_key) or _object_exists(storage, object_key)

        proofs = {
            "db claims ready+available": claims_live,
            "no mux playback": not has_mux,
            "client url does not serve": not served,
            "no object in bucket": in_bucket is False,
        }
        ok = all(proofs.values())
        print(f"  id={media_id} type={media_type} provider={provider!r} status={processing_status!r} available={is_available}")
        print(f"     url={primary[:88]!r} -> {serve_reason}")
        print(f"     bucket={'absent' if in_bucket is False else ('present' if in_bucket else 'NOT CHECKED')}")
        for label, passed in proofs.items():
            print(f"     [{'x' if passed else ' '}] {label}")
        if ok:
            repairable.append(int(media_id))
            print(f"     => REPAIRABLE: is_available {is_available}->0, processing_status {processing_status!r}->'failed', reason={REASON}")
        else:
            print("     => SKIPPED (a proof did not hold)")
        print()

    if not repairable:
        print("Nothing to repair.")
        return 0
    if not args.apply:
        print(f"DRY RUN. {len(repairable)} row(s) would be marked unavailable: {repairable}. Pass --apply to write.")
        return 0

    cur.execute(
        """
        UPDATE chat_media_uploads
        SET is_available = 0,
            processing_status = 'failed',
            availability_error = %s,
            updated_at = NOW()::text
        WHERE id = ANY(%s)
        """,
        (REASON, repairable),
    )
    conn.commit()
    print(f"Repaired {cur.rowcount} row(s). Reading back:\n")
    cur.execute(
        "SELECT id, processing_status, is_available, availability_error, deleted_at "
        "FROM chat_media_uploads WHERE id = ANY(%s) ORDER BY id",
        (repairable,),
    )
    for media_id, processing_status, is_available, availability_error, deleted_at in cur.fetchall():
        print(f"  id={media_id} processing_status={processing_status!r} is_available={is_available} "
              f"availability_error={availability_error!r} deleted_at={deleted_at!r}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
