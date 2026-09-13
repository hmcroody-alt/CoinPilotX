#!/usr/bin/env python3
"""Read-only probe: can a browser complete a direct-to-R2 upload?

Why this is a P0 for the web rebuild and invisible to the native app
--------------------------------------------------------------------
`services/media_upload_sessions.py` hands the client a presigned URL and has it
PUT bytes straight to R2. For anything at or above
``MEDIA_UPLOAD_MULTIPART_THRESHOLD_MB`` (default **16 MB**) it does that in
parts, and then requires the client to send back the **ETag of every part**::

    number = int(item.get("part_number") or 0)
    etag = str(item.get("etag") or "").strip()
    if number < 1 or number > 10000 or not etag or number in seen:
        continue

A part with no ETag is silently dropped, and `complete_multipart_upload` then
fails with a list that does not match what was uploaded.

React Native's fetch is not subject to CORS, so the native app reads that header
unconditionally and every video upload works. A browser can only read response
headers named in ``Access-Control-Expose-Headers``. Without ``ETag`` there,
``getResponseHeader("ETag")`` returns null, every part is dropped, and multipart
upload is *structurally impossible* from the web -- not slow, not flaky,
impossible, with an error that points at the app rather than at the bucket.

This is therefore a defect the shipped product cannot surface. It is not a bug
in anything running today; it is an unmet precondition for a client that does
not exist yet, and the cheapest moment to find it is before the code that
depends on it is written.

How it measures
---------------
By sending the **preflight a browser would send** -- ``OPTIONS`` with ``Origin``
and ``Access-Control-Request-Method`` -- and reading the response headers.

That is deliberately not the same as reading the bucket's CORS configuration
with ``get_bucket_cors``. The preflight needs no credentials, so it runs from CI
and from a laptop; and it measures the behaviour the browser will actually
observe rather than the configuration that is supposed to produce it. When the
two disagree, the preflight is right. (The credentialed read is kept as a
supplement, and on this account it returns AccessDenied anyway: the R2 token is
scoped to objects, not to bucket configuration. A probe that depended on it
would have reported "unknown" forever.)

Read-only on purpose
--------------------
This does not write. Bucket CORS is production infrastructure and the blast
radius of a wrong ``AllowedOrigins`` is every origin on the internet holding an
intercepted presigned URL. It prints the configuration to apply and leaves
applying it to a human who can name the real origins.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

EXIT_OK = 0
EXIT_MISCONFIGURED = 1
EXIT_NO_DATA = 3

REQUIRED_EXPOSE = "etag"
DEFAULT_ORIGINS = ["https://pulsesoc.com", "https://www.pulsesoc.com"]


def endpoint_and_bucket():
    bucket = (os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET") or "").strip()
    endpoint = (os.getenv("R2_ENDPOINT_URL") or os.getenv("R2_ENDPOINT") or "").strip()
    account = os.getenv("R2_ACCOUNT_ID", "").strip()
    if not endpoint and account:
        endpoint = f"https://{account}.r2.cloudflarestorage.com"
    return endpoint, bucket


def preflight(url, origin, method="PUT", timeout=15):
    """Send a browser's preflight. Returns (status, headers) or (None, reason).

    A 403 here is an *answer*, not a failure to read: R2 refuses the preflight
    and names the reason in the body when no CORS rule matches.
    """
    req = urllib.request.Request(url, method="OPTIONS", headers={
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "content-type",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        return exc.code, dict(exc.headers), body
    except (urllib.error.URLError, OSError) as exc:
        return None, {}, f"could not connect: {exc}"


def lower_keys(headers):
    return {k.lower(): v for k, v in headers.items()}


def assess(status, headers, body, origin):
    """Problems as a list of strings. Empty means a browser can upload."""
    h = lower_keys(headers)
    allow_origin = h.get("access-control-allow-origin")
    expose = {p.strip().lower()
              for p in (h.get("access-control-expose-headers") or "").split(",")
              if p.strip()}
    allow_methods = {p.strip().upper()
                     for p in (h.get("access-control-allow-methods") or "").split(",")
                     if p.strip()}

    problems = []
    if not allow_origin:
        detail = ""
        if "CORS not configured" in body:
            # R2 says so itself. Worth quoting rather than inferring.
            detail = " -- R2's own answer: \"CORS not configured for this bucket\""
        problems.append(
            f"the preflight returned HTTP {status} with no "
            f"Access-Control-Allow-Origin{detail}. Every browser upload fails "
            f"here, before any ETag question arises")
        return problems, {"allow_origin": allow_origin, "expose": sorted(expose),
                          "methods": sorted(allow_methods), "status": status}

    if allow_origin not in (origin, "*"):
        problems.append(
            f"Access-Control-Allow-Origin is {allow_origin!r}, which does not "
            f"match {origin!r}")
    if allow_methods and "PUT" not in allow_methods:
        problems.append(
            f"PUT is not in Access-Control-Allow-Methods ({sorted(allow_methods)}); "
            f"a presigned upload cannot be sent")
    if REQUIRED_EXPOSE not in expose:
        problems.append(
            f"ETag is not in Access-Control-Expose-Headers ({sorted(expose)}). "
            f"The browser cannot read the part ETag, complete_upload() drops "
            f"every part, and any upload at or above the 16 MB multipart "
            f"threshold fails")
    if allow_origin == "*":
        problems.append(
            "Access-Control-Allow-Origin is '*'. Presigned URLs carry their own "
            "authorisation so this is not a data hole by itself, but it lets "
            "any page on the internet spend an intercepted URL")
    return problems, {"allow_origin": allow_origin, "expose": sorted(expose),
                      "methods": sorted(allow_methods), "status": status}


def recommended(origins):
    return {"CORSRules": [{
        "AllowedOrigins": list(origins),
        "AllowedMethods": ["GET", "PUT", "HEAD"],
        "AllowedHeaders": ["*"],
        # The whole point: without this a browser cannot read the part ETag.
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--origin", default=DEFAULT_ORIGINS[0],
                    help="web origin that will upload from a browser")
    ap.add_argument("--endpoint", default="", help="override R2_ENDPOINT_URL")
    ap.add_argument("--bucket", default="", help="override R2_BUCKET")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    endpoint, bucket = endpoint_and_bucket()
    endpoint = args.endpoint or endpoint
    bucket = args.bucket or bucket
    if not endpoint or not bucket:
        print("r2-cors: need R2_ENDPOINT_URL (or R2_ACCOUNT_ID) and R2_BUCKET.\n"
              "  railway run --service CoinPilotX python3 "
              "scripts/ops/r2_cors_probe.py", file=sys.stderr)
        return EXIT_NO_DATA

    host = urlsplit(endpoint).netloc or endpoint
    # Any key works: a preflight is answered from the bucket's CORS rules and
    # never touches the object, so this deliberately does not exist.
    url = f"https://{host}/{bucket}/cors-preflight-probe"

    status, headers, body = preflight(url, args.origin)
    if status is None:
        print(f"r2-cors: could not reach {host}: {body}", file=sys.stderr)
        return EXIT_NO_DATA

    problems, observed = assess(status, headers, body, args.origin)

    if args.json:
        print(json.dumps({"bucket": bucket, "host": host, "origin": args.origin,
                          "observed": observed, "problems": problems}, indent=2))
        return EXIT_OK if not problems else EXIT_MISCONFIGURED

    print(f"r2-cors: bucket {bucket!r} at {host}")
    print(f"  preflight OPTIONS (Origin: {args.origin}, "
          f"Access-Control-Request-Method: PUT) -> HTTP {status}")
    print(f"  access-control-allow-origin  : {observed['allow_origin']}")
    print(f"  access-control-allow-methods : {observed['methods'] or None}")
    print(f"  access-control-expose-headers: {observed['expose'] or None}")

    if not problems:
        print("\nr2-cors: OK -- a browser can PUT and can read the part ETag.")
        return EXIT_OK

    print("\nr2-cors: NOT READY for a browser client")
    for p in problems:
        print(f"  - {p}")
    print("\nApply this (name the real origins; do not ship '*'):\n")
    print(json.dumps(recommended(DEFAULT_ORIGINS), indent=2))
    print(f"\n  aws s3api put-bucket-cors --bucket {bucket} \\\n"
          f"      --endpoint-url https://{host} \\\n"
          f"      --cors-configuration file://cors.json\n")
    print("Nothing is written by this script: bucket CORS is production "
          "infrastructure, and AllowedOrigins needs a human who knows which "
          "origins are real. Re-run this probe afterwards -- it reads the "
          "behaviour, not the config, so it confirms the change actually took.")
    return EXIT_MISCONFIGURED


if __name__ == "__main__":
    raise SystemExit(main())
