# =========================
# IMPORTS
# =========================

import os
import re
import json
import math
import csv
import io
import hashlib
import secrets
import sqlite3
import logging
import requests
import threading
import stripe
import smtplib
import time
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlparse
from flask import Flask, request, render_template, send_from_directory, jsonify, Response, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
# =========================
# 💳 STRIPE CONFIG (RIGHT AFTER CONSTANTS)
# =========================
BOT_NAME = "CoinPilotX"
DB_FILE = "coinpilotx.db"

BOT_TOKEN = os.getenv("BOT_TOKEN")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

STRIPE_PRO_LINK = "https://buy.stripe.com/14AdR90xmgAy304afs4Vy00"

stripe.api_key = STRIPE_SECRET_KEY

BTC_PAYMENT_ADDRESS = os.getenv("BTC_PAYMENT_ADDRESS", "0x8DE1A7eAb2C937cdCdC24E8F79B0ac0960040CD8")
BTC_PRO_PRICE = "0.00025 BTC"
BTC_PRO_SATS = 25000
BTC_REQUIRED_CONFIRMATIONS = 1
BLOCKSTREAM_TX_API = "https://blockstream.info/api/tx/"

ADMIN_USER_IDS = set()

SIGNAL_CHECK_SECONDS = 3600
HOURLY_UPDATE_SECONDS = 3600
ALERT_THRESHOLD_PERCENT = 0.25
WHALE_CHECK_SECONDS = int(os.getenv("WHALE_CHECK_SECONDS", "900"))
PORTFOLIO_TRACK_SECONDS = int(os.getenv("PORTFOLIO_TRACK_SECONDS", "900"))
WHALE_NOTIONAL_USD_THRESHOLD = float(os.getenv("WHALE_NOTIONAL_USD_THRESHOLD", "1000000"))
INTELLIGENCE_FEED_CACHE = {"data": None, "created_at": None}
INTELLIGENCE_FEED_CACHE_SECONDS = int(os.getenv("INTELLIGENCE_FEED_CACHE_SECONDS", "15"))
MARKETS_CACHE = {"data": None, "created_at": None}
MARKETS_CACHE_SECONDS = int(os.getenv("MARKETS_CACHE_SECONDS", "60"))
SPORTS_EDGE_CACHE = {"data": None, "created_at": None}
SPORTS_EDGE_CACHE_SECONDS = int(os.getenv("SPORTS_EDGE_CACHE_SECONDS", "60"))
SPORTS_EDGE_DETAIL_CACHE = {}
ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().isdigit()
}

# =========================
# 🌐 WEBHOOK APP (RIGHT AFTER STRIPE)
# =========================
webhook_app = Flask(__name__, template_folder="templates", static_folder="static")
webhook_app.secret_key = os.getenv("FLASK_SECRET_KEY", os.getenv("SECRET_KEY", secrets.token_hex(32)))
webhook_app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",
)
app = webhook_app

# =========================
# STATE VARIABLES
# =========================
onboarding_state = {}
pending_trades = {}

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename="coinpilotx.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

# =========================
# PHASE 5 DATABASE UPGRADE
# =========================

# =========================
# DATABASE HELPER
# =========================

# =========================
# INIT DB
# =========================
# =========================
# DATABASE HELPER
# =========================
DB_FILE = "coinpilotx.db"

def db():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url.startswith("sqlite:///"):
        return sqlite3.connect(database_url.replace("sqlite:///", "", 1))
    if database_url.startswith("file:"):
        return sqlite3.connect(database_url, uri=True)
    return sqlite3.connect(DB_FILE)


# =========================
# INIT DB
# =========================
def init_db():
    conn = db()
    cur = conn.cursor()

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        display_name TEXT,
        email TEXT,
        signup_time TEXT,
        onboarding_complete INTEGER DEFAULT 0,
        alerts_enabled INTEGER DEFAULT 0,
        is_pro INTEGER DEFAULT 0
    )
    """)

    # WATCHLIST
    cur.execute("""
    CREATE TABLE IF NOT EXISTS watchlists (
        user_id INTEGER,
        asset TEXT,
        PRIMARY KEY (user_id, asset)
    )
    """)

    # MANUAL PORTFOLIO
    cur.execute("""
    CREATE TABLE IF NOT EXISTS manual_portfolio (
        user_id INTEGER,
        asset TEXT,
        amount REAL,
        PRIMARY KEY (user_id, asset)
    )
    """)

    # PAPER PORTFOLIO
    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_portfolio (
        user_id INTEGER,
        asset TEXT,
        amount REAL,
        PRIMARY KEY (user_id, asset)
    )
    """)

    # LAST PRICES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS last_prices (
        user_id INTEGER,
        asset TEXT,
        price REAL,
        PRIMARY KEY (user_id, asset)
    )
    """)

    # ALERT HISTORY
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alerts_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        asset TEXT,
        action TEXT,
        price REAL,
        change_pct REAL,
        created_at TEXT
    )
    """)

    # LAST SIGNAL (ANTI-SPAM)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS last_signals (
        user_id INTEGER,
        asset TEXT,
        action TEXT,
        confidence TEXT,
        created_at TEXT,
        PRIMARY KEY (user_id, asset)
    )
    """)

    # PRICE HISTORY (IMPORTANT — you were missing this earlier)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset TEXT,
        price REAL,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()


# =========================
# HELPER FUNCTIONS
# =========================

def get_last_signal(user_id, asset):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT action, confidence FROM last_signals WHERE user_id=? AND asset=?",
        (user_id, asset)
    )
    row = cur.fetchone()
    conn.close()
    return row if row else None


def set_last_signal(user_id, asset, action, confidence):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO last_signals VALUES (?, ?, ?, ?, ?)",
        (user_id, asset, action, confidence, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_manual_holding(user_id, asset):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount FROM manual_portfolio WHERE user_id=? AND asset=?",
        (user_id, asset)
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0.0
def is_pro(user_id):
    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("SELECT is_pro FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
    except Exception:
        row = None

    conn.close()
    return bool(row and row[0] == 1)


# =========================
# PHASE 5 ALERT ENGINE
# =========================

async def market_signal_job(context: ContextTypes.DEFAULT_TYPE):
    users = get_users_with_alerts()

    for user_id in users:
        assets = get_watchlist(user_id)

        for asset in assets:
            price_now, _ = get_best_price(asset)

            if not price_now:
                continue

            previous_price = get_last_price(user_id, asset)
            set_last_price(user_id, asset, price_now)

            if previous_price is None:
                save_price_history(asset, price_now)
                continue

            change_pct = ((price_now - previous_price) / previous_price) * 100

            save_price_history(asset, price_now)
            signal_data = smart_market_signal(asset, price_now)

            action = signal_data["action"]
            confidence = signal_data["confidence"]

            if action not in ["BUY", "SELL"]:
                continue

            if confidence not in ["Medium", "High"]:
                continue

            last_signal = get_last_signal(user_id, asset)

            if last_signal:
                last_action, _ = last_signal

                # PREVENT SPAM (same signal + small movement)
                if last_action == action and abs(change_pct) < 1:
                    continue

            holding = get_manual_holding(user_id, asset)
            holding_value = holding * price_now

            portfolio_note = ""

            if holding > 0:
                if action == "SELL":
                    portfolio_note = (
                        f"\n\n💼 Portfolio note:\n"
                        f"You track {holding:.6f} {asset} (~${holding_value:,.2f}).\n"
                        "This may be a moment to review profit-taking."
                    )
                elif action == "BUY":
                    portfolio_note = (
                        f"\n\n💼 Portfolio note:\n"
                        f"You already hold {holding:.6f} {asset} (~${holding_value:,.2f}).\n"
                        "This could be an add-to-position opportunity."
                    )
            else:
                if action == "BUY":
                    portfolio_note = (
                        "\n\n💼 Portfolio note:\n"
                        f"You don’t currently hold {asset}. Start small and avoid rushing."
                    )

            set_last_signal(user_id, asset, action, confidence)

            # SAVE HISTORY
            conn = db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO alerts_history (user_id, asset, action, price, change_pct, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, asset, action, price_now, change_pct, datetime.now().isoformat())
            )
            conn.commit()
            conn.close()

            # SEND ALERT
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🚨 {asset} Personalized Alert\n\n"
                    f"Signal: {action}\n"
                    f"Confidence: {confidence}\n"
                    f"Price: ${price_now:,.2f}\n"
                    f"Change: {change_pct:.2f}%\n\n"
                    f"{signal_data['reason']}\n\n"
                    f"Trend: {signal_data['trend']}\n"
                    f"Volatility: {signal_data['volatility']}"
                    f"{portfolio_note}\n\n"
                    "Educational only. Not financial advice."
                )
            )

    conn.commit()
    conn.close()


PRO_PRICE_MONTHLY = "$14.99/month"
STRIPE_PRO_LINK = "https://buy.stripe.com/14AdR90xmgAy304afs4Vy00"

def pro_upgrade_message(user_id):
    return (
        "⭐ CoinPilotX Pro\n\n"
        f"Card price: {PRO_PRICE_MONTHLY}\n"
        f"BTC price: {BTC_PRO_PRICE}\n\n"
        "Free is useful for basic awareness. Pro is built for deeper decision support.\n\n"
        "Free includes:\n"
        "• Basic BTC price\n"
        "• Basic alerts\n"
        "• Basic news summaries\n"
        "• Short scam warning\n\n"
        "Pro unlocks:\n"
        "• Deeper AI analysis\n"
        "• Whale intelligence\n"
        "• Portfolio decision engine\n"
        "• Country crypto intelligence\n"
        "• Advanced scam breakdowns\n"
        "• Wallet/transaction risk insights\n"
        "• Market pressure signals\n"
        "• Personalized BUY / SELL / WAIT / HOLD explanations\n\n"
        "CoinPilotX is operated by CoinPilotXAI Inc.\n"
        "No hidden fees from CoinPilotXAI Inc. Card payment opens only through the secure button below.\n"
        "CoinPilotX never holds funds.\n"
        "CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.\n\n"
        "Choose a payment method below when you are ready."
    )

def upgrade_payment_menu(user_id):
    stripe_link = f"{STRIPE_PRO_LINK}?client_reference_id={user_id}"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay with Card — $14.99", url=stripe_link)],
        [InlineKeyboardButton("₿ Pay with BTC — 0.00025 BTC", callback_data="pay_btc")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")]
    ])

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        [
            InlineKeyboardButton("📈 Free BTC Price", callback_data="menu_price_btc"),
            InlineKeyboardButton("🚨 Free Alerts", callback_data="menu_alerts_on"),
        ],
        [
            InlineKeyboardButton("⭐ Pro Signals", callback_data="pro_signals"),
            InlineKeyboardButton("💼 Pro Portfolio", callback_data="pro_portfolio"),
        ],
        [
            InlineKeyboardButton("🛡️ Pro Scam Shield", callback_data="pro_scanner"),
            InlineKeyboardButton("💳 Upgrade to Pro", callback_data="upgrade_pro"),
        ],
        [
            InlineKeyboardButton("💬 Ask Crypto Question", callback_data="menu_talk"),
            InlineKeyboardButton("ℹ️ About CoinPilotX", callback_data="menu_about"),
        ],
        [
            InlineKeyboardButton("💰 Add Money Safely", callback_data="menu_deposit"),
            InlineKeyboardButton("🏦 Best Exchanges", callback_data="menu_exchanges"),
            InlineKeyboardButton("📘 Help", callback_data="menu_help"),
        ],
    ])

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY

webhook_app = Flask(__name__, template_folder="templates", static_folder="static")
webhook_app.secret_key = os.getenv("FLASK_SECRET_KEY", os.getenv("SECRET_KEY", secrets.token_hex(32)))
webhook_app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",
)
app = webhook_app


@webhook_app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@webhook_app.route("/support", methods=["GET"])
def support_page():
    return render_template("support.html")


@webhook_app.route("/privacy", methods=["GET"])
def privacy_page():
    return render_template("privacy.html")


@webhook_app.route("/terms", methods=["GET"])
def terms_page():
    return render_template("terms.html")


@webhook_app.route("/offline", methods=["GET"])
def offline_page():
    return render_template("offline.html")


@webhook_app.after_request
def add_pwa_headers(response):
    if request.path in ("/static/service-worker.js", "/sw.js"):
        response.headers["Service-Worker-Allowed"] = "/"
    return response


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def verify_csrf():
    return request.form.get("csrf_token") and request.form.get("csrf_token") == session.get("csrf_token")


def account_user_id():
    return session.get("account_user_id")


def load_account_by_id(user_id):
    if not user_id:
        return None
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def load_account_by_email(email):
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND email!='' LIMIT 1", (email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def account_display_name(user):
    return (user or {}).get("full_name") or (user or {}).get("display_name") or "CoinPilotX user"


def render_account_page(page, title, **context):
    context.setdefault("csrf_token", get_csrf_token())
    context.setdefault("current_user", load_account_by_id(account_user_id()))
    context.setdefault("message", "")
    context.setdefault("error", "")
    return render_template("account.html", page=page, title=title, **context)


def require_account():
    user = load_account_by_id(account_user_id())
    if not user:
        return None
    return user


def create_account(full_name, email, password, phone="", country="", email_opt_in=False, sms_opt_in=False):
    now = datetime.now().isoformat()
    password_hash = generate_password_hash(password)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE lower(email)=lower(?) AND email!='' LIMIT 1", (email,))
    if cur.fetchone():
        conn.close()
        return None, "An account already exists for that email."
    cur.execute(
        """
        INSERT INTO users (
            username, display_name, full_name, email, password_hash, phone, country,
            email_verified, email_opt_in, sms_opt_in, plan, subscription_status,
            signup_time, created_at, updated_at, onboarding_complete, alerts_enabled
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'free', 'inactive', ?, ?, ?, 1, 0)
        """,
        ("", full_name, full_name, email, password_hash, phone, country, int(email_opt_in), int(sms_opt_in), now, now, now)
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    return load_account_by_id(user_id), ""


def update_account_settings(user_id, full_name, phone, country, email_opt_in, sms_opt_in):
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET full_name=?, display_name=?, phone=?, country=?, email_opt_in=?, sms_opt_in=?, updated_at=?
        WHERE user_id=?
        """,
        (full_name, full_name, phone, country, int(email_opt_in), int(sms_opt_in), now, user_id)
    )
    conn.commit()
    conn.close()


def generate_telegram_link_code(user_id):
    now = datetime.now()
    code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].upper()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO telegram_link_codes (user_id, code, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (user_id, code, (now + timedelta(minutes=10)).isoformat(), now.isoformat())
    )
    conn.commit()
    conn.close()
    return code


def create_password_reset(email):
    user = load_account_by_email(email)
    if not user:
        return None, None
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO password_reset_tokens (user_id, token, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (user["user_id"], token, (now + timedelta(hours=1)).isoformat(), now.isoformat())
    )
    conn.commit()
    conn.close()
    return user, token


def create_email_verification(user_id):
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO email_verification_tokens (user_id, token, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (user_id, token, (now + timedelta(hours=24)).isoformat(), now.isoformat())
    )
    conn.commit()
    conn.close()
    return token


@webhook_app.route("/signup", methods=["GET", "POST"])
def signup_page():
    init_db()
    if request.method == "POST":
        logging.info("signup endpoint started")
        if not verify_csrf():
            return render_account_page("signup", "Create Account", error="Security check failed. Please try again.")
        full_name = clean_html(request.form.get("full_name", ""))[:160]
        email = clean_html(request.form.get("email", "")).strip().lower()
        password = request.form.get("password", "")
        phone = clean_html(request.form.get("phone", "")).strip()
        country = clean_html(request.form.get("country", ""))[:80]
        email_opt_in = request.form.get("email_opt_in") == "on"
        sms_opt_in = request.form.get("sms_opt_in") == "on"
        if not is_valid_email(email):
            return render_account_page("signup", "Create Account", error="Please enter a valid email address.")
        if len(password) < 8:
            return render_account_page("signup", "Create Account", error="Use at least 8 characters for your password.")
        if phone and not valid_phone(phone):
            return render_account_page("signup", "Create Account", error="Please enter a valid phone number or leave it blank.")
        if sms_opt_in and not phone:
            return render_account_page("signup", "Create Account", error="SMS opt-in requires a phone number.")
        user, error = create_account(full_name, email, password, phone, country, email_opt_in, sms_opt_in)
        if error:
            return render_account_page("signup", "Create Account", error=error)
        session["account_user_id"] = user["user_id"]
        logging.info("user created successfully: user_id=%s", user["user_id"])
        logging.info("Signup successful for user_id: %s", user["user_id"])
        logging.info("calling send_welcome_email")
        send_signup_welcome_emails(user)
        logging.info("send_welcome_email completed")
        verification_token = create_email_verification(user["user_id"])
        verification_link = url_for("verify_email_page", token=verification_token, _external=True)
        send_email_verification(user, verification_link)
        return redirect(url_for("account_page"))
    return render_account_page("signup", "Create Account")


@webhook_app.route("/login", methods=["GET", "POST"])
def login_page():
    init_db()
    if request.method == "POST":
        if not verify_csrf():
            return render_account_page("login", "Login", error="Security check failed. Please try again.")
        email = clean_html(request.form.get("email", "")).strip().lower()
        password = request.form.get("password", "")
        user = load_account_by_email(email)
        if not user or not user.get("password_hash") or not check_password_hash(user["password_hash"], password):
            return render_account_page("login", "Login", error="Email or password is incorrect.")
        session["account_user_id"] = user["user_id"]
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_login_at=?, last_seen_at=? WHERE user_id=?", (datetime.now().isoformat(), datetime.now().isoformat(), user["user_id"]))
        conn.commit()
        conn.close()
        return redirect(url_for("account_page"))
    return render_account_page("login", "Login")


@webhook_app.route("/logout", methods=["GET"])
def logout_page():
    session.pop("account_user_id", None)
    return redirect(url_for("login_page"))


@webhook_app.route("/account", methods=["GET"])
def account_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("login_page"))
    return render_account_page("account", "Account", current_user=user)


@webhook_app.route("/account/settings", methods=["GET", "POST"])
def account_settings_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("login_page"))
    message = ""
    link_code = ""
    if request.method == "POST":
        if not verify_csrf():
            return render_account_page("settings", "Account Settings", current_user=user, error="Security check failed. Please try again.")
        action = request.form.get("action", "save")
        if action == "connect_telegram":
            link_code = generate_telegram_link_code(user["user_id"])
            message = "Telegram link code generated. Send /connect " + link_code + " to the CoinPilotX bot within 10 minutes."
        else:
            full_name = clean_html(request.form.get("full_name", ""))[:160]
            phone = clean_html(request.form.get("phone", "")).strip()
            country = clean_html(request.form.get("country", ""))[:80]
            email_opt_in = request.form.get("email_opt_in") == "on"
            sms_opt_in = request.form.get("sms_opt_in") == "on"
            if phone and not valid_phone(phone):
                return render_account_page("settings", "Account Settings", current_user=user, error="Please enter a valid phone number or leave it blank.")
            if sms_opt_in and not phone:
                return render_account_page("settings", "Account Settings", current_user=user, error="SMS opt-in requires a phone number.")
            update_account_settings(user["user_id"], full_name, phone, country, email_opt_in, sms_opt_in)
            user = load_account_by_id(user["user_id"])
            message = "Account settings saved."
    return render_account_page("settings", "Account Settings", current_user=user, message=message, link_code=link_code)


@webhook_app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    init_db()
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            return render_account_page("forgot", "Forgot Password", error="Security check failed. Please try again.")
        email = clean_html(request.form.get("email", "")).strip().lower()
        user, token = create_password_reset(email)
        if user and token:
            reset_link = url_for("reset_password_page", token=token, _external=True)
            send_password_reset_email(user, reset_link)
        message = "If that email has an account, a password reset link will be sent."
    return render_account_page("forgot", "Forgot Password", message=message)


@webhook_app.route("/verify-email", methods=["GET"])
def verify_email_page():
    init_db()
    token = clean_html(request.args.get("token", ""))
    if not token:
        return render_account_page("login", "Login", error="This verification link is invalid.")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, expires_at, used_at FROM email_verification_tokens WHERE token=? ORDER BY id DESC LIMIT 1", (token,))
    row = cur.fetchone()
    if not row or row[2] or row[1] < datetime.now().isoformat():
        conn.close()
        return render_account_page("login", "Login", error="This verification link is invalid or expired.")
    now = datetime.now().isoformat()
    cur.execute("UPDATE users SET email_verified=1, updated_at=? WHERE user_id=?", (now, row[0]))
    cur.execute("UPDATE email_verification_tokens SET used_at=? WHERE token=?", (now, token))
    conn.commit()
    conn.close()
    return render_account_page("login", "Login", message="Email verified. You can log in anytime.")


@webhook_app.route("/reset-password", methods=["GET", "POST"])
def reset_password_page():
    init_db()
    token = clean_html(request.args.get("token") or request.form.get("token") or "")
    if request.method == "POST":
        if not verify_csrf():
            return render_account_page("reset", "Reset Password", token=token, error="Security check failed. Please try again.")
        password = request.form.get("password", "")
        if len(password) < 8:
            return render_account_page("reset", "Reset Password", token=token, error="Use at least 8 characters for your password.")
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT user_id, expires_at, used_at FROM password_reset_tokens WHERE token=? ORDER BY id DESC LIMIT 1", (token,))
        row = cur.fetchone()
        if not row or row[2] or row[1] < datetime.now().isoformat():
            conn.close()
            return render_account_page("reset", "Reset Password", token=token, error="This reset link is invalid or expired.")
        cur.execute("UPDATE users SET password_hash=?, updated_at=? WHERE user_id=?", (generate_password_hash(password), datetime.now().isoformat(), row[0]))
        cur.execute("UPDATE password_reset_tokens SET used_at=? WHERE token=?", (datetime.now().isoformat(), token))
        conn.commit()
        conn.close()
        return redirect(url_for("login_page"))
    return render_account_page("reset", "Reset Password", token=token)


@webhook_app.route("/admin/users", methods=["GET"])
def admin_users_page():
    if not require_admin_password():
        return Response("Unauthorized. Set ADMIN_ANALYTICS_PASSWORD and pass ?password=...", status=401)
    init_db()
    conn = db()
    cur = conn.cursor()
    since_day = (datetime.now() - timedelta(days=1)).isoformat()
    cur.execute("SELECT COUNT(*) FROM users WHERE email!=''")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE email!='' AND created_at>=?", (since_day,))
    new_today = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(plan, subscription_plan, 'free'), COUNT(*) FROM users WHERE email!='' GROUP BY COALESCE(plan, subscription_plan, 'free')")
    by_plan = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM users WHERE telegram_user_id IS NOT NULL")
    linked = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE email_opt_in=1")
    email_opt_ins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE sms_opt_in=1")
    sms_opt_ins = cur.fetchone()[0]
    cur.execute("SELECT full_name, email, plan, subscription_status, telegram_username, created_at FROM users WHERE email!='' ORDER BY created_at DESC LIMIT 50")
    users = cur.fetchall()
    conn.close()
    return render_template("account.html", page="admin_users", title="Admin Users", csrf_token=get_csrf_token(), current_user=None, message="", error="", stats={
        "total_users": total_users,
        "new_today": new_today,
        "by_plan": by_plan,
        "linked": linked,
        "email_opt_ins": email_opt_ins,
        "sms_opt_ins": sms_opt_ins,
        "users": users,
    })


@webhook_app.route("/admin/test-email", methods=["POST"])
def admin_test_email():
    if not require_admin_password():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    init_db()
    payload = request.get_json(silent=True) or request.form
    email = clean_html(payload.get("email", "")).strip().lower()
    if not is_valid_email(email):
        return jsonify({"ok": False, "error": "valid email required"}), 400
    logging.info("Admin test email requested for domain=%s brevo_key_loaded=%s", email.split("@")[-1], bool(os.getenv("BREVO_API_KEY")))
    text = (
        "This is a CoinPilotXAI Inc. Brevo transactional email test.\n\n"
        "If you received this, server-side email delivery is connected.\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords."
    )
    html = branded_email_html("CoinPilotXAI Inc. Email Test", """
      <p>This is a Brevo transactional email test from CoinPilotXAI Inc.</p>
      <p>If you received this, server-side email delivery is connected.</p>
    """)
    sent = send_platform_email(email, "CoinPilotXAI Inc. Brevo test email", text, html, 0)
    return jsonify({
        "ok": bool(sent),
        "provider": (os.getenv("EMAIL_PROVIDER") or ("brevo" if os.getenv("BREVO_API_KEY") else "unconfigured")),
        "brevo_key_loaded": bool(os.getenv("BREVO_API_KEY")),
        "from_email": email_sender_identity()[0],
        "message": "Test email sent or accepted by provider." if sent else "Test email failed. Check Railway logs and Brevo Transactional logs.",
    }), (200 if sent else 502)


def send_brevo_debug_email(to_email):
    from_email, from_name = email_sender_identity()
    brevo_key = os.getenv("BREVO_API_KEY")
    payload = {
        "sender": {"email": from_email, "name": from_name},
        "to": [{"email": to_email}],
        "subject": "CoinPilotXAI Inc. Brevo debug email",
        "textContent": "Brevo debug email from CoinPilotXAI Inc. If this arrives, API delivery is working.",
        "htmlContent": branded_email_html("Brevo Debug Email", "<p>If this arrives, API delivery is working.</p>"),
    }
    logging.info("Debug Brevo email requested: to_domain=%s sender=%s brevo_key_loaded=%s", to_email.split("@")[-1], from_email, bool(brevo_key))
    if not brevo_key:
        return {
            "ok": False,
            "status_code": None,
            "sender": from_email,
            "brevo_key_loaded": False,
            "response": {"message": "BREVO_API_KEY is not loaded."},
        }
    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": brevo_key, "Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=15,
        )
        try:
            body = response.json() if response.text else {}
        except Exception:
            body = {"raw": response.text}
        logging.info("Debug Brevo response status_code=%s body=%s", response.status_code, json.dumps(body)[:1200])
        return {
            "ok": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "sender": from_email,
            "brevo_key_loaded": True,
            "response": body,
        }
    except Exception as exc:
        logging.exception("Debug Brevo email exception")
        return {
            "ok": False,
            "status_code": None,
            "sender": from_email,
            "brevo_key_loaded": True,
            "response": {"message": str(exc)},
        }


@webhook_app.route("/debug/email-test", methods=["GET"])
def debug_email_test():
    expected = os.getenv("ADMIN_ANALYTICS_PASSWORD", "")
    if expected and request.args.get("password", "") != expected:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    to_email = os.getenv("DEBUG_EMAIL_TEST_TO", "support@coinpilotx.app")
    result = send_brevo_debug_email(to_email)
    return jsonify(result), (200 if result.get("ok") else 502)


def client_ip_hash():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    salt = os.getenv("ANALYTICS_SALT", "coinpilotxai-inc")
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest() if ip else ""


def parse_device(user_agent):
    ua = (user_agent or "").lower()
    if "ipad" in ua or "tablet" in ua:
        device = "tablet"
    elif "mobile" in ua or "iphone" in ua or "android" in ua:
        device = "mobile"
    else:
        device = "desktop"
    if "edg/" in ua:
        browser = "Edge"
    elif "chrome" in ua and "safari" in ua:
        browser = "Chrome"
    elif "safari" in ua and "chrome" not in ua:
        browser = "Safari"
    elif "firefox" in ua:
        browser = "Firefox"
    else:
        browser = "Other"
    return device, browser


def get_utm_payload(payload):
    return (
        clean_html(payload.get("utm_source", ""))[:120],
        clean_html(payload.get("utm_medium", ""))[:120],
        clean_html(payload.get("utm_campaign", ""))[:160],
    )


@webhook_app.route("/api/track", methods=["POST"])
def track_event_api():
    try:
        init_db()
        payload = request.get_json(silent=True) or {}
        session_id = clean_html(payload.get("session_id", ""))[:100] or hashlib.sha256(os.urandom(16)).hexdigest()
        event_name = clean_html(payload.get("event_name", "page_view"))[:80]
        page_url = clean_html(payload.get("page_url", ""))[:600]
        referrer = clean_html(payload.get("referrer", ""))[:600]
        user_agent = request.headers.get("User-Agent", "")[:600]
        device_type, browser = parse_device(user_agent)
        utm_source, utm_medium, utm_campaign = get_utm_payload(payload)
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        now = datetime.now().isoformat()

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM sessions WHERE session_id=?", (session_id,))
        if cur.fetchone():
            cur.execute(
                """
                UPDATE sessions
                SET last_seen_at=?, user_agent=COALESCE(NULLIF(?, ''), user_agent),
                    referrer=COALESCE(NULLIF(?, ''), referrer),
                    utm_source=COALESCE(NULLIF(?, ''), utm_source),
                    utm_medium=COALESCE(NULLIF(?, ''), utm_medium),
                    utm_campaign=COALESCE(NULLIF(?, ''), utm_campaign)
                WHERE session_id=?
                """,
                (now, user_agent, referrer, utm_source, utm_medium, utm_campaign, session_id)
            )
        else:
            cur.execute(
                """
                INSERT INTO sessions (session_id, first_seen_at, last_seen_at, user_agent, referrer, landing_page, utm_source, utm_medium, utm_campaign)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, now, now, user_agent, referrer, page_url, utm_source, utm_medium, utm_campaign)
            )
        cur.execute(
            """
            INSERT INTO analytics_events (session_id, user_id, event_name, page_url, referrer, device_type, browser, ip_hash, country, metadata, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                event_name,
                page_url,
                referrer,
                device_type,
                browser,
                client_ip_hash(),
                clean_html(payload.get("country", ""))[:80],
                json.dumps(metadata)[:4000],
                now,
            )
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "session_id": session_id})
    except Exception as exc:
        logging.info("Analytics track failed: %s", exc)
        return jsonify({"ok": False}), 200


def valid_phone(phone):
    if not phone:
        return True
    return bool(re.match(r"^\+?[0-9 .()\-]{7,24}$", phone))


@webhook_app.route("/api/leads", methods=["POST"])
def leads_api():
    init_db()
    payload = request.get_json(silent=True) or {}
    email = clean_html(payload.get("email", "")).strip().lower()
    phone = clean_html(payload.get("phone", "")).strip()
    email_opt_in = 1 if payload.get("email_opt_in") is True else 0
    sms_opt_in = 1 if payload.get("sms_opt_in") is True else 0
    if not is_valid_email(email):
        return jsonify({"ok": False, "message": "Please enter a valid email address."}), 400
    if phone and not valid_phone(phone):
        return jsonify({"ok": False, "message": "Please enter a valid phone number or leave it blank."}), 400
    if sms_opt_in and not phone:
        return jsonify({"ok": False, "message": "SMS opt-in requires a phone number."}), 400
    if not email_opt_in and not sms_opt_in:
        return jsonify({"ok": False, "message": "Please choose at least one opt-in option."}), 400

    now = datetime.now().isoformat()
    utm_source, utm_medium, utm_campaign = get_utm_payload(payload)
    session_id = clean_html(payload.get("session_id", ""))[:100]
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM leads WHERE email=?", (email,))
    row = cur.fetchone()
    values = (
        clean_html(payload.get("full_name", ""))[:160],
        email,
        phone[:40],
        clean_html(payload.get("country", ""))[:80],
        clean_html(payload.get("source", "website"))[:120],
        utm_source,
        utm_medium,
        utm_campaign,
        email_opt_in,
        sms_opt_in,
        clean_html(payload.get("telegram_username", ""))[:120],
        now,
        now,
    )
    is_new_lead = not bool(row)
    if row:
        cur.execute(
            """
            UPDATE leads
            SET full_name=?, email=?, phone=?, country=?, source=?, utm_source=?, utm_medium=?, utm_campaign=?,
                email_opt_in=?, sms_opt_in=?, telegram_username=?, updated_at=?, last_seen_at=?
            WHERE email=?
            """,
            values + (email,)
        )
        lead_id = row[0]
    else:
        cur.execute(
            """
            INSERT INTO leads (full_name, email, phone, country, source, utm_source, utm_medium, utm_campaign,
                               email_opt_in, sms_opt_in, telegram_username, created_at, updated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values[:11] + (now, now, now)
        )
        lead_id = cur.lastrowid
    if session_id:
        cur.execute(
            "INSERT INTO analytics_events (session_id, user_id, event_name, page_url, referrer, device_type, browser, ip_hash, country, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                lead_id,
                "signup_form_submit",
                clean_html(payload.get("page_url", ""))[:600],
                clean_html(payload.get("referrer", ""))[:600],
                parse_device(request.headers.get("User-Agent", ""))[0],
                parse_device(request.headers.get("User-Agent", ""))[1],
                client_ip_hash(),
                clean_html(payload.get("country", ""))[:80],
                json.dumps({"email_opt_in": bool(email_opt_in), "sms_opt_in": bool(sms_opt_in), "source": payload.get("source", "website")})[:4000],
                now,
            )
        )
    conn.commit()
    conn.close()
    if email_opt_in and is_new_lead:
        send_update_signup_email({
            "id": lead_id,
            "email": email,
            "full_name": values[0],
        })
    return jsonify({"ok": True, "message": "Thanks — you’re on the CoinPilotXAI Inc. update list."})


def require_admin_password():
    expected = os.getenv("ADMIN_ANALYTICS_PASSWORD", "")
    if not expected:
        return False
    supplied = request.args.get("password") or request.headers.get("X-Admin-Password", "")
    return supplied == expected


def analytics_summary():
    init_db()
    conn = db()
    cur = conn.cursor()
    since_day = (datetime.now() - timedelta(days=1)).isoformat()
    since_live = (datetime.now() - timedelta(minutes=5)).isoformat()
    cur.execute("SELECT COUNT(*) FROM sessions WHERE last_seen_at>=?", (since_live,))
    live_visitors = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT session_id) FROM analytics_events WHERE created_at>=?", (since_day,))
    visitors_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM analytics_events WHERE event_name='page_view' AND created_at>=?", (since_day,))
    page_views_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM leads WHERE created_at>=?", (since_day,))
    leads_today = cur.fetchone()[0]
    conversion_rate = (leads_today / visitors_today * 100) if visitors_today else 0

    def rows(sql, params=()):
        cur.execute(sql, params)
        return cur.fetchall()

    data = {
        "live_visitors": live_visitors,
        "visitors_today": visitors_today,
        "page_views_today": page_views_today,
        "leads_today": leads_today,
        "conversion_rate": conversion_rate,
        "top_pages": rows("SELECT page_url, COUNT(*) FROM analytics_events WHERE created_at>=? AND page_url!='' GROUP BY page_url ORDER BY COUNT(*) DESC LIMIT 8", (since_day,)),
        "referrers": rows("SELECT COALESCE(NULLIF(referrer,''),'Direct'), COUNT(*) FROM analytics_events WHERE created_at>=? GROUP BY COALESCE(NULLIF(referrer,''),'Direct') ORDER BY COUNT(*) DESC LIMIT 8", (since_day,)),
        "devices": rows("SELECT device_type, browser, COUNT(*) FROM analytics_events WHERE created_at>=? GROUP BY device_type, browser ORDER BY COUNT(*) DESC LIMIT 10", (since_day,)),
        "cta_clicks": rows("SELECT event_name, COUNT(*) FROM analytics_events WHERE created_at>=? AND event_name LIKE '%click%' GROUP BY event_name ORDER BY COUNT(*) DESC LIMIT 10", (since_day,)),
        "recent": rows("SELECT event_name, page_url, device_type, browser, created_at FROM analytics_events ORDER BY id DESC LIMIT 25"),
        "new_leads": rows("SELECT full_name, email, phone, country, email_opt_in, sms_opt_in, created_at FROM leads ORDER BY id DESC LIMIT 25"),
    }
    conn.close()
    return data


@webhook_app.route("/admin/analytics", methods=["GET"])
def admin_analytics_page():
    if not require_admin_password():
        return Response("Unauthorized. Set ADMIN_ANALYTICS_PASSWORD and pass ?password=...", status=401)
    data = analytics_summary()

    def table(rows, headers):
        body = "".join("<tr>" + "".join(f"<td>{str(cell)[:180]}</td>" for cell in row) + "</tr>" for row in rows)
        head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
        empty = f'<tr><td colspan="{len(headers)}">No data yet.</td></tr>'
        return f"<table>{head}{body or empty}</table>"

    html = f"""
    <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CoinPilotXAI Inc. Analytics</title>
    <style>
    body{{margin:0;font-family:Inter,system-ui,Arial;background:#070b14;color:#f2fbff;line-height:1.5}}
    .wrap{{width:min(100% - 28px,1180px);margin:0 auto;padding:30px 0}}
    .grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
    .card,table{{border:1px solid rgba(110,223,246,.18);background:rgba(13,22,39,.88);border-radius:10px;box-shadow:0 20px 58px rgba(0,0,0,.28)}}
    .card{{padding:18px}} .metric{{font-size:32px;font-weight:900}} h1,h2{{letter-spacing:0}} table{{width:100%;border-collapse:collapse;margin:12px 0 28px;overflow:hidden}}
    th,td{{padding:10px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left;vertical-align:top}} th{{color:#6edff6}}
    a{{color:#36e58f}} .actions{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 24px}}
    @media(max-width:800px){{.grid{{grid-template-columns:1fr 1fr}}}} @media(max-width:520px){{.grid{{grid-template-columns:1fr}} table{{font-size:12px}}}}
    </style></head><body><div class="wrap">
    <h1>CoinPilotXAI Inc. Analytics</h1>
    <div class="actions"><a href="/admin/analytics/export/emails?password={request.args.get('password','')}">Export email opt-ins</a><a href="/admin/analytics/export/sms?password={request.args.get('password','')}">Export SMS opt-ins</a></div>
    <div class="grid">
      <div class="card"><div>Live visitors</div><div class="metric">{data['live_visitors']}</div></div>
      <div class="card"><div>Visitors today</div><div class="metric">{data['visitors_today']}</div></div>
      <div class="card"><div>Page views today</div><div class="metric">{data['page_views_today']}</div></div>
      <div class="card"><div>Lead conversion</div><div class="metric">{data['conversion_rate']:.1f}%</div></div>
    </div>
    <h2>Top pages</h2>{table(data['top_pages'], ['Page', 'Events'])}
    <h2>Referral sources</h2>{table(data['referrers'], ['Referrer', 'Events'])}
    <h2>Device / browser</h2>{table(data['devices'], ['Device', 'Browser', 'Events'])}
    <h2>CTA clicks</h2>{table(data['cta_clicks'], ['Event', 'Clicks'])}
    <h2>Recent activity</h2>{table(data['recent'], ['Event', 'Page', 'Device', 'Browser', 'Time'])}
    <h2>New leads</h2>{table(data['new_leads'], ['Name', 'Email', 'Phone', 'Country', 'Email opt-in', 'SMS opt-in', 'Created'])}
    </div></body></html>
    """
    return html


@webhook_app.route("/admin/analytics/export/<kind>", methods=["GET"])
def admin_analytics_export(kind):
    if not require_admin_password():
        return Response("Unauthorized", status=401)
    if kind not in {"emails", "sms"}:
        return Response("Unknown export", status=404)
    conn = db()
    cur = conn.cursor()
    if kind == "emails":
        cur.execute("SELECT full_name, email, country, source, created_at FROM leads WHERE email_opt_in=1 AND email!='' ORDER BY created_at DESC")
        headers = ["full_name", "email", "country", "source", "created_at"]
    else:
        cur.execute("SELECT full_name, phone, country, source, created_at FROM leads WHERE sms_opt_in=1 AND phone!='' ORDER BY created_at DESC")
        headers = ["full_name", "phone", "country", "source", "created_at"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(cur.fetchall())
    conn.close()
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=coinpilotxai-{kind}.csv"})


@webhook_app.route("/robots.txt", methods=["GET"])
def robots_txt():
    return send_from_directory(webhook_app.static_folder, "robots.txt", mimetype="text/plain")


@webhook_app.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    return send_from_directory(webhook_app.static_folder, "sitemap.xml", mimetype="application/xml")


@webhook_app.route("/manifest.json", methods=["GET"])
def manifest_json():
    return send_from_directory(webhook_app.static_folder, "manifest.json", mimetype="application/manifest+json")


@webhook_app.route("/site.webmanifest", methods=["GET"])
def site_webmanifest():
    return send_from_directory(webhook_app.static_folder, "site.webmanifest", mimetype="application/manifest+json")


@webhook_app.route("/sw.js", methods=["GET"])
def service_worker_root():
    return send_from_directory(webhook_app.static_folder, "sw.js", mimetype="application/javascript")


@webhook_app.route("/icons/<path:filename>", methods=["GET"])
def pwa_icons(filename):
    return send_from_directory(os.path.join(webhook_app.static_folder, "icons"), filename)


@webhook_app.route("/indexnow-key.txt", methods=["GET"])
@webhook_app.route("/4d4dc0c2c0f94b7bb8184fd91b7f0b1e.txt", methods=["GET"])
def indexnow_key_txt():
    return send_from_directory(webhook_app.static_folder, "indexnow-key.txt", mimetype="text/plain")


@webhook_app.route("/api/indexnow", methods=["GET"])
def indexnow_metadata_api():
    return jsonify({
        "host": "coinpilotx.app",
        "key": "4d4dc0c2c0f94b7bb8184fd91b7f0b1e",
        "keyLocation": "https://coinpilotx.app/indexnow-key.txt",
        "urlList": [
            "https://coinpilotx.app/",
            "https://coinpilotx.app/support",
            "https://coinpilotx.app/privacy",
            "https://coinpilotx.app/terms",
        ],
        "submitEndpoint": "https://api.indexnow.org/indexnow",
    })


@webhook_app.route("/api/intelligence-feed", methods=["GET"])
def intelligence_feed_api():
    return jsonify(live_intelligence_feed())


@webhook_app.route("/api/markets", methods=["GET"])
def markets_api():
    category = request.args.get("category", "top_volume")
    return jsonify(live_market_board(category=category))


@webhook_app.route("/api/sports-edge", methods=["GET"])
def sports_edge_api():
    game_id = request.args.get("game_id", "").strip()
    league = request.args.get("league", "all").strip().lower()
    return jsonify(live_sports_edge(game_id=game_id, league=league))


@webhook_app.route("/api/platform-status", methods=["GET"])
def platform_status_api():
    return jsonify(platform_status())


@webhook_app.route("/api/ai-assistant", methods=["POST"])
def website_ai_assistant_api():
    payload = request.get_json(silent=True) or {}
    question = clean_html(payload.get("question", "")).strip()
    if not question:
        return jsonify({"ok": False, "response": "Ask a crypto, wallet, scam, market, or sports question first."}), 400
    return jsonify({
        "ok": True,
        "powered_by": "CoinPilotXAI Inc.",
        "response": openai_chat_completion(0, question),
        "safety": "Informational only — not financial advice.",
    })


@webhook_app.route("/api/scam-shield", methods=["POST"])
def website_scam_shield_api():
    payload = request.get_json(silent=True) or {}
    text = clean_html(payload.get("text", "")).strip()
    if not text:
        return jsonify({"ok": False, "response": "Paste a suspicious message, link, or crypto pitch first."}), 400
    return jsonify({"ok": True, **scam_text_intelligence(text)})


@webhook_app.route("/api/wallet-intel", methods=["GET"])
def website_wallet_intel_api():
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"ok": False, "response": "Enter a public BTC wallet address. Never enter private keys or seed phrases."}), 400
    if not is_btc_address(address):
        return jsonify({"ok": False, "response": "That does not look like a public BTC address. Never enter seed phrases, private keys, or wallet passwords."}), 400
    return jsonify({
        "ok": True,
        "response": wallet_info_summary(0, address, save_connection=False),
        "safety": "Only public wallet data is used. Informational only — not financial advice.",
    })


@webhook_app.route("/api/day-signal", methods=["POST"])
def website_day_signal_api():
    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers", {})
    result = build_day_signal(answers)
    if not result["ok"]:
        return jsonify(result), 400
    return jsonify(result)


@webhook_app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    init_db()
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        print("❌ Stripe webhook error:", e)
        return "Invalid", 400

    print("🔥 WEBHOOK HIT:", event["type"])

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        payment_status = session.get("payment_status")

        print("client_reference_id:", user_id)

        if user_id:
            try:
                user_id_int = int(user_id)
            except Exception:
                print("Invalid client_reference_id:", user_id)
                return "OK", 200

            customer_details = session.get("customer_details") or {}
            customer_email = customer_details.get("email") or session.get("customer_email")
            if customer_email and is_valid_email(customer_email):
                save_user_email(user_id_int, customer_email)

            if payment_status == "paid":
                timestamp = datetime.now().isoformat()
                activate_pro(
                    user_id_int,
                    payment_type="stripe",
                    stripe_customer_id=session.get("customer"),
                    stripe_session_id=session.get("id"),
                    subscription_status="active",
                )
                save_payment_verification(
                    user_id_int,
                    txid=session.get("id"),
                    payment_type="stripe",
                    amount=(session.get("amount_total") / 100 if session.get("amount_total") else None),
                    status="verified",
                    details=f"Stripe checkout.session.completed at {timestamp}",
                )
                send_telegram_confirmation(
                    user_id_int,
                    "✅ CoinPilotX Pro activated successfully.\n\n"
                    "Your card payment was confirmed by Stripe, and your Pro access is now active.\n\n"
                    "Educational only — not financial advice.\n"
                    "CoinPilotX will never ask for your seed phrase or private key."
                )
                send_subscription_email(user_id_int, "stripe", stripe_session_id=session.get("id"), timestamp=timestamp)
                print(f"✅ Activated PRO for user {user_id}")
            else:
                save_payment_verification(
                    user_id_int,
                    txid=session.get("id"),
                    payment_type="stripe",
                    amount=None,
                    status=f"not_activated_{payment_status or 'unknown'}",
                    details="Stripe checkout completed but payment_status was not paid.",
                )
                print(f"Stripe session not activated for user {user_id}: payment_status={payment_status}")

    return "OK", 200

def scan_confirm_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, scan it", callback_data="scan_confirm"),
            InlineKeyboardButton("❌ No, don’t scan", callback_data="scan_cancel"),
        ]
    ])


def ensure_user(user):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    exists = cur.fetchone()

    if not exists:
        cur.execute("""
        INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username,
            user.first_name or "",
            "",
            datetime.now().isoformat(),
            0,
            0
        ))

        for asset in ["BTC", "ETH", "SOL"]:
            cur.execute("INSERT OR IGNORE INTO watchlists VALUES (?, ?)", (user.id, asset))
            cur.execute("INSERT OR IGNORE INTO manual_portfolio VALUES (?, ?, ?)", (user.id, asset, 0.0))
            cur.execute("INSERT OR IGNORE INTO paper_portfolio VALUES (?, ?, ?)", (user.id, asset, 0.0))

        cur.execute("INSERT OR IGNORE INTO paper_portfolio VALUES (?, ?, ?)", (user.id, "USD", 10000.0))

    conn.commit()
    conn.close()


def get_user_name(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT display_name FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else "friend"


def onboarding_complete(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT onboarding_complete FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row[0] == 1)


def save_user_name(user_id, name):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET display_name=? WHERE user_id=?", (name, user_id))
    conn.commit()
    conn.close()


def save_user_email(user_id, email):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET email=?, onboarding_complete=1 WHERE user_id=?", (email, user_id))
    conn.commit()
    conn.close()


def is_valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", (email or "").strip()))


def get_user_email(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT email FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else ""


def log_email_status(user_id, email, subject, status):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO email_logs (user_id, email, subject, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, email, subject, status, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def send_email_confirmation(user_id, to_email, subject, body):
    logging.info("Attempting transactional confirmation email for user_id: %s", user_id)
    return send_platform_email(to_email, subject, body, body.replace("\n", "<br>"), user_id)


def email_sender_identity():
    from_email = os.getenv("MAIL_FROM_ADDRESS") or os.getenv("FROM_EMAIL") or os.getenv("SMTP_USER") or "support@coinpilotx.app"
    from_name = os.getenv("MAIL_FROM_NAME", "CoinPilotXAI Inc.")
    return from_email, from_name


def send_platform_email(to_email, subject, text_body, html_body="", user_id=None):
    if not to_email:
        log_email_status(user_id or 0, "", subject, "skipped_no_email")
        return False
    provider = (os.getenv("EMAIL_PROVIDER") or "").strip().lower()
    from_email, from_name = email_sender_identity()
    brevo_key = os.getenv("BREVO_API_KEY")
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    logging.info(
        "Email send requested: provider=%s user_id=%s to_domain=%s from=%s brevo_key_loaded=%s",
        provider or "auto",
        user_id or 0,
        to_email.split("@")[-1] if "@" in to_email else "invalid",
        from_email,
        bool(brevo_key),
    )
    try:
        if (provider == "brevo" or (not provider and brevo_key)) and brevo_key:
            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": brevo_key, "Content-Type": "application/json", "Accept": "application/json"},
                json={
                    "sender": {"email": from_email, "name": from_name},
                    "to": [{"email": to_email}],
                    "subject": subject,
                    "textContent": text_body,
                    "htmlContent": html_body or text_body.replace("\n", "<br>"),
                },
                timeout=15,
            )
            ok = 200 <= response.status_code < 300
            logging.info("Brevo API response status_code=%s body=%s", response.status_code, response.text[:1200])
            message_id = ""
            safe_error = ""
            try:
                payload = response.json() if response.text else {}
                message_ids = payload.get("messageIds")
                if payload.get("messageId"):
                    message_id = payload.get("messageId")
                elif isinstance(message_ids, list) and message_ids:
                    message_id = message_ids[0]
                safe_error = payload.get("message") or payload.get("code") or response.text[:240]
            except Exception:
                safe_error = response.text[:240]
            if ok:
                logging.info("Welcome/email sent through Brevo: user_id=%s message_id=%s", user_id or 0, message_id or "unavailable")
                if subject.lower().startswith("welcome"):
                    logging.info("Welcome email sent: %s", message_id or "accepted_no_message_id")
            else:
                logging.warning("Welcome/email failed through Brevo: user_id=%s status_code=%s safe_error=%s", user_id or 0, response.status_code, safe_error)
                if subject.lower().startswith("welcome"):
                    logging.warning("Welcome email failed: status_code=%s safe_error=%s", response.status_code, safe_error)
            log_email_status(user_id or 0, to_email, subject, f"sent_brevo:{message_id}" if ok and message_id else ("sent_brevo" if ok else f"failed_brevo_{response.status_code}"))
            return ok
        if provider == "sendgrid" and sendgrid_key:
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {sendgrid_key}", "Content-Type": "application/json"},
                json={
                    "personalizations": [{"to": [{"email": to_email}]}],
                    "from": {"email": from_email, "name": from_name},
                    "subject": subject,
                    "content": [
                        {"type": "text/plain", "value": text_body},
                        {"type": "text/html", "value": html_body or text_body.replace("\n", "<br>")},
                    ],
                },
                timeout=15,
            )
            ok = 200 <= response.status_code < 300
            log_email_status(user_id or 0, to_email, subject, "sent_sendgrid" if ok else f"failed_sendgrid_{response.status_code}")
            return ok
        smtp_host = os.getenv("SMTP_HOST")
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        if smtp_host and smtp_user and smtp_password:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = f"{from_name} <{from_email}>"
            message["To"] = to_email
            message.set_content(text_body)
            if html_body:
                message.add_alternative(html_body, subtype="html")
            with smtplib.SMTP(smtp_host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
                smtp.starttls()
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(message)
            log_email_status(user_id or 0, to_email, subject, "sent_smtp")
            return True
        log_email_status(user_id or 0, to_email, subject, "skipped_email_not_configured")
        return False
    except Exception as exc:
        logging.info("Platform email failed: %s", exc)
        log_email_status(user_id or 0, to_email, subject, "failed")
        return False


def branded_email_html(title, body_html):
    return f"""
    <div style="margin:0;padding:28px;background:#070b14;color:#f2fbff;font-family:Inter,Arial,sans-serif">
      <div style="max-width:620px;margin:0 auto;border:1px solid rgba(110,223,246,.22);border-radius:12px;background:#0d1627;padding:28px">
        <h1 style="margin:0 0 14px;color:#ffffff">{title}</h1>
        <div style="color:#c4d2e7;line-height:1.65;font-size:15px">{body_html}</div>
        <p style="margin-top:24px;color:#9fb5c0;font-size:13px">CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.</p>
        <p style="color:#ffd9a0;font-size:13px">Educational AI intelligence only. Not financial, betting, investment, or legal advice.</p>
      </div>
    </div>
    """


def send_welcome_email(user, override_email=None, audit_label="user"):
    name = account_display_name(user)
    to_email = override_email or user.get("email")
    logging.info("Attempting welcome email for user_id: %s recipient=%s", user.get("user_id"), audit_label)
    logging.info("Brevo API key loaded: %s", bool(os.getenv("BREVO_API_KEY")))
    subject = "Welcome to CoinPilotX — Powered by CoinPilotXAI Inc."
    text = (
        f"Hi {name},\n\n"
        "Welcome to CoinPilotX.\n\n"
        "Your account is ready. CoinPilotX helps you review AI crypto intelligence, wallet risk, scam awareness, whale movement, portfolio context, and market signals inside a safety-first workflow.\n\n"
        "You can access your dashboard at https://coinpilotx.app/account and connect Telegram from Account Settings.\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.\n"
        "Educational AI intelligence only. Not financial, betting, investment, or legal advice.\n\n"
        "Support: support@coinpilotx.app"
    )
    html = branded_email_html("Welcome to CoinPilotX", f"""
      <p>Hi {clean_html(name)},</p>
      <p>Your CoinPilotX account is ready. Use the dashboard to manage your profile, marketing preferences, plan status, and Telegram connection.</p>
      <p>CoinPilotX helps explain AI crypto intelligence, wallet risk, scam awareness, whale movement, portfolio context, and market signals inside a safety-first workflow.</p>
      <p><a href="https://coinpilotx.app/account" style="color:#36e58f">Open your account dashboard</a></p>
      <p>Support: <a href="mailto:support@coinpilotx.app" style="color:#6edff6">support@coinpilotx.app</a></p>
    """)
    sent = send_platform_email(to_email, subject, text, html, user.get("user_id"))
    logging.info("Welcome email result for user_id %s recipient=%s: %s", user.get("user_id"), audit_label, sent)
    return sent


def send_welcome_email_with_retry(user, override_email=None, audit_label="user"):
    try:
        sent = send_welcome_email(user, override_email=override_email, audit_label=audit_label)
        if sent:
            return True
        logging.warning("send_welcome_email failed; retrying once after 3 seconds for user_id=%s recipient=%s", user.get("user_id"), audit_label)
        time.sleep(3)
        return send_welcome_email(user, override_email=override_email, audit_label=f"{audit_label}_retry")
    except Exception as exc:
        logging.exception("send_welcome_email exception")
        logging.warning("send_welcome_email exception detail: %s", exc)
        try:
            time.sleep(3)
            return send_welcome_email(user, override_email=override_email, audit_label=f"{audit_label}_exception_retry")
        except Exception:
            logging.exception("send_welcome_email exception")
            return False


def send_signup_welcome_emails(user):
    results = {}
    recipients = [("new_user", user.get("email")), ("support_copy", "support@coinpilotx.app")]
    seen = set()
    for label, email in recipients:
        if not email or email in seen:
            continue
        seen.add(email)
        results[label] = send_welcome_email_with_retry(user, override_email=email, audit_label=label)
    logging.info("send_welcome_email completed: user_id=%s results=%s", user.get("user_id"), results)
    return results


def send_update_signup_email(lead):
    subject = "You’re on the CoinPilotXAI Inc. update list"
    name = lead.get("full_name") or "there"
    text = (
        f"Hi {name},\n\n"
        "Thanks for joining the CoinPilotXAI Inc. update list.\n\n"
        "You may receive product updates, launch news, safety alerts, feature releases, and promotional offers based on your consent choices.\n\n"
        "No account was created unless you registered separately at https://coinpilotx.app/signup.\n"
        "You can opt out anytime. For SMS, reply STOP where supported or contact support.\n\n"
        "Support: support@coinpilotx.app"
    )
    html = branded_email_html("You’re on the CoinPilotXAI Inc. update list", f"""
      <p>Hi {clean_html(name)},</p>
      <p>Thanks for joining updates. You may receive product updates, launch news, safety alerts, feature releases, and promotional offers based on your consent choices.</p>
      <p>No account was created unless you registered separately.</p>
      <p>You can opt out anytime. For SMS, reply STOP where supported or contact support.</p>
      <p>Support: <a href="mailto:support@coinpilotx.app" style="color:#6edff6">support@coinpilotx.app</a></p>
    """)
    return send_platform_email(lead.get("email"), subject, text, html, lead.get("id"))


def send_password_reset_email(user, reset_link):
    subject = "Reset your CoinPilotX password"
    text = (
        f"Hi {account_display_name(user)},\n\n"
        "Use this secure link to reset your CoinPilotX password. It expires in 1 hour:\n"
        f"{reset_link}\n\n"
        "If you did not request this, ignore this email.\n\n"
        "Support: support@coinpilotx.app"
    )
    html = branded_email_html("Reset your CoinPilotX password", f"""
      <p>Hi {clean_html(account_display_name(user))},</p>
      <p>Use this secure link to reset your password. It expires in 1 hour.</p>
      <p><a href="{reset_link}" style="color:#36e58f">Reset password</a></p>
      <p>If you did not request this, ignore this email.</p>
    """)
    return send_platform_email(user.get("email"), subject, text, html, user.get("user_id"))


def send_email_verification(user, verification_link):
    subject = "Verify your CoinPilotX email"
    text = f"Verify your CoinPilotX email here: {verification_link}\n\nSupport: support@coinpilotx.app"
    html = branded_email_html("Verify your CoinPilotX email", f'<p><a href="{verification_link}" style="color:#36e58f">Verify email</a></p>')
    return send_platform_email(user.get("email"), subject, text, html, user.get("user_id"))


def subscription_email_body(plan_name, timestamp, txid=None):
    txid_line = f"\nTXID reference: {txid}\n" if txid else ""
    return (
        "Welcome to CoinPilotX Pro.\n\n"
        "Your Pro access is active.\n\n"
        f"Plan: {plan_name}\n"
        f"Activated at: {timestamp}\n"
        f"{txid_line}"
        "Legal operator: CoinPilotXAI Inc.\n\n"
        "Open the Telegram bot:\n"
        "https://t.me/DocShieldX_bot\n\n"
        "Safety reminder: CoinPilotX will never ask for your seed phrase, private key, or wallet password.\n\n"
        "CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice."
    )


def send_subscription_email(user_id, payment_type, txid=None, stripe_session_id=None, timestamp=None):
    timestamp = timestamp or datetime.now().isoformat()
    email = get_user_email(user_id)
    if payment_type == "btc":
        subject = "Your CoinPilotX BTC Payment Was Verified"
        body = subscription_email_body("CoinPilotX Pro - BTC", timestamp, txid=txid)
    else:
        subject = "Your CoinPilotX Pro Subscription Is Active"
        ref = stripe_session_id or txid
        body = subscription_email_body("CoinPilotX Pro", timestamp, txid=ref)
    return send_email_confirmation(user_id, email, subject, body)


def save_payment_verification(user_id, txid, payment_type, amount, status, details=""):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payment_verifications (user_id, txid, payment_type, amount, status, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, txid, payment_type, amount, status, details, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def payment_txid_already_verified(txid):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM payment_verifications WHERE txid=? AND status='verified' LIMIT 1", (txid,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def send_telegram_confirmation(user_id, text):
    if not BOT_TOKEN:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": user_id, "text": text},
            timeout=10,
        )
        return response.ok
    except Exception as exc:
        logging.info("Telegram confirmation failed: %s", exc)
        return False


def get_watchlist(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT asset FROM watchlists WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows] or ["BTC", "ETH", "SOL"]


def set_watchlist(user_id, assets):
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlists WHERE user_id=?", (user_id,))

    for asset in assets:
        cur.execute("INSERT OR IGNORE INTO watchlists VALUES (?, ?)", (user_id, asset))
        cur.execute("INSERT OR IGNORE INTO manual_portfolio VALUES (?, ?, ?)", (user_id, asset, 0.0))
        cur.execute("INSERT OR IGNORE INTO paper_portfolio VALUES (?, ?, ?)", (user_id, asset, 0.0))

    conn.commit()
    conn.close()


def set_alerts(user_id, enabled):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET alerts_enabled=? WHERE user_id=?", (1 if enabled else 0, user_id))
    conn.commit()
    conn.close()


def get_users_with_alerts():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE alerts_enabled=1")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_all_users():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users


def get_last_price(user_id, asset):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT price FROM last_prices WHERE user_id=? AND asset=?", (user_id, asset))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def set_last_price(user_id, asset, price):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO last_prices VALUES (?, ?, ?)", (user_id, asset, price))
    conn.commit()
    conn.close()


def get_paper_amount(user_id, asset):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT amount FROM paper_portfolio WHERE user_id=? AND asset=?", (user_id, asset))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0.0


def set_paper_amount(user_id, asset, amount):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO paper_portfolio VALUES (?, ?, ?)", (user_id, asset, amount))
    conn.commit()
    conn.close()


def save_price_history(asset, price):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO price_history (asset, price, created_at) VALUES (?, ?, ?)",
        (asset, price, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_price_history(asset, limit=20):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT price FROM price_history WHERE asset=? ORDER BY id DESC LIMIT ?",
        (asset, limit)
    )
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows][::-1]


def calculate_rsi_like(prices):
    if len(prices) < 6:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def moving_average(prices):
    if not prices:
        return None
    return sum(prices) / len(prices)


def smart_market_signal(asset, current_price, is_pro_user=False):
    prices = get_price_history(asset)

    if len(prices) < 20:
        return {
            "action": "WAIT",
            "confidence": "Low",
            "reason": "Not enough data yet.",
            "trend": "Unknown",
            "volatility": "0.00%",
        }

    short_prices = prices[-10:]
    long_prices = prices[-20:]

    short_ma = sum(short_prices) / len(short_prices)
    long_ma = sum(long_prices) / len(long_prices)

    highest = max(short_prices)
    lowest = min(short_prices)
    volatility = ((highest - lowest) / current_price) * 100 if current_price else 0

    # TREND
    if short_ma > long_ma:
        trend = "Uptrend"
    elif short_ma < long_ma:
        trend = "Downtrend"
    else:
        trend = "Sideways"

    # RSI
    rsi = calculate_rsi_like(prices)

    action = "WAIT"
    confidence = "Low"
    reason = "No strong setup."

    # =========================
    # FREE LOGIC (BASIC)
    # =========================
    if not is_pro_user:
        if rsi < 30:
            action = "BUY"
            confidence = "Medium"
            reason = "Oversold."
        elif rsi > 70:
            action = "SELL"
            confidence = "Medium"
            reason = "Overbought."

    # =========================
    # PRO LOGIC (ADVANCED)
    # =========================
    else:
        momentum = current_price - short_prices[0]

        if rsi < 30 and trend == "Uptrend" and momentum > 0:
            action = "BUY"
            confidence = "High"
            reason = "Oversold + upward reversal + positive momentum."

        elif rsi > 70 and trend == "Downtrend" and momentum < 0:
            action = "SELL"
            confidence = "High"
            reason = "Overbought + downward pressure + negative momentum."

        elif trend == "Uptrend" and 40 <= rsi <= 60:
            action = "BUY"
            confidence = "Medium"
            reason = "Healthy uptrend continuation."

        elif trend == "Downtrend" and rsi > 40:
            action = "WAIT"
            confidence = "Medium"
            reason = "Downtrend still active, wait for reversal."

    # VOLATILITY adjustment
    if volatility > 2:
        confidence = "Low"
        reason += " High volatility risk."

    return {
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "trend": trend,
        "volatility": f"{volatility:.2f}%"
    }

def extract_urls(text):
    return re.findall(r"https?://[^\s]+", text)


def is_valid_btc_txid(txid):
    return bool(re.fullmatch(r"[A-Fa-f0-9]{64}", (txid or "").strip()))


def verify_btc_tx(txid):
    try:
        url = BLOCKSTREAM_TX_API + txid
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return False, 0, 0
        tx = response.json()

        confirmations = 0
        status = tx.get("status", {})

        if status.get("confirmed"):
            confirmations = 1

        paid_sats = 0

        for output in tx.get("vout", []):
            if output.get("scriptpubkey_address") == BTC_PAYMENT_ADDRESS:
                paid_sats += int(output.get("value", 0))

        if paid_sats >= BTC_PRO_SATS and confirmations >= BTC_REQUIRED_CONFIRMATIONS:
            return True, paid_sats, confirmations

        return False, paid_sats, confirmations

    except Exception:
        return False, 0, 0
def activate_pro(user_id):
    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_pro INTEGER DEFAULT 0")
    except Exception:
        pass

    cur.execute("UPDATE users SET is_pro=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


# =========================
# PRO MESSAGE (ADD HERE)
# =========================

BTC_PAYMENT_ADDRESS = os.getenv("BTC_PAYMENT_ADDRESS", BTC_PAYMENT_ADDRESS)
BTC_PRO_PRICE = "0.00025 BTC"

def pro_upgrade_message(user_id):
    return (
        "⭐ CoinPilotX Pro\n\n"
        f"Card price: {PRO_PRICE_MONTHLY}\n"
        f"BTC price: {BTC_PRO_PRICE}\n\n"
        "Free is useful for basic awareness. Pro is built for deeper decision support.\n\n"
        "Free includes:\n"
        "• Basic BTC price\n"
        "• Basic alerts\n"
        "• Basic news summaries\n"
        "• Short scam warning\n\n"
        "Pro unlocks:\n"
        "• Deeper AI analysis\n"
        "• Whale intelligence\n"
        "• Portfolio decision engine\n"
        "• Country crypto intelligence\n"
        "• Advanced scam breakdowns\n"
        "• Wallet/transaction risk insights\n"
        "• Market pressure signals\n"
        "• Personalized BUY / SELL / WAIT / HOLD explanations\n\n"
        "CoinPilotX is operated by CoinPilotXAI Inc.\n"
        "No hidden fees from CoinPilotXAI Inc. Card payment opens only through the secure button below.\n"
        "CoinPilotX never holds funds.\n"
        "CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.\n\n"
        "Choose a payment method below when you are ready."
    )


def expand_url(url):
    try:
        return requests.head(url, allow_redirects=True, timeout=5).url
    except Exception:
        try:
            return requests.get(url, allow_redirects=True, timeout=5, stream=True).url
        except Exception:
            return url


def get_domain(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def get_binance_price(asset):
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT"
        return float(requests.get(url, timeout=5).json()["price"])
    except Exception:
        return None


def get_coinbase_price(asset):
    try:
        url = f"https://api.exchange.coinbase.com/products/{asset}-USD/ticker"
        return float(requests.get(url, timeout=5).json()["price"])
    except Exception:
        return None


def normalize_market_item(item):
    return {
        "id": item.get("id") or item.get("symbol", "").lower(),
        "name": item.get("name") or item.get("symbol", "").upper(),
        "symbol": (item.get("symbol") or "").upper(),
        "image": item.get("image") or "",
        "price": item.get("current_price"),
        "volume_24h": item.get("total_volume"),
        "change_24h": item.get("price_change_percentage_24h"),
        "market_cap": item.get("market_cap"),
    }


def fetch_coingecko_markets():
    headers = {}
    api_key = os.getenv("COINGECKO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    response = requests.get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 50,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        },
        headers=headers,
        timeout=12,
    )
    response.raise_for_status()
    return [normalize_market_item(item) for item in response.json()]


def fetch_coinmarketcap_markets():
    api_key = os.getenv("COINMARKETCAP_API_KEY") or os.getenv("CMC_API_KEY")
    if not api_key:
        return []
    response = requests.get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
        params={"start": 1, "limit": 50, "convert": "USD", "sort": "volume_24h"},
        headers={"X-CMC_PRO_API_KEY": api_key},
        timeout=12,
    )
    response.raise_for_status()
    markets = []
    for item in response.json().get("data", []):
        quote = item.get("quote", {}).get("USD", {})
        markets.append({
            "id": str(item.get("id")),
            "name": item.get("name"),
            "symbol": item.get("symbol", "").upper(),
            "image": "",
            "price": quote.get("price"),
            "volume_24h": quote.get("volume_24h"),
            "change_24h": quote.get("percent_change_24h"),
            "market_cap": quote.get("market_cap"),
        })
    return markets


def fetch_coinbase_fallback_markets():
    markets = []
    names = {"BTC": "Bitcoin", "ETH": "Ethereum"}
    for symbol in ["BTC", "ETH"]:
        try:
            response = requests.get(
                f"https://api.exchange.coinbase.com/products/{symbol}-USD/ticker",
                timeout=8,
            )
            response.raise_for_status()
            item = response.json()
            markets.append({
                "id": symbol.lower(),
                "name": names[symbol],
                "symbol": symbol,
                "image": "",
                "price": float(item.get("price")),
                "volume_24h": float(item.get("volume", 0)),
                "change_24h": None,
                "market_cap": None,
            })
        except Exception as exc:
            logging.info("Coinbase market fallback failed for %s: %s", symbol, exc)
    return markets


def sort_markets(markets, category="top_volume"):
    category = (category or "top_volume").lower()
    if category in {"top_market_cap", "market_cap", "cap"}:
        return sorted(markets, key=lambda x: x.get("market_cap") or 0, reverse=True)
    if category == "gainers":
        return sorted(markets, key=lambda x: x.get("change_24h") if x.get("change_24h") is not None else -999, reverse=True)
    if category == "losers":
        return sorted(markets, key=lambda x: x.get("change_24h") if x.get("change_24h") is not None else 999)
    return sorted(markets, key=lambda x: x.get("volume_24h") or 0, reverse=True)


def live_market_board(category="top_volume", limit=50):
    now = datetime.now()
    cached_at = MARKETS_CACHE.get("created_at")
    cache_fresh = (
        MARKETS_CACHE.get("data")
        and cached_at
        and (now - cached_at).total_seconds() < MARKETS_CACHE_SECONDS
    )
    if cache_fresh:
        cached = dict(MARKETS_CACHE["data"])
        cached["markets"] = sort_markets(cached.get("markets", []), category)[:limit]
        cached.setdefault("summary", market_board_summary_metrics(cached.get("markets", [])))
        return cached

    warning = None
    source = "coingecko"
    markets = []
    try:
        markets = fetch_coingecko_markets()
    except Exception as exc:
        logging.info("CoinGecko markets failed: %s", exc)
        warning = "CoinGecko temporarily unavailable."

    if not markets:
        try:
            markets = fetch_coinmarketcap_markets()
            if markets:
                source = "coinmarketcap"
        except Exception as exc:
            logging.info("CoinMarketCap markets failed: %s", exc)
            warning = "CoinGecko/CoinMarketCap temporarily unavailable."

    if not markets:
        markets = fetch_coinbase_fallback_markets()
        if markets:
            source = "coinbase"
            warning = "Using Coinbase BTC/ETH fallback only."

    if not markets and MARKETS_CACHE.get("data"):
        cached = dict(MARKETS_CACHE["data"])
        cached["warning"] = "Live market data temporarily unavailable. Showing last cached data."
        cached["stale"] = True
        cached["markets"] = sort_markets(cached.get("markets", []), category)[:limit]
        cached.setdefault("summary", market_board_summary_metrics(cached.get("markets", [])))
        return cached

    if not markets:
        return {
            "source": "unavailable",
            "updated_at": now.isoformat(),
            "warning": "Live market data temporarily unavailable. Try again shortly.",
            "stale": False,
            "markets": [],
            "summary": market_board_summary_metrics([]),
        }

    data = {
        "source": source,
        "updated_at": now.isoformat(),
        "warning": warning,
        "stale": False,
        "markets": sort_markets(markets, category)[:limit],
        "summary": market_board_summary_metrics(markets),
    }
    if markets:
        MARKETS_CACHE["data"] = data
        MARKETS_CACHE["created_at"] = now
    return data


def market_board_summary_metrics(markets):
    valid_caps = [m for m in markets if isinstance(m.get("market_cap"), (int, float)) and m.get("market_cap") > 0]
    total_cap = sum(m["market_cap"] for m in valid_caps)
    btc = next((m for m in markets if m.get("symbol") == "BTC"), None)
    eth = next((m for m in markets if m.get("symbol") == "ETH"), None)
    btc_dominance = (btc.get("market_cap") / total_cap * 100) if btc and total_cap else None
    valid_changes = [m for m in markets if isinstance(m.get("change_24h"), (int, float))]
    avg_change = sum(m["change_24h"] for m in valid_changes) / len(valid_changes) if valid_changes else None
    gainers = sorted(valid_changes, key=lambda m: m["change_24h"], reverse=True)[:3]
    losers = sorted(valid_changes, key=lambda m: m["change_24h"])[:3]
    return {
        "btc_dominance_proxy": btc_dominance,
        "btc_change_24h": btc.get("change_24h") if btc else None,
        "eth_change_24h": eth.get("change_24h") if eth else None,
        "average_change_24h": avg_change,
        "trending_narratives": [m.get("symbol") for m in gainers if m.get("symbol")],
        "risk_pockets": [m.get("symbol") for m in losers if m.get("symbol")],
    }


def get_market_by_symbol(symbol):
    data = live_market_board(limit=50)
    symbol = normalize_asset(symbol)
    for item in data.get("markets", []):
        if item.get("symbol") == symbol:
            return item
    return None


def compact_usd(value):
    if value is None:
        return "n/a"
    try:
        value = float(value)
    except Exception:
        return "n/a"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1:
        return f"${value:,.2f}"
    return f"${value:.6f}"


def market_pressure_interpretation(markets):
    valid = [m for m in markets if isinstance(m.get("change_24h"), (int, float))]
    if not valid:
        return "Market breadth is still loading, so review price and volume before acting."
    positive = sum(1 for m in valid if m["change_24h"] > 0)
    avg_change = sum(m["change_24h"] for m in valid) / len(valid)
    top_volume = markets[0].get("symbol") if markets else "BTC"
    if avg_change > 2 and positive >= len(valid) * 0.6:
        read = "market data suggests broad positive momentum"
    elif avg_change < -2 and positive <= len(valid) * 0.4:
        read = "risk may be elevated because market breadth is weak"
    else:
        read = "market conditions look mixed, so confirmation matters"
    return f"{read}. Top-volume pressure is led by {top_volume}. Review before acting."


def market_board_summary(user_id, category="top_volume"):
    data = live_market_board(category=category, limit=20)
    markets = data.get("markets", [])
    selected = markets[:10]
    if not selected:
        return (
            "📊 Live Crypto Markets\n\n"
            "Live market data temporarily unavailable. Try again shortly.\n\n"
            "Educational only — not financial advice."
        )

    title_map = {
        "top_volume": "Top Volume",
        "top_market_cap": "Top Market Cap",
        "gainers": "Top Gainers",
        "losers": "Top Losers",
    }
    title = title_map.get(category, "Live Markets")
    lines = [
        f"📊 {title}",
        f"Source: {data.get('source', 'market data')}",
        f"Updated: {data.get('updated_at', '')[:19]}",
    ]
    if data.get("warning"):
        lines.append(f"Note: {data['warning']}")
    lines.append("")
    for index, item in enumerate(selected, start=1):
        change = item.get("change_24h")
        change_text = "n/a" if change is None else f"{change:+.2f}%"
        lines.append(
            f"{index}. {item.get('name')} ({item.get('symbol')}) — "
            f"{compact_usd(item.get('price'))} | 24h {change_text} | Vol {compact_usd(item.get('volume_24h'))}"
        )

    interpretation = market_pressure_interpretation(selected)
    if is_pro(user_id):
        gainers = [m for m in markets if isinstance(m.get("change_24h"), (int, float)) and m["change_24h"] > 0]
        losers = [m for m in markets if isinstance(m.get("change_24h"), (int, float)) and m["change_24h"] < 0]
        lines.extend([
            "",
            "Pro market pressure read:",
            interpretation,
            f"Breadth: {len(gainers)} positive vs {len(losers)} negative among loaded assets.",
            "Risk context: high-volume downside moves can signal elevated caution; high-volume upside moves can signal stronger momentum.",
        ])
    else:
        lines.extend(["", f"Quick read: {interpretation}"])

    lines.append("")
    lines.append("Educational only — not financial advice.")
    return append_plan_footer(user_id, "\n".join(lines))


def scam_text_intelligence(text):
    lowered = (text or "").lower()
    checks = [
        ("Seed phrase/private key request", ["seed phrase", "private key", "recovery phrase", "12 words", "24 words"], "Critical"),
        ("Fake support or admin pressure", ["support agent", "official support", "telegram admin", "verify your wallet", "sync your wallet"], "High"),
        ("Fake airdrop or claim lure", ["airdrop", "claim now", "free token", "reward", "allocation"], "Medium"),
        ("Wallet-drainer approval language", ["approve", "unlimited approval", "connect wallet", "sign message", "permit"], "High"),
        ("Urgency and fear pressure", ["urgent", "limited time", "act now", "last chance", "account will be locked"], "Medium"),
        ("Guaranteed return language", ["guaranteed profit", "risk free", "double your", "daily roi", "sure win"], "High"),
        ("Suspicious link pattern", ["http://", "bit.ly", "tinyurl", ".xyz", ".top", ".click"], "Medium"),
    ]
    flags = []
    severity_score = 0
    for label, needles, severity in checks:
        hits = [needle for needle in needles if needle in lowered]
        if hits:
            flags.append({"label": label, "severity": severity, "matched": hits[:3]})
            severity_score += {"Critical": 45, "High": 30, "Medium": 18}.get(severity, 10)

    if severity_score >= 70:
        risk = "Critical"
    elif severity_score >= 45:
        risk = "High"
    elif severity_score >= 18:
        risk = "Medium"
    else:
        risk = "Low"
        flags.append({"label": "No obvious high-risk phrase detected", "severity": "Low", "matched": []})

    why = [
        f"{flag['severity']}: {flag['label']}" + (f" ({', '.join(flag['matched'])})" if flag["matched"] else "")
        for flag in flags
    ]
    recommendations = [
        "Do not enter seed phrases, private keys, recovery phrases, or wallet passwords.",
        "Verify links through official websites, not DMs or ads.",
        "Avoid signing wallet approvals unless you understand the exact permission.",
        "If urgency is part of the pitch, slow down and verify independently.",
    ]
    response = (
        "🛡 Scam Shield Scan\n\n"
        f"Risk level: {risk}\n\n"
        "Why this was flagged:\n"
        + "\n".join(f"• {item}" for item in why)
        + "\n\nSafety recommendations:\n"
        + "\n".join(f"• {item}" for item in recommendations)
        + "\n\nCoinPilotX will never ask for your seed phrase, private key, or wallet password.\n"
        "Informational only — not financial advice."
    )
    return {"risk": risk, "flags": flags, "recommendations": recommendations, "response": response}


def platform_status():
    market_data = live_market_board(limit=12)
    sports_data = live_sports_edge()
    market_ok = bool(market_data.get("markets"))
    sports_ok = sports_data.get("warning") is None and bool(sports_data.get("games"))
    openai_ok = bool(os.getenv("OPENAI_API_KEY"))
    status = {
        "updated_at": datetime.now().isoformat(),
        "website": "online",
        "telegram_bot": "configured" if BOT_TOKEN else "bot token missing",
        "market_data": "live" if market_ok else "degraded",
        "market_source": market_data.get("source", "unavailable"),
        "sports_edge": "live" if sports_ok else "standby",
        "sports_source": sports_data.get("source", "unavailable"),
        "odds_status": sports_data.get("odds_status", "unavailable"),
        "ai_assistant": "online" if openai_ok else "fallback mode",
        "wallet_intelligence": "public BTC explorer",
        "scam_shield": "rules active" + (" + AI available" if openai_ok else ""),
        "safety": "No private keys or seed phrases. Informational only — not financial advice.",
    }
    return status


SPORTS_EDGE_LEAGUES = [
    {"key": "nba", "label": "NBA", "sport": "basketball", "league": "nba", "odds_key": "basketball_nba", "visual": "basketball"},
    {"key": "nfl", "label": "NFL", "sport": "football", "league": "nfl", "odds_key": "americanfootball_nfl", "visual": "football"},
    {"key": "mlb", "label": "MLB", "sport": "baseball", "league": "mlb", "odds_key": "baseball_mlb", "visual": "baseball"},
    {"key": "nhl", "label": "NHL", "sport": "hockey", "league": "nhl", "odds_key": "icehockey_nhl", "visual": "hockey"},
    {"key": "epl", "label": "EPL", "sport": "soccer", "league": "eng.1", "odds_key": "soccer_epl", "visual": "soccer"},
    {"key": "tennis", "label": "Tennis", "sport": "tennis", "league": "atp", "odds_key": "tennis_atp", "visual": "tennis"},
]

SPORTS_SAFETY_LINE = "Informational only — not betting or financial advice. Never risk money you cannot afford to lose."


def parse_score(value):
    try:
        return int(float(value))
    except Exception:
        return 0


def competitor_record(competitor):
    records = competitor.get("records") or []
    if records:
        return records[0].get("summary") or ""
    return ""


def normalize_team_key(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def american_implied_probability(price):
    try:
        price = float(price)
    except Exception:
        return None
    if price > 0:
        return 100 / (price + 100)
    if price < 0:
        return abs(price) / (abs(price) + 100)
    return None


def decimal_implied_probability(price):
    try:
        price = float(price)
    except Exception:
        return None
    if price <= 1:
        return None
    return 1 / price


def implied_probability_from_odds(price):
    if price is None:
        return None
    try:
        numeric = float(price)
    except Exception:
        return None
    if numeric > 20 or numeric < 0:
        return american_implied_probability(numeric)
    return decimal_implied_probability(numeric)


def extract_odds_context(event):
    bookmakers = event.get("bookmakers") or []
    context = {
        "odds_available": False,
        "odds_status": "unavailable",
        "sportsbook_count": len(bookmakers),
        "home_moneyline": None,
        "away_moneyline": None,
        "home_implied_probability": None,
        "away_implied_probability": None,
        "spread": None,
        "total": None,
    }
    if not bookmakers:
        return context

    context["odds_available"] = True
    context["odds_status"] = "available from configured odds provider"
    first = bookmakers[0]
    for market in first.get("markets", []):
        key = market.get("key")
        outcomes = market.get("outcomes") or []
        if key == "h2h":
            for outcome in outcomes:
                name_key = normalize_team_key(outcome.get("name"))
                price = outcome.get("price")
                if name_key == normalize_team_key(event.get("home_team")):
                    context["home_moneyline"] = price
                    context["home_implied_probability"] = implied_probability_from_odds(price)
                elif name_key == normalize_team_key(event.get("away_team")):
                    context["away_moneyline"] = price
                    context["away_implied_probability"] = implied_probability_from_odds(price)
        elif key == "spreads" and outcomes:
            context["spread"] = outcomes[0].get("point")
        elif key == "totals" and outcomes:
            context["total"] = outcomes[0].get("point")
    return context


def fetch_the_odds_games(league_info):
    api_key = os.getenv("THE_ODDS_API_KEY")
    if not api_key or not league_info.get("odds_key"):
        return []
    response = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{league_info['odds_key']}/odds",
        params={
            "apiKey": api_key,
            "regions": os.getenv("THE_ODDS_REGIONS", "us"),
            "markets": "h2h,spreads,totals",
            "oddsFormat": os.getenv("THE_ODDS_FORMAT", "american"),
            "dateFormat": "iso",
        },
        timeout=12,
    )
    response.raise_for_status()
    games = []
    for event in response.json():
        game = {
            "id": f"{league_info['key']}:{event.get('id')}",
            "callback_id": f"sportsedge_{league_info['key']}_{event.get('id')}",
            "event_id": str(event.get("id") or ""),
            "league": league_info["key"],
            "league_label": league_info["label"],
            "sport": league_info["sport"],
            "visual": league_info["visual"],
            "name": f"{event.get('away_team', 'Away')} at {event.get('home_team', 'Home')}",
            "short_name": "",
            "home_team": event.get("home_team") or "Home",
            "away_team": event.get("away_team") or "Away",
            "home_abbr": (event.get("home_team") or "HOME")[:3].upper(),
            "away_abbr": (event.get("away_team") or "AWAY")[:3].upper(),
            "home_score": 0,
            "away_score": 0,
            "home_record": "",
            "away_record": "",
            "status": "Upcoming",
            "state": "pre",
            "is_live": False,
            "is_final": False,
            "venue": "",
            "start_time": event.get("commence_time") or "",
            "data_quality": "odds only; live score unavailable",
        }
        game.update(extract_odds_context(event))
        games.append(game)
    return games


def fetch_sportsdata_games(league_info):
    api_key = os.getenv("SPORTSDATA_API_KEY")
    endpoint_map = {
        "nba": "https://api.sportsdata.io/v3/nba/scores/json/GamesByDate/{date}",
        "mlb": "https://api.sportsdata.io/v3/mlb/scores/json/GamesByDate/{date}",
        "nhl": "https://api.sportsdata.io/v3/nhl/scores/json/GamesByDate/{date}",
    }
    if not api_key or league_info["key"] not in endpoint_map:
        return []
    today = datetime.utcnow().strftime("%Y-%b-%d").upper()
    response = requests.get(
        endpoint_map[league_info["key"]].format(date=today),
        headers={"Ocp-Apim-Subscription-Key": api_key},
        timeout=12,
    )
    response.raise_for_status()
    games = []
    for event in response.json():
        event_id = str(event.get("GameID") or event.get("GameId") or event.get("GlobalGameID") or "")
        if not event_id:
            continue
        home_team = event.get("HomeTeam") or event.get("HomeTeamName") or "Home"
        away_team = event.get("AwayTeam") or event.get("AwayTeamName") or "Away"
        status = event.get("Status") or event.get("GameStatus") or "Scheduled"
        state = "post" if str(status).lower() in {"final", "f/final"} else "in" if str(status).lower() in {"inprogress", "in progress"} else "pre"
        games.append({
            "id": f"{league_info['key']}:{event_id}",
            "callback_id": f"sportsedge_{league_info['key']}_{event_id}",
            "event_id": event_id,
            "league": league_info["key"],
            "league_label": league_info["label"],
            "sport": league_info["sport"],
            "visual": league_info["visual"],
            "name": f"{away_team} at {home_team}",
            "short_name": "",
            "home_team": home_team,
            "away_team": away_team,
            "home_abbr": str(home_team)[:3].upper(),
            "away_abbr": str(away_team)[:3].upper(),
            "home_score": parse_score(event.get("HomeTeamScore")),
            "away_score": parse_score(event.get("AwayTeamScore")),
            "home_record": "",
            "away_record": "",
            "status": str(status),
            "state": state,
            "is_live": state == "in",
            "is_final": state == "post",
            "venue": event.get("StadiumDetails", {}).get("Name", "") if isinstance(event.get("StadiumDetails"), dict) else "",
            "start_time": event.get("DateTime") or event.get("Day") or "",
            "odds_available": False,
            "odds_status": "unavailable",
            "sportsbook_count": 0,
            "home_moneyline": None,
            "away_moneyline": None,
            "home_implied_probability": None,
            "away_implied_probability": None,
            "spread": None,
            "total": None,
            "data_quality": "sportsdata scoreboard",
        })
    return games


def merge_odds_into_games(games, league_info):
    try:
        odds_games = fetch_the_odds_games(league_info)
    except Exception as exc:
        logging.info("The Odds API failed for %s: %s", league_info["key"], exc)
        return games, False
    if not odds_games:
        return games, False

    by_matchup = {
        (normalize_team_key(game.get("home_team")), normalize_team_key(game.get("away_team"))): game
        for game in odds_games
    }
    merged = []
    for game in games:
        odds = by_matchup.get((normalize_team_key(game.get("home_team")), normalize_team_key(game.get("away_team"))))
        if odds:
            for key in ["odds_available", "odds_status", "sportsbook_count", "home_moneyline", "away_moneyline", "home_implied_probability", "away_implied_probability", "spread", "total"]:
                game[key] = odds.get(key)
        merged.append(game)
    if not merged:
        merged = odds_games
    return merged, True


def parse_espn_event(event, league_info):
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})
    status_type = (event.get("status") or {}).get("type") or {}
    venue = (competition.get("venue") or {}).get("fullName") or ""
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    home_score = parse_score(home.get("score"))
    away_score = parse_score(away.get("score"))
    state = status_type.get("state") or "pre"
    is_live = state == "in"
    is_final = state == "post"

    return {
        "id": f"{league_info['key']}:{event.get('id')}",
        "callback_id": f"sportsedge_{league_info['key']}_{event.get('id')}",
        "event_id": str(event.get("id") or ""),
        "league": league_info["key"],
        "league_label": league_info["label"],
        "sport": league_info["sport"],
        "visual": league_info["visual"],
        "name": event.get("name") or f"{away_team.get('displayName', 'Away')} at {home_team.get('displayName', 'Home')}",
        "short_name": event.get("shortName") or "",
        "home_team": home_team.get("displayName") or home_team.get("name") or "Home",
        "away_team": away_team.get("displayName") or away_team.get("name") or "Away",
        "home_abbr": home_team.get("abbreviation") or "HOME",
        "away_abbr": away_team.get("abbreviation") or "AWAY",
        "home_score": home_score,
        "away_score": away_score,
        "home_record": competitor_record(home),
        "away_record": competitor_record(away),
        "status": status_type.get("shortDetail") or status_type.get("description") or "Scheduled",
        "state": state,
        "is_live": is_live,
        "is_final": is_final,
        "venue": venue,
        "start_time": event.get("date") or "",
        "odds_available": False,
        "odds_status": "unavailable",
        "sportsbook_count": 0,
        "home_moneyline": None,
        "away_moneyline": None,
        "home_implied_probability": None,
        "away_implied_probability": None,
        "spread": None,
        "total": None,
        "data_quality": "public scoreboard",
    }


def fetch_espn_scoreboard(league_info):
    response = requests.get(
        f"https://site.api.espn.com/apis/site/v2/sports/{league_info['sport']}/{league_info['league']}/scoreboard",
        timeout=10,
    )
    response.raise_for_status()
    games = []
    for event in response.json().get("events", []):
        parsed = parse_espn_event(event, league_info)
        if parsed:
            games.append(parsed)
    return games


def game_sort_key(game):
    state_rank = {"in": 0, "pre": 1, "post": 2}.get(game.get("state"), 1)
    return (state_rank, game.get("start_time") or "")


def fetch_sports_edge_games(league="all", limit=30):
    selected = [
        item for item in SPORTS_EDGE_LEAGUES
        if league in {"", "all"} or item["key"] == league
    ] or SPORTS_EDGE_LEAGUES
    games = []
    source_notes = []
    odds_used = False
    sportsdata_used = False
    for league_info in selected:
        league_games = []
        try:
            league_games = fetch_sportsdata_games(league_info)
            sportsdata_used = sportsdata_used or bool(league_games)
        except Exception as exc:
            logging.info("SportsData scoreboard failed for %s: %s", league_info["key"], exc)
        try:
            espn_games = fetch_espn_scoreboard(league_info)
            if not league_games:
                league_games = espn_games
            if espn_games:
                source_notes.append(league_info["label"])
        except Exception as exc:
            logging.info("Sports Edge scoreboard failed for %s: %s", league_info["key"], exc)
        league_games, league_odds_used = merge_odds_into_games(league_games, league_info)
        odds_used = odds_used or league_odds_used
        if not league_games:
            try:
                league_games = fetch_the_odds_games(league_info)
                odds_used = odds_used or bool(league_games)
            except Exception as exc:
                logging.info("The Odds API odds-only fallback failed for %s: %s", league_info["key"], exc)
        games.extend(league_games)
    games = sorted(games, key=game_sort_key)[:limit]
    return games, source_notes, odds_used, sportsdata_used


def get_sports_edge_games(league="all", limit=30):
    now = datetime.now()
    cached_at = SPORTS_EDGE_CACHE.get("created_at")
    cached_data = SPORTS_EDGE_CACHE.get("data")
    cache_fresh = cached_data and cached_at and (now - cached_at).total_seconds() < SPORTS_EDGE_CACHE_SECONDS

    if cache_fresh:
        games = cached_data.get("games", [])
        if league not in {"", "all"}:
            games = [game for game in games if game.get("league") == league]
        return games[:limit], cached_data.get("source", "espn_public_scoreboard"), cached_data.get("warning")

    games, source_notes, odds_used, sportsdata_used = fetch_sports_edge_games("all", limit=60)
    warning = None
    if not games:
        warning = "Live sports data temporarily unavailable. Try again shortly."
    source_parts = []
    if odds_used:
        source_parts.append("the_odds_api")
    if sportsdata_used:
        source_parts.append("sportsdata")
    if source_notes:
        source_parts.append("espn_public_scoreboard")
    if os.getenv("SPORTSDATA_API_KEY"):
        source_parts.append("sportsdata_key_configured")
    data = {
        "games": games,
        "source": " + ".join(source_parts) if source_parts else "espn_public_scoreboard",
        "odds_status": "available" if odds_used else "unavailable",
        "warning": warning,
    }
    SPORTS_EDGE_CACHE["data"] = data
    SPORTS_EDGE_CACHE["created_at"] = now
    if not games and cached_data:
        cached_games = cached_data.get("games", [])
        if league not in {"", "all"}:
            cached_games = [game for game in cached_games if game.get("league") == league]
        return cached_games[:limit], cached_data.get("source", "espn_public_scoreboard"), "Showing last cached sports data."

    if league not in {"", "all"}:
        games = [game for game in games if game.get("league") == league]
    return games[:limit], data["source"], warning


def sports_risk_label(game):
    home_score = game.get("home_score", 0)
    away_score = game.get("away_score", 0)
    state = game.get("state")
    margin = abs(home_score - away_score)
    if state == "post":
        return "Low"
    if state == "pre":
        return "Medium" if game.get("odds_available") else "Elevated"
    if game.get("sport") in {"soccer", "hockey", "tennis"} and margin <= 1:
        return "High"
    if margin <= 3:
        return "High"
    if margin >= 14 and game.get("sport") in {"basketball", "football"}:
        return "Elevated"
    return "Elevated"


def probability_text(value):
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "n/a"


def odds_text(game):
    if not game.get("odds_available"):
        return "Odds are unavailable in this feed, so the read avoids pretending to know market pricing."
    return (
        f"Odds available from {game.get('sportsbook_count', 0)} book source(s). "
        f"Implied probability: {game.get('away_abbr')} {probability_text(game.get('away_implied_probability'))}, "
        f"{game.get('home_abbr')} {probability_text(game.get('home_implied_probability'))}. "
        f"Spread: {game.get('spread') if game.get('spread') is not None else 'n/a'} · "
        f"Total: {game.get('total') if game.get('total') is not None else 'n/a'}."
    )


def sports_game_intelligence(game):
    home_score = game.get("home_score", 0)
    away_score = game.get("away_score", 0)
    margin = home_score - away_score
    abs_margin = abs(margin)
    leading = game["home_team"] if margin > 0 else game["away_team"] if margin < 0 else "Neither side"
    trailing = game["away_team"] if margin > 0 else game["home_team"] if margin < 0 else "Neither side"
    state = game.get("state")
    sport = game.get("sport") or game.get("league")
    risk_label = sports_risk_label(game)
    score_line = f"{game['away_team']} {away_score} - {home_score} {game['home_team']}" if state != "pre" else f"{game['away_team']} at {game['home_team']}"

    matchup = f"{game.get('league_label')} matchup: {score_line}. Status: {game.get('status', 'Scheduled')}."
    current_state = (
        "The game is final, so the best use is post-game review."
        if state == "post"
        else "The game has not started yet; lineup, injury, weather, and price confirmation matter."
        if state == "pre"
        else f"Live scoreboard edge: {leading} leads by {abs_margin}. Momentum can change quickly from here."
    )
    market_context = odds_text(game)

    sport_notes = {
        "basketball": {
            "momentum": "Basketball momentum can swing through pace, scoring runs, foul trouble, and three-point variance.",
            "risk": ["Pace can inflate live totals quickly.", "A short scoring run can erase a mid-size lead.", "Player availability is not confirmed by this public feed."],
            "change": "confirmed injuries, foul trouble, pace shift, or a major odds move",
        },
        "football": {
            "momentum": "Football position quality depends heavily on down-distance pressure, turnovers, clock, field position, and red-zone efficiency.",
            "risk": ["One turnover can swing win probability sharply.", "Clock state matters more late in halves.", "This feed may not include weather or injury context."],
            "change": "turnover margin, quarterback injury news, weather, or a late clock-management shift",
        },
        "baseball": {
            "momentum": "Baseball is lower scoring, so inning, bullpen quality, pitcher fatigue, and base-runner pressure matter more than a simple score gap.",
            "risk": ["Bullpen changes can flip the game script.", "Low-scoring volatility makes small edges fragile.", "Pitcher and lineup context may be missing."],
            "change": "starter exit, bullpen mismatch, late-inning base traffic, or lineup news",
        },
        "hockey": {
            "momentum": "Hockey can turn on shot pressure, goalie performance, penalties, and empty-net game state.",
            "risk": ["Penalty swings can change pressure fast.", "Goalie variance is high.", "Shot pressure is limited if unavailable from the feed."],
            "change": "power-play pressure, goalie change, shot imbalance, or late empty-net situation",
        },
        "soccer": {
            "momentum": "Soccer is low scoring, so time remaining, draw risk, red-card risk, and set-piece pressure matter.",
            "risk": ["Draw risk can dominate close matches.", "One red card can change the entire position.", "Live xG and card context may be missing."],
            "change": "red card, tactical substitution, late pressure, or confirmed injury",
        },
        "tennis": {
            "momentum": "Tennis depends on serve hold pressure, break chances, set score, fatigue, and surface-specific rhythm.",
            "risk": ["A single break can swing the market.", "Serve advantage matters more than raw score.", "Point-by-point momentum may be missing."],
            "change": "serve pressure, break-point conversion, medical timeout, or visible fatigue",
        },
    }
    note = sport_notes.get(sport, {
        "momentum": "Momentum should be judged through score, time, market price, and sport-specific context.",
        "risk": ["Live markets can overreact.", "Public feeds may miss injury and lineup context.", "Price matters as much as the prediction."],
        "change": "new lineup, injury, odds, or momentum information",
    })

    if state == "post":
        action = "REVIEW ONLY"
        considerations = ["Use the result to compare your pre-game read against what actually happened."]
        avoid = "Avoid forcing a follow-up position just because the final score creates emotion."
    elif state == "pre":
        action = "WAIT FOR CONFIRMATION"
        considerations = ["Consider a position only if price, research, and risk limit all agree.", "Pre-game reads improve when odds, lineups, and availability are confirmed."]
        avoid = "Avoid forcing a position before the market price and missing context are clear."
    elif risk_label == "High":
        action = "HIGH VOLATILITY"
        considerations = [f"{leading} has the current edge, but the game state is fragile.", "If price does not compensate for uncertainty, waiting is cleaner."]
        avoid = "Avoid chasing a live move when one play, possession, inning, goal, or break can flip the view."
    elif abs_margin >= 10:
        action = "WATCH CLOSELY"
        considerations = [f"{leading} controls the scoreboard, which can support a cautious momentum lean.", f"Also check whether {trailing} has comeback paths before trusting the score."]
        avoid = "Avoid paying an inflated live price after the obvious move already happened."
    else:
        action = "REVIEW RISK"
        considerations = ["The current edge is measurable but not decisive.", "Position quality depends on whether the market price still leaves room for error."]
        avoid = "Avoid treating a small scoreboard edge as a full prediction."

    return {
        "action": action,
        "risk_label": risk_label,
        "risk_level": f"{risk_label}: {', '.join(note['risk'][:2])}",
        "matchup_summary": matchup,
        "current_game_state": current_state,
        "market_odds_context": market_context,
        "momentum_read": note["momentum"],
        "risk_factors": note["risk"],
        "position_considerations": considerations,
        "why_avoid": avoid,
        "what_could_change": f"The view could change with {note['change']}.",
        "final_caution": SPORTS_SAFETY_LINE,
        "market_context": f"{market_context} {game.get('data_quality', 'Public data only')}.",
        "disclaimer": SPORTS_SAFETY_LINE,
    }


def find_sports_game(game_id):
    if not game_id:
        return None
    normalized = game_id.replace("_", ":")
    games, _, _ = get_sports_edge_games(limit=60)
    for game in games:
        if game.get("id") == normalized or game.get("event_id") == game_id:
            return game
    return None


def live_sports_edge(game_id="", league="all"):
    games, source, warning = get_sports_edge_games(league=league, limit=30)
    for game in games:
        game["risk_label"] = sports_risk_label(game)
    payload = {
        "source": source,
        "updated_at": datetime.now().isoformat(),
        "warning": warning,
        "games": games,
        "odds_status": "available" if any(game.get("odds_available") for game in games) else "unavailable",
        "disclaimer": SPORTS_SAFETY_LINE,
    }
    if game_id:
        game = find_sports_game(game_id)
        if game:
            payload["selected_game"] = game
            payload["analysis"] = sports_game_intelligence(game)
        else:
            payload["warning"] = "That game is no longer available in the live feed."
    return payload


def sports_edge_footer(user_id):
    if user_id and is_pro(user_id):
        return "⭐ Pro active — deeper Sports Edge intelligence enabled."
    return "⭐ Want deeper Sports Edge reasoning, market pressure, and risk breakdowns? Upgrade to CoinPilotX Pro."


def sports_edge_summary(user_id=None):
    data = live_sports_edge()
    games = data.get("games", [])[:8]
    lines = [
        "🎲 Live Sports Edge",
        f"Source: {data.get('source', 'public scoreboard data')}",
        f"Odds: {data.get('odds_status', 'unavailable')}",
        f"Updated: {data.get('updated_at', '')[:19]}",
        "",
    ]
    if data.get("warning"):
        lines.append(data["warning"])
        lines.append("")
    if not games:
        lines.extend([
            "No live games are available from the public feed right now.",
            "Check back shortly or use this section as a reminder to review risk before taking any position.",
        ])
    else:
        lines.append("Tap a game below for deeper intelligence, or review the live board on the website.")
        lines.append("")
        for index, game in enumerate(games, start=1):
            score = f"{game['away_abbr']} {game['away_score']} - {game['home_score']} {game['home_abbr']}" if game.get("state") != "pre" else "Scheduled"
            lines.append(f"{index}. {game['league_label']} · {game['away_team']} at {game['home_team']}")
            lines.append(f"   {score} · {game['status']} · Risk: {game.get('risk_label', sports_risk_label(game))}")
    lines.extend([
        "",
        SPORTS_SAFETY_LINE,
    ])
    if user_id:
        lines.extend(["", sports_edge_footer(user_id)])
    return "\n".join(lines)


def openai_sports_edge_analysis(user_id, game, base_text):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not user_id or not is_pro(user_id):
        return None
    prompt = (
        "Deepen this CoinPilotX Sports Edge read without guaranteeing outcomes. "
        "Use concise sections, sport-specific risk, market context, what could change, and a final caution. "
        f"Game data: {json.dumps(game, ensure_ascii=False)[:2500]}\n\nBase read:\n{base_text[:2500]}\n\n"
        f"Required safety line: {SPORTS_SAFETY_LINE}"
    )
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": "You are CoinPilotX Sports Edge: cautious, ethical, analytical, and never certainty-based."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 700,
                "temperature": 0.32,
            },
            timeout=20,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"].strip()
        if SPORTS_SAFETY_LINE not in text:
            text += f"\n\n{SPORTS_SAFETY_LINE}"
        return text
    except Exception as exc:
        logging.info("Sports Edge OpenAI analysis failed: %s", exc)
        return None


def sports_edge_game_summary(game_id, user_id=None):
    game = find_sports_game(game_id)
    if not game:
        return (
            "🎲 Sports Edge\n\n"
            "That game is no longer available in the live feed. Try /sportsedge again for the latest list.\n\n"
            f"{SPORTS_SAFETY_LINE}"
        )
    analysis = sports_game_intelligence(game)
    score = f"{game['away_team']} {game['away_score']} - {game['home_score']} {game['home_team']}" if game.get("state") != "pre" else f"{game['away_team']} at {game['home_team']}"
    pro = bool(user_id and is_pro(user_id))
    lines = [
        "🎲 Sports Edge Intelligence",
        "",
        f"Game: {score}\n"
        f"League: {game['league_label']}\n"
        f"Status: {game['status']}\n"
        f"Position read: {analysis['action']}\n"
        f"Risk: {analysis['risk_label']}",
        "",
        "Matchup Summary:",
        analysis["matchup_summary"],
        "",
        "Current Game State:",
        analysis["current_game_state"],
        "",
        "Main Risk:",
        analysis["risk_level"],
    ]
    if pro:
        lines.extend([
            "",
            "Market / Odds Context:",
            analysis["market_odds_context"],
            "",
            "Momentum Read:",
            analysis["momentum_read"],
            "",
            "Position Considerations:",
            "\n".join(f"• {item}" for item in analysis["position_considerations"]),
            "",
            "Why to Avoid Forcing a Position:",
            analysis["why_avoid"],
            "",
            "What Could Change the View:",
            analysis["what_could_change"],
        ])
    else:
        lines.extend([
            "",
            "Quick Position Context:",
            analysis["position_considerations"][0],
            "",
            "Why to Wait/Avoid:",
            analysis["why_avoid"],
            "",
            "Free view: shortened Sports Edge summary.",
        ])
    lines.extend(["", analysis["final_caution"], "", sports_edge_footer(user_id)])
    deterministic = "\n".join(lines)
    ai_text = openai_sports_edge_analysis(user_id, game, deterministic)
    return f"{ai_text}\n\n{sports_edge_footer(user_id)}" if ai_text else deterministic


def sports_edge_menu():
    data = live_sports_edge()
    rows = []
    for game in data.get("games", [])[:10]:
        label = f"{game['league_label']} · {game['away_abbr']} @ {game['home_abbr']}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=game["callback_id"])])
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def get_best_price(asset):
    prices = {}

    market_item = get_market_by_symbol(asset)
    if market_item and market_item.get("price"):
        prices["CoinGecko"] = float(market_item["price"])

    b = get_binance_price(asset)
    c = get_coinbase_price(asset)

    if b:
        prices["Binance"] = b
    if c:
        prices["Coinbase"] = c

    if not prices:
        return None, {}

    return sum(prices.values()) / len(prices), prices
def get_portfolio_summary(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT asset, amount FROM manual_portfolio WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()

    total_value = 0
    asset_values = {}

    for asset, amount in rows:
        if amount <= 0:
            continue

        price, _ = get_best_price(asset)
        if not price:
            continue

        value = amount * price
        total_value += value
        asset_values[asset] = value

    if not asset_values:
        return "No assets in portfolio."

    top_asset = max(asset_values, key=asset_values.get)

    # Simple risk estimation
    volatility_scores = []
    for asset in asset_values:
        history = get_price_history(asset, limit=10)
        if len(history) > 1:
            v = (max(history) - min(history)) / history[-1] * 100
            volatility_scores.append(v)

    avg_volatility = sum(volatility_scores) / len(volatility_scores) if volatility_scores else 0

    if avg_volatility < 1:
        risk = "Low"
    elif avg_volatility < 3:
        risk = "Medium"
    else:
        risk = "High"

    insights = []

    if len(asset_values) == 1:
        insights.append("You are fully concentrated in one asset.")
    else:
        insights.append("You are diversified across assets.")

    insights.append(f"{top_asset} is your largest position.")

    if risk == "High":
        insights.append("Portfolio is volatile. Consider reducing risk.")
    elif risk == "Low":
        insights.append("Portfolio is relatively stable.")

    return (
        f"💼 Your Portfolio\n\n"
        f"Total Value: ${total_value:,.2f}\n"
        f"Top Asset: {top_asset}\n"
        f"Risk Level: {risk}\n\n"
        f"Insights:\n• " + "\n• ".join(insights)
    )


def analyze_text(text):
    score = 0
    reasons = []
    expanded_results = []
    low = text.lower()
    urls = extract_urls(text)

    phishing_words = [
        "urgent", "verify", "password", "login", "account suspended",
        "click here", "free gift", "limited time"
    ]

    crypto_words = [
        "connect wallet", "seed phrase", "recovery phrase", "private key",
        "airdrop", "free mint", "approve transaction", "claim tokens"
    ]

    if urls:
        score += 1
        reasons.append("This message contains a link.")

    for word in phishing_words:
        if word in low:
            score += 1
            reasons.append(f"It uses suspicious wording: “{word}”")

    for word in crypto_words:
        if word in low:
            score += 3
            reasons.append(f"It mentions a risky crypto phrase: “{word}”")

    if any(x in low for x in ["seed phrase", "private key", "recovery phrase"]):
        score += 8
        reasons.append("It mentions a seed phrase/private key. Never share those.")

    for url in urls:
        expanded = expand_url(url)
        original_domain = get_domain(url)
        expanded_domain = get_domain(expanded)

        expanded_results.append((url, expanded))

        if expanded != url:
            score += 2
            reasons.append(f"The link redirects to: {expanded_domain}")

        if any(s in original_domain for s in ["bit.ly", "tinyurl.com", "t.co", "cutt.ly", "ow.ly"]):
            score += 3
            reasons.append("It uses a shortened link, which can hide the real website.")

        if any(expanded_domain.endswith(tld) for tld in [".xyz", ".top", ".click", ".zip", ".tk", ".ml", ".ga"]):
            score += 2
            reasons.append(f"The website ending looks suspicious: {expanded_domain}")

    if score >= 12:
        risk = "CRITICAL"
    elif score >= 7:
        risk = "HIGH"
    elif score >= 3:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    return risk, reasons, expanded_results


def is_conversation_message(text):
    if not text:
        return False

    text = text.lower()

    conversation_words = [
        # General
        "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
        "thank", "thanks", "appreciate",

        # Help / emotion
        "help me", "i am scared", "i'm scared", "i lost money", "i got scammed", "scammed",

        # Questions
        "should i buy", "should i sell", "what should i do",
        "how do i start", "beginner", "explain",

        # Crypto basics
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
        "crypto", "blockchain", "wallet", "exchange",
        "coinbase", "kraken", "gemini", "binance",

        # Advanced
        "airdrop", "token", "defi", "nft", "gas fee", "staking",
        "market", "price", "bull market", "bear market",
        "stablecoin", "usdt", "usdc", "altcoin",

        # Safety
        "is crypto safe", "is bitcoin safe", "scam"
    ]

    return any(word in text for word in conversation_words)



async def conversational_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()
    name = get_user_name(update.effective_user.id)

    # =========================
    # CRYPTO KNOWLEDGE ENGINE
    # =========================

    if "bitcoin" in text or "btc" in text:
        reply = (
            "Bitcoin is the first cryptocurrency.\n\n"
            "It runs on a decentralized network called blockchain.\n"
            "No bank controls it.\n\n"
            "People use it as digital money and as a store of value."
        )

    elif "ethereum" in text or "eth" in text:
        reply = (
            "Ethereum is more than a coin.\n\n"
            "It allows smart contracts and apps (DeFi, NFTs).\n"
            "It powers a large part of the crypto ecosystem."
        )

    elif "what is crypto" in text or "crypto" in text:
        reply = (
            "Crypto is digital money secured by cryptography.\n\n"
            "It runs on blockchain instead of banks.\n"
            "Examples: Bitcoin, Ethereum, Solana."
        )

    elif "wallet" in text:
        reply = (
            "A crypto wallet stores your private keys.\n\n"
            "If someone gets your keys → they control your funds.\n\n"
            "Never share your seed phrase."
        )

    elif "scam" in text or "is this safe" in text:
        reply = (
            "Crypto scams are VERY common.\n\n"
            "Red flags:\n"
            "• Urgency\n"
            "• Free money promises\n"
            "• Asking for wallet connection or seed phrase\n\n"
            "Send it to me — I can scan it."
        )

    elif "should i buy" in text:
        reply = (
            "Buying depends on timing and risk.\n\n"
            "Never buy hype.\n"
            "Wait for confirmation and trend alignment.\n\n"
            "Use signals instead of emotions."
        )

    elif "should i sell" in text:
        reply = (
            "Selling depends on your strategy.\n\n"
            "Take profits gradually.\n"
            "Avoid panic selling.\n\n"
            "Let the market confirm your decision."
        )

    elif "how to start" in text or "beginner" in text:
        reply = (
            "Start crypto safely:\n\n"
            "1. Use Coinbase or Kraken\n"
            "2. Learn before investing big\n"
            "3. Never trust random links\n\n"
            "I can guide you step-by-step."
        )

    else:
        reply = (
            "I understand your question.\n\n"
            "Ask me anything about:\n"
            "• Crypto basics\n"
            "• Buying / selling\n"
            "• Scams\n"
            "• Wallets\n\n"
            "I’ll guide you safely."
        )

    await update.message.reply_text(reply, reply_markup=main_menu())
    

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    linked = get_linked_website_account(user_id)
    if linked:
        await update.message.reply_text(
            f"🚀 Welcome back, {account_display_name(linked)}.\n\n"
            f"Plan: {linked.get('plan') or linked.get('subscription_plan') or 'free'}\n"
            f"Subscription: {linked.get('subscription_status') or 'inactive'}\n\n"
            "Your CoinPilotX website account is connected to this Telegram profile.",
            reply_markup=main_menu()
        )
        return

    if not onboarding_complete(user_id):
        onboarding_state[user_id] = "name"
        await update.message.reply_text(
            f"🚀 Welcome to {BOT_NAME}, powered by CoinPilotXAI Inc.\n\nHow do you want me to call you?"
        )
        return

    await update.message.reply_text(
        f"🚀 Welcome back, {get_user_name(user_id)}.\n\nWhat would you like to do today?",
        reply_markup=main_menu()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(help_message(), reply_markup=main_menu())


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ About CoinPilotX\n\n"
        "CoinPilotX is a crypto safety and market intelligence assistant powered by CoinPilotXAI Inc. "
        "It is built to help everyday users understand crypto prices, alerts, portfolio movement, "
        "and scam risks in simple language.\n\n"
        "The system can check market prices, provide BUY / SELL / WAIT-style educational signals, "
        "help users track a manual crypto portfolio, and scan suspicious crypto messages or links "
        "before users trust them.\n\n"
        "CoinPilotX does not hold user funds, create exchange accounts, or guarantee profits. "
        "CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.\n\n"
        "Powered by CoinPilotXAI Inc.",
        reply_markup=main_menu()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ CoinPilotX Help\n\n"
        "/start — open menu\n"
        "/about — about CoinPilotX\n"
        "/price — BTC price\n"
        "/price ETH — ETH price\n"
        "/alerts_on — start alerts\n"
        "/alerts_off — stop alerts\n"
        "/track BTC ETH SOL — set watchlist\n"
        "/watchlist — show watchlist\n"
        "/addholding BTC 0.02 — manually track holdings\n"
        "/removeholding BTC 0.01 — remove tracked holdings\n"
        "/myportfolio — see tracked portfolio\n"
        "/paper — practice portfolio\n"
        "/signal BTC BUY 25 — practice trade\n"
        "/deposit — safe deposit guide\n"
        "/exchange beginner — exchange guide\n"
        "/users — user report\n\n"
        "If you send a regular message, I will either answer naturally or ask before scanning it.",
        reply_markup=main_menu()
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    asset = context.args[0].upper() if context.args else "BTC"

    price_now, sources = get_best_price(asset)

    if not price_now:
        await update.message.reply_text(f"I could not get the price for {asset} right now.")
        return

    set_last_price(user_id, asset, price_now)
    save_price_history(asset, price_now)
    signal = smart_market_signal(asset, price_now)

    rsi_text = f"{signal['rsi']:.2f}" if signal["rsi"] is not None else "Not enough data yet"

    msg = (
        f"📈 {asset} Price Right Now\n\n"
        f"{asset}: about ${price_now:,.2f}\n\n"
        f"Simple answer: {signal['action']}\n"
        f"Confidence: {signal['confidence']}\n\n"
        f"Why:\n{signal['reason']}\n\n"
        f"Trend: {signal['trend']}\n"
        f"RSI-style score: {rsi_text}\n"
        f"Volatility: {signal['volatility']}\n\n"
        "Prices checked:\n"
    )

    for source, p in sources.items():
        msg += f"• {source}: ${p:,.2f}\n"

    msg += "\nEducational only. Not financial advice."

    await update.message.reply_text(msg, reply_markup=main_menu())


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    if not context.args:
        await update.message.reply_text("Example: /track BTC ETH SOL")
        return

    assets = [a.upper() for a in context.args]
    set_watchlist(update.effective_user.id, assets)

    await update.message.reply_text(f"✅ I will watch: {', '.join(assets)}", reply_markup=main_menu())


async def show_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(
        f"👁️ I am watching: {', '.join(get_watchlist(update.effective_user.id))}",
        reply_markup=main_menu()
    )

    
async def alerts_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    set_alerts(update.effective_user.id, True)

    await update.message.reply_text(
        "🚨 Market alerts are ON.\n\n"
        "I will check every 60 seconds and send simple buy/sell hints when the market signal looks strong enough.",
        reply_markup=main_menu()
    )


async def alerts_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    set_alerts(update.effective_user.id, False)

    await update.message.reply_text("🔕 Market alerts are OFF.", reply_markup=main_menu())


async def addholding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    try:
        asset = context.args[0].upper()
        amount = float(context.args[1])
    except Exception:
        await update.message.reply_text("Example: /addholding BTC 0.02")
        return

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount FROM manual_portfolio WHERE user_id=? AND asset=?",
        (update.effective_user.id, asset)
    )
    row = cur.fetchone()
    current = row[0] if row else 0.0

    cur.execute(
        "INSERT OR REPLACE INTO manual_portfolio VALUES (?, ?, ?)",
        (update.effective_user.id, asset, current + amount)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Added {amount} {asset} to your tracked portfolio.",
        reply_markup=main_menu()
    )


async def removeholding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    try:
        asset = context.args[0].upper()
        amount = float(context.args[1])
    except Exception:
        await update.message.reply_text("Example: /removeholding BTC 0.01")
        return

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount FROM manual_portfolio WHERE user_id=? AND asset=?",
        (update.effective_user.id, asset)
    )
    row = cur.fetchone()
    current = row[0] if row else 0.0
    new_amount = max(0.0, current - amount)

    cur.execute(
        "INSERT OR REPLACE INTO manual_portfolio VALUES (?, ?, ?)",
        (update.effective_user.id, asset, new_amount)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Removed {amount} {asset} from your tracked portfolio.",
        reply_markup=main_menu()
    )


async def myportfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT asset, amount FROM manual_portfolio WHERE user_id=? AND amount > 0",
        (user_id,)
    )
    rows = cur.fetchall()
    conn.close()

    total = 0
    msg = "💼 Your Tracked Crypto Portfolio\n\n"

    if not rows:
        msg += "You have not added holdings yet.\nTry: /addholding BTC 0.02\n"
    else:
        for asset, amount in rows:
            price_now, _ = get_best_price(asset)
            value = amount * price_now if price_now else 0
            total += value
            msg += f"{asset}: {amount:.6f} ≈ ${value:,.2f}\n"

    msg += f"\nEstimated total value: ${total:,.2f}\n\nCoinPilotX tracks only. It does not hold your crypto."

    await update.message.reply_text(msg, reply_markup=main_menu())


async def paper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT asset, amount FROM paper_portfolio WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()

    total = 0
    msg = "📊 Practice Trading Portfolio\n\n"

    for asset, amount in rows:
        if asset == "USD":
            total += amount
            msg += f"Practice cash: ${amount:,.2f}\n"
        else:
            price_now, _ = get_best_price(asset)
            value = amount * price_now if price_now else 0
            total += value
            msg += f"{asset}: {amount:.6f} ≈ ${value:,.2f}\n"

    msg += f"\nTotal practice value: ${total:,.2f}"

    await update.message.reply_text(msg, reply_markup=main_menu())


async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💰 Add Money Safely\n\n"
        "CoinPilotX does NOT hold money or accept deposits.\n\n"
        "Use official platforms directly:\n\n"
        "Coinbase: https://www.coinbase.com\n"
        "Kraken: https://www.kraken.com\n"
        "Gemini: https://www.gemini.com\n"
        "Crypto.com: https://crypto.com\n\n"
        "Never send money to anyone claiming to be CoinPilotX.",
        reply_markup=main_menu()
    )


async def exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal = context.args[0].lower() if context.args else ""

    if not goal:
        await update.message.reply_text("What matters most to you?", reply_markup=exchange_menu())
        return

    await send_exchange_message(update.message, goal)


async def send_exchange_message(message, goal):
    if goal == "beginner":
        msg = (
            "🏦 Best for beginners: Coinbase\n\n"
            "Official website:\nhttps://www.coinbase.com\n\n"
            "Also compare:\nKraken: https://www.kraken.com\nGemini: https://www.gemini.com"
        )
    elif goal == "lowfees":
        msg = (
            "💸 Best for lower fees: Kraken\n\n"
            "Official website:\nhttps://www.kraken.com\n\n"
            "Also compare:\nCoinbase Advanced: https://www.coinbase.com/advanced-trade"
        )
    elif goal == "security":
        msg = (
            "🔐 Best for security focus: Gemini\n\n"
            "Official website:\nhttps://www.gemini.com\n\n"
            "Also compare:\nCoinbase: https://www.coinbase.com\nKraken: https://www.kraken.com"
        )
    elif goal == "mobile":
        msg = (
            "📱 Best mobile app choice: Crypto.com\n\n"
            "Official website:\nhttps://crypto.com\n\n"
            "Also compare:\nCoinbase: https://www.coinbase.com"
        )
    else:
        msg = "Choose beginner, lowfees, security, or mobile."

    msg += "\n\nCoinPilotX does not create accounts or hold funds."

    await message.reply_text(msg, reply_markup=main_menu())

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    try:
        asset = context.args[0].upper()
        action = context.args[1].upper()
        amount = float(context.args[2])
    except Exception:
        await update.message.reply_text("Example: /signal BTC BUY 25")
        return

    if action not in ["BUY", "SELL"]:
        await update.message.reply_text("Use BUY or SELL.")
        return

    await update.message.reply_text(
        f"📊 Practice signal created\n\n{action} ${amount} of {asset}",
        reply_markup=main_menu()
    )


async def market_signal_job(context: ContextTypes.DEFAULT_TYPE):
    users = get_users_with_alerts()

    for user_id in users:
        assets = get_watchlist(user_id)

        for asset in assets:
            price_now, _ = get_best_price(asset)

            if not price_now:
                continue

            save_price_history(asset, price_now)

            is_pro_user = is_pro(user_id)
            signal_data = smart_market_signal(asset, price_now, is_pro_user)

            if signal_data["action"] in ["BUY", "SELL"] and signal_data["confidence"] in ["Medium", "High"]:

                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🚨 {asset} Smart Alert\n\n"
                        f"Action: {signal_data['action']}\n"
                        f"Confidence: {signal_data['confidence']}\n\n"
                        f"Why:\n{signal_data['reason']}\n\n"
                        f"Trend: {signal_data['trend']}\n"
                        f"Volatility: {signal_data['volatility']}\n\n"
                        + ("⭐ Pro Analysis Enabled\n" if is_pro_user else "🔓 Upgrade for better signals\n")
                        + "Educational only. Not financial advice."
                    )
                )


async def hourly_market_update(context: ContextTypes.DEFAULT_TYPE):
    users = get_users_with_alerts()

    for user_id in users:
        msg = "📡 Hourly Market Update\n\n"

        for asset in get_watchlist(user_id):
            price_now, _ = get_best_price(asset)

            if price_now:
                msg += f"{asset}: ${price_now:,.2f}\n"

        await context.bot.send_message(chat_id=user_id, text=msg)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    original_text = update.message.text.strip()
    text = original_text.lower()

    # =========================
    # AUTO SCAM DETECTION
    # =========================
    if any(x in text for x in ["http", "www", ".com", "airdrop", "claim", "wallet"]):
        context.user_data["pending_scan"] = text
        await update.message.reply_text(
            "⚠️ This looks like a crypto-related message.\n\nDo you want me to scan it?",
            reply_markup=scan_confirm_menu()
        )
        return

    # =========================
    # NORMAL CONVERSATION
    # =========================
    if is_conversation_message(text):
        await update.message.reply_text(openai_chat_completion(update.effective_user.id, original_text), reply_markup=main_menu())
        return

    await update.message.reply_text(openai_chat_completion(update.effective_user.id, original_text), reply_markup=main_menu())

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    try:
        txid = context.args[0].strip()
    except Exception:
        await update.message.reply_text(
            "Usage:\n/verify_payment YOUR_TXID\n\n"
            "CoinPilotX will verify the transaction before activating Pro.\n"
            "CoinPilotX will never ask for your seed phrase or private key.",
            reply_markup=main_menu()
        )
        return

    if not is_valid_btc_txid(txid):
        save_payment_verification(update.effective_user.id, txid, "btc", None, "invalid_txid", "TXID format failed validation.")
        await update.message.reply_text(
            "⚠️ That does not look like a valid BTC TXID.\n\n"
            "A BTC transaction ID is usually 64 hexadecimal characters.\n\n"
            "Please check it and try:\n/verify_payment YOUR_TXID\n\n"
            "CoinPilotX will never ask for your seed phrase or private key.",
            reply_markup=main_menu()
        )
        return

    existing_user = payment_txid_already_verified(txid)
    if existing_user:
        save_payment_verification(update.effective_user.id, txid, "btc", None, "duplicate_txid", f"Already verified for user {existing_user}.")
        await update.message.reply_text(
            "⚠️ This TXID has already been used for a verified CoinPilotX Pro activation.\n\n"
            "If you believe this is a mistake, contact support with your payment details. Pro will not be activated from a reused TXID.",
            reply_markup=main_menu()
        )
        return

    is_paid, paid_sats, confirmations = verify_btc_tx(txid)
    paid_btc = paid_sats / 100_000_000

    if is_paid:
        timestamp = datetime.now().isoformat()
        activate_pro(update.effective_user.id, payment_type="btc")
        save_payment_verification(update.effective_user.id, txid, "btc", paid_btc, "verified", f"Confirmations: {confirmations}")
        send_subscription_email(update.effective_user.id, "btc", txid=txid, timestamp=timestamp)
        await update.message.reply_text(
            "✅ Payment verified successfully.\n\n"
            "Your CoinPilotX Pro access is now active.\n\n"
            f"TXID: {txid}\n"
            f"Detected amount: {paid_btc:.8f} BTC\n"
            f"Confirmations: {confirmations}\n\n"
            "Educational only — not financial advice.\n"
            "CoinPilotX will never ask for your seed phrase or private key.",
            reply_markup=main_menu()
        )
    else:
        status = "pending_or_wrong_amount" if paid_sats else "not_found_or_unpaid"
        save_payment_verification(update.effective_user.id, txid, "btc", paid_btc, status, f"Confirmations: {confirmations}")
        await update.message.reply_text(
            "⏳ Payment not verified yet.\n\n"
            "Pro has not been activated because the transaction could not be confirmed for the expected BTC amount yet.\n\n"
            f"Detected: {paid_btc:.8f} BTC\n"
            f"Confirmations: {confirmations}\n\n"
            "Make sure you sent the correct BTC amount to the correct address, then try again later.\n\n"
            "CoinPilotX will never ask for your seed phrase or private key.",
            reply_markup=main_menu()
        )



    # =========================
    # PRO FEATURES
    # =========================

# =========================
# MAJOR UPGRADE LAYER
# =========================

SUPPORTED_ASSETS = ["BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "AVAX", "LINK", "MATIC"]
CHART_ASSETS = ["BTC", "ETH"]
EXCHANGE_PROFILES = {
    "Coinbase": {
        "best_for": ["beginner", "mobile", "regulated", "simple"],
        "fees": "Medium",
        "security": "High",
        "url": "https://www.coinbase.com",
        "note": "Best fit when ease of use and US availability matter most.",
    },
    "Kraken": {
        "best_for": ["lowfees", "security", "advanced", "staking"],
        "fees": "Low",
        "security": "High",
        "url": "https://www.kraken.com",
        "note": "Strong choice for lower fees, security focus, and active traders.",
    },
    "Gemini": {
        "best_for": ["security", "regulated", "beginner"],
        "fees": "Medium",
        "security": "High",
        "url": "https://www.gemini.com",
        "note": "Good fit for users who prioritize compliance and custody controls.",
    },
    "Crypto.com": {
        "best_for": ["mobile", "card", "rewards"],
        "fees": "Medium",
        "security": "Medium",
        "url": "https://crypto.com",
        "note": "Useful mobile-first option with broad consumer features.",
    },
}
NEWS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
]
BLOCKSTREAM_BASE_URL = os.getenv("BLOCKSTREAM_BASE_URL", "https://blockstream.info/api").rstrip("/")
MEMPOOL_BASE_URL = os.getenv("MEMPOOL_BASE_URL", "https://mempool.space/api").rstrip("/")
BTC_WHALE_THRESHOLD_BTC = float(os.getenv("BTC_WHALE_THRESHOLD_BTC", "500"))
BTC_DORMANT_DAYS = int(os.getenv("BTC_DORMANT_DAYS", "3650"))
SPORTS_EDGE_ENABLED = os.getenv("SPORTS_EDGE_ENABLED", "false").lower() == "true"
COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "uae": "United Arab Emirates",
}
COUNTRY_PROFILES = {
    "United States": {"flag": "🇺🇸", "region": "North America", "adoption": "High institutional and retail adoption", "regulation": "Active federal and state oversight", "exchanges": "Coinbase, Kraken, Gemini, Crypto.com", "remittance": "Lower than emerging markets", "institutional": "Very high ETF, custody, and public-company activity", "retail": "Active but sensitive to rates, regulation, and ETF flows", "mining": "Major Bitcoin mining market"},
    "Canada": {"flag": "🇨🇦", "region": "North America", "adoption": "Moderate to high adoption", "regulation": "Regulated exchange and ETF environment", "exchanges": "Kraken, Coinbase, local regulated platforms", "remittance": "Moderate", "institutional": "Active ETF and custody market", "retail": "Generally cautious and compliance-aware", "mining": "Relevant mining activity in energy-rich provinces"},
    "Mexico": {"flag": "🇲🇽", "region": "Latin America", "adoption": "Growing adoption around remittances and savings", "regulation": "Developing fintech and crypto oversight", "exchanges": "Bitso, Coinbase access varies, global platforms", "remittance": "High relevance", "institutional": "Emerging", "retail": "Interested in cross-border payments", "mining": "Limited"},
    "Brazil": {"flag": "🇧🇷", "region": "Latin America", "adoption": "High and growing", "regulation": "Developing crypto asset framework", "exchanges": "Mercado Bitcoin, Binance availability varies, Coinbase", "remittance": "Moderate", "institutional": "Growing bank and fintech activity", "retail": "Strong retail interest", "mining": "Limited"},
    "Argentina": {"flag": "🇦🇷", "region": "Latin America", "adoption": "High due to inflation and dollar access concerns", "regulation": "Evolving", "exchanges": "Lemon, Ripio, Buenbit, global options vary", "remittance": "Meaningful", "institutional": "Emerging", "retail": "Strong stablecoin and BTC interest", "mining": "Limited"},
    "United Kingdom": {"flag": "🇬🇧", "region": "Europe", "adoption": "Moderate to high", "regulation": "Strict financial promotions and FCA oversight", "exchanges": "Coinbase, Kraken, Gemini, Crypto.com", "remittance": "Moderate", "institutional": "Active fintech and custody market", "retail": "Risk-aware under strict marketing rules", "mining": "Limited"},
    "France": {"flag": "🇫🇷", "region": "Europe", "adoption": "Moderate", "regulation": "EU MiCA-aligned oversight", "exchanges": "Coinbase, Kraken, Binance availability varies", "remittance": "Low to moderate", "institutional": "Growing under EU rules", "retail": "Compliance-focused", "mining": "Limited"},
    "Germany": {"flag": "🇩🇪", "region": "Europe", "adoption": "Moderate to high", "regulation": "Strict custody and BaFin oversight", "exchanges": "Coinbase, Kraken, Bitpanda access", "remittance": "Low to moderate", "institutional": "Strong custody and banking interest", "retail": "Long-term and compliance-oriented", "mining": "Limited"},
    "United Arab Emirates": {"flag": "🇦🇪", "region": "Middle East", "adoption": "High and rapidly growing", "regulation": "Dedicated virtual asset regimes in Dubai/Abu Dhabi", "exchanges": "Regional and global exchanges where licensed", "remittance": "High relevance for expatriate flows", "institutional": "Very active crypto hub development", "retail": "Strong interest", "mining": "Limited"},
    "Saudi Arabia": {"flag": "🇸🇦", "region": "Middle East", "adoption": "Growing retail interest", "regulation": "Cautious and developing", "exchanges": "Access varies; users should verify local rules", "remittance": "Relevant", "institutional": "Selective blockchain/Web3 interest", "retail": "Curious but compliance-sensitive", "mining": "Limited"},
    "Nigeria": {"flag": "🇳🇬", "region": "Africa", "adoption": "Very high grassroots adoption", "regulation": "Evolving banking and exchange guidance", "exchanges": "Local and global access varies", "remittance": "Very high relevance", "institutional": "Growing fintech activity", "retail": "Strong stablecoin, remittance, and savings use", "mining": "Limited"},
    "South Africa": {"flag": "🇿🇦", "region": "Africa", "adoption": "Moderate to high", "regulation": "Crypto asset service provider framework", "exchanges": "Luno, VALR, global access varies", "remittance": "Moderate", "institutional": "Growing regulated market", "retail": "Active but risk-aware", "mining": "Limited"},
    "Kenya": {"flag": "🇰🇪", "region": "Africa", "adoption": "Growing peer-to-peer and mobile-money-adjacent interest", "regulation": "Developing", "exchanges": "Local/global access varies", "remittance": "High relevance", "institutional": "Emerging", "retail": "Practical payment and savings interest", "mining": "Limited"},
    "India": {"flag": "🇮🇳", "region": "Asia", "adoption": "Very large retail user base", "regulation": "Tax-heavy and evolving", "exchanges": "WazirX, CoinDCX, CoinSwitch, global access varies", "remittance": "Meaningful", "institutional": "Growing but cautious", "retail": "High interest despite tax friction", "mining": "Limited"},
    "China": {"flag": "🇨🇳", "region": "Asia", "adoption": "Restricted mainland trading environment", "regulation": "Very restrictive toward crypto trading/mining", "exchanges": "Mainland access is heavily restricted", "remittance": "Limited via regulated channels", "institutional": "Blockchain interest separate from public crypto trading", "retail": "Restricted", "mining": "Historically major, now restricted domestically"},
    "Japan": {"flag": "🇯🇵", "region": "Asia", "adoption": "Moderate and regulated", "regulation": "Mature exchange licensing and consumer protection", "exchanges": "bitFlyer, Coincheck, licensed platforms", "remittance": "Low to moderate", "institutional": "Active enterprise and gaming/Web3 interest", "retail": "Conservative but steady", "mining": "Limited"},
    "South Korea": {"flag": "🇰🇷", "region": "Asia", "adoption": "High retail trading activity", "regulation": "Strict exchange and identity rules", "exchanges": "Upbit, Bithumb, local licensed exchanges", "remittance": "Low to moderate", "institutional": "Growing under regulation", "retail": "Very active and sentiment-driven", "mining": "Limited"},
    "Singapore": {"flag": "🇸🇬", "region": "Asia", "adoption": "High institutional hub activity", "regulation": "Strict licensing and consumer-risk controls", "exchanges": "Licensed global/regional platforms", "remittance": "Moderate", "institutional": "Very high", "retail": "Cautious under strong risk warnings", "mining": "Limited"},
    "Australia": {"flag": "🇦🇺", "region": "Oceania", "adoption": "Moderate to high", "regulation": "Developing licensing and tax rules", "exchanges": "CoinSpot, Swyftx, Kraken, Coinbase", "remittance": "Moderate", "institutional": "Growing ETF/custody interest", "retail": "Active but cautious", "mining": "Some renewable-energy-linked interest"},
    "Haiti": {"flag": "🇭🇹", "region": "Caribbean", "adoption": "Early-stage and education-driven", "regulation": "Limited formal crypto framework", "exchanges": "Access may depend on global platforms, payment rails, and local banking availability", "remittance": "High potential relevance because remittances matter deeply", "institutional": "Limited", "retail": "Interest can center on remittances, savings, and mobile access", "mining": "Limited"},
    "El Salvador": {"flag": "🇸🇻", "region": "Latin America", "adoption": "High policy visibility because Bitcoin is legal tender", "regulation": "Bitcoin-forward national policy", "exchanges": "Local and global options vary", "remittance": "High relevance", "institutional": "High sovereign Bitcoin visibility", "retail": "Mixed practical adoption", "mining": "Volcano/geothermal mining narrative is relevant"},
}
COUNTRY_PICKER_COUNTRIES = [
    ("United States", "United States"),
    ("Canada", "Canada"),
    ("Mexico", "Mexico"),
    ("Brazil", "Brazil"),
    ("Argentina", "Argentina"),
    ("United Kingdom", "United Kingdom"),
    ("France", "France"),
    ("Germany", "Germany"),
    ("UAE", "UAE"),
    ("Saudi Arabia", "Saudi Arabia"),
    ("Nigeria", "Nigeria"),
    ("South Africa", "South Africa"),
    ("Kenya", "Kenya"),
    ("India", "India"),
    ("China", "China"),
    ("Japan", "Japan"),
    ("South Korea", "South Korea"),
    ("Singapore", "Singapore"),
    ("Australia", "Australia"),
    ("Haiti", "Haiti"),
    ("El Salvador", "El Salvador"),
]
WISDOM_MESSAGES = [
    "Patience is a position. You do not have to trade every candle.",
    "Risk management beats prediction. Protect the downside first.",
    "Never let urgency make decisions for you. Scammers love speed.",
    "Cycles reward preparation, not panic.",
    "If you cannot explain the trade, reduce the size.",
    "Your seed phrase is not support information. It is control of your funds.",
    "A missed pump is cheaper than a forced mistake.",
]
SCAM_STORY_LIBRARY = [
    {
        "title": "The fake airdrop that emptied a wallet",
        "story": "Someone sees a token claim link that looks official. The page asks them to connect their wallet and approve access. Minutes later, valuable tokens are gone.",
        "trap": "The scam uses urgency, a professional-looking website, and a wallet approval that quietly gives the attacker permission to move assets.",
        "red_flags": ["Free token with a countdown", "Link shared by a random account", "Wallet asks for broad approval", "Website domain is slightly different from the real project"],
        "avoid": ["Verify links from official project channels", "Use a burner wallet for claims", "Reject unlimited approvals", "Revoke old permissions often"],
        "final": "A wallet approval can be as dangerous as sending funds. Slow down before every signature.",
    },
    {
        "title": "The fake exchange support takeover",
        "story": "A user complains online about a delayed withdrawal. A fake support profile replies quickly and sends a private link to 'verify the account.'",
        "trap": "The attacker copies branding, creates pressure, and asks for login details, seed phrases, or remote screen access.",
        "red_flags": ["Support starts in DMs", "They ask for seed phrases or 2FA codes", "They rush the user", "The link does not match the exchange domain"],
        "avoid": ["Use support only inside the official app or website", "Never share seed phrases or codes", "Do not screen-share wallets", "Lock the account if access feels compromised"],
        "final": "Real support does not need control of your wallet. Anyone asking for it is a danger sign.",
    },
    {
        "title": "The fake mentor profit dashboard",
        "story": "A friendly stranger spends weeks building trust, then introduces a trading platform showing steady fake profits. Withdrawals suddenly require taxes, fees, or more deposits.",
        "trap": "The victim is shown fake gains to make the next deposit feel rational. The money was never really trading.",
        "red_flags": ["Claims of certain returns", "A stranger chooses the platform", "Small first withdrawal followed by bigger pressure", "Extra fee required before withdrawal"],
        "avoid": ["Never use platforms introduced by strangers", "Verify licensing and domain history", "Test withdrawals early", "Do not pay fees to unlock alleged profits"],
        "final": "If profits only exist on a website you cannot independently verify, treat them as unreal.",
    },
]


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        display_name TEXT,
        email TEXT,
        signup_time TEXT,
        onboarding_complete INTEGER DEFAULT 0,
        alerts_enabled INTEGER DEFAULT 0,
        is_pro INTEGER DEFAULT 0,
        subscription_plan TEXT DEFAULT 'free',
        subscription_status TEXT DEFAULT 'inactive',
        subscription_started_at TEXT,
        subscription_expires_at TEXT,
        risk_profile TEXT DEFAULT 'balanced',
        preferred_exchange_goal TEXT DEFAULT 'beginner'
    )
    """)

    for statement in [
        "ALTER TABLE users ADD COLUMN is_pro INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN subscription_plan TEXT DEFAULT 'free'",
        "ALTER TABLE users ADD COLUMN subscription_status TEXT DEFAULT 'inactive'",
        "ALTER TABLE users ADD COLUMN subscription_started_at TEXT",
        "ALTER TABLE users ADD COLUMN subscription_expires_at TEXT",
        "ALTER TABLE users ADD COLUMN risk_profile TEXT DEFAULT 'balanced'",
        "ALTER TABLE users ADD COLUMN preferred_exchange_goal TEXT DEFAULT 'beginner'",
        "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT",
        "ALTER TABLE users ADD COLUMN stripe_session_id TEXT",
        "ALTER TABLE users ADD COLUMN last_payment_type TEXT",
        "ALTER TABLE users ADD COLUMN full_name TEXT",
        "ALTER TABLE users ADD COLUMN password_hash TEXT",
        "ALTER TABLE users ADD COLUMN phone TEXT",
        "ALTER TABLE users ADD COLUMN country TEXT",
        "ALTER TABLE users ADD COLUMN telegram_user_id INTEGER",
        "ALTER TABLE users ADD COLUMN telegram_username TEXT",
        "ALTER TABLE users ADD COLUMN telegram_chat_id INTEGER",
        "ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN email_opt_in INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN sms_opt_in INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'",
        "ALTER TABLE users ADD COLUMN created_at TEXT",
        "ALTER TABLE users ADD COLUMN updated_at TEXT",
        "ALTER TABLE users ADD COLUMN last_login_at TEXT",
        "ALTER TABLE users ADD COLUMN last_seen_at TEXT",
    ]:
        try:
            cur.execute(statement)
        except Exception:
            pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS watchlists (
        user_id INTEGER,
        asset TEXT,
        PRIMARY KEY (user_id, asset)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS manual_portfolio (
        user_id INTEGER,
        asset TEXT,
        amount REAL,
        PRIMARY KEY (user_id, asset)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_portfolio (
        user_id INTEGER,
        asset TEXT,
        amount REAL,
        PRIMARY KEY (user_id, asset)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS last_prices (
        user_id INTEGER,
        asset TEXT,
        price REAL,
        PRIMARY KEY (user_id, asset)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alerts_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        asset TEXT,
        action TEXT,
        price REAL,
        change_pct REAL,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS last_signals (
        user_id INTEGER,
        asset TEXT,
        action TEXT,
        confidence TEXT,
        created_at TEXT,
        PRIMARY KEY (user_id, asset)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset TEXT,
        price REAL,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        total_value REAL,
        holdings_json TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS whale_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset TEXT,
        side TEXT,
        notional_usd REAL,
        price REAL,
        source TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        asset TEXT,
        summary TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        message TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS crypto_news_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        country TEXT,
        title TEXT,
        summary TEXT,
        sentiment TEXT,
        source TEXT,
        url TEXT,
        published_at TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS engagement_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        feature TEXT,
        query TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_portfolio_settings (
        user_id INTEGER PRIMARY KEY,
        manual_total_usd REAL,
        manual_override_enabled INTEGER DEFAULT 0
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_advice_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT,
        action TEXT,
        current_value REAL,
        upside_estimate REAL,
        downside_estimate REAL,
        explanation TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS whale_intelligence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        txid TEXT,
        wallet TEXT,
        amount_btc REAL,
        usd_value REAL,
        movement_type TEXT,
        sentiment TEXT,
        explanation TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transaction_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        query TEXT,
        txid TEXT,
        address TEXT,
        amount_btc REAL,
        fee_btc REAL,
        confirmations INTEGER,
        risk_level TEXT,
        explanation TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS connected_wallets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        address TEXT,
        label TEXT,
        chain TEXT DEFAULT 'BTC',
        created_at TEXT,
        last_checked_at TEXT,
        UNIQUE(user_id, address)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        message TEXT,
        response TEXT,
        is_pro INTEGER,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        txid TEXT,
        payment_type TEXT,
        amount REAL,
        status TEXT,
        details TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        subject TEXT,
        status TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT,
        email TEXT UNIQUE,
        phone TEXT,
        country TEXT,
        source TEXT,
        utm_source TEXT,
        utm_medium TEXT,
        utm_campaign TEXT,
        email_opt_in INTEGER DEFAULT 0,
        sms_opt_in INTEGER DEFAULT 0,
        telegram_username TEXT,
        created_at TEXT,
        updated_at TEXT,
        last_seen_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        user_id INTEGER,
        event_name TEXT,
        page_url TEXT,
        referrer TEXT,
        device_type TEXT,
        browser TEXT,
        ip_hash TEXT,
        country TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT UNIQUE,
        first_seen_at TEXT,
        last_seen_at TEXT,
        user_agent TEXT,
        referrer TEXT,
        landing_page TEXT,
        utm_source TEXT,
        utm_medium TEXT,
        utm_campaign TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS telegram_link_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        code TEXT UNIQUE,
        expires_at TEXT,
        used_at TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        token TEXT UNIQUE,
        expires_at TEXT,
        used_at TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_verification_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        token TEXT UNIQUE,
        expires_at TEXT,
        used_at TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()


def help_message():
    return (
        "ℹ️ CoinPilotX Help\n"
        "Powered by CoinPilotXAI Inc.\n\n"
        "/price BTC — live price and signal\n"
        "/chart BTC — real BTC/ETH live chart\n"
        "/analysis BTC — AI-style crypto analysis\n"
        "/markets — live top market cap board\n"
        "/topvolume — top crypto assets by 24h volume\n"
        "/gainers — strongest 24h movers\n"
        "/losers — weakest 24h movers\n"
        "/daysignal — CoinPilotX Day Signal readiness check\n"
        "/signals — auto signal engine summary\n"
        "/feargreed — market fear/greed read\n"
        "/whales — latest whale-style activity\n"
        "/exchange beginner — smarter exchange recommendation\n"
        "/portfolio_live — real-time portfolio value\n"
        "/setbalance 1250 — manually override displayed portfolio balance\n"
        "/clearbalance — return to live holdings balance\n"
        "/portfolio_advice — portfolio decision intelligence\n"
        "/whalebtc — BTC whale intelligence\n"
        "/whalealerts — latest saved whale alerts\n"
        "/btcstats — Bitcoin network statistics\n"
        "/network — network health\n"
        "/fees — miner fee estimates\n"
        "/mempool — mempool congestion\n"
        "/checktx TXID — public transaction explorer\n"
        "/walletinfo ADDRESS — public wallet intelligence\n"
        "/connectwallet ADDRESS — track a public wallet address\n"
        "/walletscan ADDRESS — scam-risk wallet scan\n"
        "/chainintel — blockchain sentiment read\n"
        "/marketpressure — whale and exchange-flow pressure\n"
        "/mining — mining and network strength\n"
        "/difficulty — Bitcoin difficulty read\n"
        "/networkhealth — chain health summary\n"
        "/cryptonews — recent crypto news with market read\n"
        "/marketevents — global event impact on BTC/ETH\n"
        "/wisdom — daily risk-management wisdom\n"
        "/scamstories — recent scam examples and prevention\n"
        "/countrynews Haiti — country crypto intelligence\n"
        "/sportsedge — live sports edge board\n"
        "/livegames — live game list and risk labels\n"
        "/gameedge GAME_ID — deeper game intelligence\n"
        "/subscribe — Pro subscription options\n"
        "/setemail you@example.com — save email for payment confirmations\n"
        "/myemail — show the email saved for confirmations\n"
        "/verify_payment TXID — verify BTC payment and activate Pro if confirmed\n"
        "/account — account and subscription status\n"
        "/admin — admin dashboard summary\n\n"
        "Portfolio: /addholding BTC 0.02, /setbalance 1250, /portfolio_advice\n"
        "Alerts: /alerts_on, /alerts_off, /track BTC ETH SOL"
    )


def normalize_asset(asset):
    asset = (asset or "BTC").upper().strip()
    return asset if asset in SUPPORTED_ASSETS else asset[:12]


def binance_symbol(asset):
    return f"{normalize_asset(asset)}USDT"


def safe_get_json(url, timeout=8, params=None):
    try:
        response = requests.get(url, timeout=timeout, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logging.info("API request failed: %s", exc)
        return None


def pro_suffix(user_id):
    if is_pro(user_id):
        return "Pro"
    return "Free"


def append_plan_footer(user_id, message):
    return append_ethical_upgrade_footer(user_id, message)


def append_ethical_upgrade_footer(user_id, message, context_type=None):
    if not message:
        return message
    tiny_contexts = {"system", "cancel", "example", "menu"}
    if context_type in tiny_contexts:
        return message
    footer = (
        "⭐ Pro active — you’re receiving deeper CoinPilotX intelligence."
        if is_pro(user_id)
        else "⭐ Want deeper analysis, whale intelligence, portfolio decision support, and advanced scam protection? Upgrade to CoinPilotX Pro."
    )
    if footer in message:
        return message
    # Ethical conversion: transparent, user-controlled CTA for substantial intelligence responses.
    return f"{message.rstrip()}\n\n{footer}"


def append_upgrade_cta(user_id, text):
    return append_ethical_upgrade_footer(user_id, text)


def maybe_limit_for_free(user_id, text, max_chars=900):
    if is_pro(user_id) or len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rsplit("\n", 1)[0].strip()
    if not trimmed:
        trimmed = text[:max_chars].strip()
    return trimmed + "\n\nFree view: shortened summary."


def format_free_vs_pro_response(user_id, free_text, pro_text=None):
    text = pro_text if is_pro(user_id) and pro_text else free_text
    return append_plan_footer(user_id, maybe_limit_for_free(user_id, text))


def openai_chat_completion(user_id, question):
    api_key = os.getenv("OPENAI_API_KEY")
    pro = is_pro(user_id)
    if not api_key:
        fallback = (
            "💬 AI Crypto Assistant\n\n"
            "OpenAI chat is not connected yet. Add OPENAI_API_KEY in Railway Variables to enable full AI answers.\n\n"
            "I can still help with built-in CoinPilotX commands like /analysis BTC, /portfolio_advice, /walletscan, and /scamstories.\n\n"
            "Educational only — not financial advice."
        )
        return append_plan_footer(user_id, fallback)

    system_prompt = (
        "You are CoinPilotX, a premium crypto intelligence assistant powered by CoinPilotXAI Inc. It may use OpenAI technology for AI responses. "
        "Act as a cautious crypto analyst, scam protection advisor, blockchain educator, portfolio coach, and market explainer. "
        "Never guarantee profits, never claim certainty, never ask for seed phrases/private keys/recovery phrases/wallet passwords, "
        "and do not impersonate a licensed financial advisor. If a user mentions suspicious links, seed phrases, private keys, "
        "wallet recovery, wallet passwords, approvals, or transaction signing, prioritize safety and clearly say CoinPilotX will never "
        "ask for your seed phrase, private key, or wallet password. Keep answers concise and safety-focused. "
    )
    if pro:
        system_prompt += (
            "The user is Pro. Use structured sections: Market Context, Risk Factors, Opportunity Factors, "
            "Scam/Safety Watch, Suggested Next Step, What Could Change This View. Include confidence and risk language when relevant."
        )
        max_tokens = 850
    else:
        system_prompt += (
            "The user is Free. Give a short, simple answer with limited market context. Mention Pro only briefly for deeper analysis."
        )
        max_tokens = 260

    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.35,
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=25,
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logging.info("OpenAI chat failed: %s", exc)
        answer = (
            "💬 AI Crypto Assistant\n\n"
            "The AI assistant is temporarily unavailable. Try again in a moment, or use /analysis BTC, /walletscan, or /portfolio_advice.\n\n"
            "Educational only — not financial advice."
        )

    if "Educational only" not in answer and any(word in question.lower() for word in ["buy", "sell", "market", "price", "portfolio", "btc", "eth", "crypto", "invest"]):
        answer += "\n\nEducational only — not financial advice."

    answer += "\n\nPowered by CoinPilotXAI Inc."

    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_history (user_id, role, message, response, is_pro, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "user", question, answer, 1 if pro else 0, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("AI chat history save failed: %s", exc)

    return append_plan_footer(user_id, answer)


def safe_get_text(url, timeout=8):
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.text.strip()
    except Exception as exc:
        logging.info("Text API request failed: %s", exc)
        return None


def blockstream_json(path):
    return safe_get_json(f"{BLOCKSTREAM_BASE_URL}{path}", timeout=10)


def blockstream_text(path):
    return safe_get_text(f"{BLOCKSTREAM_BASE_URL}{path}", timeout=10)


def mempool_json(path):
    return safe_get_json(f"{MEMPOOL_BASE_URL}{path}", timeout=10)


def btc_sats_to_btc(value):
    return (value or 0) / 100_000_000


def is_btc_txid(value):
    return bool(re.fullmatch(r"[A-Fa-f0-9]{64}", value or ""))


def is_btc_address(value):
    return bool(re.fullmatch(r"(bc1|[13])[A-Za-z0-9]{25,90}", value or ""))


def btc_price_usd():
    price, _ = get_best_price("BTC")
    return price or 0


def exchange_address_labels():
    raw = os.getenv("EXCHANGE_BTC_ADDRESSES_JSON", "{}")
    try:
        labels = json.loads(raw)
        if isinstance(labels, dict):
            return labels
    except Exception:
        pass
    return {}


def label_for_address(address):
    return exchange_address_labels().get(address, "")


def btc_tip_height():
    text = blockstream_text("/blocks/tip/height")
    try:
        return int(text)
    except Exception:
        return None


def btc_tip_hash():
    return blockstream_text("/blocks/tip/hash")


def tx_confirmations(tx, tip_height=None):
    status = tx.get("status", {}) if tx else {}
    if not status.get("confirmed"):
        return 0
    block_height = status.get("block_height")
    tip_height = tip_height or btc_tip_height()
    if not block_height or not tip_height:
        return 1
    return max(1, tip_height - block_height + 1)


def network_snapshot():
    fees = mempool_json("/v1/fees/recommended") or {}
    mempool = mempool_json("/mempool") or {}
    difficulty = mempool_json("/v1/difficulty-adjustment") or {}
    height = btc_tip_height()
    tx_count_text = safe_get_text("https://blockchain.info/q/24hrtransactioncount")
    hashrate_text = safe_get_text("https://blockchain.info/q/hashrate")

    try:
        tx_count = int(float(tx_count_text))
    except Exception:
        tx_count = None
    try:
        hashrate = float(hashrate_text)
    except Exception:
        hashrate = None

    mempool_count = mempool.get("count", 0) or 0
    vsize = mempool.get("vsize", 0) or 0
    fastest_fee = fees.get("fastestFee")
    half_hour_fee = fees.get("halfHourFee")
    hour_fee = fees.get("hourFee")

    if vsize > 80_000_000 or (fastest_fee and fastest_fee > 80):
        congestion = "High"
        health = "Congested"
    elif vsize > 25_000_000 or (fastest_fee and fastest_fee > 30):
        congestion = "Medium"
        health = "Busy but functional"
    else:
        congestion = "Low"
        health = "Healthy"

    speed = "Fast" if congestion == "Low" else "Moderate" if congestion == "Medium" else "Slow"
    return {
        "height": height,
        "tx_count_24h": tx_count,
        "hashrate": hashrate,
        "fees": fees,
        "mempool": mempool,
        "difficulty": difficulty,
        "congestion": congestion,
        "health": health,
        "speed": speed,
        "fastest_fee": fastest_fee,
        "half_hour_fee": half_hour_fee,
        "hour_fee": hour_fee,
        "mempool_count": mempool_count,
        "vsize": vsize,
    }


def network_stats_summary(topic="network"):
    snap = network_snapshot()
    fee_line = (
        f"Fees: fastest {snap['fastest_fee']} sat/vB, 30 min {snap['half_hour_fee']} sat/vB, 60 min {snap['hour_fee']} sat/vB"
        if snap["fastest_fee"] is not None else
        "Fees: unavailable right now"
    )
    tx_line = f"24h transactions: {snap['tx_count_24h']:,}" if snap["tx_count_24h"] else "24h transactions: unavailable"
    hash_line = f"Hash rate: {snap['hashrate']:,.2f} GH/s" if snap["hashrate"] else "Hash rate: unavailable"
    diff = snap["difficulty"].get("currentDifficulty") if isinstance(snap["difficulty"], dict) else None
    diff_line = f"Mining difficulty: {diff:,.0f}" if isinstance(diff, (int, float)) else "Mining difficulty: unavailable"

    if snap["congestion"] == "High":
        read = "Network congestion is elevated. Higher transaction demand can signal active markets, but users may pay more to move funds."
    elif snap["congestion"] == "Medium":
        read = "The network is busy but functional. Confirmation speed may vary with fee choice."
    else:
        read = "The network looks healthy. Lower congestion usually means easier confirmations."

    title = {
        "fees": "⛏ Miner Fees",
        "mempool": "🧱 Mempool Intelligence",
        "mining": "⛏ Mining Intelligence",
        "difficulty": "⚙️ Bitcoin Difficulty",
        "health": "🟢 Network Health",
    }.get(topic, "📡 Bitcoin Network Stats")

    return (
        f"{title}\n\n"
        f"Block height: {snap['height'] or 'Unavailable'}\n"
        f"{tx_line}\n"
        f"Mempool transactions: {snap['mempool_count']:,}\n"
        f"Network congestion: {snap['congestion']}\n"
        f"Estimated transaction speed: {snap['speed']}\n"
        f"{fee_line}\n"
        f"{diff_line}\n"
        f"{hash_line}\n\n"
        f"Interpretation: {read}\n\n"
        "Educational only — not financial advice."
    )


def tx_value_summary(tx):
    input_sats = 0
    output_sats = 0
    input_addresses = []
    output_addresses = []

    for vin in tx.get("vin", []):
        prevout = vin.get("prevout") or {}
        input_sats += prevout.get("value", 0) or 0
        address = prevout.get("scriptpubkey_address")
        if address:
            input_addresses.append(address)

    for vout in tx.get("vout", []):
        output_sats += vout.get("value", 0) or 0
        address = vout.get("scriptpubkey_address")
        if address:
            output_addresses.append(address)

    fee_sats = tx.get("fee", max(0, input_sats - output_sats))
    return {
        "input_btc": btc_sats_to_btc(input_sats),
        "output_btc": btc_sats_to_btc(output_sats),
        "fee_btc": btc_sats_to_btc(fee_sats),
        "input_addresses": input_addresses,
        "output_addresses": output_addresses,
        "largest_output_btc": max([btc_sats_to_btc(v.get("value", 0)) for v in tx.get("vout", [])] or [0]),
    }


def analyze_transaction_risk(tx):
    values = tx_value_summary(tx)
    flags = []
    risk = "Low"

    input_count = len(tx.get("vin", []))
    output_count = len(tx.get("vout", []))
    if values["largest_output_btc"] >= BTC_WHALE_THRESHOLD_BTC:
        flags.append("Large BTC movement detected.")
        risk = "Medium"
    if input_count >= 10 and output_count >= 10:
        flags.append("Many inputs and outputs. This can appear in batching, privacy tools, or complex wallet activity.")
        risk = "Medium"
    if output_count >= 20:
        flags.append("High output count. Review carefully if this was unexpected.")
        risk = "Medium"
    if values["fee_btc"] > 0.05:
        flags.append("Unusually high fee paid compared with normal retail transfers.")
        risk = "Medium"
    if not flags:
        flags.append("No major public-chain red flags detected from this transaction alone.")

    return risk, flags


def classify_whale_tx(tx):
    values = tx_value_summary(tx)
    largest = values["largest_output_btc"]
    if largest < BTC_WHALE_THRESHOLD_BTC:
        return None

    price = btc_price_usd()
    usd_value = largest * price if price else 0
    labels = exchange_address_labels()
    input_labels = [labels.get(addr) for addr in values["input_addresses"] if labels.get(addr)]
    output_labels = [labels.get(addr) for addr in values["output_addresses"] if labels.get(addr)]

    movement_type = "Massive BTC transfer"
    sentiment = "Neutral"
    explanation = "Large BTC moved on-chain. Direction is unclear without verified exchange/wallet labels."
    wallet = values["output_addresses"][0] if values["output_addresses"] else ""

    if output_labels and not input_labels:
        movement_type = "Possible exchange inflow"
        sentiment = "Bearish"
        explanation = f"Large BTC moved toward a configured public exchange label ({output_labels[0]}). Possible sell pressure increasing."
    elif input_labels and not output_labels:
        movement_type = "Possible exchange outflow"
        sentiment = "Bullish"
        explanation = f"Large BTC moved away from a configured public exchange label ({input_labels[0]}). Possible accumulation or custody movement."
    elif input_labels and output_labels:
        movement_type = "Exchange distribution pattern"
        sentiment = "Neutral"
        explanation = "Configured exchange labels appear on both sides. This may be internal reshuffling or liquidity management."

    return {
        "txid": tx.get("txid", ""),
        "wallet": wallet,
        "amount_btc": largest,
        "usd_value": usd_value,
        "movement_type": movement_type,
        "sentiment": sentiment,
        "explanation": explanation,
    }


def save_whale_intelligence(event):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO whale_intelligence
        (txid, wallet, amount_btc, usd_value, movement_type, sentiment, explanation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["txid"],
            event["wallet"],
            event["amount_btc"],
            event["usd_value"],
            event["movement_type"],
            event["sentiment"],
            event["explanation"],
            datetime.now().isoformat(),
        )
    )
    conn.commit()
    conn.close()


def scan_btc_whales(limit=25):
    block_hash = btc_tip_hash()
    if not block_hash:
        return []
    txs = blockstream_json(f"/block/{block_hash}/txs") or []
    events = []
    for tx in txs[:limit]:
        event = classify_whale_tx(tx)
        if event:
            save_whale_intelligence(event)
            events.append(event)
    return events


def latest_whale_intelligence(limit=5):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT txid, wallet, amount_btc, usd_value, movement_type, sentiment, explanation, created_at
        FROM whale_intelligence
        ORDER BY id DESC LIMIT ?
        """,
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def whale_intelligence_summary(scan_live=True):
    events = scan_btc_whales() if scan_live else []
    rows = latest_whale_intelligence(5)
    if not rows:
        return (
            "🐋 BTC Whale Intelligence\n\n"
            "No massive BTC transfers were detected in the latest public block scan.\n\n"
            "Interpretation: Neutral. Whale pressure is quiet based on available public data.\n\n"
            "Educational only — not financial advice."
        )

    msg = "🐋 BTC Whale Intelligence\n\n"
    for txid, wallet, amount, usd, movement, sentiment, explanation, created_at in rows:
        confidence = "72%" if sentiment != "Neutral" else "58%"
        msg += (
            f"{movement}\n"
            f"Amount: {amount:,.2f} BTC (~${usd:,.0f})\n"
            f"Sentiment: {sentiment} | Confidence: {confidence}\n"
            f"Time: {created_at}\n"
            f"Read: {explanation}\n"
            f"TX: {txid[:12]}...\n\n"
        )
    msg += "Educational only — not financial advice."
    return msg.strip()


def transaction_explorer_summary(user_id, query):
    query = (query or "").strip()
    if is_btc_address(query):
        return wallet_info_summary(user_id, query, save_connection=False)
    if not is_btc_txid(query):
        return "I could not read that as a BTC TXID or public BTC address. Send a 64-character transaction hash or public wallet address."

    tx = blockstream_json(f"/tx/{query}")
    if not tx:
        return "Transaction not found or public explorer data is unavailable right now. Please check the TXID and try again."

    values = tx_value_summary(tx)
    confirmations = tx_confirmations(tx)
    risk, flags = analyze_transaction_risk(tx)
    status = tx.get("status", {})
    timestamp = "Unconfirmed"
    if status.get("block_time"):
        timestamp = datetime.fromtimestamp(status["block_time"]).isoformat()

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO transaction_history
        (user_id, query, txid, address, amount_btc, fee_btc, confirmations, risk_level, explanation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, query, query, "", values["output_btc"], values["fee_btc"], confirmations, risk, "; ".join(flags), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    return (
        "🔎 Transaction Explorer\n\n"
        f"TXID: {query}\n"
        f"Confirmations: {confirmations}\n"
        f"Total outputs: {values['output_btc']:,.8f} BTC\n"
        f"Fee: {values['fee_btc']:,.8f} BTC\n"
        f"Timestamp: {timestamp}\n"
        f"Risk level: {risk}\n\n"
        "Public-chain red flags:\n"
        + "\n".join([f"• {flag}" for flag in flags])
        + "\n\nOnly public blockchain data was used. Educational only — not financial advice."
    )


def wallet_stats(address):
    data = blockstream_json(f"/address/{address}")
    if not data:
        return None
    chain = data.get("chain_stats", {})
    mempool = data.get("mempool_stats", {})
    funded = (chain.get("funded_txo_sum", 0) or 0) + (mempool.get("funded_txo_sum", 0) or 0)
    spent = (chain.get("spent_txo_sum", 0) or 0) + (mempool.get("spent_txo_sum", 0) or 0)
    tx_count = (chain.get("tx_count", 0) or 0) + (mempool.get("tx_count", 0) or 0)
    balance_btc = btc_sats_to_btc(funded - spent)
    return {"raw": data, "balance_btc": balance_btc, "tx_count": tx_count, "funded_btc": btc_sats_to_btc(funded), "spent_btc": btc_sats_to_btc(spent)}


def recent_wallet_txs(address, limit=8):
    txs = blockstream_json(f"/address/{address}/txs") or []
    return txs[:limit]


def wallet_risk_analysis(address):
    stats = wallet_stats(address)
    if not stats:
        return None
    txs = recent_wallet_txs(address)
    flags = []
    risk = "Low"

    if stats["balance_btc"] >= BTC_WHALE_THRESHOLD_BTC:
        flags.append("Large wallet balance. Whale-movement monitoring is useful.")
        risk = "Medium"
    if stats["tx_count"] > 500:
        flags.append("Very active wallet. Could be exchange, service, bot, or high-frequency wallet behavior.")
        risk = "Medium"
    for tx in txs[:5]:
        tx_risk, tx_flags = analyze_transaction_risk(tx)
        if tx_risk != "Low":
            flags.extend(tx_flags[:1])
            risk = "Medium"
    if not flags:
        flags.append("No major public-chain risk pattern detected from recent activity.")

    return risk, flags, stats, txs


def wallet_info_summary(user_id, address, save_connection=False):
    if not is_btc_address(address):
        return "Please send a public BTC address only. Never send seed phrases, private keys, recovery phrases, or wallet passwords."
    analysis = wallet_risk_analysis(address)
    if not analysis:
        return "Wallet data is unavailable right now. Please verify the address and try again."
    risk, flags, stats, txs = analysis
    price = btc_price_usd()
    usd = stats["balance_btc"] * price if price else 0

    if save_connection:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO connected_wallets
            (user_id, address, label, chain, created_at, last_checked_at)
            VALUES (?, ?, ?, 'BTC', ?, ?)
            """,
            (user_id, address, "BTC Wallet", datetime.now().isoformat(), datetime.now().isoformat())
        )
        cur.execute("UPDATE connected_wallets SET last_checked_at=? WHERE user_id=? AND address=?", (datetime.now().isoformat(), user_id, address))
        conn.commit()
        conn.close()

    recent_count = len(txs)
    return (
        "👛 Wallet Intelligence\n\n"
        f"Address: {address}\n"
        f"Balance: {stats['balance_btc']:,.8f} BTC (~${usd:,.2f})\n"
        f"Total public transactions: {stats['tx_count']}\n"
        f"Recent transactions checked: {recent_count}\n"
        f"Risk level: {risk}\n\n"
        "Public-chain notes:\n"
        + "\n".join([f"• {flag}" for flag in flags[:5]])
        + "\n\nOnly public wallet address data is used. Never share seed phrases or private keys.\n"
        "Educational only — not financial advice."
    )


def scam_wallet_summary(address):
    if not is_btc_address(address):
        return "Send a public BTC address for /walletscan. Never send private keys, seed phrases, recovery phrases, or passwords."
    analysis = wallet_risk_analysis(address)
    if not analysis:
        return "Wallet scan is unavailable right now. Please verify the address and try again."
    risk, flags, stats, txs = analysis
    if stats["tx_count"] > 1000:
        risk = "Medium"
        flags.append("Extremely high transaction count can resemble service, hopping, or automated movement patterns.")
    if any(len(tx.get("vout", [])) > 25 for tx in txs):
        risk = "Medium"
        flags.append("Recent transactions include many outputs, which can be normal batching or suspicious dispersal.")
    return (
        "🚨 Scam Risk Analysis\n\n"
        f"Risk level: {risk}\n\n"
        "Why wallet appears suspicious or safe:\n"
        + "\n".join([f"• {flag}" for flag in flags[:6]])
        + "\n\nSafety recommendations:\n"
        "• Do not send funds because someone pressures you.\n"
        "• Verify addresses through official channels.\n"
        "• Never share seed phrases, private keys, recovery phrases, or wallet passwords.\n"
        "• Treat this as risk language, not an accusation of wrongdoing.\n\n"
        "Educational only — not financial advice."
    )


def chain_pressure_summary():
    rows = latest_whale_intelligence(10)
    if not rows:
        scan_btc_whales()
        rows = latest_whale_intelligence(10)
    bullish = sum(1 for row in rows if row[5] == "Bullish")
    bearish = sum(1 for row in rows if row[5] == "Bearish")
    neutral = len(rows) - bullish - bearish
    if bearish > bullish:
        read = "📉 Large BTC flows lean toward possible sell pressure."
        sentiment = "Bearish pressure"
    elif bullish > bearish:
        read = "📈 Large BTC flows lean toward possible accumulation or custody movement."
        sentiment = "Bullish pressure"
    else:
        read = "Large BTC flows are mixed or quiet."
        sentiment = "Neutral pressure"

    return (
        "🧬 Chain Intelligence\n\n"
        f"Sentiment: {sentiment}\n"
        f"Whale reads: {bullish} bullish, {bearish} bearish, {neutral} neutral\n"
        f"Interpretation: {read}\n\n"
        "Exchange flow labels require public addresses configured in EXCHANGE_BTC_ADDRESSES_JSON.\n\n"
        "Educational only — not financial advice."
    )


def log_engagement(user_id, feature, query=""):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO engagement_events (user_id, feature, query, created_at) VALUES (?, ?, ?, ?)",
            (user_id, feature, query, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("Engagement log failed: %s", exc)


def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def news_sentiment(title, summary):
    text = f"{title} {summary}".lower()
    bullish_words = ["etf", "inflow", "surge", "rally", "adoption", "partnership", "approval", "record", "buy", "accumulate", "bull"]
    bearish_words = ["hack", "lawsuit", "ban", "outflow", "selloff", "crackdown", "exploit", "fraud", "liquidation", "bear", "fine"]
    bullish = sum(1 for word in bullish_words if word in text)
    bearish = sum(1 for word in bearish_words if word in text)
    if bullish > bearish:
        return "Bullish"
    if bearish > bullish:
        return "Bearish"
    return "Neutral"


def fetch_crypto_news(limit=4, country=None):
    items = []
    query_country = (country or "").lower()
    for source, url in NEWS_FEEDS:
        try:
            response = requests.get(url, timeout=8)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:
            logging.info("RSS fetch failed for %s: %s", source, exc)
            continue

        for item in root.findall(".//item"):
            title = clean_html(item.findtext("title"))
            summary = clean_html(item.findtext("description"))
            link = item.findtext("link") or ""
            published = item.findtext("pubDate") or datetime.now().isoformat()
            if query_country and query_country not in f"{title} {summary}".lower():
                continue
            if not title:
                continue
            items.append({
                "title": title[:140],
                "summary": summary[:220] if summary else "Market story detected, but summary was brief.",
                "sentiment": news_sentiment(title, summary),
                "source": source,
                "url": link,
                "published_at": published,
            })
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break

    if items:
        cache_crypto_news(items, "country" if country else "global", country or "")
        return items
    return get_cached_crypto_news(limit, country=country)


def cache_crypto_news(items, topic, country=""):
    conn = db()
    cur = conn.cursor()
    for item in items:
        cur.execute(
            """
            INSERT INTO crypto_news_cache
            (topic, country, title, summary, sentiment, source, url, published_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                country,
                item["title"],
                item["summary"],
                item["sentiment"],
                item["source"],
                item["url"],
                item["published_at"],
                datetime.now().isoformat(),
            )
        )
    conn.commit()
    conn.close()


def get_cached_crypto_news(limit=4, country=None):
    conn = db()
    cur = conn.cursor()
    if country:
        cur.execute(
            "SELECT title, summary, sentiment, source, url, published_at FROM crypto_news_cache WHERE country=? ORDER BY id DESC LIMIT ?",
            (country, limit)
        )
    else:
        cur.execute(
            "SELECT title, summary, sentiment, source, url, published_at FROM crypto_news_cache ORDER BY id DESC LIMIT ?",
            (limit,)
        )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "title": title,
            "summary": summary,
            "sentiment": sentiment,
            "source": source,
            "url": url,
            "published_at": published_at,
        }
        for title, summary, sentiment, source, url, published_at in rows
    ]


def crypto_news_summary(user_id):
    items = fetch_crypto_news(limit=4)
    if not items:
        return (
            "📰 Crypto News\n\n"
            "Live news is unavailable right now. Market desk read: watch BTC ETF flows, exchange security headlines, regulatory pressure, and stablecoin liquidity.\n\n"
            "Interpretation: Neutral until fresh data confirms direction.\n\n"
            "Educational only — not financial advice."
        )

    lines = ["📰 Crypto News", ""]
    for item in items[:4]:
        lines.append(f"{item['sentiment']}: {item['title']}")
        lines.append(f"{item['summary']}")
        lines.append(f"Source: {item['source']}")
        lines.append("")
    lines.append("Educational only — not financial advice.")
    return "\n".join(lines).strip()


def market_events_summary(text="", pro=False):
    event = clean_html(text) if text else "macro news, regulation, rates, ETF flows, exchange security, and global risk sentiment"
    low = event.lower()
    btc = "BTC often reacts first as the market's liquidity and sentiment anchor."
    eth = "ETH may react more strongly when the event affects DeFi, staking, tokenization, or developer activity."
    sentiment = "Neutral"
    reasons = []

    if any(word in low for word in ["rate cut", "liquidity", "etf inflow", "approval", "stimulus", "adoption"]):
        sentiment = "Bullish"
        reasons.append("more liquidity or adoption can improve risk appetite")
    if any(word in low for word in ["war", "ban", "hack", "lawsuit", "rate hike", "inflation", "recession", "crackdown"]):
        sentiment = "Bearish" if sentiment == "Neutral" else "Mixed"
        reasons.append("risk-off headlines can reduce leverage and appetite for volatile assets")
    if not reasons:
        reasons.append("the impact depends on whether traders treat the event as liquidity-positive or risk-off")

    extra = ""
    if pro:
        extra = (
            "\n\nPro lens:\n"
            "Watch confirmation through BTC dominance, stablecoin supply, ETF flow direction, funding rates, and whether ETH/BTC strengthens or weakens."
        )

    return (
        "🌍 Global Event Market Predictions\n\n"
        f"Event focus: {event[:240]}\n\n"
        f"Sentiment read: {sentiment}\n"
        f"BTC: {btc}\n"
        f"ETH: {eth}\n"
        f"Why: {'; '.join(reasons).capitalize()}."
        f"{extra}\n\n"
        "Educational only — not financial advice."
    )


def daily_wisdom():
    index = datetime.now().toordinal() % len(WISDOM_MESSAGES)
    return (
        "🧠 Crypto Wisdom\n\n"
        f"{WISDOM_MESSAGES[index]}\n\n"
        "Small rule for today: size positions so you can still think clearly."
    )


def scam_stories_summary(pro=False):
    story = SCAM_STORY_LIBRARY[datetime.now().toordinal() % len(SCAM_STORY_LIBRARY)]
    red_flags = story["red_flags"] if pro else story["red_flags"][:2]
    avoid_steps = story["avoid"] if pro else story["avoid"][:2]
    msg = (
        "🚨 Real Crypto Scam Story\n\n"
        f"{story['title']}\n\n"
        f"What happened:\n{story['story']}\n\n"
        f"How the victim was trapped:\n{story['trap']}\n\n"
        "Red flags:\n"
        + "\n".join([f"• {flag}" for flag in red_flags])
        + "\n\nHow to avoid it:\n"
        + "\n".join([f"• {step}" for step in avoid_steps])
        + f"\n\nFinal warning:\n{story['final']}"
    )
    if pro:
        msg += "\n\nPro safety check: pause, verify the domain, inspect wallet permissions, confirm official support channels, and assume urgency is part of the trap."
    else:
        msg += "\n\nPro unlocks a deeper red-flag and prevention checklist."
    return msg


def normalize_country(country):
    raw = clean_html(country).strip()
    if not raw:
        return "Haiti"
    lowered = raw.lower()
    if lowered in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[lowered]
    for known in COUNTRY_PROFILES:
        if lowered == known.lower():
            return known
    return raw.title()


def country_news_summary(country, pro=False):
    country = normalize_country(country)
    profile = COUNTRY_PROFILES.get(country, {
        "flag": "🌍",
        "region": "Global",
        "adoption": "Adoption varies by banking access, inflation, smartphone usage, and exchange availability",
        "regulation": "Local rules may be unclear or changing",
        "exchanges": "Users should verify which licensed exchanges serve their country",
        "remittance": "Remittance usefulness depends on fees, liquidity, and cash-out options",
        "institutional": "Institutional activity depends on local banking and securities rules",
        "retail": "Retail sentiment often follows BTC price, local currency pressure, and social media cycles",
        "mining": "Mining relevance depends on electricity cost, climate, and legal clarity",
    })
    items = fetch_crypto_news(limit=2, country=country)

    lines = [
        f"{profile['flag']} Country Crypto News: {country}",
        "",
        f"Adoption: {profile['adoption']}",
        f"Regulation: {profile['regulation']}",
        f"Exchange access: {profile['exchanges']}",
        f"Scam risk: Watch fake exchange support, wallet-drain links, investment managers, and too-good-to-be-true return pitches.",
        f"Remittance use: {profile['remittance']}",
        "Blockchain activity: CoinPilotX reads public BTC network pressure, whale flow, and congestion as global context; local wallet-growth data is only shown when public data is available.",
    ]

    if pro:
        lines.extend([
            f"Institutional activity: {profile['institutional']}",
            f"Retail sentiment: {profile['retail']}",
            f"Mining activity: {profile['mining']}",
        ])

    if items:
        lines.extend(["", "Recent headlines:"])
        for item in items:
            lines.append(f"{item['sentiment']}: {item['title']} ({item['source']})")
    else:
        lines.extend([
            "",
            "Recent live country headlines were unavailable, so this is an educational country-level guide instead.",
        ])

    lines.append("")
    lines.append("Educational only — not financial advice.")
    return "\n".join(lines)


def country_picker_menu():
    rows = []
    for i in range(0, len(COUNTRY_PICKER_COUNTRIES), 2):
        label, country = COUNTRY_PICKER_COUNTRIES[i]
        row = [
            InlineKeyboardButton(label, callback_data=f"countrynews_{country}")
        ]
        if i + 1 < len(COUNTRY_PICKER_COUNTRIES):
            label, country = COUNTRY_PICKER_COUNTRIES[i + 1]
            row.append(InlineKeyboardButton(label, callback_data=f"countrynews_{country}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def parse_money_amount(text):
    cleaned = re.sub(r"[$,\s]", "", text or "")
    amount = float(cleaned)
    if amount < 0:
        raise ValueError("Balance cannot be negative.")
    return amount


def get_portfolio_settings(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT manual_total_usd, manual_override_enabled FROM user_portfolio_settings WHERE user_id=?",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, False
    return row[0], bool(row[1])


def set_manual_balance(user_id, amount):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO user_portfolio_settings (user_id, manual_total_usd, manual_override_enabled) VALUES (?, ?, 1)",
        (user_id, amount)
    )
    conn.commit()
    conn.close()


def clear_manual_balance(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO user_portfolio_settings (user_id, manual_total_usd, manual_override_enabled) VALUES (?, NULL, 0)",
        (user_id,)
    )
    conn.commit()
    conn.close()


def get_klines(asset, interval="1h", limit=48):
    data = safe_get_json(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": binance_symbol(asset), "interval": interval, "limit": limit},
    )
    if not data:
        return []

    candles = []
    for row in data:
        try:
            candles.append({
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        except Exception:
            continue
    return candles


def get_price_history(asset, limit=40):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT price FROM price_history WHERE asset=? ORDER BY id DESC LIMIT ?",
        (asset, limit)
    )
    rows = cur.fetchall()
    conn.close()
    prices = [r[0] for r in rows][::-1]
    if len(prices) >= min(limit, 20):
        return prices

    candles = get_klines(asset, "1h", limit)
    if candles:
        return [c["close"] for c in candles]
    return prices


def calculate_rsi_like(prices):
    if len(prices) < 6:
        return None

    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def smart_market_signal(asset, current_price, is_pro_user=False):
    prices = get_price_history(asset, 40)
    if len(prices) < 12:
        return {
            "action": "WAIT",
            "confidence": "Low",
            "reason": "Not enough live market history yet.",
            "trend": "Unknown",
            "volatility": "0.00%",
            "rsi": None,
            "score": 0,
        }

    short_prices = prices[-8:]
    long_prices = prices[-24:] if len(prices) >= 24 else prices
    short_ma = sum(short_prices) / len(short_prices)
    long_ma = sum(long_prices) / len(long_prices)
    momentum_pct = ((prices[-1] - prices[-6]) / prices[-6]) * 100 if len(prices) >= 6 and prices[-6] else 0
    volatility = ((max(short_prices) - min(short_prices)) / current_price) * 100 if current_price else 0
    rsi = calculate_rsi_like(prices)

    trend = "Uptrend" if short_ma > long_ma else "Downtrend" if short_ma < long_ma else "Sideways"
    score = 0
    reasons = []

    if trend == "Uptrend":
        score += 2
        reasons.append("short-term trend is above the broader average")
    elif trend == "Downtrend":
        score -= 2
        reasons.append("short-term trend is below the broader average")

    if rsi is not None:
        if rsi < 32:
            score += 2
            reasons.append("RSI-style reading looks oversold")
        elif rsi > 72:
            score -= 2
            reasons.append("RSI-style reading looks overheated")
        elif 45 <= rsi <= 60 and trend == "Uptrend":
            score += 1
            reasons.append("momentum is healthy without looking overextended")

    if momentum_pct > 1:
        score += 1
        reasons.append("recent momentum is positive")
    elif momentum_pct < -1:
        score -= 1
        reasons.append("recent momentum is negative")

    if volatility > 4:
        score = int(score / 2)
        reasons.append("volatility is elevated, so confidence is reduced")

    if score >= 3:
        action = "BUY"
        confidence = "High" if is_pro_user and score >= 4 else "Medium"
    elif score <= -3:
        action = "SELL"
        confidence = "High" if is_pro_user and score <= -4 else "Medium"
    else:
        action = "WAIT"
        confidence = "Medium" if is_pro_user else "Low"

    if not reasons:
        reasons.append("market signals are mixed")

    return {
        "action": action,
        "confidence": confidence,
        "reason": "; ".join(reasons).capitalize() + ".",
        "trend": trend,
        "volatility": f"{volatility:.2f}%",
        "rsi": rsi,
        "score": score,
    }


def ai_crypto_analysis(user_id, asset):
    asset = normalize_asset(asset)
    price_now, sources = get_best_price(asset)
    if not price_now:
        return f"I could not load live {asset} data right now."

    save_price_history(asset, price_now)
    signal = smart_market_signal(asset, price_now, is_pro(user_id))
    fear = get_fear_greed()
    holding = get_manual_holding(user_id, asset)
    holding_note = (
        f"You track {holding:.6f} {asset}, worth about ${holding * price_now:,.2f}."
        if holding > 0 else
        f"You do not currently track a {asset} holding."
    )
    source_text = ", ".join([f"{name} ${value:,.2f}" for name, value in sources.items()])
    rsi_text = f"{signal['rsi']:.1f}" if signal["rsi"] is not None else "warming up"

    summary = (
        f"🧠 AI Crypto Analysis: {asset}\n\n"
        f"Live price: ${price_now:,.2f}\n"
        f"Signal: {signal['action']} ({signal['confidence']})\n"
        f"Trend: {signal['trend']}\n"
        f"RSI-style score: {rsi_text}\n"
        f"Volatility: {signal['volatility']}\n"
        f"Fear/Greed: {fear['label']}\n\n"
        f"Read: {signal['reason']}\n\n"
        f"Portfolio context: {holding_note}\n"
        f"Sources: {source_text or 'live source unavailable'}\n\n"
        "Educational only. Not financial advice."
    )

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_analyses (user_id, asset, summary, created_at) VALUES (?, ?, ?, ?)",
        (user_id, asset, summary, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return summary


def get_fear_greed():
    data = safe_get_json("https://api.alternative.me/fng/", timeout=8)
    try:
        item = data["data"][0]
        value = int(item["value"])
        label = item["value_classification"]
    except Exception:
        value = None
        label = "Unavailable"

    if value is None:
        advice = "Market mood data is unavailable right now."
    elif value <= 25:
        advice = "Fear is high. Watch for panic selling, but wait for confirmation."
    elif value >= 75:
        advice = "Greed is high. Avoid chasing candles and consider risk control."
    else:
        advice = "Mood is mixed. Let trend and risk rules guide decisions."

    return {"value": value, "label": label, "advice": advice}


def chart_url(asset):
    asset = normalize_asset(asset)
    candles = get_klines(asset, "1h", 48)
    if not candles:
        return None

    labels = [datetime.fromtimestamp(c["time"] / 1000).strftime("%H:%M") for c in candles[-24:]]
    closes = [round(c["close"], 2) for c in candles[-24:]]
    config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": f"{asset}/USDT live 24h",
                "data": closes,
                "borderColor": "#1f8f6f",
                "backgroundColor": "rgba(31,143,111,0.12)",
                "fill": True,
                "pointRadius": 0,
            }],
        },
        "options": {
            "plugins": {"legend": {"display": True}},
            "scales": {"x": {"ticks": {"maxTicksLimit": 6}}},
        },
    }
    return "https://quickchart.io/chart?width=900&height=420&c=" + requests.utils.quote(json.dumps(config))


def get_whale_activity(asset):
    asset = normalize_asset(asset)
    data = safe_get_json(
        "https://api.binance.com/api/v3/aggTrades",
        params={"symbol": binance_symbol(asset), "limit": 100},
    )
    if not data:
        return []

    whales = []
    for trade in data:
        try:
            price = float(trade["p"])
            quantity = float(trade["q"])
            notional = price * quantity
            if notional >= WHALE_NOTIONAL_USD_THRESHOLD:
                whales.append({
                    "asset": asset,
                    "side": "SELL pressure" if trade.get("m") else "BUY pressure",
                    "notional": notional,
                    "price": price,
                    "source": "Binance aggregate trades",
                })
        except Exception:
            continue
    return whales[:5]


def clamp_number(value, low=0, high=100):
    return max(low, min(high, int(round(value))))


def parse_percent(value):
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return 0.0


def live_intelligence_feed():
    now = datetime.now()
    cached_at = INTELLIGENCE_FEED_CACHE.get("created_at")
    if (
        INTELLIGENCE_FEED_CACHE.get("data")
        and cached_at
        and (now - cached_at).total_seconds() < INTELLIGENCE_FEED_CACHE_SECONDS
    ):
        return INTELLIGENCE_FEED_CACHE["data"]

    try:
        price_now, sources = get_best_price("BTC")
        if not price_now:
            raise ValueError("BTC price unavailable")

        btc_market = get_market_by_symbol("BTC") or {}
        candles = get_klines("BTC", "1h", 48)
        prices = [c["close"] for c in candles] if candles else get_price_history("BTC", 40)
        volumes = [c["volume"] for c in candles] if candles else []
        previous_24h = prices[-25] if len(prices) >= 25 else prices[0] if prices else price_now
        change_24h = ((price_now - previous_24h) / previous_24h) * 100 if previous_24h else 0
        if isinstance(btc_market.get("change_24h"), (int, float)):
            change_24h = btc_market["change_24h"]
        momentum_pct = ((prices[-1] - prices[-6]) / prices[-6]) * 100 if len(prices) >= 6 and prices[-6] else change_24h

        signal = smart_market_signal("BTC", price_now, True)
        volatility = parse_percent(signal.get("volatility", "0"))
        fear = get_fear_greed()
        fear_value = fear.get("value")
        whales = get_whale_activity("BTC")

        buy_whale = sum(w["notional"] for w in whales if "BUY" in w.get("side", ""))
        sell_whale = sum(w["notional"] for w in whales if "SELL" in w.get("side", ""))
        whale_net = buy_whale - sell_whale
        if sell_whale > buy_whale * 1.15 and sell_whale:
            whale_pressure = "sell pressure"
        elif buy_whale > sell_whale * 1.15 and buy_whale:
            whale_pressure = "accumulation"
        elif whales:
            whale_pressure = "mixed"
        else:
            whale_pressure = "quiet"

        volume_pressure = "normal"
        volume_adjust = 0
        if len(volumes) >= 12:
            recent_volume = sum(volumes[-6:]) / 6
            prior_volume = sum(volumes[-12:-6]) / 6
            if prior_volume:
                volume_ratio = recent_volume / prior_volume
                if volume_ratio >= 1.35 and momentum_pct > 0:
                    volume_pressure = "rising demand"
                    volume_adjust = 5
                elif volume_ratio >= 1.35 and momentum_pct < 0:
                    volume_pressure = "sell-side pressure"
                    volume_adjust = -5

        signal_score = 50
        signal_score += signal.get("score", 0) * 7
        signal_score += max(-12, min(12, momentum_pct * 2))
        if fear_value is not None:
            if 35 <= fear_value <= 65:
                signal_score += 3
            elif fear_value <= 20:
                signal_score -= 4
            elif fear_value >= 80:
                signal_score -= 5
        if whale_pressure == "accumulation":
            signal_score += 6
        elif whale_pressure == "sell pressure":
            signal_score -= 8
        signal_score += volume_adjust
        signal_score = clamp_number(signal_score)

        risk_score = 30
        risk_score += volatility * 8
        risk_score += abs(change_24h) * 2
        if whale_pressure == "sell pressure":
            risk_score += 13
        elif whale_pressure == "mixed":
            risk_score += 7
        if fear_value is None:
            risk_score += 6
        elif fear_value <= 20 or fear_value >= 80:
            risk_score += 10
        if volume_pressure == "sell-side pressure":
            risk_score += 8
        risk_score = clamp_number(risk_score)

        if risk_score >= 78:
            action = "HIGH VOLATILITY"
        elif risk_score >= 65:
            action = "WATCH CLOSELY"
        elif signal_score >= 68 and risk_score < 55:
            action = "BUY"
        elif signal_score >= 56 and risk_score < 64:
            action = "HOLD"
        elif signal_score <= 38 or signal.get("action") == "SELL":
            action = "REDUCE RISK"
        else:
            action = "WAIT"

        if risk_score >= 70:
            market_state = "volatile"
        elif signal_score >= 65:
            market_state = "constructive"
        elif signal_score <= 40:
            market_state = "defensive"
        else:
            market_state = "mixed"

        confidence = 52
        confidence += 12 if candles else 0
        confidence += 8 if fear_value is not None else 0
        confidence += 6 if sources else 0
        confidence += min(10, len(whales) * 2)
        confidence -= 8 if volatility >= 4 else 0
        confidence = clamp_number(confidence, 25, 92)

        data = {
            "signal": signal_score,
            "risk": risk_score,
            "action": action,
            "btc_price": round(price_now, 2),
            "change_24h": round(change_24h, 2),
            "market_state": market_state,
            "confidence": confidence,
            "trend": signal.get("trend", "Unknown"),
            "volatility": f"{volatility:.2f}%",
            "whale_pressure": whale_pressure,
            "fear_greed": fear.get("label", "Unavailable"),
            "volume_pressure": volume_pressure,
            "updated_at": now.isoformat(),
            "message": "Signal suggests reviewing market context before acting. Educational only — not financial advice.",
        }
    except Exception as exc:
        logging.info("Live intelligence feed failed: %s", exc)
        data = {
            "signal": None,
            "risk": None,
            "action": "DATA UNAVAILABLE",
            "btc_price": None,
            "change_24h": None,
            "market_state": "updating",
            "confidence": None,
            "trend": "Unavailable",
            "volatility": "Unavailable",
            "whale_pressure": "updating",
            "fear_greed": "Unavailable",
            "volume_pressure": "updating",
            "updated_at": now.isoformat(),
            "message": "Live intelligence is temporarily unavailable. Educational only — not financial advice.",
        }

    INTELLIGENCE_FEED_CACHE["data"] = data
    INTELLIGENCE_FEED_CACHE["created_at"] = now
    return data


def save_whale_alert(alert):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO whale_alerts (asset, side, notional_usd, price, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (alert["asset"], alert["side"], alert["notional"], alert["price"], alert["source"], datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def latest_whale_alerts(limit=5):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT asset, side, notional_usd, price, source, created_at FROM whale_alerts ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def calculate_live_portfolio(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT asset, amount FROM manual_portfolio WHERE user_id=? AND amount > 0", (user_id,))
    rows = cur.fetchall()
    conn.close()

    total = 0
    holdings = {}
    lines = []
    for asset, amount in rows:
        price_now, _ = get_best_price(asset)
        if not price_now:
            lines.append(f"{asset}: live price unavailable")
            continue
        save_price_history(asset, price_now)
        value = amount * price_now
        total += value
        holdings[asset] = {"amount": amount, "price": price_now, "value": value}
        lines.append(f"{asset}: {amount:.6f} × ${price_now:,.2f} = ${value:,.2f}")

    return total, holdings, lines, rows


def portfolio_live_summary(user_id, save_snapshot=True):
    total, holdings, lines, rows = calculate_live_portfolio(user_id)
    manual_total, override_enabled = get_portfolio_settings(user_id)

    if not rows and not override_enabled:
        return "💼 Real-Time Portfolio\n\nNo tracked holdings yet.\nTry: /addholding BTC 0.02\n\nOr set a manual balance with /setbalance 1250"

    if save_snapshot:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO portfolio_snapshots (user_id, total_value, holdings_json, created_at) VALUES (?, ?, ?, ?)",
            (user_id, total, json.dumps(holdings), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    live_lines = "\n".join(lines) if lines else "No live holdings are currently tracked."

    if override_enabled:
        manual_value = manual_total or 0
        difference = manual_value - total
        return (
            "💼 Real-Time Portfolio\n\n"
            f"Manual balance: ${manual_value:,.2f}\n"
            f"Live tracked value: ${total:,.2f}\n"
            f"Difference: ${difference:,.2f}\n\n"
            f"Tracked holdings:\n{live_lines}\n\n"
            "Manual balance changes what you see, but it does not overwrite your holdings."
        )

    return (
        "💼 Real-Time Portfolio\n\n"
        + live_lines
        + f"\n\nEstimated total: ${total:,.2f}\nCoinPilotX tracks only. It never holds funds."
    )


def latest_market_pressure():
    news_rows = get_cached_crypto_news(limit=5)
    bullish = sum(1 for item in news_rows if item["sentiment"] == "Bullish")
    bearish = sum(1 for item in news_rows if item["sentiment"] == "Bearish")
    whale_rows = latest_whale_alerts(5)
    whale_sell = sum(1 for row in whale_rows if "SELL" in row[1])
    whale_buy = sum(1 for row in whale_rows if "BUY" in row[1])
    chain_rows = latest_whale_intelligence(5)
    chain_bullish = sum(1 for row in chain_rows if row[5] == "Bullish")
    chain_bearish = sum(1 for row in chain_rows if row[5] == "Bearish")

    score = bullish - bearish + whale_buy - whale_sell + chain_bullish - chain_bearish
    if score > 1:
        label = "Bullish pressure"
    elif score < -1:
        label = "Bearish pressure"
    else:
        label = "Mixed pressure"
    return label, score


def portfolio_advice_summary(user_id):
    total, holdings, lines, rows = calculate_live_portfolio(user_id)
    manual_total, override_enabled = get_portfolio_settings(user_id)
    display_value = manual_total if override_enabled and manual_total is not None else total
    difference = (manual_total - total) if override_enabled and manual_total is not None else 0
    pro = is_pro(user_id)
    fear = get_fear_greed()
    pressure_label, pressure_score = latest_market_pressure()

    weighted_score = 0
    weighted_value = 0
    risk_points = 0
    explanation_bits = []
    upside_total = 0
    downside_total = 0
    scenario_lines = []
    watched_symbols = []

    for asset, data in holdings.items():
        price = data["price"]
        value = data["value"]
        watched_symbols.append(asset)
        signal = smart_market_signal(asset, price, pro)
        asset_score = signal.get("score", 0)
        weighted_score += asset_score * value
        weighted_value += value
        volatility_text = signal.get("volatility", "0%").replace("%", "")
        try:
            volatility = float(volatility_text)
        except Exception:
            volatility = 0
        if volatility >= 4:
            risk_points += 2
        elif volatility >= 2:
            risk_points += 1

        scenario_pct = 0.03
        upside = value * scenario_pct
        downside = value * scenario_pct
        upside_total += upside
        downside_total += downside
        scenario_lines.append(
            f"If {asset} rises 3%, your {asset} position could increase by about ${upside:,.2f}. If it drops 3%, it could lose about ${downside:,.2f}."
        )
        if pro:
            explanation_bits.append(
                f"{asset}: {signal['action']} score {asset_score}, {signal['trend']}, volatility {signal['volatility']}"
            )

    avg_score = (weighted_score / weighted_value) if weighted_value else 0
    if fear["value"] is not None:
        if fear["value"] >= 75:
            avg_score -= 0.75
            risk_points += 1
        elif fear["value"] <= 25:
            avg_score += 0.5
            risk_points += 1

    avg_score += pressure_score * 0.25

    if not rows and not override_enabled:
        action = "WAIT"
        risk_level = "Unknown"
        reason = "No holdings or manual balance are set yet, so the safest suggestion is to set your balance or add holdings first."
    else:
        if avg_score >= 2.5:
            action = "HOLD" if total > 0 else "WAIT"
            reason = "Trend and sentiment lean constructive, but position sizing still matters."
        elif avg_score <= -2.5:
            action = "SELL" if total > 0 else "WAIT"
            reason = "Market signals lean defensive and risk control is more important than chasing."
        elif total > 0:
            action = "HOLD"
            reason = "Signals are mixed, so holding and waiting for confirmation is cleaner than forcing a trade."
        else:
            action = "WAIT"
            reason = "There is not enough tracked exposure to justify a stronger portfolio suggestion."

        if risk_points >= 3 or abs(difference) > max(total, 1) * 0.25:
            risk_level = "High"
        elif risk_points >= 1 or fear["label"] in ["Extreme Fear", "Extreme Greed"]:
            risk_level = "Medium"
        else:
            risk_level = "Low"

    if not scenario_lines:
        scenario_lines.append("No tracked asset position is available for a gain/loss estimate yet.")

    manual_text = f"${manual_total:,.2f}" if override_enabled and manual_total is not None else "Not set"
    symbols = ", ".join(watched_symbols) if watched_symbols else "Portfolio"
    explanation = (
        f"Current portfolio value: ${display_value or 0:,.2f}\n"
        f"Live tracked value: ${total:,.2f}\n"
        f"Manual balance: {manual_text}\n"
        f"Difference between manual and live: ${difference:,.2f}\n"
        f"Suggested action: {action}\n\n"
        f"Why this suggestion was made:\n{reason}\n"
        f"Fear/Greed: {fear['label']}\n"
        f"Recent whale/news pressure: {pressure_label}\n\n"
        "Potential upside scenario:\n"
        + "\n".join(scenario_lines)
        + f"\nTotal possible upside estimate on a 3% move: ${upside_total:,.2f}\n\n"
        "Potential downside scenario:\n"
        f"Total possible downside estimate on a 3% move: ${downside_total:,.2f}\n\n"
        f"Risk level: {risk_level}\n"
        "What would make the signal change: stronger trend confirmation, lower volatility, a major whale/news shift, or a clear change in Fear/Greed.\n"
    )

    if pro and explanation_bits:
        explanation += "\nPro detail:\n" + "\n".join([f"• {bit}" for bit in explanation_bits]) + "\n"
    elif not pro:
        explanation += "\nFree view: upgrade to Pro for trend, momentum, volatility, whale pressure, and exposure detail.\n"

    explanation += "\nEducational only — not financial advice."

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO portfolio_advice_history
        (user_id, symbol, action, current_value, upside_estimate, downside_estimate, explanation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, symbols, action, display_value or 0, upside_total, downside_total, explanation, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    return "🧭 Portfolio Decision Intelligence\n\n" + explanation


def account_summary(user_id):
    website_user = get_linked_website_account(user_id)
    if not website_user:
        logging.info("Unlinked account requested from Telegram user %s", user_id)
        return (
            "👤 Account Not Connected\n\n"
            "To view your CoinPilotX account inside Telegram, please create or log in to your account on our website first.\n\n"
            "Create account:\n"
            "https://coinpilotx.app/signup\n\n"
            "Already have an account?\n"
            "https://coinpilotx.app/login\n\n"
            "After logging in, go to Account Settings and tap ‘Connect Telegram Bot.’\n\n"
            "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords."
        )

    logging.info("Linked account found for Telegram user %s", user_id)
    name = website_user.get("full_name") or website_user.get("display_name") or "Not set"
    plan = website_user.get("plan") or website_user.get("subscription_plan") or "free"
    status = website_user.get("subscription_status") or "inactive"
    return (
        "👤 CoinPilotX Account\n\n"
        f"Name: {name}\n"
        f"Email: {mask_email(website_user.get('email'))}\n"
        f"Plan: {plan}\n"
        f"Subscription: {status}\n"
        "Telegram: Connected\n"
        f"Email Updates: {'Enabled' if website_user.get('email_opt_in') else 'Disabled'}\n"
        f"SMS Updates: {'Enabled' if website_user.get('sms_opt_in') else 'Disabled'}"
    )


def legacy_account_summary(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT display_name, email, is_pro, subscription_plan, subscription_status, risk_profile, preferred_exchange_goal FROM users WHERE user_id=?",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return "Account not found. Use /start first."

    name, email, pro, plan, status, risk, exchange_goal = row
    return (
        "👤 Account\n\n"
        f"Name: {name or 'Not set'}\n"
        f"Email: {email or 'Not set'}\n"
        f"Plan: {plan or 'free'}\n"
        f"Subscription: {status or 'inactive'}\n"
        f"Pro active: {'Yes' if pro else 'No'}\n"
        f"Risk profile: {risk or 'balanced'}\n"
        f"Exchange preference: {exchange_goal or 'beginner'}"
    )


def mask_email(email):
    email = email or ""
    if "@" not in email:
        return "Not set"
    name, domain = email.split("@", 1)
    visible = name[:2] if len(name) > 2 else name[:1]
    return f"{visible}***@{domain}"


def mask_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 4:
        return "Not set"
    return f"***-***-{digits[-4:]}"


def get_linked_website_account(telegram_user_id):
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM users WHERE telegram_user_id=? AND email!='' LIMIT 1",
        (telegram_user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def account_reply_markup(telegram_user_id):
    if get_linked_website_account(telegram_user_id):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Open Dashboard", url="https://coinpilotx.app/account")],
            [InlineKeyboardButton("Upgrade Pro", callback_data="upgrade_pro")],
            [InlineKeyboardButton("Settings", url="https://coinpilotx.app/account/settings")],
            [InlineKeyboardButton("Help", callback_data="menu_help")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Create Account", url="https://coinpilotx.app/signup")],
        [InlineKeyboardButton("Login", url="https://coinpilotx.app/login")],
        [InlineKeyboardButton("Help", callback_data="menu_help")],
    ])


def admin_summary():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(is_pro), SUM(alerts_enabled) FROM users")
    users, pro_users, alert_users = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM alerts_history")
    alert_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM whale_alerts")
    whale_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM portfolio_snapshots")
    snapshot_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM payment_verifications WHERE status='verified'")
    verified_payments = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM payment_verifications WHERE status!='verified'")
    failed_verifications = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM email_logs WHERE status='sent'")
    sent_emails = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM email_logs WHERE status!='sent'")
    email_issues = cur.fetchone()[0]
    conn.close()

    return (
        "🛠 Admin Dashboard\n\n"
        f"Users: {users or 0}\n"
        f"Pro users: {pro_users or 0}\n"
        f"Alerts enabled: {alert_users or 0}\n"
        f"Saved alerts: {alert_count}\n"
        f"Whale alerts: {whale_count}\n"
        f"Portfolio snapshots: {snapshot_count}\n"
        f"Verified payments: {verified_payments}\n"
        f"Failed/pending verifications: {failed_verifications}\n"
        f"Emails sent: {sent_emails}\n"
        f"Email issues/skipped: {email_issues}"
    )


@webhook_app.route("/health", methods=["GET"])
def health_check():
    return {"status": "ok", "bot": BOT_NAME}, 200


@webhook_app.route("/admin-dashboard", methods=["GET"])
def admin_dashboard_route():
    dashboard_token = os.getenv("ADMIN_DASHBOARD_TOKEN")
    if dashboard_token and request.args.get("token") != dashboard_token:
        return "Unauthorized", 401

    summary = admin_summary().replace("\n", "<br>")
    return f"""
    <html>
        <head>
            <title>CoinPilotXAI Inc. Admin</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; color: #17211f; }}
                .panel {{ max-width: 720px; border: 1px solid #d8e2df; border-radius: 8px; padding: 24px; }}
                h1 {{ margin-top: 0; }}
            </style>
        </head>
        <body>
            <div class="panel">
                <h1>CoinPilotXAI Inc. Admin</h1>
                <p>{summary}</p>
            </div>
        </body>
    </html>
    """, 200


def exchange_recommendation(goal, risk_profile="balanced"):
    goal = (goal or "beginner").lower()
    scored = []
    for name, profile in EXCHANGE_PROFILES.items():
        score = 0
        if goal in profile["best_for"]:
            score += 3
        if risk_profile == "conservative" and profile["security"] == "High":
            score += 2
        if goal == "lowfees" and profile["fees"] == "Low":
            score += 2
        scored.append((score, name, profile))
    scored.sort(reverse=True)
    _, best_name, best = scored[0]
    alternatives = [name for _, name, _ in scored[1:3]]
    return (
        f"🏦 Smarter Exchange Match\n\n"
        f"Best match: {best_name}\n"
        f"Why: {best['note']}\n"
        f"Fees: {best['fees']}\n"
        f"Security: {best['security']}\n"
        f"Official site: {best['url']}\n\n"
        f"Also compare: {', '.join(alternatives)}\n\n"
        "Use official websites only. CoinPilotX does not create accounts or hold funds."
    )


async def analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    asset = normalize_asset(context.args[0] if context.args else "BTC")
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, ai_crypto_analysis(user_id, asset))), reply_markup=main_menu())


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(
            append_plan_footer(update.effective_user.id, "💬 AI Crypto Assistant\n\nAsk me anything with /ask your question.\nExample: /ask Should I be worried about a wallet asking for approval?"),
            reply_markup=main_menu()
        )
        return
    await update.message.reply_text(openai_chat_completion(update.effective_user.id, question), reply_markup=main_menu())


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    asset = normalize_asset(context.args[0] if context.args else "BTC")
    if asset not in CHART_ASSETS:
        await update.message.reply_text("Live charts are available for BTC and ETH right now. Example: /chart ETH")
        return
    url = chart_url(asset)
    if not url:
        await update.message.reply_text(f"I could not build the live {asset} chart right now.")
        return
    await update.message.reply_photo(photo=url, caption=f"📊 {asset} live 24h chart", reply_markup=main_menu())


async def feargreed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    fear = get_fear_greed()
    value = "Unavailable" if fear["value"] is None else str(fear["value"])
    await update.message.reply_text(
        append_plan_footer(user_id, f"😬 Market Fear/Greed AI\n\nScore: {value}\nMood: {fear['label']}\n\n{fear['advice']}\n\nEducational only — not financial advice."),
        reply_markup=main_menu()
    )


async def whales_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, whale_intelligence_summary(scan_live=True))), reply_markup=main_menu())


async def whalebtc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, whale_intelligence_summary(scan_live=True))), reply_markup=main_menu())


async def whalealerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, whale_intelligence_summary(scan_live=False))), reply_markup=main_menu())


async def btcstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(append_plan_footer(update.effective_user.id, network_stats_summary("network")), reply_markup=main_menu())


async def network_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(append_plan_footer(update.effective_user.id, network_stats_summary("network")), reply_markup=main_menu())


async def fees_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(append_plan_footer(update.effective_user.id, network_stats_summary("fees")), reply_markup=main_menu())


async def mempool_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(append_plan_footer(update.effective_user.id, network_stats_summary("mempool")), reply_markup=main_menu())


async def checktx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not context.args:
        await update.message.reply_text("Example: /checktx YOUR_BTC_TXID_OR_PUBLIC_ADDRESS")
        return
    query = context.args[0].strip()
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, transaction_explorer_summary(user_id, query))), reply_markup=main_menu())


async def connectwallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not context.args:
        await update.message.reply_text(
            "👛 Connect Wallet\n\nSend a public BTC address only:\n/connectwallet bc1...\n\nNever send seed phrases, private keys, recovery phrases, or wallet passwords."
        )
        return
    address = context.args[0].strip()
    await update.message.reply_text(wallet_info_summary(update.effective_user.id, address, save_connection=True), reply_markup=main_menu())


async def walletinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not context.args:
        await update.message.reply_text("Example: /walletinfo bc1...\nOnly public BTC addresses are allowed.")
        return
    await update.message.reply_text(wallet_info_summary(update.effective_user.id, context.args[0].strip()), reply_markup=main_menu())


async def walletscan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not context.args:
        await update.message.reply_text("Example: /walletscan bc1...\nOnly public BTC addresses are allowed.")
        return
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, scam_wallet_summary(context.args[0].strip()))), reply_markup=main_menu())


async def scamintel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    msg = (
        "🚨 Scam Detection Intelligence\n\n"
        "CoinPilotX checks public wallet and transaction behavior for risk signals like large drains, high-output dispersal, unusual transfer complexity, and possible mixer-like patterns.\n\n"
        "Use:\n/walletscan PUBLIC_BTC_ADDRESS\n/checktx TXID\n\n"
        "Never send seed phrases, private keys, recovery phrases, or wallet passwords.\n\n"
        "Educational only — not financial advice."
    )
    await update.message.reply_text(append_plan_footer(update.effective_user.id, msg), reply_markup=main_menu())


async def chainintel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, chain_pressure_summary())), reply_markup=main_menu())


async def marketpressure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, chain_pressure_summary())), reply_markup=main_menu())


async def mining_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(append_plan_footer(update.effective_user.id, network_stats_summary("mining")), reply_markup=main_menu())


async def difficulty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(append_plan_footer(update.effective_user.id, network_stats_summary("difficulty")), reply_markup=main_menu())


async def networkhealth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(append_plan_footer(update.effective_user.id, network_stats_summary("health")), reply_markup=main_menu())


async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    assets = get_watchlist(user_id)
    msg = "🤖 Auto Signal Engine\n\n"
    for asset in assets:
        price_now, _ = get_best_price(asset)
        if not price_now:
            msg += f"{asset}: live data unavailable\n"
            continue
        save_price_history(asset, price_now)
        signal = smart_market_signal(asset, price_now, is_pro(user_id))
        msg += f"{asset}: {signal['action']} ({signal['confidence']}) at ${price_now:,.2f}\n{signal['reason']}\n\n"
    msg += "Educational only. Not financial advice."
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, msg)), reply_markup=main_menu())


async def markets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(market_board_summary(update.effective_user.id, "top_market_cap"), reply_markup=main_menu())


async def topvolume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(market_board_summary(update.effective_user.id, "top_volume"), reply_markup=main_menu())


async def gainers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(market_board_summary(update.effective_user.id, "gainers"), reply_markup=main_menu())


async def losers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(market_board_summary(update.effective_user.id, "losers"), reply_markup=main_menu())


async def portfolio_live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, portfolio_live_summary(user_id))), reply_markup=main_menu())


async def setbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not context.args:
        await update.message.reply_text("Example: /setbalance 1250")
        return

    try:
        amount = parse_money_amount(" ".join(context.args))
    except Exception:
        await update.message.reply_text("Please send a valid amount. Example: /setbalance 1250")
        return

    set_manual_balance(update.effective_user.id, amount)
    await update.message.reply_text(
        f"✅ Manual portfolio balance set to ${amount:,.2f}.\n\nYour holdings were not changed. Use /clearbalance to return to live holdings value.",
        reply_markup=main_menu()
    )


async def clearbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    clear_manual_balance(update.effective_user.id)
    await update.message.reply_text(
        "✅ Manual balance cleared.\n\n/portfolio_live will now show live tracked holdings value.",
        reply_markup=main_menu()
    )


async def portfolio_advice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "portfolio_advice")
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, portfolio_advice_summary(user_id), 1200)), reply_markup=main_menu())


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(pro_upgrade_message(update.effective_user.id), reply_markup=upgrade_payment_menu(update.effective_user.id))


async def setemail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/setemail you@example.com\n\n"
            "Your email is used for subscription confirmations, account/security notifications, and optional future updates if implemented.",
            reply_markup=main_menu()
        )
        return
    email = context.args[0].strip().lower()
    if not is_valid_email(email):
        await update.message.reply_text(
            "⚠️ That email format does not look valid.\n\n"
            "Try:\n/setemail you@example.com",
            reply_markup=main_menu()
        )
        return
    save_user_email(update.effective_user.id, email)
    await update.message.reply_text(
        "✅ Email saved securely for CoinPilotX account and payment confirmations.\n\n"
        "Your email is never shown publicly.",
        reply_markup=main_menu()
    )


async def myemail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    email = get_user_email(update.effective_user.id)
    if email:
        msg = (
            f"📧 Email on file:\n{email}\n\n"
            "Used for subscription confirmations, account/security notifications, and optional future updates if implemented."
        )
    else:
        msg = (
            "📧 No email is set yet.\n\n"
            "Optional but recommended for payment confirmations:\n/setemail you@example.com"
        )
    await update.message.reply_text(msg, reply_markup=main_menu())


async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    logging.info("Account command clicked by Telegram user %s", update.effective_user.id)
    await update.message.reply_text(account_summary(update.effective_user.id), reply_markup=account_reply_markup(update.effective_user.id))


async def connect_account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not context.args:
        await update.message.reply_text(
            "Connect your website account by logging in at https://coinpilotx.app/account/settings, generating a code, then sending:\n\n/connect CODE",
            reply_markup=account_reply_markup(update.effective_user.id),
        )
        return
    code = clean_html(context.args[0]).upper()
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_id, expires_at, used_at FROM telegram_link_codes WHERE code=? ORDER BY id DESC LIMIT 1",
        (code,)
    )
    row = cur.fetchone()
    if not row or row[3] or row[2] < now:
        conn.close()
        logging.info("Telegram linking failed for user %s", update.effective_user.id)
        await update.message.reply_text(
            "That connection code is invalid or expired. Please generate a new code from Account Settings.",
            reply_markup=account_reply_markup(update.effective_user.id),
        )
        return
    cur.execute(
        """
        UPDATE users
        SET telegram_user_id=?, telegram_username=?, telegram_chat_id=?, updated_at=?, last_seen_at=?
        WHERE user_id=?
        """,
        (
            update.effective_user.id,
            update.effective_user.username or "",
            update.effective_chat.id if update.effective_chat else update.effective_user.id,
            now,
            now,
            row[1],
        )
    )
    cur.execute("UPDATE telegram_link_codes SET used_at=? WHERE id=?", (now, row[0]))
    conn.commit()
    conn.close()
    logging.info("Telegram linking success for Telegram user %s", update.effective_user.id)
    await update.message.reply_text(
        "✅ Telegram connected successfully.\n\n"
        "Your CoinPilotX account is now connected to this Telegram profile. You can now use the Account button anytime to view your plan, subscription, preferences, and account status.",
        reply_markup=account_reply_markup(update.effective_user.id),
    )


async def help_account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👤 CoinPilotX Account Help\n\n"
        "Create account: https://coinpilotx.app/signup\n"
        "Login: https://coinpilotx.app/login\n"
        "Dashboard: https://coinpilotx.app/account\n\n"
        "To connect Telegram:\n"
        "1. Log in on the website.\n"
        "2. Open Account Settings.\n"
        "3. Tap Connect Telegram Bot.\n"
        "4. Send the generated code here with /connect CODE.\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.",
        reply_markup=account_reply_markup(update.effective_user.id),
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("Admin access is not enabled for this account.")
        return
    await update.message.reply_text(admin_summary(), reply_markup=main_menu())


async def voice_assistant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(
        "🎙️ Voice Assistant\n\nI can receive voice notes now. Speech-to-text is not connected yet, so send the same question as text and I’ll answer it here.",
        reply_markup=main_menu()
    )


async def whale_alert_job(context: ContextTypes.DEFAULT_TYPE):
    new_alerts = []
    try:
        scan_btc_whales()
    except Exception:
        pass
    for asset in ["BTC", "ETH"]:
        for alert in get_whale_activity(asset):
            save_whale_alert(alert)
            new_alerts.append(alert)

    if not new_alerts:
        return

    users = get_users_with_alerts()
    if not users:
        return

    alert_lines = []
    for alert in new_alerts[:3]:
        alert_lines.append(
            f"{alert['asset']}: {alert['side']} about ${alert['notional']:,.0f} near ${alert['price']:,.2f}"
        )
    msg = "🐋 Whale Alert\n\n" + "\n".join(alert_lines) + "\n\nEducational only. Not financial advice."

    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=msg)
        except Exception:
            continue


async def portfolio_tracking_job(context: ContextTypes.DEFAULT_TYPE):
    for user_id in get_all_users():
        try:
            portfolio_live_summary(user_id)
        except Exception:
            continue


def activate_pro(user_id, payment_type=None, stripe_customer_id=None, stripe_session_id=None, subscription_status="active"):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET is_pro=1,
            subscription_plan='pro',
            plan='pro',
            subscription_status=?,
            subscription_started_at=?,
            updated_at=?,
            last_payment_type=COALESCE(?, last_payment_type),
            stripe_customer_id=COALESCE(?, stripe_customer_id),
            stripe_session_id=COALESCE(?, stripe_session_id)
        WHERE user_id=? OR telegram_user_id=?
        """,
        (subscription_status, datetime.now().isoformat(), datetime.now().isoformat(), payment_type, stripe_customer_id, stripe_session_id, user_id, user_id)
    )
    conn.commit()
    conn.close()


DAY_SIGNAL_QUESTIONS = [
    {
        "key": "feeling",
        "question": "How do you feel today?",
        "options": [
            ("confident", "Confident"),
            ("calm", "Calm"),
            ("nervous", "Nervous"),
            ("tired", "Tired"),
        ],
    },
    {
        "key": "prepared",
        "question": "How prepared are you for what you want to do today?",
        "options": [
            ("very_prepared", "Very prepared"),
            ("somewhat_prepared", "Somewhat prepared"),
            ("not_prepared", "Not prepared"),
            ("guessing", "I'm guessing"),
        ],
    },
    {
        "key": "opportunity",
        "question": "What kind of opportunity are you thinking about?",
        "options": [
            ("crypto", "Crypto/Trading"),
            ("sports", "Sports Edge"),
            ("business", "Business/Money"),
            ("personal", "Personal decision"),
        ],
    },
    {
        "key": "walkaway",
        "question": "Are you willing to walk away if the signal looks risky?",
        "options": [
            ("yes", "Yes"),
            ("maybe", "Maybe"),
            ("no", "No"),
        ],
    },
]


def build_day_signal(answers):
    if not isinstance(answers, dict):
        return {"ok": False, "response": "Answer the Day Signal questions first."}

    normalized = {}
    for question in DAY_SIGNAL_QUESTIONS:
        value = str(answers.get(question["key"], "")).strip().lower()
        valid = {option_key for option_key, _ in question["options"]}
        if value not in valid:
            return {"ok": False, "response": f"Please answer: {question['question']}"}
        normalized[question["key"]] = value

    score = (
        {"confident": 22, "calm": 25, "nervous": 12, "tired": 8}[normalized["feeling"]]
        + {"very_prepared": 35, "somewhat_prepared": 24, "not_prepared": 8, "guessing": 4}[normalized["prepared"]]
        + {"crypto": 14, "sports": 12, "business": 17, "personal": 18}[normalized["opportunity"]]
        + {"yes": 22, "maybe": 12, "no": 0}[normalized["walkaway"]]
    )
    score = max(0, min(100, score))

    if score >= 80:
        signal = "Strong"
    elif score >= 62:
        signal = "Moderate"
    elif score >= 42:
        signal = "Caution"
    else:
        signal = "Not Today"

    opportunity_copy = {
        "crypto": {
            "name": "Crypto/Trading",
            "best": "Review your plan, risk limit, entry reason, and exit rule before touching real money.",
            "avoid": "Avoid revenge trading, oversized positions, leverage, or chasing a move because it already started.",
        },
        "sports": {
            "name": "Sports Edge",
            "best": "Compare the matchup data, price, timing, and risk. If the edge is unclear, waiting is a valid position.",
            "avoid": "Avoid forcing a position just because a game is live or because you want action today.",
        },
        "business": {
            "name": "Business/Money",
            "best": "Take one researched step, write down the downside, and choose the smallest move that still teaches you something.",
            "avoid": "Avoid rushed agreements, emotional spending, or decisions you cannot calmly explain tomorrow.",
        },
        "personal": {
            "name": "Personal decision",
            "best": "Choose the clearest next step, slow the conversation down, and keep the decision reversible if possible.",
            "avoid": "Avoid making high-stakes choices from fatigue, pressure, pride, or fear of missing out.",
        },
    }[normalized["opportunity"]]

    if normalized["prepared"] in {"not_prepared", "guessing"}:
        ai_message = "Your signal is asking for more preparation before action. The goal today is not bravery. It is clarity."
    elif normalized["walkaway"] == "no":
        ai_message = "Your drive is active, but discipline is the missing protection layer. A strong day still needs a stop point."
    elif normalized["feeling"] == "tired":
        ai_message = "Your energy looks low, so treat today as a risk-control day. Smaller, slower decisions can protect your future options."
    elif signal == "Strong":
        ai_message = "You look emotionally steady and prepared. Keep the confidence grounded in a plan, not impulse."
    elif signal == "Moderate":
        ai_message = "There may be room to move, but the signal favors patience, confirmation, and controlled sizing."
    elif signal == "Caution":
        ai_message = "The better win today may be avoiding a bad decision. Slow down and let the setup prove itself."
    else:
        ai_message = "Today does not look aligned for pressure-based action. Reset, prepare, and come back with a cleaner mind."

    if signal == "Not Today":
        best_move = "Pause the high-stakes move. Prepare, review, and revisit when your readiness is higher."
    elif signal == "Caution":
        best_move = f"Use a checklist first. {opportunity_copy['best']}"
    else:
        best_move = opportunity_copy["best"]

    disclaimer = "This is motivational and educational insight only. It is not financial, betting, or life advice."
    response = (
        "✨ CoinPilotX Day Signal\n\n"
        f"Today's Day Score: {score}/100\n\n"
        f"Signal: {signal}\n\n"
        f"AI Message:\n{ai_message}\n\n"
        f"Best Move Today:\n{best_move}\n\n"
        f"Avoid Today:\n{opportunity_copy['avoid']}\n\n"
        f"Disclaimer:\n{disclaimer}"
    )

    return {
        "ok": True,
        "score": score,
        "signal": signal,
        "message": ai_message,
        "best_move": best_move,
        "avoid": opportunity_copy["avoid"],
        "opportunity": opportunity_copy["name"],
        "response": response,
        "disclaimer": disclaimer,
    }


def day_signal_question_menu(step):
    question = DAY_SIGNAL_QUESTIONS[step]
    rows = [[InlineKeyboardButton(label, callback_data=f"daysignal_{step}_{option_key}")] for option_key, label in question["options"]]
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


async def send_day_signal_question(message, step):
    question = DAY_SIGNAL_QUESTIONS[step]
    await message.reply_text(
        f"✨ CoinPilotX Day Signal\n\nQuestion {step + 1} of {len(DAY_SIGNAL_QUESTIONS)}:\n{question['question']}",
        reply_markup=day_signal_question_menu(step),
    )


async def daysignal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "day_signal")
    context.user_data["day_signal_answers"] = {}
    await send_day_signal_question(update.message, 0)


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        [
            InlineKeyboardButton("📈 Live BTC", callback_data="menu_price_btc"),
            InlineKeyboardButton("📊 Live Market", callback_data="menu_live_markets"),
        ],
        [
            InlineKeyboardButton("📊 BTC/ETH Charts", callback_data="menu_chart_btc"),
            InlineKeyboardButton("🎲 Live Sports Edge", callback_data="menu_sports_edge"),
        ],
        [
            InlineKeyboardButton("🧠 AI Analysis", callback_data="menu_analysis_btc"),
            InlineKeyboardButton("🤖 Auto Signals", callback_data="menu_signals"),
        ],
        [InlineKeyboardButton("💬 AI Crypto Assistant", callback_data="menu_ai_assistant")],
        [InlineKeyboardButton("✨ Is Today My Day?", callback_data="menu_day_signal")],
        [
            InlineKeyboardButton("📰 Crypto News", callback_data="menu_crypto_news"),
            InlineKeyboardButton("🌍 Market Events", callback_data="menu_market_events"),
        ],
        [
            InlineKeyboardButton("🧠 Crypto Wisdom", callback_data="menu_wisdom"),
            InlineKeyboardButton("🛡 Scam Stories", callback_data="menu_scam_stories"),
        ],
        [
            InlineKeyboardButton("🌍 Country News", callback_data="menu_country_news"),
            InlineKeyboardButton("🎲 Sports Edge", callback_data="menu_sports_edge"),
        ],
        [
            InlineKeyboardButton("🐋 Whale Alerts", callback_data="menu_whales"),
            InlineKeyboardButton("😬 Fear/Greed", callback_data="menu_feargreed"),
        ],
        [
            InlineKeyboardButton("🧬 Chain Intel", callback_data="menu_chain_intel"),
            InlineKeyboardButton("📡 BTC Network", callback_data="menu_btc_network"),
        ],
        [
            InlineKeyboardButton("🔎 TX Explorer", callback_data="menu_tx_explorer"),
            InlineKeyboardButton("👛 Wallet Intel", callback_data="menu_wallet_intel"),
        ],
        [
            InlineKeyboardButton("💼 Live Portfolio", callback_data="menu_portfolio_live"),
            InlineKeyboardButton("🧭 Portfolio Advice", callback_data="menu_portfolio_advice"),
        ],
        [InlineKeyboardButton("👤 Account", callback_data="menu_account")],
        [
            InlineKeyboardButton("🛡️ Scam Shield", callback_data="pro_scanner"),
            InlineKeyboardButton("💳 Upgrade Pro", callback_data="upgrade_pro"),
        ],
        [
            InlineKeyboardButton("💬 Chat Assistant", callback_data="menu_talk"),
            InlineKeyboardButton("🏦 Best Exchanges", callback_data="menu_exchanges"),
        ],
        [
            InlineKeyboardButton("💰 Add Money Safely", callback_data="menu_deposit"),
            InlineKeyboardButton("📘 Help", callback_data="menu_help"),
        ],
    ])


def exchange_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Beginner", callback_data="exchange_beginner"),
            InlineKeyboardButton("Low fees", callback_data="exchange_lowfees"),
        ],
        [
            InlineKeyboardButton("Security", callback_data="exchange_security"),
            InlineKeyboardButton("Mobile", callback_data="exchange_mobile"),
        ],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")],
    ])


async def send_exchange_message(message, goal):
    await message.reply_text(exchange_recommendation(goal), reply_markup=main_menu())


async def cryptonews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "crypto_news")
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, crypto_news_summary(user_id))), reply_markup=main_menu())


async def marketevents_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    event_text = " ".join(context.args).strip()
    log_engagement(update.effective_user.id, "market_events", event_text)
    user_id = update.effective_user.id
    await update.message.reply_text(
        append_plan_footer(user_id, maybe_limit_for_free(user_id, market_events_summary(event_text, is_pro(user_id)))),
        reply_markup=main_menu()
    )


async def wisdom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "wisdom")
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, daily_wisdom()), reply_markup=main_menu())


async def scamstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "scam_stories")
    user_id = update.effective_user.id
    await update.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, scam_stories_summary(is_pro(user_id)))), reply_markup=main_menu())


async def countrynews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    country = " ".join(context.args).strip() if context.args else "Haiti"
    log_engagement(update.effective_user.id, "country_news", country)
    user_id = update.effective_user.id
    await update.message.reply_text(
        append_plan_footer(user_id, maybe_limit_for_free(user_id, country_news_summary(country, is_pro(user_id)))),
        reply_markup=main_menu()
    )


async def sportsedge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "sports_edge")
    await update.message.reply_text(sports_edge_summary(update.effective_user.id), reply_markup=sports_edge_menu())


async def livegames_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "live_games")
    await update.message.reply_text(sports_edge_summary(update.effective_user.id), reply_markup=sports_edge_menu())


async def gameedge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "🎲 Game Edge\n\nUse /gameedge GAME_ID after opening /sportsedge.\n\n"
            f"{SPORTS_SAFETY_LINE}",
            reply_markup=sports_edge_menu()
        )
        return
    game_id = context.args[0].strip()
    log_engagement(user_id, "game_edge", game_id)
    await update.message.reply_text(sports_edge_game_summary(game_id, user_id), reply_markup=sports_edge_menu())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "menu_main":
        await query.message.reply_text("🏠 Main Menu\n\nChoose what you want to do next:", reply_markup=main_menu())
        return

    if data == "menu_price_btc":
        price_now, _ = get_best_price("BTC")
        if price_now:
            save_price_history("BTC", price_now)
            signal = smart_market_signal("BTC", price_now, is_pro(user_id))
            await query.message.reply_text(
                f"📈 BTC Live Price\n\nBTC: ${price_now:,.2f}\nSignal: {signal['action']} ({signal['confidence']})\n\n{signal['reason']}\n\nEducational only. Not financial advice.",
                reply_markup=main_menu()
            )
        else:
            await query.message.reply_text("BTC price is unavailable right now.", reply_markup=main_menu())
        return

    if data == "menu_live_markets":
        log_engagement(user_id, "live_markets")
        await query.message.reply_text(market_board_summary(user_id, "top_volume"), reply_markup=main_menu())
        return

    if data == "menu_chart_btc":
        url = chart_url("BTC")
        if url:
            await query.message.reply_photo(photo=url, caption="📊 BTC live 24h chart", reply_markup=main_menu())
        else:
            await query.message.reply_text("I could not build the BTC chart right now.", reply_markup=main_menu())
        return

    if data == "menu_analysis_btc":
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, ai_crypto_analysis(user_id, "BTC"))), reply_markup=main_menu())
        return

    if data == "menu_ai_assistant":
        await query.message.reply_text(
            append_plan_footer(user_id, "💬 AI Crypto Assistant\n\nAsk a question with /ask, or just send me a normal message.\n\nI can help with crypto, scams, blockchain, portfolio thinking, and safer financial literacy.\n\nPowered by CoinPilotXAI Inc."),
            reply_markup=main_menu()
        )
        return

    if data == "menu_day_signal":
        log_engagement(user_id, "day_signal")
        context.user_data["day_signal_answers"] = {}
        await send_day_signal_question(query.message, 0)
        return

    if data.startswith("daysignal_"):
        parts = data.split("_", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            await query.message.reply_text("Let's restart the Day Signal check.", reply_markup=main_menu())
            return
        step = int(parts[1])
        answer = parts[2]
        if step < 0 or step >= len(DAY_SIGNAL_QUESTIONS):
            await query.message.reply_text("Let's restart the Day Signal check.", reply_markup=main_menu())
            return

        question = DAY_SIGNAL_QUESTIONS[step]
        valid = {option_key for option_key, _ in question["options"]}
        if answer not in valid:
            await query.message.reply_text("That answer was not recognized. Please try again.", reply_markup=day_signal_question_menu(step))
            return

        answers = context.user_data.setdefault("day_signal_answers", {})
        answers[question["key"]] = answer
        next_step = step + 1
        if next_step < len(DAY_SIGNAL_QUESTIONS):
            await send_day_signal_question(query.message, next_step)
            return

        result = build_day_signal(answers)
        context.user_data.pop("day_signal_answers", None)
        await query.message.reply_text(result["response"], reply_markup=main_menu())
        return

    if data == "menu_crypto_news":
        log_engagement(user_id, "crypto_news")
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, crypto_news_summary(user_id))), reply_markup=main_menu())
        return

    if data == "menu_market_events":
        log_engagement(user_id, "market_events")
        await query.message.reply_text(
            append_plan_footer(user_id, maybe_limit_for_free(user_id, market_events_summary("", is_pro(user_id)))),
            reply_markup=main_menu()
        )
        return

    if data == "menu_wisdom":
        log_engagement(user_id, "wisdom")
        await query.message.reply_text(append_plan_footer(user_id, daily_wisdom()), reply_markup=main_menu())
        return

    if data == "menu_scam_stories":
        log_engagement(user_id, "scam_stories")
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, scam_stories_summary(is_pro(user_id)))), reply_markup=main_menu())
        return

    if data == "menu_country_news":
        await query.message.reply_text("🌍 Choose a country for crypto intelligence:", reply_markup=country_picker_menu())
        return

    if data.startswith("countrynews_"):
        country = data.replace("countrynews_", "", 1)
        log_engagement(user_id, "country_news", country)
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, country_news_summary(country, is_pro(user_id)))), reply_markup=main_menu())
        return

    if data == "menu_sports_edge":
        log_engagement(user_id, "sports_edge")
        await query.message.reply_text(sports_edge_summary(user_id), reply_markup=sports_edge_menu())
        return

    if data.startswith("sportsedge_"):
        log_engagement(user_id, "sports_edge_game", data)
        game_id = data.replace("sportsedge_", "", 1).replace("_", ":", 1)
        await query.message.reply_text(sports_edge_game_summary(game_id, user_id), reply_markup=sports_edge_menu())
        return

    if data == "menu_signals":
        msg = "🤖 Auto Signal Engine\n\n"
        for asset in get_watchlist(user_id):
            price_now, _ = get_best_price(asset)
            if not price_now:
                msg += f"{asset}: live data unavailable\n"
                continue
            save_price_history(asset, price_now)
            signal = smart_market_signal(asset, price_now, is_pro(user_id))
            msg += f"{asset}: {signal['action']} ({signal['confidence']}) at ${price_now:,.2f}\n{signal['reason']}\n\n"
        msg += "Educational only. Not financial advice."
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, msg)), reply_markup=main_menu())
        return

    if data == "menu_whales":
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, whale_intelligence_summary(scan_live=True))), reply_markup=main_menu())
        return

    if data == "menu_feargreed":
        fear = get_fear_greed()
        value = "Unavailable" if fear["value"] is None else str(fear["value"])
        await query.message.reply_text(
            append_plan_footer(user_id, f"😬 Market Fear/Greed AI\n\nScore: {value}\nMood: {fear['label']}\n\n{fear['advice']}\n\nEducational only — not financial advice."),
            reply_markup=main_menu()
        )
        return

    if data == "menu_chain_intel":
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, chain_pressure_summary())), reply_markup=main_menu())
        return

    if data == "menu_btc_network":
        await query.message.reply_text(network_stats_summary("network"), reply_markup=main_menu())
        return

    if data == "menu_tx_explorer":
        await query.message.reply_text(
            "🔎 TX Explorer\n\nSend a public BTC transaction hash or public BTC address:\n/checktx TXID_OR_ADDRESS\n\nNever send private keys, seed phrases, recovery phrases, or wallet passwords.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_wallet_intel":
        await query.message.reply_text(
            "👛 Wallet Intelligence\n\nConnect or inspect a public BTC address only:\n/connectwallet bc1...\n/walletinfo bc1...\n/walletscan bc1...\n\nNever send seed phrases, private keys, recovery phrases, or wallet passwords.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_portfolio_live":
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, portfolio_live_summary(user_id))), reply_markup=main_menu())
        return

    if data == "menu_portfolio_advice":
        log_engagement(user_id, "portfolio_advice")
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, portfolio_advice_summary(user_id), 1200)), reply_markup=main_menu())
        return

    if data == "menu_account":
        logging.info("Account button clicked by Telegram user %s", user_id)
        await query.message.reply_text(account_summary(user_id), reply_markup=account_reply_markup(user_id))
        return

    if data == "menu_alerts_on":
        set_alerts(user_id, True)
        await query.message.reply_text("🚨 Alerts are now ON.", reply_markup=main_menu())
        return

    if data == "pro_signals":
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, ai_crypto_analysis(user_id, "BTC"))), reply_markup=main_menu())
        return

    if data == "pro_portfolio":
        await query.message.reply_text(append_plan_footer(user_id, maybe_limit_for_free(user_id, portfolio_live_summary(user_id))), reply_markup=main_menu())
        return

    if data == "pro_scanner":
        await query.message.reply_text(
            "🛡 Scam Shield\n\nSend me any suspicious crypto message, wallet address, or link and I’ll scan it.",
            reply_markup=main_menu()
        )
        return

    if data == "upgrade_pro":
        await query.message.reply_text(pro_upgrade_message(user_id), reply_markup=upgrade_payment_menu(user_id))
        return

    if data == "pay_btc":
        await query.message.reply_text(
            f"₿ Pay with Bitcoin\n\nSend exactly: {BTC_PRO_PRICE}\n\nBTC address:\n{BTC_PAYMENT_ADDRESS}\n\n"
            "For faster activation:\n"
            "1. Send payment\n"
            "2. Type:\n   /verify_payment YOUR_TXID\n"
            "3. CoinPilotX will verify the transaction and activate Pro if confirmed.\n\n"
            "Optional but recommended:\n"
            "Set your email for payment confirmations:\n  /setemail you@example.com\n\n"
            "CoinPilotX is operated by CoinPilotXAI Inc.\n"
            "CoinPilotX will never ask for your seed phrase or private key.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_talk":
        await query.message.reply_text(
            append_plan_footer(user_id, "💬 AI Crypto Assistant\n\nAsk me a crypto question in plain English, or send a suspicious message and I’ll help you inspect it.\n\nPowered by CoinPilotXAI Inc."),
            reply_markup=main_menu()
        )
        return

    if data == "menu_about":
        await query.message.reply_text(
            "ℹ️ About CoinPilotX\n\nCoinPilotX helps users understand live crypto prices, signals, portfolio movement, whale activity, exchange choices, and scam risks.\n\nCoinPilotX is powered by CoinPilotXAI Inc.\n\nCoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_deposit":
        await query.message.reply_text(
            "💰 Add Money Safely\n\nCoinPilotX is operated by CoinPilotXAI Inc. CoinPilotX does not hold money or accept deposits.\nUse official exchanges directly and never send funds to someone promising certain returns.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_exchanges":
        await query.message.reply_text("🏦 Best Exchanges\n\nChoose your goal:", reply_markup=exchange_menu())
        return

    if data.startswith("exchange_"):
        await send_exchange_message(query.message, data.replace("exchange_", ""))
        return

    if data == "menu_help":
        await query.message.reply_text(help_message(), reply_markup=main_menu())
        return

    if data in ["scan_confirm", "scan_yes"]:
        text = context.user_data.get("pending_scan")
        if not text:
            await query.message.reply_text("Nothing to scan right now.", reply_markup=main_menu())
            return

        risk, reasons, expanded_results = analyze_text(text)
        msg = f"🛡️ Message Safety Check\n\nRisk Level: {risk}\n\n"
        if expanded_results:
            msg += "Links checked:\n"
            for original, expanded in expanded_results:
                msg += f"• Original: {original}\n  Destination: {expanded}\n\n"
        msg += "Findings:\n"
        msg += "\n".join([f"• {r}" for r in reasons]) if reasons else "• No major scam signs found."
        context.user_data.pop("pending_scan", None)
        await query.message.reply_text(msg, reply_markup=main_menu())
        return

    if data in ["scan_cancel", "scan_no"]:
        context.user_data.pop("pending_scan", None)
        await query.message.reply_text("Scan canceled.", reply_markup=main_menu())
        return

    await query.message.reply_text("🏠 Back to menu.", reply_markup=main_menu())


# =========================
# MENU PUSH JOB
# =========================

async def send_menu_job(context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()

    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="📊 CoinPilotX Update\n\nStay ahead of the market.\nTap below:",
                reply_markup=main_menu()
            )
        except Exception:
            continue
def run_webhook():
    PORT = int(os.getenv("PORT", 5050))
    webhook_app.run(host="0.0.0.0", port=PORT)


def main():
    init_db()
    print(f"{BOT_NAME} starting...")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    job_queue = app.job_queue

    # MENU PUSH EVERY 3 HOURS
    job_queue.run_repeating(
        send_menu_job,
        interval=10800,
        first=30
    )

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("track", track))
    app.add_handler(CommandHandler("watchlist", show_watchlist))
    app.add_handler(CommandHandler("alerts_on", alerts_on))
    app.add_handler(CommandHandler("alerts_off", alerts_off))
    app.add_handler(CommandHandler("addholding", addholding))
    app.add_handler(CommandHandler("removeholding", removeholding))
    app.add_handler(CommandHandler("myportfolio", myportfolio))
    app.add_handler(CommandHandler("paper", paper))
    app.add_handler(CommandHandler("deposit", deposit))
    app.add_handler(CommandHandler("exchange", exchange))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("verify_payment", verify_payment))
    app.add_handler(CommandHandler("analysis", analysis_command))
    app.add_handler(CommandHandler("markets", markets_command))
    app.add_handler(CommandHandler("topvolume", topvolume_command))
    app.add_handler(CommandHandler("gainers", gainers_command))
    app.add_handler(CommandHandler("losers", losers_command))
    app.add_handler(CommandHandler("daysignal", daysignal_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("feargreed", feargreed_command))
    app.add_handler(CommandHandler("whales", whales_command))
    app.add_handler(CommandHandler("whalebtc", whalebtc_command))
    app.add_handler(CommandHandler("whalealerts", whalealerts_command))
    app.add_handler(CommandHandler("btcstats", btcstats_command))
    app.add_handler(CommandHandler("network", network_command))
    app.add_handler(CommandHandler("fees", fees_command))
    app.add_handler(CommandHandler("mempool", mempool_command))
    app.add_handler(CommandHandler("checktx", checktx_command))
    app.add_handler(CommandHandler("connectwallet", connectwallet_command))
    app.add_handler(CommandHandler("walletinfo", walletinfo_command))
    app.add_handler(CommandHandler("walletscan", walletscan_command))
    app.add_handler(CommandHandler("scamintel", scamintel_command))
    app.add_handler(CommandHandler("chainintel", chainintel_command))
    app.add_handler(CommandHandler("marketpressure", marketpressure_command))
    app.add_handler(CommandHandler("mining", mining_command))
    app.add_handler(CommandHandler("difficulty", difficulty_command))
    app.add_handler(CommandHandler("networkhealth", networkhealth_command))
    app.add_handler(CommandHandler("signals", signals_command))
    app.add_handler(CommandHandler("portfolio_live", portfolio_live_command))
    app.add_handler(CommandHandler("setbalance", setbalance_command))
    app.add_handler(CommandHandler("clearbalance", clearbalance_command))
    app.add_handler(CommandHandler("portfolio_advice", portfolio_advice_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("setemail", setemail_command))
    app.add_handler(CommandHandler("myemail", myemail_command))
    app.add_handler(CommandHandler("account", account_command))
    app.add_handler(CommandHandler("connect", connect_account_command))
    app.add_handler(CommandHandler("help_account", help_account_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("cryptonews", cryptonews_command))
    app.add_handler(CommandHandler("marketevents", marketevents_command))
    app.add_handler(CommandHandler("wisdom", wisdom_command))
    app.add_handler(CommandHandler("scamstories", scamstories_command))
    app.add_handler(CommandHandler("countrynews", countrynews_command))
    app.add_handler(CommandHandler("sportsedge", sportsedge_command))
    app.add_handler(CommandHandler("livegames", livegames_command))
    app.add_handler(CommandHandler("gameedge", gameedge_command))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_assistant))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue.run_repeating(market_signal_job, interval=SIGNAL_CHECK_SECONDS, first=10)
    job_queue.run_repeating(hourly_market_update, interval=HOURLY_UPDATE_SECONDS, first=30)
    job_queue.run_repeating(whale_alert_job, interval=WHALE_CHECK_SECONDS, first=45)
    job_queue.run_repeating(portfolio_tracking_job, interval=PORTFOLIO_TRACK_SECONDS, first=60)

    app.run_polling()


if __name__ == "__main__":
    threading.Thread(target=run_webhook, daemon=True).start()
    main()
