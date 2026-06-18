#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


FAILURES = []


def require(condition, message):
    if not condition:
        FAILURES.append(message)


def audit_legacy_schema_migration():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE admin_audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT)")
    cur.execute("CREATE TABLE admin_activity_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT)")
    bot.add_columns_if_missing(cur, "admin_audit_logs", [
        ("admin_user_id", "INTEGER"),
        ("admin_email", "TEXT"),
        ("target_type", "TEXT"),
        ("target_id", "TEXT"),
        ("metadata", "TEXT"),
        ("ip_hash", "TEXT"),
        ("created_at", "TEXT"),
    ], conn=conn)
    bot.add_columns_if_missing(cur, "admin_activity_logs", [
        ("admin_user_id", "INTEGER"),
        ("department", "TEXT"),
        ("route", "TEXT"),
        ("target_type", "TEXT"),
        ("target_id", "TEXT"),
        ("before_json", "TEXT"),
        ("after_json", "TEXT"),
        ("ip_hash", "TEXT"),
        ("user_agent", "TEXT"),
        ("created_at", "TEXT"),
    ], conn=conn)
    audit_columns = bot.migration_table_columns(cur, "admin_audit_logs")
    activity_columns = bot.migration_table_columns(cur, "admin_activity_logs")
    require("admin_email" in audit_columns, "legacy admin_audit_logs receives admin_email")
    require("target_type" in audit_columns and "target_id" in audit_columns, "legacy admin_audit_logs receives target columns")
    require("department" in activity_columns and "route" in activity_columns, "legacy admin_activity_logs receives admin security columns")
    conn.close()


def audit_admin_security_route():
    source = (ROOT / "bot.py").read_text()
    require("HAVING COUNT(*) >= 3" in source, "suspicious-domain query avoids PostgreSQL SELECT alias in HAVING")
    require("HAVING failures>=3" not in source and "HAVING failures >= 3" not in source, "suspicious-domain query is PostgreSQL-compatible")
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id FROM admin_users WHERE status='active' AND lower(role) IN ('owner','super_admin') ORDER BY id LIMIT 1")
    admin = cur.fetchone()
    require(bool(admin), "active owner or super admin exists for admin security route audit")
    admin_id = int(admin["id"]) if admin else 0
    conn.close()

    client = bot.webhook_app.test_client()
    anonymous = client.get("/admin/security")
    require(anonymous.status_code in {301, 302}, "anonymous users are redirected away from admin security")
    require("/admin/login" in (anonymous.headers.get("Location") or ""), "anonymous admin security redirect points to admin login")

    if admin_id:
        with client.session_transaction() as session:
            session["admin_user_id"] = admin_id
        response = client.get("/admin/security")
        html = response.get_data(as_text=True)
        require(response.status_code == 200, "admin security route renders for authorized admin")
        require("Security Center" in html, "admin security page title renders")
        require("Failed Logins" in html and "Blocked IPs" in html and "Suspicious Domains" in html and "Admin Actions" in html, "admin security filter tabs render")
        require("Block IP" in html and "Block Domain" in html and "Mark Safe" in html, "admin security action buttons render")
        require("Emails remain masked" in html, "admin security page documents masked emails")


def main():
    audit_legacy_schema_migration()
    audit_admin_security_route()
    if FAILURES:
        print("ADMIN_SECURITY_PAGE_AUDIT_FAIL")
        for failure in FAILURES:
            print(f"- {failure}")
        sys.exit(1)
    print("ADMIN_SECURITY_PAGE_AUDIT_PASS")


if __name__ == "__main__":
    main()
