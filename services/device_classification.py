"""Canonical device classification for PulseSoc.

Single source of truth for turning a User-Agent (plus optional request
headers) into a device classification. Every backend surface that needs a
device type (analytics, ads, presence, login-security emails, push devices,
native-app gating) must delegate here instead of keeping its own token list.

Design rules (App Review item 10):
- Explicit app signals win: the ``X-PulseSoc-Platform`` header and the
  ``PulseSocNativeApp/`` UA prefix mark a request as coming from the native
  app. The RN app's default iOS UA is ``PulseSoc/<build> CFNetwork/... Darwin/...``
  which matches no browser tokens, so CFNetwork/Darwin is treated as
  "native-likely mobile" rather than desktop.
- Client hints (``Sec-CH-UA-Mobile`` / ``Sec-CH-UA-Platform``) are consulted
  when present; they catch iPadOS in desktop-UA mode (Macintosh UA).
- Unknown is a first-class value: an empty or unmatched UA is ``"unknown"``,
  never silently ``"desktop"``.
- Device type is orthogonal to trust; nothing here touches
  ``security_devices.trusted``.
"""

import re

DEVICE_MOBILE = "mobile"
DEVICE_TABLET = "tablet"
DEVICE_DESKTOP = "desktop"
DEVICE_UNKNOWN = "unknown"

NATIVE_UA_PREFIX = "PulseSocNativeApp/"
NATIVE_PLATFORM_HEADER = "X-PulseSoc-Platform"

_ANDROID_MOBILE_RE = re.compile(r"android.*mobile", re.IGNORECASE)


def _header_get(headers, name):
    """Case-insensitive header lookup that works for dicts and Flask headers."""
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    value = getter(name)
    if value is None:
        # Plain dicts are case-sensitive; try common casings.
        value = getter(name.lower()) or getter(name.upper()) or ""
    return str(value or "").strip()


def _clean_hint(value):
    return str(value or "").strip().strip('"').strip().lower()


def classify_device(user_agent, headers=None):
    """Classify a request's device.

    Returns a dict:
      device_type: "mobile" | "tablet" | "desktop" | "unknown"
      is_native_app: True only on explicit app signals (header or UA prefix)
      native_likely: True for CFNetwork/Darwin UAs (iOS URLSession) too
      platform: "ios" | "android" | "macos" | "windows" | "linux" | "chromeos" | ""
    """
    ua = str(user_agent or "")
    ua_lower = ua.lower()

    # (a) Explicit native-app signals.
    header_platform = _clean_hint(_header_get(headers, NATIVE_PLATFORM_HEADER))
    ua_native = NATIVE_UA_PREFIX.lower() in ua_lower
    if header_platform in {"ios", "android", "ipad"} or ua_native:
        platform = header_platform
        if platform not in {"ios", "android", "ipad"}:
            # Derive from the native UA, e.g. "PulseSocNativeApp/123 (iOS; iPhone ...)".
            if "android" in ua_lower:
                platform = "android"
            elif "ipad" in ua_lower:
                platform = "ipad"
            else:
                platform = "ios"
        device_type = DEVICE_TABLET if platform == "ipad" else DEVICE_MOBILE
        canonical_platform = "ios" if platform in {"ios", "ipad"} else "android"
        return {
            "device_type": device_type,
            "is_native_app": True,
            "native_likely": True,
            "platform": canonical_platform,
        }

    # (b) iOS URLSession UA (`PulseSoc/<build> CFNetwork/... Darwin/...`):
    # native-likely mobile, not a desktop browser.
    if "cfnetwork/" in ua_lower and "darwin/" in ua_lower:
        return {
            "device_type": DEVICE_MOBILE,
            "is_native_app": False,
            "native_likely": True,
            "platform": "ios",
        }

    # (c) Client hints, when the browser sent them.
    hint_mobile = _clean_hint(_header_get(headers, "Sec-CH-UA-Mobile"))
    hint_platform = _clean_hint(_header_get(headers, "Sec-CH-UA-Platform"))
    if hint_platform or hint_mobile:
        if hint_platform in {"ios", "ipados"}:
            device_type = DEVICE_TABLET if ("ipad" in ua_lower or hint_platform == "ipados" or "macintosh" in ua_lower) else DEVICE_MOBILE
            return {"device_type": device_type, "is_native_app": False, "native_likely": False, "platform": "ios"}
        if hint_mobile == "?1":
            # A "mobile" hint on a Macintosh UA is iPadOS desktop-UA mode.
            if "macintosh" in ua_lower or "mac os" in ua_lower:
                return {"device_type": DEVICE_TABLET, "is_native_app": False, "native_likely": False, "platform": "ios"}
            platform = "android" if hint_platform == "android" or "android" in ua_lower else ("ios" if "iphone" in ua_lower or "ipod" in ua_lower else "")
            return {"device_type": DEVICE_MOBILE, "is_native_app": False, "native_likely": False, "platform": platform}
        # hint present but not mobile: fall through to UA tokens for
        # tablet/desktop resolution (hints do not distinguish tablets).

    # (d) Classic UA tokens.
    if "ipad" in ua_lower or "tablet" in ua_lower:
        return {"device_type": DEVICE_TABLET, "is_native_app": False, "native_likely": False, "platform": "ios" if "ipad" in ua_lower else ("android" if "android" in ua_lower else "")}
    if "iphone" in ua_lower or "ipod" in ua_lower:
        return {"device_type": DEVICE_MOBILE, "is_native_app": False, "native_likely": False, "platform": "ios"}
    if _ANDROID_MOBILE_RE.search(ua_lower):
        return {"device_type": DEVICE_MOBILE, "is_native_app": False, "native_likely": False, "platform": "android"}
    if "android" in ua_lower:
        # Android without the Mobile token is a tablet per UA spec.
        return {"device_type": DEVICE_TABLET, "is_native_app": False, "native_likely": False, "platform": "android"}
    if "mobile" in ua_lower:
        return {"device_type": DEVICE_MOBILE, "is_native_app": False, "native_likely": False, "platform": ""}
    if "macintosh" in ua_lower or "mac os" in ua_lower:
        return {"device_type": DEVICE_DESKTOP, "is_native_app": False, "native_likely": False, "platform": "macos"}
    if "windows" in ua_lower:
        return {"device_type": DEVICE_DESKTOP, "is_native_app": False, "native_likely": False, "platform": "windows"}
    if "cros" in ua_lower:
        return {"device_type": DEVICE_DESKTOP, "is_native_app": False, "native_likely": False, "platform": "chromeos"}
    if "x11" in ua_lower or "linux" in ua_lower:
        return {"device_type": DEVICE_DESKTOP, "is_native_app": False, "native_likely": False, "platform": "linux"}

    # (e) Empty or unmatched: unknown, never desktop.
    return {"device_type": DEVICE_UNKNOWN, "is_native_app": False, "native_likely": False, "platform": ""}


def device_family_fingerprint(user_agent, headers=None):
    """Coarse, stable device-family string for cross-context matching.

    Used by deferred referral attribution: the App Store redirect is hit by
    Mobile Safari while the claim comes from the native app's URLSession UA,
    so exact-UA hashes can never match. Both collapse to e.g. "ios-mobile".
    """
    info = classify_device(user_agent, headers)
    platform = info.get("platform") or "any"
    return f"{platform}-{info.get('device_type') or DEVICE_UNKNOWN}"
