#!/usr/bin/env python3
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


FAILURES = []


def require(condition, message):
    if not condition:
        FAILURES.append(message)


def fetch_csrf(client):
    response = client.get("/login")
    html = response.get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    require(match, "login page exposes CSRF token")
    return match.group(1) if match else ""


def cleanup(domain, ip=""):
    conn = sqlite3.connect(ROOT / "coinpilotx.db")
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM auth_events WHERE email_domain=?", (domain,))
        if ip:
            cur.execute("DELETE FROM auth_events WHERE ip_address=?", (ip,))
            cur.execute("DELETE FROM security_events WHERE details_json LIKE ?", (f"%{ip}%",))
        cur.execute("DELETE FROM failed_login_controls WHERE control_value=? OR control_value LIKE ?", (domain, f"%@{domain}"))
        if ip:
            cur.execute("DELETE FROM failed_login_controls WHERE control_value=?", (ip,))
        cur.execute("DELETE FROM failed_login_safe_list WHERE control_value=? OR control_value LIKE ?", (domain, f"%@{domain}"))
        if ip:
            cur.execute("DELETE FROM failed_login_safe_list WHERE control_value=?", (ip,))
        conn.commit()
    finally:
        conn.close()


def main():
    source = (ROOT / "bot.py").read_text()
    template = (ROOT / "templates" / "account.html").read_text()
    require("FAILED_LOGIN_IP_LIMIT" in source, "per-IP failed login limit is defined")
    require("FAILED_LOGIN_EMAIL_LIMIT" in source, "per-email failed login limit is defined")
    require("FAILED_LOGIN_DOMAIN_LIMIT" in source, "per-domain failed login limit is defined")
    require("failed_login_controls" in source, "temporary failed-login controls table exists")
    require("failed_login_safe_list" in source, "mark-safe table exists")
    require("login_challenge_answer" in template, "web login renders challenge answer input")
    require("Block IP" in source and "Block Domain" in source and "Mark Safe" in source, "admin security actions exist")
    require("Failed Logins" in source and "Blocked IPs" in source and "Suspicious Domains" in source and "Admin Actions" in source, "admin security filter tabs exist")
    require("auth_email_hash" in source and "email_hash" in source, "per-email limits use a private hash instead of full public email")

    domain = f"qa-bruteforce-{int(time.time())}.invalid"
    email = f"attack@{domain}"
    qa_ip = f"198.51.100.{int(time.time()) % 200 + 20}"
    cleanup(domain, qa_ip)
    bot.init_db()
    client = bot.webhook_app.test_client()
    for _ in range(bot.FAILED_LOGIN_CHALLENGE_AFTER):
        csrf = fetch_csrf(client)
        response = client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": "wrong-password"},
            headers={"User-Agent": "PulseSoc QA Browser", "X-Forwarded-For": qa_ip, "CF-IPCountry": "US"},
        )
        require(response.status_code in {200, 401}, "initial failed login stays non-lockout before challenge threshold")

    csrf = fetch_csrf(client)
    challenged = client.post(
        "/login",
        data={"csrf_token": csrf, "email": email, "password": "wrong-password"},
        headers={"User-Agent": "PulseSoc QA Browser", "X-Forwarded-For": qa_ip, "CF-IPCountry": "US"},
    )
    challenge_html = challenged.get_data(as_text=True)
    require(challenged.status_code == 403, "suspicious repeated login requires challenge")
    require("Security Check" in challenge_html and "login_challenge_answer" in challenge_html, "challenge is visible on web login")

    conn = sqlite3.connect(ROOT / "coinpilotx.db")
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM users WHERE lower(email) LIKE ?", (f"%@{domain}",))
        require(int(cur.fetchone()["total"] or 0) == 0, "failed-login audit domain did not create accounts")
        cur.execute("SELECT ip_address, country, user_agent, device, route, severity, email FROM auth_events WHERE email_domain=? ORDER BY id DESC LIMIT 1", (domain,))
        row = cur.fetchone()
        require(bool(row), "failed login auth event was written")
        if row:
            require(row["ip_address"] == qa_ip, "failed login stores IP address")
            require(row["country"] == "US", "failed login stores country")
            require("PulseSoc QA Browser" in (row["user_agent"] or ""), "failed login stores user agent")
            require(bool(row["device"]), "failed login stores device")
            require(row["route"] == "/login", "failed login stores route")
            require(row["severity"] in {"Low", "Medium", "High", "Critical"}, "failed login stores severity label")
            require("@"+domain in row["email"] and "attack@" not in row["email"], "admin email value remains masked")
        cur.execute("SELECT COUNT(*) AS total FROM security_events WHERE event_type='failed_login_burst' AND details_json LIKE ?", (f"%{domain}%",))
        require(int(cur.fetchone()["total"] or 0) >= 1, "failed-login burst admin alert is logged")
    finally:
        conn.close()
        cleanup(domain, qa_ip)

    if FAILURES:
        print("FAILED_LOGIN_SECURITY_AUDIT_FAIL")
        for failure in FAILURES:
            print(f"- {failure}")
        sys.exit(1)
    print("FAILED_LOGIN_SECURITY_AUDIT_PASS")


if __name__ == "__main__":
    main()
