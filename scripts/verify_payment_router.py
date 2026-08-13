"""Standalone verification of the PulseSoc payment policy matrix.

Runs without pytest so it can execute in any environment. Imports the real
`services.pulse_payment_router` and asserts the server-decided-provider policy
end to end: the client never chooses; classification is enumerated; anything
unenumerated is refused, not guessed.
"""

import sys

sys.path.insert(0, ".")

from services import pulse_payment_router as r  # noqa: E402

PASS = []
FAIL = []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


# --- classification is enumerated, never inferred ---------------------------
check("classify ad_credits", r.classify_item("ad_credits"), "digital")
check("classify premium_subscription", r.classify_item("premium_subscription"), "digital")
check("classify marketplace_physical", r.classify_item("marketplace_physical"), "physical")
check("classify real_world_service", r.classify_item("real_world_service"), "physical")
check("classify creator_payout", r.classify_item("creator_payout"), "payout")
check("classify seller_payout", r.classify_item("seller_payout"), "payout")
check("classify promo_credit_grant", r.classify_item("promo_credit_grant"), "promo")
check("classify unknown -> ambiguous", r.classify_item("mystery_box"), "ambiguous")

# --- iOS digital MUST route to Apple IAP (guideline 3.1.1) ------------------
check("ios ad_credits -> apple_iap",
      r.route_payment(platform="ios", item_type="ad_credits").get("provider"),
      r.PROVIDER_APPLE_IAP)
check("ios premium_subscription -> apple_iap",
      r.route_payment(platform="ios", item_type="premium_subscription").get("provider"),
      r.PROVIDER_APPLE_IAP)
check("ipados ad_credits -> apple_iap",
      r.route_payment(platform="ipados", item_type="ad_credits").get("provider"),
      r.PROVIDER_APPLE_IAP)

# --- physical goods MUST NOT use IAP (guideline 3.1.3(e)) -------------------
check("ios marketplace_physical -> stripe",
      r.route_payment(platform="ios", item_type="marketplace_physical").get("provider"),
      r.PROVIDER_STRIPE)
check("ios real_world_service -> stripe",
      r.route_payment(platform="ios", item_type="real_world_service").get("provider"),
      r.PROVIDER_STRIPE)

# --- digital off-iOS settles via Stripe ------------------------------------
check("android ad_credits -> stripe",
      r.route_payment(platform="android", item_type="ad_credits").get("provider"),
      r.PROVIDER_STRIPE)
check("web premium_subscription -> stripe",
      r.route_payment(platform="web", item_type="premium_subscription").get("provider"),
      r.PROVIDER_STRIPE)

# --- payouts move via Stripe Connect, never IAP ----------------------------
check("ios seller_payout -> stripe_connect",
      r.route_payment(platform="ios", item_type="seller_payout").get("provider"),
      r.PROVIDER_STRIPE_CONNECT)
check("web creator_payout -> stripe_connect",
      r.route_payment(platform="web", item_type="creator_payout").get("provider"),
      r.PROVIDER_STRIPE_CONNECT)

# --- promo credits are internal ledger, non-cash ---------------------------
check("promo_credit_grant -> internal_ledger",
      r.route_payment(platform="ios", item_type="promo_credit_grant").get("provider"),
      r.PROVIDER_INTERNAL_LEDGER)

# --- ambiguous is REFUSED and flagged, not defaulted -----------------------
amb = r.route_payment(platform="ios", item_type="mystery_box")
check("ambiguous ok is False", amb.get("ok"), False)
check("ambiguous flagged True", amb.get("flagged"), True)
check("ambiguous provider absent", amb.get("provider"), None)

# --- unknown platform is refused -------------------------------------------
badp = r.route_payment(platform="playstation", item_type="ad_credits")
check("unknown platform ok False", badp.get("ok"), False)
check("unknown platform flagged", badp.get("flagged"), True)

# --- ad-credit catalog is server truth (amounts server-side) ---------------
cat = {p["product_id"]: p["amount_cents"] for p in r.adcredit_catalog()}
check("tier1 amount", cat.get("com.pulsesoc.adcredits.tier1"), 499)
check("tier5 amount", cat.get("com.pulsesoc.adcredits.tier5"), 9999)
check("exactly 5 ad-credit products", len(cat), 5)
check("no premium ids leaked into ad-credit catalog",
      any("premium" in pid for pid in cat), False)

# --- buyer-safe error classification (pure, no stripe import) --------------
from services import marketplace_payment_errors as e  # noqa: E402


class AuthenticationError(Exception):
    pass


class CardError(Exception):
    code = "card_declined"
    param = "number"


class APIConnectionError(Exception):
    pass


auth = e.classify_provider_exception(AuthenticationError("bad key"))
check("auth -> PAYMENT_CONFIGURATION_ERROR", auth["code"], "PAYMENT_CONFIGURATION_ERROR")
check("auth -> 503", auth["status"], 503)
check("auth msg reassures no charge", "No card was charged." in auth["message"], True)
check("auth never leaks raw message", "bad key" not in str(auth), True)

card = e.classify_provider_exception(CardError("declined"))
check("card decline -> PAYMENT_FAILED", card["code"], "PAYMENT_FAILED")
check("card decline -> 402", card["status"], 402)
check("card provider_error fingerprint code", card["provider_error"]["code"], "card_declined")

net = e.classify_provider_exception(APIConnectionError("timeout"))
check("network -> NETWORK_ERROR", net["code"], "NETWORK_ERROR")

bug = e.classify_provider_exception(ValueError("our own bug"))
check("non-provider bug -> PAYMENT_UNAVAILABLE", bug["code"], "PAYMENT_UNAVAILABLE")
check("non-provider bug -> 500", bug["status"], 500)

# ---------------------------------------------------------------------------
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
for name, got, want in FAIL:
    print(f"  FAIL {name}: got {got!r} want {want!r}")
sys.exit(1 if FAIL else 0)
