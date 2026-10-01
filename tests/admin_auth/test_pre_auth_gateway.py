"""Pre-auth lockdown contract for the PulseSoc Operations Command Center.

The unauthenticated admin experience must be a closed door: a standalone
secure-access gateway that ships NONE of the authenticated shell — no sidebar
navigation labels, no status chips, no nav-index JSON, no admin JavaScript,
and no protected data. These tests pin that contract at the route level
against the real Flask app, plus the rate-limit helper in isolation.

Runs against a temp sqlite file so nothing touches coinpilotx.db.

Run: python3 -m pytest tests/admin_auth/test_pre_auth_gateway.py
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="admin_gateway_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import admin_gateway  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


# Every internal label the owner listed as forbidden pre-auth. Kept broad on
# purpose: if any of these strings shows up in the unauthenticated response
# body, internal structure is leaking again.
FORBIDDEN_PRE_AUTH_STRINGS = [
    "Dashboard", "Global Command", "Backend Command Center", "Support",
    "Admins", "Employees", "Departments", "Data Recovery", "PulseSoc Mod",
    "Ads Review Board", "Music Review", "Chat Reports", "Watch Rules",
    "Scam Shield", "Feed Health", "PulseSoc Analytics", "Education",
    "Transactions", "Payments Command Center", "Unmatched Payments",
    "Notifications", "Telegram", "AI Usage", "Predictions",
    "PulseSoc Infra", "Audit Logs", "Command Logs", "Visitors",
    # shell internals
    "ops-sidebar", "ops-topbar", "ops-status-strip", "ops-nav-index",
    "ops-palette", "admin_ops_center.js", "admin_ops_center.css",
    "/admin/ops/status.json", "/admin/ops/search.json",
    # every protected route href the old shell exposed
    "/admin/dashboard", "/admin/global-command", "/admin/users",
    "/admin/security", "/admin/audit-logs", "/admin/payments-command-center",
]

ADMIN_EMAIL = "owner@test.local"
ADMIN_PASSWORD = "Sup3r-Secret-Passw0rd!"


def _seed_admin():
    conn = sqlite3.connect(_DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM admin_users WHERE email=?", (ADMIN_EMAIL,))
    now = datetime.now().isoformat()
    cur.execute(
        "INSERT INTO admin_users (email, password_hash, role, status, failed_login_count, created_at, updated_at) "
        "VALUES (?, ?, 'owner', 'active', 0, ?, ?)",
        (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), now, now),
    )
    conn.commit()
    conn.close()


def _csrf_token(client):
    with client.session_transaction() as flask_session:
        token = flask_session.get("csrf_token")
    if token:
        return token
    # Token is minted on first gateway render.
    client.get("/admin/login")
    with client.session_transaction() as flask_session:
        return flask_session["csrf_token"]


class PreAuthGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.init_db()
        _seed_admin()

    def setUp(self):
        self.client = bot.webhook_app.test_client()

    # ------------------------------------------------------------------
    # Unauthenticated surface
    # ------------------------------------------------------------------

    def test_login_page_contains_no_internal_structure(self):
        response = self.client.get("/admin/login")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for forbidden in FORBIDDEN_PRE_AUTH_STRINGS:
            self.assertNotIn(forbidden, body, f"pre-auth leak: {forbidden!r}")
        for forbidden in admin_gateway.INTERNAL_NAV_LABELS:
            self.assertNotIn(forbidden, body, f"pre-auth leak: {forbidden!r}")

    def test_login_page_branding_and_security_copy(self):
        body = self.client.get("/admin/login").get_data(as_text=True)
        self.assertIn("PulseSoc", body.replace("Pulse<b>Soc</b>", "PulseSoc"))
        self.assertIn("Operations Command Center", body)
        self.assertIn("Secure Access Only", body)
        self.assertIn("Authorized personnel only", body)
        self.assertIn("monitored and logged", body)
        self.assertNotIn("CoinPlotXAI Inc", body)

    def test_login_page_supports_reduced_motion_and_a11y(self):
        body = self.client.get("/admin/login").get_data(as_text=True)
        self.assertIn("prefers-reduced-motion", body)
        self.assertIn("for='gw-email'", body)
        self.assertIn("for='gw-password'", body)
        self.assertIn("autocomplete='current-password'", body)

    def test_login_page_is_never_cached(self):
        response = self.client.get("/admin/login")
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_login_page_loads_no_admin_scripts(self):
        body = self.client.get("/admin/login").get_data(as_text=True)
        external_scripts = re.findall(r"<script[^>]+src=['\"]([^'\"]+)", body)
        self.assertEqual(external_scripts, [], "gateway must not load external admin JS")

    # ------------------------------------------------------------------
    # Server-side boundary
    # ------------------------------------------------------------------

    def test_protected_pages_redirect_to_gateway(self):
        for path in ("/admin", "/admin/dashboard", "/admin/users",
                     "/admin/security", "/admin/audit-logs"):
            response = self.client.get(path)
            self.assertIn(response.status_code, (301, 302), path)
            self.assertIn("/admin/login", response.headers.get("Location", ""), path)

    def test_protected_apis_reject_unauthenticated(self):
        for path in ("/admin/ops/status.json", "/admin/ops/search.json?q=x",
                     "/api/admin/users"):
            response = self.client.get(path)
            self.assertIn(response.status_code, (401, 403), path)

    def test_protected_page_responses_are_not_cacheable(self):
        response = self.client.get("/admin/dashboard")
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    def test_invalid_login_is_generic_no_enumeration(self):
        token = _csrf_token(self.client)
        wrong_password = self.client.post("/admin/login", data={
            "csrf_token": token, "email": ADMIN_EMAIL, "password": "nope"})
        unknown_email = self.client.post("/admin/login", data={
            "csrf_token": token, "email": "ghost@test.local", "password": "nope"})
        body_a = wrong_password.get_data(as_text=True)
        body_b = unknown_email.get_data(as_text=True)
        self.assertIn("Access denied.", body_a)
        self.assertIn("Access denied.", body_b)
        # Same generic copy for both failure classes.
        self.assertNotIn("password is wrong", body_a.lower())
        self.assertNotIn("exists", body_b.lower())
        _seed_admin()  # reset failed_login_count

    def test_valid_login_reaches_operations_center(self):
        _seed_admin()
        token = _csrf_token(self.client)
        response = self.client.post("/admin/login", data={
            "csrf_token": token, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/dashboard", response.headers.get("Location", ""))
        dashboard = self.client.get("/admin/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        body = dashboard.get_data(as_text=True)
        # Authenticated shell renders sidebar and status chips only now.
        self.assertIn("ops-sidebar", body)
        self.assertIn("ops-status-strip", body)
        self.client.get("/admin/logout")

    def test_logout_invalidates_session_and_blocks_back_navigation(self):
        _seed_admin()
        token = _csrf_token(self.client)
        self.client.post("/admin/login", data={
            "csrf_token": token, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        logout = self.client.get("/admin/logout")
        self.assertIn("/admin/login", logout.headers.get("Location", ""))
        after = self.client.get("/admin/dashboard")
        self.assertIn(after.status_code, (301, 302))
        self.assertIn("/admin/login", after.headers.get("Location", ""))
        # no-store means the browser cannot serve the old dashboard from cache.
        self.assertIn("no-store", after.headers.get("Cache-Control", ""))

    def test_expired_session_shows_neutral_notice(self):
        body = self.client.get("/admin/login?expired=1").get_data(as_text=True)
        self.assertIn("Session ended", body)
        for forbidden in FORBIDDEN_PRE_AUTH_STRINGS:
            self.assertNotIn(forbidden, body)

    def test_login_without_csrf_token_denied(self):
        response = self.client.post("/admin/login", data={
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Access denied.", response.get_data(as_text=True))


class AdminAuthHardeningTests(unittest.TestCase):
    """Route-level pins for the final hardening pass: CSRF-rejected auditing,
    identifier throttling wiring, session lifetimes, logout invalidation,
    /admin-dashboard fail-closed, and gateway script isolation."""

    @classmethod
    def setUpClass(cls):
        bot.init_db()
        _seed_admin()

    def setUp(self):
        self.client = bot.webhook_app.test_client()
        _seed_admin()

    def _audit_count(self, action, since=None):
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        if since:
            cur.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action=? AND created_at>=?", (action, since))
        else:
            cur.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action=?", (action,))
        n = cur.fetchone()[0]
        conn.close()
        return n

    def _login(self):
        token = _csrf_token(self.client)
        return self.client.post("/admin/login", data={
            "csrf_token": token, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    def test_csrf_rejected_login_is_audited_and_hashed(self):
        since = datetime.now().isoformat()
        self.client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": "x"})
        self.assertGreaterEqual(self._audit_count("admin_login_csrf_rejected", since), 1)
        # Identifier failure row exists and never contains the raw email.
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT target_id, metadata FROM admin_audit_logs WHERE action=? AND created_at>=?",
            (admin_gateway.IDENTIFIER_FAILED_ACTION, since))
        rows = cur.fetchall()
        conn.close()
        self.assertGreaterEqual(len(rows), 1)
        for target_id, metadata in rows:
            self.assertNotIn(ADMIN_EMAIL, target_id or "")
            self.assertNotIn(ADMIN_EMAIL, metadata or "")
            self.assertEqual(len(target_id or ""), 64)

    def test_failed_login_records_identifier_dimension(self):
        since = datetime.now().isoformat()
        token = _csrf_token(self.client)
        self.client.post("/admin/login", data={
            "csrf_token": token, "email": ADMIN_EMAIL, "password": "wrong"})
        self.assertGreaterEqual(self._audit_count(admin_gateway.IDENTIFIER_FAILED_ACTION, since), 1)
        _seed_admin()

    def test_successful_login_rotates_and_stamps_session(self):
        response = self._login()
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as flask_session:
            self.assertTrue(flask_session.get("admin_user_id"))
            self.assertTrue(flask_session.get("admin_session_issued_at"))
            self.assertTrue(flask_session.get("admin_session_last_seen"))
            # Pre-auth CSRF token was rotated away at the boundary.
            self.assertIsNone(flask_session.get("csrf_token"))
        self.client.get("/admin/logout")

    def test_absolute_session_lifetime_expires(self):
        self._login()
        stale = (datetime.now() - timedelta(hours=bot.ADMIN_SESSION_ABSOLUTE_HOURS + 1)).isoformat()
        with self.client.session_transaction() as flask_session:
            flask_session["admin_session_issued_at"] = stale
            flask_session["admin_session_last_seen"] = datetime.now().isoformat()
        since = datetime.now().isoformat()
        response = self.client.get("/admin/dashboard")
        self.assertIn(response.status_code, (301, 302))
        self.assertIn("/admin/login", response.headers.get("Location", ""))
        self.assertGreaterEqual(self._audit_count("admin_session_expired", since), 1)
        with self.client.session_transaction() as flask_session:
            self.assertIsNone(flask_session.get("admin_user_id"))

    def test_idle_session_lifetime_expires(self):
        self._login()
        idle = (datetime.now() - timedelta(minutes=bot.ADMIN_SESSION_IDLE_MINUTES + 5)).isoformat()
        with self.client.session_transaction() as flask_session:
            flask_session["admin_session_last_seen"] = idle
        response = self.client.get("/admin/dashboard")
        self.assertIn(response.status_code, (301, 302))
        self.assertIn("/admin/login", response.headers.get("Location", ""))

    def test_legacy_session_without_lifetime_metadata_expires(self):
        with self.client.session_transaction() as flask_session:
            flask_session["admin_user_id"] = 1  # pre-hardening cookie shape
        response = self.client.get("/admin/dashboard")
        self.assertIn(response.status_code, (301, 302))
        self.assertIn("/admin/login", response.headers.get("Location", ""))

    def test_logout_clears_all_admin_session_keys(self):
        self._login()
        self.client.get("/admin/dashboard")  # mint post-auth csrf via render
        self.client.get("/admin/logout")
        with self.client.session_transaction() as flask_session:
            for key in ("admin_user_id", "admin_session_issued_at",
                        "admin_session_last_seen", "csrf_token"):
                self.assertIsNone(flask_session.get(key), key)

    def test_admin_dashboard_route_fails_closed_anonymously(self):
        response = self.client.get("/admin-dashboard")
        self.assertEqual(response.status_code, 401)
        response = self.client.get("/admin-dashboard?token=guess")
        self.assertEqual(response.status_code, 401)

    def test_admin_dashboard_route_allows_admin_session(self):
        self._login()
        response = self.client.get("/admin-dashboard")
        self.assertEqual(response.status_code, 200)
        self.client.get("/admin/logout")

    def test_gateway_exempt_from_public_script_injection(self):
        body = self.client.get("/admin/login").get_data(as_text=True)
        self.assertNotIn("pulse_pwa_install.js", body)
        self.assertNotIn("pulse_i18n.js", body)

    def test_simple_content_types_enforce_csrf_on_admin_posts(self):
        self._login()
        for content_type in ("text/plain",):
            response = self.client.post(
                "/admin/users", data="x", content_type=content_type)
            self.assertEqual(response.status_code, 400, content_type)
        self.client.get("/admin/logout")


if __name__ == "__main__":
    unittest.main()
