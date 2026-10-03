"""Read-only: what pricing rule has this store actually configured?

The survey runs under ``MANUAL_PRICE`` on purpose -- a read must not propose a
price -- and under that rule a "below target" count has no target to be below.
But §8 says to use the seller's *existing* import settings, so whether
shortfall-against-target is a real number or an invented one depends entirely on
what this store has stored. This asks the module that owns the answer, then
counts the shortfall only if a target turns out to exist.
"""
import os
import sys

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services import db  # noqa: E402
from services.business_os.suppliers import pricing, store_policy  # noqa: E402

SELLER = "mkt-seller:1"

conn = db.connect()
try:
    print("get_policy:", store_policy.get_policy(conn, SELLER, SELLER))
    rule, source = store_policy.resolve_rule(conn, SELLER, SELLER)
    print("resolve_rule:", rule, "from", source)
    allowance, a_source = store_policy.resolve_shipping_allowance(conn, SELLER, SELLER)
    print("resolve_shipping_allowance:", allowance, "from", a_source)
finally:
    conn.close()

print()
print("MARGIN_STATES:", pricing.MARGIN_STATES)
print("DEFAULT_TARGET_MARGIN:", getattr(store_policy, "DEFAULT_TARGET_MARGIN_PCT",
                                        getattr(store_policy, "DEFAULT_TARGET_MARGIN", "<none>")))
print("normalize_rule(None):", pricing.normalize_rule(None))
