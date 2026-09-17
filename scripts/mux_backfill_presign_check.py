"""Prove a presigned R2 URL is fetchable where the CDN URL is not.

Mux ingests by fetching the source itself, so the backfill for the rows that
still have no Mux asset is blocked on giving Mux a URL it can actually read.
The CDN hostname answers 403 for video extensions -- a Cloudflare edge rule
that matches before origin -- and `create_mux_asset_from_url` correctly refuses
a source that returns an HTML challenge.

A presigned GET against the R2 *S3* endpoint is a different hostname and is not
subject to that zone's rules. This script checks that claim against the real
objects before anything creates a billable Mux asset.

Creates nothing and writes nothing.

    railway run --service coinpilotx-media-engine \
        .venv/bin/python scripts/mux_backfill_presign_check.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import media_storage  # noqa: E402


KEYS = "/tmp/mux_backfill_keys.json"


def probe(url, label):
    # These rows predate the R2 migration and several carry a site-relative
    # /static/uploads path rather than a CDN URL, so there is no host to probe.
    # That is itself the finding: they were never CDN-blocked, they point at
    # ephemeral dyno disk.
    if not str(url or "").startswith(("http://", "https://")):
        return f"{label}: NOT-ABSOLUTE {url}"
    # Range-limited so a reachability check never pulls a whole video.
    request = urllib.request.Request(
        url, method="GET", headers={"Range": "bytes=0-0", "User-Agent": "Mux Video Ingest/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return f"{label}: {response.status} {response.headers.get('content-type', '?')}"
    except urllib.error.HTTPError as exc:
        return f"{label}: {exc.code} {exc.headers.get('content-type', '?')} {exc.headers.get('cf-mitigated', '')}".strip()
    except Exception as exc:
        return f"{label}: ERROR {type(exc).__name__} {exc}"


def main():
    print(f"provider={media_storage.provider()} status={media_storage.storage_status()}")
    client = media_storage.object_client()
    if client is None:
        raise SystemExit("object_client() is None -- R2 credentials not present on this service")
    bucket = os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET")
    print(f"bucket={bucket}")

    with open(KEYS) as fh:
        rows = json.load(fh)

    # Two rows is enough to establish the pattern; the point is the hostname,
    # not the object. Checking all 13 would just repeat the same evidence.
    for row in rows:
        key = row["storage_key"]
        print(f"\n--- id={row['id']} {key}")
        print("   " + probe(row["public_url"], "cdn      "))
        try:
            signed = client.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
            )
        except Exception as exc:
            print(f"   presigned: FAILED to sign {exc}")
            continue
        print("   " + probe(signed, "presigned"))
        print(f"   host={signed.split('/')[2]}")


if __name__ == "__main__":
    main()
