#!/usr/bin/env python3
"""Fail if the deployed canonical Live RTC route is missing.

This deliberately makes an unauthenticated request and never handles tokens.
A registered route must reject it with 401; the generic Flask 404 proves that
production was deployed from source predating the Agora Live migration.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://pulsesoc.com")
    parser.add_argument("--live-id", type=int, default=1)
    args = parser.parse_args()
    url = f"{args.base_url.rstrip('/')}/api/pulse/live/{args.live_id}/rtc/token"
    request = urllib.request.Request(
        url,
        data=b'{"role":"viewer"}',
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=15)
        status = response.status
        body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = json.loads(exc.read().decode("utf-8"))
    except Exception as exc:
        print(f"Agora Live route audit: FAIL ({exc.__class__.__name__})")
        return 1
    error_code = str(body.get("error_code") or "") if isinstance(body, dict) else ""
    if status == 401 and error_code == "NOT_AUTHENTICATED":
        print("Agora Live route audit: PASS (registered route rejected unauthenticated request)")
        return 0
    print(f"Agora Live route audit: FAIL (status={status}, error_code={error_code or 'none'})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
