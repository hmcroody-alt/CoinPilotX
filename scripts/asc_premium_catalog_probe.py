"""Read-only probe: can we see the Premium subscription products Apple would serve?

Answers one question the app cannot answer for itself: when StoreKit reports "no
products", is that because App Store Connect has no returnable subscription, or
because the device asked wrongly?

Strictly read-only — GET requests only. Prints no secret material: the private
key is loaded, used to sign, and never echoed.

Two different Apple APIs are involved and the distinction matters:

  * App Store Server API (api.storekit.itunes.apple.com) — transaction and
    subscription *state*. An "In-App Purchase" key can reach it.
  * App Store Connect API (api.appstoreconnect.apple.com) — the product
    *catalog*. Only a Team key can reach it.

If the configured key is IAP-scoped, the catalog call returns 401 and that is a
finding, not a failure: it means catalog truth must come from the ASC web UI.
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.request

APP_ID = "6777591572"
PREMIUM_IDS = ("com.pulsesoc.premium.monthly", "com.pulsesoc.premium.annual")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_jwt(*, include_bid: bool) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw = (os.getenv("APPLE_IAP_PRIVATE_KEY") or "").strip()
    if "-----BEGIN" not in raw and os.path.isfile(raw):
        pem = open(raw, "rb").read()
    else:
        pem = raw.replace("\\n", "\n").encode()
    key = load_pem_private_key(pem, password=None)

    issued = int(time.time())
    header = {"alg": "ES256", "kid": (os.getenv("APPLE_IAP_KEY_ID") or "").strip(), "typ": "JWT"}
    claims = {
        "iss": (os.getenv("APPLE_IAP_ISSUER_ID") or "").strip(),
        "iat": issued,
        "exp": issued + 300,
        "aud": "appstoreconnect-v1",
    }
    if include_bid:
        claims["bid"] = "com.pulsesoc.app"
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
        _b64url(json.dumps(claims, separators=(",", ":")).encode())
    ).encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input.decode() + "." + _b64url(sig)


def get(url: str, token: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:600]
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> None:
    missing = [n for n in ("APPLE_IAP_ISSUER_ID", "APPLE_IAP_KEY_ID", "APPLE_IAP_PRIVATE_KEY")
               if not (os.getenv(n) or "").strip()]
    if missing:
        print(f"NOT CONFIGURED: {', '.join(missing)}")
        return

    asc = build_jwt(include_bid=False)
    print("=" * 68)
    print("APP STORE CONNECT API — catalog truth")
    print("=" * 68)

    for label, url in (
        ("subscriptionGroups",
         f"https://api.appstoreconnect.apple.com/v1/apps/{APP_ID}/subscriptionGroups?include=subscriptions&limit=50"),
        ("inAppPurchasesV2",
         f"https://api.appstoreconnect.apple.com/v1/apps/{APP_ID}/inAppPurchasesV2?limit=100"),
    ):
        status, body = get(url, asc)
        print(f"\n--- {label}: HTTP {status} ---")
        if status == 200:
            try:
                print(summarize(json.loads(body), label))
            except Exception as exc:  # noqa: BLE001
                print(f"(unparsed) {exc}: {body[:400]}")
        else:
            print(body[:600])


def summarize(payload: dict, label: str) -> str:
    lines = []
    included = payload.get("included") or []
    for item in payload.get("data", []):
        attrs = item.get("attributes", {})
        if label == "subscriptionGroups":
            lines.append(f"  GROUP  {attrs.get('referenceName')}  (id={item.get('id')})")
        else:
            lines.append(
                f"  IAP    {attrs.get('productId')}  type={attrs.get('inAppPurchaseType')}  "
                f"state={attrs.get('state')}"
            )
    for item in included:
        if item.get("type") == "subscriptions":
            attrs = item.get("attributes", {})
            pid = attrs.get("productId")
            mark = "  <-- PREMIUM" if pid in PREMIUM_IDS else ""
            lines.append(
                f"  SUB    {pid}  state={attrs.get('state')}  "
                f"period={attrs.get('subscriptionPeriod')}  "
                f"available={attrs.get('availableInAllTerritories')}{mark}"
            )
    if not lines:
        return "  (no products returned)"
    return "\n".join(lines)


if __name__ == "__main__":
    main()
