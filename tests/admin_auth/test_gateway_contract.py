"""Standalone contract tests for services.admin_gateway (no Flask needed).

These pin the closed-door property of the rendered gateway HTML itself and the
sliding-window login rate limiter, independent of the web stack. Route-level
enforcement is covered in test_pre_auth_gateway.py, which imports the real app.

Run: python3 -m unittest tests.admin_auth.test_gateway_contract
"""

import os
import re
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import admin_gateway  # noqa: E402

FORBIDDEN_PRE_AUTH_STRINGS = [
    "Dashboard", "Global Command", "Backend Command Center", "Support",
    "Admins", "Employees", "Departments", "Data Recovery", "PulseSoc Mod",
    "Ads Review Board", "Music Review", "Chat Reports", "Watch Rules",
    "Scam Shield", "Feed Health", "PulseSoc Analytics", "Education",
    "Transactions", "Payments Command Center", "Unmatched Payments",
    "Notifications", "Telegram", "AI Usage", "Predictions", "PulseSoc Infra",
    "Audit Logs", "Command Logs", "Visitors",
    "ops-sidebar", "ops-topbar", "ops-status-strip", "ops-nav-index",
    "ops-palette", "admin_ops_center.js", "admin_ops_center.css",
    "/admin/ops/status.json", "/admin/ops/search.json",
    "/admin/dashboard", "/admin/global-command", "/admin/users",
    "/admin/security", "/admin/audit-logs", "/admin/payments-command-center",
]


class GatewayHtmlContractTests(unittest.TestCase):
    def render(self, state="idle"):
        return admin_gateway.render_gateway("test-csrf-token", state=state)

    def test_no_internal_structure_in_any_state(self):
        for state in ("idle", "denied", "rate_limited", "expired", "unavailable"):
            body = self.render(state)
            for forbidden in FORBIDDEN_PRE_AUTH_STRINGS:
                self.assertNotIn(forbidden, body, f"{state}: leak {forbidden!r}")
            for forbidden in admin_gateway.INTERNAL_NAV_LABELS:
                self.assertNotIn(forbidden, body, f"{state}: leak {forbidden!r}")

    def test_only_login_admin_href(self):
        body = self.render()
        admin_refs = set(re.findall(r"/admin/[a-z\-]+", body))
        self.assertEqual(admin_refs, {"/admin/login"})

    def test_no_external_scripts(self):
        for state in ("idle", "denied"):
            scripts = re.findall(r"<script[^>]+src=", self.render(state))
            self.assertEqual(scripts, [])

    def test_branding_and_security_copy(self):
        body = self.render()
        self.assertIn("Operations Command Center", body)
        self.assertIn("Secure Access Only", body)
        self.assertIn("Authorized personnel only", body)
        self.assertIn("monitored and logged", body)
        self.assertNotIn("CoinPlotXAI", body)
        self.assertNotIn("military-grade", body.lower())

    def test_generic_error_copy(self):
        denied = self.render("denied")
        self.assertIn("Access denied.", denied)
        self.assertNotIn("password", denied.split("gw-form")[0].lower().replace(
            "secure access", ""))  # alert region names no credential detail
        limited = self.render("rate_limited")
        self.assertIn("Too many attempts", limited)
        expired = self.render("expired")
        self.assertIn("Session ended", expired)
        unavailable = self.render("unavailable")
        self.assertIn("temporarily unavailable", unavailable)

    def test_form_fields_and_a11y(self):
        body = self.render()
        self.assertIn("name='email'", body)
        self.assertIn("name='password'", body)
        self.assertIn("name='csrf_token'", body)
        self.assertIn("test-csrf-token", body)
        self.assertIn("for='gw-email'", body)
        self.assertIn("for='gw-password'", body)
        self.assertIn("prefers-reduced-motion", body)
        self.assertIn("autocomplete='current-password'", body)
        self.assertIn("focus-visible", body)

    def test_rate_limited_state_disables_submit(self):
        self.assertIn("type='submit' disabled", self.render("rate_limited"))

    def test_notice_page_is_also_closed_door(self):
        body = admin_gateway.render_notice("Owner Bootstrap Status", "<p>hello</p>")
        for forbidden in FORBIDDEN_PRE_AUTH_STRINGS:
            if forbidden == "/admin/login":
                continue
            self.assertNotIn(forbidden, body)


class LoginRateLimitTests(unittest.TestCase):
    def _conn_with_failures(self, count, ip_hash="src-hash", minutes_ago=1):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE admin_audit_logs (id INTEGER PRIMARY KEY, admin_user_id INTEGER,"
            " admin_email TEXT, action TEXT, target_type TEXT, target_id TEXT,"
            " metadata TEXT, ip_hash TEXT, created_at TEXT)"
        )
        stamp = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()
        for _ in range(count):
            conn.execute(
                "INSERT INTO admin_audit_logs (action, ip_hash, created_at) "
                "VALUES ('admin_login_failed', ?, ?)", (ip_hash, stamp))
        conn.commit()
        return conn

    def test_under_threshold_not_limited(self):
        conn = self._conn_with_failures(admin_gateway.RATE_LIMIT_MAX_FAILURES - 1)
        self.assertFalse(admin_gateway.login_rate_limited(conn, "src-hash"))

    def test_at_threshold_limited(self):
        conn = self._conn_with_failures(admin_gateway.RATE_LIMIT_MAX_FAILURES)
        self.assertTrue(admin_gateway.login_rate_limited(conn, "src-hash"))

    def test_old_failures_expire(self):
        conn = self._conn_with_failures(
            admin_gateway.RATE_LIMIT_MAX_FAILURES + 5,
            minutes_ago=admin_gateway.RATE_LIMIT_WINDOW_MINUTES + 5)
        self.assertFalse(admin_gateway.login_rate_limited(conn, "src-hash"))

    def test_other_sources_unaffected(self):
        conn = self._conn_with_failures(admin_gateway.RATE_LIMIT_MAX_FAILURES)
        self.assertFalse(admin_gateway.login_rate_limited(conn, "different-hash"))

    def test_empty_ip_hash_fails_open(self):
        conn = self._conn_with_failures(admin_gateway.RATE_LIMIT_MAX_FAILURES, ip_hash="")
        self.assertFalse(admin_gateway.login_rate_limited(conn, ""))

    def test_success_events_do_not_count(self):
        conn = self._conn_with_failures(0)
        stamp = datetime.now().isoformat()
        for _ in range(admin_gateway.RATE_LIMIT_MAX_FAILURES + 5):
            conn.execute(
                "INSERT INTO admin_audit_logs (action, ip_hash, created_at) "
                "VALUES ('admin_login_success', 'src-hash', ?)", (stamp,))
        conn.commit()
        self.assertFalse(admin_gateway.login_rate_limited(conn, "src-hash"))


class IdentifierRateLimitTests(unittest.TestCase):
    """Second throttling dimension: hashed login identifier."""

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE admin_audit_logs (id INTEGER PRIMARY KEY, admin_user_id INTEGER,"
            " admin_email TEXT, action TEXT, target_type TEXT, target_id TEXT,"
            " metadata TEXT, ip_hash TEXT, created_at TEXT)"
        )
        return conn

    def _add_identifier_failures(self, conn, count, identifier_hash, minutes_ago=1):
        stamp = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()
        for _ in range(count):
            conn.execute(
                "INSERT INTO admin_audit_logs (action, target_id, created_at) VALUES (?, ?, ?)",
                (admin_gateway.IDENTIFIER_FAILED_ACTION, identifier_hash, stamp))
        conn.commit()

    def test_hash_identifier_normalizes_and_is_salted(self):
        a = admin_gateway.hash_identifier("Owner@Example.com ", salt="s1")
        b = admin_gateway.hash_identifier("owner@example.com", salt="s1")
        c = admin_gateway.hash_identifier("owner@example.com", salt="s2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 64)
        self.assertNotIn("owner", a)

    def test_identifier_threshold_limits_across_sources(self):
        conn = self._conn()
        idh = admin_gateway.hash_identifier("target@x.com")
        self._add_identifier_failures(conn, admin_gateway.RATE_LIMIT_MAX_IDENTIFIER_FAILURES, idh)
        # Fresh source IP, but the identifier itself is saturated.
        self.assertTrue(admin_gateway.login_rate_limited(conn, "fresh-src", idh))

    def test_identifier_under_threshold_not_limited(self):
        conn = self._conn()
        idh = admin_gateway.hash_identifier("target@x.com")
        self._add_identifier_failures(conn, admin_gateway.RATE_LIMIT_MAX_IDENTIFIER_FAILURES - 1, idh)
        self.assertFalse(admin_gateway.login_rate_limited(conn, "fresh-src", idh))

    def test_identifier_failures_expire(self):
        conn = self._conn()
        idh = admin_gateway.hash_identifier("target@x.com")
        self._add_identifier_failures(
            conn, admin_gateway.RATE_LIMIT_MAX_IDENTIFIER_FAILURES + 3, idh,
            minutes_ago=admin_gateway.RATE_LIMIT_WINDOW_MINUTES + 5)
        self.assertFalse(admin_gateway.login_rate_limited(conn, "fresh-src", idh))

    def test_csrf_rejected_counts_toward_source_dimension(self):
        conn = self._conn()
        stamp = datetime.now().isoformat()
        for _ in range(admin_gateway.RATE_LIMIT_MAX_FAILURES):
            conn.execute(
                "INSERT INTO admin_audit_logs (action, ip_hash, created_at) "
                "VALUES ('admin_login_csrf_rejected', 'src-hash', ?)", (stamp,))
        conn.commit()
        self.assertTrue(admin_gateway.login_rate_limited(conn, "src-hash"))


class MemoryFallbackTests(unittest.TestCase):
    """Fail-safe (not fail-open) limiter fallback with guaranteed expiry."""

    class _BrokenConn:
        def cursor(self):
            raise RuntimeError("audit store unavailable")

    def setUp(self):
        admin_gateway._MEMORY_WINDOW.clear()

    def tearDown(self):
        admin_gateway._MEMORY_WINDOW.clear()

    def test_db_error_without_failures_stays_open(self):
        # Owner must not be locked out just because the audit store is down.
        self.assertFalse(admin_gateway.login_rate_limited(self._BrokenConn(), "src", "idh"))

    def test_db_error_with_memory_failures_limits(self):
        for _ in range(admin_gateway.RATE_LIMIT_MAX_FAILURES):
            admin_gateway.note_failure(ip_hash="src")
        self.assertTrue(admin_gateway.login_rate_limited(self._BrokenConn(), "src", ""))

    def test_memory_identifier_dimension(self):
        for _ in range(admin_gateway.RATE_LIMIT_MAX_IDENTIFIER_FAILURES):
            admin_gateway.note_failure(identifier_hash="idh")
        self.assertTrue(admin_gateway.login_rate_limited(self._BrokenConn(), "other-src", "idh"))

    def test_memory_window_expires_no_permanent_lockout(self):
        old = datetime.now() - timedelta(minutes=admin_gateway.RATE_LIMIT_WINDOW_MINUTES + 5)
        for _ in range(admin_gateway.RATE_LIMIT_MAX_FAILURES + 10):
            admin_gateway.note_failure(ip_hash="src", now=old)
        self.assertFalse(admin_gateway.login_rate_limited(self._BrokenConn(), "src", ""))


class CinematicPresentationContractTests(unittest.TestCase):
    """The visual layer must stay decorative, factual, and isolated."""

    def render(self, state="idle"):
        return admin_gateway.render_gateway("test-csrf-token", state=state)

    def test_real_logo_asset_referenced(self):
        body = self.render()
        self.assertIn(admin_gateway.GATEWAY_MARK_SRC, body)
        self.assertIn("/static/brand/pulsesoc-gateway-mark-", body)

    def test_threat_state_only_on_denied_and_rate_limited(self):
        self.assertIn("gw gw-threat", self.render("denied"))
        self.assertIn("gw gw-threat", self.render("rate_limited"))
        for state in ("idle", "expired", "unavailable"):
            self.assertNotIn("gw-threat'", self.render(state).split("<body")[1].split(">")[0])

    def test_no_military_or_government_claims(self):
        for state in ("idle", "denied"):
            low = self.render(state).lower()
            for claim in ("military", "government-grade", "nsa", "pentagon", "classified"):
                self.assertNotIn(claim, low)

    def test_no_operational_api_calls_in_gateway_js(self):
        body = self.render()
        # The only fetch permitted is the login form's own POST to its action.
        for endpoint in ("/api/", "status.json", "search.json", "EventSource", "WebSocket", "setInterval"):
            self.assertNotIn(endpoint, body)

    def test_reduced_motion_collapses_animation(self):
        body = self.render()
        self.assertIn("prefers-reduced-motion", body)
        reduced = body.split("prefers-reduced-motion", 1)[1]
        self.assertIn("animation", reduced)

    def test_form_immediately_interactive(self):
        body = self.render()
        self.assertNotIn("type='submit' disabled", body)  # idle: enabled
        form_markup = body.split("<section class='gw-card'>", 1)[1].split("</form>")[0]
        self.assertNotIn("pointer-events:none", form_markup)
        self.assertIn("autofocus", form_markup)


if __name__ == "__main__":
    unittest.main()
