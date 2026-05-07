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
from flask import Flask, request

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
webhook_app = Flask(__name__)

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
        "What you unlock:\n"
        "• Smarter BUY / SELL / WAIT signals\n"
        "• Portfolio-aware alerts\n"
        "• Advanced scam protection\n"
        "• Better timing insights\n\n"
        "Choose a payment method below."
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

webhook_app = Flask(__name__)
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
    stripe_link = f"{STRIPE_PRO_LINK}?client_reference_id={user_id}"

    return (
        "⭐ CoinPilotX Pro\n\n"
        f"Card price: {PRO_PRICE_MONTHLY}\n\n"
        "What you unlock:\n"
        "• Smarter BUY / SELL / WAIT signals\n"
        "• Portfolio-aware alerts\n"
        "• Advanced scam protection\n"
        "• Better timing insights\n\n"
        "💳 Pay with card:\n"
        f"{stripe_link}\n\n"
        "After payment, Pro activates automatically."
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
        "All information is educational only and should not be treated as financial advice.",
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

    text = update.message.text.strip().lower()

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
        await conversational_reply(update, context)
        return

    # =========================
    # DEFAULT FALLBACK
    # =========================
    context.user_data["pending_scan"] = text

    await update.message.reply_text(
        "Do you want me to scan this message for crypto scam risk?",
        reply_markup=scan_confirm_menu()
    )

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
        "title": "Fake airdrop wallet drain",
        "story": "A user connects a wallet to claim a free token, approves a malicious contract, and loses assets.",
        "avoid": "Use a burner wallet for claims, verify official links, and never approve unlimited access casually.",
        "pro": "Pro check: review spender permissions, revoke stale approvals, compare the domain to official project channels, and pause when the offer creates urgency.",
    },
    {
        "title": "Impersonated exchange support",
        "story": "A fake support account asks for a seed phrase or remote access after a withdrawal delay.",
        "avoid": "Support never needs your seed phrase. Use only official app or website support paths.",
        "pro": "Pro check: verify handles, avoid screen sharing wallets, document ticket IDs, and lock withdrawals if account access feels compromised.",
    },
    {
        "title": "Romance or mentorship investment scam",
        "story": "A friendly contact slowly builds trust, then pushes deposits into a fake trading platform.",
        "avoid": "Do not send funds to platforms introduced by strangers. Test withdrawals before adding money.",
        "pro": "Pro check: inspect domain age, withdrawal rules, app store legitimacy, and whether profits are only visible inside the platform.",
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
        "/cryptonews — recent crypto news with market read\n"
        "/marketevents — global event impact on BTC/ETH\n"
        "/wisdom — daily risk-management wisdom\n"
        "/scamstories — recent scam examples and prevention\n"
        "/countrynews Haiti — country crypto intelligence\n"
        "/sportsedge — experimental sports section\n"
        "/subscribe — Pro subscription options\n"
        "/account — account and subscription status\n"
        "/admin — admin dashboard summary\n\n"
        "Portfolio: /addholding BTC 0.02, /removeholding BTC 0.01, /myportfolio\n"
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
    msg = (
        "🛡 Recent Crypto Scam Stories\n\n"
        f"{story['title']}\n"
        f"What happened: {story['story']}\n"
        f"How to avoid it: {story['avoid']}"
    )
    if pro:
        msg += f"\n\n{story['pro']}"
    else:
        msg += "\n\nPro unlocks a deeper safety breakdown and checklist."
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
        f"Scam risk: Watch fake exchange support, wallet-drain links, investment managers, and guaranteed-return pitches.",
        f"Remittance use: {profile['remittance']}",
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
            "When enabled, it should stay informational only: no guaranteed bets, no promises, and no financial claims."
        )
    return (
        "🎲 Sports Edge\n\n"
        "Experimental read: compare team form, injuries, schedule fatigue, line movement, and bankroll risk before making any decision.\n\n"
        "Informational only. No guaranteed bets or guaranteed wins."
    )


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


def portfolio_live_summary(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT asset, amount FROM manual_portfolio WHERE user_id=? AND amount > 0", (user_id,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "💼 Real-Time Portfolio\n\nNo tracked holdings yet.\nTry: /addholding BTC 0.02"

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

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO portfolio_snapshots (user_id, total_value, holdings_json, created_at) VALUES (?, ?, ?, ?)",
        (user_id, total, json.dumps(holdings), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    return (
        "💼 Real-Time Portfolio\n\n"
        + "\n".join(lines)
        + f"\n\nEstimated total: ${total:,.2f}\nCoinPilotX tracks only. It never holds funds."
    )


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
    await update.message.reply_text(ai_crypto_analysis(update.effective_user.id, asset), reply_markup=main_menu())


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
    fear = get_fear_greed()
    value = "Unavailable" if fear["value"] is None else str(fear["value"])
    await update.message.reply_text(
        f"😬 Market Fear/Greed AI\n\nScore: {value}\nMood: {fear['label']}\n\n{fear['advice']}",
        reply_markup=main_menu()
    )


async def whales_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    asset = normalize_asset(context.args[0] if context.args else "BTC")
    live = get_whale_activity(asset)
    for alert in live:
        save_whale_alert(alert)

    rows = latest_whale_alerts(5)
    if not rows:
        await update.message.reply_text(
            "🐋 Whale Alerts\n\nNo large BTC/ETH style trades detected in the latest scan.",
            reply_markup=main_menu()
        )
        return

    msg = "🐋 Whale Alerts\n\n"
    for asset, side, notional, price, source, created_at in rows:
        msg += f"{asset}: {side} near ${price:,.2f}, about ${notional:,.0f}\nSource: {source}\n\n"
    await update.message.reply_text(msg.strip(), reply_markup=main_menu())


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
    await update.message.reply_text(msg, reply_markup=main_menu())


async def portfolio_live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(portfolio_live_summary(update.effective_user.id), reply_markup=main_menu())


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
        [
            InlineKeyboardButton("📰 Crypto News", callback_data="menu_crypto_news"),
            InlineKeyboardButton("🌍 Market Events", callback_data="menu_market_events"),
        ],
        [
            InlineKeyboardButton("🧠 Crypto Wisdom", callback_data="menu_wisdom"),
            InlineKeyboardButton("🛡 Scam Stories", callback_data="menu_scam_stories"),
        ],
        [
            InlineKeyboardButton("🇭🇹 Country News", callback_data="menu_country_news"),
            InlineKeyboardButton("🎲 Sports Edge", callback_data="menu_sports_edge"),
        ],
        [
            InlineKeyboardButton("🐋 Whale Alerts", callback_data="menu_whales"),
            InlineKeyboardButton("😬 Fear/Greed", callback_data="menu_feargreed"),
        ],
        [
            InlineKeyboardButton("💼 Live Portfolio", callback_data="menu_portfolio_live"),
            InlineKeyboardButton("👤 Account", callback_data="menu_account"),
        ],
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
    await update.message.reply_text(crypto_news_summary(update.effective_user.id), reply_markup=main_menu())


async def marketevents_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    event_text = " ".join(context.args).strip()
    log_engagement(update.effective_user.id, "market_events", event_text)
    await update.message.reply_text(
        market_events_summary(event_text, is_pro(update.effective_user.id)),
        reply_markup=main_menu()
    )


async def wisdom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "wisdom")
    await update.message.reply_text(daily_wisdom(), reply_markup=main_menu())


async def scamstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    log_engagement(update.effective_user.id, "scam_stories")
    await update.message.reply_text(scam_stories_summary(is_pro(update.effective_user.id)), reply_markup=main_menu())


async def countrynews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    country = " ".join(context.args).strip() if context.args else "Haiti"
    log_engagement(update.effective_user.id, "country_news", country)
    await update.message.reply_text(
        country_news_summary(country, is_pro(update.effective_user.id)),
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
        await query.message.reply_text(ai_crypto_analysis(user_id, "BTC"), reply_markup=main_menu())
        return

    if data == "menu_crypto_news":
        log_engagement(user_id, "crypto_news")
        await query.message.reply_text(crypto_news_summary(user_id), reply_markup=main_menu())
        return

    if data == "menu_market_events":
        log_engagement(user_id, "market_events")
        await query.message.reply_text(
            market_events_summary("", is_pro(user_id)),
            reply_markup=main_menu()
        )
        return

    if data == "menu_wisdom":
        log_engagement(user_id, "wisdom")
        await query.message.reply_text(daily_wisdom(), reply_markup=main_menu())
        return

    if data == "menu_scam_stories":
        log_engagement(user_id, "scam_stories")
        await query.message.reply_text(scam_stories_summary(is_pro(user_id)), reply_markup=main_menu())
        return

    if data == "menu_country_news":
        log_engagement(user_id, "country_news", "Haiti")
        await query.message.reply_text(country_news_summary("Haiti", is_pro(user_id)), reply_markup=main_menu())
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
        await query.message.reply_text(msg + "Educational only. Not financial advice.", reply_markup=main_menu())
        return

    if data == "menu_whales":
        live = get_whale_activity("BTC") + get_whale_activity("ETH")
        for alert in live:
            save_whale_alert(alert)
        rows = latest_whale_alerts(5)
        msg = "🐋 Whale Alerts\n\n"
        if rows:
            for asset, side, notional, price, source, created_at in rows:
                msg += f"{asset}: {side} about ${notional:,.0f} near ${price:,.2f}\n"
        else:
            msg += "No large trades found in the latest scan."
        await query.message.reply_text(msg, reply_markup=main_menu())
        return

    if data == "menu_feargreed":
        fear = get_fear_greed()
        value = "Unavailable" if fear["value"] is None else str(fear["value"])
        await query.message.reply_text(
            f"😬 Market Fear/Greed AI\n\nScore: {value}\nMood: {fear['label']}\n\n{fear['advice']}",
            reply_markup=main_menu()
        )
        return

    if data == "menu_portfolio_live":
        await query.message.reply_text(portfolio_live_summary(user_id), reply_markup=main_menu())
        return

    if data == "menu_account":
        await query.message.reply_text(account_summary(user_id), reply_markup=main_menu())
        return

    if data == "menu_alerts_on":
        set_alerts(user_id, True)
        await query.message.reply_text("🚨 Alerts are now ON.", reply_markup=main_menu())
        return

    if data == "pro_signals":
        await query.message.reply_text(ai_crypto_analysis(user_id, "BTC"), reply_markup=main_menu())
        return

    if data == "pro_portfolio":
        await query.message.reply_text(portfolio_live_summary(user_id), reply_markup=main_menu())
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
            "💬 Chat Assistant\n\nAsk me a crypto question in plain English, or send a suspicious message and I’ll help you inspect it.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_about":
        await query.message.reply_text(
            "ℹ️ About CoinPilotX\n\nCoinPilotX helps users understand live crypto prices, signals, portfolio movement, whale activity, exchange choices, and scam risks.\n\nEducational only. Not financial advice.",
            reply_markup=main_menu()
        )
        return

    if data == "menu_deposit":
        await query.message.reply_text(
            "💰 Add Money Safely\n\nCoinPilotX does not hold money or accept deposits.\nUse official exchanges directly and never send funds to someone promising guaranteed returns.",
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
    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("feargreed", feargreed_command))
    app.add_handler(CommandHandler("whales", whales_command))
    app.add_handler(CommandHandler("signals", signals_command))
    app.add_handler(CommandHandler("portfolio_live", portfolio_live_command))
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
