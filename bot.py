# =========================
# IMPORTS
# =========================

import os
import re
import sqlite3
import logging
import requests
import threading
import stripe

from datetime import datetime
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
STRIPE_SECRET_KEY = os.getenv("pk_live_51TTVo7FP8qvvGWBIDouaWZFEAS55BIs7sNhnTQoG3ViKYGfrsKhxZNLcxxNV12hVKscsZcxUuc5v8djSwjBGymMv001qSfctBS")
STRIPE_WEBHOOK_SECRET = os.getenv("whsec_53ae77e0b59c5891c8ac08764311976c3b6bc90f0783b64e0adc7148e9e0b2e1")

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
STRIPE_WEBHOOK_SECRET = "whsec_YOUR_WEBHOOK_SECRET"

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
# BUTTON HANDLER
# =========================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    print("BUTTON CLICKED:", data)

    # =========================
    # MAIN MENU
    # =========================
    if data == "menu_main":
        await query.message.reply_text(
            "🏠 Main Menu\n\nChoose what you want to do next:",
            reply_markup=main_menu()
        )
        return

    # =========================
    # FREE BTC PRICE
    # =========================
    if data == "menu_price_btc":
        price_now, _ = get_best_price("BTC")

        if price_now:
            await query.message.reply_text(
                f"📈 BTC Price\n\nBTC: ${price_now:,.2f}\n\nEducational only — not financial advice.",
                reply_markup=main_menu()
            )
        else:
            await query.message.reply_text(
                "⚠️ BTC price is unavailable right now. Try again in a few minutes.",
                reply_markup=main_menu()
            )
        return

    # =========================
    # FREE ALERTS
    # =========================
    if data == "menu_alerts_on":
        set_alerts(query.from_user.id, True)
        await query.message.reply_text(
            "🚨 Free alerts are now ON.\n\nI’ll notify you when market movement looks important.",
            reply_markup=main_menu()
        )
        return

    # =========================
    # PRO SIGNALS
    # =========================
    if data == "pro_signals":
        if not is_pro(query.from_user.id):
            await query.message.reply_text(
                "⭐ Pro Signals\n\nUpgrade to unlock smarter BUY / SELL / WAIT insights based on stronger market checks.",
                reply_markup=main_menu()
            )
            return

        await query.message.reply_text(
            "⭐ Pro Signals Active\n\nYou now have access to smarter signal insights.",
            reply_markup=main_menu()
        )
        return

    # =========================
    # PRO PORTFOLIO
    # =========================
    if data == "pro_portfolio":
        if not is_pro(query.from_user.id):
            await query.message.reply_text(
                "💼 Pro Portfolio\n\nUpgrade to unlock portfolio-aware insights and smarter risk tracking.",
                reply_markup=main_menu()
            )
            return

        summary = get_portfolio_summary(query.from_user.id)
        await query.message.reply_text(summary, reply_markup=main_menu())
        return

    # =========================
    # PRO SCAM SHIELD
    # =========================
    if data == "pro_scanner":
        if not is_pro(query.from_user.id):
            await query.message.reply_text(
                "🛡 Pro Scam Shield\n\nUpgrade to unlock deeper scam checks, link review, and safer crypto guidance.",
                reply_markup=main_menu()
            )
            return

        await query.message.reply_text(
            "🛡 Pro Scam Shield Active\n\nSend me any suspicious crypto message, wallet address, or link and I’ll scan it.",
            reply_markup=main_menu()
        )
        return

    # =========================
    # UPGRADE TO PRO
    # =========================
    if data == "upgrade_pro":
        await query.message.reply_text(
            pro_upgrade_message(query.from_user.id),
            reply_markup=upgrade_payment_menu(query.from_user.id)
        )
        return

    # =========================
    # PAY WITH BTC
    # =========================
    if data == "pay_btc":
        await query.message.reply_text(
            f"₿ Pay with Bitcoin\n\n"
            f"Send exactly: {BTC_PRO_PRICE}\n\n"
            f"BTC address:\n{BTC_PAYMENT_ADDRESS}\n\n"
            "After sending, type:\n"
            "/verify_payment YOUR_TXID\n\n"
            "⚠️ Double-check the address before sending.",
            reply_markup=main_menu()
        )
        return

    # =========================
    # ASK CRYPTO QUESTION
    # =========================
    if data == "menu_talk":
        await query.message.reply_text(
            "💬 Ask me anything crypto-related.\n\nExample:\nWhat is Bitcoin?\nHow do I avoid crypto scams?\nWhat is a wallet?",
            reply_markup=main_menu()
        )
        return

    # =========================
    # ABOUT
    # =========================
    if data == "menu_about":
        await query.message.reply_text(
            "ℹ️ About CoinPilotX\n\nCoinPilotX helps you track crypto prices, receive alerts, review risks, and avoid common scams.\n\nEducational only — not financial advice.",
            reply_markup=main_menu()
        )
        return

    # =========================
    # ADD MONEY SAFELY
    # =========================
    if data == "menu_deposit":
        await query.message.reply_text(
            "💰 Add Money Safely\n\nUse trusted exchanges like Coinbase, Kraken, Gemini, or Crypto.com.\n\nNever send money to people promising guaranteed returns.\nNever share your seed phrase or private keys.",
            reply_markup=main_menu()
        )
        return

    # =========================
    # BEST EXCHANGES
    # =========================
    if data == "menu_exchanges":
        await query.message.reply_text(
            "🏦 Best Exchanges\n\nChoose your goal below:",
            reply_markup=exchange_menu()
        )
        return

    if data.startswith("exchange_"):
        goal = data.replace("exchange_", "")
        await send_exchange_message(query.message, goal)
        return

    # =========================
    # HELP
    # =========================
    if data == "menu_help":
        await query.message.reply_text(
            help_message(),
            reply_markup=main_menu()
        )
        return

    # =========================
    # SCAN YES
    # =========================
    if data in ["scan_confirm", "scan_yes"]:
        text = context.user_data.get("pending_scan")

        if not text:
            await query.message.reply_text(
                "Nothing to scan right now.",
                reply_markup=main_menu()
            )
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

    # =========================
    # SCAN NO
    # =========================
    if data in ["scan_cancel", "scan_no"]:
        context.user_data.pop("pending_scan", None)
        await query.message.reply_text(
            "Scan canceled.",
            reply_markup=main_menu()
        )
        return

    # =========================
    # FINAL FALLBACK — MUST BE LAST
    # =========================
    await query.message.reply_text(
        "🏠 Back to menu.",
        reply_markup=main_menu()
    )
    return
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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue.run_repeating(market_signal_job, interval=SIGNAL_CHECK_SECONDS, first=10)
    job_queue.run_repeating(hourly_market_update, interval=HOURLY_UPDATE_SECONDS, first=30)

    app.run_polling()


if __name__ == "__main__":
    threading.Thread(target=run_webhook, daemon=True).start()
    main()
