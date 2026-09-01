"""Ops2 Stage 7 — subscription webhook exactly-once acceptance.

Mandate: a duplicate provider webhook must yield ONE provider-sub row, ONE
canonical grant set (no duplicate grant rows), and ONE unchanged admin
projection (Paid Pro count stays 1) — never a doubled side effect.

Provider email note (documented, not tested here): Apple/Google IAP lifecycle
sends no platform email by design (the store sends its own receipts); the
Stripe email path dedupes via payment_email_already_sent(stripe_event_id,
payment_id, email_type) in bot.py, which is untestable hermetically in this
sandbox (bot.py imports the absent `stripe` module) — ENV-SKIP recorded.

    python3 tests/business_os/test_subscription_exactly_once.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_exactonce_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import schema as ent_schema  # noqa: E402
from services.business_os.entitlements import service as ent_svc  # noqa: E402
from services.business_os.entitlements import iap_apple as apple  # noqa: E402
from services import pro_access  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from _iap_jws_util import Chain, build_notification  # noqa: E402

KEY = "premium.access"


def _verifier(chain):
    return apple.AppleNotificationVerifier(trust_anchors=[chain.root_der()])


def _paid_count():
    bulk = ent_svc.resolve_all_subjects(KEY)
    return sum(1 for v in bulk.values()
               if v["allowed"] and v["source"] in pro_access.PAID_GRANT_SOURCES)


def test_duplicate_webhook_exactly_once():
    ent_schema.ensure_schema()
    ent_schema.seed_catalog()
    chain = Chain()
    uid, otx = "701", "1000000000000701"
    tok = build_notification(chain, notification_type="SUBSCRIBED",
                             app_account_token=uid, original_transaction_id=otx)

    r1 = apple.apply_apple_notification(tok, verifier=_verifier(chain))
    assert r1["projected"] is True, r1
    conn = db.connect()
    try:
        subs_1 = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id=?", (otx,)).fetchone()[0]
        grants_1 = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_grants "
            "WHERE subject_id=? AND entitlement_key=?", (uid, KEY)).fetchone()[0]
    finally:
        conn.close()
    paid_1 = _paid_count()
    merged_1 = pro_access.merged_access_type(
        {}, ent_svc.resolve_all_subjects(KEY).get(uid))

    # Replay the identical signed payload (duplicate delivery).
    r2 = apple.apply_apple_notification(tok, verifier=_verifier(chain))
    conn = db.connect()
    try:
        subs_2 = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id=?", (otx,)).fetchone()[0]
        grants_2 = conn.execute(
            "SELECT COUNT(*) FROM business_os_ent_grants "
            "WHERE subject_id=? AND entitlement_key=?", (uid, KEY)).fetchone()[0]
    finally:
        conn.close()

    assert subs_1 == subs_2 == 1, (subs_1, subs_2)
    assert grants_1 == grants_2 == 1, (grants_1, grants_2)  # upsert, not append
    assert _paid_count() == paid_1 == 1
    merged_2 = pro_access.merged_access_type(
        {}, ent_svc.resolve_all_subjects(KEY).get(uid))
    assert merged_1 == merged_2 == "paid", (merged_1, merged_2)
    assert ent_svc.has_entitlement(uid, KEY) is True
    assert r2["recorded"] is True


def test_refund_after_duplicate_still_revokes_cleanly():
    ent_schema.ensure_schema()
    ent_schema.seed_catalog()
    chain = Chain()
    uid, otx = "702", "1000000000000702"
    tok = build_notification(chain, notification_type="SUBSCRIBED",
                             app_account_token=uid, original_transaction_id=otx)
    apple.apply_apple_notification(tok, verifier=_verifier(chain))
    apple.apply_apple_notification(tok, verifier=_verifier(chain))
    refund = build_notification(chain, notification_type="REFUND",
                                app_account_token=uid, original_transaction_id=otx)
    r = apple.apply_apple_notification(refund, verifier=_verifier(chain))
    assert r["revoked"] is True, r
    assert ent_svc.has_entitlement(uid, KEY) is False
    bulk = ent_svc.resolve_all_subjects(KEY)
    assert bulk[uid]["allowed"] is False
    assert pro_access.merged_access_type({}, bulk[uid]) == "none"


if __name__ == "__main__":
    test_duplicate_webhook_exactly_once()
    test_refund_after_duplicate_still_revokes_cleanly()
    print("OK: 2 tests passed")
