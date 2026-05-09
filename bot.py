# =========================
# IMPORTS
# =========================

import os
import re
import json
import math
import sqlite3
import logging
import requests
import threading
import stripe
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
from urllib.parse import urlparse
from flask import Flask, request, render_template, send_from_directory

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

BTC_PAYMENT_ADDRESS = "0x8DE1A7eAb2C937cdCdC24E8F79B0ac0960040CD8"
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
ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().isdigit()
}

# =========================
# 🌐 WEBHOOK APP (RIGHT AFTER STRIPE)
# =========================
webhook_app = Flask(__name__, template_folder="templates", static_folder="static")
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
        "No hidden fees from CoinPilotX. Card payment opens only through the secure button below.\n"
        "CoinPilotX never holds funds.\n\n"
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
app = webhook_app


@webhook_app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@webhook_app.route("/robots.txt", methods=["GET"])
def robots_txt():
    return send_from_directory(webhook_app.static_folder, "robots.txt", mimetype="text/plain")


@webhook_app.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    return send_from_directory(webhook_app.static_folder, "sitemap.xml", mimetype="application/xml")


@webhook_app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
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

        print("client_reference_id:", user_id)

        if user_id:
            activate_pro(int(user_id))
            print(f"✅ Activated PRO for user {user_id}")

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
def verify_btc_tx(txid):
    try:
        url = BLOCKSTREAM_TX_API + txid
        tx = requests.get(url, timeout=10).json()

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

BTC_PAYMENT_ADDRESS = "0x8DE1A7eAb2C937cdCdC24E8F79B0ac0960040CD8"
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
        "No hidden fees from CoinPilotX. Card payment opens only through the secure button below.\n"
        "CoinPilotX never holds funds.\n\n"
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


def get_best_price(asset):
    prices = {}

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

    if not onboarding_complete(user_id):
        onboarding_state[user_id] = "name"
        await update.message.reply_text(
            f"🚀 Welcome to {BOT_NAME}.\n\nHow do you want me to call you?"
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
        "CoinPilotX is a crypto safety and market intelligence assistant founded by Roody Cherie. "
        "It is built to help everyday users understand crypto prices, alerts, portfolio movement, "
        "and scam risks in simple language.\n\n"
        "The system can check market prices, provide BUY / SELL / WAIT-style educational signals, "
        "help users track a manual crypto portfolio, and scan suspicious crypto messages or links "
        "before users trust them.\n\n"
        "CoinPilotX does not hold user funds, create exchange accounts, or guarantee profits. "
        "All information is educational only and should not be treated as financial advice.\n\n"
        "Powered by OpenAI + CoinPilotX crypto intelligence.",
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
        txid = context.args[0]
    except Exception:
        await update.message.reply_text(
            "Usage:\n/verify_payment YOUR_TXID",
            reply_markup=main_menu()
        )
        return

    is_paid, paid_sats, confirmations = verify_btc_tx(txid)

    if is_paid:
        activate_pro(update.effective_user.id)
        await update.message.reply_text(
            "✅ BTC payment verified.\n\n"
            "Your CoinPilotX Pro access is now active.",
            reply_markup=main_menu()
        )
    else:
        await update.message.reply_text(
            "⏳ Payment not verified yet.\n\n"
            f"Detected: {paid_sats} sats\n"
            f"Confirmations: {confirmations}\n\n"
            "Make sure you sent the correct BTC amount to the correct address, then try again later.",
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
        "red_flags": ["Guaranteed returns", "A stranger chooses the platform", "Small first withdrawal followed by bigger pressure", "Extra fee required before withdrawal"],
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

    conn.commit()
    conn.close()


def help_message():
    return (
        "ℹ️ CoinPilotX Help\n\n"
        "/price BTC — live price and signal\n"
        "/chart BTC — real BTC/ETH live chart\n"
        "/analysis BTC — AI-style crypto analysis\n"
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
        "/sportsedge — experimental sports section\n"
        "/subscribe — Pro subscription options\n"
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
        "You are CoinPilotX, a premium crypto intelligence assistant powered by OpenAI + CoinPilotX crypto intelligence. "
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

    answer += "\n\nPowered by OpenAI + CoinPilotX crypto intelligence."

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


def sports_edge_summary():
    if not SPORTS_EDGE_ENABLED:
        return (
            "🎲 Sports Edge\n\n"
            "This section is experimental and disabled by default for compliance safety.\n\n"
            "When enabled, it should stay informational only: no promised bets, no outcome claims, and no financial claims."
        )
    return (
        "🎲 Sports Edge\n\n"
        "Experimental read: compare team form, injuries, schedule fatigue, line movement, and bankroll risk before making any decision.\n\n"
        "Informational only. No promised bets or promised wins."
    )


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
    conn.close()

    return (
        "🛠 Admin Dashboard\n\n"
        f"Users: {users or 0}\n"
        f"Pro users: {pro_users or 0}\n"
        f"Alerts enabled: {alert_users or 0}\n"
        f"Saved alerts: {alert_count}\n"
        f"Whale alerts: {whale_count}\n"
        f"Portfolio snapshots: {snapshot_count}"
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
            <title>CoinPilotX Admin</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; color: #17211f; }}
                .panel {{ max-width: 720px; border: 1px solid #d8e2df; border-radius: 8px; padding: 24px; }}
                h1 {{ margin-top: 0; }}
            </style>
        </head>
        <body>
            <div class="panel">
                <h1>CoinPilotX Admin</h1>
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


async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(account_summary(update.effective_user.id), reply_markup=main_menu())


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


def activate_pro(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET is_pro=1,
            subscription_plan='pro',
            subscription_status='active',
            subscription_started_at=?
        WHERE user_id=?
        """,
        (datetime.now().isoformat(), user_id)
    )
    conn.commit()
    conn.close()


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        [
            InlineKeyboardButton("📈 Live BTC", callback_data="menu_price_btc"),
            InlineKeyboardButton("📊 BTC/ETH Charts", callback_data="menu_chart_btc"),
        ],
        [
            InlineKeyboardButton("🧠 AI Analysis", callback_data="menu_analysis_btc"),
            InlineKeyboardButton("🤖 Auto Signals", callback_data="menu_signals"),
        ],
        [InlineKeyboardButton("💬 AI Crypto Assistant", callback_data="menu_ai_assistant")],
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
    await update.message.reply_text(sports_edge_summary(), reply_markup=main_menu())


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
            append_plan_footer(user_id, "💬 AI Crypto Assistant\n\nAsk a question with /ask, or just send me a normal message.\n\nI can help with crypto, scams, blockchain, portfolio thinking, and safer financial literacy.\n\nPowered by OpenAI + CoinPilotX crypto intelligence."),
            reply_markup=main_menu()
        )
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
        await query.message.reply_text(sports_edge_summary(), reply_markup=main_menu())
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
        await query.message.reply_text(account_summary(user_id), reply_markup=main_menu())
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
            f"₿ Pay with Bitcoin\n\nSend exactly: {BTC_PRO_PRICE}\n\nBTC address:\n{BTC_PAYMENT_ADDRESS}\n\nAfter sending, type:\n/verify_payment YOUR_TXID",
            reply_markup=main_menu()
        )
        return

    if data == "menu_talk":
        await query.message.reply_text(
            append_plan_footer(user_id, "💬 AI Crypto Assistant\n\nAsk me a crypto question in plain English, or send a suspicious message and I’ll help you inspect it.\n\nPowered by OpenAI + CoinPilotX crypto intelligence."),
            reply_markup=main_menu()
        )
        return

    if data == "menu_about":
        await query.message.reply_text(
            "ℹ️ About CoinPilotX\n\nCoinPilotX helps users understand live crypto prices, signals, portfolio movement, whale activity, exchange choices, and scam risks.\n\nPowered by OpenAI + CoinPilotX crypto intelligence.\n\nEducational only. Not financial advice.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_deposit":
        await query.message.reply_text(
            "💰 Add Money Safely\n\nCoinPilotX does not hold money or accept deposits.\nUse official exchanges directly and never send funds to someone promising certain returns.",
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
    app.add_handler(CommandHandler("account", account_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("cryptonews", cryptonews_command))
    app.add_handler(CommandHandler("marketevents", marketevents_command))
    app.add_handler(CommandHandler("wisdom", wisdom_command))
    app.add_handler(CommandHandler("scamstories", scamstories_command))
    app.add_handler(CommandHandler("countrynews", countrynews_command))
    app.add_handler(CommandHandler("sportsedge", sportsedge_command))

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
