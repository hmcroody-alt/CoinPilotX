"""App Review items 3 + 10: canonical device classification and referral
App Store redirect with deferred attribution.

bot.py is a 111k-line monolith whose import boots half the platform, so the
bot-side functions are extracted by AST and executed against an in-memory
sqlite database (the established pattern in this test suite). The classifier
module is imported directly — it is pure.
"""

import ast
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
BOT_SOURCE = (REPO / "bot.py").read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)

import sys

sys.path.insert(0, str(REPO))

from services.device_classification import classify_device, device_family_fingerprint  # noqa: E402

UA_IPHONE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
UA_IPAD_LEGACY = (
    "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
UA_IPADOS_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)
UA_RN_CFNETWORK = "PulseSoc/13 CFNetwork/1494.0.7 Darwin/23.4.0"
UA_NATIVE_APP = "PulseSocNativeApp/13 (iOS; iPhone15,3)"
UA_DESKTOP_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
UA_ANDROID_PHONE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
)
UA_ANDROID_TABLET = (
    "Mozilla/5.0 (Linux; Android 14; SM-X910) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def extract_function(name, tree=BOT_TREE):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            clone = ast.parse(ast.unparse(node)).body[0]
            clone.decorator_list = []
            return ast.unparse(clone)
    raise AssertionError(f"{name} not found in bot.py")


class DeviceClassifierMatrixTest(unittest.TestCase):
    def test_iphone_safari_is_mobile(self):
        info = classify_device(UA_IPHONE_SAFARI)
        self.assertEqual(info["device_type"], "mobile")
        self.assertEqual(info["platform"], "ios")
        self.assertFalse(info["is_native_app"])

    def test_ipad_legacy_ua_is_tablet(self):
        self.assertEqual(classify_device(UA_IPAD_LEGACY)["device_type"], "tablet")

    def test_ipados_desktop_ua_with_client_hints_is_tablet(self):
        headers = {"Sec-CH-UA-Mobile": "?1", "Sec-CH-UA-Platform": '"macOS"'}
        info = classify_device(UA_IPADOS_DESKTOP, headers)
        self.assertEqual(info["device_type"], "tablet")

    def test_ipados_desktop_ua_without_hints_stays_desktop(self):
        # Documented best-effort limitation: no hints, Macintosh UA wins.
        self.assertEqual(classify_device(UA_IPADOS_DESKTOP)["device_type"], "desktop")

    def test_rn_cfnetwork_ua_is_mobile_native_likely(self):
        info = classify_device(UA_RN_CFNETWORK)
        self.assertEqual(info["device_type"], "mobile")
        self.assertEqual(info["platform"], "ios")
        self.assertTrue(info["native_likely"])
        self.assertFalse(info["is_native_app"])  # explicit signals only

    def test_pulsesoc_native_app_ua_is_native_mobile(self):
        info = classify_device(UA_NATIVE_APP)
        self.assertTrue(info["is_native_app"])
        self.assertEqual(info["device_type"], "mobile")
        self.assertEqual(info["platform"], "ios")

    def test_platform_header_marks_native(self):
        info = classify_device(UA_RN_CFNETWORK, {"X-PulseSoc-Platform": "ios"})
        self.assertTrue(info["is_native_app"])
        self.assertEqual(info["device_type"], "mobile")
        ipad = classify_device("", {"X-PulseSoc-Platform": "ipad"})
        self.assertTrue(ipad["is_native_app"])
        self.assertEqual(ipad["device_type"], "tablet")
        self.assertEqual(ipad["platform"], "ios")
        android = classify_device("", {"X-PulseSoc-Platform": "android"})
        self.assertTrue(android["is_native_app"])
        self.assertEqual(android["platform"], "android")

    def test_desktop_chrome_is_desktop(self):
        self.assertEqual(classify_device(UA_DESKTOP_CHROME)["device_type"], "desktop")

    def test_empty_ua_is_unknown_never_desktop(self):
        self.assertEqual(classify_device("")["device_type"], "unknown")
        self.assertEqual(classify_device(None)["device_type"], "unknown")
        self.assertEqual(classify_device("weird-bot/1.0")["device_type"], "unknown")

    def test_android_mobile_vs_tablet(self):
        self.assertEqual(classify_device(UA_ANDROID_PHONE)["device_type"], "mobile")
        self.assertEqual(classify_device(UA_ANDROID_TABLET)["device_type"], "tablet")

    def test_device_family_fingerprint_matches_across_safari_and_urlsession(self):
        # Deferred referral attribution depends on Safari-at-click and
        # URLSession-at-claim collapsing to the same coarse family.
        self.assertEqual(
            device_family_fingerprint(UA_IPHONE_SAFARI),
            device_family_fingerprint(UA_RN_CFNETWORK),
        )


class BotCallSitesDelegateTest(unittest.TestCase):
    def _source_of(self, name):
        return extract_function(name)

    def test_all_five_call_sites_delegate_to_canonical_classifier(self):
        for name in ("parse_device", "visitor_user_agent_meta", "presence_device_label", "native_app_request_context"):
            self.assertIn("classify_device", self._source_of(name), f"{name} must delegate to the canonical classifier")
        notification_src = (REPO / "services" / "notification_service.py").read_text(encoding="utf-8")
        self.assertIn("from .device_classification import classify_device", notification_src)
        self.assertNotIn('"mobile" if any(token in', notification_src)

    def test_native_context_accepts_platform_header(self):
        module_src = (REPO / "services" / "device_classification.py").read_text(encoding="utf-8")
        self.assertIn("X-PulseSoc-Platform", module_src)
        self.assertIn("classify_device", self._source_of("native_app_request_context"))


def _make_referral_db():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, referral_code TEXT, referred_by TEXT, updated_at TEXT)")
    cur.execute(
        """CREATE TABLE referral_events (id INTEGER PRIMARY KEY AUTOINCREMENT, referral_code TEXT,
           referrer_user_id INTEGER, session_id TEXT, landing_page TEXT, referrer TEXT, ip_hash TEXT, created_at TEXT)"""
    )
    cur.execute(
        """CREATE TABLE referral_deferred_claims (id INTEGER PRIMARY KEY AUTOINCREMENT, referral_code TEXT,
           ip_hash TEXT, ua_hash TEXT, created_at TEXT, claimed_at TEXT, claimed_user_id INTEGER)"""
    )
    cur.execute("CREATE TABLE referral_conversions (id INTEGER PRIMARY KEY AUTOINCREMENT, inviter_user_id INTEGER, referred_user_id INTEGER, referral_code TEXT, counted INTEGER, fraud_flag INTEGER, created_at TEXT)")
    cur.execute("INSERT INTO users (user_id, referral_code) VALUES (1, 'cpxreferrer')")
    cur.execute("INSERT INTO users (user_id, referral_code) VALUES (2, 'cpxnewuser')")
    conn.commit()
    return conn


class _NonClosingConn:
    """The extracted functions call conn.close(); keep the memory DB alive."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def close(self):
        pass


class _FakeRequest:
    def __init__(self, user_agent):
        self.headers = {"User-Agent": user_agent, "Referer": ""}
        self.cookies = {}
        self.path = "/r/cpxreferrer"


def _referral_namespace(conn, user_agent):
    import logging

    calls = []
    namespace = {
        "db": lambda: _NonClosingConn(conn),
        "clean_html": lambda value: str(value or ""),
        "client_ip_hash": lambda: "iphash-1",
        "referral_device_family_hash": lambda *a, **k: "uafamilyhash-ios-mobile",
        "record_referral_signup": lambda user_id, code: calls.append((user_id, code)),
        "redirect": lambda url, code=302: ("redirect", url, code),
        "request": _FakeRequest(user_agent),
        "quote": quote,
        "datetime": datetime,
        "timedelta": timedelta,
        "logging": logging,
        "has_request_context": lambda: True,
        "os": __import__("os"),
        "hashlib": __import__("hashlib"),
        "REFERRAL_DEFERRED_CLAIM_WINDOW_HOURS": 48,
        "PULSESOC_APP_STORE_FALLBACK_URL": "https://apps.apple.com/us/app/pulsesoc/id6777591572",
    }
    for name in (
        "pulsesoc_app_store_url",
        "is_ios_user_agent",
        "record_referral_deferred_claim",
        "claim_deferred_referral",
        "referral_redirect",
    ):
        exec(extract_function(name), namespace)  # noqa: S102 - repo test pattern
    namespace["_attribution_calls"] = calls
    return namespace


class ReferralRedirectTest(unittest.TestCase):
    def test_ios_ua_redirects_to_app_store_and_records_deferred_claim(self):
        conn = _make_referral_db()
        ns = _referral_namespace(conn, UA_IPHONE_SAFARI)
        kind, url, code = ns["referral_redirect"]("cpxreferrer")
        self.assertEqual(kind, "redirect")
        self.assertEqual(code, 302)
        self.assertTrue(url.startswith("https://apps.apple.com/"))
        rows = conn.execute("SELECT referral_code, ip_hash, ua_hash FROM referral_deferred_claims").fetchall()
        self.assertEqual(rows, [("cpxreferrer", "iphash-1", "uafamilyhash-ios-mobile")])
        # Click log still written.
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM referral_events").fetchone()[0], 1)

    def test_desktop_ua_keeps_web_redirect(self):
        conn = _make_referral_db()
        ns = _referral_namespace(conn, UA_DESKTOP_CHROME)
        kind, url, code = ns["referral_redirect"]("cpxreferrer")
        self.assertEqual(code, 302)
        self.assertIn("/?ref=cpxreferrer", url)
        self.assertIn("utm_source=referral", url)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM referral_deferred_claims").fetchone()[0], 0)

    def test_invalid_code_on_ios_still_goes_to_app_store_without_attribution(self):
        conn = _make_referral_db()
        ns = _referral_namespace(conn, UA_IPHONE_SAFARI)
        kind, url, code = ns["referral_redirect"]("nosuchcode")
        self.assertTrue(url.startswith("https://apps.apple.com/"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM referral_deferred_claims").fetchone()[0], 0)

    def test_destination_is_never_derived_from_request_input(self):
        src = extract_function("referral_redirect")
        self.assertIn("pulsesoc_app_store_url()", src)
        self.assertNotIn("request.args", src)
        url_src = extract_function("pulsesoc_app_store_url")
        self.assertIn("https://apps.apple.com/", url_src)
        self.assertNotIn("request.", url_src)


class DeferredClaimTest(unittest.TestCase):
    def _seed_click(self, conn, hours_ago=1, code="cpxreferrer"):
        created = (datetime.now() - timedelta(hours=hours_ago)).isoformat()
        conn.execute(
            "INSERT INTO referral_deferred_claims (referral_code, ip_hash, ua_hash, created_at) VALUES (?, ?, ?, ?)",
            (code, "iphash-1", "uafamilyhash-ios-mobile", created),
        )
        conn.commit()

    def test_claim_matches_recent_click_and_is_idempotent(self):
        conn = _make_referral_db()
        ns = _referral_namespace(conn, UA_RN_CFNETWORK)
        self._seed_click(conn)
        first = ns["claim_deferred_referral"](2, code="", ip_hash="iphash-1", ua_hash="uafamilyhash-ios-mobile")
        self.assertTrue(first["claimed"])
        self.assertEqual(ns["_attribution_calls"], [(2, "cpxreferrer")])
        row = conn.execute("SELECT claimed_user_id, claimed_at FROM referral_deferred_claims").fetchone()
        self.assertEqual(row[0], 2)
        self.assertTrue(row[1])
        self.assertEqual(conn.execute("SELECT referred_by FROM users WHERE user_id=2").fetchone()[0], "cpxreferrer")
        # Second call: no-op success, no duplicate attribution.
        second = ns["claim_deferred_referral"](2, code="", ip_hash="iphash-1", ua_hash="uafamilyhash-ios-mobile")
        self.assertTrue(second["claimed"])
        self.assertEqual(len(ns["_attribution_calls"]), 1)

    def test_clicks_older_than_48h_are_ignored(self):
        conn = _make_referral_db()
        ns = _referral_namespace(conn, UA_RN_CFNETWORK)
        self._seed_click(conn, hours_ago=49)
        result = ns["claim_deferred_referral"](2, code="", ip_hash="iphash-1", ua_hash="uafamilyhash-ios-mobile")
        self.assertFalse(result["claimed"])
        self.assertEqual(ns["_attribution_calls"], [])

    def test_explicit_valid_code_wins(self):
        conn = _make_referral_db()
        ns = _referral_namespace(conn, UA_RN_CFNETWORK)
        result = ns["claim_deferred_referral"](2, code="cpxreferrer", ip_hash="", ua_hash="")
        self.assertTrue(result["claimed"])
        self.assertEqual(ns["_attribution_calls"], [(2, "cpxreferrer")])

    def test_self_referral_rejected(self):
        conn = _make_referral_db()
        ns = _referral_namespace(conn, UA_RN_CFNETWORK)
        result = ns["claim_deferred_referral"](1, code="cpxreferrer", ip_hash="", ua_hash="")
        self.assertFalse(result["claimed"])
        self.assertEqual(ns["_attribution_calls"], [])

    def test_endpoint_never_reveals_referrer_identity(self):
        src = extract_function("api_mobile_referral_claim")
        self.assertNotIn("referrer_user_id", src)
        self.assertIn("'claimed'", src)


class SchemaRegistrationTest(unittest.TestCase):
    def test_deferred_claims_table_created_idempotently(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS referral_deferred_claims", BOT_SOURCE)

    def test_deferred_claims_registered_for_postgres_auto_pk(self):
        db_src = (REPO / "services" / "db.py").read_text(encoding="utf-8")
        self.assertIn('"referral_deferred_claims": "id"', db_src)


if __name__ == "__main__":
    unittest.main()
