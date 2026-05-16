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
from urllib.parse import quote, urlparse
from flask import Flask, request, render_template, send_from_directory, jsonify, Response, session, redirect, url_for, has_request_context
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

from services import (
    brevo_contacts as brevo_contacts_service,
    command_router as command_router_service,
    db as db_service,
    day_signal as day_signal_service,
    email_service as email_service_service,
    ai_router as ai_router_service,
    live_market_service,
    intelligence as intelligence_service,
    market_data as market_data_service,
    news_service,
    notification_service,
    notification_orchestrator as notification_orchestrator_service,
    portfolio_service,
    predictions_service,
    pro_access as pro_access_service,
    scam_shield as scam_shield_service,
    sports_data as sports_data_service,
    user_context as user_context_service,
    wallet_intel as wallet_intel_service,
)
from seo import schema as seo_schema
from seo.content import (
    all_public_paths,
    article_page,
    country_page,
    hub_page,
    market_live_page,
    market_page,
    market_prediction_page,
    search_pages,
    seo_index_payload,
    seo_page,
    sports_page,
)
# =========================
# 💳 STRIPE CONFIG (RIGHT AFTER CONSTANTS)
# =========================
BOT_NAME = "CoinPilotX"
DB_FILE = "coinpilotx.db"

BOT_TOKEN = os.getenv("BOT_TOKEN")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

STRIPE_PRO_LINK = ""

stripe.api_key = STRIPE_SECRET_KEY

BTC_PAYMENT_ADDRESS = os.getenv("BTC_PAYMENT_ADDRESS", "0x8DE1A7eAb2C937cdCdC24E8F79B0ac0960040CD8")
BTC_PRO_PRICE = "0.00025 BTC"
BTC_PRO_SATS = 25000
BTC_REQUIRED_CONFIRMATIONS = 1
BLOCKSTREAM_TX_API = "https://blockstream.info/api/tx/"
PRO_TRIAL_DAYS = 30
FREE_AI_DAILY_LIMIT = 5
TRIAL_MAINTENANCE_INTERVAL_SECONDS = 3600
TRIAL_MAINTENANCE_LAST_RUN = 0

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
RATE_LIMIT_BUCKETS = {}
OWNER_ADMIN_EMAIL = "cherieroody@gmail.com"
OWNER_ADMIN_FULL_NAME = "Roody Cherie"
OWNER_ADMIN_PHONE = "5164618652"
OWNER_BOOTSTRAP_TEMP = {
    "password": "",
    "email": "",
    "created_at": "",
    "display_available": False,
    "reason": "",
}

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
    return db_service.connect()


def normalize_email(email):
    return (email or "").strip().lower()


DB_STARTUP_DIAGNOSTICS = db_service.log_startup_diagnostics()
MAIL_STARTUP_STATUS = email_service_service.provider_status()
logging.info(
    "MAIL_PROVIDER_READY provider=%s ready=%s sender_address=%s sender_name=%s",
    MAIL_STARTUP_STATUS.get("provider"),
    MAIL_STARTUP_STATUS.get("ready"),
    MAIL_STARTUP_STATUS.get("sender_email"),
    MAIL_STARTUP_STATUS.get("sender_name"),
)


def generate_owner_temp_password():
    token = secrets.token_urlsafe(18).replace("-", "A").replace("_", "z")
    return f"CPX-{token[:18]}!9aZ"


def remember_owner_temp_password(password, reason):
    OWNER_BOOTSTRAP_TEMP.update({
        "password": password,
        "email": OWNER_ADMIN_EMAIL,
        "created_at": datetime.now().isoformat(),
        "display_available": True,
        "reason": reason,
    })
    logging.warning(
        "OWNER ADMIN TEMPORARY CREDENTIALS GENERATED ONCE email=%s temporary_password=%s reason=%s",
        OWNER_ADMIN_EMAIL,
        password,
        reason,
    )


def insert_admin_audit_with_cursor(cur, admin_user_id, admin_email, action, target_type="", target_id="", metadata=None):
    cur.execute(
        """
        INSERT INTO admin_audit_logs
        (admin_user_id, admin_email, action, target_type, target_id, metadata, ip_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            admin_user_id or 0,
            admin_email or "",
            action,
            target_type or "",
            target_id or "",
            json.dumps(metadata or {})[:4000],
            "",
            datetime.now().isoformat(),
        ),
    )


def ensure_owner_admin_with_cursor(cur, allow_reset=False):
    now_iso = datetime.now().isoformat()
    reset_requested = allow_reset and os.getenv("ADMIN_RESET_OWNER_PASSWORD", "false").strip().lower() == "true"
    cur.execute(
        """
        SELECT id, password_hash, must_change_password
        FROM admin_users
        WHERE lower(email)=lower(?)
        LIMIT 1
        """,
        (OWNER_ADMIN_EMAIL,),
    )
    owner_row = cur.fetchone()
    temp_password = ""
    action = ""
    if owner_row:
        owner_id, password_hash, must_change_password = owner_row[0], owner_row[1], owner_row[2] if len(owner_row) > 2 else 1
        needs_temp_password = not password_hash or reset_requested
        if needs_temp_password:
            temp_password = generate_owner_temp_password()
            cur.execute(
                """
                UPDATE admin_users
                SET full_name=?, phone=?, role='owner', status='active', company_role='Owner',
                    password_hash=?, must_change_password=1, temp_password_created_at=?,
                    failed_login_count=0, locked_until=NULL, updated_at=?
                WHERE id=?
                """,
                (
                    OWNER_ADMIN_FULL_NAME,
                    OWNER_ADMIN_PHONE,
                    generate_password_hash(temp_password),
                    now_iso,
                    now_iso,
                    owner_id,
                ),
            )
            action = "owner_password_reset" if reset_requested else "owner_temp_password_generated"
            remember_owner_temp_password(temp_password, action)
            insert_admin_audit_with_cursor(cur, owner_id, OWNER_ADMIN_EMAIL, action, "admin_user", str(owner_id), {"forced_change": True})
        else:
            cur.execute(
                """
                UPDATE admin_users
                SET full_name=?, phone=?, role='owner', status='active', company_role='Owner',
                    must_change_password=COALESCE(must_change_password, ?), updated_at=?
                WHERE id=?
                """,
                (
                    OWNER_ADMIN_FULL_NAME,
                    OWNER_ADMIN_PHONE,
                    1 if must_change_password is None else must_change_password,
                    now_iso,
                    owner_id,
                ),
            )
        return {"created": False, "reset": bool(reset_requested and temp_password), "temp_password": temp_password, "owner_id": owner_id, "action": action}

    temp_password = generate_owner_temp_password()
    cur.execute(
        """
        INSERT INTO admin_users
        (full_name, email, phone, password_hash, role, status, company_role, must_change_password,
         temp_password_created_at, failed_login_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'owner', 'active', 'Owner', 1, ?, 0, ?, ?)
        """,
        (
            OWNER_ADMIN_FULL_NAME,
            OWNER_ADMIN_EMAIL,
            OWNER_ADMIN_PHONE,
            generate_password_hash(temp_password),
            now_iso,
            now_iso,
            now_iso,
        ),
    )
    owner_id = cur.lastrowid
    remember_owner_temp_password(temp_password, "owner_admin_created")
    insert_admin_audit_with_cursor(cur, owner_id, OWNER_ADMIN_EMAIL, "owner_admin_created", "admin_user", str(owner_id), {"forced_change": True})
    insert_admin_audit_with_cursor(cur, owner_id, OWNER_ADMIN_EMAIL, "owner_temp_password_generated", "admin_user", str(owner_id), {"forced_change": True})
    return {"created": True, "reset": False, "temp_password": temp_password, "owner_id": owner_id, "action": "owner_admin_created"}


def ensure_owner_admin(allow_reset=False):
    init_db()
    conn = db()
    cur = conn.cursor()
    result = ensure_owner_admin_with_cursor(cur, allow_reset=allow_reset)
    conn.commit()
    conn.close()
    return result


# =========================
# INIT DB
# =========================
def seed_education_knowledge_bank(cur):
    now = datetime.now().isoformat()
    categories = [
        ("crypto-basics", "Crypto Basics", "Clear foundations: coins, wallets, exchanges, stablecoins, and blockchain language.", 1),
        ("investor-safety", "Investor Safety", "Safety-first habits for accounts, wallets, links, and decision-making.", 2),
        ("scam-defense", "Scam Defense", "Fake airdrops, wallet drainers, fake support, recovery scams, and phishing defense.", 3),
        ("wallet-intelligence", "Wallet Intelligence", "How public wallets, approvals, explorers, gas fees, and address activity work.", 4),
        ("market-psychology", "Market Psychology", "FOMO, panic selling, greed, overtrading, sizing, and discipline.", 5),
        ("on-chain-analysis", "On-Chain Analysis", "Whale alerts, exchange flows, mempool basics, gas, and suspicious activity.", 6),
        ("portfolio-intelligence", "Portfolio Intelligence", "Diversification, volatility, drawdown, paper trading, and risk exposure.", 7),
        ("ai-signal-literacy", "AI Signal Literacy", "How to read AI signals responsibly without treating them as guarantees.", 8),
        ("simulator-training", "Trading Simulator Training", "Paper trading practice, fake orders, stops, limits, and emotional review.", 9),
    ]
    for slug, title, summary, order in categories:
        cur.execute(
            "INSERT OR IGNORE INTO education_categories (slug, title, summary, sort_order, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (slug, title, summary, order, now),
        )
    lesson_seed = [
        ("crypto-basics-101", "crypto-basics", "Crypto Basics 101", "beginner", "8 min", "Understand what crypto is, how blockchains record value, and why security habits matter before buying anything."),
        ("bitcoin-basics", "crypto-basics", "Bitcoin Basics", "beginner", "7 min", "Bitcoin as a decentralized monetary network, its supply design, volatility, and custody risks."),
        ("ethereum-basics", "crypto-basics", "Ethereum Basics", "beginner", "8 min", "Ethereum, smart contracts, gas fees, wallets, tokens, and common risks around approvals."),
        ("wallet-safety-101", "investor-safety", "Wallet Safety 101", "beginner", "9 min", "Hot wallets, cold wallets, seed phrase storage, transaction review, and safer approval habits."),
        ("seed-phrase-safety", "investor-safety", "Seed Phrase Safety", "beginner", "7 min", "Why seed phrases control funds and why no app, exchange, bot, support agent, or AI assistant should ever ask for one."),
        ("fake-airdrop-scams", "scam-defense", "Fake Airdrop Scams", "beginner", "9 min", "How fake claim links, urgency copy, wallet connects, and approval drainers trick users."),
        ("wallet-drainer-scams", "scam-defense", "Wallet Drainer Scams", "intermediate", "11 min", "Understand malicious approvals, blind signing, permit scams, and safe revoke habits."),
        ("market-psychology-101", "market-psychology", "Market Psychology 101", "beginner", "8 min", "FOMO, fear, greed, revenge trading, and how a written plan reduces impulsive decisions."),
        ("fomo-and-panic-selling", "market-psychology", "FOMO and Panic Selling", "beginner", "9 min", "Spot emotional triggers, slow down entries, and build a decision checklist."),
        ("whale-alerts-explained", "on-chain-analysis", "Whale Alerts Explained", "intermediate", "9 min", "Large wallet movements, exchange inflows, false signals, and how to interpret them responsibly."),
        ("transaction-explorer-basics", "wallet-intelligence", "Transaction Explorer Basics", "beginner", "10 min", "Read public transaction hashes, confirmations, gas, token transfers, and address histories safely."),
        ("portfolio-risk-basics", "portfolio-intelligence", "Portfolio Risk Basics", "beginner", "10 min", "Allocation, concentration, drawdown, rebalancing, and paper trading as practice."),
        ("how-to-read-ai-signals", "ai-signal-literacy", "How to Read AI Signals", "beginner", "8 min", "AI can organize context, but signals are uncertain and must be checked against risk rules and sources."),
        ("optimism-market-data-education", "on-chain-analysis", "Optimism Market Data Education", "intermediate", "10 min", "Layer 2 basics, OP token context, rollups, gas costs, and live market data reading."),
        ("toncoin-scenario-education", "scam-defense", "Toncoin Scenario Education", "intermediate", "10 min", "Toncoin ecosystem context, wallet safety scenarios, fake bot/payment risks, and decision trees."),
        ("scam-alert-checklist", "scam-defense", "Scam Alert Checklist", "beginner", "8 min", "A practical checklist for suspicious links, messages, contracts, support chats, and guaranteed-profit claims."),
    ]
    base_sections = [
        ("Core Idea", "Start with definitions, source quality, and the exact risk being discussed. A good crypto lesson should make the user slower, safer, and more precise."),
        ("Real Examples", "Example 1: a link asks you to connect a wallet to claim free tokens. Example 2: a support account asks for a seed phrase. Example 3: a market move tempts a rushed trade."),
        ("Red Flags", "Urgency, secret links, guaranteed returns, seed phrase requests, tax-to-unlock claims, fake support agents, and unexplained wallet approval prompts are major warning signs."),
        ("Safe Action Steps", "Verify official domains manually, never share recovery phrases, use small test transactions when appropriate, revoke suspicious approvals, and document your plan before acting."),
        ("Common Beginner Mistakes", "Trusting screenshots, clicking sponsored links without checking the domain, confusing confidence with certainty, oversizing positions, and ignoring wallet approvals."),
    ]
    questions = [
        {"question": "Should you ever share a seed phrase with support?", "options": ["Yes", "No", "Only if urgent"], "answer": "No", "explanation": "A seed phrase controls funds and should never be shared."},
        {"question": "What does an urgent wallet-connect airdrop link usually deserve?", "options": ["Immediate click", "Caution and verification", "A bigger transaction"], "answer": "Caution and verification", "explanation": "Urgency and wallet connect requests are common scam patterns."},
        {"question": "Are AI signals guaranteed outcomes?", "options": ["Yes", "No", "Only for Pro users"], "answer": "No", "explanation": "AI can summarize context, but outcomes remain uncertain."},
    ]
    cur.execute("SELECT COUNT(*) FROM education_lessons")
    if (cur.fetchone()[0] or 0) > 0:
        return
    for slug, category, title, difficulty, estimated, summary in lesson_seed:
        content = (
            f"{summary}\n\n"
            "CoinPilotXAI teaches this topic with a safety-first lens: understand the tool, identify the risk, decide slowly, and never treat educational intelligence as a guaranteed result.\n\n"
            "Key terms: wallet, private key, approval, volatility, liquidity, signal confidence, source status, and risk control."
        )
        cur.execute(
            """
            INSERT OR IGNORE INTO education_lessons
            (slug, category_slug, title, difficulty, estimated_time, summary, content, examples, red_flags, safe_steps, beginner_mistakes, access_level, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'free', 1, ?, ?)
            """,
            (
                slug,
                category,
                title,
                difficulty,
                estimated,
                summary,
                content,
                "Fake airdrop link; sudden guaranteed-profit dashboard; fake support account asking for recovery words.",
                "Seed phrase request; urgency; shortened URL; pay-to-unlock claim; celebrity/exchange impersonation.",
                "Pause; verify official sources; never share keys; use read-only explorers; ask CoinPilotXAI Tutor for education.",
                "Rushing; trusting screenshots; oversizing; clicking without checking the domain; ignoring approvals.",
                now,
                now,
            ),
        )
        for order, (heading, body) in enumerate(base_sections, start=1):
            cur.execute(
                "INSERT INTO education_sections (lesson_slug, heading, body, sort_order) VALUES (?, ?, ?, ?)",
                (slug, heading, body, order),
            )
        cur.execute("INSERT INTO education_quizzes (lesson_slug, title, created_at) VALUES (?, ?, ?)", (slug, f"{title} Quiz", now))
        for question in questions:
            cur.execute(
                "INSERT INTO education_quiz_questions (lesson_slug, question, options, answer, explanation) VALUES (?, ?, ?, ?, ?)",
                (slug, question["question"], json.dumps(question["options"]), question["answer"], question["explanation"]),
            )


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
    run_trial_maintenance()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT * FROM users WHERE user_id=? OR telegram_user_id=? ORDER BY is_pro DESC LIMIT 1", (user_id, user_id))
        row = cur.fetchone()
    except Exception:
        row = None

    conn.close()
    return pro_access_service.is_pro_row(dict(row)) if row else False


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
STRIPE_PRO_LINK = ""


def website_upgrade_url(telegram_user_id=None):
    if telegram_user_id:
        return f"https://coinpilotx.app/upgrade?source=telegram&telegram_id={telegram_user_id}"
    return "https://coinpilotx.app/upgrade?source=telegram"


def pro_upgrade_message(user_id):
    account = get_linked_website_account(user_id)
    if platform_pro_access(account):
        return (
            "✅ Your CoinPilotXAI Pro access is already active.\n\n"
            "Open your dashboard anytime to use the full platform. Telegram is an optional companion for quick commands and alerts."
        )
    return (
        "⭐ CoinPilotX Pro\n\n"
        f"Card price: {PRO_PRICE_MONTHLY}\n"
        "Payments are completed securely on the CoinPilotXAI website.\n\n"
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
        "No hidden fees from CoinPilotXAI Inc. Checkout opens only on the website.\n"
        "CoinPilotX never holds funds.\n"
        "CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.\n\n"
        "Create or log in to your website account, upgrade there, then return here and send your Telegram activation code."
    )

def upgrade_payment_menu(user_id):
    account = get_linked_website_account(user_id)
    if platform_pro_access(account):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Open Dashboard", url="https://coinpilotx.app/dashboard")],
            [InlineKeyboardButton("Account", url="https://coinpilotx.app/account")],
            [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Upgrade Pro on Website", url=website_upgrade_url(user_id))],
        [InlineKeyboardButton("Open Platform Account", url="https://coinpilotx.app/account")],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
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
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", os.getenv("BASE_URL", os.getenv("DOMAIN", "https://coinpilotx.app"))).rstrip("/")
BASE_URL = APP_BASE_URL

stripe.api_key = STRIPE_SECRET_KEY
logging.info(
    "Stripe startup validation: secret_key_loaded=%s publishable_key_loaded=%s webhook_secret_loaded=%s price_id_loaded=%s app_base_url=%s",
    bool(STRIPE_SECRET_KEY),
    bool(STRIPE_PUBLISHABLE_KEY),
    bool(STRIPE_WEBHOOK_SECRET),
    bool(STRIPE_PRICE_ID),
    APP_BASE_URL,
)
if not STRIPE_SECRET_KEY:
    logging.warning("Railway Stripe warning: STRIPE_SECRET_KEY is missing. Checkout session creation will fail.")
if not STRIPE_PUBLISHABLE_KEY:
    logging.warning("Railway Stripe warning: STRIPE_PUBLISHABLE_KEY is missing. Frontend publishable-key integrations may not work.")
if not STRIPE_WEBHOOK_SECRET:
    logging.warning("Railway Stripe warning: STRIPE_WEBHOOK_SECRET is missing. Webhook signature verification is not fully configured.")
if not STRIPE_PRICE_ID:
    logging.warning("Railway Stripe warning: STRIPE_PRICE_ID is missing. Website checkout will be disabled until the Railway variable is configured.")

webhook_app = Flask(__name__, template_folder="templates", static_folder="static")
webhook_app.secret_key = os.getenv("FLASK_SECRET_KEY", os.getenv("SECRET_KEY", secrets.token_hex(32)))
webhook_app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",
)
app = webhook_app


@webhook_app.context_processor
def inject_seo_runtime_config():
    return {
        "ga_measurement_id": os.getenv("GA_MEASUREMENT_ID", "").strip(),
        "google_site_verification": os.getenv("GOOGLE_SITE_VERIFICATION", "").strip(),
        "bing_site_verification": os.getenv("BING_SITE_VERIFICATION", "").strip(),
    }


@webhook_app.route("/", methods=["GET"])
def home():
    user = load_account_by_id(account_user_id())
    greeting = "Welcome to CoinPilotXAI — your AI intelligence command center."
    if user:
        first_name = (user.get("full_name") or user.get("display_name") or "").strip().split(" ")[0]
        if not first_name:
            first_name = (user.get("email") or "there").split("@")[0]
        if has_pro_access(user):
            greeting = f"Welcome back, {first_name}. Your Pro Intelligence is active."
        else:
            greeting = f"Welcome back, {first_name}. Create signals, scan wallets, and unlock Pro intelligence anytime."
    response = render_template("index.html", current_user=user or {}, homepage_greeting=greeting)
    response_obj = webhook_app.make_response(response)
    if user:
        response_obj.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response_obj


@webhook_app.route("/support", methods=["GET", "POST"])
def support_page():
    if request.method == "POST":
        init_db()
        name = clean_html(request.form.get("name", ""))[:160]
        email = normalize_email(clean_html(request.form.get("email", "")))
        issue_type = clean_html(request.form.get("issue_type", "general support"))[:80]
        subject = clean_html(request.form.get("subject", issue_type or "Support request"))[:180]
        message = clean_html(request.form.get("message", ""))[:4000]
        if is_valid_email(email) and message:
            conn = db()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO support_tickets
                (user_id, email, name, issue_type, subject, message, status, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'open', 'normal', ?, ?)
                """,
                (account_user_id() or 0, email, name, issue_type, subject, message, datetime.now().isoformat(), datetime.now().isoformat()),
            )
            ticket_id = cur.lastrowid
            cur.execute(
                "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_user_id, message, created_at) VALUES (?, 'user', ?, ?, ?)",
                (ticket_id, account_user_id() or 0, message, datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
            send_channel_email(
                "support@coinpilotx.app",
                f"CoinPilotXAI Support Ticket: {subject}",
                f"<p><strong>From:</strong> {clean_html(name)} &lt;{clean_html(email)}&gt;</p><p><strong>Issue:</strong> {clean_html(issue_type)}</p><p>{clean_html(message)}</p>",
                f"From: {name} <{email}>\nIssue: {issue_type}\n\n{message}",
                user_id=account_user_id() or 0,
                email_type="support_ticket",
                channel="support",
            )
            log_product_event(account_user_id() or 0, "support_ticket_created", {"issue_type": issue_type})
        return render_template("support.html", message="Thanks. CoinPilotXAI Inc. support received your request.")
    return render_template("support.html")


@webhook_app.route("/api/support/ticket", methods=["GET", "POST"])
def api_support_ticket():
    init_db()
    user = require_account()
    if request.method == "GET":
        if not user:
            return jsonify({"ok": False, "message": "Login required."}), 401
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT id, issue_type, subject, status, priority, created_at, updated_at FROM support_tickets WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["user_id"],))
        tickets = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({"ok": True, "tickets": tickets})
    payload = request.get_json(silent=True) or {}
    name = clean_html(payload.get("name") or (user or {}).get("full_name") or "")[:160]
    email = normalize_email(clean_html(payload.get("email") or (user or {}).get("email") or ""))
    issue_type = clean_html(payload.get("issue_type") or "general support")[:80]
    subject = clean_html(payload.get("subject") or issue_type or "Support request")[:180]
    message = clean_html(payload.get("message") or "")[:4000]
    if not is_valid_email(email) or not message:
        return jsonify({"ok": False, "message": "Valid email and message required."}), 400
    conn = db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute(
        """
        INSERT INTO support_tickets
        (user_id, email, name, issue_type, subject, message, status, priority, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'open', 'normal', ?, ?)
        """,
        ((user or {}).get("user_id") or 0, email, name, issue_type, subject, message, now, now),
    )
    ticket_id = cur.lastrowid
    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_user_id, message, created_at) VALUES (?, 'user', ?, ?, ?)",
        (ticket_id, (user or {}).get("user_id") or 0, message, now),
    )
    conn.commit()
    conn.close()
    send_channel_email(
        "support@coinpilotx.app",
        f"CoinPilotXAI Support Ticket: {subject}",
        f"<p><strong>From:</strong> {clean_html(name)} &lt;{clean_html(email)}&gt;</p><p><strong>Issue:</strong> {clean_html(issue_type)}</p><p>{clean_html(message)}</p>",
        f"From: {name} <{email}>\nIssue: {issue_type}\n\n{message}",
        user_id=(user or {}).get("user_id") or 0,
        email_type="support_ticket",
        channel="support",
    )
    log_product_event((user or {}).get("user_id") or 0, "support_ticket_created", {"issue_type": issue_type, "ticket_id": ticket_id})
    return jsonify({"ok": True, "ticket_id": ticket_id, "message": "Support ticket opened."})


@webhook_app.route("/security", methods=["GET"])
def security_page():
    return simple_public_page(
        "security",
        "Security Reporting | CoinPilotXAI Inc.",
        "CoinPilotXAI Security Reporting",
        "Report scams, suspicious wallets, phishing, abusive users, or account compromise to CoinPilotXAI Inc.",
        "Security reports are routed to security@coinpilotx.app. CoinPilotXAI never asks for seed phrases, private keys, recovery phrases, wallet passwords, or exchange passwords.",
        ["Scam reporting", "Suspicious wallet reporting", "Phishing reporting", "Account compromise help"],
        [{"title": "Report a security concern", "body": "Use the security reporting API or support form to report suspicious behavior. Include public wallet addresses or URLs only, never private credentials."}],
        ["/safety", "/scam-guide", "/wallet-security", "/support"],
    )


@webhook_app.route("/api/security/report", methods=["POST"])
def api_security_report():
    init_db()
    user = require_account()
    payload = request.get_json(silent=True) or {}
    email = normalize_email(clean_html(payload.get("email") or (user or {}).get("email") or ""))
    report_type = clean_html(payload.get("report_type") or "security")[:80]
    target = clean_html(payload.get("target") or "")[:500]
    description = clean_html(payload.get("description") or payload.get("message") or "")[:5000]
    if not description:
        return jsonify({"ok": False, "message": "Security report description required."}), 400
    conn = db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute(
        """
        INSERT INTO security_reports (user_id, email, report_type, target, description, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
        """,
        ((user or {}).get("user_id") or 0, email, report_type, target, description, now, now),
    )
    report_id = cur.lastrowid
    conn.commit()
    conn.close()
    send_channel_email(
        "security@coinpilotx.app",
        f"CoinPilotXAI Security Report: {report_type}",
        f"<p><strong>Email:</strong> {clean_html(email)}</p><p><strong>Target:</strong> {clean_html(target)}</p><p>{clean_html(description)}</p>",
        f"Email: {email}\nTarget: {target}\n\n{description}",
        user_id=(user or {}).get("user_id") or 0,
        email_type="security_report",
        channel="security",
    )
    log_product_event((user or {}).get("user_id") or 0, "security_report_created", {"report_type": report_type, "report_id": report_id})
    return jsonify({"ok": True, "report_id": report_id, "message": "Security report received."})


@webhook_app.route("/scam-shield", methods=["GET"])
def scam_shield_page():
    return simple_public_page(
        "scam-shield",
        "Scam Shield Crypto Threat Scanner | CoinPilotXAI",
        "Scam Shield Crypto Threat Scanner",
        "Scan suspicious crypto messages, wallet prompts, token claims, URLs, and fake support messages with CoinPilotXAI Scam Shield.",
        "Scam Shield uses layered AI and rule-based threat detection to identify many common crypto scam patterns while reminding users to verify independently.",
        ["Seed phrase requests", "Fake airdrops", "Wallet drainer prompts", "Phishing domains", "Fake support", "Guaranteed return scams"],
        [
            {"title": "What Scam Shield checks", "body": "Scam Shield looks for credential theft, fake wallet-connect prompts, guaranteed profit claims, fake support, urgency pressure, shortened URLs, suspicious domains, and withdrawal unlock scams."},
            {"title": "Safety rule", "body": "Never share seed phrases, private keys, recovery phrases, wallet passwords, exchange passwords, or signing credentials."},
        ],
        ["/dashboard/scam-alerts", "/crypto-scams", "/wallet-security", "/safety"],
    )


@webhook_app.route("/privacy", methods=["GET"])
def privacy_page():
    return render_template("privacy.html")


@webhook_app.route("/terms", methods=["GET"])
def terms_page():
    return render_template("terms.html")


@webhook_app.route("/search", methods=["GET"])
def site_search():
    query = (request.args.get("q") or "").strip()[:120]
    results = search_pages(query)
    return render_template("search.html", query=query, results=results)


def render_seo_landing(page, include_article=False):
    schema_json = seo_schema.schema_graph(
        page,
        include_product=page.get("slug") in {"portfolio-intelligence", "ai-market-analysis", "telegram-crypto-bot"},
        include_article=include_article or page.get("og_type") == "article",
    )
    share_url = quote(page["canonical"], safe="")
    share_text = quote(f"{page['h1']} by CoinPilotXAI Inc.", safe="")
    return render_template("seo_page.html", page=page, schema_json=schema_json, share_url=share_url, share_text=share_text)


def simple_public_page(slug, title, h1, intro, answer, points, sections=None, related=None):
    canonical = f"https://coinpilotx.app/{slug.strip('/')}"
    page = {
        "slug": slug.strip("/").replace("/", "-"),
        "title": title,
        "description": intro[:155],
        "canonical": canonical,
        "image": "https://coinpilotx.app/static/og/coinpilotxai-og.png",
        "og_type": "website",
        "eyebrow": "CoinPilotXAI Intelligence",
        "h1": h1,
        "intro": intro,
        "answer": answer,
        "points": points,
        "sections": sections or [],
        "faqs": [
            {"question": "Is this financial advice?", "answer": "No. CoinPilotXAI Inc. provides educational AI intelligence only, not financial, betting, investment, or legal advice."},
            {"question": "Does CoinPilotXAI need my seed phrase?", "answer": "No. CoinPilotXAI never asks for seed phrases, private keys, recovery phrases, wallet passwords, or exchange passwords."},
        ],
        "related": related or ["/platform", "/scam-guide", "/live-market", "/pricing"],
        "keywords": ["AI crypto intelligence platform", "crypto alerts", "wallet intelligence", "scam detection"],
    }
    return render_seo_landing(page)


def education_shell(title, h1, intro, body):
    return f"""<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{clean_html(title)}</title>
  <meta name="description" content="{clean_html(intro)[:155]}">
  <link rel="canonical" href="https://coinpilotx.app{clean_html(request.path)}">
  <meta property="og:title" content="{clean_html(title)}"><meta property="og:description" content="{clean_html(intro)[:155]}">
  <style>
    :root {{ color-scheme:dark; --bg:#050b14; --panel:#0d1627; --line:rgba(110,223,246,.22); --text:#f2fbff; --muted:#9fb5c0; --cyan:#6edff6; --green:#36e58f; --gold:#ffd166; }}
    *{{box-sizing:border-box}} body{{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;background:radial-gradient(circle at 10% 0,rgba(110,223,246,.18),transparent 28rem),linear-gradient(145deg,#050b14,#081421);color:var(--text);line-height:1.65;overflow-x:hidden}} a{{color:inherit;text-decoration:none}}
    .wrap{{width:min(100% - 32px,1180px);margin:auto}} header{{position:sticky;top:0;background:rgba(5,11,20,.88);backdrop-filter:blur(16px);border-bottom:1px solid var(--line);z-index:5}} nav{{min-height:68px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}} nav a,.edu-cta,.edu-customize-button{{display:inline-flex;min-height:42px;align-items:center;justify-content:center;border:1px solid var(--line);border-radius:10px;padding:10px 14px;background:rgba(255,255,255,.05);font-weight:850}} .edu-customize-button{{color:var(--text);cursor:pointer}} [data-edu-hidden="true"]{{display:none!important}} .edu-customize{{position:relative}} .edu-customize-panel{{position:absolute;right:0;top:48px;z-index:10;width:min(92vw,320px);padding:14px;border:1px solid var(--line);border-radius:16px;background:rgba(7,15,28,.96);box-shadow:0 24px 80px rgba(0,0,0,.38)}} .edu-toggle{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px;border:1px solid rgba(255,255,255,.08);border-radius:12px;margin:8px 0;color:var(--muted)}} .edu-toggle input{{accent-color:var(--cyan);inline-size:42px;block-size:22px}}
    main{{padding:42px 0 70px}} .hero{{padding:32px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.035));box-shadow:0 28px 90px rgba(0,0,0,.28)}} h1{{font-size:clamp(38px,7vw,68px);line-height:1;margin:8px 0}} h2{{font-size:clamp(24px,4vw,34px)}} .muted,p{{color:var(--muted)}} .edu-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:18px 0}} .edu-card,.edu-panel{{border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.045);padding:18px;box-shadow:0 20px 60px rgba(0,0,0,.22)}} .edu-card{{transition:transform .18s ease,box-shadow .18s ease}} .edu-card:hover{{transform:translateY(-3px);box-shadow:0 0 32px rgba(110,223,246,.18)}} .edu-card span,.edu-card strong{{display:block;font-size:18px;color:var(--cyan);font-weight:950}} small{{color:var(--gold)}} .edu-actions{{display:flex;gap:10px;flex-wrap:wrap}} .concepts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}} .concept{{border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:16px;background:rgba(0,0,0,.16)}} .radar{{height:130px;border-radius:999px;background:radial-gradient(circle,rgba(54,229,143,.28),transparent 28%,rgba(110,223,246,.18),transparent 58%);animation:pulseRadar 4s ease-in-out infinite}} @keyframes pulseRadar{{50%{{filter:brightness(1.35);transform:scale(1.02)}}}} @media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}} @media(max-width:720px){{.hero{{padding:22px}}.edu-actions a{{width:100%}}}}
  </style>
</head><body>
  <header><div class="wrap"><nav><a href="/" data-edu-nav="show_edu_nav_home">CoinPilotXAI</a><div class="edu-actions"><a href="/dashboard" data-edu-nav="show_edu_nav_dashboard">Dashboard</a><a href="/education" data-edu-nav="show_edu_nav_education">Education</a><a href="/scam-shield" data-edu-nav="show_edu_nav_scam_shield">Scam Shield</a><div class="edu-customize"><button class="edu-customize-button" type="button" data-edu-customize>Customize</button><div class="edu-customize-panel" data-edu-customize-panel hidden><strong>Customize Education Navigation</strong><label class="edu-toggle">Show CoinPilotXAI <input type="checkbox" data-edu-pref="show_edu_nav_home" checked></label><label class="edu-toggle">Show Dashboard <input type="checkbox" data-edu-pref="show_edu_nav_dashboard" checked></label><label class="edu-toggle">Show Education <input type="checkbox" data-edu-pref="show_edu_nav_education" checked></label><label class="edu-toggle">Show Scam Shield <input type="checkbox" data-edu-pref="show_edu_nav_scam_shield" checked></label><button class="edu-cta" type="button" data-edu-reset>Reset defaults</button></div></div></div></nav></div></header>
  <main class="wrap"><section class="hero"><div class="radar" aria-hidden="true"></div><h1>{clean_html(h1)}</h1><p>{clean_html(intro)}</p></section>{body}<p class="muted">Educational market intelligence only. Not financial, investment, legal, betting, or tax advice. Never share seed phrases or private keys.</p></main>
  <script>
    (function () {{
      function applyPrefs(prefs) {{
        Object.keys(prefs || {{}}).forEach(function (key) {{
          if (!key.startsWith("show_edu_nav_")) return;
          document.querySelectorAll('[data-edu-nav="' + key + '"]').forEach(function (node) {{ node.dataset.eduHidden = prefs[key] ? "false" : "true"; }});
          document.querySelectorAll('[data-edu-pref="' + key + '"]').forEach(function (input) {{ input.checked = !!prefs[key]; }});
        }});
      }}
      async function loadPrefs() {{
        try {{
          var response = await fetch("/api/education/preferences", {{ cache: "no-store", credentials: "same-origin" }});
          var prefs = await response.json();
          if (response.ok && prefs.ok) applyPrefs(prefs);
        }} catch (error) {{}}
      }}
      async function savePref(key, value) {{
        applyPrefs({{ [key]: value }});
        var response = await fetch("/api/education/preferences", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, credentials: "same-origin", body: JSON.stringify({{ [key]: value }}) }});
        var prefs = await response.json();
        if (response.ok && prefs.ok) applyPrefs(prefs);
      }}
      document.addEventListener("click", function (event) {{
        var button = event.target.closest("[data-edu-customize]");
        if (button) {{
          var panel = document.querySelector("[data-edu-customize-panel]");
          if (panel) panel.hidden = !panel.hidden;
        }}
        if (event.target.closest("[data-edu-reset]")) {{
          ["show_edu_nav_home","show_edu_nav_dashboard","show_edu_nav_education","show_edu_nav_scam_shield"].forEach(function (key) {{ savePref(key, true); }});
        }}
      }});
      document.addEventListener("change", function (event) {{
        var input = event.target.closest("[data-edu-pref]");
        if (input) savePref(input.dataset.eduPref, input.checked);
      }});
      loadPrefs();
    }})();
  </script>
</body></html>"""


def education_feature_page(h1, intro, sections, lesson_slug, cta=""):
    concepts = "".join(f"<article class='concept'><h3>{clean_html(title)}</h3><p>{clean_html(body)}</p></article>" for title, body in sections)
    quiz = """
      <div class='edu-panel'><h2>Quick Quiz</h2>
        <p><strong>Question:</strong> Should an app or support agent ever ask for your seed phrase?</p>
        <p><strong>Answer:</strong> No. A seed phrase controls funds and must never be shared.</p>
      </div>
    """
    tutor = f"""
      <div class='edu-panel'><h2>Ask CoinPilotXAI Tutor</h2>
        <form data-tutor-form><input name='question' placeholder='Ask about this lesson...' style='width:100%;min-height:44px;border-radius:10px;border:1px solid var(--line);background:#081323;color:var(--text);padding:10px'><button class='edu-cta' type='submit'>Ask Tutor</button></form>
        <p class='muted' data-tutor-response>Answers use this lesson first and stay safety-focused.</p>
      </div>
      <script>
        document.querySelector('[data-tutor-form]').addEventListener('submit', async event => {{
          event.preventDefault();
          const question = new FormData(event.target).get('question');
          const res = await fetch('/api/education/tutor', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{lesson_slug:'{clean_html(lesson_slug)}',question}})}}).then(r=>r.json()).catch(()=>({{ok:false,message:'Tutor temporarily unavailable.'}}));
          document.querySelector('[data-tutor-response]').textContent = res.response || res.message || 'Tutor temporarily unavailable.';
        }});
      </script>
    """
    return education_shell(f"{h1} | CoinPilotXAI Education", h1, intro, f"<section class='edu-panel'><div class='concepts'>{concepts}</div>{cta}</section>{quiz}{tutor}<section class='edu-panel'><a class='edu-cta' href='/education/lesson/{clean_html(lesson_slug)}'>Open Full Lesson</a></section>")


@webhook_app.route("/education", methods=["GET"])
@webhook_app.route("/dashboard/education", methods=["GET"])
def education_hub_page():
    if request.path.startswith("/dashboard"):
        user = require_account()
        if not user:
            return redirect(url_for("signup_page", next=request.path))
    init_db()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM education_categories WHERE active=1 ORDER BY sort_order ASC, title ASC")
    categories = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM education_lessons WHERE active=1 ORDER BY id ASC LIMIT 9")
    lessons = [dict(row) for row in cur.fetchall()]
    conn.close()
    cards = "".join(
        f"<a class='edu-card' href='/education/{clean_html(c['slug'])}'><span>{clean_html(c['title'])}</span><p>{clean_html(c.get('summary') or '')}</p></a>"
        for c in categories
    )
    lesson_cards = "".join(
        f"<a class='edu-card lesson' href='/education/lesson/{clean_html(l['slug'])}'><strong>{clean_html(l['title'])}</strong><small>{clean_html(l['difficulty'])} · {clean_html(l['estimated_time'])}</small><p>{clean_html(l['summary'])}</p></a>"
        for l in lessons
    )
    return education_shell(
        "Crypto Education Hub | CoinPilotXAI",
        "Crypto Education Hub",
        "Build safer crypto habits with structured lessons, quizzes, progress tracking, Scam Shield training, wallet safety, market psychology, and AI tutor support.",
        f"""
        <section class='edu-grid'>{cards}</section>
        <section class='edu-panel'><h2>Continue Learning</h2><div class='edu-grid'>{lesson_cards}</div></section>
        <section class='edu-panel'><h2>Featured Journeys</h2><div class='edu-actions'>
          <a href='/education/optimism'>Live Optimism Market Data</a>
          <a href='/education/toncoin-scenarios'>Toncoin Scenario Education</a>
          <a href='/education/scam-alerts'>Crypto Scam Alerts</a>
          <a href='/simulator'>Trading Simulator Training</a>
        </div></section>
        """,
    )


@webhook_app.route("/education/optimism", methods=["GET"])
@webhook_app.route("/dashboard/optimism", methods=["GET"])
def optimism_education_page():
    if request.path.startswith("/dashboard"):
        user = require_account()
        if not user:
            return redirect(url_for("signup_page", next=request.path))
    op = market_data_service.get_symbol("OP")
    price = op.get("price") if isinstance(op, dict) else None
    price_text = f"Current OP price from available feed: ${price}" if price else "Live Optimism market data is temporarily unavailable."
    return education_feature_page(
        "Live Optimism Market Data",
        "Learn how Optimism, rollups, gas fees, and OP token market context fit into a safer research process.",
        [
            ("What Optimism Is", "Optimism is an Ethereum Layer 2 ecosystem built to reduce transaction cost and improve throughput while still anchoring to Ethereum security assumptions."),
            ("Layer 2 Basics", "Rollups batch activity and publish proofs/data back to Ethereum. Users should still understand bridge risk, smart contract risk, and ecosystem liquidity."),
            ("OP Token Context", "OP can be affected by broader crypto liquidity, governance news, token unlocks, Ethereum activity, and Layer 2 competition."),
            ("Live Market Snapshot", price_text),
            ("Safe Reading Checklist", "Check source labels, avoid guaranteed predictions, compare time horizons, and treat market data as educational context only."),
        ],
        "optimism-market-data-education",
    )


@webhook_app.route("/education/toncoin-scenarios", methods=["GET"])
def toncoin_scenarios_page():
    return education_feature_page(
        "Toncoin Scenario Education",
        "Study Toncoin through bull/base/bear scenarios, wallet safety, ecosystem context, and Telegram-related scam awareness without making Telegram the core platform.",
        [
            ("What Toncoin Is", "Toncoin is connected to a broad ecosystem of wallets, mini-apps, and user flows. Treat ecosystem access as context, not a guarantee of token performance."),
            ("Bull/Base/Bear Cases", "Bull: stronger ecosystem adoption and liquidity. Base: range-bound activity. Bear: market stress, regulatory pressure, liquidity contraction, or security concerns."),
            ("Wallet Safety Scenarios", "Never send crypto to verify a wallet, never share recovery words, and verify bot/payment prompts against official sources."),
            ("Fake Bot and Payment Risks", "Scammers may impersonate support, create fake payment prompts, or pressure users to validate/synchronize wallets."),
            ("Decision Tree", "If a request involves urgency, seed words, wallet approvals, or unlock fees, stop and run Scam Shield before taking action."),
        ],
        "toncoin-scenario-education",
    )


@webhook_app.route("/education/scam-alerts", methods=["GET"])
def education_scam_alerts_page():
    return education_feature_page(
        "Crypto Scam Alerts",
        "Learn the most common crypto scam patterns and use a checklist before clicking links, connecting wallets, or sending funds.",
        [
            ("Fake Airdrops", "Airdrops that demand urgent wallet connection or approval signing can be wallet drainers."),
            ("Fake Support", "Support agents should never ask for seed phrases, private keys, wallet passwords, or remote-control access."),
            ("Wallet Drainers", "Malicious approvals can authorize token movement. Review transactions and revoke suspicious approvals when needed."),
            ("Guaranteed Profit Claims", "Guaranteed returns, celebrity managers, and pressure to deposit more are major red flags."),
            ("Safety Checklist", "Verify domains manually, avoid shortened links, refuse seed phrase requests, and run suspicious text through Scam Shield."),
        ],
        "scam-alert-checklist",
        cta="<a class='edu-cta' href='/scam-shield'>Run Scam Shield</a>",
    )


def education_categories_rows():
    init_db()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM education_categories WHERE active=1 ORDER BY sort_order ASC, title ASC")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def education_lesson_by_slug(slug):
    init_db()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM education_lessons WHERE slug=? AND active=1 LIMIT 1", (slug,))
    lesson = dict(cur.fetchone() or {})
    if lesson:
        cur.execute("SELECT heading, body FROM education_sections WHERE lesson_slug=? ORDER BY sort_order ASC, id ASC", (slug,))
        lesson["sections"] = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT question, options, answer, explanation FROM education_quiz_questions WHERE lesson_slug=? LIMIT 10", (slug,))
        lesson["quiz"] = [dict(row) for row in cur.fetchall()]
    conn.close()
    return lesson


@webhook_app.route("/education/<category_slug>", methods=["GET"])
def education_category_page(category_slug):
    init_db()
    category_slug = clean_html(category_slug)[:120]
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM education_categories WHERE slug=? AND active=1 LIMIT 1", (category_slug,))
    category = dict(cur.fetchone() or {})
    if not category:
        return Response("Education category not found", status=404)
    cur.execute("SELECT * FROM education_lessons WHERE category_slug=? AND active=1 ORDER BY id ASC", (category_slug,))
    lessons = [dict(row) for row in cur.fetchall()]
    conn.close()
    cards = "".join(f"<a class='edu-card' href='/education/lesson/{clean_html(l['slug'])}'><strong>{clean_html(l['title'])}</strong><small>{clean_html(l['difficulty'])} · {clean_html(l['estimated_time'])}</small><p>{clean_html(l['summary'])}</p></a>" for l in lessons)
    return education_shell(
        f"{category['title']} Lessons | CoinPilotXAI",
        category["title"],
        category.get("summary") or "Structured crypto education with safety-first lessons.",
        f"<section class='edu-grid'>{cards}</section><section class='edu-panel'><a class='edu-cta' href='/education'>Back to Education Hub</a></section>",
    )


@webhook_app.route("/education/lesson/<lesson_slug>", methods=["GET"])
def education_lesson_page(lesson_slug):
    user = load_account_by_id(account_user_id())
    lesson_slug = clean_html(lesson_slug)[:160]
    lesson = education_lesson_by_slug(lesson_slug)
    if not lesson:
        return Response("Education lesson not found", status=404)
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO education_lesson_views (user_id, lesson_slug, path, created_at) VALUES (?, ?, ?, ?)",
            ((user or {}).get("user_id") or 0, lesson_slug, request.path, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    sections = "".join(f"<article class='concept'><h3>{clean_html(s['heading'])}</h3><p>{clean_html(s['body'])}</p></article>" for s in lesson.get("sections", []))
    quiz = "".join(
        f"<article class='concept'><h3>{clean_html(q['question'])}</h3><p>Options: {', '.join(json.loads(q.get('options') or '[]'))}</p><p><strong>Answer:</strong> {clean_html(q['answer'])}. {clean_html(q['explanation'])}</p></article>"
        for q in lesson.get("quiz", [])
    )
    body = f"""
    <section class='edu-panel'><p><strong>{clean_html(lesson.get('difficulty'))}</strong> · {clean_html(lesson.get('estimated_time'))}</p><p>{clean_html(lesson.get('content'))}</p></section>
    <section class='edu-panel'><h2>Knowledge Map</h2><div class='concepts'>{sections}</div></section>
    <section class='edu-panel'><h2>Quiz</h2><div class='concepts'>{quiz}</div><button class='edu-cta' data-complete-lesson>Mark Complete</button><p class='muted' data-progress-message></p></section>
    <section class='edu-panel'><h2>Ask CoinPilotXAI Tutor</h2><form data-tutor-form><input name='question' placeholder='Ask about this lesson...' style='width:100%;min-height:44px;border-radius:10px;border:1px solid var(--line);background:#081323;color:var(--text);padding:10px'><button class='edu-cta' type='submit'>Ask Tutor</button></form><p class='muted' data-tutor-response></p></section>
    <script>
      document.querySelector('[data-complete-lesson]').addEventListener('click', async () => {{
        const res = await fetch('/api/education/progress', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{lesson_slug:'{lesson_slug}',path:'{clean_html(lesson.get('category_slug'))}',status:'completed',score:100}})}}).then(r=>r.json()).catch(()=>({{ok:false,message:'Login required to save progress.'}}));
        document.querySelector('[data-progress-message]').textContent = res.message || (res.ok ? 'Progress saved.' : 'Login required to save progress.');
      }});
      document.querySelector('[data-tutor-form]').addEventListener('submit', async event => {{
        event.preventDefault();
        const question = new FormData(event.target).get('question');
        const res = await fetch('/api/education/tutor', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{lesson_slug:'{lesson_slug}',question}})}}).then(r=>r.json()).catch(()=>({{ok:false,message:'Tutor temporarily unavailable.'}}));
        document.querySelector('[data-tutor-response]').textContent = res.response || res.message || 'Tutor temporarily unavailable.';
      }});
    </script>
    """
    return education_shell(f"{lesson['title']} | CoinPilotXAI Education", lesson["title"], lesson.get("summary") or "", body)


@webhook_app.route("/api/education/categories", methods=["GET"])
def api_education_categories():
    response = jsonify({"ok": True, "categories": education_categories_rows()})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/education/lessons", methods=["GET"])
def api_education_lessons():
    init_db()
    category = clean_html(request.args.get("category") or "")
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if category:
        cur.execute("SELECT slug, category_slug, title, difficulty, estimated_time, summary, access_level FROM education_lessons WHERE active=1 AND category_slug=? ORDER BY id ASC", (category,))
    else:
        cur.execute("SELECT slug, category_slug, title, difficulty, estimated_time, summary, access_level FROM education_lessons WHERE active=1 ORDER BY id ASC")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "lessons": rows})


@webhook_app.route("/api/education/lesson/<lesson_slug>", methods=["GET"])
def api_education_lesson(lesson_slug):
    lesson = education_lesson_by_slug(clean_html(lesson_slug)[:160])
    return jsonify({"ok": bool(lesson), "lesson": lesson}), (200 if lesson else 404)


@webhook_app.route("/api/education/tutor", methods=["POST"])
def api_education_tutor():
    user = load_account_by_id(account_user_id())
    payload = request.get_json(silent=True) or {}
    lesson_slug = clean_html(payload.get("lesson_slug") or "")[:160]
    question = clean_html(payload.get("question") or "")[:1000]
    lesson = education_lesson_by_slug(lesson_slug) if lesson_slug else {}
    if not question:
        return jsonify({"ok": False, "message": "Ask a lesson question."}), 400
    if re.search(r"(seed phrase|private key|wallet password|recovery phrase)", question, re.I):
        response = "I can explain why those secrets must never be shared, but I cannot request, store, or handle seed phrases, private keys, recovery phrases, or wallet passwords."
    else:
        response = (
            f"CoinPilotXAI Tutor: Based on {lesson.get('title') or 'this lesson'}, the safe way to think about it is: "
            f"{lesson.get('summary') or 'verify sources, slow down, and keep risk controlled.'} "
            "Use official sources, avoid urgent wallet prompts, and treat all market intelligence as educational context rather than a guarantee."
        )
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("INSERT INTO education_ai_tutor_logs (user_id, lesson_slug, question, response, created_at) VALUES (?, ?, ?, ?, ?)", ((user or {}).get("user_id") or 0, lesson_slug, question, response, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({"ok": True, "response": response})


@webhook_app.route("/api/education/quiz/submit", methods=["POST"])
def api_education_quiz_submit():
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    lesson_slug = clean_html(payload.get("lesson_slug") or "")[:160]
    score = int(payload.get("score") or 0)
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO education_user_progress (user_id, lesson_slug, status, score, updated_at) VALUES (?, ?, ?, ?, ?)",
        (user["user_id"], lesson_slug, "completed" if score >= 70 else "started", score, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "score": score, "message": "Quiz progress saved."})


@webhook_app.route("/dashboard/scam-alerts", methods=["GET"])
def dashboard_scam_alerts_page():
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next=request.path))
    body = """
    <h1>Crypto Scam Alerts</h1>
    <p>Scan suspicious messages, wallet addresses, URLs, or project names. Never enter seed phrases, private keys, recovery phrases, wallet passwords, or exchange passwords.</p>
    <form id="scam-alert-form">
      <textarea name="text" placeholder="Paste suspicious text, URL, wallet, or project name" style="width:100%;min-height:140px"></textarea>
      <button type="submit">Scan With Scam Shield</button>
    </form>
    <pre id="scam-alert-result">Result will appear here.</pre>
    <script>
      document.getElementById('scam-alert-form').addEventListener('submit', async function(event){
        event.preventDefault();
        const text = new FormData(event.target).get('text');
        const res = await fetch('/api/scam-shield', {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', cache:'no-store', body:JSON.stringify({text})});
        const data = await res.json();
        document.getElementById('scam-alert-result').textContent = data.response || data.summary || JSON.stringify(data, null, 2);
      });
    </script>
    """
    return render_account_page("custom", "Crypto Scam Alerts", current_user=user, custom_body=body)


@webhook_app.route("/markets/<symbol>/prediction", methods=["GET"])
def seo_market_prediction(symbol):
    page = market_prediction_page(symbol)
    if not page:
        return redirect(url_for("home"), code=302)
    return render_seo_landing(page)


@webhook_app.route("/markets/<symbol>/live", methods=["GET"])
def seo_market_live(symbol):
    page = market_live_page(symbol)
    if not page:
        return redirect(url_for("home"), code=302)
    return render_seo_landing(page)


@webhook_app.route("/markets/<symbol>", methods=["GET"])
def seo_market_page(symbol):
    page = market_page(symbol)
    if not page:
        return redirect(url_for("home"), code=302)
    return render_seo_landing(page)


@webhook_app.route("/country-intelligence/<country_slug>", methods=["GET"])
def seo_country_page(country_slug):
    page = country_page(country_slug)
    if not page:
        return redirect(url_for("home"), code=302)
    return render_seo_landing(page)


@webhook_app.route("/sports-edge/<sport_slug>", methods=["GET"])
def seo_sports_edge_page(sport_slug):
    page = sports_page(sport_slug)
    if not page:
        return redirect(url_for("home"), code=302)
    return render_seo_landing(page)


@webhook_app.route("/intel/<article_slug>", methods=["GET"])
def seo_intel_article_page(article_slug):
    page = article_page(article_slug)
    if not page:
        return redirect("/intel", code=302)
    return render_seo_landing(page, include_article=True)


@webhook_app.route("/news", methods=["GET"])
@webhook_app.route("/insights", methods=["GET"])
@webhook_app.route("/intel", methods=["GET"])
def seo_content_hub():
    slug = request.path.strip("/")
    page = hub_page(slug)
    return render_seo_landing(page, include_article=True)


@webhook_app.route("/<slug>", methods=["GET"])
def seo_topic_page(slug):
    page = seo_page(slug)
    if not page:
        return Response("Not found", status=404)
    return render_seo_landing(page)


@webhook_app.route("/offline", methods=["GET"])
def offline_page():
    return render_template("offline.html")


@webhook_app.route("/reset-pwa", methods=["GET"])
def reset_pwa_page():
    html = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Reset CoinPilotXAI App Cache</title>
      <style>
        body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#050b14;color:#f2fbff;font-family:Inter,system-ui,Arial,sans-serif}
        main{width:min(100%,560px);padding:28px;border:1px solid rgba(0,229,255,.22);border-radius:14px;background:#0d1627;text-align:center;box-shadow:0 24px 70px rgba(0,0,0,.35)}
        p{color:#9fb5c0;line-height:1.6}
      </style>
    </head>
    <body>
      <main>
        <h1>Resetting CoinPilotXAI app cache</h1>
        <p>Clearing old offline cache, service workers, and local app flags. You will be returned to the homepage automatically.</p>
      </main>
      <script>
        (async function () {
          try {
            console.log("[CoinPilotXAI PWA] reset started");
            if ("serviceWorker" in navigator) {
              var regs = await navigator.serviceWorker.getRegistrations();
              await Promise.all(regs.map(function (reg) { return reg.unregister(); }));
              console.log("[CoinPilotXAI PWA] service workers unregistered", regs.length);
            }
            if ("caches" in window) {
              var keys = await caches.keys();
              await Promise.all(keys.map(function (key) { return caches.delete(key); }));
              console.log("[CoinPilotXAI PWA] caches deleted", keys);
            }
            if ("indexedDB" in window && indexedDB.databases) {
              var databases = await indexedDB.databases();
              await Promise.all(databases.map(function (db) {
                return db && db.name ? new Promise(function (resolve) {
                  var req = indexedDB.deleteDatabase(db.name);
                  req.onsuccess = req.onerror = req.onblocked = function () { resolve(); };
                }) : Promise.resolve();
              }));
              console.log("[CoinPilotXAI PWA] indexedDB cleared");
            }
            try { localStorage.clear(); } catch (err) {}
            try { sessionStorage.clear(); } catch (err) {}
            try {
              localStorage.removeItem("coinpilotx_install_popup_dismissed_until");
              localStorage.removeItem("coinpilotx_offline");
              localStorage.removeItem("coinpilotx_offline_mode");
              localStorage.removeItem("offline");
            } catch (err) {}
          } finally {
            setTimeout(function () { location.href = "/?reset_pwa=1&ts=" + Date.now(); }, 1000);
          }
        })();
      </script>
    </body>
    </html>
    """
    response = Response(html, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.after_request
def add_pwa_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.headers.get("X-Forwarded-Proto", request.scheme) == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if not request.path.startswith(("/api/", "/static/")):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self' https:; frame-ancestors 'self'; base-uri 'self'; form-action 'self' https://checkout.stripe.com;",
        )
    if request.path in ("/static/service-worker.js", "/sw.js"):
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-store, max-age=0"
    elif request.path.startswith(("/static/", "/icons/")):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path in ("/sitemap.xml", "/robots.txt", "/llms.txt", "/ai-index.json", "/manifest.json", "/site.webmanifest"):
        response.headers["Cache-Control"] = "public, max-age=300"
    return response


@webhook_app.before_request
def enforce_https():
    host = request.host.split(":")[0]
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return None
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    if forwarded_proto == "http":
        return redirect(request.url.replace("http://", "https://", 1), code=301)
    return None


@webhook_app.before_request
def capture_referral_and_run_trial_maintenance():
    ref = (request.args.get("ref") or "").strip()[:80]
    if ref:
        session["referred_by"] = ref
    if request.path.startswith(("/static/", "/api/track", "/health")):
        return None
    run_trial_maintenance()
    return None


@webhook_app.before_request
def basic_abuse_guard():
    protected = {
        "/login": (12, 300),
        "/signup": (8, 300),
        "/forgot-password": (6, 300),
        "/forgot-username": (6, 300),
        "/admin/login": (8, 300),
        "/create-checkout-session": (8, 300),
        "/api/create-checkout-session": (8, 300),
        "/api/ai-assistant": (30, 300),
    }
    if request.method not in {"POST", "PUT"} or request.path not in protected:
        return None
    limit, window_seconds = protected[request.path]
    key = f"{client_ip_hash()}:{request.path}"
    now = time.time()
    bucket = [stamp for stamp in RATE_LIMIT_BUCKETS.get(key, []) if now - stamp < window_seconds]
    if len(bucket) >= limit:
        logging.warning("Rate limit triggered path=%s ip_hash=%s", request.path, client_ip_hash())
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "message": "Too many attempts. Please wait a few minutes and try again."}), 429
        return Response("Too many attempts. Please wait a few minutes and try again.", status=429)
    bucket.append(now)
    RATE_LIMIT_BUCKETS[key] = bucket
    return None


@webhook_app.before_request
def log_visitor_request():
    if request.path.startswith(("/static/", "/icons/", "/manifest", "/site.webmanifest", "/sw.js")):
        return None
    if request.path in {"/health", "/health/database"}:
        return None
    try:
        visitor_session_id = session.get("visitor_session_id")
        if not visitor_session_id:
            visitor_session_id = secrets.token_urlsafe(18)
            session["visitor_session_id"] = visitor_session_id
        now = datetime.now()
        dedupe_after = (now - timedelta(minutes=1)).isoformat()
        user_id = account_user_id() or 0
        ip_hash = client_ip_hash()
        user_agent = (request.headers.get("User-Agent") or "")[:500]
        referrer = (request.headers.get("Referer") or "")[:500]
        ua_lower = user_agent.lower()
        device_type = "mobile" if any(token in ua_lower for token in ("mobile", "iphone", "android")) else "tablet" if "ipad" in ua_lower or "tablet" in ua_lower else "desktop"
        browser = "Chrome" if "chrome" in ua_lower and "edg" not in ua_lower else "Safari" if "safari" in ua_lower and "chrome" not in ua_lower else "Firefox" if "firefox" in ua_lower else "Edge" if "edg" in ua_lower else "Other"
        os_name = "iOS" if "iphone" in ua_lower or "ipad" in ua_lower else "Android" if "android" in ua_lower else "macOS" if "mac os" in ua_lower or "macintosh" in ua_lower else "Windows" if "windows" in ua_lower else "Linux" if "linux" in ua_lower else "Other"
        path = request.path[:500]
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM visitor_logs
            WHERE session_id=? AND path=? AND timestamp>=?
            LIMIT 1
            """,
            (visitor_session_id, path, dedupe_after),
        )
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO visitor_logs
                (user_id, session_id, ip_address, user_agent, path, referrer, device_type, browser, os, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, visitor_session_id, ip_hash, user_agent, path, referrer, device_type, browser, os_name, now.isoformat()),
            )
            conn.commit()
        conn.close()
    except Exception as exc:
        logging.debug("visitor log skipped safely: %s", exc)
    return None


@webhook_app.before_request
def enforce_admin_first_password_change():
    if not request.path.startswith("/admin/"):
        return None
    allowed = {
        "/admin/login",
        "/admin/logout",
        "/admin/change-password",
        "/admin/bootstrap-owner",
    }
    if request.path in allowed:
        return None
    admin = admin_current_user()
    if admin and int(admin.get("must_change_password") or 0) == 1:
        return redirect(url_for("admin_change_password_page"))
    return None


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
    email = normalize_email(email)
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND email!='' LIMIT 1", (email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def account_display_name(user):
    return (user or {}).get("full_name") or (user or {}).get("display_name") or "CoinPilotX user"


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def format_date(value):
    parsed = parse_iso_datetime(value)
    if not parsed:
        return "Not set"
    return parsed.strftime("%b %d, %Y")


def days_until(value):
    parsed = parse_iso_datetime(value)
    if not parsed:
        return None
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return max(0, math.ceil((parsed - now).total_seconds() / 86400))


def generate_referral_code():
    return "cpx" + secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].lower()


def plan_status_label(user):
    user = user or {}
    status = (user.get("subscription_status") or "inactive").lower()
    access_type = pro_access_type(user)
    if access_type == "paid":
        return "Paid Pro Active"
    if access_type == "trial":
        return "Pro Trial"
    if has_pro_access(user):
        return "Pro Active"
    if status == "expired":
        return "Free — trial ended"
    return "Free"


def pro_access_type(user):
    return pro_access_service.pro_access_type(user or {})


def is_paid_pro_user(user):
    return pro_access_type(user) == "paid"


def is_trialing_user(user):
    return pro_access_type(user) == "trial"


def account_access_context(user):
    user = user or {}
    paid = is_paid_pro_user(user)
    trial = is_trialing_user(user)
    pro_end = user.get("pro_expires_at") or user.get("subscription_expires_at")
    trial_end = user.get("trial_end_date") or pro_end
    return {
        "label": plan_status_label(user),
        "has_pro": has_pro_access(user),
        "pro_access_type": pro_access_type(user),
        "is_paid_pro": paid,
        "is_trial": trial,
        "trial_end": format_date(trial_end),
        "pro_expires_at": format_date(pro_end),
        "days_remaining": days_until(trial_end if trial else pro_end),
        "trial_expired": (user.get("subscription_status") or "").lower() == "expired",
        "referral_code": user.get("referral_code") or "",
    }


def has_pro_access(user):
    return pro_access_service.has_pro_access(user or {})


def platform_pro_access(user):
    return has_pro_access(user)


def backend_pro_status_payload(user):
    user = user or {}
    has_access = has_pro_access(user)
    paid = is_paid_pro_user(user)
    trial = is_trialing_user(user)
    return {
        "user_id": user.get("user_id"),
        "email": mask_email(user.get("email")),
        "plan": user.get("plan") or "",
        "subscription_plan": user.get("subscription_plan") or "",
        "subscription_status": user.get("subscription_status") or "",
        "is_pro": int(user.get("is_pro") or 0),
        "has_pro_access": has_access,
        "pro_access_type": pro_access_type(user),
        "is_paid_pro": paid,
        "is_trialing": trial,
        "pro_expires_at": user.get("pro_expires_at") or user.get("subscription_expires_at") or "",
        "stripe_customer_id": user.get("stripe_customer_id") or "",
        "stripe_subscription_id": user.get("stripe_subscription_id") or "",
        "source": "database",
    }


def pro_locked_response(user, feature_name="AI Command Center"):
    log_product_event((user or {}).get("user_id") or 0, "pro_gated_blocked_attempt", {"feature": feature_name, "path": request.path})
    body = f"""
      <div class='grid'>
        <div class='profile-card'>
          <h2>You need CoinPilotXAI Pro to access the {clean_html(feature_name)}.</h2>
          <p>Pro unlocks the native AI command center, private AI chat, advanced Scam Shield, Wallet Intel, portfolio intelligence, Sports Edge context, and real-time alert tools.</p>
          <div class='actions'>
            <a class='button gold' href='/upgrade'>Upgrade to Pro</a>
            <a class='button' href='/account'>Account</a>
            <a class='button' href='/logout'>Logout</a>
          </div>
        </div>
      </div>
    """
    response = render_account_page(
        "custom",
        feature_name,
        current_user=user,
        custom_body=body,
        message="",
        error="",
    )
    response_obj = webhook_app.make_response(response)
    response_obj.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response_obj


def api_pro_required(user, feature_name="CoinPilotXAI Pro"):
    if not user:
        response = jsonify({"ok": False, "message": "Login required.", "signup_url": url_for("signup_page", next=request.path)})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    if not platform_pro_access(user):
        log_product_event(user["user_id"], "pro_gated_blocked_attempt", {"feature": feature_name, "path": request.path})
        response = jsonify({
            "ok": False,
            "error": "CoinPilotXAI Pro required.",
            "message": "You need CoinPilotXAI Pro to access the AI Command Center.",
            "upgrade_url": url_for("upgrade_page"),
            "account_url": url_for("account_page"),
        })
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 403
    return None


def render_account_page(page, title, **context):
    context.setdefault("csrf_token", get_csrf_token())
    context.setdefault("current_user", load_account_by_id(account_user_id()))
    context.setdefault("access", account_access_context(context.get("current_user")))
    context.setdefault("message", "")
    context.setdefault("error", "")
    return render_template("account.html", page=page, title=title, **context)


def require_account():
    user = load_account_by_id(account_user_id())
    if not user:
        return None
    return user


def safe_redirect_target(default_endpoint="dashboard_page"):
    target = request.args.get("next") or request.form.get("next") or ""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for(default_endpoint)


def log_auth_event(event_type, email="", user_id=0, status="info", details=None):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                email TEXT,
                user_id INTEGER,
                status TEXT,
                details TEXT,
                db_engine TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            INSERT INTO auth_events (event_type, email, user_id, status, details, db_engine, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                mask_email(normalize_email(email)) if email else "",
                int(user_id or 0),
                status,
                json.dumps(details or {})[:4000],
                db_service.ENGINE_NAME,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("Auth event logging failed: %s", exc)


def create_account(full_name, email, password, phone="", country="", email_opt_in=False, sms_opt_in=False):
    email = normalize_email(email)
    logging.info("signup normalized email=%s db_engine=%s", mask_email(email), db_service.ENGINE_NAME)
    log_auth_event("signup_started", email, status="started", details={"db_engine": db_service.ENGINE_NAME})
    now_dt = datetime.now()
    now = now_dt.isoformat()
    trial_end = (now_dt + timedelta(days=PRO_TRIAL_DAYS)).isoformat()
    password_hash = generate_password_hash(password)
    referred_by = ""
    try:
        referred_by = (session.get("referred_by") or request.args.get("ref") or "").strip()[:80]
    except Exception:
        referred_by = ""
    conn = db()
    try:
        cur = conn.cursor()
        logging.info("database insert precheck for signup email=%s engine=%s", mask_email(email), db_service.ENGINE_NAME)
        cur.execute("SELECT user_id FROM users WHERE lower(email)=lower(?) AND email!='' LIMIT 1", (email,))
        if cur.fetchone():
            conn.close()
            logging.info("duplicate email detection during signup email=%s", mask_email(email))
            log_auth_event("signup_duplicate", email, status="duplicate", details={"db_engine": db_service.ENGINE_NAME})
            return None, "An account already exists for that email."
        referral_code = generate_referral_code()
        for _ in range(5):
            cur.execute("SELECT user_id FROM users WHERE referral_code=? LIMIT 1", (referral_code,))
            if not cur.fetchone():
                break
            referral_code = generate_referral_code()
        logging.info("database insert attempt for signup email=%s engine=%s", mask_email(email), db_service.ENGINE_NAME)
        cur.execute(
            """
            INSERT INTO users (
                username, display_name, full_name, email, password_hash, phone, country,
                email_verified, email_opt_in, sms_opt_in, plan, subscription_status,
                trial_start_date, trial_end_date, trial_used, pro_expires_at,
                referral_code, referred_by, usage_ai_count, usage_reset_at,
                signup_time, created_at, updated_at, onboarding_complete, alerts_enabled, is_pro,
                subscription_plan, subscription_started_at, subscription_expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'pro', 'trialing',
                    ?, ?, 1, ?, ?, ?, 0, ?,
                    ?, ?, ?, 1, 0, 1, 'pro', ?, ?)
            """,
            (
                "",
                full_name,
                full_name,
                email,
                password_hash,
                phone,
                country,
                int(email_opt_in),
                int(sms_opt_in),
                now,
                trial_end,
                trial_end,
                referral_code,
                referred_by,
                now[:10],
                now,
                now,
                now,
                now,
                trial_end,
            )
        )
        user_id = cur.lastrowid
        logging.info("database insert generated user_id=%s email=%s", user_id, mask_email(email))
        cur.execute(
            """
            INSERT INTO subscriptions
            (user_id, plan, status, payment_type, trial_start_date, trial_end_date, pro_expires_at, created_at, updated_at)
            VALUES (?, 'pro', 'trialing', 'trial', ?, ?, ?, ?, ?)
            """,
            (user_id, now, trial_end, trial_end, now, now)
        )
        conn.commit()
        logging.info("database commit success for signup user_id=%s engine=%s", user_id, db_service.ENGINE_NAME)
        log_auth_event("signup_completed", email, user_id, status="success", details={"db_engine": db_service.ENGINE_NAME})
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("database transaction rollback during signup email=%s engine=%s error=%s", mask_email(email), db_service.ENGINE_NAME, exc)
        log_auth_event("signup_failed", email, status="failed", details={"error": str(exc)[:500], "db_engine": db_service.ENGINE_NAME})
        return None, "Account creation is temporarily unavailable. Please try again shortly."
    finally:
        try:
            conn.close()
        except Exception:
            pass
    user = load_account_by_id(user_id)
    log_product_event(user_id, "pro_trial_started", {"trial_end_date": trial_end, "source": "website_signup"})
    record_referral_signup(user_id, referred_by)
    sync_brevo_contact_safe({**(user or {}), "source": "website_account"}, entity_type="user", entity_id=user_id)
    return user, ""


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
    user = load_account_by_id(user_id)
    sync_brevo_contact_safe({**(user or {}), "source": "account_settings"}, entity_type="user", entity_id=user_id)


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
    email = normalize_email(email)
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
    if request.method == "GET" and require_account():
        return redirect(safe_redirect_target("dashboard_page"))
    if request.method == "POST":
        logging.info("signup endpoint started")
        if not verify_csrf():
            return render_account_page("signup", "Create Account", error="Security check failed. Please try again.")
        full_name = clean_html(request.form.get("full_name", ""))[:160]
        email = normalize_email(clean_html(request.form.get("email", "")))
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
        return redirect(url_for("dashboard_page"))
    return render_account_page("signup", "Create Account")


@webhook_app.route("/login", methods=["GET", "POST"])
def login_page():
    init_db()
    if request.method == "GET" and require_account():
        return redirect(url_for("dashboard_page"))
    if request.method == "POST":
        if not verify_csrf():
            return render_account_page("login", "Login", error="Security check failed. Please try again.")
        email = normalize_email(clean_html(request.form.get("email", "")))
        password = request.form.get("password", "")
        logging.info("login attempt email=%s db_engine=%s", mask_email(email), db_service.ENGINE_NAME)
        user = load_account_by_email(email)
        if not user or not user.get("password_hash") or not check_password_hash(user["password_hash"], password):
            log_auth_event("login_failed", email, user.get("user_id") if user else 0, status="failed", details={"found": bool(user), "db_engine": db_service.ENGINE_NAME})
            return render_account_page("login", "Login", error="Email or password is incorrect.")
        if (user.get("account_status") or "active").lower() != "active":
            return render_account_page("login", "Login", error="This account is not active. Please contact support@coinpilotx.app.")
        session["account_user_id"] = user["user_id"]
        log_auth_event("login_success", email, user["user_id"], status="success", details={"db_engine": db_service.ENGINE_NAME})
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_login_at=?, last_seen_at=? WHERE user_id=?", (datetime.now().isoformat(), datetime.now().isoformat(), user["user_id"]))
        conn.commit()
        conn.close()
        return redirect(safe_redirect_target("dashboard_page"))
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
    get_or_create_referral_code(user["user_id"])
    user = load_account_by_id(user["user_id"])
    return render_account_page("account", "Account", current_user=user)


@webhook_app.route("/dashboard", methods=["GET"])
def dashboard_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("login_page"))
    log_product_event(user["user_id"], "dashboard_viewed", {})
    current_user = load_account_by_id(user["user_id"])
    link_code = ""
    if current_user and not current_user.get("telegram_user_id"):
        link_code = generate_telegram_link_code(user["user_id"])
    return render_template(
        "dashboard.html",
        current_user=current_user,
        access=account_access_context(current_user),
        link_code=link_code,
    )


@webhook_app.route("/app", methods=["GET"])
@webhook_app.route("/command-center", methods=["GET"])
@webhook_app.route("/intelligence", methods=["GET"])
@webhook_app.route("/dashboard/intelligence", methods=["GET"])
def app_command_center_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next=request.path))
    fresh_user = load_account_by_id(user["user_id"]) or user
    if not platform_pro_access(fresh_user):
        return pro_locked_response(fresh_user, "AI Command Center")
    log_product_event(fresh_user["user_id"], "command_center_viewed", {"path": request.path})
    response = render_template(
        "app.html",
        current_user=fresh_user,
        access=account_access_context(fresh_user),
        menu=command_router_service.get_menu_items(fresh_user),
        is_guest=False,
        chat_mode=False,
        pro_locked=False,
    )
    response_obj = webhook_app.make_response(response)
    response_obj.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response_obj


@webhook_app.route("/chat", methods=["GET"])
def native_chat_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next="/chat"))
    fresh_user = load_account_by_id(user["user_id"]) or user
    if not platform_pro_access(fresh_user):
        return pro_locked_response(fresh_user, "Native Chat")
    log_product_event(fresh_user["user_id"], "native_chat_viewed", {"path": request.path})
    response = render_template(
        "app.html",
        current_user=fresh_user,
        access=account_access_context(fresh_user),
        menu=command_router_service.get_menu_items(fresh_user),
        is_guest=False,
        chat_mode=True,
        pro_locked=False,
    )
    response_obj = webhook_app.make_response(response)
    response_obj.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response_obj


def user_is_conversation_member(user_id, conversation_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM conversation_members WHERE user_id=? AND conversation_id=? LIMIT 1", (user_id, conversation_id))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def direct_conversation_between(user_id, other_user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id
        FROM conversations c
        JOIN conversation_members a ON a.conversation_id=c.id AND a.user_id=?
        JOIN conversation_members b ON b.conversation_id=c.id AND b.user_id=?
        WHERE c.conversation_type='direct'
        ORDER BY c.id DESC
        LIMIT 1
        """,
        (user_id, other_user_id),
    )
    row = cur.fetchone()
    now = datetime.now().isoformat()
    if row:
        conn.close()
        return row[0]
    cur.execute("INSERT INTO conversations (conversation_type, created_by, created_at, updated_at) VALUES ('direct', ?, ?, ?)", (user_id, now, now))
    conversation_id = cur.lastrowid
    cur.execute("INSERT INTO conversation_members (conversation_id, user_id, joined_at, last_read_at) VALUES (?, ?, ?, ?)", (conversation_id, user_id, now, now))
    cur.execute("INSERT INTO conversation_members (conversation_id, user_id, joined_at, last_read_at) VALUES (?, ?, ?, ?)", (conversation_id, other_user_id, now, ""))
    conn.commit()
    conn.close()
    return conversation_id


@webhook_app.route("/messages", methods=["GET"])
def messages_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next="/messages"))
    body = """
      <div class='grid'>
        <section class='profile-card'>
          <h2>Private Messages</h2>
          <p>Start secure one-to-one conversations with another CoinPilotXAI user by email. Telegram is not required.</p>
          <form id='message-start-form'>
            <label>User email or username</label>
            <input name='query' placeholder='user@example.com' required>
            <button class='button gold' type='submit'>Start Chat</button>
          </form>
          <div id='message-status' class='muted'></div>
        </section>
        <section class='profile-card'>
          <h2>Conversations</h2>
          <div id='conversation-list'>Loading...</div>
        </section>
      </div>
      <script>
        async function loadConversations(){
          const node=document.getElementById('conversation-list');
          try{
            const res=await fetch('/api/messages/conversations',{cache:'no-store',credentials:'same-origin'});
            const data=await res.json();
            if(!data.ok){node.textContent=data.message||'Unable to load conversations.';return;}
            node.innerHTML=(data.conversations||[]).map(c=>`<p><a href="/messages/${c.id}">${c.title||'Conversation'}</a> <span class="muted">${c.last_message_at||''}</span></p>`).join('')||'<p class="muted">No conversations yet.</p>';
          }catch(e){node.textContent='Messages unavailable. Try again shortly.';}
        }
        document.getElementById('message-start-form').addEventListener('submit',async(e)=>{
          e.preventDefault();
          const status=document.getElementById('message-status');
          status.textContent='Starting chat...';
          const payload={query:new FormData(e.target).get('query')};
          const res=await fetch('/api/messages/start',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',cache:'no-store',body:JSON.stringify(payload)});
          const data=await res.json();
          if(data.ok){location.href='/messages/'+data.conversation_id}else{status.textContent=data.message||'Could not start chat.'}
        });
        loadConversations();
      </script>
    """
    return render_account_page("custom", "Messages", current_user=user, custom_body=body)


@webhook_app.route("/messages/<int:conversation_id>", methods=["GET"])
def message_thread_page(conversation_id):
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next=f"/messages/{conversation_id}"))
    if not user_is_conversation_member(user["user_id"], conversation_id):
        return render_account_page("custom", "Messages", current_user=user, custom_body="<p>You do not have access to this conversation.</p>")
    body = f"""
      <section class='profile-card'>
        <h2>Conversation</h2>
        <div id='message-thread' style='display:grid;gap:10px;min-height:260px'>Loading...</div>
        <form id='message-send-form' style='display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:16px'>
          <input name='body' placeholder='Write a message...' required>
          <button class='button gold' type='submit'>Send</button>
        </form>
      </section>
      <script>
        const cid={conversation_id};
        async function loadThread(){{
          const node=document.getElementById('message-thread');
          const res=await fetch('/api/messages/'+cid,{{cache:'no-store',credentials:'same-origin'}});
          const data=await res.json();
          if(!data.ok){{node.textContent=data.message||'Unable to load messages.';return;}}
          node.innerHTML=(data.messages||[]).map(m=>`<div class="notice"><strong>${{m.sender_user_id===data.current_user_id?'You':'User '+m.sender_user_id}}</strong><p>${{m.body}}</p><span class="muted">${{m.created_at||''}}</span></div>`).join('')||'<p class="muted">No messages yet.</p>';
        }}
        document.getElementById('message-send-form').addEventListener('submit',async(e)=>{{
          e.preventDefault();
          const body=new FormData(e.target).get('body');
          e.target.reset();
          await fetch('/api/messages/'+cid+'/send',{{method:'POST',headers:{{'Content-Type':'application/json'}},credentials:'same-origin',cache:'no-store',body:JSON.stringify({{body}})}});
          loadThread();
        }});
        loadThread();
      </script>
    """
    return render_account_page("custom", "Messages", current_user=user, custom_body=body)


@webhook_app.route("/notifications", methods=["GET"])
def notifications_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("login_page", next="/notifications"))
    payload = notification_service.list_notifications(user["user_id"])
    rows = "".join(
        f"<article class='profile-card'><h3>{clean_html(item.get('title') or 'Notification')}</h3><p>{clean_html(item.get('message') or '')}</p><p class='muted'>{clean_html(item.get('created_at') or '')}</p></article>"
        for item in payload.get("notifications", [])
    ) or "<p class='muted'>No notifications yet.</p>"
    return render_account_page(
        "custom",
        "Notifications",
        current_user=user,
        message="",
        error="",
        custom_body=f"<div class='grid'>{rows}</div>",
    )


def stripe_checkout_url_for_user(user_id):
    # Website-only checkout: direct/public Stripe links are intentionally disabled.
    return ""


def stripe_config_snapshot():
    return {
        "secret_loaded": bool(STRIPE_SECRET_KEY),
        "publishable_loaded": bool(STRIPE_PUBLISHABLE_KEY),
        "webhook_loaded": bool(STRIPE_WEBHOOK_SECRET),
        "price_loaded": bool(STRIPE_PRICE_ID),
        "app_base_url": APP_BASE_URL,
    }


def safe_stripe_error(exc):
    try:
        return str(getattr(exc, "user_message", "") or getattr(exc, "message", "") or exc)
    except Exception:
        return "Unknown Stripe error"


def record_checkout_attempt(user=None, status="started", stripe_session_id="", redirect_url="", error_message=""):
    try:
        user = user or {}
        cfg = stripe_config_snapshot()
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO checkout_attempts
            (user_id, email, account_status, authenticated, stripe_secret_loaded, stripe_publishable_loaded,
             stripe_webhook_loaded, stripe_price_loaded, app_base_url, status, stripe_session_id, redirect_url,
             error_message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.get("user_id") or 0,
                user.get("email") or "",
                user.get("account_status") or "unknown",
                1 if user.get("user_id") else 0,
                int(cfg["secret_loaded"]),
                int(cfg["publishable_loaded"]),
                int(cfg["webhook_loaded"]),
                int(cfg["price_loaded"]),
                cfg["app_base_url"],
                status,
                stripe_session_id or "",
                (redirect_url or "")[:1200],
                (error_message or "")[:1000],
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("Checkout attempt logging failed: %s", exc)


def create_stripe_checkout_session(user):
    user = user or {}
    cfg = stripe_config_snapshot()
    logging.info(
        "checkout session creation start user_id=%s email_present=%s authenticated=%s account_status=%s stripe_key_loaded=%s stripe_publishable_loaded=%s stripe_webhook_loaded=%s stripe_price_id_loaded=%s app_base_url=%s",
        user.get("user_id"),
        bool(user.get("email")),
        bool(user.get("user_id")),
        user.get("account_status") or "unknown",
        cfg["secret_loaded"],
        cfg["publishable_loaded"],
        cfg["webhook_loaded"],
        cfg["price_loaded"],
        cfg["app_base_url"],
    )
    record_checkout_attempt(user, "started")
    if platform_pro_access(user):
        logging.info("pro_upgrade_blocked_already_active user_id=%s source=checkout_session", user.get("user_id"))
        log_product_event(user.get("user_id") or 0, "pro_upgrade_blocked_already_active", {"source": "checkout_session"})
        record_checkout_attempt(user, "blocked", error_message="Already active paid Pro")
        return None, "You already have CoinPilotXAI Pro active. No upgrade is needed."
    if not STRIPE_SECRET_KEY:
        logging.warning("Stripe checkout cannot be created: STRIPE_SECRET_KEY missing.")
        record_checkout_attempt(user, "failed", error_message="STRIPE_SECRET_KEY missing")
        return None, "Stripe is not configured."
    if not STRIPE_PRICE_ID:
        logging.warning("Stripe checkout cannot be created: STRIPE_PRICE_ID missing. Configure STRIPE_PRICE_ID in Railway.")
        record_checkout_attempt(user, "failed", error_message="STRIPE_PRICE_ID missing")
        return None, "Stripe price is not configured."
    user_id = str(user["user_id"])
    email = (user.get("email") or "").strip().lower()
    if not email or not is_valid_email(email):
        logging.warning("Stripe checkout cannot be created: missing valid user email user_id=%s", user_id)
        record_checkout_attempt(user, "failed", error_message="Missing valid account email")
        return None, "Account email is required for checkout."
    if (user.get("account_status") or "active").lower() != "active":
        logging.warning("Stripe checkout cannot be created: inactive account user_id=%s account_status=%s", user_id, user.get("account_status"))
        record_checkout_attempt(user, "failed", error_message="Account is not active")
        return None, "Account is not active."
    metadata = {"user_id": user_id, "email": email, "plan": "pro"}
    params = {
        "mode": "subscription",
        "client_reference_id": user_id,
        "line_items": [{"price": STRIPE_PRICE_ID, "quantity": 1}],
        "metadata": metadata,
        "subscription_data": {"metadata": metadata},
        "success_url": f"{BASE_URL}/upgrade/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{BASE_URL}/upgrade?canceled=1",
        "allow_promotion_codes": True,
    }
    if user.get("stripe_customer_id"):
        params["customer"] = user.get("stripe_customer_id")
    elif email:
        params["customer_email"] = email
    logging.info(
        "Creating Stripe checkout session user_id=%s email=%s price_configured=%s success_url=%s cancel_url=%s metadata=%s",
        user_id,
        email,
        bool(STRIPE_PRICE_ID),
        params["success_url"],
        params["cancel_url"],
        metadata,
    )
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        session_obj = stripe.checkout.Session.create(**params)
        redirect_url = getattr(session_obj, "url", "") or session_obj.get("url", "")
        session_id = getattr(session_obj, "id", "") or session_obj.get("id", "")
        logging.info("Stripe checkout session created user_id=%s session_id=%s redirect_url_generated=%s", user_id, session_id, bool(redirect_url))
        record_checkout_attempt(user, "success", stripe_session_id=session_id, redirect_url=redirect_url)
        return session_obj, ""
    except Exception as exc:
        error = safe_stripe_error(exc)
        logging.exception("Stripe checkout session creation Stripe exception user_id=%s error=%s", user_id, error)
        record_checkout_attempt(user, "failed", error_message=error)
        return None, error


@webhook_app.route("/upgrade", methods=["GET"])
def upgrade_page():
    init_db()
    user = require_account()
    logging.info(
        "upgrade route accessed authenticated=%s user_id=%s email_present=%s account_status=%s checkout_requested=%s",
        bool(user),
        (user or {}).get("user_id"),
        bool((user or {}).get("email")),
        (user or {}).get("account_status"),
        request.args.get("checkout") == "1",
    )
    if not user:
        return redirect(url_for("login_page", next="/upgrade"))
    if (user.get("account_status") or "active").lower() != "active":
        return render_account_page("account", "Account", current_user=user, error="Please contact support before upgrading. This account is not active.")
    if not user.get("email"):
        return render_account_page("account", "Account", current_user=user, error="Add an email address to your account before upgrading to Pro.")
    if platform_pro_access(user):
        logging.info("pro_upgrade_blocked_already_active user_id=%s source=upgrade_page", user.get("user_id"))
        log_product_event(user["user_id"], "pro_upgrade_blocked_already_active", {"source": "upgrade_page"})
        return render_account_page(
            "upgrade",
            "Upgrade Pro",
            current_user=load_account_by_id(user["user_id"]),
            message="You already have CoinPilotXAI Pro active. No upgrade is needed.",
        )
    if request.args.get("checkout") == "1":
        log_product_event(user["user_id"], "stripe_checkout_started", {"source": request.args.get("source", "website")})
        try:
            checkout_session, checkout_error = create_stripe_checkout_session(user)
        except Exception as exc:
            logging.exception("Stripe checkout session creation failed user_id=%s error=%s", user["user_id"], exc)
            checkout_session, checkout_error = None, str(exc)
        if checkout_session and getattr(checkout_session, "url", None):
            logging.info("upgrade route redirecting to Stripe user_id=%s session_id=%s", user["user_id"], getattr(checkout_session, "id", ""))
            return redirect(checkout_session.url, code=303)
        logging.warning("Stripe checkout session unavailable for user_id=%s reason=%s", user["user_id"], checkout_error)
        return render_account_page(
            "upgrade",
            "Upgrade Pro",
            current_user=load_account_by_id(user["user_id"]),
            error="Checkout temporarily unavailable. Please try again in a few minutes or contact support@coinpilotx.app.",
        )
    log_product_event(user["user_id"], "website_upgrade_started", {"source": request.args.get("source", "website")})
    return render_account_page("upgrade", "Upgrade Pro", current_user=load_account_by_id(user["user_id"]))


@webhook_app.route("/create-checkout-session", methods=["POST"])
@webhook_app.route("/api/create-checkout-session", methods=["POST"])
def create_checkout_session_route():
    init_db()
    user = require_account()
    logging.info(
        "checkout session API route accessed authenticated=%s user_id=%s email_present=%s account_status=%s",
        bool(user),
        (user or {}).get("user_id"),
        bool((user or {}).get("email")),
        (user or {}).get("account_status"),
    )
    if not user:
        return jsonify({"ok": False, "message": "Please log in before upgrading to Pro.", "login_url": url_for("login_page", next="/upgrade")}), 401
    if (user.get("account_status") or "active").lower() != "active":
        return jsonify({"ok": False, "message": "This account is not active. Contact support before upgrading."}), 403
    if not user.get("email"):
        return jsonify({"ok": False, "message": "Add an email address to your account before upgrading."}), 400
    if platform_pro_access(user):
        logging.info("pro_upgrade_blocked_already_active user_id=%s source=checkout_api", user.get("user_id"))
        log_product_event(user["user_id"], "pro_upgrade_blocked_already_active", {"source": "checkout_api"})
        return jsonify({
            "ok": False,
            "already_active": True,
            "message": "You already have CoinPilotXAI Pro active. No upgrade is needed.",
            "dashboard_url": url_for("dashboard_page"),
        }), 409
    try:
        checkout_session, checkout_error = create_stripe_checkout_session(user)
    except Exception as exc:
        logging.exception("Stripe checkout API route failed user_id=%s error=%s", user.get("user_id"), exc)
        checkout_session, checkout_error = None, str(exc)
    if checkout_session and getattr(checkout_session, "url", None):
        log_product_event(user["user_id"], "checkout_started", {"source": "api"})
        logging.info("checkout session API returning redirect URL user_id=%s session_id=%s", user["user_id"], getattr(checkout_session, "id", ""))
        return jsonify({"ok": True, "checkout_url": checkout_session.url, "session_id": getattr(checkout_session, "id", "")})
    return jsonify({"ok": False, "message": "Checkout temporarily unavailable. Please try again in a few minutes or contact support@coinpilotx.app."}), 503


@webhook_app.route("/checkout", methods=["GET", "POST"])
def checkout_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("login_page", next="/upgrade"))
    if request.method == "POST":
        return create_checkout_session_route()
    return redirect(url_for("upgrade_page", checkout="1", source=request.args.get("source", "website")))


@webhook_app.route("/upgrade/success", methods=["GET"])
@webhook_app.route("/checkout/success", methods=["GET"])
def upgrade_success_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("login_page"))
    return render_account_page(
        "upgrade_success",
        "Upgrade Confirmation",
        current_user=user,
        message="Your Pro upgrade is being confirmed. You will receive a confirmation email shortly. If you experience any issue after payment, email support@coinpilotx.app.",
    )


@webhook_app.errorhandler(405)
def friendly_method_not_allowed(error):
    init_db()
    if request.path in {"/account", "/dashboard", "/upgrade", "/checkout/success", "/upgrade/success"}:
        if require_account():
            return redirect(url_for("dashboard_page"))
        return redirect(url_for("login_page"))
    return (
        "<!doctype html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>CoinPilotXAI Inc. | Page Method</title>"
        "<style>body{margin:0;font-family:system-ui;background:#050b14;color:#f2fbff;display:grid;min-height:100vh;place-items:center;padding:24px}"
        ".card{max-width:620px;border:1px solid rgba(110,223,246,.22);border-radius:16px;background:#0d1627;padding:28px;box-shadow:0 24px 80px rgba(0,0,0,.32)}"
        "a{color:#36e58f}</style></head><body><main class='card'>"
        "<h1>Let’s get you back on track.</h1>"
        "<p>That page was opened with the wrong request method. Your account is safe.</p>"
        "<p><a href='/dashboard'>Open Dashboard</a> · <a href='/login'>Login</a> · <a href='/support'>Support</a></p>"
        "</main></body></html>",
        405,
    )


@webhook_app.route("/settings", methods=["GET", "POST"])
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
        email = normalize_email(clean_html(request.form.get("email", "")))
        logging.info("forgot password requested email=%s db_engine=%s", mask_email(email), db_service.ENGINE_NAME)
        user, token = create_password_reset(email)
        if user and token:
            reset_link = url_for("reset_password_page", token=token, _external=True)
            send_password_reset_email(user, reset_link)
            log_product_event(user.get("user_id"), "forgot_password_requested", {})
            log_auth_event("forgot_password_token_created", email, user.get("user_id"), status="success", details={"db_engine": db_service.ENGINE_NAME})
        else:
            log_auth_event("forgot_password_no_match", email, status="not_found", details={"db_engine": db_service.ENGINE_NAME})
        message = "If that email has an account, a password reset link will be sent."
    return render_account_page("forgot", "Forgot Password", message=message)


@webhook_app.route("/forgot-username", methods=["GET", "POST"])
def forgot_username_page():
    init_db()
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            return render_account_page("forgot_username", "Recover Account", error="Security check failed. Please try again.")
        email = normalize_email(clean_html(request.form.get("email", "")))
        user = load_account_by_email(email) if is_valid_email(email) else None
        if user:
            send_username_recovery_email(user)
            log_product_event(user.get("user_id"), "username_recovery_requested", {})
        message = "If that email has an account, CoinPilotX will send login recovery details."
    return render_account_page("forgot_username", "Recover Account", message=message)


@webhook_app.route("/verify-email/<token>", methods=["GET"])
@webhook_app.route("/verify-email", methods=["GET"])
def verify_email_page(token=""):
    init_db()
    token = clean_html(token or request.args.get("token", ""))
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
    verified_user = load_account_by_id(row[0])
    if verified_user and verified_user.get("referred_by"):
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE referral_code=? LIMIT 1", (verified_user.get("referred_by"),))
        referrer = cur.fetchone()
        conn.close()
        if referrer:
            evaluate_referral_reward(referrer[0])
    return render_account_page("login", "Login", message="Email verified. You can log in anytime.")


@webhook_app.route("/reset-password/<token>", methods=["GET", "POST"])
@webhook_app.route("/reset-password", methods=["GET", "POST"])
def reset_password_page(token=""):
    init_db()
    token = clean_html(token or request.args.get("token") or request.form.get("token") or "")
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
        user_id = row[0]
        cur.execute("UPDATE users SET password_hash=?, updated_at=? WHERE user_id=?", (generate_password_hash(password), datetime.now().isoformat(), user_id))
        cur.execute("UPDATE password_reset_tokens SET used_at=? WHERE token=?", (datetime.now().isoformat(), token))
        conn.commit()
        conn.close()
        changed_user = load_account_by_id(user_id)
        if changed_user:
            send_password_changed_email(changed_user)
            log_product_event(user_id, "password_reset_completed", {})
        return redirect(url_for("login_page"))
    return render_account_page("reset", "Reset Password", token=token)


@webhook_app.route("/admin/users", methods=["GET"])
def admin_users_page():
    admin, denied = require_admin_page("users.view")
    if denied:
        return denied
    data = admin_users_payload()
    rows = "".join(
        f"<tr><td><a href='/admin/users/{u.get('user_id')}'>{clean_html(u.get('name') or 'User')}</a></td>"
        f"<td>{clean_html(u.get('email') or '')}</td><td>{u.get('user_id')}</td><td>{clean_html(u.get('account_status') or '')}</td>"
        f"<td>{clean_html(u.get('plan') or '')}</td><td>{clean_html(u.get('subscription_status') or '')}</td>"
        f"<td>{'Yes' if u.get('has_pro_access') else 'No'}</td><td>{clean_html(u.get('pro_access_type') or '')}</td>"
        f"<td>{clean_html(str(u.get('total_revenue') or 0))}</td><td>{clean_html(u.get('created_at') or '')}</td></tr>"
        for u in data.get("users", [])
    )
    filter_links = " ".join(f"<a class='button secondary' href='/admin/users?filter={key}'>{label}</a>" for key, label in [
        ("all", "All"), ("pro", "Pro"), ("trial", "Trials"), ("free", "Free"), ("restricted", "Restricted"), ("suspended", "Suspended"), ("deleted", "Deleted"), ("payment_issue", "Payment Issues")
    ])
    body = (
        "<h1>User Management Center</h1>"
        "<p class='muted'>Owner-grade user database, Pro status, payments, email logs, restrictions, and account controls.</p>"
        f"<div class='card'>{filter_links}</div>"
        "<div class='card'><table><tr><th>Name</th><th>Email</th><th>ID</th><th>Status</th><th>Plan</th><th>Subscription</th><th>Pro Access</th><th>Type</th><th>Revenue</th><th>Signup</th></tr>"
        f"{rows}</table></div>"
    )
    return admin_page_html("User Management", body, admin)


def admin_users_payload():
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    search = clean_html(request.args.get("q", "")).strip().lower()
    filter_key = clean_html(request.args.get("filter", "all")).strip().lower()
    cur.execute(
        """
        SELECT u.*,
               COALESCE(SUM(CASE WHEN lower(COALESCE(p.status,''))='succeeded' THEN COALESCE(p.amount,0) ELSE 0 END),0) AS total_revenue,
               COUNT(p.id) AS payment_count,
               MAX(p.created_at) AS last_payment_at
        FROM users u
        LEFT JOIN payment_records p ON p.user_id=u.user_id
        WHERE COALESCE(u.email,'')!=''
        GROUP BY u.user_id
        ORDER BY COALESCE(u.created_at,u.signup_time,'') DESC
        LIMIT 500
        """
    )
    users = []
    for row in [dict(r) for r in cur.fetchall()]:
        status = (row.get("account_status") or "active").lower()
        access_type = pro_access_type(row)
        if search and search not in " ".join(str(row.get(k) or "").lower() for k in ("email", "full_name", "display_name", "phone", "user_id", "telegram_user_id", "stripe_customer_id")):
            continue
        if filter_key == "pro" and access_type != "paid":
            continue
        if filter_key == "trial" and access_type != "trial":
            continue
        if filter_key == "free" and has_pro_access(row):
            continue
        if filter_key in {"restricted", "suspended", "deleted"} and status != filter_key:
            continue
        if filter_key == "payment_issue" and (row.get("subscription_status") or "").lower() not in {"past_due", "unpaid", "canceled"}:
            continue
        users.append({
            "user_id": row.get("user_id"),
            "name": row.get("full_name") or row.get("display_name") or row.get("username") or "",
            "email": mask_email(row.get("email")),
            "phone": mask_phone(row.get("phone") or ""),
            "country": row.get("country") or "",
            "account_status": row.get("account_status") or "active",
            "plan": row.get("plan") or row.get("subscription_plan") or "free",
            "subscription_status": row.get("subscription_status") or "inactive",
            "trial_status": row.get("trial_status") or "",
            "has_pro_access": has_pro_access(row),
            "pro_access_type": access_type,
            "signup_date": row.get("created_at") or row.get("signup_time") or "",
            "created_at": row.get("created_at") or row.get("signup_time") or "",
            "last_login": row.get("last_login_at") or "",
            "last_seen": row.get("last_seen_at") or "",
            "telegram_linked": bool(row.get("telegram_user_id")),
            "payment_count": row.get("payment_count") or 0,
            "total_revenue": round(float(row.get("total_revenue") or 0), 2),
            "email_delivery_status": "",
            "risk_status": row.get("restricted_reason") or row.get("suspended_reason") or "",
        })
    conn.close()
    return {"ok": True, "filter": filter_key, "count": len(users), "users": users}


@webhook_app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    admin, denied = require_admin_api("users.view")
    if denied:
        return denied
    payload = admin_users_payload()
    log_admin_audit(admin.get("id"), "admin_api_users_viewed", "user", "", {"count": payload.get("count"), "filter": payload.get("filter")})
    return jsonify(payload)


@webhook_app.route("/api/admin/users/<int:user_id>", methods=["GET"])
def api_admin_user_detail(user_id):
    admin, denied = require_admin_api("users.view")
    if denied:
        return denied
    profile = get_user_full_profile(user_id)
    if not profile:
        return jsonify({"ok": False, "error": "User not found."}), 404
    log_admin_audit(admin.get("id"), "admin_api_user_profile_viewed", "user", str(user_id), {})
    return jsonify({"ok": True, **profile})


def owner_update_user_status(user_id, status, reason, admin):
    status = clean_html(status or "")[:40].lower()
    allowed = {"active", "restricted", "suspended", "deleted"}
    if status not in allowed:
        return {"ok": False, "error": "Invalid status."}, 400
    field = "restricted_reason" if status == "restricted" else "suspended_reason" if status == "suspended" else None
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    if status == "deleted":
        cur.execute("UPDATE users SET account_status='deleted', deleted_at=?, updated_at=? WHERE user_id=?", (now, now, user_id))
    elif field:
        cur.execute(f"UPDATE users SET account_status=?, {field}=?, updated_at=? WHERE user_id=?", (status, reason[:500], now, user_id))
    else:
        cur.execute("UPDATE users SET account_status='active', restricted_reason='', suspended_reason='', updated_at=? WHERE user_id=?", (now, user_id))
    conn.commit()
    conn.close()
    admin_user_action(admin, user_id, f"user_status_{status}", {"reason": reason})
    return {"ok": True, "user": backend_pro_status_payload(load_account_by_id(user_id) or {})}, 200


@webhook_app.route("/api/admin/users/<int:user_id>/pro", methods=["POST"])
def api_admin_user_pro(user_id):
    admin, denied = require_owner_api()
    if denied:
        return denied
    user = load_account_by_id(user_id)
    if not user:
        return jsonify({"ok": False, "error": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    action = clean_html(payload.get("action") or "activate").lower()
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    if action in {"activate", "pro"}:
        days = int(payload.get("days") or 365)
        expires = (datetime.now() + timedelta(days=max(1, min(days, 3650)))).isoformat()
        cur.execute(
            """
            UPDATE users SET account_status='active', plan='pro', subscription_plan='pro',
                subscription_status='active', trial_status='converted', is_pro=1,
                pro_expires_at=?, subscription_expires_at=?, updated_at=?
            WHERE user_id=?
            """,
            (expires, expires, now, user_id),
        )
        conn.commit()
        conn.close()
        payment_id = record_payment_record(user_id, stripe_event_id=f"owner_pro_{int(time.time())}_{user_id}", amount=0, currency="USD", status="succeeded", payment_type="owner_manual_pro", manual=True, metadata={"admin_id": admin.get("id"), "action": action})
        fresh = load_account_by_id(user_id) or {}
        if fresh.get("email"):
            send_platform_email(fresh.get("email"), "CoinPilotXAI Pro Activated", "Your CoinPilotXAI Pro access has been activated by the owner/admin.", "<p>Your CoinPilotXAI Pro access has been activated by the owner/admin.</p>", user_id)
        admin_user_action(admin, user_id, "owner_activate_pro", {"payment_record_id": payment_id, "expires": expires})
    elif action in {"downgrade", "free", "remove"}:
        cur.execute("UPDATE users SET plan='free', subscription_plan='free', subscription_status='inactive', trial_status='', is_pro=0, pro_expires_at='', subscription_expires_at='', updated_at=? WHERE user_id=?", (now, user_id))
        conn.commit()
        conn.close()
        fresh = load_account_by_id(user_id) or {}
        admin_user_action(admin, user_id, "owner_remove_pro", {})
    elif action in {"start_trial", "extend_trial"}:
        days = int(payload.get("days") or 30)
        existing = parse_iso_datetime(user.get("trial_end_date") or user.get("pro_expires_at")) if action == "extend_trial" else None
        base = existing if existing and existing > datetime.now(existing.tzinfo) else datetime.now()
        trial_end = (base + timedelta(days=max(1, min(days, 365)))).isoformat()
        cur.execute(
            """
            UPDATE users SET account_status='active', plan='pro', subscription_plan='pro',
                subscription_status='trialing', trial_status='active', is_pro=1,
                trial_start_date=COALESCE(trial_start_date, ?), trial_end_date=?, pro_expires_at=?, updated_at=?
            WHERE user_id=?
            """,
            (now, trial_end, trial_end, now, user_id),
        )
        conn.commit()
        conn.close()
        fresh = load_account_by_id(user_id) or {}
        admin_user_action(admin, user_id, f"owner_{action}", {"trial_end_date": trial_end})
    else:
        conn.close()
        return jsonify({"ok": False, "error": "Invalid Pro action."}), 400
    return jsonify({"ok": True, "message": "User Pro status updated.", "user": backend_pro_status_payload(fresh)})


@webhook_app.route("/api/admin/users/<int:user_id>/status", methods=["POST"])
@webhook_app.route("/api/admin/users/<int:user_id>/restrict", methods=["POST"])
@webhook_app.route("/api/admin/users/<int:user_id>/delete", methods=["POST"])
@webhook_app.route("/api/admin/users/<int:user_id>/restore", methods=["POST"])
def api_admin_user_status(user_id):
    admin, denied = require_owner_api()
    if denied:
        return denied
    if not load_account_by_id(user_id):
        return jsonify({"ok": False, "error": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    if request.path.endswith("/restrict"):
        status = "restricted"
    elif request.path.endswith("/delete"):
        status = "deleted"
    elif request.path.endswith("/restore"):
        status = "active"
    else:
        status = payload.get("account_status") or payload.get("status")
    result, code = owner_update_user_status(user_id, status, clean_html(payload.get("reason") or ""), admin)
    return jsonify(result), code


@webhook_app.route("/api/admin/users/<int:user_id>/resend-email", methods=["POST"])
def api_admin_user_resend_email(user_id):
    admin, denied = require_admin_api("emails.resend")
    if denied:
        return denied
    user = load_account_by_id(user_id)
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "User email not found."}), 404
    payload = request.get_json(silent=True) or {}
    email_type = clean_html(payload.get("email_type") or "account_update")[:80]
    subject = "CoinPilotXAI Account Update" if email_type == "account_update" else "CoinPilotXAI Pro Activated"
    text = "This is a CoinPilotXAI account update from support."
    html = "<p>This is a CoinPilotXAI account update from support.</p>"
    sent = send_platform_email(user.get("email"), subject, text, html, user_id)
    admin_user_action(admin, user_id, "admin_resend_user_email", {"email_type": email_type, "sent": bool(sent)})
    return jsonify({"ok": bool(sent), "status": "sent" if sent else "queued_or_failed"})


@webhook_app.route("/api/admin/users/<int:user_id>/notes", methods=["POST"])
def api_admin_user_notes(user_id):
    admin, denied = require_admin_api("users.edit")
    if denied:
        return denied
    if not load_account_by_id(user_id):
        return jsonify({"ok": False, "error": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    note = clean_html(payload.get("note") or "")[:2000]
    if not note:
        return jsonify({"ok": False, "error": "Note required."}), 400
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO admin_user_notes (user_id, admin_user_id, note, created_at) VALUES (?, ?, ?, ?)", (user_id, admin.get("id"), note, now))
    cur.execute("INSERT INTO support_notes (user_id, admin_user_id, note, status, created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?)", (user_id, admin.get("id"), note, now, now))
    conn.commit()
    conn.close()
    admin_user_action(admin, user_id, "admin_user_note_added", {"note_preview": note[:80]})
    return jsonify({"ok": True, "message": "Note saved."})


@webhook_app.route("/admin/test-email", methods=["POST"])
def admin_test_email():
    if not require_admin_password():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    init_db()
    payload = request.get_json(silent=True) or request.form
    email = normalize_email(clean_html(payload.get("email", "")))
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


def sync_brevo_contact_safe(record, entity_type="user", entity_id=None):
    try:
        return brevo_contacts_service.sync_user_to_brevo(record, entity_type=entity_type, entity_id=entity_id)
    except Exception as exc:
        logging.warning("brevo contact sync failed safely: entity_type=%s entity_id=%s error=%s", entity_type, entity_id, exc)
        return {"ok": False, "status": "failed", "error": str(exc)}


def log_product_event(user_id, event_name, metadata=None):
    try:
        in_request = has_request_context()
        user_agent = request.headers.get("User-Agent", "") if in_request else ""
        device_type, browser = parse_device(user_agent)
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO analytics_events
            (session_id, user_id, event_name, page_url, referrer, device_type, browser, ip_hash, country, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.cookies.get("coinpilotxai_session_id", "") if in_request else "",
                user_id,
                event_name,
                request.path if in_request else "",
                request.headers.get("Referer", "") if in_request else "",
                device_type,
                browser,
                client_ip_hash() if in_request else "",
                "",
                json.dumps(metadata or {})[:4000],
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("Product event log failed: %s", exc)


def trial_email_sent(user_id, event_type):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM trial_email_events WHERE user_id=? AND event_type=? LIMIT 1", (user_id, event_type))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def record_trial_email_event(user_id, event_type, email, status):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO trial_email_events (user_id, event_type, email, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, event_type, email or "", status, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        log_product_event(user_id, "pro_trial_email_sent", {"event_type": event_type, "status": status})
    except Exception as exc:
        logging.info("Trial email event log failed: %s", exc)


def record_referral_signup(new_user_id, referral_code):
    if not referral_code:
        return
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE referral_code=? LIMIT 1", (referral_code,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return
        referrer_user_id = row[0]
        if referrer_user_id == new_user_id:
            conn.close()
            return
        cur.execute(
            """
            INSERT OR IGNORE INTO referral_rewards
            (referrer_user_id, referred_user_id, referral_code, reward_type, reward_days, status, created_at)
            VALUES (?, ?, ?, 'invite_3_verified', 30, 'pending', ?)
            """,
            (referrer_user_id, new_user_id, referral_code, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        log_product_event(new_user_id, "referral_signup", {"referral_code": referral_code, "referrer_user_id": referrer_user_id})
    except Exception as exc:
        logging.info("Referral signup tracking failed: %s", exc)


def evaluate_referral_reward(referrer_user_id):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT referral_code FROM users WHERE user_id=?", (referrer_user_id,))
        row = cur.fetchone()
        referral_code = row[0] if row else ""
        if not referral_code:
            conn.close()
            return
        cur.execute("SELECT COUNT(*) FROM users WHERE referred_by=? AND email_verified=1", (referral_code,))
        verified_count = cur.fetchone()[0]
        cur.execute(
            "SELECT id FROM referral_rewards WHERE referrer_user_id=? AND reward_type='invite_3_verified' AND status='granted' LIMIT 1",
            (referrer_user_id,),
        )
        already_granted = cur.fetchone()
        conn.close()
        if verified_count >= 3 and not already_granted:
            grant_referral_reward(referrer_user_id, referral_code)
    except Exception as exc:
        logging.info("Referral reward evaluation failed: %s", exc)


def grant_referral_reward(user_id, referral_code, reward_days=30):
    now_dt = datetime.now()
    user = load_account_by_id(user_id)
    existing_end = parse_iso_datetime((user or {}).get("pro_expires_at"))
    start_dt = existing_end if existing_end and existing_end > now_dt else now_dt
    new_end = start_dt + timedelta(days=reward_days)
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET plan='pro', subscription_plan='pro', subscription_status='active', is_pro=1,
            pro_expires_at=?, subscription_expires_at=?, updated_at=?
        WHERE user_id=?
        """,
        (new_end.isoformat(), new_end.isoformat(), now_dt.isoformat(), user_id),
    )
    cur.execute(
        """
        UPDATE referral_rewards
        SET status='granted', granted_at=?
        WHERE referrer_user_id=? AND referral_code=? AND reward_type='invite_3_verified'
        """,
        (now_dt.isoformat(), user_id, referral_code),
    )
    conn.commit()
    conn.close()
    log_product_event(user_id, "referral_reward_granted", {"reward_days": reward_days, "pro_expires_at": new_end.isoformat()})
    refreshed = load_account_by_id(user_id)
    if refreshed:
        sync_brevo_contact_safe({**refreshed, "source": "referral_reward"}, entity_type="user", entity_id=user_id)


def get_or_create_referral_code(user_id):
    if not user_id:
        return ""
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT referral_code FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row and row[0]:
            conn.close()
            return row[0]
        code = "cpx" + secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].lower()
        cur.execute("UPDATE users SET referral_code=?, updated_at=? WHERE user_id=?", (code, datetime.now().isoformat(), user_id))
        conn.commit()
        conn.close()
        return code
    except Exception:
        conn.close()
        return ""


@webhook_app.route("/api/referral-link", methods=["GET"])
def referral_link_api():
    user_id = account_user_id()
    if not user_id:
        return jsonify({"ok": False, "message": "Login required."}), 401
    code = get_or_create_referral_code(user_id)
    if not code:
        return jsonify({"ok": False, "message": "Referral link unavailable."}), 500
    return jsonify({
        "ok": True,
        "code": code,
        "url": f"https://coinpilotx.app/r/{code}",
        "message": "Share CoinPilotXAI Inc. with people who want safer crypto intelligence. No fake urgency, no spam.",
    })


@webhook_app.route("/r/<referral_code>", methods=["GET"])
def referral_redirect(referral_code):
    code = clean_html(referral_code)[:80]
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE referral_code=? LIMIT 1", (code,))
    row = cur.fetchone()
    referrer_user_id = row[0] if row else None
    cur.execute(
        """
        INSERT INTO referral_events (referral_code, referrer_user_id, session_id, landing_page, referrer, ip_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            referrer_user_id,
            request.cookies.get("coinpilotxai_session_id", ""),
            request.path,
            request.headers.get("Referer", ""),
            client_ip_hash(),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return redirect(f"/?ref={quote(code)}&utm_source=referral&utm_medium=share&utm_campaign=user_referral", code=302)


@webhook_app.route("/api/leads", methods=["POST"])
def leads_api():
    init_db()
    payload = request.get_json(silent=True) or {}
    email = normalize_email(clean_html(payload.get("email", "")))
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
    lead_record = {
        "email": email,
        "full_name": values[0],
        "phone": values[2],
        "country": values[3],
        "source": values[4],
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
        "email_opt_in": email_opt_in,
        "sms_opt_in": sms_opt_in,
        "telegram_username": values[10],
        "created_at": now,
        "plan": "free",
    }
    sync_brevo_contact_safe(lead_record, entity_type="lead", entity_id=lead_id)
    if email_opt_in and is_new_lead:
        send_update_signup_email({
            "id": lead_id,
            "email": email,
            "full_name": values[0],
        })
    return jsonify({"ok": True, "message": "Thanks — you’re on the CoinPilotXAI Inc. update list."})


def require_admin_password():
    if session.get("admin_user_id"):
        return True
    expected = os.getenv("ADMIN_ANALYTICS_PASSWORD", "")
    if not expected:
        return False
    supplied = request.args.get("password") or request.headers.get("X-Admin-Password", "")
    return supplied == expected


def admin_current_user():
    admin_id = session.get("admin_user_id")
    if not admin_id:
        return None
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_users WHERE id=? AND status='active' LIMIT 1", (admin_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def load_admin_by_email(email):
    email = normalize_email(email)
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_users WHERE lower(email)=lower(?) LIMIT 1", (email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def log_admin_audit(admin_user_id, action, target_type="", target_id="", metadata=None):
    try:
        admin = admin_current_user() if has_request_context() else None
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO admin_audit_logs
            (admin_user_id, admin_email, action, target_type, target_id, metadata, ip_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin_user_id or (admin or {}).get("id") or 0,
                (admin or {}).get("email") or "",
                action,
                target_type,
                target_id,
                json.dumps(metadata or {})[:4000],
                client_ip_hash() if has_request_context() else "",
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("Admin audit log failed: %s", exc)


def admin_login_required():
    admin = admin_current_user()
    if admin:
        return admin
    return None


@webhook_app.route("/admin", methods=["GET"])
def admin_root_page():
    admin = admin_current_user()
    if admin:
        return redirect(url_for("admin_dashboard_page"))
    return redirect(url_for("admin_login_page"))


def admin_password_is_strong(password):
    if len(password or "") < 12:
        return False, "Use at least 12 characters."
    checks = [
        (r"[A-Z]", "one uppercase letter"),
        (r"[a-z]", "one lowercase letter"),
        (r"\d", "one number"),
        (r"[^A-Za-z0-9]", "one symbol"),
    ]
    missing = [label for pattern, label in checks if not re.search(pattern, password)]
    if missing:
        return False, "Include " + ", ".join(missing) + "."
    return True, ""


def owner_profile_missing_fields(admin):
    required = [
        "date_of_birth",
        "address_line1",
        "city",
        "state",
        "zip_code",
        "country",
        "job_title",
        "emergency_contact_name",
        "emergency_contact_phone",
    ]
    return [field for field in required if not (admin or {}).get(field)]


def update_admin_failed_login(admin):
    if not admin:
        log_admin_audit(0, "admin_login_failed", "admin_user", "", {"reason": "unknown_email"})
        return
    failed_count = int(admin.get("failed_login_count") or 0) + 1
    locked_until = ""
    if failed_count >= 5:
        locked_until = (datetime.now() + timedelta(minutes=15)).isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE admin_users SET failed_login_count=?, locked_until=?, updated_at=? WHERE id=?",
        (failed_count, locked_until, datetime.now().isoformat(), admin["id"]),
    )
    conn.commit()
    conn.close()
    log_admin_audit(admin.get("id"), "admin_login_failed", "admin_user", str(admin.get("id")), {"locked": bool(locked_until)})


def repair_trialing_users_with_successful_payments():
    try:
        conn = db()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT u.user_id
            FROM users u
            JOIN payment_records p ON p.user_id=u.user_id AND p.status='succeeded'
            WHERE lower(COALESCE(u.subscription_status,''))='trialing'
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
        converted = 0
        for row in rows:
            cur.execute(
                """
                UPDATE users
                SET plan='pro', subscription_plan='pro', subscription_status='active',
                    trial_status='converted', is_pro=1, updated_at=?
                WHERE user_id=?
                """,
                (datetime.now().isoformat(), row["user_id"]),
            )
            converted += cur.rowcount
        conn.commit()
        conn.close()
        if converted:
            logging.info("TRIAL_TO_PAID_CONVERSION auto_repair converted=%s", converted)
        return converted
    except Exception as exc:
        logging.warning("Trial-to-paid auto repair failed: %s", exc)
        return 0


def repair_paid_users_from_payment_records():
    try:
        conn = db()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id, MAX(p.stripe_customer_id) AS stripe_customer_id,
                   MAX(p.stripe_subscription_id) AS stripe_subscription_id,
                   MAX(p.created_at) AS latest_payment_at
            FROM users u
            JOIN payment_records p ON p.user_id=u.user_id AND p.status='succeeded'
            WHERE lower(COALESCE(u.subscription_status,''))!='active'
               OR lower(COALESCE(u.plan,''))!='pro'
            GROUP BY u.user_id
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
        repaired = 0
        repaired_users = []
        for row in rows:
            cur.execute(
                """
                UPDATE users
                SET plan='pro',
                    subscription_plan='pro',
                    subscription_status='active',
                    trial_status='converted',
                    is_pro=1,
                    stripe_customer_id=COALESCE(?, stripe_customer_id),
                    stripe_subscription_id=COALESCE(?, stripe_subscription_id),
                    updated_at=?
                WHERE user_id=?
                """,
                (
                    row.get("stripe_customer_id"),
                    row.get("stripe_subscription_id"),
                    datetime.now().isoformat(),
                    row["user_id"],
                ),
            )
            repaired += cur.rowcount
            repaired_users.append(dict(row))
        conn.commit()
        conn.close()
        if repaired:
            logging.warning("DATA_RECOVERY paid_user_repair repaired=%s", repaired)
        for row in repaired_users:
            user = load_account_by_id(row.get("user_id"))
            if user:
                send_successful_payment_email_bundle(user, {
                    "stripe_customer_id": row.get("stripe_customer_id") or "",
                    "stripe_subscription_id": row.get("stripe_subscription_id") or "",
                    "payment_id": f"repair-{row.get('user_id')}-{row.get('latest_payment_at') or ''}",
                    "billing_date": format_date(row.get("latest_payment_at") or datetime.now().isoformat()),
                }, force=False)
                log_product_event(row.get("user_id"), "stripe_paid_user_repaired", {"source": "admin_data_recovery"})
        return repaired
    except Exception as exc:
        logging.warning("DATA_RECOVERY paid_user_repair_failed error=%s", exc)
        return 0


def admin_saas_summary():
    init_db()
    repair_trialing_users_with_successful_payments()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    since_day = (datetime.now() - timedelta(days=1)).isoformat()
    since_week = (datetime.now() - timedelta(days=7)).isoformat()
    cur.execute("SELECT COUNT(*) FROM users WHERE email!=''")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE email!='' AND created_at>=?", (since_day,))
    new_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE email!='' AND created_at>=?", (since_week,))
    new_week = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE telegram_user_id IS NOT NULL")
    telegram_linked = cur.fetchone()[0]
    cur.execute("SELECT * FROM users WHERE email!=''")
    user_rows = [dict(row) for row in cur.fetchall()]
    paid_pro = sum(1 for row in user_rows if is_paid_pro_user(row))
    trial_users = sum(1 for row in user_rows if is_trialing_user(row))
    free_users = sum(1 for row in user_rows if not has_pro_access(row))
    active_pro_access = paid_pro + trial_users
    cur.execute("SELECT COUNT(*) FROM users WHERE lower(COALESCE(subscription_status,'')) IN ('past_due','unpaid')")
    failed_payments = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM unmatched_payments WHERE resolved_at IS NULL")
    unmatched = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM email_logs WHERE created_at>=?", (since_day,))
    emails_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM analytics_events WHERE created_at>=?", (since_day,))
    events_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT session_id) FROM visitor_logs WHERE timestamp>=?", (since_day,))
    visitors_24h = cur.fetchone()[0] or 0
    cur.execute("SELECT COALESCE(SUM(COALESCE(amount, 14.99)), 0) FROM payment_records WHERE status='succeeded'")
    total_revenue = cur.fetchone()[0] or 0
    cur.execute(
        """
        SELECT COALESCE(SUM(latest_amount), 0) FROM (
            SELECT user_id, MAX(COALESCE(amount, 14.99)) AS latest_amount
            FROM payment_records
            WHERE status='succeeded'
            GROUP BY user_id
        )
        """
    )
    mrr_from_payments = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM admin_audit_logs")
    audit_count = cur.fetchone()[0]
    conn.close()
    mrr_estimate = float(mrr_from_payments or 0) if mrr_from_payments else paid_pro * 14.99
    logging.info(
        "ADMIN_METRICS_QUERY_RESULT total_users=%s paid_pro=%s trial_users=%s free_users=%s total_revenue=%s mrr=%s source=backend_helpers",
        total_users,
        paid_pro,
        trial_users,
        free_users,
        total_revenue,
        mrr_estimate,
    )
    return {
        "total_users": total_users,
        "new_today": new_today,
        "new_week": new_week,
        "telegram_linked": telegram_linked,
        "active_pro_access": active_pro_access,
        "trial_users": trial_users,
        "paid_pro": paid_pro,
        "free_users": free_users,
        "failed_payments": failed_payments,
        "unmatched": unmatched,
        "emails_today": emails_today,
        "events_today": events_today,
        "visitors_24h": visitors_24h,
        "total_revenue": round(float(total_revenue), 2),
        "mrr_estimate": round(mrr_estimate, 2),
        "audit_count": audit_count,
    }


def latest_checkout_diagnostics():
    init_db()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM checkout_attempts WHERE status='success' ORDER BY created_at DESC LIMIT 1")
    success = cur.fetchone()
    cur.execute("SELECT * FROM checkout_attempts WHERE status='failed' ORDER BY created_at DESC LIMIT 1")
    failed = cur.fetchone()
    conn.close()
    return {
        "last_success": dict(success) if success else None,
        "last_error": dict(failed) if failed else None,
    }


def admin_page_html(title, body, admin=None):
    nav = (
        "<a href='/admin/dashboard'>Dashboard</a>"
        "<a href='/admin/users'>Users</a>"
        "<a href='/admin/admins'>Admins</a>"
        "<a href='/admin/employees'>Employees</a>"
        "<a href='/admin/departments'>Departments</a>"
        "<a href='/admin/transactions'>Transactions</a>"
        "<a href='/admin/emails'>Emails</a>"
        "<a href='/admin/emails/payment'>Payment Emails</a>"
        "<a href='/admin/telegram'>Telegram</a>"
        "<a href='/admin/ai-usage'>AI Usage</a>"
        "<a href='/admin/scam-shield'>Scam Shield</a>"
        "<a href='/admin/command-logs'>Command Logs</a>"
        "<a href='/admin/visitors'>Visitors</a>"
        "<a href='/admin/notifications'>Notifications</a>"
        "<a href='/admin/notification-delivery'>Delivery</a>"
        "<a href='/admin/watch-rules'>Watch Rules</a>"
        "<a href='/admin/education'>Education</a>"
        "<a href='/admin/predictions'>Predictions</a>"
        "<a href='/admin/seo'>SEO</a>"
        "<a href='/admin/private-chat-reports'>Chat Reports</a>"
        "<a href='/admin/support'>Support</a>"
        "<a href='/admin/data-recovery'>Data Recovery</a>"
        "<a href='/admin/unmatched-payments'>Unmatched</a>"
        "<a href='/admin/security'>Security</a>"
        "<a href='/admin/system'>System</a>"
        "<a href='/admin/audit-logs'>Audit</a>"
        "<a href='/admin/logout'>Logout</a>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="robots" content="noindex,nofollow" />
  <title>{clean_html(title)} | CoinPilotXAI Admin</title>
  <style>
    :root {{ color-scheme: dark; --bg:#050b14; --panel:#0d1627; --line:rgba(110,223,246,.22); --text:#f2fbff; --muted:#9fb5c0; --accent:#36e58f; --cyan:#6edff6; }}
    body {{ margin:0; font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif; background:radial-gradient(circle at top left,rgba(54,229,143,.12),transparent 34%),var(--bg); color:var(--text); }}
    header {{ position:sticky; top:0; z-index:2; backdrop-filter:blur(14px); background:rgba(5,11,20,.88); border-bottom:1px solid var(--line); }}
    .wrap {{ max-width:1180px; margin:auto; padding:22px; }}
    nav {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:12px; }}
    a {{ color:var(--cyan); text-decoration:none; }}
    nav a,.button {{ min-height:44px; display:inline-flex; align-items:center; padding:0 14px; border-radius:10px; border:1px solid var(--line); background:rgba(255,255,255,.04); }}
    h1 {{ margin:.2rem 0; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; }}
    .card {{ background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.025)); border:1px solid var(--line); border-radius:14px; padding:18px; box-shadow:0 24px 80px rgba(0,0,0,.25); overflow-wrap:break-word; }}
    .metric {{ font-size:2rem; font-weight:800; color:var(--accent); }}
    table {{ width:100%; border-collapse:collapse; overflow-wrap:break-word; }}
    th,td {{ text-align:left; padding:10px; border-bottom:1px solid rgba(255,255,255,.08); }}
    .muted {{ color:var(--muted); }}
    input,button,select {{ width:100%; min-height:44px; border-radius:10px; border:1px solid var(--line); background:#081323; color:var(--text); padding:10px; box-sizing:border-box; }}
    button {{ background:linear-gradient(135deg,#00e5ff,#36e58f); color:#031016; font-weight:800; cursor:pointer; }}
    @media(max-width:720px) {{ .wrap {{ padding:16px; }} table {{ display:block; overflow-x:auto; }} }}
  </style>
</head>
<body>
  <header><div class="wrap"><strong>CoinPilotXAI Inc. Admin</strong><div class="muted">{clean_html((admin or {}).get('email') or '')}</div><nav>{nav}</nav></div></header>
  <main class="wrap">{body}</main>
</body>
</html>"""


@webhook_app.route("/admin/login", methods=["GET", "POST"])
def admin_login_page():
    init_db()
    if admin_current_user():
        return redirect(url_for("admin_dashboard_page"))
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            message = "Security check failed. Please try again."
        else:
            email = normalize_email(clean_html(request.form.get("email", "")))
            password = request.form.get("password", "")
            admin = load_admin_by_email(email)
            fallback_ok = admin and admin.get("role") == "owner" and not admin.get("password_hash") and os.getenv("ADMIN_ANALYTICS_PASSWORD") and password == os.getenv("ADMIN_ANALYTICS_PASSWORD")
            locked_until = parse_iso_datetime((admin or {}).get("locked_until"))
            locked = bool(locked_until and locked_until > datetime.now(locked_until.tzinfo) if locked_until and locked_until.tzinfo else locked_until and locked_until > datetime.now())
            auth_ok = admin and admin.get("status") == "active" and not locked and ((admin.get("password_hash") and check_password_hash(admin["password_hash"], password)) or fallback_ok)
            if auth_ok:
                session["admin_user_id"] = admin["id"]
                conn = db()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE admin_users SET last_login_at=?, failed_login_count=0, locked_until=NULL, updated_at=? WHERE id=?",
                    (datetime.now().isoformat(), datetime.now().isoformat(), admin["id"])
                )
                conn.commit()
                conn.close()
                log_admin_audit(admin["id"], "admin_login_success", "admin_user", str(admin["id"]), {})
                if int(admin.get("must_change_password") or 0) == 1:
                    return redirect(url_for("admin_change_password_page"))
                return redirect(url_for("admin_dashboard_page"))
            update_admin_failed_login(admin)
            message = "Admin login failed."
    body = f"""
    <section class="card" style="max-width:520px;margin:7vh auto">
      <h1>Admin Login</h1>
      <p class="muted">Private owner and team access for CoinPilotXAI Inc.</p>
      {f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
      <form method="post">
        <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
        <p><input name="email" type="email" autocomplete="email" placeholder="Admin email" required /></p>
        <p><input name="password" type="password" autocomplete="current-password" placeholder="Password" required /></p>
        <button type="submit">Log In</button>
      </form>
    </section>
    """
    return admin_page_html("Admin Login", body)


@webhook_app.route("/admin/bootstrap-owner", methods=["GET"])
def admin_bootstrap_owner_page():
    token = request.args.get("token", "")
    expected = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "")
    if not expected or not token or not secrets.compare_digest(token, expected):
        return Response("Not found", status=404)
    init_db()
    result = {"created": False, "reset": False, "temp_password": ""}
    if os.getenv("ADMIN_RESET_OWNER_PASSWORD", "false").strip().lower() == "true":
        result = ensure_owner_admin(allow_reset=True)
    temp_password = result.get("temp_password") or ""
    reason = result.get("action") or ""
    if not temp_password and OWNER_BOOTSTRAP_TEMP.get("display_available"):
        temp_password = OWNER_BOOTSTRAP_TEMP.get("password") or ""
        reason = OWNER_BOOTSTRAP_TEMP.get("reason") or "owner_temp_password_generated"
    if temp_password:
        OWNER_BOOTSTRAP_TEMP["display_available"] = False
        body = f"""
        <section class="card" style="max-width:680px;margin:5vh auto">
          <h1>Owner Bootstrap Created</h1>
          <p class="muted">This temporary password is shown once. Store it securely, log in, then change it immediately.</p>
          <p><strong>Email:</strong> {clean_html(OWNER_ADMIN_EMAIL)}</p>
          <p><strong>Temporary password:</strong></p>
          <p style="font-size:1.2rem"><code>{clean_html(temp_password)}</code></p>
          <p><strong>Reason:</strong> {clean_html(reason)}</p>
          <p>The account must change this password before accessing the admin dashboard.</p>
          <p><a class="button" href="/admin/login">Go to Admin Login</a></p>
        </section>
        """
    else:
        body = f"""
        <section class="card" style="max-width:680px;margin:5vh auto">
          <h1>Owner Bootstrap Status</h1>
          <p>Owner admin exists for {clean_html(OWNER_ADMIN_EMAIL)}.</p>
          <p class="muted">No temporary password is available to display. Set <code>ADMIN_RESET_OWNER_PASSWORD=true</code> and reload this route with the bootstrap token if you need a one-time reset.</p>
          <p><a class="button" href="/admin/login">Go to Admin Login</a></p>
        </section>
        """
    return admin_page_html("Owner Bootstrap", body)


@webhook_app.route("/admin/change-password", methods=["GET", "POST"])
def admin_change_password_page():
    init_db()
    admin = admin_current_user()
    if not admin:
        return redirect(url_for("admin_login_page"))
    message = ""
    error = ""
    if request.method == "POST":
        if not verify_csrf():
            error = "Security check failed. Please try again."
        else:
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not admin.get("password_hash") or not check_password_hash(admin["password_hash"], current_password):
                error = "Current password is incorrect."
            elif new_password != confirm_password:
                error = "New passwords do not match."
            else:
                ok, password_error = admin_password_is_strong(new_password)
                if not ok:
                    error = password_error
                else:
                    now = datetime.now().isoformat()
                    conn = db()
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE admin_users
                        SET password_hash=?, must_change_password=0, password_changed_at=?,
                            failed_login_count=0, locked_until=NULL, updated_at=?
                        WHERE id=?
                        """,
                        (generate_password_hash(new_password), now, now, admin["id"]),
                    )
                    conn.commit()
                    conn.close()
                    log_admin_audit(admin["id"], "admin_password_changed", "admin_user", str(admin["id"]), {"forced_change_completed": True})
                    return redirect(url_for("admin_dashboard_page"))
    body = f"""
    <section class="card" style="max-width:620px;margin:5vh auto">
      <h1>Change Temporary Password</h1>
      <p class="muted">Before accessing the admin dashboard, create a permanent password for your CoinPilotXAI Inc. owner account.</p>
      {f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
      {f"<p style='color:#ff9aa8'>{clean_html(error)}</p>" if error else ""}
      <form method="post">
        <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
        <p><input name="current_password" type="password" autocomplete="current-password" placeholder="Current temporary password" required /></p>
        <p><input name="new_password" type="password" autocomplete="new-password" placeholder="New password, 12+ characters" required /></p>
        <p><input name="confirm_password" type="password" autocomplete="new-password" placeholder="Confirm new password" required /></p>
        <button type="submit">Save New Password</button>
      </form>
      <p class="muted">Use at least 12 characters with uppercase, lowercase, number, and symbol.</p>
    </section>
    """
    return admin_page_html("Change Password", body, admin)


@webhook_app.route("/admin/logout", methods=["GET"])
def admin_logout_page():
    admin = admin_current_user()
    if admin:
        log_admin_audit(admin["id"], "admin_logout", "admin_user", str(admin["id"]), {})
    session.pop("admin_user_id", None)
    return redirect(url_for("admin_login_page"))


@webhook_app.route("/admin/dashboard", methods=["GET"])
def admin_dashboard_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    stats = admin_saas_summary()
    cards = "".join(f"<div class='card'><div class='muted'>{label.replace('_',' ').title()}</div><div class='metric'>{value}</div></div>" for label, value in stats.items())
    missing = owner_profile_missing_fields(admin) if admin.get("role") == "owner" else []
    profile_prompt = ""
    if missing:
        profile_prompt = (
            "<div class='card' style='border-color:rgba(255,209,102,.45)'>"
            "<strong>Complete owner profile</strong>"
            f"<p class='muted'>Missing: {clean_html(', '.join(missing).replace('_', ' '))}</p>"
            "<p><a class='button' href='/admin/profile'>Complete Profile</a></p>"
            "</div>"
        )
    body = (
        "<h1>Owner Dashboard</h1><p class='muted'>Live SaaS visibility across accounts, billing, emails, Telegram, analytics, and support.</p>"
        f"{profile_prompt}<div class='grid'>{cards}</div>"
        f"<form method='post' action='/admin/billing/recalculate' class='card'><input type='hidden' name='csrf_token' value='{get_csrf_token()}' /><button type='submit'>Recalculate Billing Metrics</button><p class='muted'>Scans successful Stripe payment records and fixes any paid users still marked trialing.</p></form>"
    )
    return admin_page_html("Owner Dashboard", body, admin)


@webhook_app.route("/admin/system", methods=["GET"])
def admin_system_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    diag = latest_checkout_diagnostics()
    db_diag = db_service.health_check()
    last_success = diag.get("last_success") or {}
    last_error = diag.get("last_error") or {}
    checks = {
        "Database": bool(db_diag.get("connected")),
        "Stripe configured": bool(STRIPE_SECRET_KEY and STRIPE_PRICE_ID),
        "Stripe secret": bool(STRIPE_SECRET_KEY),
        "Stripe publishable key": bool(STRIPE_PUBLISHABLE_KEY),
        "Stripe webhook configured": bool(STRIPE_WEBHOOK_SECRET),
        "Stripe price id configured": bool(STRIPE_PRICE_ID),
        "Brevo": bool(os.getenv("BREVO_API_KEY")),
        "OpenAI": bool(os.getenv("OPENAI_API_KEY")),
        "Telegram bot token": bool(BOT_TOKEN),
        "CoinGecko key optional": bool(os.getenv("COINGECKO_API_KEY")),
    }
    body = "<h1>System Health</h1><div class='grid'>" + "".join(
        f"<div class='card'><strong>{clean_html(name)}</strong><p class='metric'>{'OK' if ok else 'Missing'}</p></div>" for name, ok in checks.items()
    ) + (
        "</div><h2>Database Diagnostics</h2><div class='grid'>"
        f"<div class='card'><strong>Active DB engine</strong><p class='metric'>{clean_html(db_diag.get('db_engine') or '')}</p></div>"
        f"<div class='card'><strong>DATABASE_URL loaded</strong><p class='metric'>{'Yes' if db_diag.get('database_url_loaded') else 'No'}</p></div>"
        f"<div class='card'><strong>Database name</strong><p>{clean_html(str(db_diag.get('database_name') or ''))}</p></div>"
        f"<div class='card'><strong>Latency</strong><p>{clean_html(str(db_diag.get('latency_ms') or ''))} ms</p></div>"
        "</div><h2>Stripe Checkout Diagnostics</h2><div class='grid'>"
        f"<div class='card'><strong>App base URL</strong><p>{clean_html(APP_BASE_URL)}</p></div>"
        f"<div class='card'><strong>Last successful checkout session</strong><p>{clean_html(last_success.get('stripe_session_id') or 'None recorded')}</p><p class='muted'>{clean_html(last_success.get('created_at') or '')}</p></div>"
        f"<div class='card'><strong>Last Stripe error</strong><p>{clean_html(last_error.get('error_message') or 'None recorded')}</p><p class='muted'>{clean_html(last_error.get('created_at') or '')}</p></div>"
        "</div><p class='muted'>Missing optional keys produce honest fallbacks instead of fake data. Missing Stripe secret or price ID blocks checkout creation.</p>"
    )
    return admin_page_html("System Health", body, admin)


@webhook_app.route("/admin/system/claims", methods=["GET"])
def admin_claims_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    claims = [
        ("AI Chat", "/chat", "/api/ai/chat", True),
        ("Command Center", "/command-center", "/api/command", True),
        ("Scam Shield", "/scam-shield", "/api/scam-shield/analyze", True),
        ("Wallet Intel", "/app", "/api/wallet-intel", True),
        ("Day Signal", "/app", "/api/day-signal", True),
        ("Notifications", "/notifications", "/api/notifications", True),
        ("Private Messages", "/messages", "/api/messages/conversations", True),
        ("Education Journey", "/education", "/api/education/progress", False),
        ("Live Market", "/live-market", "/api/live/market", False),
        ("Account Status", "/account", "/api/account/status", True),
    ]
    rows = []
    for name, route, api, pro_gated in claims:
        route_exists = any(str(rule.rule) == route for rule in webhook_app.url_map.iter_rules())
        api_exists = any(str(rule.rule) == api for rule in webhook_app.url_map.iter_rules())
        rows.append({
            "claim": name,
            "route": route,
            "api": api,
            "route_works": route_exists,
            "api_works": api_exists,
            "mobile_ready": True,
            "pro_gated": pro_gated,
            "fallback": "Honest fallback required when live/API source is unavailable.",
        })
    if request.headers.get("Accept", "").startswith("application/json") or request.args.get("format") == "json":
        return jsonify({"ok": True, "claims": rows})
    table = admin_rows_table(rows, [("claim", "Claim"), ("route", "Route"), ("api", "API"), ("route_works", "Route"), ("api_works", "API"), ("pro_gated", "Pro Gated"), ("fallback", "Fallback")])
    return admin_page_html("Claims Verification", f"<h1>Claims Verification</h1><div class='card'>{table}</div>", admin)


@webhook_app.route("/admin/test-smtp", methods=["GET"])
def admin_test_smtp_route():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    host = (os.getenv("SMTP_HOST") or "").strip()
    port_raw = (os.getenv("SMTP_PORT") or "587").strip()
    user = (os.getenv("SMTP_USER") or "").strip()
    password = os.getenv("SMTP_PASSWORD") or ""
    sender = (os.getenv("MAIL_FROM_ADDRESS") or user or "noreply@coinpilotx.app").strip()
    recipient = (os.getenv("SMTP_TEST_RECIPIENT") or os.getenv("ADMIN_TEST_EMAIL") or os.getenv("SUPPORT_EMAIL") or "support@coinpilotx.app").strip()
    logging.info("SMTP_TEST_ATTEMPT admin_id=%s host=%s port=%s user_present=%s recipient=%s", admin.get("id"), host, port_raw, bool(user), mask_email(recipient))
    if not host or not port_raw:
        logging.warning("SMTP_TEST_FAIL stage=connect missing_host_or_port")
        return jsonify({"ok": False, "error_stage": "connect", "error": "Could not connect to SMTP server: SMTP_HOST or SMTP_PORT is missing."}), 500
    try:
        port = int(port_raw)
    except ValueError:
        logging.warning("SMTP_TEST_FAIL stage=connect invalid_port=%s", port_raw)
        return jsonify({"ok": False, "error_stage": "connect", "error": f"Could not connect to SMTP server: invalid SMTP_PORT {port_raw}"}), 500
    smtp = None
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
            smtp.ehlo()
            if port == 587:
                smtp.starttls()
                smtp.ehlo()
    except Exception as exc:
        logging.warning("SMTP_TEST_FAIL stage=connect host=%s port=%s error=%s", host, port, exc)
        try:
            if smtp:
                smtp.quit()
        except Exception:
            pass
        return jsonify({"ok": False, "error_stage": "connect", "error": f"Could not connect to SMTP server: {exc}"}), 500
    try:
        if user or password:
            smtp.login(user, password)
    except Exception as exc:
        logging.warning("SMTP_TEST_FAIL stage=login host=%s port=%s user_present=%s error=%s", host, port, bool(user), exc)
        try:
            smtp.quit()
        except Exception:
            pass
        return jsonify({"ok": False, "error_stage": "login", "error": f"SMTP login failed: {exc}"}), 500
    try:
        msg = EmailMessage()
        msg["Subject"] = "CoinPilotXAI SMTP test"
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content("CoinPilotXAI SMTP diagnostics succeeded. This confirms SMTP connection, login, and delivery path.")
        smtp.send_message(msg)
        smtp.quit()
        logging.info("SMTP_TEST_SUCCESS admin_id=%s host=%s port=%s recipient=%s", admin.get("id"), host, port, mask_email(recipient))
        log_admin_audit(admin.get("id"), "smtp_test_success", "system", "smtp", {"host": host, "port": port, "recipient": mask_email(recipient)})
        return jsonify({"ok": True, "smtp_host": host, "smtp_port": port, "login_ok": True, "test_email_sent": True, "message": "SMTP is working"})
    except Exception as exc:
        logging.warning("SMTP_TEST_FAIL stage=send host=%s port=%s recipient=%s error=%s", host, port, mask_email(recipient), exc)
        try:
            smtp.quit()
        except Exception:
            pass
        return jsonify({"ok": False, "error_stage": "send", "error": f"Test email failed: {exc}"}), 500


@webhook_app.route("/admin/manual-upgrade", methods=["POST"])
def admin_manual_upgrade_route():
    admin, denied = require_admin_page("billing.repair")
    if denied:
        return jsonify({"ok": False, "error": "Insufficient permissions or invalid user."}), 403
    payload = request.get_json(silent=True) or {}
    target_user_id = str(payload.get("user_id") or "").strip()
    if not target_user_id.isdigit():
        return jsonify({"ok": False, "error": "Invalid user."}), 400
    user = load_account_by_id(int(target_user_id))
    if not user:
        return jsonify({"ok": False, "error": "Insufficient permissions or invalid user."}), 404
    if has_pro_access(user):
        return jsonify({
            "ok": True,
            "message": "User already has Pro.",
            "user": {
                "user_id": user.get("user_id"),
                "email": mask_email(user.get("email")),
                "plan": user.get("plan") or user.get("subscription_plan"),
                "subscription_status": user.get("subscription_status"),
                "has_pro_access": True,
            },
        })
    try:
        days = int(payload.get("days") or os.getenv("MANUAL_PRO_DAYS") or 365)
    except Exception:
        days = 365
    days = max(1, min(days, 3650))
    pro_expires_at = (datetime.now() + timedelta(days=days)).isoformat()
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET plan='pro',
            subscription_plan='pro',
            subscription_status='active',
            is_pro=1,
            trial_status=CASE WHEN lower(COALESCE(trial_status,''))='trialing' THEN 'converted' ELSE COALESCE(trial_status, '') END,
            pro_expires_at=?,
            subscription_expires_at=?,
            updated_at=?
        WHERE user_id=?
        """,
        (pro_expires_at, pro_expires_at, now, int(target_user_id)),
    )
    conn.commit()
    conn.close()
    payment_id = record_payment_record(
        int(target_user_id),
        stripe_event_id=f"manual_upgrade_{int(time.time())}_{target_user_id}",
        amount=0,
        currency="usd",
        status="succeeded",
        payment_type="manual_admin_upgrade",
        manual=True,
        metadata={"admin_id": admin.get("id"), "admin_email": admin.get("email"), "days": days},
    )
    log_admin_audit(admin.get("id"), "manual_pro_upgrade", "user", target_user_id, {"plan": "pro", "subscription_status": "active", "pro_expires_at": pro_expires_at, "payment_record_id": payment_id})
    fresh_user = load_account_by_id(int(target_user_id)) or {}
    subject = "CoinPilotXAI Pro Activated"
    text = "Your CoinPilotXAI Pro access has been activated by the system administrator.\n\nOpen your dashboard: https://coinpilotx.app/dashboard\n\nCoinPilotXAI Inc. provides educational AI intelligence only. Not financial, betting, investment, or legal advice."
    html = "<p>Your CoinPilotXAI Pro access has been activated by the system administrator.</p><p><a href='https://coinpilotx.app/dashboard'>Open your dashboard</a></p><p>CoinPilotXAI Inc. provides educational AI intelligence only. Not financial, betting, investment, or legal advice.</p>"
    if fresh_user.get("email"):
        send_platform_email(fresh_user.get("email"), subject, text, html, fresh_user.get("user_id"))
    log_product_event(fresh_user.get("user_id") or int(target_user_id), "manual_pro_upgrade", {"admin_id": admin.get("id"), "payment_record_id": payment_id})
    return jsonify({
        "ok": True,
        "message": "User upgraded to Pro successfully.",
        "user": {
            "user_id": fresh_user.get("user_id"),
            "email": mask_email(fresh_user.get("email")),
            "plan": fresh_user.get("plan") or fresh_user.get("subscription_plan"),
            "subscription_status": fresh_user.get("subscription_status"),
            "pro_expires_at": fresh_user.get("pro_expires_at") or "",
            "has_pro_access": has_pro_access(fresh_user),
        },
    })


@webhook_app.route("/api/admin/visitors", methods=["GET"])
def admin_visitors_route():
    admin, denied = require_admin_page("analytics.view")
    if denied:
        return denied
    range_key = clean_html(request.args.get("range", "24h")).lower()
    hours = {"1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(range_key, 24)
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    filter_key = clean_html(request.args.get("filter", "all")).lower()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT session_id) FROM visitor_logs WHERE timestamp>=?", (since,))
    unique_total = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM visitor_logs WHERE timestamp>=?", (since,))
    total_visits = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(DISTINCT session_id) FROM visitor_logs WHERE timestamp>=? AND COALESCE(user_id,0)>0", (since,))
    logged_in = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(DISTINCT session_id) FROM visitor_logs WHERE timestamp>=? AND COALESCE(user_id,0)=0", (since,))
    anonymous = cur.fetchone()[0] or 0
    active_since = (datetime.now() - timedelta(minutes=10)).isoformat()
    cur.execute("SELECT COUNT(DISTINCT session_id) FROM visitor_logs WHERE timestamp>=?", (active_since,))
    active_now = cur.fetchone()[0] or 0
    def top(field, limit=12):
        field_name = _migration_identifier(field)
        cur.execute(f"SELECT COALESCE({field_name}, '') AS value, COUNT(*) AS count FROM visitor_logs WHERE timestamp>=? GROUP BY COALESCE({field_name}, '') ORDER BY count DESC LIMIT {int(limit)}", (since,))
        return [dict(row) for row in cur.fetchall()]
    conversions = {}
    for name, pattern in {
        "signup_clicks": "%signup%",
        "upgrade_clicks": "%upgrade%",
        "checkout_starts": "%checkout_started%",
        "successful_checkout_returns": "%payment_success%",
        "failed_checkout_returns": "%payment_failed%",
        "command_center_usage": "%command%",
        "chat_usage": "%chat%",
        "scam_scans": "%scam_shield%",
        "notification_opt_ins": "%push_subscription%",
    }.items():
        cur.execute("SELECT COUNT(*) FROM analytics_events WHERE created_at>=? AND lower(event_name) LIKE lower(?)", (since, pattern))
        conversions[name] = cur.fetchone()[0] or 0
    visitors_where = "WHERE timestamp>=?"
    params = [since]
    if filter_key == "logged_in":
        visitors_where += " AND COALESCE(user_id,0)>0"
    elif filter_key == "anonymous":
        visitors_where += " AND COALESCE(user_id,0)=0"
    elif filter_key in {"pro", "free"}:
        visitors_where += " AND COALESCE(user_id,0)>0"
    cur.execute(
        f"""
        SELECT user_id, session_id, ip_address, user_agent, path, referrer, device_type, browser, os, country, city, timestamp
        FROM visitor_logs
        {visitors_where}
        ORDER BY timestamp DESC
        LIMIT 200
        """,
        params,
    )
    visitors = []
    for row in [dict(row) for row in cur.fetchall()]:
        include = True
        if filter_key in {"pro", "free"}:
            u = load_account_by_id(row.get("user_id"))
            include = bool(u and has_pro_access(u)) if filter_key == "pro" else bool(u and not has_pro_access(u))
        if include:
            visitors.append(row)
    top_pages = top("path")
    referrers = top("referrer")
    devices = top("device_type")
    browsers = top("browser")
    os_breakdown = top("os")
    countries = top("country")
    cities = top("city")
    conn.close()
    payload = {
        "ok": True,
        "range": range_key,
        "total_visits": total_visits,
        "total_last_24h": unique_total if range_key == "24h" else None,
        "unique_visitors": unique_total,
        "logged_in_visitors": logged_in,
        "anonymous_visitors": anonymous,
        "active_sessions_now": active_now,
        "top_pages": top_pages,
        "referrers": referrers,
        "device_type": devices,
        "browser": browsers,
        "os": os_breakdown,
        "country": countries,
        "city": cities,
        "conversion_events": conversions,
        "recent_visit_stream": visitors,
        "visitors": visitors,
    }
    log_admin_audit(admin.get("id"), "admin_viewed_live_visitors", "analytics", "visitor_logs", {"unique": unique_total, "range": range_key, "filter": filter_key})
    return jsonify(payload)


@webhook_app.route("/admin/visitors", methods=["GET"])
def admin_visitors_dashboard_page():
    admin, denied = require_admin_page("analytics.view")
    if denied:
        return denied
    body = """
    <style>
      .analytics-hero{display:grid;grid-template-columns:1.1fr .9fr;gap:16px;align-items:stretch}
      .live-map{min-height:320px;border:1px solid var(--line);border-radius:18px;background:radial-gradient(circle at 28% 42%,rgba(54,229,143,.26),transparent 7px),radial-gradient(circle at 55% 36%,rgba(110,223,246,.24),transparent 9px),radial-gradient(circle at 70% 58%,rgba(255,209,102,.22),transparent 8px),linear-gradient(135deg,rgba(110,223,246,.08),rgba(54,229,143,.04));position:relative;overflow:hidden}
      .live-map:before{content:"";position:absolute;inset:12%;border:1px solid rgba(255,255,255,.08);border-radius:45% 55% 48% 52%;box-shadow:0 0 70px rgba(110,223,246,.16) inset;animation:mapPulse 5s ease-in-out infinite}
      .analytics-card{position:relative;overflow:hidden}.analytics-card:after{content:"";position:absolute;inset:auto -20% -60% -20%;height:80%;background:radial-gradient(circle,rgba(110,223,246,.18),transparent 65%);pointer-events:none}
      .metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
      .metric-card{border:1px solid var(--line);border-radius:14px;padding:16px;background:rgba(255,255,255,.045);box-shadow:0 18px 60px rgba(0,0,0,.22)}
      .metric-card strong{display:block;font-size:28px;color:var(--accent)}
      .bars{display:grid;gap:8px}.bar-row{display:grid;grid-template-columns:1fr 4fr auto;gap:8px;align-items:center}.bar-fill{height:10px;border-radius:99px;background:linear-gradient(90deg,var(--cyan),var(--accent));min-width:4px}
      .stream{max-height:420px;overflow:auto}.stream-row{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;padding:10px;border-bottom:1px solid rgba(255,255,255,.07)}
      .tag{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:3px 8px;color:var(--cyan)}
      @keyframes mapPulse{50%{transform:scale(1.03);filter:brightness(1.25)}} @media(max-width:820px){.analytics-hero{grid-template-columns:1fr}.bar-row{grid-template-columns:1fr}.live-map{min-height:240px}}
    </style>
    <h1>Visitor Intelligence Center</h1>
    <p class="muted">Executive analytics for visits, devices, conversion intent, command usage, and live behavior. IP/session data is masked and admin-only.</p>
    <div class="analytics-hero">
      <section class="card analytics-card"><h2>Live World Signal</h2><div class="live-map" aria-label="Approximate visitor geo map"></div><div id="geoTop" class="bars"></div></section>
      <section class="card"><h2>Conversion Intelligence</h2><div id="funnel" class="bars"></div></section>
    </div>
    <section id="summaryCards" class="metric-grid"></section>
    <section class="grid">
      <div class="card"><h2>Top Pages</h2><div id="topPages" class="bars"></div></div>
      <div class="card"><h2>Device Mix</h2><div id="devices" class="bars"></div></div>
      <div class="card"><h2>Live Activity Stream</h2><div id="activityStream" class="stream"></div></div>
    </section>
    <script>
      const api = "/api/admin/visitors";
      const fmt = n => Number(n || 0).toLocaleString();
      const rel = value => { const d = new Date(value || Date.now()); const s = Math.max(1, Math.floor((Date.now()-d.getTime())/1000)); if(s<60)return s+"s ago"; const m=Math.floor(s/60); if(m<60)return m+"m ago"; const h=Math.floor(m/60); if(h<24)return h+"h ago"; return Math.floor(h/24)+"d ago"; };
      const mask = value => String(value || "").replace(/(\\d+\\.\\d+)\\.\\d+\\.\\d+/, "$1.x.x").slice(0, 64);
      function bars(node, rows){ const max=Math.max(...rows.map(r=>r.count||0),1); node.innerHTML=rows.map(r=>`<div class="bar-row"><span>${r.value||"Direct/Unknown"}</span><span class="bar-fill" style="width:${Math.max(4,(r.count||0)/max*100)}%"></span><strong>${fmt(r.count)}</strong></div>`).join("") || "<p class='muted'>No data yet.</p>"; }
      function stream(node, rows){ node.innerHTML=(rows||[]).slice(0,60).map(v=>`<div class="stream-row"><span class="tag">${v.user_id ? "User" : "Visitor"}</span><span>${mask(v.city||v.country||"Unknown")} opened <strong>${v.path||"/"}</strong><br><small class="muted">${mask(v.browser||v.device_type||"browser")} · ${mask(v.referrer||"direct")}</small></span><time>${rel(v.timestamp)}</time></div>`).join("") || "<p class='muted'>No visits yet.</p>"; }
      async function loadVisitors(){ const data = await fetch(api,{cache:"no-store",credentials:"same-origin"}).then(r=>r.json()); const c=data.conversion_events||{}; const cards=[
        ["Active Visitors Now",data.active_sessions_now],["Unique Visitors 24h",data.unique_visitors],["Logged-In Users",data.logged_in_visitors],["Anonymous Visitors",data.anonymous_visitors],["Mobile Users",(data.device_type||[]).find(x=>(x.value||"").toLowerCase()==="mobile")?.count||0],["Desktop Users",(data.device_type||[]).find(x=>(x.value||"").toLowerCase()==="desktop")?.count||0],["Upgrade Clicks",c.upgrade_clicks],["Pro Conversions",c.successful_checkout_returns],["AI Chat Sessions",c.chat_usage],["Command Center Sessions",c.command_center_usage],["Scam Shield Usage",c.scam_scans],["Notification Opt-ins",c.notification_opt_ins]
      ]; document.getElementById("summaryCards").innerHTML=cards.map(([label,value])=>`<div class="metric-card"><span class="muted">${label}</span><strong>${fmt(value)}</strong></div>`).join(""); bars(document.getElementById("topPages"),data.top_pages||[]); bars(document.getElementById("devices"),data.device_type||[]); bars(document.getElementById("geoTop"),data.country||[]); bars(document.getElementById("funnel"),Object.entries(c).map(([value,count])=>({value:value.replaceAll("_"," "),count}))); stream(document.getElementById("activityStream"),data.recent_visit_stream||[]); }
      loadVisitors().catch(()=>{}); setInterval(()=>loadVisitors().catch(()=>{}),15000);
    </script>
    """
    log_admin_audit(admin.get("id"), "admin_viewed_visitor_intelligence_center", "analytics", "visitor_logs", {})
    return admin_page_html("Visitor Intelligence Center", body, admin)


@webhook_app.route("/admin/transactions", methods=["GET"])
@webhook_app.route("/admin/subscriptions", methods=["GET"])
def admin_transactions_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM payment_records ORDER BY created_at DESC LIMIT 100")
    records = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM subscriptions ORDER BY created_at DESC LIMIT 100")
    subs = [dict(row) for row in cur.fetchall()]
    conn.close()
    rows = "".join(f"<tr><td>{r.get('user_id')}</td><td>{clean_html(str(r.get('amount') or ''))}</td><td>{clean_html(str(r.get('currency') or ''))}</td><td>{clean_html(str(r.get('status') or ''))}</td><td>{clean_html(str(r.get('created_at') or ''))}</td></tr>" for r in records)
    sub_rows = "".join(f"<tr><td>{s.get('user_id')}</td><td>{clean_html(s.get('plan') or '')}</td><td>{clean_html(s.get('status') or '')}</td><td>{clean_html(s.get('stripe_subscription_id') or '')}</td><td>{clean_html(s.get('created_at') or '')}</td></tr>" for s in subs)
    body = f"<h1>Transactions</h1><div class='card'><table><tr><th>User</th><th>Amount</th><th>Currency</th><th>Status</th><th>Date</th></tr>{rows}</table></div><h2>Subscriptions</h2><div class='card'><table><tr><th>User</th><th>Plan</th><th>Status</th><th>Stripe Subscription</th><th>Date</th></tr>{sub_rows}</table></div>"
    return admin_page_html("Transactions", body, admin)


@webhook_app.route("/admin/emails", methods=["GET"])
def admin_emails_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id, recipient_email, email, email_type, subject, status, created_at FROM email_logs ORDER BY created_at DESC LIMIT 150")
    logs = [dict(row) for row in cur.fetchall()]
    conn.close()
    rows = "".join(f"<tr><td>{r.get('user_id')}</td><td>{clean_html(mask_email(r.get('recipient_email') or r.get('email') or ''))}</td><td>{clean_html(r.get('email_type') or '')}</td><td>{clean_html(r.get('subject') or '')}</td><td>{clean_html(r.get('status') or '')}</td><td>{clean_html(r.get('created_at') or '')}</td></tr>" for r in logs)
    body = f"<h1>Email Logs</h1><div class='card'><table><tr><th>User</th><th>Email</th><th>Type</th><th>Subject</th><th>Status</th><th>Date</th></tr>{rows}</table></div>"
    return admin_page_html("Emails", body, admin)


def _count_table_safe(cur, table):
    table_name = _migration_identifier(table)
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        return cur.fetchone()[0]
    except Exception:
        return None


def sqlite_recovery_snapshot():
    path = os.path.join(os.getcwd(), DB_FILE)
    if not os.path.exists(path):
        return {"available": False, "path": path, "counts": {}, "error": "Local SQLite file not found."}
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        counts = {}
        for table in ["users", "admin_users", "payment_records", "stripe_events", "unmatched_payments"]:
            counts[table] = _count_table_safe(cur, table)
        conn.close()
        return {"available": True, "path": path, "counts": counts, "error": ""}
    except Exception as exc:
        return {"available": False, "path": path, "counts": {}, "error": str(exc)[:300]}


@webhook_app.route("/admin/data-recovery", methods=["GET", "POST"])
def admin_data_recovery_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            message = "Security check failed."
        else:
            action = request.form.get("action", "")
            if action == "ensure_owner":
                ensure_owner_admin(allow_reset=False)
                log_admin_audit(admin["id"], "data_recovery_ensure_owner", "admin_user", "owner", {})
                message = "Owner admin check completed. Existing owner password was not overwritten."
            elif action == "repair_paid":
                repaired = repair_paid_users_from_payment_records()
                converted = repair_trialing_users_with_successful_payments()
                log_admin_audit(admin["id"], "data_recovery_repair_paid_users", "billing", "", {"repaired": repaired, "converted": converted})
                message = f"Paid user repair completed. Repaired {repaired}; converted trialing users {converted}."
    diagnostics = db_service.health_check()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    current_counts = {
        table: _count_table_safe(cur, table)
        for table in ["users", "admin_users", "payment_records", "stripe_events", "unmatched_payments", "subscriptions"]
    }
    owner = load_admin_by_email("cherieroody@gmail.com")
    cur.execute(
        "SELECT user_id, email, plan, subscription_status, stripe_customer_id, stripe_subscription_id, updated_at FROM users ORDER BY user_id DESC LIMIT 12"
    )
    latest_users = [dict(row) for row in cur.fetchall()]
    cur.execute(
        "SELECT id, user_id, amount, currency, status, stripe_event_id, stripe_customer_id, stripe_subscription_id, created_at FROM payment_records ORDER BY id DESC LIMIT 12"
    )
    latest_payments = [dict(row) for row in cur.fetchall()]
    cur.execute(
        "SELECT id, stripe_event_id, event_type, status, user_id, created_at, processed_at FROM stripe_events ORDER BY id DESC LIMIT 12"
    )
    latest_events = [dict(row) for row in cur.fetchall()]
    cur.execute(
        "SELECT id, event_type, customer_email, customer_id, amount, currency, reason, created_at FROM unmatched_payments ORDER BY id DESC LIMIT 12"
    )
    unmatched = [dict(row) for row in cur.fetchall()]
    conn.close()
    sqlite_snapshot = sqlite_recovery_snapshot()
    count_cards = "".join(
        f"<div class='card'><strong>{clean_html(table)}</strong><p class='metric'>{value if value is not None else 'missing'}</p></div>"
        for table, value in current_counts.items()
    )
    sqlite_cards = "".join(
        f"<div class='card'><strong>{clean_html(table)}</strong><p class='metric'>{value if value is not None else 'missing'}</p></div>"
        for table, value in (sqlite_snapshot.get("counts") or {}).items()
    ) or "<p class='muted'>No local SQLite comparison counts available.</p>"
    body = f"""
    <h1>Data Recovery</h1>
    <p class="muted">Non-destructive recovery tools for PostgreSQL migration checks, owner restoration, payment repair, and old SQLite comparison. This page never wipes production data.</p>
    {f"<p class='card'>{clean_html(message)}</p>" if message else ""}
    <div class="grid">
      <div class="card"><strong>Active DB Engine</strong><p class="metric">{clean_html(db_service.ENGINE_NAME)}</p></div>
      <div class="card"><strong>DATABASE_URL Loaded</strong><p class="metric">{'yes' if db_service.DATABASE_URL_LOADED else 'no'}</p></div>
      <div class="card"><strong>DB Connected</strong><p class="metric">{'yes' if diagnostics.get('connected') else 'no'}</p></div>
      <div class="card"><strong>Database</strong><p>{clean_html(diagnostics.get('database_name') or '')}</p></div>
      <div class="card"><strong>Owner Admin</strong><p>{'present' if owner else 'missing'} · role {clean_html((owner or {}).get('role') or '')} · status {clean_html((owner or {}).get('status') or '')}</p></div>
    </div>
    <h2>Current Database Counts</h2>
    <div class="grid">{count_cards}</div>
    <h2>Old Local SQLite Snapshot</h2>
    <p class="muted">Path checked: {clean_html(sqlite_snapshot.get('path') or '')}. {clean_html(sqlite_snapshot.get('error') or '')}</p>
    <div class="grid">{sqlite_cards}</div>
    <div class="grid">
      <form method="post" class="card">
        <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
        <input type="hidden" name="action" value="ensure_owner" />
        <h3>Restore Owner Admin</h3>
        <p>Ensures Roody Cherie / cherieroody@gmail.com exists as active owner. Existing passwords are not overwritten.</p>
        <button type="submit">Ensure Owner Admin</button>
      </form>
      <form method="post" class="card">
        <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
        <input type="hidden" name="action" value="repair_paid" />
        <h3>Restore Paid Pro From Payments</h3>
        <p>Scans successful payment records and converts matched users to paid Pro active. Unmatched payments remain visible for manual repair.</p>
        <button type="submit">Repair Paid Users</button>
      </form>
    </div>
    <h2>Latest Users</h2><div class="card">{admin_rows_table([{**row, 'email': mask_email(row.get('email'))} for row in latest_users], [('user_id','ID'),('email','Email'),('plan','Plan'),('subscription_status','Status'),('stripe_customer_id','Stripe Customer'),('stripe_subscription_id','Stripe Sub'),('updated_at','Updated')])}</div>
    <h2>Latest Payments</h2><div class="card">{admin_rows_table(latest_payments, [('user_id','User'),('amount','Amount'),('currency','Currency'),('status','Status'),('stripe_event_id','Event'),('stripe_customer_id','Customer'),('stripe_subscription_id','Subscription'),('created_at','Created')])}</div>
    <h2>Latest Stripe Events</h2><div class="card">{admin_rows_table(latest_events, [('stripe_event_id','Event'),('event_type','Type'),('status','Status'),('user_id','User'),('created_at','Created'),('processed_at','Processed')])}</div>
    <h2>Unmatched Payments</h2><div class="card">{admin_rows_table(unmatched, [('event_type','Type'),('customer_email','Email'),('customer_id','Customer'),('amount','Amount'),('currency','Currency'),('reason','Reason'),('created_at','Created')])}</div>
    """
    return admin_page_html("Data Recovery", body, admin)


@webhook_app.route("/admin/emails/payment", methods=["GET", "POST"])
def admin_payment_emails_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            message = "Security check failed."
        else:
            log_id = int(request.form.get("log_id") or 0)
            conn = db()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM payment_email_logs WHERE id=? LIMIT 1", (log_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                log_row = dict(row)
                user = load_account_by_id(log_row.get("user_id"))
                if user:
                    sent = send_payment_email_with_retry(
                        user,
                        {
                            "stripe_event_id": log_row.get("stripe_event_id") or "",
                            "payment_id": log_row.get("payment_id") or "",
                        },
                        email_type=log_row.get("email_type") or "payment_successful",
                        force=True,
                    )
                    log_admin_audit(admin["id"], "payment_email_resend", "payment_email_log", str(log_id), {"sent": bool(sent)})
                    message = "Payment email resent." if sent else "Payment email resend failed. Check the provider response."
                else:
                    message = "No matching user found for that payment email log."
            else:
                message = "Payment email log not found."
    status = clean_html(request.args.get("status", ""))[:40]
    search = normalize_email(clean_html(request.args.get("email", "")))
    payment_id = clean_html(request.args.get("payment_id", ""))[:180]
    clauses = []
    params = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if search:
        clauses.append("lower(email)=lower(?)")
        params.append(search)
    if payment_id:
        clauses.append("(payment_id=? OR stripe_event_id=?)")
        params.extend([payment_id, payment_id])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM payment_email_logs{where} ORDER BY created_at DESC LIMIT 300",
        tuple(params),
    )
    logs = [dict(row) for row in cur.fetchall()]
    conn.close()
    if request.args.get("export") == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["id", "user_id", "email", "stripe_event_id", "payment_id", "email_type", "status", "retry_count", "provider_response", "error_message", "created_at", "sent_at"])
        writer.writeheader()
        for row in logs:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})
        return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=payment-email-logs.csv"})
    rows = ""
    for row in logs:
        rows += (
            "<tr>"
            f"<td>{row.get('id')}</td>"
            f"<td>{row.get('user_id')}</td>"
            f"<td>{clean_html(mask_email(row.get('email') or ''))}</td>"
            f"<td>{clean_html(row.get('email_type') or '')}</td>"
            f"<td>{clean_html(row.get('status') or '')}</td>"
            f"<td>{clean_html(row.get('stripe_event_id') or '')}</td>"
            f"<td>{clean_html(row.get('payment_id') or '')}</td>"
            f"<td>{clean_html(str(row.get('retry_count') or 0))}</td>"
            f"<td>{clean_html((row.get('provider_response') or row.get('error_message') or '')[:280])}</td>"
            f"<td>{clean_html(row.get('created_at') or '')}</td>"
            "<td>"
            f"<form method='post'><input type='hidden' name='csrf_token' value='{get_csrf_token()}' /><input type='hidden' name='log_id' value='{row.get('id')}' /><button type='submit'>Resend</button></form>"
            "</td>"
            "</tr>"
        )
    body = f"""
    <h1>Payment Emails</h1>
    <p class="muted">Billing transactional email delivery for Pro activation, payment success, and receipts.</p>
    {f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
    <form method="get" class="card">
      <div class="grid">
        <label>Status<input name="status" value="{clean_html(status)}" placeholder="sent, failed, pending, retried" /></label>
        <label>Email<input name="email" value="{clean_html(search)}" placeholder="customer@email.com" /></label>
        <label>Stripe payment/event<input name="payment_id" value="{clean_html(payment_id)}" placeholder="event, invoice, session" /></label>
      </div>
      <button type="submit">Filter</button>
      <p><a class="button" href="/admin/emails/payment?export=csv">Export CSV</a></p>
    </form>
    <div class="card"><table><tr><th>ID</th><th>User</th><th>Email</th><th>Type</th><th>Status</th><th>Stripe Event</th><th>Payment</th><th>Retries</th><th>Provider/Error</th><th>Date</th><th>Action</th></tr>{rows}</table></div>
    """
    return admin_page_html("Payment Emails", body, admin)


@webhook_app.route("/admin/emails/payment/resend/<payment_id>", methods=["POST"])
def admin_payment_email_resend_by_payment_id(payment_id):
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    if not verify_csrf():
        return redirect(url_for("admin_payment_emails_page", status="failed"))
    token = clean_html(payment_id)[:180]
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if token.isdigit():
        cur.execute("SELECT * FROM payment_records WHERE id=? LIMIT 1", (int(token),))
    else:
        cur.execute(
            """
            SELECT * FROM payment_records
            WHERE stripe_session_id=? OR invoice_id=? OR payment_intent_id=? OR stripe_event_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (token, token, token, token),
        )
    payment = cur.fetchone()
    conn.close()
    if payment:
        payment_row = dict(payment)
        user = load_account_by_id(payment_row.get("user_id"))
        if user:
            send_successful_payment_email_bundle(user, {
                "stripe_event_id": payment_row.get("stripe_event_id") or "",
                "payment_id": payment_row.get("stripe_session_id") or payment_row.get("invoice_id") or payment_row.get("payment_intent_id") or str(payment_row.get("id")),
                "stripe_session_id": payment_row.get("stripe_session_id") or "",
                "invoice_id": payment_row.get("invoice_id") or "",
                "amount": payment_row.get("amount"),
                "currency": payment_row.get("currency") or "USD",
            }, force=True)
            log_admin_audit(admin["id"], "payment_email_resend", "payment_record", token, {"user_id": user.get("user_id")})
    return redirect(url_for("admin_payment_emails_page", payment_id=token))


@webhook_app.route("/admin/unmatched-payments", methods=["GET"])
def admin_unmatched_payments_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM unmatched_payments ORDER BY created_at DESC LIMIT 100")
    rows_data = [dict(row) for row in cur.fetchall()]
    conn.close()
    rows = "".join(f"<tr><td>{clean_html(str(r.get('stripe_event_id') or ''))}</td><td>{clean_html(str(r.get('customer_email') or ''))}</td><td>{clean_html(str(r.get('amount') or ''))}</td><td>{clean_html(str(r.get('reason') or ''))}</td><td>{clean_html(str(r.get('created_at') or ''))}</td></tr>" for r in rows_data)
    body = f"<h1>Unmatched Payments</h1><p class='muted'>Payments Stripe confirmed but CoinPilotXAI could not safely match to a website account.</p><div class='card'><table><tr><th>Event</th><th>Email</th><th>Amount</th><th>Reason</th><th>Date</th></tr>{rows}</table></div>"
    return admin_page_html("Unmatched Payments", body, admin)


@webhook_app.route("/admin/audit-logs", methods=["GET"])
def admin_audit_logs_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_audit_logs ORDER BY created_at DESC LIMIT 150")
    logs = [dict(row) for row in cur.fetchall()]
    conn.close()
    rows = "".join(f"<tr><td>{clean_html(r.get('admin_email') or '')}</td><td>{clean_html(r.get('action') or '')}</td><td>{clean_html(r.get('target_type') or '')}</td><td>{clean_html(r.get('target_id') or '')}</td><td>{clean_html(r.get('created_at') or '')}</td></tr>" for r in logs)
    body = f"<h1>Audit Logs</h1><div class='card'><table><tr><th>Admin</th><th>Action</th><th>Target</th><th>ID</th><th>Date</th></tr>{rows}</table></div>"
    return admin_page_html("Audit Logs", body, admin)


@webhook_app.route("/admin/profile", methods=["GET", "POST"])
def admin_profile_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    message = ""
    error = ""
    if request.method == "POST":
        if not verify_csrf():
            error = "Security check failed. Please try again."
        else:
            fields = {
                "date_of_birth": clean_html(request.form.get("date_of_birth", ""))[:40],
                "address_line1": clean_html(request.form.get("address_line1", ""))[:180],
                "address_line2": clean_html(request.form.get("address_line2", ""))[:180],
                "city": clean_html(request.form.get("city", ""))[:100],
                "state": clean_html(request.form.get("state", ""))[:80],
                "zip_code": clean_html(request.form.get("zip_code", ""))[:30],
                "country": clean_html(request.form.get("country", ""))[:80],
                "job_title": clean_html(request.form.get("job_title", ""))[:120],
                "emergency_contact_name": clean_html(request.form.get("emergency_contact_name", ""))[:160],
                "emergency_contact_phone": clean_html(request.form.get("emergency_contact_phone", ""))[:40],
                "notes": clean_html(request.form.get("notes", ""))[:1000],
            }
            conn = db()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE admin_users
                SET date_of_birth=?, address_line1=?, address_line2=?, city=?, state=?, zip_code=?,
                    country=?, job_title=?, emergency_contact_name=?, emergency_contact_phone=?, notes=?, updated_at=?
                WHERE id=?
                """,
                (
                    fields["date_of_birth"], fields["address_line1"], fields["address_line2"],
                    fields["city"], fields["state"], fields["zip_code"], fields["country"],
                    fields["job_title"], fields["emergency_contact_name"], fields["emergency_contact_phone"],
                    fields["notes"], datetime.now().isoformat(), admin["id"],
                ),
            )
            conn.commit()
            conn.close()
            log_admin_audit(admin["id"], "admin_profile_updated", "admin_user", str(admin["id"]), {"fields": list(fields.keys())})
            admin = admin_current_user()
            message = "Profile saved."
    def input_field(name, label, input_type="text"):
        value = clean_html((admin or {}).get(name) or "")
        return f"<label>{clean_html(label)}<input name='{name}' type='{input_type}' value='{value}' /></label>"
    body = f"""
    <h1>Admin Profile</h1>
    <p class="muted">Owner profile information is private and only available inside protected admin routes.</p>
    {f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
    {f"<p style='color:#ff9aa8'>{clean_html(error)}</p>" if error else ""}
    <form method="post" class="card">
      <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
      <div class="grid">
        {input_field("date_of_birth", "Date of birth", "date")}
        {input_field("job_title", "Job title")}
        {input_field("address_line1", "Address line 1")}
        {input_field("address_line2", "Address line 2")}
        {input_field("city", "City")}
        {input_field("state", "State")}
        {input_field("zip_code", "ZIP code")}
        {input_field("country", "Country")}
        {input_field("emergency_contact_name", "Emergency contact name")}
        {input_field("emergency_contact_phone", "Emergency contact phone")}
      </div>
      <p><label>Notes<textarea name="notes">{clean_html((admin or {}).get("notes") or "")}</textarea></label></p>
      <button type="submit">Save Profile</button>
    </form>
    """
    return admin_page_html("Admin Profile", body, admin)


ROLE_FALLBACK_PERMISSIONS = {
    "owner": {"*"},
    "super_admin": {"*"},
    "admin": {"users.view", "users.edit", "billing.view", "billing.repair", "emails.view", "telegram.view", "analytics.view", "support.manage", "system.view", "audit.view"},
    "billing_manager": {"users.view", "billing.view", "billing.repair", "subscriptions.edit", "emails.view"},
    "support_manager": {"users.view", "users.edit", "emails.view", "emails.resend", "telegram.view", "support.manage"},
    "support_agent": {"users.view", "emails.view", "telegram.view", "support.manage"},
    "analyst": {"analytics.view", "ai.view", "system.view"},
    "content_manager": {"analytics.view", "settings.edit"},
    "developer": {"system.view", "settings.edit", "audit.view", "ai.view"},
    "read_only": {"users.view", "billing.view", "emails.view", "telegram.view", "analytics.view", "system.view", "audit.view"},
}


def admin_has_permission(admin, permission):
    if not admin:
        return False
    role = (admin.get("role") or "").strip().lower()
    if role == "owner":
        return True
    permissions = ROLE_FALLBACK_PERMISSIONS.get(role, set())
    if "*" in permissions or permission in permissions:
        return True
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM role_permissions WHERE role_name=? AND permission_key=? LIMIT 1",
            (role, permission),
        )
        ok = bool(cur.fetchone())
        conn.close()
        return ok
    except Exception:
        return False


def require_admin_page(permission):
    init_db()
    admin = admin_login_required()
    if not admin:
        return None, redirect(url_for("admin_login_page"))
    if not admin_has_permission(admin, permission):
        log_admin_audit(admin.get("id"), "admin_permission_denied", "permission", permission, {"role": admin.get("role")})
        return None, Response("Forbidden", status=403)
    return admin, None


def require_owner_api():
    init_db()
    admin = admin_login_required()
    if not admin:
        return None, (jsonify({"ok": False, "error": "Admin login required."}), 401)
    if (admin.get("role") or "").lower() != "owner":
        log_admin_audit(admin.get("id"), "owner_permission_denied", "admin_user", str(admin.get("id")), {"path": request.path})
        return None, (jsonify({"ok": False, "error": "Owner permission required."}), 403)
    return admin, None


def require_admin_api(permission="users.view"):
    init_db()
    admin = admin_login_required()
    if not admin:
        return None, (jsonify({"ok": False, "error": "Admin login required."}), 401)
    if not admin_has_permission(admin, permission):
        log_admin_audit(admin.get("id"), "admin_permission_denied", "permission", permission, {"path": request.path})
        return None, (jsonify({"ok": False, "error": "Insufficient permissions."}), 403)
    return admin, None


def admin_user_action(admin, target_user_id, action, details=None):
    details = details or {}
    log_admin_audit((admin or {}).get("id"), action, "user", str(target_user_id), details)
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO admin_user_actions (admin_user_id, target_user_id, action, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ((admin or {}).get("id") or 0, target_user_id, action, json.dumps(details)[:4000], datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("admin_user_actions write failed safely: %s", exc)


def user_email_logs(user):
    user = user or {}
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, email_type, subject, status, recipient_email, provider, provider_message_id, error_message, created_at
        FROM email_logs
        WHERE user_id=? OR lower(COALESCE(recipient_email,email,''))=lower(?)
        ORDER BY id DESC
        LIMIT 100
        """,
        (user.get("user_id") or 0, user.get("email") or ""),
    )
    email_logs = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT id, email_type, template, status, stripe_event_id, payment_id, error_message, created_at, sent_at
        FROM payment_email_logs
        WHERE user_id=? OR lower(COALESCE(email,''))=lower(?)
        ORDER BY id DESC
        LIMIT 100
        """,
        (user.get("user_id") or 0, user.get("email") or ""),
    )
    payment_email_logs = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"email_logs": email_logs, "payment_email_logs": payment_email_logs}


def user_payment_history(user):
    user = user or {}
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM payment_records WHERE user_id=? ORDER BY id DESC LIMIT 100", (user.get("user_id") or 0,))
    payments = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 100", (user.get("user_id") or 0,))
    transactions = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT * FROM stripe_events
        WHERE user_id=? OR stripe_event_id IN (SELECT stripe_event_id FROM payment_records WHERE user_id=?)
        ORDER BY id DESC
        LIMIT 100
        """,
        (user.get("user_id") or 0, user.get("user_id") or 0),
    )
    stripe_events = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT * FROM unmatched_payments
        WHERE lower(COALESCE(customer_email,''))=lower(?) OR customer_id=?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user.get("email") or "", user.get("stripe_customer_id") or ""),
    )
    unmatched = [dict(row) for row in cur.fetchall()]
    conn.close()
    total_revenue = sum(float(p.get("amount") or 0) for p in payments if (p.get("status") or "").lower() == "succeeded")
    return {"payments": payments, "transactions": transactions, "stripe_events": stripe_events, "unmatched_payments": unmatched, "total_revenue": round(total_revenue, 2)}


def get_user_full_profile(user_id):
    user = load_account_by_id(user_id)
    if not user:
        return None
    payment_data = user_payment_history(user)
    email_data = user_email_logs(user)
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM user_activity WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,))
    activity = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM command_history WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,))
    commands = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT id, feature, prompt, response, metadata, created_at FROM user_ai_interactions WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
    ai = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT id, risk_level, risk_score, created_at FROM scam_scans WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
    scam_scans = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT id, notification_type, title, status, created_at, read_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
    notifications = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM visitor_logs WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
    visits = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT id, note, status, created_at, updated_at FROM support_notes WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
    support_notes_rows = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT id, admin_user_id, note, created_at FROM admin_user_notes WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
    notes = [dict(row) for row in cur.fetchall()] + support_notes_rows
    cur.execute("SELECT id, admin_user_id, action, details, created_at FROM admin_user_actions WHERE target_user_id=? ORDER BY id DESC LIMIT 100", (user_id,))
    admin_actions = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {
        "user": {**user, **backend_pro_status_payload(user)},
        "backend_pro_status": backend_pro_status_payload(user),
        "payments": payment_data,
        "emails": email_data,
        "activity": activity,
        "commands": commands,
        "ai_interactions": ai,
        "scam_scans": scam_scans,
        "notifications": notifications,
        "visitor_sessions": visits,
        "admin_notes": notes,
        "admin_actions": admin_actions,
    }


def admin_rows_table(rows, columns):
    header = "".join(f"<th>{clean_html(label)}</th>" for _, label in columns)
    if not rows:
        return f"<table><tr>{header}</tr><tr><td colspan='{len(columns)}'>No records yet.</td></tr></table>"
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{clean_html(str(row.get(key) or ''))}</td>" for key, _ in columns) + "</tr>"
    return f"<table><tr>{header}</tr>{body}</table>"


def admin_input(name, label, value="", input_type="text"):
    return f"<label>{clean_html(label)}<input name='{name}' type='{input_type}' value='{clean_html(str(value or ''))}' /></label>"


@webhook_app.route("/admin/users/new", methods=["GET", "POST"])
def admin_user_new_page():
    admin, denied = require_admin_page("users.create")
    if denied:
        return denied
    error = ""
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            error = "Security check failed."
        else:
            email = normalize_email(clean_html(request.form.get("email", "")))
            full_name = clean_html(request.form.get("full_name", ""))[:160]
            password = request.form.get("password") or secrets.token_urlsafe(14) + "Aa1!"
            if not is_valid_email(email):
                error = "Enter a valid email."
            else:
                user, error = create_account(full_name, email, password, clean_html(request.form.get("phone", "")), clean_html(request.form.get("country", "")), False, False)
                if user:
                    log_admin_audit(admin["id"], "admin_created_user", "user", str(user["user_id"]), {"email": mask_email(email)})
                    message = f"User created. Temporary password was generated only for this admin session: {clean_html(password)}"
    body = f"""
    <h1>Add User</h1>
    {f"<p style='color:#ff9aa8'>{clean_html(error)}</p>" if error else ""}
    {f"<p class='muted'>{message}</p>" if message else ""}
    <form method="post" class="card">
      <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
      <div class="grid">
        {admin_input("full_name", "Full name")}
        {admin_input("email", "Email", input_type="email")}
        {admin_input("phone", "Phone")}
        {admin_input("country", "Country")}
        {admin_input("password", "Temporary password")}
      </div>
      <button type="submit">Create User</button>
    </form>
    """
    return admin_page_html("Add User", body, admin)


@webhook_app.route("/admin/users/<int:user_id>", methods=["GET"])
def admin_user_detail_page(user_id):
    admin, denied = require_admin_page("users.view")
    if denied:
        return denied
    user = load_account_by_id(user_id)
    if not user:
        return admin_page_html("User Not Found", "<h1>User not found</h1>", admin), 404
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT event_type, event_label, created_at FROM user_activity WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,))
    activity = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT email_type, subject, status, created_at FROM email_logs WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,))
    emails = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT stripe_event_id, stripe_session_id, invoice_id, amount, currency, status, created_at FROM payment_records WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,))
    payments = [dict(row) for row in cur.fetchall()]
    latest_payment = payments[0] if payments else {}
    cur.execute("SELECT stripe_event_id, event_type, status, created_at, processed_at FROM stripe_events WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    latest_stripe_event = dict(cur.fetchone() or {})
    cur.execute("SELECT email_type, template, status, stripe_event_id, payment_id, created_at, sent_at, error_message FROM payment_email_logs WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    latest_payment_email = dict(cur.fetchone() or {})
    conn.close()
    paid_class = "Paid Pro" if is_paid_pro_user(user) else "Trial" if is_trialing_user(user) else "Free/Inactive"
    backend_status = backend_pro_status_payload(user)
    diagnostic_rows = {
        "User ID": backend_status.get("user_id"),
        "Email": backend_status.get("email"),
        "Plan": backend_status.get("plan"),
        "Subscription plan": backend_status.get("subscription_plan"),
        "Subscription status": backend_status.get("subscription_status"),
        "is_pro": backend_status.get("is_pro"),
        "has_pro_access": backend_status.get("has_pro_access"),
        "Latest payment": f"{latest_payment.get('status') or 'none'} {latest_payment.get('amount') or ''} {latest_payment.get('currency') or ''} {latest_payment.get('created_at') or ''}",
        "Latest Stripe event": f"{latest_stripe_event.get('event_type') or 'none'} {latest_stripe_event.get('status') or ''} {latest_stripe_event.get('stripe_event_id') or ''}",
        "Latest payment email": f"{latest_payment_email.get('email_type') or latest_payment_email.get('template') or 'none'} {latest_payment_email.get('status') or ''} {latest_payment_email.get('created_at') or ''}",
    }
    diagnostic = "".join(f"<div class='card'><strong>{clean_html(str(label))}</strong><p>{clean_html(str(value))}</p></div>" for label, value in diagnostic_rows.items())
    summary = "".join(
        f"<div class='card'><strong>{clean_html(label)}</strong><p>{clean_html(str(value or ''))}</p></div>"
        for label, value in {
            "Name": user.get("full_name") or user.get("display_name"),
            "Email": mask_email(user.get("email")),
            "Plan": user.get("plan"),
            "Subscription": user.get("subscription_status"),
            "Metric class": paid_class,
            "Trial start/end": f"{user.get('trial_start_date') or ''} / {user.get('trial_end_date') or ''}",
            "Pro expires": user.get("pro_expires_at"),
            "Stripe Customer": user.get("stripe_customer_id"),
            "Stripe Subscription": user.get("stripe_subscription_id"),
            "Telegram": user.get("telegram_username") or user.get("telegram_user_id"),
            "Created": user.get("created_at") or user.get("signup_time"),
            "Last login": user.get("last_login_at"),
        }.items()
    )
    body = (
        f"<h1>User #{user_id}</h1><p><a class='button' href='/admin/users/{user_id}/edit'>Edit User</a></p>"
        f"<form method='post' action='/admin/users/{user_id}/convert-paid-pro' class='card'><input type='hidden' name='csrf_token' value='{get_csrf_token()}' /><button type='submit'>Convert Trial to Paid Pro</button><p class='muted'>Use only after confirming a successful Stripe payment for this user.</p></form>"
        f"<form method='post' action='/admin/users/{user_id}/force-sync-pro' class='card'><input type='hidden' name='csrf_token' value='{get_csrf_token()}' /><button type='submit'>Force Sync Pro From Stripe/Payment</button><p class='muted'>Repairs this user only when a successful local payment record or Stripe event exists.</p></form>"
        f"<h2>Backend Pro Status</h2><div class='grid'>{diagnostic}</div>"
        f"<div class='grid'>{summary}</div>"
        f"<h2>Payment History</h2><div class='card'>{admin_rows_table(payments, [('amount','Amount'),('currency','Currency'),('status','Status'),('stripe_event_id','Event'),('invoice_id','Invoice'),('created_at','Date')])}</div>"
        f"<h2>Activity Timeline</h2><div class='card'><p class='muted'>Latest activity: {clean_html((activity[0] or {}).get('event_type') if activity else 'none')} · {len(activity)} recent items.</p><button type='button' onclick=\"var p=document.getElementById('activityPanel');p.hidden=!p.hidden;this.textContent=p.hidden?'View Activity Timeline':'Hide Timeline';\">View Activity Timeline</button><div id='activityPanel' hidden style='max-height:400px;overflow:auto;margin-top:12px'>{admin_rows_table(activity[:25], [('event_type','Event'),('event_label','Label'),('created_at','Date')])}</div></div>"
        f"<h2>Email Logs</h2><div class='card'>{admin_rows_table(emails, [('email_type','Type'),('subject','Subject'),('status','Status'),('created_at','Date')])}</div>"
    )
    return admin_page_html("User Detail", body, admin)


@webhook_app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
def admin_user_edit_page(user_id):
    admin, denied = require_admin_page("users.edit")
    if denied:
        return denied
    user = load_account_by_id(user_id)
    if not user:
        return admin_page_html("User Not Found", "<h1>User not found</h1>", admin), 404
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            message = "Security check failed."
        else:
            plan = clean_html(request.form.get("plan", user.get("plan") or "free"))[:20]
            status = clean_html(request.form.get("subscription_status", user.get("subscription_status") or "inactive"))[:40]
            account_status = clean_html(request.form.get("account_status", user.get("account_status") or "active"))[:40]
            now = datetime.now().isoformat()
            conn = db()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE users
                SET full_name=?, display_name=?, phone=?, country=?, account_status=?, plan=?,
                    subscription_status=?, pro_expires_at=?, updated_at=?
                WHERE user_id=?
                """,
                (
                    clean_html(request.form.get("full_name", user.get("full_name") or ""))[:160],
                    clean_html(request.form.get("full_name", user.get("full_name") or ""))[:160],
                    clean_html(request.form.get("phone", user.get("phone") or ""))[:40],
                    clean_html(request.form.get("country", user.get("country") or ""))[:80],
                    account_status,
                    plan,
                    status,
                    clean_html(request.form.get("pro_expires_at", user.get("pro_expires_at") or ""))[:60],
                    now,
                    user_id,
                ),
            )
            conn.commit()
            conn.close()
            log_admin_audit(admin["id"], "admin_edited_user", "user", str(user_id), {"plan": plan, "status": status})
            user = load_account_by_id(user_id)
            message = "User saved."
    body = f"""
    <h1>Edit User</h1>
    {f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
    <form method="post" class="card">
      <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
      <div class="grid">
        {admin_input("full_name", "Full name", user.get("full_name"))}
        {admin_input("phone", "Phone", user.get("phone"))}
        {admin_input("country", "Country", user.get("country"))}
        {admin_input("account_status", "Account status", user.get("account_status") or "active")}
        {admin_input("plan", "Plan", user.get("plan") or "free")}
        {admin_input("subscription_status", "Subscription status", user.get("subscription_status") or "inactive")}
        {admin_input("pro_expires_at", "Pro expires at", user.get("pro_expires_at"))}
      </div>
      <button type="submit">Save User</button>
    </form>
    """
    return admin_page_html("Edit User", body, admin)


@webhook_app.route("/admin/users/<int:user_id>/convert-paid-pro", methods=["POST"])
def admin_user_convert_paid_pro(user_id):
    admin, denied = require_admin_page("billing.repair")
    if denied:
        return denied
    if not verify_csrf():
        return admin_page_html("Security Check", "<h1>Security check failed.</h1>", admin), 400
    user = load_account_by_id(user_id)
    if not user:
        return admin_page_html("User Not Found", "<h1>User not found</h1>", admin), 404
    pro_expires_at = user.get("pro_expires_at") or (datetime.now() + timedelta(days=30)).isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET plan='pro', subscription_plan='pro', subscription_status='active', is_pro=1,
            trial_status='converted', pro_expires_at=?, subscription_expires_at=?, updated_at=?
        WHERE user_id=?
        """,
        (pro_expires_at, pro_expires_at, datetime.now().isoformat(), user_id),
    )
    conn.commit()
    conn.close()
    log_admin_audit(admin["id"], "admin_convert_trial_to_paid_pro", "user", str(user_id), {"email": mask_email(user.get("email"))})
    logging.info("TRIAL_TO_PAID_CONVERSION admin_repair user_id=%s", user_id)
    return redirect(url_for("admin_user_detail_page", user_id=user_id))


@webhook_app.route("/admin/users/<int:user_id>/force-sync-pro", methods=["POST"])
def admin_user_force_sync_pro(user_id):
    admin, denied = require_admin_page("billing.repair")
    if denied:
        return denied
    if not verify_csrf():
        return admin_page_html("Security Check", "<h1>Security check failed.</h1>", admin), 400
    user = load_account_by_id(user_id)
    if not user:
        return admin_page_html("User Not Found", "<h1>User not found</h1>", admin), 404
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM payment_records
        WHERE user_id=? AND lower(COALESCE(status,''))='succeeded'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    payment = dict(cur.fetchone() or {})
    if not payment:
        cur.execute(
            """
            SELECT * FROM transactions
            WHERE user_id=? AND lower(COALESCE(status,''))='succeeded'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        payment = dict(cur.fetchone() or {})
    if not payment:
        conn.close()
        log_admin_audit(admin["id"], "admin_force_sync_pro_failed", "user", str(user_id), {"reason": "no_successful_payment"})
        return admin_page_html(
            "No Successful Payment",
            "<h1>No successful payment found</h1><p>This repair only runs when the backend has a successful payment record or transaction for the user.</p>",
            admin,
        ), 400
    pro_expires_at = user.get("pro_expires_at") or user.get("subscription_expires_at") or (datetime.now() + timedelta(days=30)).isoformat()
    cur.execute(
        """
        UPDATE users
        SET plan='pro',
            subscription_plan='pro',
            subscription_status='active',
            trial_status='converted',
            is_pro=1,
            stripe_customer_id=COALESCE(?, stripe_customer_id),
            stripe_subscription_id=COALESCE(?, stripe_subscription_id),
            pro_expires_at=?,
            subscription_expires_at=?,
            updated_at=?
        WHERE user_id=?
        """,
        (
            payment.get("stripe_customer_id"),
            payment.get("stripe_subscription_id"),
            pro_expires_at,
            pro_expires_at,
            datetime.now().isoformat(),
            user_id,
        ),
    )
    conn.commit()
    conn.close()
    fresh_user = load_account_by_id(user_id) or {}
    send_successful_payment_email_bundle(fresh_user, {
        "stripe_event_id": payment.get("stripe_event_id") or f"force-sync-{user_id}",
        "stripe_session_id": payment.get("stripe_session_id") or "",
        "payment_id": payment.get("stripe_session_id") or payment.get("invoice_id") or payment.get("payment_intent_id") or f"force-sync-{user_id}",
        "amount": payment.get("amount"),
        "currency": payment.get("currency") or "USD",
        "billing_date": format_date(payment.get("created_at") or datetime.now().isoformat()),
    }, force=False)
    log_admin_audit(admin["id"], "admin_force_sync_pro_from_payment", "user", str(user_id), {"payment_record_id": payment.get("id"), "stripe_event_id": payment.get("stripe_event_id")})
    log_product_event(user_id, "admin_force_sync_pro_from_payment", {"admin_id": admin.get("id"), "payment_record_id": payment.get("id")})
    logging.info("ADMIN_FORCE_SYNC_PRO_FROM_PAYMENT user_id=%s payment_record_id=%s has_pro_access=%s", user_id, payment.get("id"), has_pro_access(fresh_user))
    return redirect(url_for("admin_user_detail_page", user_id=user_id))


@webhook_app.route("/admin/billing/recalculate", methods=["POST"])
def admin_billing_recalculate_page():
    admin, denied = require_admin_page("billing.repair")
    if denied:
        return denied
    if not verify_csrf():
        return admin_page_html("Security Check", "<h1>Security check failed.</h1>", admin), 400
    converted = repair_trialing_users_with_successful_payments()
    log_admin_audit(admin["id"], "admin_recalculate_billing_metrics", "billing", "metrics", {"converted": converted})
    logging.info("ADMIN_METRICS_QUERY_RESULT recalculated converted_trialing_paid_users=%s", converted)
    return redirect(url_for("admin_dashboard_page"))


@webhook_app.route("/admin/admins", methods=["GET"])
def admin_admins_page():
    admin, denied = require_admin_page("admins.view")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, full_name, email, role, status, last_login_at, created_at FROM admin_users ORDER BY id ASC")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>Admins</h1><p><a class='button' href='/admin/admins/new'>Create Admin</a></p><div class='card'>{admin_rows_table(rows, [('id','ID'),('full_name','Name'),('email','Email'),('role','Role'),('status','Status'),('last_login_at','Last Login')])}</div>"
    return admin_page_html("Admins", body, admin)


@webhook_app.route("/admin/admins/new", methods=["GET", "POST"])
def admin_admin_new_page():
    admin, denied = require_admin_page("admins.create")
    if denied:
        return denied
    message = ""
    error = ""
    temp_password = ""
    if request.method == "POST":
        if not verify_csrf():
            error = "Security check failed."
        else:
            email = normalize_email(clean_html(request.form.get("email", "")))
            role = clean_html(request.form.get("role", "admin"))[:60]
            if not is_valid_email(email):
                error = "Enter a valid email."
            else:
                temp_password = generate_owner_temp_password()
                now = datetime.now().isoformat()
                conn = db()
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO admin_users
                    (full_name, email, phone, password_hash, role, status, must_change_password, temp_password_created_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?, ?)
                    """,
                    (
                        clean_html(request.form.get("full_name", ""))[:160],
                        email,
                        clean_html(request.form.get("phone", ""))[:40],
                        generate_password_hash(temp_password),
                        role,
                        now,
                        now,
                        now,
                    ),
                )
                conn.commit()
                conn.close()
                log_admin_audit(admin["id"], "admin_created_admin", "admin_user", mask_email(email), {"role": role})
                message = "Admin created. Temporary password is shown once below."
    body = f"""
    <h1>Create Admin</h1>
    {f"<p style='color:#ff9aa8'>{clean_html(error)}</p>" if error else ""}
    {f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
    {f"<div class='card'><strong>Temporary password</strong><p><code>{clean_html(temp_password)}</code></p></div>" if temp_password else ""}
    <form method="post" class="card">
      <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
      <div class="grid">
        {admin_input("full_name", "Full name")}
        {admin_input("email", "Email", input_type="email")}
        {admin_input("phone", "Phone")}
        {admin_input("role", "Role", "admin")}
      </div>
      <button type="submit">Create Admin</button>
    </form>
    """
    return admin_page_html("Create Admin", body, admin)


@webhook_app.route("/admin/admins/<int:admin_id>/edit", methods=["GET", "POST"])
def admin_admin_edit_page(admin_id):
    admin, denied = require_admin_page("admins.edit")
    if denied:
        return denied
    target = None
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_users WHERE id=? LIMIT 1", (admin_id,))
    row = cur.fetchone()
    target = dict(row) if row else None
    conn.close()
    if not target:
        return admin_page_html("Admin Not Found", "<h1>Admin not found</h1>", admin), 404
    owner_locked = normalize_email(target.get("email")) == OWNER_ADMIN_EMAIL
    message = ""
    if request.method == "POST":
        if not verify_csrf():
            message = "Security check failed."
        elif owner_locked:
            message = "Owner role, status, and access cannot be changed here."
        else:
            conn = db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE admin_users SET full_name=?, phone=?, role=?, status=?, updated_at=? WHERE id=?",
                (
                    clean_html(request.form.get("full_name", target.get("full_name") or ""))[:160],
                    clean_html(request.form.get("phone", target.get("phone") or ""))[:40],
                    clean_html(request.form.get("role", target.get("role") or "admin"))[:60],
                    clean_html(request.form.get("status", target.get("status") or "active"))[:40],
                    datetime.now().isoformat(),
                    admin_id,
                ),
            )
            conn.commit()
            conn.close()
            log_admin_audit(admin["id"], "admin_edited_admin", "admin_user", str(admin_id), {})
            message = "Admin saved."
    body = f"""
    <h1>Edit Admin</h1>
    {f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
    <form method="post" class="card">
      <input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
      <div class="grid">
        {admin_input("full_name", "Full name", target.get("full_name"))}
        {admin_input("phone", "Phone", target.get("phone"))}
        {admin_input("role", "Role", target.get("role"))}
        {admin_input("status", "Status", target.get("status"))}
      </div>
      <button type="submit" {'disabled' if owner_locked else ''}>Save Admin</button>
    </form>
    """
    return admin_page_html("Edit Admin", body, admin)


@webhook_app.route("/admin/employees", methods=["GET"])
def admin_employees_page():
    admin, denied = require_admin_page("employees.view")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT employee_id, full_name, email, job_title, role, status, created_at FROM employees ORDER BY id DESC LIMIT 200")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>Employees</h1><p><a class='button' href='/admin/employees/new'>Add Employee</a></p><div class='card'>{admin_rows_table(rows, [('employee_id','Employee ID'),('full_name','Name'),('email','Email'),('job_title','Job'),('role','Role'),('status','Status')])}</div>"
    return admin_page_html("Employees", body, admin)


@webhook_app.route("/admin/employees/new", methods=["GET", "POST"])
def admin_employee_new_page():
    admin, denied = require_admin_page("employees.create")
    if denied:
        return denied
    message = ""
    if request.method == "POST" and verify_csrf():
        now = datetime.now().isoformat()
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO employees
            (employee_id, full_name, email, phone, job_title, role, status, start_date, address, date_of_birth, emergency_contact, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_html(request.form.get("employee_id", ""))[:80] or f"EMP-{int(time.time())}",
                clean_html(request.form.get("full_name", ""))[:160],
                normalize_email(clean_html(request.form.get("email", ""))),
                clean_html(request.form.get("phone", ""))[:40],
                clean_html(request.form.get("job_title", ""))[:120],
                clean_html(request.form.get("role", "employee"))[:60],
                clean_html(request.form.get("start_date", ""))[:40],
                clean_html(request.form.get("address", ""))[:240],
                clean_html(request.form.get("date_of_birth", ""))[:40],
                clean_html(request.form.get("emergency_contact", ""))[:200],
                clean_html(request.form.get("notes", ""))[:1000],
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        log_admin_audit(admin["id"], "admin_created_employee", "employee", "", {})
        message = "Employee created."
    body = f"""
    <h1>Add Employee</h1>{f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
    <form method="post" class="card"><input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
      <div class="grid">
        {admin_input("employee_id", "Employee ID")}
        {admin_input("full_name", "Full name")}
        {admin_input("email", "Email", input_type="email")}
        {admin_input("phone", "Phone")}
        {admin_input("job_title", "Job title")}
        {admin_input("role", "Role")}
        {admin_input("start_date", "Start date", input_type="date")}
        {admin_input("address", "Address")}
        {admin_input("date_of_birth", "Date of birth", input_type="date")}
        {admin_input("emergency_contact", "Emergency contact")}
      </div><button type="submit">Add Employee</button></form>
    """
    return admin_page_html("Add Employee", body, admin)


@webhook_app.route("/admin/employees/<int:employee_id>/edit", methods=["GET", "POST"])
def admin_employee_edit_page(employee_id):
    admin, denied = require_admin_page("employees.edit")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM employees WHERE id=? LIMIT 1", (employee_id,))
    row = cur.fetchone()
    employee = dict(row) if row else None
    conn.close()
    if not employee:
        return admin_page_html("Employee Not Found", "<h1>Employee not found</h1>", admin), 404
    message = ""
    if request.method == "POST" and verify_csrf():
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE employees SET full_name=?, phone=?, job_title=?, role=?, status=?, notes=?, updated_at=? WHERE id=?",
            (
                clean_html(request.form.get("full_name", employee.get("full_name") or ""))[:160],
                clean_html(request.form.get("phone", employee.get("phone") or ""))[:40],
                clean_html(request.form.get("job_title", employee.get("job_title") or ""))[:120],
                clean_html(request.form.get("role", employee.get("role") or ""))[:60],
                clean_html(request.form.get("status", employee.get("status") or "active"))[:40],
                clean_html(request.form.get("notes", employee.get("notes") or ""))[:1000],
                datetime.now().isoformat(),
                employee_id,
            ),
        )
        conn.commit()
        conn.close()
        log_admin_audit(admin["id"], "admin_edited_employee", "employee", str(employee_id), {})
        message = "Employee saved."
        employee = {**employee, "full_name": request.form.get("full_name", employee.get("full_name"))}
    body = f"""
    <h1>Edit Employee</h1>{f"<p class='muted'>{clean_html(message)}</p>" if message else ""}
    <form method="post" class="card"><input type="hidden" name="csrf_token" value="{get_csrf_token()}" />
      <div class="grid">
        {admin_input("full_name", "Full name", employee.get("full_name"))}
        {admin_input("phone", "Phone", employee.get("phone"))}
        {admin_input("job_title", "Job title", employee.get("job_title"))}
        {admin_input("role", "Role", employee.get("role"))}
        {admin_input("status", "Status", employee.get("status"))}
      </div>
      <p><label>Notes<textarea name="notes">{clean_html(employee.get("notes") or "")}</textarea></label></p>
      <button type="submit">Save Employee</button>
    </form>
    """
    return admin_page_html("Edit Employee", body, admin)


@webhook_app.route("/admin/departments", methods=["GET"])
def admin_departments_page():
    admin, denied = require_admin_page("departments.manage")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, name, status, created_at FROM departments ORDER BY name")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>Departments</h1><p><a class='button' href='/admin/departments/new'>Add Department</a></p><div class='card'>{admin_rows_table(rows, [('id','ID'),('name','Name'),('status','Status'),('created_at','Created')])}</div>"
    return admin_page_html("Departments", body, admin)


@webhook_app.route("/admin/departments/new", methods=["GET", "POST"])
def admin_department_new_page():
    admin, denied = require_admin_page("departments.manage")
    if denied:
        return denied
    message = ""
    if request.method == "POST" and verify_csrf():
        now = datetime.now().isoformat()
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO departments (name, description, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
            (clean_html(request.form.get("name", ""))[:120], clean_html(request.form.get("description", ""))[:500], now, now),
        )
        conn.commit()
        conn.close()
        log_admin_audit(admin["id"], "admin_created_department", "department", request.form.get("name", ""), {})
        message = "Department created."
    message_html = f"<p class='muted'>{clean_html(message)}</p>" if message else ""
    body = f"<h1>Add Department</h1>{message_html}<form method='post' class='card'><input type='hidden' name='csrf_token' value='{get_csrf_token()}' />{admin_input('name','Name')}{admin_input('description','Description')}<button type='submit'>Create Department</button></form>"
    return admin_page_html("Add Department", body, admin)


@webhook_app.route("/admin/departments/<int:department_id>/edit", methods=["GET", "POST"])
def admin_department_edit_page(department_id):
    admin, denied = require_admin_page("departments.manage")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM departments WHERE id=? LIMIT 1", (department_id,))
    row = cur.fetchone()
    department = dict(row) if row else None
    conn.close()
    if not department:
        return admin_page_html("Department Not Found", "<h1>Department not found</h1>", admin), 404
    message = ""
    if request.method == "POST" and verify_csrf():
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE departments SET name=?, description=?, status=?, updated_at=? WHERE id=?",
            (
                clean_html(request.form.get("name", department.get("name") or ""))[:120],
                clean_html(request.form.get("description", department.get("description") or ""))[:500],
                clean_html(request.form.get("status", department.get("status") or "active"))[:40],
                datetime.now().isoformat(),
                department_id,
            ),
        )
        conn.commit()
        conn.close()
        log_admin_audit(admin["id"], "admin_edited_department", "department", str(department_id), {})
        message = "Department saved."
    message_html = f"<p class='muted'>{clean_html(message)}</p>" if message else ""
    body = f"<h1>Edit Department</h1>{message_html}<form method='post' class='card'><input type='hidden' name='csrf_token' value='{get_csrf_token()}' />{admin_input('name','Name',department.get('name'))}{admin_input('description','Description',department.get('description'))}{admin_input('status','Status',department.get('status'))}<button type='submit'>Save Department</button></form>"
    return admin_page_html("Edit Department", body, admin)


@webhook_app.route("/admin/roles", methods=["GET"])
def admin_roles_page():
    admin, denied = require_admin_page("settings.edit")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name, description, status, created_at FROM roles ORDER BY name")
    roles = [dict(row) for row in cur.fetchall()]
    conn.close()
    return admin_page_html("Roles", f"<h1>Roles</h1><div class='card'>{admin_rows_table(roles, [('name','Role'),('description','Description'),('status','Status'),('created_at','Created')])}</div>", admin)


@webhook_app.route("/admin/permissions", methods=["GET"])
def admin_permissions_page():
    admin, denied = require_admin_page("settings.edit")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT key, description, created_at FROM permissions ORDER BY key")
    permissions = [dict(row) for row in cur.fetchall()]
    conn.close()
    return admin_page_html("Permissions", f"<h1>Permissions</h1><div class='card'>{admin_rows_table(permissions, [('key','Permission'),('description','Description'),('created_at','Created')])}</div>", admin)


@webhook_app.route("/admin/telegram", methods=["GET"])
def admin_telegram_page():
    admin, denied = require_admin_page("telegram.view")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id, full_name, email, telegram_user_id, telegram_username, telegram_chat_id, plan, subscription_status, last_seen_at FROM users WHERE telegram_user_id IS NOT NULL ORDER BY last_seen_at DESC LIMIT 200")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    for row in rows:
        row["email"] = mask_email(row.get("email"))
    body = f"<h1>Telegram Center</h1><div class='card'>{admin_rows_table(rows, [('user_id','User'),('full_name','Name'),('email','Email'),('telegram_user_id','Telegram ID'),('telegram_username','Username'),('plan','Plan'),('subscription_status','Subscription')])}</div>"
    return admin_page_html("Telegram", body, admin)


@webhook_app.route("/admin/ai-usage", methods=["GET"])
def admin_ai_usage_page():
    admin, denied = require_admin_page("ai.view")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT feature, COUNT(*) AS total FROM user_ai_interactions GROUP BY feature ORDER BY total DESC LIMIT 50")
    by_feature = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT user_id, feature, created_at FROM user_ai_interactions ORDER BY id DESC LIMIT 100")
    recent = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>AI Usage</h1><h2>By Feature</h2><div class='card'>{admin_rows_table(by_feature, [('feature','Feature'),('total','Requests')])}</div><h2>Recent</h2><div class='card'>{admin_rows_table(recent, [('user_id','User'),('feature','Feature'),('created_at','Date')])}</div>"
    return admin_page_html("AI Usage", body, admin)


@webhook_app.route("/admin/command-logs", methods=["GET"])
def admin_command_logs_page():
    admin, denied = require_admin_page("ai.view")
    if denied:
        return denied
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT command_name, source, status, COUNT(*) AS total FROM command_history GROUP BY command_name, source, status ORDER BY total DESC LIMIT 80")
    by_command = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT user_id, command_name, source, status, error, created_at FROM command_history ORDER BY id DESC LIMIT 150")
    recent = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>Command Logs</h1><h2>Most Used Commands</h2><div class='card'>{admin_rows_table(by_command, [('command_name','Command'),('source','Source'),('status','Status'),('total','Total')])}</div><h2>Recent Command Activity</h2><div class='card'>{admin_rows_table(recent, [('user_id','User'),('command_name','Command'),('source','Source'),('status','Status'),('error','Error'),('created_at','Created')])}</div>"
    return admin_page_html("Command Logs", body, admin)


@webhook_app.route("/admin/scam-shield", methods=["GET"])
def admin_scam_shield_page():
    admin, denied = require_admin_page("ai.view")
    if denied:
        return denied
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT risk_level, COUNT(*) AS total FROM scam_scans GROUP BY risk_level ORDER BY total DESC")
    by_risk = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT user_id, risk_level, risk_score, confidence, source_status, created_at FROM scam_scans ORDER BY id DESC LIMIT 150")
    recent = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT report_type, status, COUNT(*) AS total FROM security_reports GROUP BY report_type, status ORDER BY total DESC LIMIT 80")
    reports = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = (
        "<h1>Scam Shield Threat Analytics</h1>"
        f"<div class='grid'><div class='card'><h2>Risk Levels</h2>{admin_rows_table(by_risk, [('risk_level','Risk'),('total','Total')])}</div>"
        f"<div class='card'><h2>Security Reports</h2>{admin_rows_table(reports, [('report_type','Type'),('status','Status'),('total','Total')])}</div></div>"
        f"<h2>Recent Scam Scans</h2><div class='card'>{admin_rows_table(recent, [('user_id','User'),('risk_level','Risk'),('risk_score','Score'),('confidence','Confidence'),('source_status','Source'),('created_at','Created')])}</div>"
    )
    return admin_page_html("Scam Shield", body, admin)


@webhook_app.route("/admin/notifications", methods=["GET"])
def admin_notifications_page():
    admin, denied = require_admin_page("analytics.view")
    if denied:
        return denied
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT notification_type, status, COUNT(*) AS total FROM notifications GROUP BY notification_type, status ORDER BY total DESC LIMIT 80")
    stats = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT channel, status, COUNT(*) AS total FROM notification_delivery_logs GROUP BY channel, status ORDER BY total DESC LIMIT 80")
    delivery = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>Notifications</h1><h2>Notification Stats</h2><div class='card'>{admin_rows_table(stats, [('notification_type','Type'),('status','Status'),('total','Total')])}</div><h2>Delivery Logs</h2><div class='card'>{admin_rows_table(delivery, [('channel','Channel'),('status','Status'),('total','Total')])}</div>"
    return admin_page_html("Notifications", body, admin)


@webhook_app.route("/admin/private-chat-reports", methods=["GET"])
def admin_private_chat_reports_page():
    admin, denied = require_admin_page("support.manage")
    if denied:
        return denied
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, reporter_user_id, reported_user_id, conversation_id, message_id, reason, status, created_at FROM chat_reports ORDER BY id DESC LIMIT 200")
    reports = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>Private Chat Reports</h1><div class='card'>{admin_rows_table(reports, [('id','ID'),('reporter_user_id','Reporter'),('reported_user_id','Reported'),('conversation_id','Conversation'),('message_id','Message'),('reason','Reason'),('status','Status'),('created_at','Created')])}</div>"
    return admin_page_html("Private Chat Reports", body, admin)


@webhook_app.route("/admin/security", methods=["GET"])
def admin_security_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT event_type, email, user_id, status, db_engine, created_at FROM auth_events ORDER BY id DESC LIMIT 120")
    auth_rows = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT admin_email, action, target_type, target_id, created_at FROM admin_audit_logs ORDER BY id DESC LIMIT 120")
    audit_rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    body = f"<h1>Security Center</h1><h2>Authentication Events</h2><div class='card'>{admin_rows_table(auth_rows, [('event_type','Event'),('email','Email'),('user_id','User'),('status','Status'),('db_engine','DB'),('created_at','Date')])}</div><h2>Admin Audit</h2><div class='card'>{admin_rows_table(audit_rows, [('admin_email','Admin'),('action','Action'),('target_type','Target'),('target_id','ID'),('created_at','Date')])}</div>"
    return admin_page_html("Security", body, admin)


@webhook_app.route("/admin/settings", methods=["GET"])
def admin_settings_page():
    admin, denied = require_admin_page("settings.edit")
    if denied:
        return denied
    checks = {
        "DATABASE_URL": db_service.DATABASE_URL_LOADED,
        "Stripe secret": bool(STRIPE_SECRET_KEY),
        "Stripe price": bool(STRIPE_PRICE_ID),
        "Stripe webhook": bool(STRIPE_WEBHOOK_SECRET),
        "Brevo": bool(os.getenv("BREVO_API_KEY")),
        "OpenAI": bool(os.getenv("OPENAI_API_KEY")),
        "Telegram": bool(BOT_TOKEN),
    }
    body = "<h1>Settings</h1><div class='grid'>" + "".join(f"<div class='card'><strong>{clean_html(k)}</strong><p class='metric'>{'OK' if v else 'Missing'}</p></div>" for k, v in checks.items()) + "</div>"
    return admin_page_html("Settings", body, admin)


@webhook_app.route("/admin/support", methods=["GET", "POST"])
def admin_support_page():
    admin, denied = require_admin_page("support.manage")
    if denied:
        return denied
    if request.method == "POST" and verify_csrf():
        conn = db()
        cur = conn.cursor()
        ticket_id = int(request.form.get("ticket_id") or 0)
        reply_message = clean_html(request.form.get("reply_message") or "")[:4000]
        cur.execute(
            "UPDATE support_tickets SET status=?, assigned_to=?, updated_at=? WHERE id=?",
            (
                clean_html(request.form.get("status", "open"))[:40],
                admin["id"],
                datetime.now().isoformat(),
                ticket_id,
            ),
        )
        if reply_message:
            cur.execute(
                "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_admin_id, message, created_at) VALUES (?, 'admin', ?, ?, ?)",
                (ticket_id, admin["id"], reply_message, datetime.now().isoformat()),
            )
        conn.commit()
        conn.close()
        if reply_message:
            send_channel_email(
                request.form.get("ticket_email") or "support@coinpilotx.app",
                "CoinPilotXAI Support Reply",
                f"<p>{clean_html(reply_message)}</p>",
                reply_message,
                user_id=0,
                email_type="support_reply",
                channel="support",
            )
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, email, name, issue_type, subject, status, priority, created_at FROM support_tickets ORDER BY id DESC LIMIT 150")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    quick_reply = f"""
    <div class='card'>
      <h2>Reply / Close Ticket</h2>
      <form method='post'>
        <input type='hidden' name='csrf_token' value='{get_csrf_token()}' />
        <input name='ticket_id' placeholder='Ticket ID' />
        <input name='ticket_email' placeholder='Recipient email' />
        <select name='status'><option value='open'>Open</option><option value='escalated'>Escalated</option><option value='closed'>Closed</option></select>
        <textarea name='reply_message' placeholder='Optional reply message'></textarea>
        <button type='submit'>Update Ticket</button>
      </form>
    </div>
    """
    body = f"<h1>Support Center</h1>{quick_reply}<div class='card'>{admin_rows_table(rows, [('id','ID'),('email','Email'),('name','Name'),('issue_type','Type'),('subject','Subject'),('status','Status'),('priority','Priority'),('created_at','Created')])}</div>"
    return admin_page_html("Support", body, admin)


@webhook_app.route("/admin/audit", methods=["GET"])
def admin_audit_alias_page():
    return admin_audit_logs_page()


@webhook_app.route("/admin/support-notes", methods=["GET"])
def admin_placeholder_page():
    init_db()
    admin = admin_login_required()
    if not admin:
        return redirect(url_for("admin_login_page"))
    body = "<h1>Admin Workspace</h1><div class='card'><p>This protected workspace is ready for deeper role-specific controls. Core owner visibility, users, transactions, email logs, audit logs, and unmatched payment repair are active.</p></div>"
    return admin_page_html("Admin Workspace", body, admin)


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
    cur.execute("SELECT COUNT(*) FROM users WHERE created_at>=?", (since_day,))
    new_users_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE telegram_user_id IS NOT NULL")
    linked_telegram_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE is_pro=1 OR lower(COALESCE(plan,''))='pro' OR lower(COALESCE(subscription_status,''))='active'")
    pro_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT email) FROM brevo_contact_sync_logs WHERE status='success' AND email!=''")
    brevo_synced_contacts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM brevo_contact_sync_logs WHERE status!='success'")
    brevo_failed_syncs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT email) FROM (SELECT email FROM leads WHERE email_opt_in=1 AND email!='' UNION SELECT email FROM users WHERE email_opt_in=1 AND email!='')")
    email_subscribers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT phone) FROM (SELECT phone FROM leads WHERE sms_opt_in=1 AND phone!='' UNION SELECT phone FROM users WHERE sms_opt_in=1 AND phone!='')")
    sms_subscribers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM analytics_events WHERE created_at>=? AND (referrer LIKE '%google.%' OR referrer LIKE '%bing.%' OR referrer LIKE '%duckduckgo.%' OR referrer LIKE '%search.yahoo.%')", (since_day,))
    organic_visits = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM referral_events WHERE created_at>=?", (since_day,))
    referral_clicks = cur.fetchone()[0]
    conversion_rate = (leads_today / visitors_today * 100) if visitors_today else 0
    cur.execute("SELECT COUNT(*) FROM users WHERE lower(COALESCE(subscription_status,''))='trialing'")
    pro_trial_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE lower(COALESCE(plan,''))='pro' AND lower(COALESCE(subscription_status,''))='active'")
    paid_pro_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE lower(COALESCE(subscription_status,''))='expired' AND COALESCE(trial_used,0)=1")
    expired_trials = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE lower(COALESCE(plan,''))='free'")
    free_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE lower(COALESCE(subscription_status,''))='trialing' AND trial_end_date<=?", ((datetime.now() + timedelta(days=7)).isoformat(),))
    trials_expiring_soon = cur.fetchone()[0]
    trial_conversion_rate = (paid_pro_users / (paid_pro_users + expired_trials + pro_trial_users) * 100) if (paid_pro_users + expired_trials + pro_trial_users) else 0

    def rows(sql, params=()):
        cur.execute(sql, params)
        return cur.fetchall()

    data = {
        "live_visitors": live_visitors,
        "visitors_today": visitors_today,
        "page_views_today": page_views_today,
        "leads_today": leads_today,
        "new_users_today": new_users_today,
        "linked_telegram_users": linked_telegram_users,
        "pro_users": pro_users,
        "brevo_synced_contacts": brevo_synced_contacts,
        "brevo_failed_syncs": brevo_failed_syncs,
        "email_subscribers": email_subscribers,
        "sms_subscribers": sms_subscribers,
        "organic_visits": organic_visits,
        "referral_clicks": referral_clicks,
        "conversion_rate": conversion_rate,
        "pro_trial_users": pro_trial_users,
        "paid_pro_users": paid_pro_users,
        "expired_trials": expired_trials,
        "free_users": free_users,
        "trials_expiring_soon": trials_expiring_soon,
        "trial_conversion_rate": trial_conversion_rate,
        "top_pages": rows("SELECT page_url, COUNT(*) FROM analytics_events WHERE created_at>=? AND page_url!='' GROUP BY page_url ORDER BY COUNT(*) DESC LIMIT 8", (since_day,)),
        "referrers": rows("SELECT COALESCE(NULLIF(referrer,''),'Direct'), COUNT(*) FROM analytics_events WHERE created_at>=? GROUP BY COALESCE(NULLIF(referrer,''),'Direct') ORDER BY COUNT(*) DESC LIMIT 8", (since_day,)),
        "devices": rows("SELECT device_type, browser, COUNT(*) FROM analytics_events WHERE created_at>=? GROUP BY device_type, browser ORDER BY COUNT(*) DESC LIMIT 10", (since_day,)),
        "cta_clicks": rows("SELECT event_name, COUNT(*) FROM analytics_events WHERE created_at>=? AND event_name LIKE '%click%' GROUP BY event_name ORDER BY COUNT(*) DESC LIMIT 10", (since_day,)),
        "feature_usage": rows("SELECT feature, COUNT(*) FROM user_ai_interactions WHERE created_at>=? GROUP BY feature ORDER BY COUNT(*) DESC LIMIT 12", (since_day,)),
        "bot_usage": rows("SELECT feature, COUNT(*) FROM user_ai_interactions WHERE created_at>=? AND metadata LIKE '%telegram%' GROUP BY feature ORDER BY COUNT(*) DESC LIMIT 10", (since_day,)),
        "website_usage": rows("SELECT feature, COUNT(*) FROM user_ai_interactions WHERE created_at>=? AND metadata LIKE '%website%' GROUP BY feature ORDER BY COUNT(*) DESC LIMIT 10", (since_day,)),
        "api_failures": rows("SELECT event_name, page_url, metadata, created_at FROM analytics_events WHERE event_name='api_failure' ORDER BY id DESC LIMIT 15"),
        "email_sends": rows("SELECT subject, status, COUNT(*) FROM email_logs WHERE created_at>=? GROUP BY subject, status ORDER BY COUNT(*) DESC LIMIT 12", (since_day,)),
        "brevo_syncs": rows("SELECT entity_type, email, status, list_names, created_at FROM brevo_contact_sync_logs ORDER BY id DESC LIMIT 25"),
        "top_converting_pages": rows("SELECT page_url, COUNT(*) FROM analytics_events WHERE created_at>=? AND event_name='signup_form_submit' GROUP BY page_url ORDER BY COUNT(*) DESC LIMIT 10", (since_day,)),
        "top_intent_pages": rows("SELECT page_url, COUNT(*) FROM analytics_events WHERE created_at>=? AND (page_url LIKE '%crypto%' OR page_url LIKE '%wallet%' OR page_url LIKE '%sports%' OR page_url LIKE '%markets%' OR page_url LIKE '%telegram%') GROUP BY page_url ORDER BY COUNT(*) DESC LIMIT 12", (since_day,)),
        "referral_codes": rows("SELECT referral_code, COUNT(*) FROM referral_events WHERE created_at>=? GROUP BY referral_code ORDER BY COUNT(*) DESC LIMIT 12", (since_day,)),
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
	      <div class="card"><div>New users today</div><div class="metric">{data['new_users_today']}</div></div>
	      <div class="card"><div>Linked Telegram users</div><div class="metric">{data['linked_telegram_users']}</div></div>
	      <div class="card"><div>Pro users</div><div class="metric">{data['pro_users']}</div></div>
	      <div class="card"><div>Leads today</div><div class="metric">{data['leads_today']}</div></div>
	      <div class="card"><div>Brevo synced contacts</div><div class="metric">{data['brevo_synced_contacts']}</div></div>
	      <div class="card"><div>Brevo sync failures</div><div class="metric">{data['brevo_failed_syncs']}</div></div>
	      <div class="card"><div>Email subscribers</div><div class="metric">{data['email_subscribers']}</div></div>
	      <div class="card"><div>SMS subscribers</div><div class="metric">{data['sms_subscribers']}</div></div>
	      <div class="card"><div>Organic search visits</div><div class="metric">{data['organic_visits']}</div></div>
	      <div class="card"><div>Referral clicks</div><div class="metric">{data['referral_clicks']}</div></div>
	      <div class="card"><div>Pro trial users</div><div class="metric">{data['pro_trial_users']}</div></div>
	      <div class="card"><div>Paid Pro users</div><div class="metric">{data['paid_pro_users']}</div></div>
	      <div class="card"><div>Expired trials</div><div class="metric">{data['expired_trials']}</div></div>
	      <div class="card"><div>Trials expiring 7 days</div><div class="metric">{data['trials_expiring_soon']}</div></div>
	      <div class="card"><div>Free users</div><div class="metric">{data['free_users']}</div></div>
	      <div class="card"><div>Trial conversion</div><div class="metric">{data['trial_conversion_rate']:.1f}%</div></div>
	    </div>
	    <h2>Top pages</h2>{table(data['top_pages'], ['Page', 'Events'])}
	    <h2>Referral sources</h2>{table(data['referrers'], ['Referrer', 'Events'])}
	    <h2>Device / browser</h2>{table(data['devices'], ['Device', 'Browser', 'Events'])}
	    <h2>CTA clicks</h2>{table(data['cta_clicks'], ['Event', 'Clicks'])}
	    <h2>Most-used intelligence features</h2>{table(data['feature_usage'], ['Feature', 'Uses'])}
	    <h2>Bot usage</h2>{table(data['bot_usage'], ['Feature', 'Uses'])}
	    <h2>Website intelligence usage</h2>{table(data['website_usage'], ['Feature', 'Uses'])}
	    <h2>API failures</h2>{table(data['api_failures'], ['Event', 'Page', 'Metadata', 'Time'])}
	    <h2>Email sends</h2>{table(data['email_sends'], ['Subject', 'Status', 'Count'])}
	    <h2>Brevo contact syncs</h2>{table(data['brevo_syncs'], ['Type', 'Email', 'Status', 'Lists', 'Time'])}
	    <h2>Top converting pages</h2>{table(data['top_converting_pages'], ['Page', 'Signups'])}
	    <h2>Top search intent pages</h2>{table(data['top_intent_pages'], ['Page', 'Events'])}
	    <h2>Referral codes</h2>{table(data['referral_codes'], ['Code', 'Clicks'])}
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


@webhook_app.route("/admin/brevo/resync", methods=["POST"])
def admin_brevo_resync():
    if not require_admin_password():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    init_db()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM leads WHERE email!=''")
    leads = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM users WHERE email!=''")
    users = [dict(row) for row in cur.fetchall()]
    conn.close()
    synced = 0
    failed = 0
    for lead in leads:
        result = sync_brevo_contact_safe({**lead, "source": lead.get("source") or "website_lead", "plan": "free"}, entity_type="lead", entity_id=lead.get("id"))
        synced += 1 if result.get("ok") else 0
        failed += 0 if result.get("ok") else 1
    for user in users:
        result = sync_brevo_contact_safe({**user, "source": "website_account"}, entity_type="user", entity_id=user.get("user_id"))
        synced += 1 if result.get("ok") else 0
        failed += 0 if result.get("ok") else 1
    return jsonify({"ok": True, "synced": synced, "failed": failed, "leads_checked": len(leads), "users_checked": len(users)})


@webhook_app.route("/admin/debug/auth-test", methods=["GET"])
def admin_debug_auth_test():
    if not require_admin_password():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    init_db()
    diagnostics = db_service.health_check()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT user_id, full_name, email, plan, subscription_status, created_at, last_login_at FROM users ORDER BY user_id DESC LIMIT 10")
    latest_users = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT event_type, email, user_id, status, db_engine, created_at FROM auth_events ORDER BY id DESC LIMIT 25")
    auth_events = [dict(row) for row in cur.fetchall()]
    required_tables = ["users", "password_reset_tokens", "email_logs", "subscriptions", "admin_users", "sessions"]
    tables = set(diagnostics.get("tables_detected") or [])
    table_status = {name: name in tables for name in required_tables}
    conn.close()
    return jsonify({
        "ok": True,
        "active_db_engine": db_service.ENGINE_NAME,
        "database_url_loaded": db_service.DATABASE_URL_LOADED,
        "database_name": diagnostics.get("database_name"),
        "engine_url_masked": diagnostics.get("engine_url_masked"),
        "postgresql_connection_state": "connected" if diagnostics.get("connected") and db_service.IS_POSTGRES else "not_postgresql_or_disconnected",
        "database_latency_ms": diagnostics.get("latency_ms"),
        "users_count": total_users,
        "latest_users": [
            {**row, "email": mask_email(row.get("email"))}
            for row in latest_users
        ],
        "latest_signup_attempts": auth_events,
        "auth_table_status": table_status,
    })


@webhook_app.route("/debug/stripe-user", methods=["GET"])
@webhook_app.route("/debug/user-email-status", methods=["GET"])
def debug_stripe_user():
    if not require_admin_password():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    email = normalize_email(clean_html(request.args.get("email", "")))
    if not is_valid_email(email):
        return jsonify({"ok": False, "message": "Provide a valid email."}), 400
    user = load_account_by_email(email)
    if not user:
        return jsonify({"ok": False, "message": "User not found."}), 404
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, payment_type, txid, amount, status, details, created_at FROM payment_verifications WHERE user_id=? ORDER BY id DESC LIMIT 10",
        (user["user_id"],),
    )
    payments = [dict(row) for row in cur.fetchall()]
    cur.execute(
        "SELECT id, plan, status, payment_type, stripe_customer_id, stripe_subscription_id, current_period_end, pro_expires_at, created_at FROM subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 10",
        (user["user_id"],),
    )
    subscriptions = [dict(row) for row in cur.fetchall()]
    cur.execute(
        "SELECT id, stripe_event_id, stripe_session_id, stripe_customer_id, stripe_subscription_id, invoice_id, amount, currency, status, created_at FROM payment_records WHERE user_id=? ORDER BY id DESC LIMIT 10",
        (user["user_id"],),
    )
    payment_records = [dict(row) for row in cur.fetchall()]
    cur.execute(
        "SELECT id, email_type, recipient_email, subject, status, stripe_event_id, stripe_session_id, sent_at, created_at FROM email_logs WHERE user_id=? ORDER BY id DESC LIMIT 10",
        (user["user_id"],),
    )
    email_logs = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({
        "ok": True,
        "user": {
            "user_id": user.get("user_id"),
            "email": mask_email(user.get("email")),
            "plan": user.get("plan") or user.get("subscription_plan"),
            "subscription_status": user.get("subscription_status"),
            "stripe_customer_id": user.get("stripe_customer_id"),
            "stripe_subscription_id": user.get("stripe_subscription_id"),
            "pro_expires_at": user.get("pro_expires_at") or user.get("subscription_expires_at"),
            "telegram_linked": bool(user.get("telegram_user_id")),
            "has_pro": has_pro_access(user),
        },
        "payments": payments,
        "payment_records": payment_records,
        "subscriptions": subscriptions,
        "email_logs": email_logs,
    })


@webhook_app.route("/debug/password-reset-email-test", methods=["GET"])
def debug_password_reset_email_test():
    if not require_admin_password():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    email = normalize_email(clean_html(request.args.get("email", "")))
    user = load_account_by_email(email)
    if not user:
        return jsonify({"ok": False, "message": "User not found."}), 404
    sent = send_password_reset_email(user, "https://coinpilotx.app/reset-password/TEST-LINK-NOT-A-REAL-TOKEN")
    return jsonify({"ok": bool(sent), "email": mask_email(email), "sent": bool(sent)})


@webhook_app.route("/debug/upgrade-email-test", methods=["GET"])
def debug_upgrade_email_test():
    if not require_admin_password():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    email = normalize_email(clean_html(request.args.get("email", "")))
    user = load_account_by_email(email)
    if not user:
        return jsonify({"ok": False, "message": "User not found."}), 404
    sent = send_upgrade_confirmation_email(user, {
        "stripe_event_id": f"debug_upgrade_email_{user['user_id']}_{int(time.time())}",
        "stripe_session_id": "debug_upgrade_email",
        "billing_date": datetime.now().strftime("%b %d, %Y"),
        "next_billing_date": "Debug test",
    })
    return jsonify({"ok": bool(sent), "email": mask_email(email), "sent": bool(sent)})


@webhook_app.route("/admin/stripe/repair-user-pro", methods=["POST"])
def admin_repair_user_pro():
    if not require_admin_password():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    payload = request.get_json(silent=True) or request.form
    email = normalize_email(clean_html(payload.get("email", "")))
    if not is_valid_email(email):
        return jsonify({"ok": False, "message": "Provide a valid email."}), 400
    user = load_account_by_email(email)
    if not user:
        return jsonify({"ok": False, "message": "User not found."}), 404
    pro_expires_at = (datetime.now() + timedelta(days=30)).isoformat()
    activated_user_id = activate_pro(
        user["user_id"],
        payment_type="stripe_manual_repair",
        stripe_customer_id=clean_html(payload.get("stripe_customer_id", "")).strip() or None,
        stripe_subscription_id=clean_html(payload.get("stripe_subscription_id", "")).strip() or None,
        subscription_status="active",
        pro_expires_at=pro_expires_at,
    )
    if activated_user_id:
        repaired_user = load_account_by_id(activated_user_id)
        if repaired_user:
            send_upgrade_confirmation_email(repaired_user, {
                "stripe_event_id": f"manual_repair_{activated_user_id}_{int(time.time())}",
                "stripe_session_id": clean_html(payload.get("stripe_subscription_id", "")).strip() or "manual_repair",
                "next_billing_date": format_date(pro_expires_at),
            })
        log_admin_audit(0, "manual_pro_repair", "user", str(activated_user_id), {"email": mask_email(email)})
    logging.warning("Admin Stripe repair executed email=%s user_id=%s activated_user_id=%s", email, user["user_id"], activated_user_id)
    return jsonify({
        "ok": bool(activated_user_id),
        "user_id": activated_user_id,
        "email": mask_email(email),
        "plan": "pro",
        "subscription_status": "active",
        "pro_expires_at": pro_expires_at,
    })


@webhook_app.route("/robots.txt", methods=["GET"])
def robots_txt():
    return send_from_directory(webhook_app.static_folder, "robots.txt", mimetype="text/plain")


@webhook_app.route("/llms.txt", methods=["GET"])
def llms_txt():
    return send_from_directory(webhook_app.static_folder, "llms.txt", mimetype="text/plain")


@webhook_app.route("/ai-index.json", methods=["GET"])
def ai_index_json():
    return jsonify(seo_index_payload())


@webhook_app.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    today = datetime.now().strftime("%Y-%m-%d")
    priority = {
        "/": "1.0",
        "/ai-market-analysis": "0.92",
        "/telegram-crypto-bot": "0.92",
        "/crypto-scams": "0.9",
        "/wallet-security": "0.88",
        "/sports-edge": "0.86",
        "/portfolio-intelligence": "0.86",
    }
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in all_public_paths():
        loc = "https://coinpilotx.app" + path
        if path.startswith("/markets/") and path.endswith("/live"):
            page_priority = "0.82"
        elif path.startswith("/markets/") and path.endswith("/prediction"):
            page_priority = "0.8"
        elif path.startswith("/markets/"):
            page_priority = "0.8"
        elif path.startswith("/sports-edge/") or path.startswith("/intel/"):
            page_priority = "0.78"
        elif path.startswith("/country-intelligence/"):
            page_priority = "0.74"
        else:
            page_priority = priority.get(path, "0.72")
        body.append("  <url>")
        body.append(f"    <loc>{loc}</loc>")
        body.append(f"    <lastmod>{today}</lastmod>")
        body.append("    <changefreq>weekly</changefreq>")
        body.append(f"    <priority>{page_priority}</priority>")
        body.append("  </url>")
    body.append("</urlset>")
    return Response("\n".join(body), mimetype="application/xml")


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
            *["https://coinpilotx.app" + path for path in all_public_paths()],
        ],
        "submitEndpoint": "https://api.indexnow.org/indexnow",
    })


@webhook_app.route("/api/intelligence-feed", methods=["GET"])
def intelligence_feed_api():
    return jsonify(intelligence_service.intelligence_feed())


def record_command_history(user_id, command_name, input_text, result, source="web", pro_required=False, status="success", error=""):
    try:
        output_summary = result.get("summary") or result.get("message") or result.get("title") or ""
        if not output_summary and result.get("cards"):
            output_summary = " ".join(str(card.get("title") or "") for card in result.get("cards")[:3]).strip()
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO command_history
            (user_id, command_name, input, output_summary, source, pro_required, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                (command_name or result.get("action_key") or "command")[:120],
                (input_text or "")[:2000],
                clean_html(str(output_summary))[:2000],
                source,
                1 if pro_required else 0,
                status,
                clean_html(str(error or ""))[:800],
                datetime.now().isoformat(),
            ),
        )
        history_id = cur.lastrowid
        conn.commit()
        conn.close()
        return history_id
    except Exception as exc:
        logging.warning("command history save failed safely: user_id=%s command=%s error=%s", user_id, command_name, exc)
        return None


def command_history_payload(user_id, limit=50):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, command_name, input, output_summary, source, pro_required, status, error, created_at
        FROM command_history
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def save_command_result(user_id, command_history_id, title="", summary="", source="web", metadata=None):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO saved_command_results
        (user_id, command_history_id, title, summary, source, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            int(command_history_id or 0),
            clean_html(title or "Saved CoinPilotXAI result")[:180],
            clean_html(summary or "")[:3000],
            clean_html(source or "web")[:80],
            json.dumps(metadata or {}),
            datetime.now().isoformat(),
        ),
    )
    saved_id = cur.lastrowid
    conn.commit()
    conn.close()
    return saved_id


def get_or_create_ai_conversation(user_id, conversation_id=None):
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    if conversation_id:
        cur.execute("SELECT id FROM ai_conversations WHERE id=? AND user_id=? LIMIT 1", (int(conversation_id), user_id))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE ai_conversations SET updated_at=? WHERE id=?", (now, row[0]))
            conn.commit()
            conn.close()
            return row[0]
    cur.execute(
        "INSERT INTO ai_conversations (user_id, title, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
        (user_id, "CoinPilotXAI Chat", now, now),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def save_ai_message(user_id, conversation_id, role, content, metadata=None):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ai_messages (conversation_id, user_id, role, content, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            user_id,
            role,
            clean_html(content or "")[:8000],
            json.dumps(metadata or {})[:4000],
            datetime.now().isoformat(),
        ),
    )
    message_id = cur.lastrowid
    cur.execute("UPDATE ai_conversations SET updated_at=? WHERE id=? AND user_id=?", (datetime.now().isoformat(), conversation_id, user_id))
    conn.commit()
    conn.close()
    return message_id


@webhook_app.route("/api/commands", methods=["GET"])
def api_commands():
    init_db()
    user = require_account()
    payload = command_router_service.get_menu_items(load_account_by_id(user["user_id"]) if user else None)
    payload["ok"] = True
    payload["authenticated"] = bool(user)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/menu", methods=["GET"])
def api_menu():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "AI Command Center")
    if gated:
        return gated
    response = jsonify(command_router_service.get_menu_items(load_account_by_id(user["user_id"]) or user))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/command", methods=["POST"])
def api_command():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "AI Command Center")
    if gated:
        return gated
    payload = request.get_json(silent=True) or {}
    command_text = clean_html(payload.get("command") or payload.get("question") or "")
    result = command_router_service.handle_command(user["user_id"], command_text, channel="web")
    history_id = record_command_history(
        user["user_id"],
        result.get("action_key") or command_text.split(" ", 1)[0] or "command",
        command_text,
        result,
        source=payload.get("source") or "web",
        pro_required=bool(result.get("pro_required")),
        status="success" if result.get("ok", True) else "failed",
        error=result.get("error") or "",
    )
    log_product_event(user["user_id"], "website_command_used", {"command": command_text[:120], "action": result.get("action_key")})
    formatted = command_router_service.format_response_for_web(result)
    formatted["history_id"] = history_id
    response = jsonify(formatted)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/menu-action", methods=["POST"])
def api_menu_action():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "AI Command Center")
    if gated:
        return gated
    payload = request.get_json(silent=True) or {}
    action_key = clean_html(payload.get("action_key") or "")
    result = command_router_service.execute_menu_action(user["user_id"], action_key, channel="web", payload=payload)
    history_id = record_command_history(
        user["user_id"],
        action_key,
        json.dumps({key: value for key, value in payload.items() if key != "csrf_token"})[:2000],
        result,
        source=payload.get("source") or "web",
        pro_required=bool(result.get("pro_required")),
        status="success" if result.get("ok", True) else "failed",
        error=result.get("error") or "",
    )
    log_product_event(user["user_id"], "website_menu_action_used", {"action": action_key})
    formatted = command_router_service.format_response_for_web(result)
    formatted["history_id"] = history_id
    response = jsonify(formatted)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/command/history", methods=["GET"])
def api_command_history():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "AI Command Center")
    if gated:
        return gated
    response = jsonify({"ok": True, "history": command_history_payload(user["user_id"], request.args.get("limit", 50))})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/command/save", methods=["POST"])
def api_command_save():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "AI Command Center")
    if gated:
        return gated
    payload = request.get_json(silent=True) or {}
    saved_id = save_command_result(
        user["user_id"],
        payload.get("history_id") or 0,
        title=payload.get("title") or payload.get("command_name") or "Saved CoinPilotXAI result",
        summary=payload.get("summary") or payload.get("output_summary") or "",
        source=payload.get("source") or "web",
        metadata=payload.get("metadata") or {},
    )
    log_product_event(user["user_id"], "command_result_saved", {"saved_id": saved_id})
    response = jsonify({"ok": True, "saved_id": saved_id})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "Native AI Chat")
    if gated:
        return gated
    payload = request.get_json(silent=True) or {}
    message = clean_html(payload.get("message") or payload.get("question") or payload.get("command") or "")
    if not message:
        response = jsonify({"ok": False, "error": "Message required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 400
    conversation_id = get_or_create_ai_conversation(user["user_id"], payload.get("conversation_id"))
    save_ai_message(user["user_id"], conversation_id, "user", message)
    if message.strip().startswith("/"):
        result = command_router_service.handle_command(user["user_id"], message, channel="web_chat")
    else:
        routed = ai_router_service.route(user["user_id"], message, pro=platform_pro_access(user))
        result = {
            "ok": routed.get("ok", True),
            "action_key": "ai_chat",
            "title": "CoinPilotXAI",
            "summary": routed.get("response", ""),
            "source": routed.get("source", "coinpilotxai"),
            "confidence": routed.get("confidence", "Medium"),
            "latency_ms": routed.get("latency_ms"),
            "disclaimer": "Educational information only. Not financial, betting, investment, or legal advice.",
        }
    ai_text = result.get("summary") or result.get("message") or result.get("title") or ""
    if "temporarily unavailable" in ai_text.lower() or "source unavailable" in ai_text.lower():
        ai_text = "Live source is reconnecting right now. Here is what I can safely tell you…\n\n" + ai_text
    save_ai_message(user["user_id"], conversation_id, "assistant", ai_text, metadata={"action_key": result.get("action_key"), "source": result.get("source")})
    history_id = record_command_history(
        user["user_id"],
        result.get("action_key") or "ai_chat",
        message,
        result,
        source="web_chat",
        pro_required=bool(result.get("pro_required")),
        status="success" if result.get("ok", True) else "failed",
        error=result.get("error") or "",
    )
    log_product_event(user["user_id"], "website_ai_chat_used", {"action": result.get("action_key")})
    formatted = command_router_service.format_response_for_web(result)
    formatted["history_id"] = history_id
    formatted["conversation_id"] = conversation_id
    formatted["response"] = ai_text
    formatted["saved"] = bool(history_id)
    formatted.setdefault("source", "coinpilotxai")
    response = jsonify(formatted)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/ai/history", methods=["GET"])
def api_ai_history():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "Native AI Chat")
    if gated:
        return gated
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, feature, prompt, response, metadata, created_at
        FROM user_ai_interactions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user["user_id"],),
    )
    interactions = [dict(row) for row in cur.fetchall()]
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    cur.execute(
        """
        SELECT m.id, m.conversation_id, m.role, m.content, m.metadata, m.created_at
        FROM ai_messages m
        JOIN ai_conversations c ON c.id=m.conversation_id
        WHERE c.user_id=? AND m.created_at>=?
        ORDER BY m.id ASC
        LIMIT 200
        """,
        (user["user_id"], cutoff),
    )
    messages = [dict(row) for row in cur.fetchall()]
    conn.close()
    response = jsonify({"ok": True, "interactions": interactions, "messages": messages, "commands": command_history_payload(user["user_id"], 50)})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/ai/feedback", methods=["POST"])
def api_ai_feedback():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    payload = request.get_json(silent=True) or {}
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_feedback (user_id, interaction_id, rating, feedback, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            user["user_id"],
            int(payload.get("interaction_id") or 0),
            clean_html(payload.get("rating") or "")[:40],
            clean_html(payload.get("feedback") or "")[:1200],
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    log_product_event(user["user_id"], "ai_feedback_submitted", {"rating": payload.get("rating")})
    response = jsonify({"ok": True})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/ai/conversation/new", methods=["POST"])
def api_ai_conversation_new():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "Native AI Chat")
    if gated:
        return gated
    conversation_id = get_or_create_ai_conversation(user["user_id"])
    response = jsonify({"ok": True, "conversation_id": conversation_id})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/ai/history/clear", methods=["POST"])
def api_ai_history_clear():
    init_db()
    user = api_account_user()
    gated = api_pro_required(user, "Native AI Chat")
    if gated:
        return gated
    conn = db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("UPDATE ai_conversations SET status='cleared', updated_at=? WHERE user_id=? AND status='active'", (now, user["user_id"]))
    cur.execute("UPDATE user_ai_interactions SET metadata=COALESCE(metadata, '') || ' cleared_by_user' WHERE user_id=?", (user["user_id"],))
    conn.commit()
    conn.close()
    log_product_event(user["user_id"], "ai_history_cleared", {})
    response = jsonify({"ok": True})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/live/market", methods=["GET"])
def api_live_market():
    response = jsonify(market_data_service.live_market_board(category=request.args.get("category", "top_volume"), limit=12))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/live/btc", methods=["GET"])
def api_live_btc():
    payload = market_data_service.live_market_board(limit=20)
    payload["selected"] = market_data_service.get_symbol(request.args.get("symbol", "BTC"))
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/live/news", methods=["GET"])
def api_live_news():
    response = jsonify(news_service.get_crypto_news(limit=int(request.args.get("limit") or 12)))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/live/fear-greed", methods=["GET"])
def api_live_fear_greed():
    response = jsonify({"ok": True, "source": "unavailable", "message": "Fear & Greed source temporarily unavailable.", "updated_at": datetime.now().isoformat()})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/markets", methods=["GET"])
def markets_api():
    category = request.args.get("category", "top_volume")
    return jsonify(market_data_service.live_market_board(category=category))


@webhook_app.route("/api/sports-edge", methods=["GET"])
def sports_edge_api():
    game_id = request.args.get("game_id", "").strip()
    league = request.args.get("league", "all").strip().lower()
    payload = sports_data_service.live_sports_edge(league=league)
    if game_id:
        selected = next((game for game in payload.get("games", []) if game.get("id") == game_id or game.get("id") == game_id.replace("_", ":")), None)
        payload["selected_game"] = selected
        payload["analysis"] = sports_data_service.game_analysis(selected)
    return jsonify(payload)


def get_gemini_trade_url():
    return os.getenv("GEMINI_AFFILIATE_URL") or "https://www.gemini.com/"


@webhook_app.route("/sports-edge", methods=["GET"])
def sports_edge_landing_page():
    data = sports_data_service.live_sports_edge(league=request.args.get("league", "all"))
    source_note = data.get("warning") or "Sports intelligence source connected where configured."
    features = [
        "Live Sports Edge Signals", "AI Game Intelligence", "Market Psychology", "Odds Movement Tracker",
        "Risk Score", "Betting Discipline Coach", "Sports News Intelligence", "Crypto + Sports Market Connection",
        "Alerts and Notifications", "Training/Education Mode",
    ]
    feature_cards = "".join(f"<article class='card mini'><h3>{clean_html(item)}</h3><p>Educational intelligence with risk context, source status, and no guaranteed-outcome claims.</p></article>" for item in features)
    body = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Sports Edge AI Intelligence | CoinPilotXAI</title><meta name='description' content='Use CoinPilotXAI Sports Edge to track sports data, AI signals, risk psychology, and crypto market intelligence in one web and mobile command center.'><link rel='canonical' href='https://coinpilotx.app/sports-edge'><meta property='og:title' content='Sports Edge AI Intelligence | CoinPilotXAI'><meta property='og:description' content='Track sports data, AI signals, discipline coaching, and risk intelligence.'><style>body{{margin:0;background:#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif;overflow-x:hidden}}.wrap{{width:min(100% - 32px,1180px);margin:auto}}header{{position:sticky;top:0;background:rgba(5,11,20,.9);backdrop-filter:blur(16px);border-bottom:1px solid rgba(110,223,246,.18)}}nav{{min-height:68px;display:flex;justify-content:space-between;align-items:center;gap:12px}}a{{color:inherit;text-decoration:none}}.hero{{padding:72px 0 32px;display:grid;grid-template-columns:1.15fr .85fr;gap:20px;align-items:center}}h1{{font-size:clamp(40px,7vw,76px);line-height:.96;margin:0 0 16px}}p{{color:#9fb5c0}}.button{{display:inline-flex;min-height:46px;align-items:center;justify-content:center;border-radius:10px;padding:12px 16px;background:rgba(255,255,255,.06);border:1px solid rgba(110,223,246,.24);font-weight:900}}.primary{{background:linear-gradient(135deg,#36e58f,#6edff6);color:#06101b}}.gold{{background:linear-gradient(135deg,#ffd166,#b6ff4f);color:#1c1303}}.actions{{display:flex;gap:10px;flex-wrap:wrap}}.card{{border:1px solid rgba(110,223,246,.2);border-radius:18px;background:rgba(255,255,255,.05);padding:20px;box-shadow:0 24px 80px rgba(0,0,0,.24)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:18px 0 50px}}.mini:hover{{box-shadow:0 0 34px rgba(110,223,246,.18)}}.status{{color:#36e58f;font-weight:900}}@media(max-width:820px){{.hero{{grid-template-columns:1fr;padding-top:36px}}.actions .button{{width:100%}}}}</style></head><body><header><div class='wrap'><nav><a href='/'>CoinPilotXAI</a><a href='/dashboard'>Dashboard</a></nav></div></header><main class='wrap'><section class='hero'><div><p class='status'>Sports Edge Intelligence</p><h1>Sports Edge Intelligence Meets Crypto Market Discipline</h1><p>Track live sports data, market psychology, odds movement, AI insights, and risk signals from one CoinPilotXAI command center.</p><div class='actions'><a class='button primary' href='/app#sports-edge'>Open Sports Edge</a><a class='button gold' href='/sports-edge/trade' target='_blank' rel='noopener sponsored' data-analytics='gemini_trade_redirect_clicked'>Sign In to Trade</a></div><p><small>External trading platform. CoinPilotXAI may use affiliate links. Trading involves risk.</small></p></div><aside class='card'><h2>Live Status</h2><p>{clean_html(source_note)}</p><p>Educational intelligence only. Not financial advice. Not betting advice. No guaranteed outcomes. Follow local laws.</p></aside></section><section class='grid'>{feature_cards}</section></main></body></html>"""
    log_product_event(account_user_id(), "sports_edge_opened", {})
    return Response(body)


@webhook_app.route("/sports-edge/trade", methods=["GET"])
def sports_edge_trade_redirect():
    log_product_event(account_user_id(), "gemini_trade_redirect_clicked", {"target": "gemini"})
    return redirect(get_gemini_trade_url())


def quote_payload(symbol=None, category="top_volume", limit=50):
    board = live_market_service.get_crypto_market(category=category, limit=limit)
    if symbol:
        selected = market_data_service.get_symbol(symbol)
        if not selected:
            selected = {"symbol": symbol.upper(), "name": symbol.upper(), "price": None, "change_24h": None, "volume_24h": None, "market_cap": None}
        return {"ok": True, "asset": selected, "source": board.get("source") or "unavailable", "last_updated": board.get("updated_at"), "warning": board.get("warning") or ("Live data source temporarily unavailable." if not selected.get("price") else None)}
    return {"ok": True, **board}


def tactical_insight_payload(symbol):
    symbol = clean_html(symbol).upper()[:12] or "BTC"
    quote_data = quote_payload(symbol)
    asset = quote_data.get("asset") or {}
    price = float(asset.get("price") or 0)
    change = float(asset.get("change_24h") or 0)
    volume = float(asset.get("volume_24h") or 0)
    fear = live_market_service.get_fear_greed()
    news = news_service.get_crypto_news(limit=6)
    relevant_news = [
        item for item in news.get("items", [])
        if symbol in (item.get("affected_assets") or []) or symbol.lower() in (item.get("title", "") + " " + item.get("summary", "")).lower()
    ] or (news.get("items") or [])[:2]
    volatility = min(100, round(abs(change) * 12 + (8 if volume else 0), 1))
    support = round(price * (0.985 if abs(change) < 3 else 0.97), 2) if price else None
    resistance = round(price * (1.018 if change >= 0 else 1.012), 2) if price else None
    fear_value = int(fear.get("value") or 50) if str(fear.get("value") or "").isdigit() else 50
    fear_label = fear.get("classification") or ("Neutral" if fear_value >= 45 else "Fear")
    if change >= 4:
        mood = "Bullish momentum building"
        bias = "Momentum Watch"
        decision = "Watch for breakout confirmation"
    elif change <= -4:
        mood = "Fear spike"
        bias = "Defensive"
        decision = "Avoid emotional entries"
    elif volatility >= 45:
        mood = "Volatility expansion"
        bias = "Risk-Off"
        decision = "Wait for lower volatility"
    elif fear_value < 35:
        mood = "Cautious accumulation"
        bias = "Neutral / Watch"
        decision = "Monitor news catalysts and liquidity"
    else:
        mood = "Calm accumulation"
        bias = "Neutral"
        decision = "Watch for confirmation before aggressive positioning"
    top_news = relevant_news[0].get("title") if relevant_news else "No urgent catalyst is dominating cached crypto intelligence."
    if price:
        insight = (
            f"{symbol} is moving {change:.2f}% over 24h with liquidity still visible through volume near {volume:,.0f}. "
            f"Fear & Greed reads {fear_label}, so the tactical posture is {bias.lower()} rather than reactive. "
            f"Nearest working levels are support near {support:,.0f} and resistance near {resistance:,.0f}. "
            f"Recent catalyst watch: {top_news}"
        )
    else:
        insight = (
            f"{symbol} live pricing is reconnecting, so CoinPilotXAI is using cached market intelligence. "
            "The safest posture is observation until price, volume, and news context refresh."
        )
    confidence = 84 if price and news.get("items") else 68 if price else 56
    return {
        "ok": True,
        "symbol": symbol,
        "market_mood": mood,
        "ai_insight": insight,
        "tactical_bias": bias,
        "key_levels": {"support": support, "resistance": resistance},
        "next_decision": decision,
        "confidence": confidence,
        "volatility_score": volatility,
        "fear_greed": {"value": fear.get("value"), "classification": fear_label, "source": fear.get("source")},
        "news_source": news.get("source_badge") or news.get("source") or "Cached intelligence",
        "whale_activity": "Watch inflows/outflows" if symbol == "BTC" else "Whale layer available when provider is configured",
        "source": quote_data.get("source") or "cached intelligence",
        "last_updated": quote_data.get("last_updated") or datetime.utcnow().isoformat(timespec="seconds"),
    }


@webhook_app.route("/api/quote", methods=["GET"])
@webhook_app.route("/api/quote/crypto", methods=["GET"])
def api_quote_board():
    response = jsonify(quote_payload(category=request.args.get("category", "top_volume"), limit=int(request.args.get("limit", 50))))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/quote/crypto/<symbol>", methods=["GET"])
def api_quote_symbol(symbol):
    payload = quote_payload(clean_html(symbol).upper()[:12])
    return jsonify(payload), (200 if payload.get("ok") else 404)


@webhook_app.route("/api/quote/crypto/<symbol>/tactical-insight", methods=["GET"])
def api_quote_tactical_insight(symbol):
    return jsonify(tactical_insight_payload(symbol))


@webhook_app.route("/api/quote/crypto/<symbol>/chart", methods=["GET"])
def api_quote_chart(symbol):
    asset = market_data_service.get_symbol(clean_html(symbol).upper()[:12]) or {}
    price = float(asset.get("price") or 0)
    points = [{"t": i, "price": round(price * (1 + math.sin(i / 3) * 0.015), 2)} for i in range(24)] if price else []
    return jsonify({"ok": bool(points), "symbol": symbol.upper(), "timeframe": request.args.get("timeframe", "1D"), "points": points, "source": "market feed + educational chart fallback"})


@webhook_app.route("/api/quote/crypto/<symbol>/news", methods=["GET"])
def api_quote_news(symbol):
    payload = news_service.get_crypto_news(limit=12)
    sym = clean_html(symbol).upper()[:12]
    items = [
        item for item in payload.get("items", [])
        if sym in (item.get("affected_assets") or []) or sym.lower() in (item.get("title", "") + " " + item.get("summary", "")).lower()
    ]
    if not items:
        items = payload.get("items", [])[:5]
    return jsonify({
        "ok": True,
        "symbol": sym,
        "items": items,
        "mode": payload.get("mode"),
        "source": payload.get("source") or payload.get("mode", "cached intelligence"),
        "source_badge": payload.get("source_badge") or "Cached intelligence",
        "last_updated": payload.get("last_updated"),
    })


@webhook_app.route("/api/quote/crypto/<symbol>/signals", methods=["GET"])
def api_quote_signals(symbol):
    asset = market_data_service.get_symbol(clean_html(symbol).upper()[:12]) or {}
    change = float(asset.get("change_24h") or 0)
    signal = "Caution" if change < -3 else "Momentum watch" if change > 3 else "Neutral watch"
    return jsonify({"ok": True, "symbol": symbol.upper(), "signal": signal, "explanation": "Educational market intelligence only. Not financial advice.", "source": "CoinPilotXAI rules"})


@webhook_app.route("/quote", methods=["GET"])
def quote_center_page():
    return Response("""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Live Quote Market Center | CoinPilotXAI</title><meta name='description' content='Live crypto quote board with AI market intelligence, source status, and educational risk notes.'><link rel='canonical' href='https://coinpilotx.app/quote'><meta property='og:title' content='Live Quote Market Center | CoinPilotXAI'><meta property='og:description' content='Track live crypto quotes, AI market context, and educational risk intelligence.'><style>:root{color-scheme:dark;--cyan:#6edff6;--green:#36e58f;--gold:#ffd166;--line:rgba(110,223,246,.22);--muted:#9fb5c0}*{box-sizing:border-box}html,body{min-height:100%;overflow-x:hidden;overflow-y:auto}body{margin:0;background:#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif}.psych-market-bg,.intelligence-glow-bg{position:relative;isolation:isolate;overflow-x:hidden;background:radial-gradient(circle at 14% 4%,rgba(110,223,246,.2),transparent 28rem),radial-gradient(circle at 86% 18%,rgba(54,229,143,.12),transparent 23rem),linear-gradient(180deg,#050b14,#081421)}.soft-data-grid:before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(110,223,246,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(110,223,246,.055) 1px,transparent 1px);background-size:52px 52px;mask-image:radial-gradient(circle at 50% 15%,black,transparent 72%);pointer-events:none;z-index:-2}.intelligence-glow-bg:after{content:'';position:absolute;inset:auto -15% -28% -15%;height:260px;background:linear-gradient(90deg,transparent,rgba(110,223,246,.17),rgba(255,209,102,.1),transparent);filter:blur(24px);animation:intelligenceDrift 16s ease-in-out infinite alternate;z-index:-1}.wrap{width:min(100% - 28px,1180px);margin:auto;padding:38px 0 96px}.hero{padding:24px 0 20px}.kicker{color:var(--green);font-weight:950;letter-spacing:.08em;text-transform:uppercase;font-size:12px}h1{font-size:clamp(38px,7vw,72px);line-height:.98;margin:10px 0 14px}p{color:var(--muted)}.trust-gradient-panel,.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,rgba(110,223,246,.1),rgba(54,229,143,.045) 42%,rgba(255,209,102,.055)),rgba(13,22,39,.82);box-shadow:0 28px 90px rgba(0,0,0,.26),inset 0 1px 0 rgba(255,255,255,.06);padding:18px}.quote-cta-glow{box-shadow:0 0 0 1px rgba(110,223,246,.22),0 0 30px rgba(110,223,246,.18)}.filters{display:flex;gap:8px;overflow-x:auto;padding:10px 0 16px;position:sticky;top:0;background:rgba(5,11,20,.9);backdrop-filter:blur(14px);z-index:5}.filter{white-space:nowrap;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.055);color:#dff7ff;padding:9px 12px;font-weight:850;cursor:pointer}.filter.active{box-shadow:0 0 24px rgba(110,223,246,.34);color:#fff}.search{width:100%;min-height:46px;border:1px solid var(--line);border-radius:12px;background:rgba(255,255,255,.06);color:#fff;padding:12px;margin:8px 0 12px}.meta{color:var(--muted);font-size:13px;margin:8px 0 12px}.row{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:10px;padding:14px 12px;border-bottom:1px solid rgba(255,255,255,.08);cursor:pointer;border-radius:12px;transition:background .2s ease,transform .2s ease,box-shadow .2s ease}.row:hover{background:rgba(110,223,246,.08);transform:translateY(-1px);box-shadow:0 0 26px rgba(110,223,246,.12)}.green{color:var(--green)}.red{color:#ff6b7a}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 20px}.button{display:inline-flex;min-height:44px;align-items:center;justify-content:center;border-radius:10px;border:1px solid var(--line);padding:10px 14px;color:#06101b;background:linear-gradient(135deg,var(--green),var(--cyan));font-weight:900;text-decoration:none}@keyframes intelligenceDrift{from{transform:translateX(-4%) scale(1);opacity:.55}to{transform:translateX(4%) scale(1.06);opacity:.85}}@media(max-width:760px){.row{grid-template-columns:1fr 1fr}.actions .button{width:100%}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}</style></head><body class='psych-market-bg intelligence-glow-bg soft-data-grid'><main class='wrap'><section class='hero'><div class='kicker'>Live Quote Market Center</div><h1>Real-time crypto intelligence with calm market context.</h1><p>Track live assets, top movers, volume leaders, source status, and market filters. Educational market intelligence only. Not financial advice.</p><div class='actions'><a class='button quote-cta-glow' href='/signup?next=/watch'>Create Free Account</a><a class='button quote-cta-glow' href='/simulator'>Start Trading Simulator</a></div></section><section class='card trust-gradient-panel'><input class='search' id='search' placeholder='Search BTC, ETH, SUI, OP, TON...'><div class='filters'><button class='filter active' data-filter='top_volume'>Top Volume</button><button class='filter' data-filter='top_market_cap'>Top Market Cap</button><button class='filter' data-filter='gainers'>Gainers</button><button class='filter' data-filter='losers'>Losers</button><button class='filter' data-filter='stablecoins'>Stablecoins</button><button class='filter' data-filter='layer1'>Layer 1</button><button class='filter' data-filter='layer2'>Layer 2</button><button class='filter' data-filter='ai'>AI Coins</button><button class='filter' data-filter='meme'>Meme Coins</button></div><div class='meta' id='meta'>Loading live market board...</div><div id='rows'><div class='meta'>Loading...</div></div></section></main><script>const money=n=>Number(n||0).toLocaleString(undefined,{style:'currency',currency:'USD'});const pct=n=>(Number(n||0)>=0?'+':'')+Number(n||0).toFixed(2)+'%';let markets=[];let filter='top_volume';function categoryOk(m){const s=(m.symbol||'').toUpperCase(), name=(m.name||'').toLowerCase();if(filter==='stablecoins')return ['USDT','USDC','DAI'].includes(s)||name.includes('stable');if(filter==='layer1')return ['BTC','ETH','SOL','SUI','TON','ADA','AVAX','NEAR','ATOM','DOT'].includes(s);if(filter==='layer2')return ['OP','ARB','MATIC','POL','STRK','BASE'].includes(s)||name.includes('optimism')||name.includes('arbitrum');if(filter==='ai')return ['FET','TAO','RNDR','RENDER','AI'].includes(s)||name.includes('ai')||name.includes('artificial');if(filter==='meme')return ['DOGE','SHIB','PEPE','BONK','WIF'].includes(s)||name.includes('doge')||name.includes('meme');return true}function sorted(list){if(filter==='gainers')return list.sort((a,b)=>(b.change_24h??-999)-(a.change_24h??-999));if(filter==='losers')return list.sort((a,b)=>(a.change_24h??999)-(b.change_24h??999));if(filter==='top_market_cap')return list.sort((a,b)=>(b.market_cap||0)-(a.market_cap||0));return list.sort((a,b)=>(b.volume_24h||0)-(a.volume_24h||0))}function render(){const q=document.getElementById('search').value.trim().toLowerCase();const data=sorted(markets.filter(categoryOk).filter(m=>!q||(m.symbol||'').toLowerCase().includes(q)||(m.name||'').toLowerCase().includes(q))).slice(0,60);document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===filter));document.getElementById('rows').innerHTML=data.map((m,i)=>`<div class='row' onclick=\"location.href='/quote/crypto/${m.symbol}'\"><strong>${i+1}. ${m.name} (${m.symbol})</strong><span>${money(m.price)}</span><span class='${Number(m.change_24h||0)>=0?'green':'red'}'>${pct(m.change_24h)}</span><span>${Number(m.volume_24h||0).toLocaleString()}</span></div>`).join('')||'<div class=meta>Live source is reconnecting right now. Here is what I can safely show when cached data returns.</div>'}async function load(){try{const d=await fetch('/api/quote/crypto',{cache:'no-store'}).then(r=>r.json());markets=d.markets||[];document.getElementById('meta').textContent=`Source: ${d.source||'unavailable'} · Updated: ${d.updated_at||'reconnecting'}${d.warning?' · '+d.warning:''}`;render()}catch(e){document.getElementById('meta').textContent='Live source is reconnecting right now. Here is what I can safely tell you… market data is temporarily unavailable.'}}document.addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;render()});document.getElementById('search').addEventListener('input',render);load();setInterval(load,30000)</script></body></html>""")


@webhook_app.route("/quote/crypto/<symbol>", methods=["GET"])
def quote_symbol_page(symbol):
    symbol = clean_html(symbol).upper()[:12]
    return Response(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{symbol} Live Price, Market Data, AI Crypto Intelligence | CoinPilotXAI</title><meta name='description' content='{symbol} live price, market data, educational AI explanation, watchlist actions, and risk notes from CoinPilotXAI.'><link rel='canonical' href='https://coinpilotx.app/quote/crypto/{symbol}'><meta property='og:title' content='{symbol} Live Price | CoinPilotXAI'><meta property='og:description' content='Live quote intelligence, AI context, and educational risk notes for {symbol}.'><style>:root{{color-scheme:dark;--cyan:#6edff6;--green:#36e58f;--gold:#ffd166;--line:rgba(110,223,246,.22);--muted:#9fb5c0}}*{{box-sizing:border-box}}body{{margin:0;background:#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif;overflow-x:hidden}}.psych-market-bg,.intelligence-glow-bg{{position:relative;isolation:isolate;overflow-x:hidden;background:radial-gradient(circle at 14% 4%,rgba(110,223,246,.2),transparent 28rem),radial-gradient(circle at 86% 18%,rgba(54,229,143,.12),transparent 23rem),linear-gradient(180deg,#050b14,#081421)}}.soft-data-grid:before{{content:'';position:absolute;inset:0;background-image:linear-gradient(rgba(110,223,246,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(110,223,246,.055) 1px,transparent 1px);background-size:52px 52px;mask-image:radial-gradient(circle at 50% 15%,black,transparent 72%);pointer-events:none;z-index:-2}}.wrap{{width:min(100% - 28px,1050px);margin:auto;padding:30px 0 90px}}a{{color:inherit}}.trust-gradient-panel,.card{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,rgba(110,223,246,.1),rgba(54,229,143,.045) 42%,rgba(255,209,102,.055)),rgba(13,22,39,.82);box-shadow:0 28px 90px rgba(0,0,0,.26),inset 0 1px 0 rgba(255,255,255,.06);padding:18px;margin:14px 0}}.metric{{font-size:clamp(40px,8vw,68px);font-weight:950}}p{{color:var(--muted)}}.actions{{display:flex;gap:10px;flex-wrap:wrap}}.button{{min-height:44px;border:1px solid var(--line);border-radius:10px;background:linear-gradient(135deg,var(--green),var(--cyan));color:#06101b;font-weight:900;padding:10px 14px;text-decoration:none;cursor:pointer;box-shadow:0 0 30px rgba(110,223,246,.16)}}.tactical-card{{position:relative;overflow:hidden;background:radial-gradient(circle at 14% 0,rgba(110,223,246,.18),transparent 22rem),linear-gradient(135deg,rgba(14,28,48,.96),rgba(7,14,26,.92))}}.tactical-card:before{{content:'';position:absolute;inset:-1px;background:linear-gradient(120deg,transparent,rgba(110,223,246,.16),rgba(54,229,143,.08),transparent);animation:tacticalGlow 7s ease-in-out infinite;pointer-events:none}}.tactical-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;position:relative}}.live-dot,.confidence{{display:inline-flex;align-items:center;gap:7px;border:1px solid rgba(110,223,246,.22);border-radius:999px;padding:6px 9px;color:#c8ffe2;font-size:12px;font-weight:900;background:rgba(255,255,255,.055)}}.live-dot:before{{content:'';width:8px;height:8px;border-radius:999px;background:var(--green);box-shadow:0 0 14px rgba(54,229,143,.8)}}.tactical-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;position:relative}}.tactical-pill{{border:1px solid rgba(255,255,255,.08);border-radius:13px;background:rgba(255,255,255,.045);padding:12px}}.tactical-pill strong{{display:block;color:#fff;margin-top:2px}}.tactical-insight{{font-size:15px;line-height:1.55;margin:12px 0;position:relative}}canvas{{width:100%;height:260px;background:rgba(0,0,0,.18);border-radius:14px}}@keyframes tacticalGlow{{0%,100%{{opacity:.28;transform:translateX(-8%)}}50%{{opacity:.68;transform:translateX(8%)}}}}@media(max-width:720px){{.actions .button{{width:100%}}.tactical-head{{align-items:flex-start;flex-direction:column}}.tactical-grid{{grid-template-columns:1fr}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}</style></head><body class='psych-market-bg intelligence-glow-bg soft-data-grid'><main class='wrap'><a href='/quote'>← Quote Center</a><section class='card trust-gradient-panel'><h1>{symbol} Live Quote</h1><div class='metric' id='price'>Loading...</div><p id='meta'></p><div class='actions'><button class='button' id='watch'>Add to Watchlist</button><a class='button' href='/alerts'>Create Alert</a><a class='button' href='/simulator?asset={symbol}'>Simulate Trade</a><a class='button' href='/chat?asset={symbol}'>Ask AI</a><a class='button' href='/education'>Open Education</a></div></section><section class='card tactical-card' aria-live='polite'><div class='tactical-head'><div><p class='live-dot'>Live intelligence</p><h2>AI Tactical Insight</h2></div><span class='confidence' id='confidence'>Confidence --</span></div><div class='tactical-grid'><div class='tactical-pill'>Market Mood<strong id='mood'>Loading tactical context...</strong></div><div class='tactical-pill'>Tactical Bias<strong id='bias'>Neutral</strong></div><div class='tactical-pill'>Key Levels<strong id='levels'>Support -- · Resistance --</strong></div><div class='tactical-pill'>AI Next Decision<strong id='decision'>Watch for confirmation</strong></div></div><p class='tactical-insight' id='tacticalInsight'>CoinPilotXAI is loading live quote context, Fear & Greed, cached news, and volatility. If live sources reconnect slowly, cached tactical intelligence will appear here.</p><p id='tacticalMeta'></p></section><section class='card'><canvas id='chart'></canvas></section><section class='card'><h2>AI Market Explanation</h2><p id='explain'>Educational market intelligence only. Not financial advice.</p></section></main><script>const money=n=>Number(n||0).toLocaleString(undefined,{{style:'currency',currency:'USD'}});const compact=n=>Number(n||0).toLocaleString(undefined,{{maximumFractionDigits:0}});async function loadTactical(){{try{{const t=await fetch('/api/quote/crypto/{symbol}/tactical-insight',{{cache:'no-store'}}).then(r=>r.json());document.getElementById('mood').textContent=t.market_mood||'Cached tactical context';document.getElementById('bias').textContent=t.tactical_bias||'Neutral';const levels=t.key_levels||{{}};document.getElementById('levels').textContent='Support '+(levels.support?compact(levels.support):'--')+' · Resistance '+(levels.resistance?compact(levels.resistance):'--');document.getElementById('decision').textContent=t.next_decision||'Wait for confirmation before aggressive positioning';document.getElementById('tacticalInsight').textContent=t.ai_insight||'Cached tactical intelligence is active while live sources reconnect.';document.getElementById('confidence').textContent='Confidence '+(t.confidence||60)+'%';document.getElementById('tacticalMeta').textContent=`Fear & Greed: ${{(t.fear_greed||{{}}).classification||'reconnecting'}} · Volatility score: ${{t.volatility_score||0}} · News: ${{t.news_source||'cached intelligence'}}`;}}catch(e){{document.getElementById('tacticalInsight').textContent='Cached tactical intelligence is active while live sources reconnect. Avoid emotional entries and wait for confirmation.';}}}}async function load(){{try{{const d=await fetch('/api/quote/crypto/{symbol}',{{cache:'no-store'}}).then(r=>r.json());const a=d.asset||{{}};document.getElementById('price').textContent=money(a.price);document.getElementById('meta').textContent=`24h: ${{Number(a.change_24h||0).toFixed(2)}}% · Volume: ${{Number(a.volume_24h||0).toLocaleString()}} · Source: ${{d.source||'unavailable'}} · Last updated: ${{d.last_updated||'now'}}`;document.getElementById('explain').textContent=`${symbol} is being shown with source labels and risk context. Use the watchlist, alert, simulator, and AI tools for education, not guaranteed outcomes.`;const c=document.getElementById('chart'),ctx=c.getContext('2d');c.width=c.clientWidth*2;c.height=260*2;const chart=await fetch('/api/quote/crypto/{symbol}/chart').then(r=>r.json());ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle='#6edff6';ctx.lineWidth=4;ctx.beginPath();(chart.points||[]).forEach((p,i)=>{{const x=i/(chart.points.length-1||1)*c.width;const y=c.height-(p.price/(a.price*1.04||1))*c.height*.9;if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y)}});ctx.stroke()}}catch(e){{document.getElementById('meta').textContent='Live quote source temporarily reconnecting.'}}}}document.getElementById('watch').onclick=async()=>{{await fetch('/api/watch',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{watch_type:'coin',value:'{symbol}',channels:['in_app']}})}});document.getElementById('watch').textContent='Saved'}};load();loadTactical();setInterval(()=>{{load();loadTactical()}},30000)</script></body></html>""")


def prediction_samples():
    return predictions_service.get_active_crypto_predictions(limit=50)


@webhook_app.route("/api/predictions", methods=["GET"])
def api_predictions():
    status_payload = predictions_service.get_prediction_provider_status()
    provider = status_payload.get("provider") or os.getenv("PREDICTIONS_PROVIDER") or "polymarket"
    markets = predictions_service.get_active_crypto_predictions(limit=int(request.args.get("limit") or 50))
    category = (request.args.get("category") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    if category:
        markets = [market for market in markets if str(market.get("category", "")).lower() == category]
    if status:
        markets = [market for market in markets if str(market.get("status", "")).lower() == status]
    return jsonify({"ok": True, "provider": provider, "source_status": "educational sample fallback" if status_payload.get("fallback") else "live", "provider_status": status_payload, "markets": markets, "disclaimer": "Prediction intelligence is educational only. Event contracts and trading involve risk and may be restricted by location. CoinPilotXAI does not guarantee outcomes."})


@webhook_app.route("/api/predictions/<market_id>", methods=["GET"])
def api_prediction_detail(market_id):
    market = predictions_service.get_prediction_by_id(market_id)
    return jsonify({"ok": bool(market), "market": market}), (200 if market else 404)


@webhook_app.route("/predictions", methods=["GET"])
def predictions_page():
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next="/predictions"))
    return Response("""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Predictions Intelligence | CoinPilotXAI</title><style>body{margin:0;background:#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(100% - 28px,1120px);margin:auto;padding:34px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.card{border:1px solid rgba(110,223,246,.22);border-radius:16px;background:rgba(255,255,255,.05);padding:18px}.button{min-height:42px;border-radius:10px;border:1px solid rgba(110,223,246,.22);background:linear-gradient(135deg,#36e58f,#6edff6);color:#06101b;padding:10px 12px;font-weight:900;cursor:pointer}</style></head><body><main class='wrap'><h1>Live Predictions Intelligence</h1><p>Track event probabilities, crypto scenarios, sports outcomes, economic events, and market sentiment from one AI intelligence dashboard.</p><div id='cards' class='grid'></div></main><script>async function load(){const d=await fetch('/api/predictions',{cache:'no-store'}).then(r=>r.json());document.getElementById('cards').innerHTML=(d.markets||[]).map(m=>`<article class='card'><small>${m.category} · ${m.source}</small><h2>${m.title}</h2><p>Probability: <strong>${m.probability}%</strong></p><p>Volume: ${Number(m.volume||0).toLocaleString()} · Risk: ${m.risk_level}</p><button class='button' data-watch='${m.id}'>Track in Dashboard</button> <a class='button' href='/predictions/${m.id}'>View Details</a></article>`).join('')}document.addEventListener('click',async e=>{if(e.target.dataset.watch){await fetch('/api/predictions/watch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({market_id:e.target.dataset.watch})});e.target.textContent='Tracked'}});load()</script></body></html>""")


@webhook_app.route("/predictions/crypto", methods=["GET"])
def predictions_crypto_page():
    public_preview = not bool(account_user_id())
    action_url = "/signup?next=/predictions/crypto" if public_preview else "/predictions/crypto"
    external_url = clean_html(os.getenv("EXTERNAL_TRADE_URL") or os.getenv("PREDICTIONS_EXTERNAL_TRADE_URL") or get_gemini_trade_url())
    return Response(f"""<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Live Crypto Predictions Intelligence | CoinPilotXAI</title>
<meta name='description' content='Track active crypto prediction scenarios, market probabilities, AI explanations, and risk intelligence with CoinPilotXAI.'>
<link rel='canonical' href='https://coinpilotx.app/predictions/crypto'>
<style>
:root{{color-scheme:dark;--cyan:#6edff6;--green:#36e58f;--gold:#ffd166;--line:rgba(110,223,246,.22);--muted:#9fb5c0}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,rgba(110,223,246,.18),transparent 26rem),radial-gradient(circle at 88% 16%,rgba(54,229,143,.11),transparent 23rem),#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif;overflow-x:hidden}}body:before{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(110,223,246,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(110,223,246,.045) 1px,transparent 1px);background-size:54px 54px;mask-image:radial-gradient(circle at 50% 10%,black,transparent 72%);pointer-events:none}}.wrap{{position:relative;width:min(100% - 28px,1180px);margin:auto;padding:34px 0 90px}}a{{color:inherit;text-decoration:none}}.kicker{{color:var(--green);font-weight:950;letter-spacing:.08em;text-transform:uppercase;font-size:12px}}h1{{font-size:clamp(38px,7vw,72px);line-height:.98;margin:10px 0 14px}}p{{color:var(--muted)}}.filters{{display:flex;gap:8px;overflow:auto;padding:10px 0 18px;position:sticky;top:0;background:rgba(5,11,20,.86);backdrop-filter:blur(12px);z-index:2}}.pill{{white-space:nowrap;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.055);color:#dff7ff;padding:9px 12px;font-weight:850;cursor:pointer}}.pill.active{{box-shadow:0 0 24px rgba(110,223,246,.34);color:#fff}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px}}.card{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,rgba(110,223,246,.1),rgba(54,229,143,.045) 42%,rgba(255,209,102,.055)),rgba(13,22,39,.84);box-shadow:0 28px 90px rgba(0,0,0,.26),inset 0 1px 0 rgba(255,255,255,.06);padding:18px}}.status{{display:inline-flex;gap:8px;align-items:center;color:var(--green);font-weight:900}}.status:before{{content:'';width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 16px var(--green)}}.prob{{height:12px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden;margin:12px 0}}.prob span{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--green),var(--gold))}}.actions{{display:flex;gap:8px;flex-wrap:wrap}}.button{{min-height:42px;border-radius:10px;border:1px solid var(--line);background:linear-gradient(135deg,var(--green),var(--cyan));color:#06101b;padding:10px 12px;font-weight:900;cursor:pointer}}.button.secondary{{background:rgba(255,255,255,.055);color:#f2fbff}}.disclaimer{{margin-top:22px;border:1px solid rgba(255,209,102,.22);border-radius:14px;background:rgba(255,209,102,.06);padding:14px;color:#ffe7a6}}@media(max-width:720px){{.actions .button,.actions a{{width:100%;text-align:center}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style></head><body><main class='wrap'><section><div class='kicker'>Crypto Predictions Intelligence</div><h1>Active crypto prediction scenarios with AI risk context.</h1><p>Track probabilities, close dates, liquidity context, and market psychology. Actions save inside your CoinPilotXAI account.</p><div class='filters'><button class='pill active' data-filter='active'>Active</button><button class='pill' data-filter='trending'>Trending</button><button class='pill' data-filter='closing'>Closing Soon</button><button class='pill' data-filter='btc'>Bitcoin</button><button class='pill' data-filter='eth'>Ethereum</button><button class='pill' data-filter='alt'>Altcoins</button><button class='pill' data-filter='macro'>Macro Crypto</button><button class='pill' data-filter='volume'>High Volume</button></div></section><section id='cards' class='grid' aria-live='polite'></section><p class='disclaimer'>Prediction intelligence is educational only. Event contracts and trading involve risk and may be restricted by location. CoinPilotXAI does not guarantee outcomes.</p></main>
<script>
const actionUrl='{action_url}';
const externalUrl='{external_url}';
let markets=[];
let currentFilter='active';
function symbolFor(m){{const text=(m.title||'').toUpperCase();if(text.includes('BTC')||text.includes('BITCOIN'))return'BTC';if(text.includes('ETH')||text.includes('ETHEREUM'))return'ETH';return'CRYPTO'}}
function card(m){{const p=Number(m.probability||m.yes_probability||0);const symbol=symbolFor(m);return `<article class='card'><span class='status'>${{m.status||'active'}} · ${{m.source||'source pending'}}</span><h2>${{m.title}}</h2><p>${{m.category}} · Risk: ${{m.risk_level||'Unknown'}}</p><div class='prob'><span style='width:${{Math.max(0,Math.min(100,p))}}%'></span></div><p><strong>${{p}}%</strong> Yes probability · Volume ${{Number(m.volume||0).toLocaleString()}} · Liquidity ${{Number(m.liquidity||0).toLocaleString()}}</p><p>Closes: ${{(m.close_time||'').slice(0,10)}} · Resolves: ${{(m.resolve_time||'').slice(0,10)}}</p><div class='actions'><button class='button' data-action='watch' data-id='${{m.id}}'>Watch Prediction</button><button class='button secondary' data-action='alert' data-id='${{m.id}}'>Create Alert</button><button class='button secondary' data-action='ai' data-id='${{m.id}}' data-symbol='${{symbol}}'>Ask AI</button><button class='button secondary' data-action='simulate' data-id='${{m.id}}'>Simulate Outcome</button><a class='button secondary' href='${{externalUrl}}' target='_blank' rel='noopener sponsored'>Open External Trade</a></div></article>`}}
function filtered(){{const now=Date.now();return markets.filter(m=>{{const title=(m.title||'').toLowerCase();if(currentFilter==='btc')return title.includes('btc')||title.includes('bitcoin');if(currentFilter==='eth')return title.includes('eth')||title.includes('ethereum');if(currentFilter==='alt')return title.includes('altcoin');if(currentFilter==='volume')return Number(m.volume||0)>400000;if(currentFilter==='closing')return new Date(m.close_time||0).getTime()-now<1000*60*60*24*30;if(currentFilter==='macro')return title.includes('macro')||m.category==='Market Events';return true;}})}}
function render(){{document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===currentFilter));document.getElementById('cards').innerHTML=filtered().map(card).join('')||'<article class=card>Predictions source reconnecting. No matching scenarios are available right now.</article>'}}
async function load(){{const d=await fetch('/api/predictions?category=crypto&status=active',{{cache:'no-store'}}).then(r=>r.json());markets=d.markets||[];render()}}
document.addEventListener('click',async e=>{{const filter=e.target.closest('[data-filter]');if(filter){{currentFilter=filter.dataset.filter;render();return}}const btn=e.target.closest('button[data-action]');if(!btn)return;if(actionUrl.startsWith('/signup')){{location.href=actionUrl;return}}if(btn.dataset.action==='ai'){{location.href='/chat?context=prediction&symbol='+encodeURIComponent(btn.dataset.symbol||'CRYPTO')+'&id='+encodeURIComponent(btn.dataset.id);return}}if(btn.dataset.action==='simulate'){{location.href='/simulator?prediction='+encodeURIComponent(btn.dataset.id);return}}const endpoint=btn.dataset.action==='alert'?'/api/predictions/alert':'/api/predictions/watch';await fetch(endpoint,{{method:'POST',headers:{{'Content-Type':'application/json'}},credentials:'same-origin',body:JSON.stringify({{market_id:btn.dataset.id}})}});btn.textContent=btn.dataset.action==='watch'?'Watching ✓':'Prediction alert activated'}});
load();
</script></body></html>""")
    return Response(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Live Crypto Predictions Intelligence | CoinPilotXAI</title><meta name='description' content='Track active crypto prediction scenarios, market probabilities, AI explanations, and risk intelligence with CoinPilotXAI.'><link rel='canonical' href='https://coinpilotx.app/predictions/crypto'><meta property='og:title' content='Live Crypto Predictions Intelligence | CoinPilotXAI'><meta property='og:description' content='Crypto prediction scenarios with AI explanations, probability context, and educational risk intelligence.'><script type='application/ld+json'>{{"@context":"https://schema.org","@type":"WebPage","name":"Live Crypto Predictions Intelligence","description":"Educational crypto prediction scenarios, probability tracking, and AI risk intelligence from CoinPilotXAI."}}</script><style>:root{{color-scheme:dark;--cyan:#6edff6;--green:#36e58f;--gold:#ffd166;--line:rgba(110,223,246,.22);--muted:#9fb5c0}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,rgba(110,223,246,.18),transparent 26rem),radial-gradient(circle at 88% 16%,rgba(54,229,143,.11),transparent 23rem),#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif;overflow-x:hidden}}body:before{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(110,223,246,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(110,223,246,.045) 1px,transparent 1px);background-size:54px 54px;mask-image:radial-gradient(circle at 50% 10%,black,transparent 72%);pointer-events:none}}.wrap{{position:relative;width:min(100% - 28px,1180px);margin:auto;padding:34px 0 90px}}a{{color:inherit;text-decoration:none}}.hero{{padding:34px 0 20px}}.kicker{{color:var(--green);font-weight:950;letter-spacing:.08em;text-transform:uppercase;font-size:12px}}h1{{font-size:clamp(38px,7vw,72px);line-height:.98;margin:10px 0 14px}}p{{color:var(--muted)}}.filters{{display:flex;gap:8px;overflow:auto;padding:10px 0 18px}}.pill{{white-space:nowrap;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.055);color:#dff7ff;padding:9px 12px;font-weight:850}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.card{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,rgba(110,223,246,.1),rgba(54,229,143,.045) 42%,rgba(255,209,102,.055)),rgba(13,22,39,.84);box-shadow:0 28px 90px rgba(0,0,0,.26),inset 0 1px 0 rgba(255,255,255,.06);padding:18px}}.status{{display:inline-flex;gap:8px;align-items:center;color:var(--green);font-weight:900}}.status:before{{content:'';width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 16px var(--green)}}.prob{{height:12px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden;margin:12px 0}}.prob span{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--green),var(--gold))}}.actions{{display:flex;gap:8px;flex-wrap:wrap}}.button{{min-height:42px;border-radius:10px;border:1px solid var(--line);background:linear-gradient(135deg,var(--green),var(--cyan));color:#06101b;padding:10px 12px;font-weight:900;cursor:pointer}}.button.secondary{{background:rgba(255,255,255,.055);color:#f2fbff}}.disclaimer{{margin-top:22px;border:1px solid rgba(255,209,102,.22);border-radius:14px;background:rgba(255,209,102,.06);padding:14px;color:#ffe7a6}}@media(max-width:720px){{.actions .button{{width:100%}}.tactical-head{{align-items:flex-start;flex-direction:column}}.tactical-grid{{grid-template-columns:1fr}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}</style></head><body><main class='wrap'><section class='hero'><div class='kicker'>Crypto Predictions Intelligence</div><h1>Active crypto prediction scenarios with AI risk context.</h1><p>Track scenario probabilities, close dates, liquidity context, and market psychology without guaranteed-outcome claims. Live provider data appears when legally configured; sample scenarios are clearly labeled.</p><div class='filters'><span class='pill'>Active</span><span class='pill'>Trending</span><span class='pill'>Closing Soon</span><span class='pill'>Bitcoin</span><span class='pill'>Ethereum</span><span class='pill'>Altcoins</span><span class='pill'>Macro Crypto</span><span class='pill'>High Volume</span></div></section><section id='cards' class='grid' aria-live='polite'></section><p class='disclaimer'>Prediction intelligence is educational only. Event contracts and trading involve risk and may be restricted by location. CoinPilotXAI does not guarantee outcomes.</p></main><script>const actionUrl='{action_url}';const externalUrl='{clean_html(os.getenv("PREDICTIONS_EXTERNAL_TRADE_URL") or get_gemini_trade_url())}';function card(m){{const p=Number(m.probability||m.yes_probability||0);return `<article class='card'><span class='status'>${{m.status||'active'}} · ${{m.source||'source pending'}}</span><h2>${{m.title}}</h2><p>${{m.category}} · Risk: ${{m.risk_level||'Unknown'}}</p><div class='prob'><span style='width:${{Math.max(0,Math.min(100,p))}}%'></span></div><p><strong>${{p}}%</strong> Yes probability · Volume ${{Number(m.volume||0).toLocaleString()}} · Liquidity ${{Number(m.liquidity||0).toLocaleString()}}</p><p>Closes: ${{(m.close_time||'').slice(0,10)}} · Resolves: ${{(m.resolve_time||'').slice(0,10)}}</p><div class='actions'><button class='button' data-action='watch' data-id='${{m.id}}'>Watch Prediction</button><button class='button secondary' data-action='alert' data-id='${{m.id}}'>Create Alert</button><button class='button secondary' data-action='ai' data-id='${{m.id}}'>Ask AI</button><button class='button secondary' data-action='simulate' data-id='${{m.id}}'>Simulate Outcome</button><a class='button secondary' href='${{externalUrl}}' target='_blank' rel='noopener sponsored'>Open External Trade</a></div></article>`}}async function load(){{const d=await fetch('/api/predictions?category=crypto&status=active',{{cache:'no-store'}}).then(r=>r.json());document.getElementById('cards').innerHTML=(d.markets||[]).map(card).join('')||'<article class=card>Predictions source reconnecting. No live crypto scenarios are available right now.</article>'}}document.addEventListener('click',async e=>{{const btn=e.target.closest('button[data-action]');if(!btn)return;if(actionUrl.startsWith('/signup')){{location.href=actionUrl;return}}const endpoint=btn.dataset.action==='alert'?'/api/predictions/alert':btn.dataset.action==='simulate'?'/api/predictions/simulate':'/api/predictions/watch';await fetch(endpoint,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{market_id:btn.dataset.id}})}});btn.textContent='Saved'}});load()</script></body></html>""")


@webhook_app.route("/predictions/<market_id>", methods=["GET"])
def prediction_detail_page(market_id):
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next=f"/predictions/{market_id}"))
    market = next((m for m in prediction_samples() if m["id"] == market_id), None)
    if not market:
        return Response("Prediction market not found", status=404)
    return simple_public_page(
        f"predictions/{market_id}",
        f"{market['title']} | CoinPilotXAI Predictions Intelligence",
        market["title"],
        "Educational prediction intelligence for scenario planning, probability awareness, and risk discipline.",
        f"Current educational probability: {market['probability']}%. Source: {market['source']}. CoinPilotXAI does not guarantee outcomes.",
        ["Key drivers", "Uncertainty", "Risk controls", "Alert setup"],
        [{"title": "AI explanation", "body": "Ask AI to explain the drivers, uncertainty, and risk level before taking any external action."}],
        ["/predictions", "/education", "/sports-edge", "/quote"],
    )


@webhook_app.route("/api/predictions/watch", methods=["POST"])
def api_prediction_watch():
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    market_id = clean_html(payload.get("market_id") or "")[:120]
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO prediction_watches (user_id, market_id, threshold, created_at) VALUES (?, ?, ?, ?)", (user["user_id"], market_id, payload.get("threshold") or 50, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    notification_service.send_user_alert(user["user_id"], "market_alerts", "Prediction watch saved", f"CoinPilotXAI is watching {market_id}.", {"market_id": market_id}, channels=["in_app"])
    return jsonify({"ok": True, "market_id": market_id})


@webhook_app.route("/api/predictions/alert", methods=["POST"])
def api_prediction_alert():
    return api_prediction_watch()


@webhook_app.route("/api/predictions/simulate", methods=["POST"])
def api_prediction_simulate():
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    return jsonify({"ok": True, "simulation": "Educational scenario simulation saved. No real trade was placed.", "disclaimer": "No guaranteed outcomes."})


@webhook_app.route("/admin/predictions", methods=["GET"])
def admin_predictions_page():
    admin, denied = require_admin_page("analytics.view")
    if denied:
        return denied
    status = predictions_service.get_prediction_provider_status()
    body = (
        f"<h1>Predictions Intelligence</h1><div class='grid'>"
        f"<div class='card'><div class='metric'>{status.get('active_crypto_markets', len(prediction_samples()))}</div><p>active crypto markets fetched</p></div>"
        f"<div class='card'><div class='metric'>{clean_html(status.get('provider') or 'polymarket')}</div><p>provider selected</p></div>"
        f"<div class='card'><div class='metric'>{'yes' if status.get('reachable') else 'no'}</div><p>provider reachable</p></div>"
        f"<div class='card'><div class='metric'>{'yes' if status.get('fallback') else 'no'}</div><p>fallback mode</p></div>"
        f"</div><p class='muted'>Last successful fetch: {clean_html(status.get('last_successful_fetch') or 'not yet')} · Cache age: {status.get('cache_age_seconds', 0)}s · Error: {clean_html(status.get('error') or 'none')}</p>"
        "<p class='muted'>No fake live data is shown as live. Educational samples are labeled when provider data is unavailable.</p>"
    )
    return admin_page_html("Predictions Intelligence", body, admin)


@webhook_app.route("/admin/email-health", methods=["GET"])
def admin_email_health_page():
    admin, denied = require_admin_page("emails.view")
    if denied:
        return denied
    brevo_ready = bool(os.getenv("BREVO_API_KEY"))
    smtp_ready = bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT created_at, recipient_email, email_type, subject, status FROM email_logs ORDER BY created_at DESC LIMIT 1")
    last_email = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS count FROM email_logs WHERE lower(status) IN ('failed','error')")
    failed_row = cur.fetchone()
    failed_count = failed_row["count"] if failed_row else 0
    cur.execute("SELECT COUNT(*) AS count FROM failed_email_queue")
    queued_row = cur.fetchone()
    queued_count = queued_row["count"] if queued_row else 0
    cur.execute("SELECT created_at, email, template, status FROM payment_email_logs ORDER BY created_at DESC LIMIT 1")
    last_payment_email = cur.fetchone()
    conn.close()
    body = f"""
    <h1>Email Health</h1>
    <div class="grid">
      <div class="card"><div class="metric">{'Yes' if brevo_ready else 'No'}</div><p>Brevo configured</p></div>
      <div class="card"><div class="metric">{'Yes' if smtp_ready else 'No'}</div><p>SMTP configured</p></div>
      <div class="card"><div class="metric">{failed_count}</div><p>failed emails</p></div>
      <div class="card"><div class="metric">{queued_count}</div><p>queued retries</p></div>
    </div>
    <div class="card"><h2>Latest Email</h2><pre>{clean_html(json.dumps(last_email or {}, indent=2))}</pre></div>
    <div class="card"><h2>Latest Payment Email</h2><pre>{clean_html(json.dumps(last_payment_email or {}, indent=2))}</pre></div>
    <p class="muted">Secrets are never shown here. Sender values are checked from MAIL_FROM_ADDRESS/BREVO_SENDER_EMAIL and MAIL_FROM_NAME/BREVO_SENDER_NAME.</p>
    """
    return admin_page_html("Email Health", body, admin)


@webhook_app.route("/admin/system/health", methods=["GET"])
@webhook_app.route("/admin/system-health", methods=["GET"])
def admin_system_health_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    db_health = db_service.health_check()
    checks = [
        ("Database", db_health.get("connected")),
        ("Stripe secret", bool(os.getenv("STRIPE_SECRET_KEY"))),
        ("Stripe webhook", bool(os.getenv("STRIPE_WEBHOOK_SECRET"))),
        ("Brevo", bool(os.getenv("BREVO_API_KEY"))),
        ("SMTP", bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))),
        ("OpenAI", bool(os.getenv("OPENAI_API_KEY"))),
        ("Predictions provider", bool(os.getenv("PREDICTIONS_PROVIDER"))),
        ("SMS/Twilio", bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN") and os.getenv("TWILIO_FROM_NUMBER"))),
        ("PWA push/VAPID", bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY"))),
        ("Telegram optional", bool(os.getenv("BOT_TOKEN"))),
    ]
    cards = "".join(f"<div class='card'><div class='metric'>{'OK' if ok else 'Missing'}</div><p>{clean_html(name)}</p></div>" for name, ok in checks)
    provider_health = live_market_service.health()
    notification_health = notification_orchestrator_service.health()
    body = f"<h1>System Health</h1><div class='grid'>{cards}</div><div class='card'><h2>Database</h2><pre>{clean_html(json.dumps(db_health, indent=2))}</pre></div><div class='card'><h2>Provider Health</h2><pre>{clean_html(json.dumps(provider_health, indent=2))}</pre></div><div class='card'><h2>Notification Health</h2><pre>{clean_html(json.dumps(notification_health, indent=2))}</pre></div>"
    return admin_page_html("System Health", body, admin)


@webhook_app.route("/admin/provider-health", methods=["GET"])
@webhook_app.route("/admin/live-data", methods=["GET"])
def admin_provider_health_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    health = live_market_service.health()
    cards = "".join(
        f"<div class='card'><h2>{clean_html(name)}</h2><pre>{clean_html(json.dumps(payload, indent=2))}</pre></div>"
        for name, payload in health.get("providers", {}).items()
    )
    return admin_page_html("Provider Health", f"<h1>Provider Health</h1><p class='muted'>Live data failover, stale detection, cache status, and configured provider readiness.</p><div class='grid'>{cards}</div>", admin)


@webhook_app.route("/admin/notification-health", methods=["GET"])
def admin_notification_health_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    health = notification_orchestrator_service.health()
    return admin_page_html("Notification Health", f"<h1>Notification Health</h1><div class='card'><pre>{clean_html(json.dumps(health, indent=2))}</pre></div>", admin)


@webhook_app.route("/admin/system/errors", methods=["GET"])
def admin_system_errors_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT created_at, event_name, metadata FROM analytics_events WHERE lower(event_name) LIKE '%error%' ORDER BY created_at DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()
    items = "".join(f"<tr><td>{clean_html(row.get('created_at'))}</td><td>{clean_html(row.get('event_name'))}</td><td><code>{clean_html(row.get('metadata'))}</code></td></tr>" for row in rows)
    body = f"<h1>System Errors</h1><div class='card'><table><thead><tr><th>Time</th><th>Event</th><th>Details</th></tr></thead><tbody>{items or '<tr><td colspan=3>No recent tracked errors.</td></tr>'}</tbody></table></div>"
    return admin_page_html("System Errors", body, admin)


@webhook_app.route("/api/platform-status", methods=["GET"])
def platform_status_api():
    return jsonify(platform_status())


@webhook_app.route("/api/ai-assistant", methods=["POST"])
def website_ai_assistant_api():
    payload = request.get_json(silent=True) or {}
    question = clean_html(payload.get("question", "")).strip()
    if not question:
        return jsonify({"ok": False, "response": "Ask a crypto, wallet, scam, market, or sports question first."}), 400
    account = load_account_by_id(account_user_id())
    user_id = account.get("user_id") if account else 0
    allowed, limit_message = consume_ai_usage(user_id, "website_ai_assistant") if user_id else (True, "")
    if not allowed:
        return jsonify({"ok": False, "response": limit_message}), 429
    response = intelligence_service.assistant_response(user_id, question, pro=pro_access_service.has_pro_access(account or {}))
    user_context_service.log_interaction(user_id, "ai_assistant_used", question, response, "website")
    return jsonify({
        "ok": True,
        "powered_by": "CoinPilotXAI Inc.",
        "response": response,
        "safety": "Informational only — not financial advice.",
    })


@webhook_app.route("/api/scam-shield", methods=["POST"])
def website_scam_shield_api():
    payload = request.get_json(silent=True) or {}
    text = clean_html(payload.get("text", "")).strip()
    if not text:
        return jsonify({"ok": False, "response": "Paste a suspicious message, link, or crypto pitch first."}), 400
    return scam_shield_analyze_api()


@webhook_app.route("/api/scam-shield/analyze", methods=["POST"])
def scam_shield_analyze_api():
    payload = request.get_json(silent=True) or {}
    text = clean_html(payload.get("text") or payload.get("message") or payload.get("url") or payload.get("address") or payload.get("token") or "").strip()
    if not text:
        return jsonify({"ok": False, "message": "Paste suspicious text, a URL, wallet address, token name, or contract address first."}), 400
    user = load_account_by_id(account_user_id())
    user_id = user.get("user_id") if user else 0
    result = scam_shield_service.analyze_text(text)
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO scam_scans
            (user_id, input_text, risk_level, risk_score, threats_json, red_flags_json, safe_actions_json, confidence, source_status, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                text[:4000],
                result.get("risk_level") or "",
                int(result.get("risk_score") or 0),
                json.dumps(result.get("threats_detected") or []),
                json.dumps(result.get("red_flags") or []),
                json.dumps(result.get("safe_actions") or []),
                float(result.get("confidence") or 0),
                result.get("source_status") or "",
                json.dumps(result)[:8000],
                datetime.now().isoformat(),
            ),
        )
        scan_id = cur.lastrowid
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.warning("scam scan save failed safely: %s", exc)
        scan_id = None
    user_context_service.log_interaction(user_id, "scam_shield_used", text, result.get("response", ""), "website")
    record_command_history(user_id, "scam_shield", text, {"summary": result.get("response", ""), "action_key": "scam_shield"}, source="web", pro_required=False, status="success")
    log_product_event(user_id, "scam_shield_used", {"risk_level": result.get("risk_level"), "scan_id": scan_id})
    return jsonify({**result, "scan_id": scan_id})


@webhook_app.route("/api/wallet-intel", methods=["GET"])
def website_wallet_intel_api():
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"ok": False, "response": "Enter a public wallet address or TXID. Never enter private keys or seed phrases."}), 400
    account = load_account_by_id(account_user_id())
    user_id = account.get("user_id") if account else 0
    result = wallet_intel_service.analyze_public_identifier(address)
    user_context_service.log_interaction(user_id, "wallet_intel_used", address, result.get("response", ""), "website")
    return jsonify(result)


@webhook_app.route("/api/day-signal", methods=["POST"])
def website_day_signal_api():
    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers", {})
    account = load_account_by_id(account_user_id())
    user_id = account.get("user_id") if account else 0
    result = day_signal_service.generate(answers, pro=has_pro_access(account))
    if not result["ok"]:
        return jsonify(result), 400
    day_signal_service.save_result(user_id, result, answers)
    user_context_service.log_interaction(user_id, "day_signal_used", json.dumps(answers), result.get("response", ""), "website")
    return jsonify(result)


@webhook_app.route("/day-signal", methods=["GET"])
def day_signal_page():
    init_db()
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next="/day-signal"))
    pro = has_pro_access(user)
    questions = [
        {
            "key": "feeling",
            "label": "How do you feel today?",
            "type": "select",
            "options": [
                ("", "Choose your current state"),
                ("calm", "Calm"),
                ("focused", "Focused"),
                ("tired", "Tired"),
                ("anxious", "Anxious"),
                ("excited", "Excited"),
                ("angry", "Angry"),
                ("distracted", "Distracted"),
                ("overconfident", "Overconfident"),
            ],
        },
        {"key": "fomo", "label": "Are you feeling FOMO right now?", "placeholder": "yes/no"},
        {
            "key": "prepared",
            "label": "Do you already have a trading plan today?",
            "type": "select",
            "options": [
                ("", "Choose readiness"),
                ("very_prepared", "Yes, clear and written"),
                ("somewhat_prepared", "Mostly, but still refining it"),
                ("not_prepared", "Not yet"),
                ("guessing", "I am mostly guessing"),
            ],
        },
        {"key": "focus", "label": "Can you focus for the next 30-60 minutes without interruption?", "placeholder": "yes/no"},
    ]
    if pro:
        questions.extend([
            {"key": "recover_loss", "label": "Are you trying to recover from a recent loss?", "placeholder": "yes/no"},
            {"key": "sleep", "label": "Did you sleep enough before making market decisions today?", "placeholder": "yes/no"},
            {"key": "market", "label": "Is the market trending, ranging, volatile, or unclear?", "placeholder": "short answer"},
            {
                "key": "walkaway",
                "label": "Are you willing to miss a trade rather than force one?",
                "type": "select",
                "options": [("", "Choose discipline"), ("yes", "Yes"), ("maybe", "Maybe"), ("no", "No")],
            },
            {"key": "no_trade", "label": "What would make today a no-trade day?", "placeholder": "short answer"},
        ])
    else:
        questions.extend([
            {
                "key": "opportunity",
                "label": "What kind of opportunity are you thinking about?",
                "type": "select",
                "options": [("", "Choose one"), ("crypto", "Crypto/Trading"), ("sports", "Sports Edge"), ("business", "Business/Money"), ("personal", "Personal decision")],
            },
            {
                "key": "walkaway",
                "label": "Are you willing to walk away if the signal looks risky?",
                "type": "select",
                "options": [("", "Choose one"), ("yes", "Yes"), ("maybe", "Maybe"), ("no", "No")],
            },
        ])
    if pro:
        questions.insert(4, {"key": "opportunity", "type": "hidden", "value": "crypto"})
    def render_day_signal_field(item):
        key = clean_html(item["key"])
        if item.get("type") == "hidden":
            return f"<input type='hidden' name='{key}' value='{clean_html(item.get('value', ''))}'>"
        label = clean_html(item["label"])
        error = f"<small class='field-error' data-error-for='{key}'></small>"
        if item.get("type") == "select":
            options = "".join(f"<option value='{clean_html(value)}'>{clean_html(text)}</option>" for value, text in item.get("options", []))
            return f"<label data-question='{key}'>{label}<select name='{key}' required>{options}</select>{error}</label>"
        return f"<label data-question='{key}'>{label}<input name='{key}' placeholder='{clean_html(item.get('placeholder', 'short answer'))}' required>{error}</label>"
    fields = "".join(render_day_signal_field(item) for item in questions)
    return Response(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Day Signal | CoinPilotXAI</title><style>:root{{color-scheme:dark;--cyan:#6edff6;--green:#36e58f;--gold:#ffd166;--line:rgba(110,223,246,.22);--muted:#9fb5c0}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,rgba(110,223,246,.18),transparent 28rem),linear-gradient(145deg,#050b14,#081421);color:#f2fbff;font-family:Inter,system-ui,sans-serif;overflow-x:hidden}}.wrap{{width:min(100% - 28px,900px);margin:auto;padding:calc(24px + env(safe-area-inset-top)) 0 90px}}.card{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(180deg,rgba(17,29,50,.92),rgba(13,22,39,.88));box-shadow:0 24px 80px rgba(0,0,0,.28);padding:18px}}label{{display:block;margin:12px 0;color:var(--muted);font-weight:850;scroll-margin-top:24px}}input,select{{width:100%;min-height:46px;border:1px solid var(--line);border-radius:10px;background:#081323;color:#fff;padding:10px;margin-top:6px}}label.is-missing input,label.is-missing select{{border-color:#ff6b7a;box-shadow:0 0 0 3px rgba(255,107,122,.16)}}.field-error{{display:block;min-height:18px;color:#ffb7bf;margin-top:5px}}button,.button{{min-height:44px;border-radius:10px;border:1px solid var(--line);background:linear-gradient(135deg,var(--green),var(--cyan));color:#06101b;padding:10px 14px;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex}}pre{{white-space:pre-wrap;color:#dff7ff}}</style></head><body><main class='wrap'><a class='button' href='/dashboard'>Dashboard</a><section class='card'><h1>{'Pro Psychological Day Signal' if pro else 'Basic Day Signal'}</h1><p>Educational readiness check only. Not financial, trading, betting, or investment advice.</p><form id='form' novalidate>{fields}<button>Generate Day Signal</button></form><pre id='result'></pre></section></main><script>const form=document.getElementById('form');function clearErrors(){{document.querySelectorAll('.is-missing').forEach(n=>n.classList.remove('is-missing'));document.querySelectorAll('[data-error-for]').forEach(n=>n.textContent='')}}function markMissing(name,message){{const q=document.querySelector(`[data-question="${{name}}"]`);if(!q)return false;q.classList.add('is-missing');const err=q.querySelector('[data-error-for]');if(err)err.textContent=message||'Please answer this question.';q.scrollIntoView({{behavior:'smooth',block:'center'}});return true}}form.addEventListener('submit',async e=>{{e.preventDefault();clearErrors();const answers=Object.fromEntries(new FormData(e.target).entries());for(const element of form.querySelectorAll('[required]')){{if(!String(element.value||'').trim()){{markMissing(element.name,'Please answer this question.');return;}}}}document.getElementById('result').textContent='CoinPilotXAI is thinking...';const r=await fetch('/api/day-signal',{{method:'POST',headers:{{'Content-Type':'application/json'}},credentials:'same-origin',body:JSON.stringify({{answers}})}});const d=await r.json();if(!r.ok||d.ok===false){{const text=d.response||d.message||'Day Signal unavailable right now.';document.getElementById('result').textContent=text;const match=text.match(/Please answer: (.+)$/);if(match){{const label=[...document.querySelectorAll('[data-question]')].find(node=>node.firstChild&&node.firstChild.textContent.trim()===match[1]);if(label)markMissing(label.dataset.question,'Required for Day Signal.')}}return}}document.getElementById('result').textContent=d.response||d.message||'Day Signal unavailable right now.'}})</script></body></html>""")


def api_account_user():
    user = require_account()
    if not user:
        return None
    return user


@webhook_app.route("/api/dashboard", methods=["GET"])
def dashboard_api():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    repair_trialing_users_with_successful_payments()
    response = jsonify(portfolio_service.get_user_dashboard_data(user["user_id"]))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def dashboard_brief_payload(user_id):
    market = market_data_service.live_market_board(limit=8)
    markets = market.get("markets") or []
    btc = next((item for item in markets if (item.get("symbol") or "").upper() == "BTC"), None) or (markets[0] if markets else {})
    eth = next((item for item in markets if (item.get("symbol") or "").upper() == "ETH"), None) or {}
    return {
        "ok": True,
        "title": "Today's Market Pulse",
        "market_pulse": f"BTC {btc.get('price', 'unavailable')} · ETH {eth.get('price', 'unavailable')}",
        "risk_alerts": "Review position size, avoid urgency, and verify links before signing wallet approvals.",
        "top_watch": [item.get("symbol") for item in markets[:5] if item.get("symbol")],
        "scam_warning": "Never enter a seed phrase, private key, wallet password, exchange password, or recovery phrase into any CoinPilotXAI tool.",
        "ai_insight": "Based on available public market data, start with risk control and alerts before making any high-conviction move.",
        "source": market.get("source") or "CoinPilotXAI market service",
        "updated_at": datetime.now().isoformat(),
    }


@webhook_app.route("/api/dashboard/brief", methods=["GET"])
def api_dashboard_brief():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    payload = dashboard_brief_payload(user["user_id"])
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/dashboard/risk-score", methods=["GET"])
def api_dashboard_risk_score():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    portfolio = portfolio_service.calculate_user_portfolio(user["user_id"])
    alert_count = len(portfolio_service.get_alerts(user["user_id"]) or [])
    watch_count = len(portfolio_service.get_watchlist(user["user_id"]) or [])
    holding_count = len(portfolio.get("holdings") or [])
    score = min(95, 45 + alert_count * 8 + watch_count * 4 + holding_count * 5)
    payload = {
        "ok": True,
        "score": score,
        "account_security": 80 if user.get("email_verified") else 60,
        "wallet_safety": 75,
        "scam_exposure": 70,
        "portfolio_risk": max(45, 85 - holding_count * 5),
        "alert_coverage": min(95, 45 + alert_count * 15),
        "updated_at": datetime.now().isoformat(),
    }
    return jsonify(payload)


@webhook_app.route("/api/dashboard/streak", methods=["GET"])
def api_dashboard_streak():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("SELECT daily_checkin_streak, last_checkin_at FROM user_streaks WHERE user_id=? LIMIT 1", (user["user_id"],))
    existing = cur.fetchone()
    if existing:
        last_seen = existing["last_checkin_at"] if hasattr(existing, "keys") else existing[1]
        streak = int((existing["daily_checkin_streak"] if hasattr(existing, "keys") else existing[0]) or 0)
        if not last_seen or str(last_seen)[:10] < datetime.now().date().isoformat():
            streak += 1
        cur.execute("UPDATE user_streaks SET daily_checkin_streak=?, last_checkin_at=?, updated_at=? WHERE user_id=?", (streak, now, now, user["user_id"]))
    else:
        cur.execute(
            """
            INSERT INTO user_streaks (user_id, daily_checkin_streak, learning_streak, scam_safety_score, portfolio_discipline_score, alert_readiness_score, last_checkin_at, updated_at)
            VALUES (?, 1, 0, 70, 70, 70, ?, ?)
            """,
            (user["user_id"], now, now),
        )
    cur.execute("SELECT * FROM user_streaks WHERE user_id=?", (user["user_id"],))
    row = dict(cur.fetchone())
    conn.commit()
    conn.close()
    return jsonify({"ok": True, **row})


@webhook_app.route("/api/insights/save", methods=["POST"])
def api_insights_save():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO saved_insights (user_id, insight_type, title, content, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            user["user_id"],
            clean_html(payload.get("insight_type") or "ai")[:80],
            clean_html(payload.get("title") or "Saved insight")[:180],
            clean_html(payload.get("content") or payload.get("summary") or "")[:5000],
            json.dumps(payload.get("metadata") or {}),
            datetime.now().isoformat(),
        ),
    )
    saved_id = cur.lastrowid
    conn.commit()
    conn.close()
    log_product_event(user["user_id"], "insight_saved", {"saved_id": saved_id})
    return jsonify({"ok": True, "saved_id": saved_id})


@webhook_app.route("/api/insights", methods=["GET"])
def api_insights():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, insight_type, title, content, created_at FROM saved_insights WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["user_id"],))
    insights = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "insights": insights})


@webhook_app.route("/api/watch", methods=["GET", "POST"])
def api_watch_items():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        watch_type = clean_html(payload.get("watch_type") or payload.get("type") or "coin")[:80]
        target_value = clean_html(payload.get("target_value") or payload.get("value") or payload.get("symbol") or "")[:250]
        channels = payload.get("channels") or [payload.get("channel") or "in_app"]
        if not isinstance(channels, list):
            channels = [str(channels)]
        channel_text = ",".join(clean_html(str(channel)) for channel in channels if channel)
        cur.execute(
            """
            INSERT OR IGNORE INTO user_watch_items (user_id, watch_type, label, value, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user["user_id"],
                watch_type,
                clean_html(payload.get("label") or target_value)[:180],
                target_value,
                clean_html(payload.get("notes") or "")[:1200],
                datetime.now().isoformat(),
            ),
        )
        cur.execute(
            """
            INSERT INTO watch_rules (user_id, watch_type, target_value, conditions, channels, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                user["user_id"],
                watch_type,
                target_value,
                json.dumps(payload.get("conditions") or {"mode": "watch"}),
                channel_text or "in_app",
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        watch_rule_id = cur.lastrowid
        conn.commit()
        notification_service.send_user_alert(
            user["user_id"],
            "market_alerts",
            f"Watch created for {target_value or watch_type}",
            f"CoinPilotXAI will watch {target_value or watch_type} and deliver alerts through your enabled channels.",
            {"watch_rule_id": watch_rule_id, "watch_type": watch_type},
            channels=["in_app"],
        )
    cur.execute("SELECT id, watch_type, label, value, notes, created_at FROM user_watch_items WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["user_id"],))
    items = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM watch_rules WHERE user_id=? AND COALESCE(status,'active')!='deleted' ORDER BY id DESC LIMIT 100", (user["user_id"],))
    rules = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "items": items, "watch_rules": rules})


@webhook_app.route("/watch", methods=["GET"])
def watch_page():
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next="/watch"))
    return Response("""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Watch Command Center | CoinPilotXAI</title><style>body{margin:0;background:#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(100% - 28px,1100px);margin:auto;padding:28px 0 90px}.card{border:1px solid rgba(110,223,246,.22);border-radius:16px;background:rgba(255,255,255,.05);padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.row{display:grid;grid-template-columns:1fr auto;gap:10px;padding:12px;border:1px solid rgba(255,255,255,.08);border-radius:12px}.button,input,select{min-height:44px;border-radius:10px;border:1px solid rgba(110,223,246,.22);background:#081323;color:#f2fbff;padding:10px}.button{background:linear-gradient(135deg,#36e58f,#6edff6);color:#06101b;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}</style></head><body><main class='wrap'><h1>Live Watch Command Center</h1><p>Watch coins, wallets, exchanges, scam keywords, whale movements, news topics, sports edges, and portfolio assets. Alerts use your in-app, email, SMS, push, and optional companion preferences.</p><section class='card'><form id='watchForm' class='grid'><select name='watch_type'><option value='coin'>Coin/token</option><option value='wallet'>Wallet</option><option value='exchange'>Exchange</option><option value='scam_keyword'>Scam keyword</option><option value='whale'>Whale movement</option><option value='news_topic'>News topic</option><option value='sports_edge'>Sports edge</option><option value='portfolio_asset'>Portfolio asset</option></select><input name='value' placeholder='BTC, wallet, topic, keyword...' required><button class='button'>Watch This</button></form><p id='msg'></p></section><section class='card'><h2>My Active Watches</h2><div id='rules'>Loading...</div></section></main><script>async function load(){const d=await fetch('/api/watch',{cache:'no-store',credentials:'same-origin'}).then(r=>r.json());document.getElementById('rules').innerHTML=(d.watch_rules||[]).map(r=>`<div class='row'><span><strong>${r.target_value}</strong><br>${r.watch_type} · ${r.status}</span><span><button data-test='${r.id}'>Test Alert</button> <button data-pause='${r.id}'>Pause</button> <button data-delete='${r.id}'>Delete</button></span></div>`).join('')||'No watches yet.'}document.getElementById('watchForm').addEventListener('submit',async e=>{e.preventDefault();const p=Object.fromEntries(new FormData(e.target).entries());const d=await fetch('/api/watch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(r=>r.json());document.getElementById('msg').textContent=d.ok?'Watch created.':'Could not save watch.';e.target.reset();load()});document.addEventListener('click',async e=>{const b=e.target;if(b.dataset.test)await fetch('/api/watch/'+b.dataset.test+'/test-alert',{method:'POST'});if(b.dataset.pause)await fetch('/api/watch/'+b.dataset.pause+'/pause',{method:'POST'});if(b.dataset.delete)await fetch('/api/watch/'+b.dataset.delete+'/delete',{method:'POST'});load()});load()</script></body></html>""")


def update_watch_rule_status(rule_id, user_id, status):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE watch_rules SET status=?, updated_at=? WHERE id=? AND user_id=?", (status, datetime.now().isoformat(), rule_id, user_id))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(changed), "status": status}


@webhook_app.route("/api/watch/<int:rule_id>/pause", methods=["POST"])
def api_watch_pause(rule_id):
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    return jsonify(update_watch_rule_status(rule_id, user["user_id"], "paused"))


@webhook_app.route("/api/watch/<int:rule_id>/resume", methods=["POST"])
def api_watch_resume(rule_id):
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    return jsonify(update_watch_rule_status(rule_id, user["user_id"], "active"))


@webhook_app.route("/api/watch/<int:rule_id>/delete", methods=["POST"])
def api_watch_delete(rule_id):
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    return jsonify(update_watch_rule_status(rule_id, user["user_id"], "deleted"))


@webhook_app.route("/api/watch/<int:rule_id>/test-alert", methods=["POST"])
def api_watch_test_alert(rule_id):
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM watch_rules WHERE id=? AND user_id=? LIMIT 1", (rule_id, user["user_id"]))
    rule = dict(cur.fetchone() or {})
    conn.close()
    if not rule:
        return jsonify({"ok": False, "message": "Watch rule not found."}), 404
    result = notification_service.send_user_alert(user["user_id"], "market_alerts", f"Test alert: {rule.get('target_value')}", "This is a CoinPilotXAI watch test alert.", {"watch_rule_id": rule_id}, channels=(rule.get("channels") or "in_app").split(","))
    return jsonify({"ok": True, "delivery": result})


@webhook_app.route("/api/dashboard/widgets", methods=["GET", "POST"])
def api_dashboard_widgets():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        widgets = payload.get("widgets") or []
        for idx, widget in enumerate(widgets):
            cur.execute(
                "INSERT OR REPLACE INTO dashboard_widgets (user_id, widget_key, position, pinned, updated_at) VALUES (?, ?, ?, ?, ?)",
                (user["user_id"], clean_html(str(widget.get("widget_key") or widget))[:80], idx, 1, datetime.now().isoformat()),
            )
        conn.commit()
    cur.execute("SELECT widget_key, position, pinned, updated_at FROM dashboard_widgets WHERE user_id=? ORDER BY position ASC, id ASC", (user["user_id"],))
    widgets = [dict(row) for row in cur.fetchall()]
    if not widgets:
        widgets = [{"widget_key": key, "position": idx, "pinned": 1} for idx, key in enumerate(["live_market", "portfolio", "alerts", "ai_chat", "education"])]
    conn.close()
    return jsonify({"ok": True, "widgets": widgets})


DASHBOARD_PREF_KEYS = {
    "show_account": True,
    "show_upgrade_pro": True,
    "show_command_center": True,
    "show_logout": True,
    "show_saved_insights": True,
    "show_activity_timeline": True,
}


def get_dashboard_preferences(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM user_dashboard_preferences WHERE user_id=? LIMIT 1", (user_id,))
    row = cur.fetchone()
    if not row:
        now = datetime.now().isoformat()
        cur.execute(
            """
            INSERT INTO user_dashboard_preferences
            (user_id, show_account, show_upgrade_pro, show_command_center, show_logout, show_saved_insights, show_activity_timeline, created_at, updated_at)
            VALUES (?, 1, 1, 1, 1, 1, 1, ?, ?)
            """,
            (user_id, now, now),
        )
        conn.commit()
        cur.execute("SELECT * FROM user_dashboard_preferences WHERE user_id=? LIMIT 1", (user_id,))
        row = cur.fetchone()
    prefs = {key: bool(row[key]) if key in row.keys() else default for key, default in DASHBOARD_PREF_KEYS.items()}
    conn.close()
    return prefs


@webhook_app.route("/api/dashboard/preferences", methods=["GET", "POST"])
def api_dashboard_preferences():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if request.method == "GET":
        response = jsonify({"ok": True, **get_dashboard_preferences(user["user_id"])})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
    payload = request.get_json(silent=True) or {}
    updates = {}
    for key in DASHBOARD_PREF_KEYS:
        if key in payload:
            value = payload.get(key)
            if not isinstance(value, bool):
                return jsonify({"ok": False, "message": f"{key} must be true or false."}), 400
            updates[key] = int(value)
    if not updates:
        return jsonify({"ok": True, **get_dashboard_preferences(user["user_id"])})
    get_dashboard_preferences(user["user_id"])
    conn = db()
    cur = conn.cursor()
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [datetime.now().isoformat(), user["user_id"]]
    cur.execute(f"UPDATE user_dashboard_preferences SET {assignments}, updated_at=? WHERE user_id=?", values)
    conn.commit()
    conn.close()
    response = jsonify({"ok": True, **get_dashboard_preferences(user["user_id"])})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


EDUCATION_PREF_KEYS = {
    "show_edu_nav_home": True,
    "show_edu_nav_dashboard": True,
    "show_edu_nav_education": True,
    "show_edu_nav_scam_shield": True,
}


def get_education_preferences(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM user_education_preferences WHERE user_id=? LIMIT 1", (user_id,))
    row = cur.fetchone()
    if not row:
        now = datetime.now().isoformat()
        cur.execute(
            """
            INSERT INTO user_education_preferences
            (user_id, show_edu_nav_home, show_edu_nav_dashboard, show_edu_nav_education, show_edu_nav_scam_shield, created_at, updated_at)
            VALUES (?, 1, 1, 1, 1, ?, ?)
            """,
            (user_id, now, now),
        )
        conn.commit()
        cur.execute("SELECT * FROM user_education_preferences WHERE user_id=? LIMIT 1", (user_id,))
        row = cur.fetchone()
    prefs = {key: bool(row[key]) if key in row.keys() else default for key, default in EDUCATION_PREF_KEYS.items()}
    conn.close()
    return prefs


@webhook_app.route("/api/education/preferences", methods=["GET", "POST"])
def api_education_preferences():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if request.method == "GET":
        response = jsonify({"ok": True, **get_education_preferences(user["user_id"])})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
    payload = request.get_json(silent=True) or {}
    updates = {}
    for key in EDUCATION_PREF_KEYS:
        if key in payload:
            value = payload.get(key)
            if not isinstance(value, bool):
                return jsonify({"ok": False, "message": f"{key} must be true or false."}), 400
            updates[key] = int(value)
    if not updates:
        return jsonify({"ok": True, **get_education_preferences(user["user_id"])})
    get_education_preferences(user["user_id"])
    conn = db()
    cur = conn.cursor()
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [datetime.now().isoformat(), user["user_id"]]
    cur.execute(f"UPDATE user_education_preferences SET {assignments}, updated_at=? WHERE user_id=?", values)
    conn.commit()
    conn.close()
    response = jsonify({"ok": True, **get_education_preferences(user["user_id"])})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def simulator_snapshot(user_id):
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("SELECT * FROM paper_simulator_wallets WHERE user_id=?", (user_id,))
    wallet = cur.fetchone()
    if not wallet:
        cur.execute("INSERT INTO paper_simulator_wallets (user_id, cash_balance, created_at, updated_at) VALUES (?, 10000, ?, ?)", (user_id, now, now))
        conn.commit()
        cur.execute("SELECT * FROM paper_simulator_wallets WHERE user_id=?", (user_id,))
        wallet = cur.fetchone()
    cur.execute("SELECT symbol, SUM(CASE WHEN side='buy' THEN quantity ELSE -quantity END) AS quantity FROM paper_simulator_trades WHERE user_id=? GROUP BY symbol", (user_id,))
    positions = []
    total_value = 0
    for row in cur.fetchall():
        qty = float(row["quantity"] or 0)
        if abs(qty) <= 0.00000001:
            continue
        price_payload = market_data_service.get_symbol(row["symbol"])
        price = float(price_payload.get("price") or 0)
        value = qty * price
        total_value += value
        positions.append({"symbol": row["symbol"], "quantity": qty, "price": price, "value": round(value, 2), "source": price_payload.get("source") or "market service"})
    cur.execute("SELECT * FROM paper_simulator_trades WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,))
    trades = [dict(r) for r in cur.fetchall()]
    conn.close()
    cash = float(wallet["cash_balance"] or 0)
    equity = cash + total_value
    return {
        "ok": True,
        "cash_balance": round(cash, 2),
        "portfolio_value": round(total_value, 2),
        "equity": round(equity, 2),
        "positions": positions,
        "trades": trades,
        "ai_coaching": "Training simulator only. Practice position sizing, volatility awareness, and exit discipline before risking real money.",
        "risk_score": min(100, int(sum(abs(p["value"]) for p in positions) / max(equity, 1) * 100)) if equity else 0,
        "disclaimer": "Training simulator only. No real trades. Not financial advice.",
    }


@webhook_app.route("/api/simulator", methods=["GET", "POST"])
def api_market_simulator():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if request.method == "GET":
        return jsonify(simulator_snapshot(user["user_id"]))
    payload = request.get_json(silent=True) or {}
    side = clean_html(payload.get("side") or "").lower()
    symbol = clean_html(payload.get("symbol") or "BTC").upper()[:12]
    try:
        quantity = float(payload.get("quantity") or 0)
    except Exception:
        quantity = 0
    if side not in {"buy", "sell"} or quantity <= 0:
        return jsonify({"ok": False, "message": "Choose buy/sell and a positive fake quantity."}), 400
    price_payload = market_data_service.get_symbol(symbol)
    price = float(price_payload.get("price") or 0)
    if price <= 0:
        return jsonify({"ok": False, "message": "Live price feed temporarily unavailable."}), 503
    notional = price * quantity
    conn = db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("SELECT cash_balance FROM paper_simulator_wallets WHERE user_id=?", (user["user_id"],))
    row = cur.fetchone()
    cash = float(row[0] if row else 10000)
    if side == "buy" and notional > cash:
        conn.close()
        return jsonify({"ok": False, "message": "Simulator balance is too low for this fake trade."}), 400
    cash = cash - notional if side == "buy" else cash + notional
    cur.execute("INSERT OR IGNORE INTO paper_simulator_wallets (user_id, cash_balance, created_at, updated_at) VALUES (?, 10000, ?, ?)", (user["user_id"], now, now))
    cur.execute("UPDATE paper_simulator_wallets SET cash_balance=?, updated_at=? WHERE user_id=?", (cash, now, user["user_id"]))
    cur.execute("INSERT INTO paper_simulator_trades (user_id, symbol, side, quantity, price, notional, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (user["user_id"], symbol, side, quantity, price, notional, now))
    conn.commit()
    conn.close()
    log_product_event(user["user_id"], "simulator_trade_created", {"symbol": symbol, "side": side, "notional": notional})
    return jsonify(simulator_snapshot(user["user_id"]))


@webhook_app.route("/simulator", methods=["GET"])
def simulator_page():
    user = require_account()
    if not user:
        return redirect(url_for("signup_page", next="/simulator"))
    asset = clean_html(request.args.get("asset") or "BTC").upper()[:12]
    return Response(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Trading Simulator | CoinPilotXAI</title><style>body{{margin:0;background:#050b14;color:#f2fbff;font-family:Inter,system-ui,sans-serif;overflow-x:hidden}}.wrap{{width:min(100% - 28px,1180px);margin:auto;padding:28px 0 96px}}.grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}}.card{{border:1px solid rgba(110,223,246,.22);border-radius:16px;background:rgba(255,255,255,.05);padding:18px;box-shadow:0 22px 70px rgba(0,0,0,.24)}}.button,input,select{{min-height:44px;border-radius:10px;border:1px solid rgba(110,223,246,.22);background:#081323;color:#f2fbff;padding:10px}}.button{{background:linear-gradient(135deg,#36e58f,#6edff6);color:#06101b;font-weight:900;cursor:pointer}}.row{{display:grid;grid-template-columns:1fr auto auto;gap:10px;padding:10px;border-bottom:1px solid rgba(255,255,255,.08)}}.muted{{color:#9fb5c0}}@media(max-width:820px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main class='wrap'><h1>CoinPilotXAI Trading Simulator</h1><p class='muted'>Training simulator only. No real trades. Not financial advice.</p><section class='grid'><article class='card'><h2>Paper Wallet</h2><div id='summary'>Loading...</div><h2>Positions</h2><div id='positions'></div><h2>Trade History</h2><div id='history'></div></article><aside class='card'><h2>Simulated Order Ticket</h2><form id='order'><select name='side'><option value='buy'>Market Buy</option><option value='sell'>Market Sell</option></select><input name='symbol' value='{asset}' placeholder='BTC'><input name='quantity' type='number' step='any' min='0' placeholder='Fake quantity'><button class='button'>Preview and Place Fake Trade</button></form><p id='msg' class='muted'></p><h2>AI Hitchhiker Coach</h2><p id='coach' class='muted'>The coach will warn about overexposure, FOMO, revenge trading, and sizing risk.</p></aside></section></main><script>const money=n=>Number(n||0).toLocaleString(undefined,{{style:'currency',currency:'USD'}});async function load(){{const d=await fetch('/api/simulator',{{cache:'no-store',credentials:'same-origin'}}).then(r=>r.json());document.getElementById('summary').innerHTML=`<div class='row'><strong>Cash</strong><span>${{money(d.cash_balance)}}</span></div><div class='row'><strong>Equity</strong><span>${{money(d.equity)}}</span></div><div class='row'><strong>Risk Score</strong><span>${{d.risk_score}}/100</span></div>`;document.getElementById('positions').innerHTML=(d.positions||[]).map(p=>`<div class='row'><strong>${{p.symbol}}</strong><span>${{p.quantity}}</span><span>${{money(p.value)}}</span></div>`).join('')||'<p class=muted>No fake positions yet.</p>';document.getElementById('history').innerHTML=(d.trades||[]).slice(0,20).map(t=>`<div class='row'><strong>${{t.side}} ${{t.symbol}}</strong><span>${{t.quantity}}</span><span>${{money(t.notional)}}</span></div>`).join('')||'<p class=muted>No fake trades yet.</p>';document.getElementById('coach').textContent=d.ai_coaching}}document.getElementById('order').addEventListener('submit',async e=>{{e.preventDefault();const p=Object.fromEntries(new FormData(e.target).entries());const d=await fetch('/api/simulator/order',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(p)}}).then(r=>r.json());document.getElementById('msg').textContent=d.ok?'Fake trade saved.':'Simulator says: '+(d.message||'Trade failed.');load()}});load()</script></body></html>""")


@webhook_app.route("/api/simulator/order", methods=["POST"])
def api_simulator_order():
    return api_market_simulator()


@webhook_app.route("/api/simulator/portfolio", methods=["GET"])
def api_simulator_portfolio():
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    return jsonify(simulator_snapshot(user["user_id"]))


@webhook_app.route("/api/simulator/history", methods=["GET"])
def api_simulator_history():
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    return jsonify({"ok": True, "trades": simulator_snapshot(user["user_id"]).get("trades", [])})


@webhook_app.route("/api/simulator/watchlist", methods=["GET", "POST"])
def api_simulator_watchlist():
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        cur.execute("INSERT OR IGNORE INTO simulator_watchlists (user_id, symbol, created_at) VALUES (?, ?, ?)", (user["user_id"], clean_html(payload.get("symbol") or "BTC").upper()[:12], datetime.now().isoformat()))
        conn.commit()
    cur.execute("SELECT symbol, created_at FROM simulator_watchlists WHERE user_id=? ORDER BY symbol ASC", (user["user_id"],))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "watchlist": rows})


@webhook_app.route("/api/simulator/lesson", methods=["GET"])
def api_simulator_lesson():
    return jsonify({"ok": True, "lesson": {"title": "How paper trading works", "content": "Paper trading uses fake money to practice decision quality, order types, position sizing, and emotional discipline. No real trades are placed.", "disclaimer": "Training simulator only. Not financial advice."}})


@webhook_app.route("/api/simulator/reset", methods=["POST"])
def api_simulator_reset():
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM paper_simulator_trades WHERE user_id=?", (user["user_id"],))
    cur.execute("INSERT OR IGNORE INTO paper_simulator_wallets (user_id, cash_balance, created_at, updated_at) VALUES (?, 10000, ?, ?)", (user["user_id"], datetime.now().isoformat(), datetime.now().isoformat()))
    cur.execute("UPDATE paper_simulator_wallets SET cash_balance=10000, updated_at=? WHERE user_id=?", (datetime.now().isoformat(), user["user_id"]))
    conn.commit()
    conn.close()
    return jsonify(simulator_snapshot(user["user_id"]))


@webhook_app.route("/api/education/progress", methods=["GET", "POST"])
def api_education_progress():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        lesson_slug = clean_html(payload.get("lesson_slug") or "")[:160]
        path = clean_html(payload.get("path") or "education")[:120]
        status = clean_html(payload.get("status") or "started")[:40]
        score = int(payload.get("score") or 0)
        cur.execute(
            "INSERT OR REPLACE INTO education_progress (user_id, path, lesson_slug, status, score, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user["user_id"], path, lesson_slug, status, score, datetime.now().isoformat()),
        )
        cur.execute(
            "INSERT OR REPLACE INTO education_user_progress (user_id, lesson_slug, status, score, updated_at) VALUES (?, ?, ?, ?, ?)",
            (user["user_id"], lesson_slug, status, score, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "message": "Education progress saved.", "lesson_slug": lesson_slug, "status": status, "score": score})
    cur.execute("SELECT path, lesson_slug, status, score, updated_at FROM education_progress WHERE user_id=? ORDER BY updated_at DESC LIMIT 100", (user["user_id"],))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "progress": rows, "paths": ["Crypto Basics", "Investor Safety", "Scam Defense", "Wallet Intelligence", "Market Psychology", "On-chain Analysis"]})


@webhook_app.route("/api/account/status", methods=["GET"])
def account_status_api():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    repair_trialing_users_with_successful_payments()
    fresh_user = load_account_by_id(user["user_id"]) or user
    access = account_access_context(fresh_user)
    payload = {
        "ok": True,
        "user_id": fresh_user.get("user_id"),
        "email": mask_email(fresh_user.get("email")),
        "plan": fresh_user.get("plan") or fresh_user.get("subscription_plan") or "free",
        "subscription_plan": fresh_user.get("subscription_plan") or fresh_user.get("plan") or "free",
        "subscription_status": fresh_user.get("subscription_status") or "inactive",
        "is_pro": int(fresh_user.get("is_pro") or 0),
        "trial_status": fresh_user.get("trial_status") or "",
        "has_pro_access": has_pro_access(fresh_user),
        "has_pro": has_pro_access(fresh_user),
        "pro_access_type": pro_access_type(fresh_user),
        "backend_source": "database",
        "is_paid_pro": is_paid_pro_user(fresh_user),
        "is_trialing": is_trialing_user(fresh_user),
        "access_label": access.get("label"),
        "pro_expires_at": fresh_user.get("pro_expires_at") or fresh_user.get("subscription_expires_at") or "",
        "trial_end_date": fresh_user.get("trial_end_date") or "",
        "stripe_customer_id": fresh_user.get("stripe_customer_id") or "",
        "stripe_subscription_id": fresh_user.get("stripe_subscription_id") or "",
        "telegram_linked": bool(fresh_user.get("telegram_user_id")),
        "telegram_username": fresh_user.get("telegram_username") or "",
        "updated_at": fresh_user.get("updated_at") or "",
    }
    logging.info(
        "account status API called authenticated_user_id=%s db_plan=%s db_status=%s paid_pro=%s trialing=%s",
        fresh_user.get("user_id"),
        payload["plan"],
        payload["subscription_status"],
        payload["is_paid_pro"],
        payload["is_trialing"],
    )
    logging.info("Dashboard status payload user_id=%s payload=%s", fresh_user.get("user_id"), json.dumps({k: payload[k] for k in ("plan", "subscription_status", "has_pro_access", "is_paid_pro", "is_trialing", "telegram_linked")}))
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/portfolio", methods=["GET", "POST"])
def portfolio_api():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if request.method == "GET":
        return jsonify({"ok": True, "portfolio": portfolio_service.calculate_user_portfolio(user["user_id"])})
    gated = api_pro_required(user, "Portfolio Tracker")
    if gated:
        return gated
    payload = request.get_json(silent=True) or {}
    result = portfolio_service.add_portfolio_item(
        user["user_id"],
        clean_html(payload.get("symbol", "")),
        clean_html(payload.get("coin_name", "")),
        payload.get("amount", 0),
        payload.get("average_buy_price", 0),
        clean_html(payload.get("notes", "")),
    )
    log_product_event(user["user_id"], "portfolio_item_added", {"symbol": payload.get("symbol", ""), "ok": result.get("ok")})
    return jsonify(result), (200 if result.get("ok") else 400)


@webhook_app.route("/api/portfolio/<int:item_id>", methods=["PUT", "DELETE"])
def portfolio_item_api(item_id):
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    gated = api_pro_required(user, "Portfolio Tracker")
    if gated:
        return gated
    if request.method == "DELETE":
        result = portfolio_service.delete_portfolio_item(user["user_id"], item_id)
        log_product_event(user["user_id"], "portfolio_item_deleted", {"item_id": item_id, "ok": result.get("ok")})
        return jsonify(result), (200 if result.get("ok") else 404)
    payload = request.get_json(silent=True) or {}
    clean_payload = {key: clean_html(str(value)) if key in {"symbol", "coin_name", "notes"} else value for key, value in payload.items()}
    result = portfolio_service.update_portfolio_item(user["user_id"], item_id, clean_payload)
    log_product_event(user["user_id"], "portfolio_item_updated", {"item_id": item_id, "ok": result.get("ok")})
    return jsonify(result), (200 if result.get("ok") else 404)


@webhook_app.route("/api/watchlist", methods=["GET", "POST"])
def watchlist_api():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if request.method == "GET":
        return jsonify({"ok": True, "watchlist": portfolio_service.get_watchlist(user["user_id"])})
    gated = api_pro_required(user, "Watchlist")
    if gated:
        return gated
    payload = request.get_json(silent=True) or {}
    result = portfolio_service.add_watchlist_item(user["user_id"], clean_html(payload.get("symbol", "")), clean_html(payload.get("coin_name", "")))
    log_product_event(user["user_id"], "watchlist_item_added", {"symbol": payload.get("symbol", ""), "ok": result.get("ok")})
    return jsonify(result), (200 if result.get("ok") else 400)


@webhook_app.route("/api/watchlist/<int:item_id>", methods=["DELETE"])
def watchlist_item_api(item_id):
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    gated = api_pro_required(user, "Watchlist")
    if gated:
        return gated
    result = portfolio_service.delete_watchlist_item(user["user_id"], item_id)
    return jsonify(result), (200 if result.get("ok") else 404)


@webhook_app.route("/api/alerts", methods=["GET", "POST"])
def alerts_api():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if request.method == "GET":
        return jsonify({"ok": True, "alerts": portfolio_service.get_alerts(user["user_id"])})
    gated = api_pro_required(user, "Alerts")
    if gated:
        return gated
    payload = request.get_json(silent=True) or {}
    channels = payload.get("channels") or [payload.get("channel") or "in_app"]
    if not isinstance(channels, list):
        channels = [str(channels)]
    channel_value = ",".join(clean_html(str(channel)) for channel in channels if channel)
    result = portfolio_service.create_price_alert(
        user["user_id"],
        clean_html(payload.get("alert_type", "price")),
        clean_html(payload.get("symbol", "")),
        payload.get("target_value", 0),
        clean_html(payload.get("condition", "above")),
        channel_value or "in_app",
    )
    log_product_event(user["user_id"], "alert_created", {"symbol": payload.get("symbol", ""), "ok": result.get("ok")})
    if result.get("ok"):
        notification_service.send_user_alert(
            user["user_id"],
            "market_alerts",
            f"Alert created for {clean_html(payload.get('symbol', '')).upper()}",
            "CoinPilotXAI saved your alert rule and will notify you through enabled channels when it triggers.",
            {"symbol": payload.get("symbol", ""), "channels": channels},
            channels=["in_app"],
        )
    return jsonify(result), (200 if result.get("ok") else 400)


@webhook_app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def alert_item_api(alert_id):
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    result = portfolio_service.delete_alert(user["user_id"], alert_id)
    return jsonify(result), (200 if result.get("ok") else 404)


@webhook_app.route("/alerts", methods=["GET"])
def alerts_page():
    user = require_account()
    if not user:
        return redirect(url_for("login_page", next="/alerts"))
    return Response("""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Alerts Command Center | CoinPilotXAI</title><style>:root{color-scheme:dark;--bg:#050b14;--cyan:#6edff6;--green:#36e58f;--gold:#ffd166;--red:#ff6b7a;--line:rgba(110,223,246,.22);--muted:#9fb5c0}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% 0,rgba(110,223,246,.18),transparent 28rem),linear-gradient(145deg,#050b14,#081421);color:#f2fbff;font-family:Inter,system-ui,sans-serif;overflow-x:hidden}.wrap{width:min(100% - 28px,1120px);margin:auto;padding:28px 0 92px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(180deg,rgba(17,29,50,.92),rgba(13,22,39,.88));box-shadow:0 24px 80px rgba(0,0,0,.28);padding:18px}.button,input,select{min-height:44px;border-radius:10px;border:1px solid var(--line);background:#081323;color:#f2fbff;padding:10px;font:inherit}.button{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;background:linear-gradient(135deg,var(--green),var(--cyan));color:#06101b;font-weight:900;cursor:pointer}.row{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;padding:12px;border:1px solid rgba(255,255,255,.08);border-radius:12px;margin:10px 0;background:rgba(255,255,255,.04)}.status{color:#c8ffe2;font-weight:900}.muted{color:var(--muted)}.channel-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.channel-grid label{border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:10px;color:var(--muted)}@media(max-width:800px){.grid{grid-template-columns:1fr}.button{width:100%}.row{grid-template-columns:1fr}}</style></head><body><main class='wrap'><a href='/dashboard'>← Dashboard</a><h1>Alerts Command Center</h1><p class='muted'>Manage price, whale, wallet, scam keyword, prediction, news, and volatility alerts from one focused page.</p><section class='grid'><article class='card'><h2>Create Alert</h2><form id='alertForm'><select name='alert_type'><option value='price'>Coin price</option><option value='move_24h'>24h move</option><option value='wallet'>Wallet movement</option><option value='scam_keyword'>Scam keyword</option><option value='prediction'>Prediction probability</option><option value='news'>News trigger</option><option value='volatility'>Volatility spike</option></select><input name='symbol' placeholder='BTC, wallet, keyword, prediction...' required><select name='condition'><option value='above'>Above</option><option value='below'>Below</option><option value='changes'>Changes</option></select><input name='target_value' type='number' step='any' placeholder='Target value / threshold' value='0'><div class='channel-grid'><label><input type='checkbox' name='channels' value='in_app' checked> In-app</label><label><input type='checkbox' name='channels' value='email'> Email</label><label><input type='checkbox' name='channels' value='push'> PWA push</label><label><input type='checkbox' name='channels' value='sms'> SMS/Text</label><label><input type='checkbox' name='channels' value='telegram'> Optional companion</label></div><button class='button'>Activate Alert</button><p id='msg' class='muted'></p></form></article><article class='card'><h2>Alert Preferences</h2><p class='muted'>Delivery follows your notification preferences. Missing SMS or push provider settings will fail gracefully and stay logged.</p><a class='button' href='/notifications'>Notification Center</a></article></section><section class='card'><h2>Active Alerts</h2><div id='alerts'>Loading...</div></section></main><script>async function load(){const d=await fetch('/api/alerts',{cache:'no-store',credentials:'same-origin'}).then(r=>r.json());document.getElementById('alerts').innerHTML=(d.alerts||[]).map(a=>`<div class='row'><span><strong>${a.symbol||a.target||'Alert'}</strong><br><span class='muted'>${a.alert_type||'price'} ${a.condition||''} ${a.target_value||a.threshold||''} · ${a.channel||'in-app'}</span></span><button class='button' data-delete='${a.id}'>Delete</button></div>`).join('')||'<p class=muted>No alerts yet.</p>'}document.getElementById('alertForm').addEventListener('submit',async e=>{e.preventDefault();const fd=new FormData(e.target);const payload=Object.fromEntries(fd.entries());payload.channels=fd.getAll('channels');const r=await fetch('/api/alerts',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(payload)});const d=await r.json();document.getElementById('msg').textContent=d.ok?'Prediction alert activated.':'Alert could not be created: '+(d.message||'check Pro access');if(d.ok){e.target.reset();load()}});document.addEventListener('click',async e=>{const b=e.target.closest('[data-delete]');if(!b)return;await fetch('/api/alerts/'+b.dataset.delete,{method:'DELETE',credentials:'same-origin'});load()});load()</script></body></html>""")


@webhook_app.route("/api/notifications", methods=["GET"])
def api_notifications():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    response = jsonify(notification_service.list_notifications(user["user_id"]))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/notifications/read", methods=["POST"])
def api_notifications_read():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    payload = request.get_json(silent=True) or {}
    result = notification_service.mark_read(user["user_id"], int(payload.get("notification_id") or payload.get("id") or 0))
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/notifications/read-all", methods=["POST"])
def api_notifications_read_all():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    response = jsonify(notification_service.mark_all_read(user["user_id"]))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/notification-preferences", methods=["GET", "POST"])
def api_notification_preferences():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    if request.method == "GET":
        result = notification_service.get_preferences(user["user_id"])
    else:
        payload = request.get_json(silent=True) or {}
        result = notification_service.update_preferences(user["user_id"], payload.get("preferences") or payload)
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    payload = request.get_json(silent=True) or {}
    result = notification_service.save_push_subscription(user["user_id"], payload, request.headers.get("User-Agent", ""))
    log_product_event(user["user_id"], "push_subscription_saved", {"ok": result.get("ok")})
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response, (200 if result.get("ok") else 400)


@webhook_app.route("/api/push/unsubscribe", methods=["POST"])
def api_push_unsubscribe():
    init_db()
    user = api_account_user()
    if not user:
        response = jsonify({"ok": False, "message": "Login required."})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response, 401
    payload = request.get_json(silent=True) or {}
    result = notification_service.unsubscribe_push(user["user_id"], payload.get("endpoint") or "")
    log_product_event(user["user_id"], "push_subscription_removed", {"ok": result.get("ok")})
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@webhook_app.route("/api/push/test", methods=["POST"])
def api_push_test():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    result = notification_service.send_user_alert(
        user["user_id"],
        "product_updates",
        "CoinPilotXAI push test",
        "Your CoinPilotXAI alert channels are connected where configured.",
        {"url": "/notifications"},
        channels=["in_app", "push"],
    )
    log_product_event(user["user_id"], "push_test_requested", result)
    return jsonify({"ok": True, "delivery": result})


@webhook_app.route("/admin/notification-delivery", methods=["GET"])
def admin_notification_delivery_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM notification_delivery_logs ORDER BY id DESC LIMIT 250")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    table = "".join(
        f"<tr><td>{r.get('user_id')}</td><td>{clean_html(r.get('channel') or '')}</td><td>{clean_html(r.get('status') or '')}</td><td>{clean_html(r.get('error_message') or '')}</td><td>{clean_html(r.get('created_at') or '')}</td></tr>"
        for r in rows
    )
    body = f"<h1>Notification Delivery</h1><p class='muted'>In-app, email, SMS, push, and optional companion alert attempts.</p><div class='card'><table><tr><th>User</th><th>Channel</th><th>Status</th><th>Error</th><th>Created</th></tr>{table}</table></div>"
    return admin_page_html("Notification Delivery", body, admin)


@webhook_app.route("/admin/test-notification", methods=["GET", "POST"])
def admin_test_notification_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    if request.method == "POST":
        payload = request.get_json(silent=True) if request.is_json else request.form
        user_id = int(payload.get("user_id") or 0)
        result = notification_service.send_user_alert(
            user_id,
            "product_updates",
            "CoinPilotXAI admin test alert",
            "This is a test notification from the CoinPilotXAI admin center.",
            {"admin_id": admin.get("id"), "url": "/notifications"},
            channels=payload.getlist("channels") if hasattr(payload, "getlist") else payload.get("channels", ["in_app", "email", "sms", "push"]),
        )
        log_admin_audit(admin.get("id"), "admin_test_notification_sent", "user", str(user_id), result)
        if request.is_json:
            return jsonify({"ok": True, "delivery": result})
        return admin_page_html("Test Notification", f"<h1>Test Notification</h1><pre>{clean_html(json.dumps(result, indent=2))}</pre><p><a class='button' href='/admin/test-notification'>Send another</a></p>", admin)
    body = """
    <h1>Send Test Notification</h1>
    <div class="card"><form method="post">
      <label>User ID</label><input name="user_id" required>
      <label><input type="checkbox" name="channels" value="in_app" checked> In-app</label>
      <label><input type="checkbox" name="channels" value="email"> Email</label>
      <label><input type="checkbox" name="channels" value="sms"> SMS</label>
      <label><input type="checkbox" name="channels" value="push"> PWA push</label>
      <label><input type="checkbox" name="channels" value="telegram"> Optional companion</label>
      <button>Send Test Alert</button>
    </form></div>
    """
    return admin_page_html("Test Notification", body, admin)


@webhook_app.route("/admin/watch-rules", methods=["GET"])
def admin_watch_rules_page():
    admin, denied = require_admin_page("analytics.view")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM watch_rules ORDER BY id DESC LIMIT 250")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    table = "".join(f"<tr><td>{r.get('id')}</td><td>{r.get('user_id')}</td><td>{clean_html(r.get('watch_type') or '')}</td><td>{clean_html(r.get('target_value') or '')}</td><td>{clean_html(r.get('channels') or '')}</td><td>{clean_html(r.get('status') or '')}</td><td>{clean_html(r.get('last_triggered_at') or '')}</td></tr>" for r in rows)
    body = f"<h1>Watch Rules</h1><div class='card'><table><tr><th>ID</th><th>User</th><th>Type</th><th>Target</th><th>Channels</th><th>Status</th><th>Last Triggered</th></tr>{table}</table></div>"
    return admin_page_html("Watch Rules", body, admin)


@webhook_app.route("/admin/education", methods=["GET"])
def admin_education_page():
    admin, denied = require_admin_page("settings.edit")
    if denied:
        return denied
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT category_slug, COUNT(*) AS count FROM education_lessons GROUP BY category_slug ORDER BY count DESC")
    counts = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT lesson_slug, COUNT(*) AS views FROM education_lesson_views GROUP BY lesson_slug ORDER BY views DESC LIMIT 20")
    views = [dict(row) for row in cur.fetchall()]
    conn.close()
    count_rows = "".join(f"<tr><td>{clean_html(r.get('category_slug') or '')}</td><td>{r.get('count')}</td></tr>" for r in counts)
    view_rows = "".join(f"<tr><td>{clean_html(r.get('lesson_slug') or '')}</td><td>{r.get('views')}</td></tr>" for r in views)
    body = f"<h1>Education Manager</h1><p class='muted'>Starter knowledge bank seeded and active. Editing UI can be expanded here without touching public lesson URLs.</p><div class='grid'><div class='card'><h2>Lessons by Category</h2><table>{count_rows}</table></div><div class='card'><h2>Popular Lessons</h2><table>{view_rows}</table></div></div>"
    return admin_page_html("Education Manager", body, admin)


@webhook_app.route("/admin/seo", methods=["GET"])
def admin_seo_page():
    admin, denied = require_admin_page("system.view")
    if denied:
        return denied
    public_paths = all_public_paths()
    noindex = ["/app", "/chat", "/command-center", "/dashboard", "/account", "/admin", "/api/*", "/stripe/*"]
    schema_types = ["Organization", "SoftwareApplication", "Product", "FAQPage", "BreadcrumbList", "WebSite", "Course", "LearningResource"]
    rows = "".join(f"<tr><td>{clean_html(path)}</td><td>crawlable</td><td>canonical expected</td></tr>" for path in public_paths[:80])
    body = f"<h1>SEO Intelligence Center</h1><div class='grid'><div class='card'><div class='metric'>{len(public_paths)}</div><p>public indexable paths tracked</p></div><div class='card'><div class='metric'>{len(noindex)}</div><p>private/noindex patterns protected</p></div><div class='card'><div class='metric'>{len(schema_types)}</div><p>schema families active/planned</p></div></div><div class='card'><h2>Noindex Protection</h2><p>{', '.join(noindex)}</p></div><div class='card'><h2>Public Crawl Targets</h2><table><tr><th>Path</th><th>Status</th><th>Metadata</th></tr>{rows}</table></div>"
    return admin_page_html("SEO Intelligence Center", body, admin)


@webhook_app.route("/api/messages/start", methods=["POST"])
def api_messages_start():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    query = normalize_email(clean_html(payload.get("query") or payload.get("email") or payload.get("username") or ""))
    if not query:
        return jsonify({"ok": False, "message": "User email or username required."}), 400
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id FROM users
        WHERE lower(email)=lower(?) OR lower(username)=lower(?) OR lower(display_name)=lower(?)
        ORDER BY user_id LIMIT 1
        """,
        (query, query, query),
    )
    other = cur.fetchone()
    conn.close()
    if not other or other[0] == user["user_id"]:
        return jsonify({"ok": False, "message": "User not found."}), 404
    conversation_id = direct_conversation_between(user["user_id"], other[0])
    log_product_event(user["user_id"], "private_chat_started", {"conversation_id": conversation_id})
    return jsonify({"ok": True, "conversation_id": conversation_id})


@webhook_app.route("/api/messages/conversations", methods=["GET"])
def api_messages_conversations():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id, c.updated_at, ou.user_id AS other_user_id,
               COALESCE(NULLIF(ou.display_name, ''), NULLIF(ou.full_name, ''), NULLIF(ou.username, ''), ou.email, 'CoinPilotXAI user') AS other_name,
               ou.email AS other_email, ou.last_seen_at AS other_last_seen_at,
               MAX(pm.created_at) AS last_message_at,
               (
                 SELECT body FROM private_messages p2
                 WHERE p2.conversation_id=c.id AND p2.deleted_at IS NULL
                 ORDER BY p2.id DESC LIMIT 1
               ) AS latest_message,
               COUNT(CASE WHEN pm.sender_user_id != ? AND pm.created_at > COALESCE(cm.last_read_at, '') THEN 1 END) AS unread_count
        FROM conversations c
        JOIN conversation_members cm ON cm.conversation_id=c.id AND cm.user_id=?
        LEFT JOIN conversation_members other_cm ON other_cm.conversation_id=c.id AND other_cm.user_id != ?
        LEFT JOIN users ou ON ou.user_id=other_cm.user_id
        LEFT JOIN private_messages pm ON pm.conversation_id=c.id AND pm.deleted_at IS NULL
        GROUP BY c.id, c.updated_at, ou.user_id, ou.display_name, ou.full_name, ou.username, ou.email, ou.last_seen_at
        ORDER BY COALESCE(MAX(pm.created_at), c.updated_at) DESC
        LIMIT 80
        """,
        (user["user_id"], user["user_id"], user["user_id"]),
    )
    rows = []
    for row in cur.fetchall():
        rows.append({
            "id": row["id"],
            "title": row["other_name"] or f"Conversation {row['id']}",
            "other_user_id": row["other_user_id"],
            "other_email": row["other_email"],
            "other_last_seen_at": row["other_last_seen_at"],
            "updated_at": row["updated_at"],
            "last_message_at": row["last_message_at"],
            "latest_message": row["latest_message"],
            "unread_count": row["unread_count"] or 0,
        })
    conn.close()
    return jsonify({"ok": True, "conversations": rows})


@webhook_app.route("/api/messages/<int:conversation_id>", methods=["GET"])
def api_messages_thread(conversation_id):
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if not user_is_conversation_member(user["user_id"], conversation_id):
        return jsonify({"ok": False, "message": "Conversation not found."}), 404
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, conversation_id, sender_user_id, body, created_at FROM private_messages WHERE conversation_id=? AND deleted_at IS NULL ORDER BY id ASC LIMIT 200",
        (conversation_id,),
    )
    messages = [dict(row) for row in cur.fetchall()]
    now = datetime.now().isoformat()
    cur.execute("UPDATE conversation_members SET last_read_at=? WHERE user_id=? AND conversation_id=?", (now, user["user_id"], conversation_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "current_user_id": user["user_id"], "messages": messages})


@webhook_app.route("/api/messages/<int:conversation_id>/send", methods=["POST"])
def api_messages_send(conversation_id):
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if not user_is_conversation_member(user["user_id"], conversation_id):
        return jsonify({"ok": False, "message": "Conversation not found."}), 404
    payload = request.get_json(silent=True) or {}
    body = clean_html(payload.get("body") or payload.get("message") or "")[:2000]
    if not body:
        return jsonify({"ok": False, "message": "Message required."}), 400
    conn = db()
    cur = conn.cursor()
    one_minute_ago = (datetime.now() - timedelta(minutes=1)).isoformat()
    cur.execute("SELECT COUNT(*) AS count FROM private_messages WHERE sender_user_id=? AND created_at>=?", (user["user_id"], one_minute_ago))
    recent_send_row = cur.fetchone()
    if (recent_send_row["count"] if recent_send_row else 0) >= 12:
        conn.close()
        return jsonify({"ok": False, "message": "Slow down before sending more messages."}), 429
    cur.execute(
        """
        SELECT blocked_user_id FROM blocked_users
        WHERE (blocker_user_id=? AND blocked_user_id IN (SELECT user_id FROM conversation_members WHERE conversation_id=?))
           OR (blocked_user_id=? AND blocker_user_id IN (SELECT user_id FROM conversation_members WHERE conversation_id=?))
        LIMIT 1
        """,
        (user["user_id"], conversation_id, user["user_id"], conversation_id),
    )
    if cur.fetchone():
        conn.close()
        return jsonify({"ok": False, "message": "Messaging is blocked for this conversation."}), 403
    cur.execute("INSERT INTO private_messages (conversation_id, sender_user_id, body, created_at) VALUES (?, ?, ?, ?)", (conversation_id, user["user_id"], body, datetime.now().isoformat()))
    message_id = cur.lastrowid
    cur.execute("UPDATE conversations SET updated_at=? WHERE id=?", (datetime.now().isoformat(), conversation_id))
    cur.execute("SELECT user_id FROM conversation_members WHERE conversation_id=? AND user_id != ?", (conversation_id, user["user_id"]))
    recipients = [row["user_id"] for row in cur.fetchall()]
    conn.commit()
    conn.close()
    log_product_event(user["user_id"], "private_message_sent", {"conversation_id": conversation_id})
    for recipient_id in recipients:
        notification_service.send_user_alert(
            recipient_id,
            "private_message",
            "New private message",
            "You received a private message in CoinPilotXAI.",
            {"conversation_id": conversation_id, "message_id": message_id},
            channels=["in_app"],
        )
    return jsonify({"ok": True, "message_id": message_id})


@webhook_app.route("/api/messages/<int:conversation_id>/read", methods=["POST"])
def api_messages_read(conversation_id):
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    if not user_is_conversation_member(user["user_id"], conversation_id):
        return jsonify({"ok": False, "message": "Conversation not found."}), 404
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE conversation_members SET last_read_at=? WHERE user_id=? AND conversation_id=?", (datetime.now().isoformat(), user["user_id"], conversation_id))
    cur.execute(
        """
        INSERT OR IGNORE INTO message_read_receipts (message_id, user_id, read_at)
        SELECT id, ?, ? FROM private_messages
        WHERE conversation_id=? AND sender_user_id != ? AND deleted_at IS NULL
        """,
        (user["user_id"], datetime.now().isoformat(), conversation_id, user["user_id"]),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@webhook_app.route("/api/messages/block", methods=["POST"])
def api_messages_block():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    blocked_user_id = int(payload.get("user_id") or payload.get("blocked_user_id") or 0)
    if not blocked_user_id or blocked_user_id == user["user_id"]:
        return jsonify({"ok": False, "message": "Valid user required."}), 400
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, reason, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(blocker_user_id, blocked_user_id) DO UPDATE SET reason=excluded.reason",
        (user["user_id"], blocked_user_id, clean_html(payload.get("reason") or "")[:400], datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@webhook_app.route("/api/messages/report", methods=["POST"])
def api_messages_report():
    init_db()
    user = api_account_user()
    if not user:
        return jsonify({"ok": False, "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    conversation_id = int(payload.get("conversation_id") or 0)
    if conversation_id and not user_is_conversation_member(user["user_id"], conversation_id):
        return jsonify({"ok": False, "message": "Conversation not found."}), 404
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_reports (reporter_user_id, reported_user_id, conversation_id, message_id, reason, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
        """,
        (
            user["user_id"],
            int(payload.get("reported_user_id") or 0),
            conversation_id,
            int(payload.get("message_id") or 0),
            clean_html(payload.get("reason") or "")[:1000],
            datetime.now().isoformat(),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    log_product_event(user["user_id"], "private_chat_reported", {"conversation_id": conversation_id})
    return jsonify({"ok": True})


def stripe_event_processed(event_id):
    if not event_id:
        return False
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM stripe_events WHERE stripe_event_id=? AND status='processed' LIMIT 1", (event_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def record_stripe_event(event, status="processed", user_id=None, error_message=""):
    event = event or {}
    event_id = event.get("id") or ""
    if not event_id:
        return
    event_type = event.get("type") or ""
    stripe_object = ((event.get("data") or {}).get("object") or {})
    payload_summary = json.dumps({
        "object_id": stripe_object.get("id"),
        "customer": stripe_object.get("customer"),
        "subscription": stripe_object.get("subscription") or stripe_object.get("id") if event_type.startswith("customer.subscription") else stripe_object.get("subscription"),
        "payment_status": stripe_object.get("payment_status"),
        "amount": stripe_object.get("amount_total") or stripe_object.get("amount_paid"),
    })[:2000]
    now = datetime.now().isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO stripe_events (stripe_event_id, event_type, user_id, status, error_message, payload_summary, created_at, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_event_id) DO UPDATE SET
            user_id=COALESCE(excluded.user_id, stripe_events.user_id),
            status=excluded.status,
            error_message=excluded.error_message,
            payload_summary=excluded.payload_summary,
            processed_at=excluded.processed_at
        """,
        (event_id, event_type, user_id, status, error_message[:500], payload_summary, now, now if status == "processed" else ""),
    )
    conn.commit()
    conn.close()


def record_payment_record(user_id, stripe_event_id="", stripe_session_id="", stripe_customer_id="", stripe_subscription_id="", invoice_id="", payment_intent_id="", amount=None, currency="usd", status="succeeded", payment_type="stripe", manual=False, metadata=None):
    if not user_id:
        return
    if amount is None and status == "succeeded" and payment_type == "stripe":
        amount = 14.99
    conn = db()
    cur = conn.cursor()
    if stripe_event_id:
        cur.execute(
            "SELECT id FROM payment_records WHERE stripe_event_id=? AND status=? LIMIT 1",
            (stripe_event_id, status),
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            logging.info("PAYMENT_RECORD_CREATED skipped_duplicate user_id=%s stripe_event_id=%s status=%s", user_id, stripe_event_id, status)
            return existing[0]
    if invoice_id:
        cur.execute(
            "SELECT id FROM payment_records WHERE invoice_id=? AND status=? LIMIT 1",
            (invoice_id, status),
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            logging.info("PAYMENT_RECORD_CREATED skipped_duplicate user_id=%s invoice_id=%s status=%s", user_id, invoice_id, status)
            return existing[0]
    if payment_intent_id:
        cur.execute(
            "SELECT id FROM payment_records WHERE payment_intent_id=? AND status=? LIMIT 1",
            (payment_intent_id, status),
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            logging.info("PAYMENT_RECORD_CREATED skipped_duplicate user_id=%s payment_intent_id=%s status=%s", user_id, payment_intent_id, status)
            return existing[0]
    cur.execute(
        """
        INSERT INTO payment_records
        (user_id, stripe_event_id, stripe_session_id, stripe_customer_id, stripe_subscription_id, invoice_id, payment_intent_id, amount, currency, status, payment_type, manual, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            stripe_event_id or "",
            stripe_session_id or "",
            stripe_customer_id or "",
            stripe_subscription_id or "",
            invoice_id or "",
            payment_intent_id or "",
            amount,
            (currency or "usd").upper(),
            status,
            payment_type,
            1 if manual else 0,
            json.dumps(metadata or {})[:4000],
            datetime.now().isoformat(),
        ),
    )
    payment_record_id = cur.lastrowid
    cur.execute(
        """
        INSERT INTO transactions
        (user_id, stripe_event_id, stripe_customer_id, stripe_subscription_id, amount, currency, status, transaction_type, manual, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            stripe_event_id or "",
            stripe_customer_id or "",
            stripe_subscription_id or "",
            amount,
            (currency or "usd").upper(),
            status,
            payment_type,
            1 if manual else 0,
            json.dumps(metadata or {})[:4000],
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    logging.info(
        "PAYMENT_RECORD_CREATED user_id=%s stripe_event_id=%s session_id=%s invoice_id=%s amount=%s currency=%s status=%s",
        user_id,
        stripe_event_id,
        stripe_session_id,
        invoice_id,
        amount,
        currency,
        status,
    )
    logging.info(
        "STRIPE_PAYMENT_RECORD_CREATED payment_record_id=%s user_id=%s stripe_event_id=%s customer_id=%s subscription_id=%s amount=%s",
        payment_record_id,
        user_id,
        stripe_event_id,
        stripe_customer_id,
        stripe_subscription_id,
        amount,
    )
    logging.info("ADMIN_TRANSACTION_SYNCED user_id=%s payment_record_id=%s amount=%s currency=%s status=%s", user_id, payment_record_id, amount, currency, status)
    return payment_record_id


def record_unmatched_payment(event, stripe_object, reason):
    event = event or {}
    stripe_object = stripe_object or {}
    customer_details = stripe_object.get("customer_details") or {}
    customer_email = (
        customer_details.get("email")
        or stripe_object.get("customer_email")
        or stripe_object.get("customer_email_address")
        or stripe_object.get("email")
        or ""
    )
    amount = stripe_object.get("amount_total") or stripe_object.get("amount_paid")
    amount = amount / 100 if amount else None
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO unmatched_payments
        (stripe_event_id, event_type, stripe_object_id, customer_id, customer_email, amount, currency, reason, payload_summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.get("id") or "",
            event.get("type") or "",
            stripe_object.get("id") or "",
            stripe_object.get("customer") or "",
            customer_email,
            amount,
            (stripe_object.get("currency") or "usd").upper(),
            reason[:500],
            json.dumps({
                "client_reference_id": stripe_object.get("client_reference_id"),
                "metadata": stripe_object.get("metadata") or {},
                "subscription": stripe_object.get("subscription"),
                "payment_status": stripe_object.get("payment_status"),
            })[:2000],
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    logging.warning("Stripe payment received but no matching CoinPilotXAI account found. event_id=%s object_id=%s reason=%s", event.get("id"), stripe_object.get("id"), reason)


@webhook_app.route("/stripe-webhook", methods=["GET"])
@webhook_app.route("/stripe/webhook", methods=["GET"])
def stripe_webhook_health():
    return jsonify({
        "ok": True,
        "route": "stripe-webhook",
        "webhook_secret_configured": bool(STRIPE_WEBHOOK_SECRET),
    })


@webhook_app.route("/stripe-webhook", methods=["POST"])
@webhook_app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    init_db()
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    logging.info("STRIPE_WEBHOOK_RECEIVED path=%s payload_bytes=%s signature_present=%s", request.path, len(payload or b""), bool(sig_header))

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload,
                sig_header,
                STRIPE_WEBHOOK_SECRET
            )
            logging.info("STRIPE_SIGNATURE_VERIFIED signature_present=%s", bool(sig_header))
        else:
            logging.warning("STRIPE_WEBHOOK_SECRET missing. Stripe webhook is parsing unsigned payload; configure the Railway variable immediately.")
            event = json.loads(payload.decode("utf-8"))
    except Exception as e:
        logging.exception("Stripe webhook error details: %s", e)
        return "Invalid", 400

    event_type = event.get("type")
    if not event_type or not isinstance(event.get("data"), dict):
        logging.warning("Stripe webhook invalid event payload event_id=%s event_type=%s", event.get("id"), event_type)
        return "Invalid", 400

    logging.info("STRIPE_EVENT_RECEIVED event_type=%s event_id=%s", event_type, event.get("id"))
    logging.info("STRIPE_EVENT_TYPE event_type=%s event_id=%s", event_type, event.get("id"))
    logging.info("stripe webhook received event_type=%s event_id=%s", event_type, event.get("id"))
    event_id = event.get("id", "")
    if stripe_event_processed(event_id):
        logging.info("Stripe webhook duplicate skipped event_id=%s event_type=%s", event_id, event.get("type"))
        return "OK", 200
    resolved_event_user_id = None

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session.get("id")
        logging.info("STRIPE_USER_MATCH_START event_id=%s object_id=%s customer_id=%s", event_id, session_id, session.get("customer"))
        user_id, resolved_email = resolve_checkout_session_user(session)
        payment_status = session.get("payment_status")
        subscription_id = session.get("subscription")
        customer_id = session.get("customer")
        metadata = session.get("metadata") or {}
        logging.info(
            "checkout.session.completed received event_id=%s session_id=%s customer_id=%s customer_email=%s client_reference_id=%s metadata_user_id=%s payment_status=%s subscription_id=%s resolved_user_id=%s",
            event_id,
            session_id,
            customer_id,
            bool(resolved_email),
            session.get("client_reference_id"),
            metadata.get("user_id"),
            payment_status,
            subscription_id,
            user_id,
        )
        if user_id:
            logging.info("STRIPE_USER_MATCHED event_id=%s user_id=%s session_id=%s", event_id, user_id, session_id)
            resolved_event_user_id = user_id
            customer_details = session.get("customer_details") or {}
            customer_email = customer_details.get("email") or session.get("customer_email") or resolved_email
            if customer_email and is_valid_email(customer_email):
                save_user_email(user_id, customer_email)
            subscription = fetch_stripe_subscription(subscription_id) if subscription_id else None
            period_end = stripe_period_end_to_iso((subscription or {}).get("current_period_end")) if subscription else None
            if payment_status == "paid" or subscription_id:
                timestamp = datetime.now().isoformat()
                logging.info("STRIPE_PRO_ACTIVATION_START event_id=%s user_id=%s session_id=%s subscription_id=%s", event_id, user_id, session_id, subscription_id)
                activated_user_id = activate_pro(
                    user_id,
                    payment_type="stripe",
                    stripe_customer_id=customer_id,
                    stripe_session_id=session_id,
                    stripe_subscription_id=subscription_id,
                    subscription_status="active" if payment_status == "paid" else ((subscription or {}).get("status") or "active"),
                    pro_expires_at=period_end,
                )
                logging.info("STRIPE_PRO_ACTIVATION_COMMITTED event_id=%s user_id=%s activated_user_id=%s", event_id, user_id, activated_user_id)
                logging.info("STRIPE_USER_UPGRADED event_id=%s user_id=%s activated_user_id=%s", event_id, user_id, activated_user_id)
                save_payment_verification(
                    user_id,
                    txid=session_id,
                    payment_type="stripe",
                    amount=(session.get("amount_total") / 100 if session.get("amount_total") else None),
                    status="verified",
                    details=f"Stripe checkout.session.completed at {timestamp}",
                )
                record_payment_record(
                    activated_user_id or user_id,
                    stripe_event_id=event_id,
                    stripe_session_id=session_id,
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    payment_intent_id=session.get("payment_intent") or "",
                    amount=(session.get("amount_total") / 100 if session.get("amount_total") else None),
                    currency=session.get("currency", "usd"),
                    status="succeeded",
                )
                logging.info("TRANSACTION_CREATED event_id=%s user_id=%s session_id=%s amount=%s", event_id, activated_user_id or user_id, session_id, session.get("amount_total"))
                if activated_user_id:
                    send_telegram_confirmation(
                        activated_user_id,
                        "✅ CoinPilotX Pro activated successfully.\n\n"
                        "Your card payment was confirmed by Stripe, and your Pro access is now active.\n\n"
                        "Educational only — not financial advice.\n"
                        "CoinPilotX will never ask for your seed phrase or private key."
                    )
                user = load_account_by_id(activated_user_id or user_id)
                if user and has_pro_access(user):
                    logging.info("PAYMENT_SUCCESS_VERIFIED event_id=%s user_id=%s", event_id, user["user_id"])
                    send_successful_payment_email_bundle(user, {
                        "stripe_event_id": event_id,
                        "stripe_session_id": session_id,
                        "payment_id": session_id or session.get("payment_intent") or "",
                        "amount": (session.get("amount_total") / 100 if session.get("amount_total") else None),
                        "currency": session.get("currency", "usd"),
                        "billing_date": datetime.now().strftime("%b %d, %Y"),
                        "next_billing_date": format_date(period_end) if period_end else "",
                    })
                    logging.info("upgrade confirmation email sent/skipped after successful DB Pro update user_id=%s event_id=%s", user["user_id"], event_id)
                logging.info("Checkout session activated Pro user_id=%s session_id=%s database_commit_success=%s", user_id, session_id, bool(activated_user_id))
            else:
                save_payment_verification(
                    user_id,
                    txid=session_id,
                    payment_type="stripe",
                    amount=None,
                    status=f"not_activated_{payment_status or 'unknown'}",
                    details="Stripe checkout completed but payment_status was not paid.",
                )
                logging.warning("Stripe session not activated user_id=%s session_id=%s payment_status=%s", user_id, session_id, payment_status)
        else:
            logging.error("STRIPE_USER_NOT_FOUND event_id=%s session_id=%s customer_id=%s email_present=%s", event_id, session_id, customer_id, bool(resolved_email))
            logging.error("checkout.session.completed could not resolve local user session_id=%s customer_id=%s email=%s", session_id, customer_id, bool(resolved_email))
            record_unmatched_payment(event, session, "checkout.session.completed could not resolve local user")

    if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        subscription_object = event["data"]["object"]
        logging.info("STRIPE_USER_MATCH_START event_id=%s object_id=%s customer_id=%s", event_id, subscription_object.get("id"), subscription_object.get("customer"))
        synced_user_id = sync_stripe_subscription(event["data"]["object"])
        if synced_user_id:
            resolved_event_user_id = synced_user_id
        if synced_user_id and event_type in {"customer.subscription.created", "customer.subscription.updated"}:
            subscription = event["data"]["object"]
            if (subscription.get("status") or "").lower() == "active":
                user = load_account_by_id(synced_user_id)
                if user:
                    logging.info("PAYMENT_SUCCESS_VERIFIED subscription_event=%s user_id=%s", event_id, synced_user_id)
                    send_successful_payment_email_bundle(user, {
                        "stripe_event_id": event_id,
                        "stripe_session_id": subscription.get("latest_invoice") or subscription.get("id"),
                        "payment_id": subscription.get("latest_invoice") or subscription.get("id"),
                        "next_billing_date": format_date(stripe_period_end_to_iso(subscription.get("current_period_end"))),
                    })
                    logging.info("upgrade confirmation email sent/skipped for subscription event user_id=%s event_id=%s", synced_user_id, event_id)
        if synced_user_id and event_type == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            user = load_account_by_id(synced_user_id)
            if user:
                send_subscription_canceled_email(user, {
                    "access_until": format_date(stripe_period_end_to_iso(subscription.get("current_period_end"))),
                    "stripe_event_id": event_id,
                    "subscription_id": subscription.get("id") or "",
                })
        if not synced_user_id:
            logging.error("STRIPE_USER_NOT_FOUND event_id=%s object_id=%s customer_id=%s", event_id, event["data"]["object"].get("id"), event["data"]["object"].get("customer"))
            record_unmatched_payment(event, event["data"]["object"], "subscription event could not resolve local user")

    if event_type in {"invoice.paid", "invoice.payment_succeeded", "invoice.payment_failed"}:
        invoice_object = event["data"]["object"]
        logging.info("STRIPE_USER_MATCH_START event_id=%s object_id=%s customer_id=%s", event_id, invoice_object.get("id"), invoice_object.get("customer"))
        synced_user_id = sync_stripe_invoice(event["data"]["object"], event_type)
        if synced_user_id:
            resolved_event_user_id = synced_user_id
        if synced_user_id and event_type in {"invoice.paid", "invoice.payment_succeeded"}:
            invoice = event["data"]["object"]
            user = load_account_by_id(synced_user_id)
            if user:
                amount = invoice.get("amount_paid")
                record_payment_record(
                    synced_user_id,
                    stripe_event_id=event_id,
                    stripe_customer_id=invoice.get("customer") or "",
                    stripe_subscription_id=invoice.get("subscription") or "",
                    invoice_id=invoice.get("id") or "",
                    payment_intent_id=invoice.get("payment_intent") or "",
                    amount=(amount / 100 if amount else None),
                    currency=invoice.get("currency", "usd"),
                    status="succeeded",
                )
                logging.info("PAYMENT_SUCCESS_VERIFIED invoice_event=%s user_id=%s", event_id, synced_user_id)
                send_successful_payment_email_bundle(user, {
                    "stripe_event_id": event_id,
                    "stripe_session_id": invoice.get("id"),
                    "invoice_id": invoice.get("id"),
                    "payment_id": invoice.get("id") or invoice.get("payment_intent") or "",
                    "amount": (amount / 100 if amount else None),
                    "currency": invoice.get("currency", "usd"),
                    "billing_date": datetime.now().strftime("%b %d, %Y"),
                })
                logging.info("upgrade confirmation email sent/skipped for invoice event user_id=%s event_id=%s", synced_user_id, event_id)
        if synced_user_id and event_type == "invoice.payment_failed":
            invoice = event["data"]["object"]
            user = load_account_by_id(synced_user_id)
            if user:
                send_payment_issue_email(user, {"invoice_id": invoice.get("id"), "stripe_event_id": event_id})
        if not synced_user_id:
            logging.error("STRIPE_USER_NOT_FOUND event_id=%s object_id=%s customer_id=%s", event_id, event["data"]["object"].get("id"), event["data"]["object"].get("customer"))
            record_unmatched_payment(event, event["data"]["object"], "invoice event could not resolve local user")

    record_stripe_event(event, "processed", resolved_event_user_id)
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
    user = load_account_by_id(user_id)
    if user:
        sync_brevo_contact_safe({**user, "source": "telegram_email"}, entity_type="user", entity_id=user_id)


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
    subject_lower = (subject or "").lower()
    email_type = "transactional"
    if "welcome" in subject_lower:
        email_type = "welcome"
    elif "reset" in subject_lower:
        email_type = "password_reset"
    elif "password was changed" in subject_lower:
        email_type = "password_changed"
    elif "upgrade" in subject_lower or "subscription" in subject_lower:
        email_type = "subscription"
    elif "verify" in subject_lower:
        email_type = "email_verification"
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO email_logs (user_id, email, recipient_email, email_type, subject, status, provider, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, email, email, email_type, subject, status, (os.getenv("EMAIL_PROVIDER") or ("brevo" if os.getenv("BREVO_API_KEY") else "auto")), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def queue_failed_email(user_id, recipient_email, email_type, subject, html_body="", text_body="", error_message="", metadata=None):
    try:
        conn = db()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        cur.execute(
            """
            INSERT INTO failed_email_queue
            (user_id, recipient_email, email_type, subject, html_body, text_body, metadata, status, retry_count, last_error, next_retry_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
            """,
            (
                user_id or 0,
                recipient_email or "",
                email_type or "transactional",
                subject or "",
                html_body or "",
                text_body or "",
                json.dumps(metadata or {}),
                str(error_message or "")[:1000],
                (datetime.now() + timedelta(minutes=5)).isoformat(),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.warning("failed email queue write failed safely: %s", exc)


def send_email_confirmation(user_id, to_email, subject, body):
    logging.info("Attempting transactional confirmation email for user_id: %s", user_id)
    return send_platform_email(to_email, subject, body, body.replace("\n", "<br>"), user_id)


def email_sender_identity():
    config = email_service_service.sender_config()
    return config["email"], config["name"]


def send_platform_email(to_email, subject, text_body, html_body="", user_id=None):
    send_platform_email.last_response = ""
    send_platform_email.last_error = ""
    if not to_email:
        log_email_status(user_id or 0, "", subject, "skipped_no_email")
        send_platform_email.last_error = "skipped_no_email"
        return False
    from_email, from_name = email_sender_identity()
    brevo_key = os.getenv("BREVO_API_KEY")
    logging.info(
        "Email send requested: provider=brevo user_id=%s to_domain=%s from=%s brevo_key_loaded=%s",
        user_id or 0,
        to_email.split("@")[-1] if "@" in to_email else "invalid",
        from_email,
        bool(brevo_key),
    )
    try:
        result = email_service_service.send_email(
            to_email,
            subject,
            html_body or text_body.replace("\n", "<br>"),
            text_body=text_body,
            user_id=user_id,
        )
        ok = bool(result.get("ok"))
        send_platform_email.last_response = f"brevo_status={result.get('status_code')} body={json.dumps(result.get('response') or {})[:1200]}"
        send_platform_email.last_error = str(result.get("error") or "")[:1000]
        logging.info("Brevo API response status_code=%s body=%s", result.get("status_code"), json.dumps(result.get("response") or {})[:1200])
        message_id = result.get("message_id") or ""
        log_email_status(user_id or 0, to_email, subject, f"sent_brevo:{message_id}" if ok and message_id else ("sent_brevo" if ok else f"failed_brevo_{result.get('status_code') or 'not_configured'}"))
        if not ok:
            queue_failed_email(user_id or 0, to_email, "transactional", subject, html_body, text_body, send_platform_email.last_error)
        return ok
    except Exception as exc:
        logging.info("Platform email failed: %s", exc)
        log_email_status(user_id or 0, to_email, subject, "failed")
        send_platform_email.last_error = str(exc)[:1000]
        queue_failed_email(user_id or 0, to_email, "transactional", subject, html_body, text_body, send_platform_email.last_error)
        return False


def send_channel_email(to_email, subject, html_body, text_body="", user_id=0, email_type="transactional", channel="transactional"):
    result = email_service_service.send_email(
        to_email,
        subject,
        html_body,
        text_body=text_body or clean_html(html_body),
        email_type=email_type,
        user_id=user_id,
        channel=channel,
    )
    status = "sent_brevo"
    if result.get("message_id"):
        status += f":{result.get('message_id')}"
    if not result.get("ok"):
        status = f"failed_brevo_{result.get('status_code') or 'not_configured'}"
        queue_failed_email(user_id or 0, to_email, email_type, subject, html_body, text_body, result.get("error") or "", {"channel": channel})
    log_email_status(user_id or 0, to_email, subject, status)
    return bool(result.get("ok"))


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
    trial_end = user.get("trial_end_date") or user.get("pro_expires_at")
    trial_end_label = format_date(trial_end)
    logging.info("Attempting welcome email for user_id: %s recipient=%s", user.get("user_id"), audit_label)
    logging.info("Brevo API key loaded: %s", bool(os.getenv("BREVO_API_KEY")))
    subject = "Welcome to CoinPilotX Pro Trial — Powered by CoinPilotXAI Inc."
    text = (
        f"Hi {name},\n\n"
        "Welcome to CoinPilotX. Your account includes 30 days of free Pro access.\n\n"
        f"Trial end date: {trial_end_label}\n\n"
        "Pro features unlocked during your trial:\n"
        "- Deeper AI crypto intelligence\n"
        "- Premium Sports Edge context\n"
        "- Wallet and transaction risk details\n"
        "- Portfolio decision support\n"
        "- Whale intelligence and deeper market analysis\n"
        "- Advanced scam protection and saved intelligence workflows\n\n"
        "You can upgrade before the trial ends to keep Pro active. If you do not subscribe, your account automatically returns to Free access after the trial.\n\n"
        "You can access your dashboard at https://coinpilotx.app/account and connect Telegram from Account Settings.\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.\n"
        "Educational AI intelligence only. Not financial, betting, investment, or legal advice.\n\n"
        "Support: support@coinpilotx.app"
    )
    html = branded_email_html("Welcome to CoinPilotX Pro Trial", f"""
      <p>Hi {clean_html(name)},</p>
      <p>Your CoinPilotX account includes <strong>30 days of free Pro access</strong>.</p>
      <p><strong>Trial end date:</strong> {clean_html(trial_end_label)}</p>
      <p>During your trial, Pro unlocks deeper AI crypto intelligence, premium Sports Edge context, wallet and transaction risk details, portfolio decision support, whale intelligence, deeper market analysis, advanced scam protection, and saved intelligence workflows.</p>
      <p>You can upgrade before the trial ends to keep Pro active. If you do not subscribe, your account automatically returns to Free access after the trial.</p>
      <p><a href="https://coinpilotx.app/account" style="color:#36e58f">Open your account dashboard</a></p>
      <p>Support: <a href="mailto:support@coinpilotx.app" style="color:#6edff6">support@coinpilotx.app</a></p>
    """)
    sent = send_platform_email(to_email, subject, text, html, user.get("user_id"))
    if audit_label.startswith("new_user"):
        record_trial_email_event(user.get("user_id"), "day_1_welcome", to_email, "sent" if sent else "failed")
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


def send_password_changed_email(user):
    subject = "Your CoinPilotX password was changed"
    text = (
        f"Hi {account_display_name(user)},\n\n"
        "Your CoinPilotX account password was changed successfully.\n\n"
        "If you did not make this change, contact support immediately at support@coinpilotx.app.\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords."
    )
    html = branded_email_html("Your CoinPilotX password was changed", f"""
      <p>Hi {clean_html(account_display_name(user))},</p>
      <p>Your account password was changed successfully.</p>
      <p>If you did not make this change, contact <a href="mailto:support@coinpilotx.app" style="color:#6edff6">support@coinpilotx.app</a> immediately.</p>
    """)
    return send_platform_email(user.get("email"), subject, text, html, user.get("user_id"))


def send_username_recovery_email(user):
    subject = "Your CoinPilotX account login"
    text = (
        f"Hi {account_display_name(user)},\n\n"
        "You requested help finding your CoinPilotX account login.\n\n"
        f"Login email: {user.get('email')}\n"
        f"Display name: {account_display_name(user)}\n\n"
        "Login: https://coinpilotx.app/login\n\n"
        "If you did not request this, you can ignore this email."
    )
    html = branded_email_html("Your CoinPilotX account login", f"""
      <p>Hi {clean_html(account_display_name(user))},</p>
      <p>You requested help finding your account login.</p>
      <p><strong>Login email:</strong> {clean_html(user.get('email') or '')}<br>
      <strong>Display name:</strong> {clean_html(account_display_name(user))}</p>
      <p><a href="https://coinpilotx.app/login" style="color:#36e58f">Log in</a></p>
    """)
    return send_platform_email(user.get("email"), subject, text, html, user.get("user_id"))


def send_email_verification(user, verification_link):
    subject = "Verify your CoinPilotX email"
    text = f"Verify your CoinPilotX email here: {verification_link}\n\nSupport: support@coinpilotx.app"
    html = branded_email_html("Verify your CoinPilotX email", f'<p><a href="{verification_link}" style="color:#36e58f">Verify email</a></p>')
    return send_platform_email(user.get("email"), subject, text, html, user.get("user_id"))


def send_trial_lifecycle_email(user, event_type):
    if not user or not user.get("email") or trial_email_sent(user.get("user_id"), event_type):
        return False
    trial_end_label = format_date(user.get("trial_end_date") or user.get("pro_expires_at"))
    days_left = days_until(user.get("trial_end_date") or user.get("pro_expires_at"))
    copy = {
        "day_7": (
            "Make the most of your CoinPilotX Pro trial",
            "You still have Pro access. Try deeper AI analysis, Wallet Intel, Scam Shield, Portfolio Advice, whale intelligence, and Sports Edge context while your trial is active.",
        ),
        "day_21": (
            "Your CoinPilotX Pro trial expires soon",
            f"Your Pro trial is scheduled to end on {trial_end_label}. Upgrade before then if you want to keep deeper intelligence active.",
        ),
        "day_29": (
            "Final reminder: CoinPilotX Pro trial ending",
            f"Your Pro trial is close to ending. Days remaining: {days_left if days_left is not None else 'soon'}. You can upgrade anytime from your CoinPilotXAI website account dashboard.",
        ),
        "trial_ended": (
            "Your CoinPilotX Pro trial has ended",
            "Your account has returned to Free access. You can keep using CoinPilotX, and you can upgrade anytime when deeper intelligence becomes useful.",
        ),
    }
    subject, message = copy.get(event_type, copy["day_7"])
    text = (
        f"Hi {account_display_name(user)},\n\n"
        f"{message}\n\n"
        f"Trial end date: {trial_end_label}\n\n"
        "Upgrade or review your account:\n"
        "https://coinpilotx.app/account\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.\n"
        "Educational AI intelligence only. Not financial, betting, investment, or legal advice.\n\n"
        "Support: support@coinpilotx.app"
    )
    html = branded_email_html(subject, f"""
      <p>Hi {clean_html(account_display_name(user))},</p>
      <p>{clean_html(message)}</p>
      <p><strong>Trial end date:</strong> {clean_html(trial_end_label)}</p>
      <p><a href="https://coinpilotx.app/account" style="color:#36e58f">Open your account</a></p>
    """)
    sent = send_platform_email(user.get("email"), subject, text, html, user.get("user_id"))
    record_trial_email_event(user.get("user_id"), event_type, user.get("email"), "sent" if sent else "failed")
    return sent


def payment_email_already_sent(stripe_event_id="", payment_id="", email_type=""):
    if not email_type:
        return False
    conn = db()
    cur = conn.cursor()
    if stripe_event_id:
        cur.execute(
            """
            SELECT id FROM payment_email_logs
            WHERE stripe_event_id=? AND email_type=? AND status='sent'
            LIMIT 1
            """,
            (stripe_event_id, email_type),
        )
        if cur.fetchone():
            conn.close()
            return True
    if payment_id:
        cur.execute(
            """
            SELECT id FROM payment_email_logs
            WHERE payment_id=? AND email_type=? AND status='sent'
            LIMIT 1
            """,
            (payment_id, email_type),
        )
        if cur.fetchone():
            conn.close()
            return True
    conn.close()
    return False


def record_payment_email_attempt(user_id, email, stripe_event_id="", payment_id="", email_type="", status="pending", provider_response="", error_message="", retry_count=0):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payment_email_logs
        (user_id, email, stripe_event_id, payment_id, email_type, template, status, provider_response, error_message, retry_count, created_at, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id or 0,
            email or "",
            stripe_event_id or "",
            payment_id or "",
            email_type or "",
            email_type or "",
            status,
            str(provider_response or "")[:4000],
            str(error_message or "")[:1000],
            int(retry_count or 0),
            datetime.now().isoformat(),
            datetime.now().isoformat() if status == "sent" else "",
        ),
    )
    conn.commit()
    conn.close()


def payment_email_copy(user, details, email_type):
    details = details or {}
    amount = details.get("amount")
    currency = (details.get("currency") or "USD").upper()
    amount_line = f"Payment amount: {amount} {currency}\n" if amount else ""
    billing_date = details.get("billing_date") or datetime.now().strftime("%b %d, %Y")
    next_billing_date = details.get("next_billing_date") or details.get("pro_expires_at") or "Available in your Stripe billing details"
    dashboard = "https://coinpilotx.app/dashboard"
    account = "https://coinpilotx.app/account"
    support = "https://coinpilotx.app/support"
    subject_map = {
        "pro_activated": "Your CoinPilotXAI Pro Access Is Active",
        "payment_successful": "CoinPilotXAI Payment Successful",
        "receipt_invoice": "Your CoinPilotXAI Receipt and Billing Details",
        "payment_failed": "Action needed: CoinPilotXAI Pro payment issue",
        "subscription_canceled": "CoinPilotXAI Pro subscription update",
        "trial_ending": "Your CoinPilotXAI Pro trial is ending soon",
    }
    intro_map = {
        "pro_activated": "Your CoinPilotXAI Pro access is active.",
        "payment_successful": "Stripe confirmed your CoinPilotXAI Pro payment successfully.",
        "receipt_invoice": "Your CoinPilotXAI Pro billing details are below.",
        "payment_failed": "Stripe reported a payment issue for your CoinPilotXAI Pro subscription.",
        "subscription_canceled": "Your CoinPilotXAI Pro subscription status changed to canceled.",
        "trial_ending": "Your CoinPilotXAI Pro trial is ending soon.",
    }
    subject = subject_map.get(email_type, "Your CoinPilotXAI Pro Upgrade Is Active")
    intro = intro_map.get(email_type, "Your CoinPilotXAI Pro access is active.")
    text = (
        f"Hi {account_display_name(user)},\n\n"
        f"{intro}\n\n"
        "Plan: CoinPilotX Pro\n"
        f"{amount_line}"
        f"Billing date: {billing_date}\n"
        f"Next billing date: {next_billing_date}\n\n"
        f"Dashboard: {dashboard}\n"
        f"Account: {account}\n"
        f"Support: {support}\n\n"
        "Telegram is optional. You can connect it from Account Settings if you want companion alerts.\n\n"
        "If you experience any issue after payment, please email us immediately at support@coinpilotx.app and include the email address used for your CoinPilotXAI account.\n\n"
        "CoinPilotXAI Inc. provides educational AI intelligence only. Not financial, betting, investment, or legal advice."
    )
    html = branded_email_html(subject, f"""
      <p>Hi {clean_html(account_display_name(user))},</p>
      <p>{clean_html(intro)}</p>
      <p><strong>Plan:</strong> CoinPilotX Pro<br>
      {f"<strong>Payment amount:</strong> {clean_html(str(amount))} {clean_html(currency)}<br>" if amount else ""}
      <strong>Billing date:</strong> {clean_html(str(billing_date))}<br>
      <strong>Next billing date:</strong> {clean_html(str(next_billing_date))}</p>
      <p><a href="{dashboard}" style="color:#36e58f">Open Dashboard</a> · <a href="{account}" style="color:#6edff6">Account</a> · <a href="{support}" style="color:#6edff6">Support</a></p>
      <p>Telegram is optional. Connect it from Account Settings if you want companion alerts.</p>
      <p>If you experience any issue after payment, please email us immediately at <a href="mailto:support@coinpilotx.app" style="color:#6edff6">support@coinpilotx.app</a> and include the email address used for your CoinPilotXAI account.</p>
    """)
    return subject, text, html


def send_payment_email_with_retry(user, details=None, email_type="pro_activated", force=False):
    details = details or {}
    user = user or {}
    user_id = user.get("user_id") or 0
    to_email = normalize_email(user.get("email") or details.get("email") or "")
    stripe_event_id = details.get("stripe_event_id") or ""
    payment_id = details.get("payment_id") or details.get("stripe_session_id") or details.get("invoice_id") or details.get("payment_intent_id") or ""
    logging.info("PAYMENT_EMAIL_START user_id=%s type=%s event=%s payment_id=%s", user_id, email_type, stripe_event_id, payment_id)
    logging.info("PAYMENT_EMAIL_QUEUED user_id=%s type=%s event=%s payment_id=%s", user_id, email_type, stripe_event_id, payment_id)
    if not force and payment_email_already_sent(stripe_event_id, payment_id, email_type):
        logging.info("PAYMENT_EMAIL_DUPLICATE_SKIPPED user_id=%s type=%s event=%s payment_id=%s", user_id, email_type, stripe_event_id, payment_id)
        return True
    record_payment_email_attempt(user_id, to_email, stripe_event_id, payment_id, email_type, "pending")
    if not to_email:
        record_payment_email_attempt(user_id, "", stripe_event_id, payment_id, email_type, "failed", error_message="No account email available.")
        logging.warning("PAYMENT_EMAIL_FAILED user_id=%s type=%s reason=no_email", user_id, email_type)
        return False
    subject, text, html = payment_email_copy(user, details, email_type)
    backoffs = [0]
    for attempt, delay in enumerate(backoffs):
        if delay:
            time.sleep(delay)
        sent = send_platform_email(to_email, subject, text, html, user_id)
        if sent:
            record_payment_email_attempt(user_id, to_email, stripe_event_id, payment_id, email_type, "sent", provider_response=getattr(send_platform_email, "last_response", "accepted") or "accepted", retry_count=attempt)
            logging.info("PAYMENT_EMAIL_SENT user_id=%s type=%s event=%s payment_id=%s retry=%s", user_id, email_type, stripe_event_id, payment_id, attempt)
            logging.info("PAYMENT_EMAIL_SUCCESS user_id=%s type=%s event=%s payment_id=%s", user_id, email_type, stripe_event_id, payment_id)
            return True
        status = "retried" if attempt < len(backoffs) - 1 else "failed"
        record_payment_email_attempt(user_id, to_email, stripe_event_id, payment_id, email_type, status, provider_response=getattr(send_platform_email, "last_response", ""), error_message=getattr(send_platform_email, "last_error", "") or "Email provider rejected or unavailable.", retry_count=attempt)
        queue_failed_email(user_id, to_email, email_type, subject, html, text, getattr(send_platform_email, "last_error", "") or "Email provider rejected or unavailable.", details)
        logging.warning("PAYMENT_EMAIL_FAILED user_id=%s type=%s event=%s payment_id=%s retry=%s", user_id, email_type, stripe_event_id, payment_id, attempt)
    return False


def send_successful_payment_email_bundle(user, details=None, force=False):
    details = details or {}
    results = {}
    for email_type in ("pro_activated", "payment_successful", "receipt_invoice"):
        results[email_type] = send_payment_email_with_retry(user, details, email_type=email_type, force=force)
    return results


def retry_failed_payment_emails_for_event(stripe_event_id):
    if not stripe_event_id:
        return 0
    try:
        conn = db()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM payment_email_logs
            WHERE stripe_event_id=? AND status IN ('pending','failed','retried')
            ORDER BY created_at ASC
            """,
            (stripe_event_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        retried = 0
        for row in rows:
            user = load_account_by_id(row.get("user_id"))
            if not user:
                continue
            sent = send_payment_email_with_retry(
                user,
                {"stripe_event_id": stripe_event_id, "payment_id": row.get("payment_id") or ""},
                email_type=row.get("email_type") or "payment_successful",
                force=True,
            )
            if sent:
                retried += 1
        if retried:
            logging.info("PAYMENT_EMAIL_RETRY duplicate_event=%s retried=%s", stripe_event_id, retried)
        return retried
    except Exception as exc:
        logging.warning("Payment email retry lookup failed event_id=%s error=%s", stripe_event_id, exc)
        return 0


def pro_upgrade_confirmation_already_sent(stripe_event_id="", stripe_session_id=""):
    conn = db()
    cur = conn.cursor()
    if stripe_event_id:
        cur.execute(
            "SELECT id FROM email_logs WHERE email_type='pro_upgrade_confirmation' AND stripe_event_id=? AND status LIKE 'sent%' LIMIT 1",
            (stripe_event_id,),
        )
        if cur.fetchone():
            conn.close()
            return True
    if stripe_session_id:
        cur.execute(
            "SELECT id FROM email_logs WHERE email_type='pro_upgrade_confirmation' AND stripe_session_id=? AND status LIKE 'sent%' LIMIT 1",
            (stripe_session_id,),
        )
        if cur.fetchone():
            conn.close()
            return True
    conn.close()
    return False


def recent_upgrade_confirmation_sent(user_id, minutes=10):
    if not user_id:
        return False
    cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM email_logs
        WHERE user_id=? AND email_type='pro_upgrade_confirmation' AND status LIKE 'sent%' AND created_at>=?
        LIMIT 1
        """,
        (user_id, cutoff),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row)


def log_upgrade_confirmation_email(user_id, email, stripe_event_id="", stripe_session_id="", status="sent", error_message=""):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO email_logs
        (user_id, email, subject, status, created_at, email_type, recipient_email, stripe_event_id, stripe_session_id, sent_at, error_message)
        VALUES (?, ?, ?, ?, ?, 'pro_upgrade_confirmation', ?, ?, ?, ?, ?)
        """,
        (
            user_id or 0,
            email or "",
            "Your CoinPilotXAI Pro Upgrade Is Active",
            status,
            datetime.now().isoformat(),
            email or "",
            stripe_event_id or "",
            stripe_session_id or "",
            datetime.now().isoformat() if str(status).startswith("sent") else "",
            error_message[:500],
        ),
    )
    conn.commit()
    conn.close()


def send_upgrade_confirmation_email(user, details=None):
    details = details or {}
    user = user or {}
    user_id = user.get("user_id") or 0
    to_email = user.get("email") or details.get("email")
    stripe_event_id = details.get("stripe_event_id") or ""
    stripe_session_id = details.get("stripe_session_id") or details.get("stripe_session") or ""
    logging.info("upgrade confirmation email requested user_id=%s event=%s session=%s", user_id, stripe_event_id, stripe_session_id)
    if pro_upgrade_confirmation_already_sent(stripe_event_id, stripe_session_id):
        logging.info("upgrade confirmation email skipped duplicate user_id=%s event=%s session=%s", user_id, stripe_event_id, stripe_session_id)
        return True
    if not to_email:
        log_upgrade_confirmation_email(user_id, "", stripe_event_id, stripe_session_id, "failed_no_email", "No recipient email on account.")
        return False
    sent = send_payment_email_with_retry(user, {**details, "payment_id": stripe_session_id}, email_type="pro_activated")
    log_upgrade_confirmation_email(user_id, to_email, stripe_event_id, stripe_session_id, "sent" if sent else "failed", "" if sent else "Email provider rejected or unavailable.")
    logging.info("upgrade confirmation email %s user_id=%s", "sent" if sent else "failed", user_id)
    return sent
    amount = details.get("amount")
    currency = (details.get("currency") or "USD").upper()
    amount_line = f"Payment amount: {amount} {currency}\n" if amount else ""
    billing_date = details.get("billing_date") or datetime.now().strftime("%b %d, %Y")
    next_billing_date = details.get("next_billing_date") or details.get("pro_expires_at") or "Available in your Stripe billing details"
    subject = "Your CoinPilotXAI Pro Upgrade Is Active"
    text = (
        f"Hi {account_display_name(user)},\n\n"
        "Your CoinPilotXAI Pro access is active.\n\n"
        "Plan: CoinPilotX Pro\n"
        f"{amount_line}"
        f"Billing date: {billing_date}\n"
        f"Next billing date: {next_billing_date}\n\n"
        "Dashboard: https://coinpilotx.app/dashboard\n"
        "Account: https://coinpilotx.app/account\n"
        "Support: https://coinpilotx.app/support\n"
        "Optional Telegram companion: https://t.me/DocShieldX_bot\n\n"
        "Telegram activation: log in at https://coinpilotx.app/account/settings, generate a Telegram code, then return to the bot and send /connect CODE.\n\n"
        "If you experience any issue after payment, please email us immediately at support@coinpilotx.app and include the email address used for your CoinPilotXAI account.\n\n"
        "CoinPilotXAI Inc. provides educational AI intelligence only. Not financial, betting, investment, or legal advice."
    )
    html = branded_email_html("Your CoinPilotXAI Pro Upgrade Is Active", f"""
      <p>Hi {clean_html(account_display_name(user))},</p>
      <p>Your <strong>CoinPilotX Pro</strong> access is active.</p>
      <p><strong>Plan:</strong> CoinPilotX Pro<br>
      {f"<strong>Payment amount:</strong> {clean_html(str(amount))} {clean_html(currency)}<br>" if amount else ""}
      <strong>Billing date:</strong> {clean_html(str(billing_date))}<br>
      <strong>Next billing date:</strong> {clean_html(str(next_billing_date))}</p>
      <p><a href="https://coinpilotx.app/dashboard" style="color:#36e58f">Open Dashboard</a> · <a href="https://coinpilotx.app/account" style="color:#6edff6">Account</a> · <a href="https://coinpilotx.app/support" style="color:#6edff6">Support</a></p>
      <p>Telegram activation: open Account Settings, generate a Telegram code, then return to the bot and send <strong>/connect CODE</strong>.</p>
      <p>If you experience any issue after payment, please email us immediately at <a href="mailto:support@coinpilotx.app" style="color:#6edff6">support@coinpilotx.app</a> and include the email address used for your CoinPilotXAI account.</p>
    """)
    sent = send_platform_email(to_email, subject, text, html, user_id)
    if not sent:
        logging.warning("upgrade confirmation email failed; retrying once after 3 seconds user_id=%s", user_id)
        time.sleep(3)
        sent = send_platform_email(to_email, subject, text, html, user_id)
    log_upgrade_confirmation_email(user_id, to_email, stripe_event_id, stripe_session_id, "sent" if sent else "failed", "" if sent else "Email provider rejected or unavailable.")
    logging.info("upgrade confirmation email %s user_id=%s", "sent" if sent else "failed", user_id)
    return sent


def send_payment_issue_email(user, details=None):
    details = details or {}
    user = user or {}
    if not user.get("email"):
        return False
    payment_log_sent = send_payment_email_with_retry(
        user,
        {
            "stripe_event_id": details.get("stripe_event_id") or "",
            "invoice_id": details.get("invoice_id") or "",
            "payment_id": details.get("invoice_id") or details.get("payment_id") or "",
            "billing_date": datetime.now().strftime("%b %d, %Y"),
        },
        email_type="payment_failed",
    )
    return payment_log_sent


def send_subscription_canceled_email(user, details=None):
    details = details or {}
    user = user or {}
    if not user.get("email"):
        return False
    payment_log_sent = send_payment_email_with_retry(
        user,
        {
            "stripe_event_id": details.get("stripe_event_id") or "",
            "payment_id": details.get("subscription_id") or details.get("stripe_subscription_id") or "subscription_canceled",
            "next_billing_date": details.get("access_until") or "",
        },
        email_type="subscription_canceled",
    )
    return payment_log_sent


def subscription_email_body(plan_name, timestamp, txid=None):
    txid_line = f"\nTXID reference: {txid}\n" if txid else ""
    return (
        "Welcome to CoinPilotX Pro.\n\n"
        "Your Pro access is active.\n\n"
        f"Plan: {plan_name}\n"
        f"Activated at: {timestamp}\n"
        f"{txid_line}"
        "Legal operator: CoinPilotXAI Inc.\n\n"
        "Open your CoinPilotXAI dashboard:\n"
        "https://coinpilotx.app/dashboard\n\n"
        "Optional Telegram companion:\n"
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


def expire_trials(send_email=True):
    init_db()
    now = datetime.now()
    now_iso = now.isoformat()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM users
        WHERE lower(COALESCE(plan,''))='pro'
          AND lower(COALESCE(subscription_status,''))='trialing'
          AND COALESCE(trial_end_date, pro_expires_at, '')!=''
          AND COALESCE(trial_end_date, pro_expires_at) < ?
        """,
        (now_iso,),
    )
    expired_trials = [dict(row) for row in cur.fetchall()]
    for user in expired_trials:
        cur.execute(
            """
            UPDATE users
            SET plan='free', subscription_plan='free', subscription_status='expired',
                is_pro=0, updated_at=?
            WHERE user_id=?
            """,
            (now_iso, user["user_id"]),
        )
        cur.execute(
            """
            INSERT INTO subscriptions
            (user_id, plan, status, payment_type, trial_start_date, trial_end_date, pro_expires_at, created_at, updated_at)
            VALUES (?, 'free', 'expired', 'trial', ?, ?, ?, ?, ?)
            """,
            (
                user["user_id"],
                user.get("trial_start_date"),
                user.get("trial_end_date"),
                user.get("pro_expires_at"),
                now_iso,
                now_iso,
            ),
        )
    cur.execute(
        """
        SELECT * FROM users
        WHERE lower(COALESCE(plan,''))='pro'
          AND lower(COALESCE(subscription_status,'')) IN ('canceled', 'past_due')
          AND COALESCE(pro_expires_at, subscription_expires_at, '')!=''
          AND COALESCE(pro_expires_at, subscription_expires_at) < ?
        """,
        (now_iso,),
    )
    expired_paid = [dict(row) for row in cur.fetchall()]
    for user in expired_paid:
        cur.execute(
            """
            UPDATE users
            SET plan='free', subscription_plan='free', subscription_status='expired',
                is_pro=0, updated_at=?
            WHERE user_id=?
            """,
            (now_iso, user["user_id"]),
        )
    conn.commit()
    conn.close()

    for user in expired_trials:
        log_product_event(user["user_id"], "pro_trial_expired", {"trial_end_date": user.get("trial_end_date")})
        if send_email:
            send_trial_lifecycle_email(user, "trial_ended")
        refreshed = load_account_by_id(user["user_id"])
        if refreshed:
            sync_brevo_contact_safe({**refreshed, "source": "trial_expired"}, entity_type="user", entity_id=user["user_id"])
    for user in expired_paid:
        log_product_event(user["user_id"], "pro_access_expired", {"pro_expires_at": user.get("pro_expires_at")})

    return {"expired_trials": len(expired_trials), "expired_paid": len(expired_paid)}


def send_due_trial_emails():
    init_db()
    now = datetime.now()
    conn = db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM users
        WHERE lower(COALESCE(plan,''))='pro'
          AND lower(COALESCE(subscription_status,''))='trialing'
          AND email!=''
          AND COALESCE(trial_start_date, '')!=''
          AND COALESCE(trial_end_date, '')!=''
        """
    )
    users = [dict(row) for row in cur.fetchall()]
    conn.close()
    sent = 0
    for user in users:
        start = parse_iso_datetime(user.get("trial_start_date"))
        end = parse_iso_datetime(user.get("trial_end_date"))
        if not start or not end:
            continue
        elapsed_days = (now - start).days
        days_left = math.ceil((end - now).total_seconds() / 86400)
        event_type = None
        if elapsed_days >= 7 and days_left > 9:
            event_type = "day_7"
        if days_left <= 9 and days_left > 1:
            event_type = "day_21"
        if days_left <= 1 and days_left >= 0:
            event_type = "day_29"
        if event_type and not trial_email_sent(user["user_id"], event_type):
            if send_trial_lifecycle_email(user, event_type):
                sent += 1
    return sent


def run_trial_maintenance(force=False):
    global TRIAL_MAINTENANCE_LAST_RUN
    now = time.time()
    if not force and (now - TRIAL_MAINTENANCE_LAST_RUN) < TRIAL_MAINTENANCE_INTERVAL_SECONDS:
        return
    TRIAL_MAINTENANCE_LAST_RUN = now
    try:
        expire_trials(send_email=True)
        send_due_trial_emails()
    except Exception as exc:
        logging.info("Trial maintenance failed: %s", exc)


def consume_ai_usage(user_id, feature="ai_assistant", limit=FREE_AI_DAILY_LIMIT):
    if not user_id or is_pro(user_id):
        return True, ""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT usage_ai_count, usage_reset_at FROM users WHERE user_id=? OR telegram_user_id=? LIMIT 1", (user_id, user_id))
    row = cur.fetchone()
    if not row:
        conn.close()
        return True, ""
    count = int(row[0] or 0)
    reset_at = row[1] or ""
    if reset_at != today:
        count = 0
    if count >= limit:
        conn.close()
        return False, (
            f"Free AI limit reached for today ({limit} requests). "
            "Upgrade to CoinPilotX Pro to continue with deeper AI intelligence, or try again tomorrow."
        )
    count += 1
    cur.execute("UPDATE users SET usage_ai_count=?, usage_reset_at=?, updated_at=? WHERE user_id=? OR telegram_user_id=?", (count, today, now.isoformat(), user_id, user_id))
    cur.execute(
        "INSERT INTO usage_events (user_id, feature, count, plan, metadata, created_at) VALUES (?, ?, 1, 'free', ?, ?)",
        (user_id, feature, json.dumps({"daily_count": count, "limit": limit}), now.isoformat()),
    )
    conn.commit()
    conn.close()
    return True, ""


def send_telegram_confirmation(user_id, text):
    if not BOT_TOKEN:
        return False
    chat_id = user_id
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT telegram_chat_id, telegram_user_id FROM users WHERE user_id=? OR telegram_user_id=? LIMIT 1",
            (user_id, user_id),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            chat_id = row[0] or row[1] or user_id
    except Exception as exc:
        logging.info("Telegram confirmation lookup failed: %s", exc)
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Main Menu", "callback_data": "main_menu"}]]
                },
            },
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

    add_column_if_missing(cur, "users", "is_pro", "INTEGER DEFAULT 0", conn=conn)

    cur.execute("UPDATE users SET is_pro=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


# =========================
# PRO MESSAGE (ADD HERE)
# =========================

BTC_PAYMENT_ADDRESS = os.getenv("BTC_PAYMENT_ADDRESS", BTC_PAYMENT_ADDRESS)
BTC_PRO_PRICE = "0.00025 BTC"

def pro_upgrade_message(user_id):
    account = get_linked_website_account(user_id)
    if platform_pro_access(account):
        return (
            "✅ Your CoinPilotXAI Pro access is already active.\n\n"
            "Open your dashboard anytime to use the full platform. Telegram is an optional companion for quick commands and alerts."
        )
    return (
        "⭐ CoinPilotX Pro\n\n"
        f"Card price: {PRO_PRICE_MONTHLY}\n"
        "Payments are completed securely on the CoinPilotXAI website.\n\n"
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
        "No hidden fees from CoinPilotXAI Inc. Checkout opens only on the website.\n"
        "CoinPilotX never holds funds.\n"
        "CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.\n\n"
        "Create or log in to your website account, upgrade there, then return here and send your Telegram activation code."
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
    return market_data_service.live_market_board(category=category, limit=limit)
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
    return scam_shield_service.analyze_text(text).get("response", "")
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
    db_status = db_service.health_check()
    market_ok = bool(market_data.get("markets"))
    sports_ok = sports_data.get("warning") is None and bool(sports_data.get("games"))
    openai_ok = bool(os.getenv("OPENAI_API_KEY"))
    status = {
        "updated_at": datetime.now().isoformat(),
        "website": "online",
        "database": "connected" if db_status.get("connected") else "database issue",
        "database_engine": db_status.get("db_engine"),
        "telegram_bot": "configured" if BOT_TOKEN else "bot token missing",
        "stripe_checkout": "configured" if STRIPE_SECRET_KEY and STRIPE_PRICE_ID else "missing Stripe secret or price id",
        "stripe_webhook": "configured" if STRIPE_WEBHOOK_SECRET else "webhook secret missing",
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
    payload = sports_data_service.live_sports_edge(league=league, limit=limit)
    return payload.get("games", [])[:limit], payload.get("source", "public scoreboard data"), payload.get("warning")
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
    return sports_data_service.risk_label(game)
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
    return sports_data_service.game_intelligence(game)
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
    payload = sports_data_service.live_sports_edge(league=league, limit=30)
    if game_id:
        normalized = game_id.replace("_", ":")
        game = next((item for item in payload.get("games", []) if item.get("id") == normalized or item.get("event_id") == game_id), None)
        if game:
            payload["selected_game"] = game
            payload["analysis"] = sports_data_service.game_analysis(game)
        else:
            payload["warning"] = "That game is no longer available in the live feed."
    return payload
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
    logging.info("TELEGRAM_COMMAND_RECEIVED command=text_message telegram_user_id=%s", update.effective_user.id)

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
    await update.message.reply_text(
        "⭐ Website Checkout Required\n\n"
        "Direct Telegram payment verification is no longer used. For your safety, Pro payments and subscription status are managed by your CoinPilotXAI website account.\n\n"
        "1. Open your website account.\n"
        "2. Upgrade to Pro on the website.\n"
        "3. Return here and send your activation code with /connect CODE.\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.",
        reply_markup=upgrade_payment_menu(update.effective_user.id),
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


_MIGRATION_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _migration_identifier(identifier):
    if not _MIGRATION_IDENTIFIER_RE.match(identifier or ""):
        raise ValueError(f"Unsafe migration identifier: {identifier}")
    return identifier


def _rollback_failed_migration(conn, label, exc):
    try:
        conn.rollback()
    except Exception as rollback_exc:
        logging.warning("MIGRATION_ERROR_ROLLBACK_FAILED label=%s rollback_error=%s", label, rollback_exc)
    logging.warning("MIGRATION_ERROR_ROLLED_BACK label=%s error=%s", label, exc)


def add_column_if_missing(cur, table, column, definition, conn=None):
    table_name = _migration_identifier(table)
    column_name = _migration_identifier(column)
    label = f"{table_name}.{column_name}"
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                  AND column_name = ?
                """,
                (table_name, column_name),
            )
            exists = bool(cur.fetchone())
        else:
            cur.execute(f"PRAGMA table_info({table_name})")
            exists = any((row.get("name") if hasattr(row, "get") else row["name"]) == column_name for row in cur.fetchall())

        if exists:
            logging.info("COLUMN_EXISTS_SKIPPED table=%s column=%s", table_name, column_name)
            return False

        if db_service.IS_POSTGRES:
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {definition}")
        else:
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        logging.info("COLUMN_ADDED table=%s column=%s definition=%s", table_name, column_name, definition)
        return True
    except Exception as exc:
        if conn is not None:
            _rollback_failed_migration(conn, label, exc)
        else:
            logging.warning("MIGRATION_ERROR_ROLLED_BACK label=%s error=%s", label, exc)
        return False


def add_columns_if_missing(cur, table, columns, conn=None):
    for column, definition in columns:
        add_column_if_missing(cur, table, column, definition, conn=conn)


def init_db():
    conn = db()
    if db_service.IS_POSTGRES and hasattr(conn, "set_autocommit"):
        conn.set_autocommit(True)
    cur = conn.cursor()
    logging.info(
        "MIGRATION_START engine=%s database_url_loaded=%s",
        db_service.ENGINE_NAME,
        db_service.DATABASE_URL_LOADED,
    )

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

    add_columns_if_missing(cur, "users", [
        ("is_pro", "INTEGER DEFAULT 0"),
        ("subscription_plan", "TEXT DEFAULT 'free'"),
        ("subscription_status", "TEXT DEFAULT 'inactive'"),
        ("subscription_started_at", "TEXT"),
        ("subscription_expires_at", "TEXT"),
        ("risk_profile", "TEXT DEFAULT 'balanced'"),
        ("preferred_exchange_goal", "TEXT DEFAULT 'beginner'"),
        ("stripe_customer_id", "TEXT"),
        ("stripe_session_id", "TEXT"),
        ("last_payment_type", "TEXT"),
        ("full_name", "TEXT"),
        ("password_hash", "TEXT"),
        ("phone", "TEXT"),
        ("country", "TEXT"),
        ("telegram_user_id", "INTEGER"),
        ("telegram_username", "TEXT"),
        ("telegram_chat_id", "INTEGER"),
        ("account_status", "TEXT DEFAULT 'active'"),
        ("email_verified", "INTEGER DEFAULT 0"),
        ("email_opt_in", "INTEGER DEFAULT 0"),
        ("sms_opt_in", "INTEGER DEFAULT 0"),
        ("plan", "TEXT DEFAULT 'free'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("last_login_at", "TEXT"),
        ("last_seen_at", "TEXT"),
        ("referral_code", "TEXT"),
        ("referred_by", "TEXT"),
        ("trial_start_date", "TEXT"),
        ("trial_end_date", "TEXT"),
        ("trial_status", "TEXT"),
        ("trial_used", "INTEGER DEFAULT 0"),
        ("stripe_subscription_id", "TEXT"),
        ("pro_expires_at", "TEXT"),
        ("usage_ai_count", "INTEGER DEFAULT 0"),
        ("usage_reset_at", "TEXT"),
        ("marketing_email_opt_in", "INTEGER DEFAULT 0"),
        ("notification_email_opt_in", "INTEGER DEFAULT 1"),
        ("security_email_opt_in", "INTEGER DEFAULT 1"),
        ("payment_receipt_opt_in", "INTEGER DEFAULT 1"),
        ("deleted_at", "TEXT"),
        ("restricted_reason", "TEXT"),
        ("suspended_reason", "TEXT"),
    ], conn=conn)

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
    add_columns_if_missing(cur, "portfolio_snapshots", [
        ("total_cost", "REAL"),
        ("pnl_value", "REAL"),
        ("pnl_percent", "REAL"),
    ], conn=conn)
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
    add_columns_if_missing(cur, "crypto_news_cache", [
        ("tags", "TEXT"),
        ("affected_assets", "TEXT"),
        ("ai_summary", "TEXT"),
        ("confidence", "REAL DEFAULT 0.58"),
    ], conn=conn)
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
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        stripe_event_id TEXT,
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        amount REAL,
        currency TEXT,
        status TEXT,
        transaction_type TEXT,
        created_at TEXT
    )
    """)
    add_columns_if_missing(cur, "transactions", [
        ("manual", "INTEGER DEFAULT 0"),
        ("metadata", "TEXT"),
    ], conn=conn)
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
    add_columns_if_missing(cur, "email_logs", [
        ("email_type", "TEXT"),
        ("recipient_email", "TEXT"),
        ("stripe_event_id", "TEXT"),
        ("stripe_session_id", "TEXT"),
        ("sent_at", "TEXT"),
        ("error_message", "TEXT"),
        ("provider", "TEXT"),
        ("provider_message_id", "TEXT"),
        ("metadata", "TEXT"),
    ], conn=conn)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS failed_email_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        recipient_email TEXT,
        email_type TEXT,
        subject TEXT,
        html_body TEXT,
        text_body TEXT,
        metadata TEXT,
        status TEXT DEFAULT 'pending',
        retry_count INTEGER DEFAULT 0,
        last_error TEXT,
        next_retry_at TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_email_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        stripe_event_id TEXT,
        payment_id TEXT,
        email_type TEXT,
        status TEXT,
        provider_response TEXT,
        error_message TEXT,
        retry_count INTEGER DEFAULT 0,
        created_at TEXT,
        sent_at TEXT
    )
    """)
    add_columns_if_missing(cur, "payment_email_logs", [
        ("retry_count", "INTEGER DEFAULT 0"),
        ("provider_response", "TEXT"),
        ("error_message", "TEXT"),
        ("template", "TEXT"),
    ], conn=conn)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS brevo_contact_sync_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT,
        entity_id INTEGER,
        email TEXT,
        status TEXT,
        details TEXT,
        list_names TEXT,
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_resets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        token_hash TEXT,
        expires_at TEXT,
        used_at TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        token_hash TEXT,
        expires_at TEXT,
        verified_at TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_user_id INTEGER,
        actor_type TEXT,
        action TEXT,
        target_type TEXT,
        target_id TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS account_recovery_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        token TEXT UNIQUE,
        recovery_type TEXT,
        expires_at TEXT,
        used_at TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS day_signal_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        score INTEGER,
        signal TEXT,
        answers_json TEXT,
        response TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_ai_interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        feature TEXT,
        prompt TEXT,
        response TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS command_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        command_name TEXT,
        input TEXT,
        output_summary TEXT,
        source TEXT,
        pro_required INTEGER DEFAULT 0,
        status TEXT,
        error TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS saved_command_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        command_history_id INTEGER,
        title TEXT,
        summary TEXT,
        source TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        interaction_id INTEGER,
        rating TEXT,
        feedback TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER,
        user_id INTEGER,
        role TEXT,
        content TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_context_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        conversation_id INTEGER,
        summary TEXT,
        starts_at TEXT,
        ends_at TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_type TEXT DEFAULT 'direct',
        created_by INTEGER,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS conversation_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER,
        user_id INTEGER,
        joined_at TEXT,
        last_read_at TEXT,
        UNIQUE(conversation_id, user_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS private_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER,
        sender_user_id INTEGER,
        body TEXT,
        created_at TEXT,
        deleted_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS message_read_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER,
        user_id INTEGER,
        read_at TEXT,
        UNIQUE(message_id, user_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS message_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER,
        attachment_type TEXT,
        storage_key TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS blocked_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        blocker_user_id INTEGER,
        blocked_user_id INTEGER,
        reason TEXT,
        created_at TEXT,
        UNIQUE(blocker_user_id, blocked_user_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_user_id INTEGER,
        reported_user_id INTEGER,
        conversation_id INTEGER,
        message_id INTEGER,
        reason TEXT,
        status TEXT DEFAULT 'open',
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS saved_wallets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        address TEXT,
        chain TEXT,
        label TEXT,
        created_at TEXT,
        last_checked_at TEXT,
        UNIQUE(user_id, address)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        alert_type TEXT,
        target TEXT,
        threshold TEXT,
        enabled INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    add_columns_if_missing(cur, "user_alerts", [
        ("symbol", "TEXT"),
        ("target_value", "REAL"),
        ("condition", "TEXT"),
        ("channel", "TEXT"),
        ("active", "INTEGER DEFAULT 1"),
        ("last_triggered_at", "TEXT"),
    ], conn=conn)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT,
        coin_name TEXT,
        amount REAL,
        average_buy_price REAL,
        notes TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS watchlist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT,
        coin_name TEXT,
        created_at TEXT,
        UNIQUE(user_id, symbol)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        event_type TEXT,
        event_label TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS telegram_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        telegram_chat_id INTEGER,
        notification_type TEXT,
        message TEXT,
        sent_at TEXT,
        status TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        notification_type TEXT,
        title TEXT,
        message TEXT,
        status TEXT DEFAULT 'unread',
        metadata TEXT,
        created_at TEXT,
        read_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notification_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        category TEXT,
        in_app INTEGER DEFAULT 1,
        push INTEGER DEFAULT 0,
        email INTEGER DEFAULT 0,
        telegram INTEGER DEFAULT 0,
        updated_at TEXT,
        UNIQUE(user_id, category)
    )
    """)
    add_columns_if_missing(cur, "notification_preferences", [
        ("sms", "INTEGER DEFAULT 0"),
    ], conn=conn)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notification_delivery_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        notification_id INTEGER,
        channel TEXT,
        status TEXT,
        provider_response TEXT,
        error_message TEXT,
        retry_count INTEGER DEFAULT 0,
        created_at TEXT,
        sent_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notification_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        channel TEXT,
        category TEXT,
        sent_at TEXT,
        delivery_status TEXT,
        provider_response TEXT,
        retries INTEGER DEFAULT 0,
        failed_reason TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sms_delivery_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone TEXT,
        alert_type TEXT,
        status TEXT,
        provider_response TEXT,
        error_message TEXT,
        created_at TEXT,
        sent_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS telegram_delivery_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        telegram_chat_id INTEGER,
        alert_type TEXT,
        status TEXT,
        provider_response TEXT,
        error_message TEXT,
        created_at TEXT,
        sent_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        endpoint TEXT UNIQUE,
        subscription_json TEXT,
        user_agent TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_alert_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        alert_type TEXT,
        symbol TEXT,
        condition TEXT,
        target_value REAL,
        channels TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alert_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        alert_type TEXT,
        symbol TEXT,
        condition TEXT,
        target_value REAL,
        channels TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS watch_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        watch_type TEXT,
        target_value TEXT,
        conditions TEXT,
        channels TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT,
        last_checked_at TEXT,
        last_triggered_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alert_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        watch_rule_id INTEGER,
        alert_type TEXT,
        title TEXT,
        body TEXT,
        status TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_briefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        brief_date TEXT,
        market_pulse TEXT,
        risk_alerts TEXT,
        watchlist_notes TEXT,
        scam_warning TEXT,
        ai_insight TEXT,
        created_at TEXT,
        UNIQUE(user_id, brief_date)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_streaks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        daily_checkin_streak INTEGER DEFAULT 0,
        learning_streak INTEGER DEFAULT 0,
        scam_safety_score INTEGER DEFAULT 0,
        portfolio_discipline_score INTEGER DEFAULT 0,
        alert_readiness_score INTEGER DEFAULT 0,
        last_checkin_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS saved_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        insight_type TEXT,
        title TEXT,
        content TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_watch_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        watch_type TEXT,
        label TEXT,
        value TEXT,
        notes TEXT,
        created_at TEXT,
        UNIQUE(user_id, watch_type, value)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS risk_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        account_security INTEGER DEFAULT 70,
        wallet_safety INTEGER DEFAULT 70,
        scam_exposure INTEGER DEFAULT 70,
        portfolio_risk INTEGER DEFAULT 70,
        alert_coverage INTEGER DEFAULT 70,
        score INTEGER DEFAULT 70,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        path TEXT,
        lesson_slug TEXT,
        status TEXT DEFAULT 'not_started',
        score INTEGER DEFAULT 0,
        updated_at TEXT,
        UNIQUE(user_id, path, lesson_slug)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE,
        title TEXT,
        summary TEXT,
        sort_order INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE,
        category_slug TEXT,
        title TEXT,
        difficulty TEXT,
        estimated_time TEXT,
        summary TEXT,
        content TEXT,
        examples TEXT,
        red_flags TEXT,
        safe_steps TEXT,
        beginner_mistakes TEXT,
        access_level TEXT DEFAULT 'free',
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lesson_slug TEXT,
        heading TEXT,
        body TEXT,
        sort_order INTEGER DEFAULT 0
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lesson_slug TEXT,
        title TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_quiz_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lesson_slug TEXT,
        question TEXT,
        options TEXT,
        answer TEXT,
        explanation TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_user_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        lesson_slug TEXT,
        status TEXT,
        score INTEGER DEFAULT 0,
        updated_at TEXT,
        UNIQUE(user_id, lesson_slug)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_lesson_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        lesson_slug TEXT,
        path TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_badges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        badge_key TEXT,
        title TEXT,
        earned_at TEXT,
        UNIQUE(user_id, badge_key)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS education_ai_tutor_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        lesson_slug TEXT,
        question TEXT,
        response TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS dashboard_widgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        widget_key TEXT,
        position INTEGER DEFAULT 0,
        pinned INTEGER DEFAULT 1,
        updated_at TEXT,
        UNIQUE(user_id, widget_key)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_dashboard_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        show_account INTEGER DEFAULT 1,
        show_upgrade_pro INTEGER DEFAULT 1,
        show_command_center INTEGER DEFAULT 1,
        show_logout INTEGER DEFAULT 1,
        show_saved_insights INTEGER DEFAULT 1,
        show_activity_timeline INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    add_columns_if_missing(cur, "user_dashboard_preferences", [
        ("show_account", "INTEGER DEFAULT 1"),
        ("show_upgrade_pro", "INTEGER DEFAULT 1"),
        ("show_command_center", "INTEGER DEFAULT 1"),
        ("show_logout", "INTEGER DEFAULT 1"),
        ("show_saved_insights", "INTEGER DEFAULT 1"),
        ("show_activity_timeline", "INTEGER DEFAULT 1"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_education_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        show_edu_nav_home INTEGER DEFAULT 1,
        show_edu_nav_dashboard INTEGER DEFAULT 1,
        show_edu_nav_education INTEGER DEFAULT 1,
        show_edu_nav_scam_shield INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    add_columns_if_missing(cur, "user_education_preferences", [
        ("show_edu_nav_home", "INTEGER DEFAULT 1"),
        ("show_edu_nav_dashboard", "INTEGER DEFAULT 1"),
        ("show_edu_nav_education", "INTEGER DEFAULT 1"),
        ("show_edu_nav_scam_shield", "INTEGER DEFAULT 1"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notification_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        schedule_type TEXT,
        channels TEXT,
        enabled INTEGER DEFAULT 1,
        updated_at TEXT,
        UNIQUE(user_id, schedule_type)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_memory_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        summary TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS scam_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        report_type TEXT,
        target TEXT,
        description TEXT,
        status TEXT DEFAULT 'open',
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS scam_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        severity TEXT,
        source TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS scam_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        input_text TEXT,
        risk_level TEXT,
        risk_score INTEGER,
        threats_json TEXT,
        red_flags_json TEXT,
        safe_actions_json TEXT,
        confidence REAL,
        source_status TEXT,
        result_json TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallet_risk_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        wallet_or_txid TEXT,
        chain TEXT,
        risk_level TEXT,
        result_json TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS referral_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referral_code TEXT,
        referrer_user_id INTEGER,
        session_id TEXT,
        landing_page TEXT,
        referrer TEXT,
        ip_hash TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plan TEXT,
        status TEXT,
        payment_type TEXT,
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        trial_start_date TEXT,
        trial_end_date TEXT,
        current_period_end TEXT,
        pro_expires_at TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS usage_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        feature TEXT,
        count INTEGER DEFAULT 1,
        plan TEXT,
        metadata TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS referral_rewards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_user_id INTEGER,
        referred_user_id INTEGER,
        referral_code TEXT,
        reward_type TEXT,
        reward_days INTEGER DEFAULT 30,
        status TEXT,
        granted_at TEXT,
        created_at TEXT,
        UNIQUE(referrer_user_id, referred_user_id, reward_type)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        reward_days INTEGER DEFAULT 30,
        max_redemptions INTEGER,
        redemption_count INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        expires_at TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trial_email_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        event_type TEXT,
        email TEXT,
        status TEXT,
        created_at TEXT,
        UNIQUE(user_id, event_type)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT,
        email TEXT UNIQUE,
        phone TEXT,
        password_hash TEXT,
        role TEXT DEFAULT 'admin',
        status TEXT DEFAULT 'active',
        job_title TEXT,
        company_role TEXT,
        date_of_birth TEXT,
        address_line1 TEXT,
        address_line2 TEXT,
        city TEXT,
        state TEXT,
        zip_code TEXT,
        country TEXT,
        emergency_contact_name TEXT,
        emergency_contact_phone TEXT,
        notes TEXT,
        created_at TEXT,
        updated_at TEXT,
        last_login_at TEXT
    )
    """)
    add_columns_if_missing(cur, "admin_users", [
        ("must_change_password", "INTEGER DEFAULT 1"),
        ("password_changed_at", "TEXT"),
        ("temp_password_created_at", "TEXT"),
        ("failed_login_count", "INTEGER DEFAULT 0"),
        ("locked_until", "TEXT"),
    ], conn=conn)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_user_id INTEGER,
        admin_email TEXT,
        action TEXT,
        target_type TEXT,
        target_id TEXT,
        metadata TEXT,
        ip_hash TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stripe_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stripe_event_id TEXT UNIQUE,
        event_type TEXT,
        user_id INTEGER,
        status TEXT,
        error_message TEXT,
        payload_summary TEXT,
        created_at TEXT,
        processed_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        stripe_event_id TEXT,
        stripe_session_id TEXT,
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        invoice_id TEXT,
        payment_intent_id TEXT,
        amount REAL,
        currency TEXT,
        status TEXT,
        payment_type TEXT,
        created_at TEXT
    )
    """)
    add_columns_if_missing(cur, "payment_records", [
        ("manual", "INTEGER DEFAULT 0"),
        ("metadata", "TEXT"),
    ], conn=conn)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS visitor_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        session_id TEXT,
        ip_address TEXT,
        user_agent TEXT,
        path TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    add_columns_if_missing(cur, "visitor_logs", [
        ("referrer", "TEXT"),
        ("device_type", "TEXT"),
        ("browser", "TEXT"),
        ("os", "TEXT"),
        ("country", "TEXT"),
        ("city", "TEXT"),
    ], conn=conn)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS checkout_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        account_status TEXT,
        authenticated INTEGER DEFAULT 0,
        stripe_secret_loaded INTEGER DEFAULT 0,
        stripe_publishable_loaded INTEGER DEFAULT 0,
        stripe_webhook_loaded INTEGER DEFAULT 0,
        stripe_price_loaded INTEGER DEFAULT 0,
        app_base_url TEXT,
        status TEXT,
        stripe_session_id TEXT,
        redirect_url TEXT,
        error_message TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS unmatched_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stripe_event_id TEXT,
        event_type TEXT,
        stripe_object_id TEXT,
        customer_id TEXT,
        customer_email TEXT,
        amount REAL,
        currency TEXT,
        reason TEXT,
        payload_summary TEXT,
        created_at TEXT,
        resolved_at TEXT,
        resolved_by_admin_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS support_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        admin_user_id INTEGER,
        note TEXT,
        status TEXT DEFAULT 'open',
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_user_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        admin_user_id INTEGER,
        note TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_user_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_user_id INTEGER,
        target_user_id INTEGER,
        action TEXT,
        details TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_simulator_wallets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        cash_balance REAL DEFAULT 10000,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_simulator_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT,
        side TEXT,
        quantity REAL,
        price REAL,
        notional REAL,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS simulator_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        starting_balance REAL DEFAULT 10000,
        cash_balance REAL DEFAULT 10000,
        training_level INTEGER DEFAULT 1,
        practice_streak INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS simulator_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT,
        side TEXT,
        order_type TEXT,
        quantity REAL,
        limit_price REAL,
        stop_price REAL,
        status TEXT,
        created_at TEXT,
        filled_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS simulator_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT,
        side TEXT,
        quantity REAL,
        price REAL,
        notional REAL,
        fee REAL DEFAULT 0,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS simulator_watchlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT,
        created_at TEXT,
        UNIQUE(user_id, symbol)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS simulator_lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE,
        title TEXT,
        content TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS simulator_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        lesson_slug TEXT,
        status TEXT,
        score INTEGER DEFAULT 0,
        updated_at TEXT,
        UNIQUE(user_id, lesson_slug)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS simulator_ai_coaching_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        prompt TEXT,
        response TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prediction_markets (
        id TEXT PRIMARY KEY,
        title TEXT,
        category TEXT,
        status TEXT,
        outcomes TEXT,
        probability REAL,
        price REAL,
        volume REAL,
        liquidity REAL,
        close_time TEXT,
        resolve_time TEXT,
        source TEXT,
        source_url TEXT,
        risk_level TEXT,
        last_updated TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prediction_watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        market_id TEXT,
        threshold REAL,
        created_at TEXT,
        UNIQUE(user_id, market_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS auth_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        email TEXT,
        user_id INTEGER,
        status TEXT,
        details TEXT,
        db_engine TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        description TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id TEXT UNIQUE,
        full_name TEXT,
        email TEXT UNIQUE,
        phone TEXT,
        job_title TEXT,
        department_id INTEGER,
        manager_id INTEGER,
        role TEXT,
        status TEXT DEFAULT 'active',
        start_date TEXT,
        address TEXT,
        date_of_birth TEXT,
        emergency_contact TEXT,
        notes TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        description TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        description TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS role_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role_name TEXT,
        permission_key TEXT,
        created_at TEXT,
        UNIQUE(role_name, permission_key)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        name TEXT,
        issue_type TEXT,
        subject TEXT,
        message TEXT,
        status TEXT DEFAULT 'open',
        priority TEXT DEFAULT 'normal',
        assigned_to INTEGER,
        internal_notes TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS support_ticket_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        sender_type TEXT,
        sender_user_id INTEGER,
        sender_admin_id INTEGER,
        message TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS security_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        report_type TEXT,
        target TEXT,
        description TEXT,
        status TEXT DEFAULT 'open',
        created_at TEXT,
        updated_at TEXT
    )
    """)

    now_seed = datetime.now().isoformat()
    for department in ["Executive", "Engineering", "Support", "Billing", "Marketing", "Content", "Analytics", "Security", "Operations"]:
        cur.execute(
            "INSERT OR IGNORE INTO departments (name, description, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
            (department, f"{department} department", now_seed, now_seed),
        )
    for role_name in ["owner", "super_admin", "admin", "billing_manager", "support_manager", "support_agent", "analyst", "content_manager", "developer", "read_only"]:
        cur.execute(
            "INSERT OR IGNORE INTO roles (name, description, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
            (role_name, f"{role_name.replace('_', ' ').title()} role", now_seed, now_seed),
        )
    default_permissions = [
        "users.view", "users.create", "users.edit", "users.delete", "users.suspend",
        "admins.view", "admins.create", "admins.edit", "admins.delete",
        "employees.view", "employees.create", "employees.edit", "employees.delete",
        "departments.manage", "billing.view", "billing.repair", "subscriptions.edit",
        "emails.view", "emails.resend", "telegram.view", "telegram.unlink", "ai.view",
        "analytics.view", "system.view", "settings.edit", "audit.view", "support.manage",
    ]
    for permission in default_permissions:
        cur.execute(
            "INSERT OR IGNORE INTO permissions (key, description, created_at) VALUES (?, ?, ?)",
            (permission, permission.replace(".", " ").title(), now_seed),
        )
        cur.execute(
            "INSERT OR IGNORE INTO role_permissions (role_name, permission_key, created_at) VALUES ('owner', ?, ?)",
            (permission, now_seed),
        )

    seed_education_knowledge_bank(cur)
    ensure_owner_admin_with_cursor(cur, allow_reset=False)

    conn.commit()
    conn.close()
    logging.info("MIGRATION_COMPLETE engine=%s tables_checked=production_saas", db_service.ENGINE_NAME)


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
        "/subscribe — website Pro upgrade link\n"
        "/setemail you@example.com — save email for payment confirmations\n"
        "/myemail — show the email saved for confirmations\n"
        "/connect CODE — link the optional Telegram companion to your website account\n"
        "/portfolio — website dashboard portfolio summary\n"
        "/watchlist — website dashboard watchlist\n"
        "/alerts — website dashboard alerts\n"
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
    logging.info("Telegram AI request started")
    logging.info("Telegram user id: %s", user_id)
    linked = get_linked_website_account(user_id)
    logging.info("linked account found: %s", bool(linked))
    logging.info("Pro access: %s", is_pro(user_id))
    openai_key_loaded = bool(os.getenv("OPENAI_API_KEY"))
    logging.info("OpenAI key loaded: %s", openai_key_loaded)
    allowed, limit_message = consume_ai_usage(user_id, "telegram_ai_assistant")
    if not allowed:
        return append_plan_footer(user_id, limit_message)
    try:
        response = intelligence_service.assistant_response(user_id, question, pro=is_pro(user_id))
        if openai_key_loaded:
            logging.info("OpenAI response success")
    except Exception as exc:
        logging.warning("OpenAI error message: %s", exc)
        log_product_event(user_id, "openai_error", {"error": str(exc)[:300], "surface": "telegram"})
        response = (
            "💬 AI Crypto Assistant\n\n"
            "AI intelligence is temporarily unavailable. Please try again shortly.\n\n"
            "Educational information only. Not financial, betting, investment, or legal advice."
        )
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_history (user_id, role, message, response, is_pro, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "user", question, response, 1 if is_pro(user_id) else 0, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("AI chat history save failed: %s", exc)
    user_context_service.log_interaction(user_id, "ai_assistant_used", question, response, "telegram")
    log_product_event(user_id, "telegram_ai_used", {"linked_account": bool(linked), "pro": is_pro(user_id)})
    return append_plan_footer(user_id, response)


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
        result = wallet_intel_service.analyze_public_identifier(address)
        return result.get(
            "response",
            "Please send a public BTC/ETH wallet address or public BTC TXID. Never send seed phrases, private keys, recovery phrases, or wallet passwords.",
        )
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
        result = wallet_intel_service.analyze_public_identifier(address)
        if result.get("ok"):
            return result.get("response")
        return "Send a public BTC/ETH wallet address or public BTC TXID for /walletscan. Never send private keys, seed phrases, recovery phrases, or passwords."
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
    if not country:
        payload = news_service.get_crypto_news(limit=limit)
        return [
            {
                "title": item.get("title"),
                "summary": item.get("summary"),
                "sentiment": item.get("sentiment", "neutral").title(),
                "source": item.get("source"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
            }
            for item in payload.get("items", [])[:limit]
        ]
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
            "To view your CoinPilotX account from the optional Telegram companion, please create or log in to your account on our website first.\n\n"
            "Create account:\n"
            "https://coinpilotx.app/signup\n\n"
            "Already have an account?\n"
            "https://coinpilotx.app/login\n\n"
            "After logging in, go to Account Settings and tap ‘Connect Telegram Bot.’\n\n"
            "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords."
        )

    logging.info("Linked account found for Telegram user %s", user_id)
    name = website_user.get("full_name") or website_user.get("display_name") or "Not set"
    status = website_user.get("subscription_status") or "inactive"
    access = account_access_context(website_user)
    trial_line = ""
    if access["is_trial"]:
        trial_line = f"Trial ends: {access['trial_end']} ({access['days_remaining']} days remaining)\n"
    elif access["trial_expired"]:
        trial_line = "Trial: Ended — upgrade anytime when deeper intelligence becomes useful.\n"
    portfolio = portfolio_service.calculate_user_portfolio(website_user["user_id"])
    portfolio_line = (
        f"\nPortfolio: ${portfolio.get('total_value', 0):,.2f} tracked"
        f" · P/L {portfolio.get('pnl_percent', 0):+.2f}%\n"
        if portfolio.get("holdings")
        else "\nPortfolio: No website holdings saved yet.\n"
    )
    pro_line = "\n✅ Pro active — deeper CoinPilotX intelligence enabled.\n" if has_pro_access(website_user) else "\nUpgrade Pro on the website when you want deeper intelligence.\n"
    return (
        "👤 CoinPilotX Account\n\n"
        f"Name: {name}\n"
        f"Email: {mask_email(website_user.get('email'))}\n"
        f"Plan: {access['label']}\n"
        f"Subscription: {status}\n"
        f"{trial_line}"
        "Telegram: Connected\n"
        f"Email Updates: {'Enabled' if website_user.get('email_opt_in') else 'Disabled'}\n"
        f"SMS Updates: {'Enabled' if website_user.get('sms_opt_in') else 'Disabled'}\n"
        f"{portfolio_line}"
        "Dashboard: https://coinpilotx.app/dashboard"
        f"{pro_line}"
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
    account = get_linked_website_account(telegram_user_id)
    if account:
        rows = [
            [InlineKeyboardButton("Open Dashboard", url="https://coinpilotx.app/dashboard")],
            [InlineKeyboardButton("Settings", url="https://coinpilotx.app/account/settings")],
            [InlineKeyboardButton("Help", callback_data="menu_help")],
            [InlineKeyboardButton("Main Menu", callback_data="main_menu")],
        ]
        if not has_pro_access(account):
            rows.insert(1, [InlineKeyboardButton("Upgrade Pro on Website", url=website_upgrade_url(telegram_user_id))])
        return InlineKeyboardMarkup(rows)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Create Account", url="https://coinpilotx.app/signup")],
        [InlineKeyboardButton("Login", url="https://coinpilotx.app/login")],
        [InlineKeyboardButton("Open Platform Account", url="https://coinpilotx.app/account")],
        [InlineKeyboardButton("Help", callback_data="menu_help")],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")],
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
    response = jsonify({"ok": True, "status": "online"})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response, 200


@webhook_app.route("/health/database", methods=["GET"])
def database_health_check():
    diagnostics = db_service.health_check()
    payload = {
        "connected": diagnostics.get("connected"),
        "db_engine": diagnostics.get("db_engine"),
        "database_url_loaded": diagnostics.get("database_url_loaded"),
        "database_name": diagnostics.get("database_name"),
        "latency_ms": diagnostics.get("latency_ms"),
        "tables_detected": diagnostics.get("tables_detected"),
    }
    if not diagnostics.get("connected"):
        payload["error"] = "Database connection failed. Check server logs for details."
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response, 200 if diagnostics.get("connected") else 503


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
    logging.info("TELEGRAM_COMMAND_RECEIVED command=ask telegram_user_id=%s", update.effective_user.id)
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
    logging.info("TELEGRAM_COMMAND_RECEIVED command=markets telegram_user_id=%s", update.effective_user.id)
    await update.message.reply_text(market_board_summary(update.effective_user.id, "top_market_cap"), reply_markup=main_menu())


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    logging.info("TELEGRAM_COMMAND_RECEIVED command=market telegram_user_id=%s", update.effective_user.id)
    await update.message.reply_text(market_board_summary(update.effective_user.id, "top_market_cap"), reply_markup=main_menu())


async def btc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    logging.info("TELEGRAM_COMMAND_RECEIVED command=btc telegram_user_id=%s", update.effective_user.id)
    context.args = ["BTC"]
    await price(update, context)


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    logging.info("TELEGRAM_COMMAND_RECEIVED command=chat telegram_user_id=%s", update.effective_user.id)
    await update.message.reply_text(
        "💬 CoinPilotXAI Chat\n\nSend a message here or open the full web chat at https://coinpilotx.app/chat. The website is the main platform; Telegram is optional companion access.",
        reply_markup=main_menu(),
    )


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    logging.info("TELEGRAM_COMMAND_RECEIVED command=upgrade telegram_user_id=%s", update.effective_user.id)
    user = get_linked_website_account(update.effective_user.id)
    if user and platform_pro_access(user):
        await update.message.reply_text("Your CoinPilotXAI Pro access is already active.", reply_markup=account_reply_markup(update.effective_user.id))
        return
    await update.message.reply_text(pro_upgrade_message(update.effective_user.id), reply_markup=upgrade_payment_menu(update.effective_user.id))


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
    email = normalize_email(context.args[0])
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
    linked_user = load_account_by_id(row[1])
    if linked_user:
        sync_brevo_contact_safe({**linked_user, "source": "telegram_link"}, entity_type="user", entity_id=row[1])
    logging.info("Telegram linking success for Telegram user %s", update.effective_user.id)
    pro_line = (
        "\n\nYour CoinPilotXAI Pro access is now active in Telegram.\n\n"
        "If you had any payment issue, email support@coinpilotx.app with your account email."
        if linked_user and has_pro_access(linked_user)
        else "\n\nYour Telegram account is linked. Upgrade Pro on the website when you want deeper intelligence."
    )
    log_product_event(row[1], "telegram_account_linked", {"telegram_user_id": update.effective_user.id})
    if linked_user and has_pro_access(linked_user):
        log_product_event(row[1], "telegram_pro_verified", {"telegram_user_id": update.effective_user.id})
    await update.message.reply_text(
        "✅ Telegram connected successfully.\n\n"
        "Your CoinPilotX account is now connected to this Telegram profile. You can now use the Account button anytime to view your plan, subscription, preferences, and account status."
        f"{pro_line}",
        reply_markup=account_reply_markup(update.effective_user.id),
    )


async def help_account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👤 CoinPilotX Account Help\n\n"
        "Create account: https://coinpilotx.app/signup\n"
        "Login: https://coinpilotx.app/login\n"
        "Dashboard: https://coinpilotx.app/dashboard\n\n"
        "To connect Telegram:\n"
        "1. Log in on the website.\n"
        "2. Open Account Settings.\n"
        "3. Tap Connect Telegram Bot.\n"
        "4. Send the generated code here with /connect CODE.\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.",
        reply_markup=account_reply_markup(update.effective_user.id),
    )


def linked_account_or_message(telegram_user_id):
    user = get_linked_website_account(telegram_user_id)
    if user:
        return user, None
    return None, (
        "👤 Account Not Connected\n\n"
        "Your Telegram is not linked yet. Create or log in on the website, then generate a Telegram activation code from Account Settings.\n\n"
        "Create account: https://coinpilotx.app/signup\n"
        "Login: https://coinpilotx.app/login\n\n"
        "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords."
    )


async def website_portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user, message = linked_account_or_message(update.effective_user.id)
    if not user:
        await update.message.reply_text(message, reply_markup=account_reply_markup(update.effective_user.id))
        return
    data = portfolio_service.calculate_user_portfolio(user["user_id"])
    holdings = data.get("holdings", [])
    lines = [
        "💼 CoinPilotX Website Portfolio",
        "",
        f"Total value: ${data.get('total_value', 0):,.2f}",
        f"Total cost: ${data.get('total_cost', 0):,.2f}",
        f"P/L: ${data.get('pnl_value', 0):+,.2f} ({data.get('pnl_percent', 0):+.2f}%)",
    ]
    if holdings:
        lines.append("\nTop holdings:")
        for item in holdings[:6]:
            lines.append(
                f"• {item['symbol']}: {item['amount']:.6g} ≈ ${item['current_value']:,.2f} "
                f"({item['pnl_percent']:+.2f}% P/L)"
            )
    else:
        lines.append("\nNo website holdings saved yet. Add holdings from your dashboard.")
    lines.append("\nDashboard: https://coinpilotx.app/dashboard")
    lines.append("Educational information only. Not financial, betting, investment, or legal advice.")
    await update.message.reply_text("\n".join(lines), reply_markup=account_reply_markup(update.effective_user.id))


async def website_watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user, message = linked_account_or_message(update.effective_user.id)
    if not user:
        await update.message.reply_text(message, reply_markup=account_reply_markup(update.effective_user.id))
        return
    items = portfolio_service.get_watchlist(user["user_id"])
    lines = ["👁️ CoinPilotX Website Watchlist", ""]
    if items:
        for item in items[:12]:
            price_text = "unavailable" if item.get("price") is None else f"${item['price']:,.4g}"
            change_text = "n/a" if item.get("change_24h") is None else f"{item['change_24h']:+.2f}% 24h"
            lines.append(f"• {item['symbol']}: {price_text} · {change_text}")
    else:
        lines.append("No watchlist coins saved yet. Add coins from your dashboard.")
    lines.append("\nDashboard: https://coinpilotx.app/dashboard")
    await update.message.reply_text("\n".join(lines), reply_markup=account_reply_markup(update.effective_user.id))


async def website_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    user, message = linked_account_or_message(update.effective_user.id)
    if not user:
        await update.message.reply_text(message, reply_markup=account_reply_markup(update.effective_user.id))
        return
    alerts = portfolio_service.get_alerts(user["user_id"])
    lines = ["🚨 CoinPilotX Alerts Center", ""]
    if alerts:
        for alert in alerts[:12]:
            status = "active" if alert.get("active") else "paused"
            lines.append(
                f"• {alert.get('symbol') or 'Portfolio'} {alert.get('condition') or alert.get('alert_type')} "
                f"{alert.get('target_value') or ''} · {status}"
            )
    else:
        lines.append("No website alerts saved yet. Create price or portfolio alerts from your dashboard.")
    lines.append("\nDashboard: https://coinpilotx.app/dashboard")
    lines.append("Educational information only. Not financial, betting, investment, or legal advice.")
    await update.message.reply_text("\n".join(lines), reply_markup=account_reply_markup(update.effective_user.id))


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


async def trial_maintenance_job(context: ContextTypes.DEFAULT_TYPE):
    run_trial_maintenance(force=True)


def stripe_period_end_to_iso(value):
    try:
        if value:
            return datetime.fromtimestamp(int(value)).isoformat()
    except Exception:
        return None
    return None


def stripe_object_get(obj, key, default=None):
    try:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return obj.get(key, default)
    except Exception:
        return getattr(obj, key, default)


def fetch_stripe_customer_email(customer_id):
    if not customer_id or not STRIPE_SECRET_KEY:
        return ""
    try:
        customer = stripe.Customer.retrieve(customer_id)
        email = stripe_object_get(customer, "email", "") or ""
        logging.info("Stripe customer lookup customer_id=%s email_found=%s", customer_id, bool(email))
        return email
    except Exception as exc:
        logging.warning("Stripe customer lookup failed customer_id=%s error=%s", customer_id, exc)
        return ""


def fetch_stripe_subscription(subscription_id):
    if not subscription_id or not STRIPE_SECRET_KEY:
        return None
    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
        logging.info("Stripe subscription lookup success subscription_id=%s", subscription_id)
        return subscription
    except Exception as exc:
        logging.warning("Stripe subscription lookup failed subscription_id=%s error=%s", subscription_id, exc)
        return None


def user_id_from_candidate(value):
    if not value:
        return None
    try:
        candidate = int(str(value))
    except Exception:
        return None
    if load_account_by_id(candidate):
        return candidate
    return None


def find_user_by_stripe(customer_id=None, subscription_id=None, metadata_user_id=None, customer_email=None):
    candidate = user_id_from_candidate(metadata_user_id)
    if candidate:
        logging.info("Stripe user resolved by metadata/client reference user_id=%s", candidate)
        return candidate
    conn = db()
    cur = conn.cursor()
    if subscription_id:
        cur.execute("SELECT user_id FROM users WHERE stripe_subscription_id=? LIMIT 1", (subscription_id,))
        row = cur.fetchone()
        if row:
            conn.close()
            logging.info("Stripe user resolved by subscription_id user_id=%s", row[0])
            return row[0]
    if customer_id:
        cur.execute("SELECT user_id FROM users WHERE stripe_customer_id=? LIMIT 1", (customer_id,))
        row = cur.fetchone()
        if row:
            conn.close()
            logging.info("Stripe user resolved by customer_id user_id=%s", row[0])
            return row[0]
    conn.close()
    email = normalize_email(customer_email)
    if not email:
        email = normalize_email(fetch_stripe_customer_email(customer_id))
    if email and is_valid_email(email):
        user = load_account_by_email(email)
        if user:
            logging.info("Stripe user resolved by email user_id=%s email=%s", user["user_id"], email)
            return user["user_id"]
    logging.warning(
        "Stripe user resolution failed customer_id=%s subscription_id=%s metadata_user_id=%s email_present=%s",
        customer_id,
        subscription_id,
        metadata_user_id,
        bool(customer_email),
    )
    return None


def resolve_checkout_session_user(session):
    session = session or {}
    metadata = session.get("metadata") or {}
    customer_details = session.get("customer_details") or {}
    customer_email = (
        customer_details.get("email")
        or session.get("customer_email")
        or metadata.get("email")
        or ""
    )
    metadata_user_id = (
        session.get("client_reference_id")
        or metadata.get("user_id")
        or metadata.get("client_reference_id")
        or metadata.get("account_user_id")
    )
    subscription_id = session.get("subscription")
    customer_id = session.get("customer")
    user_id = find_user_by_stripe(customer_id, subscription_id, metadata_user_id, customer_email)
    logging.info(
        "Stripe checkout resolution session_id=%s customer_id=%s customer_email=%s client_reference_id=%s metadata_user_id=%s resolved_user_id=%s",
        session.get("id"),
        customer_id,
        bool(customer_email),
        session.get("client_reference_id"),
        metadata.get("user_id"),
        user_id,
    )
    return user_id, customer_email


def sync_stripe_subscription(subscription):
    subscription = subscription or {}
    customer_id = subscription.get("customer")
    subscription_id = subscription.get("id")
    metadata = subscription.get("metadata") or {}
    user_id = find_user_by_stripe(
        customer_id,
        subscription_id,
        metadata.get("user_id") or metadata.get("client_reference_id"),
        metadata.get("email"),
    )
    if not user_id:
        logging.info("Stripe subscription sync skipped: no matching user for customer=%s subscription=%s", customer_id, subscription_id)
        return None
    raw_status = (subscription.get("status") or "").lower()
    status = raw_status if raw_status in {"active", "trialing"} else ("past_due" if raw_status in {"past_due", "unpaid"} else ("canceled" if raw_status in {"canceled", "incomplete_expired"} else raw_status or "inactive"))
    period_end = stripe_period_end_to_iso(subscription.get("current_period_end"))
    before = load_account_by_id(user_id) or {}
    logging.info(
        "Stripe subscription sync start event_subscription=%s user_id=%s before_plan=%s before_status=%s stripe_status=%s period_end=%s",
        subscription_id,
        user_id,
        before.get("plan") or before.get("subscription_plan"),
        before.get("subscription_status"),
        raw_status,
        period_end,
    )
    conn = db()
    cur = conn.cursor()
    if status in {"active", "trialing"}:
        cur.execute(
            """
            UPDATE users
            SET plan='pro', subscription_plan='pro', subscription_status=?, is_pro=1,
                stripe_customer_id=COALESCE(?, stripe_customer_id),
                stripe_subscription_id=COALESCE(?, stripe_subscription_id),
                trial_status=CASE WHEN ?='active' THEN 'converted' ELSE COALESCE(trial_status, '') END,
                pro_expires_at=?,
                subscription_expires_at=?,
                updated_at=?
            WHERE user_id=?
            """,
            (status, customer_id, subscription_id, status, period_end, period_end, datetime.now().isoformat(), user_id),
        )
    else:
        cur.execute(
            """
            UPDATE users
            SET subscription_status=?, stripe_customer_id=COALESCE(?, stripe_customer_id),
                stripe_subscription_id=COALESCE(?, stripe_subscription_id),
                pro_expires_at=COALESCE(?, pro_expires_at),
                subscription_expires_at=COALESCE(?, subscription_expires_at),
                updated_at=?
            WHERE user_id=?
            """,
            (status, customer_id, subscription_id, period_end, period_end, datetime.now().isoformat(), user_id),
        )
    cur.execute(
        """
        INSERT INTO subscriptions
        (user_id, plan, status, payment_type, stripe_customer_id, stripe_subscription_id, current_period_end, pro_expires_at, created_at, updated_at)
        VALUES (?, 'pro', ?, 'stripe', ?, ?, ?, ?, ?, ?)
        """,
        (user_id, status, customer_id, subscription_id, period_end, period_end, datetime.now().isoformat(), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    after = load_account_by_id(user_id) or {}
    logging.info(
        "Stripe subscription sync committed user_id=%s after_plan=%s after_status=%s customer_id=%s subscription_id=%s",
        user_id,
        after.get("plan") or after.get("subscription_plan"),
        after.get("subscription_status"),
        customer_id,
        subscription_id,
    )
    logging.info(
        "SUBSCRIPTION_RECORD_UPDATED user_id=%s status=%s stripe_customer_id=%s stripe_subscription_id=%s period_end=%s",
        user_id,
        status,
        customer_id,
        subscription_id,
        period_end,
    )
    if (before.get("subscription_status") or "").lower() == "trialing" and status == "active":
        logging.info("TRIAL_TO_PAID_CONVERSION user_id=%s subscription_id=%s", user_id, subscription_id)
    log_product_event(user_id, "pro_subscription_active" if status == "active" else "pro_subscription_updated", {"stripe_status": raw_status or status})
    expire_trials(send_email=False)
    user = load_account_by_id(user_id)
    if user:
        sync_brevo_contact_safe({**user, "source": "stripe_subscription"}, entity_type="user", entity_id=user_id)
    return user_id


def sync_stripe_invoice(invoice, event_type):
    invoice = invoice or {}
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")
    customer_email = invoice.get("customer_email") or invoice.get("customer_email_address") or ""
    subscription = None
    metadata_user_id = ""
    if subscription_id:
        subscription = fetch_stripe_subscription(subscription_id)
        if subscription:
            metadata = subscription.get("metadata") or {}
            metadata_user_id = metadata.get("user_id") or metadata.get("client_reference_id") or ""
            customer_email = customer_email or metadata.get("email") or ""
    user_id = find_user_by_stripe(customer_id, subscription_id, metadata_user_id, customer_email)
    if not user_id:
        logging.info("Stripe invoice sync skipped: no matching user for customer=%s subscription=%s", customer_id, subscription_id)
        return None
    lines = (invoice.get("lines") or {}).get("data") or []
    period_end = None
    if lines:
        period_end = stripe_period_end_to_iso((lines[0].get("period") or {}).get("end"))
    if event_type in {"invoice.paid", "invoice.payment_succeeded"}:
        logging.info("Stripe invoice payment succeeded user_id=%s invoice_id=%s customer_id=%s subscription_id=%s", user_id, invoice.get("id"), customer_id, subscription_id)
        activate_pro(user_id, payment_type="stripe", stripe_customer_id=customer_id, stripe_subscription_id=subscription_id, subscription_status="active", pro_expires_at=period_end)
        return user_id
    logging.warning("Stripe invoice payment failed user_id=%s invoice_id=%s customer_id=%s subscription_id=%s", user_id, invoice.get("id"), customer_id, subscription_id)
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET subscription_status='past_due', updated_at=? WHERE user_id=?",
        (datetime.now().isoformat(), user_id),
    )
    conn.commit()
    conn.close()
    log_product_event(user_id, "pro_subscription_payment_failed", {"invoice": invoice.get("id")})
    return user_id


def activate_pro(user_id, payment_type=None, stripe_customer_id=None, stripe_session_id=None, stripe_subscription_id=None, subscription_status="active", pro_expires_at=None):
    target_user = load_account_by_id(user_id) or get_linked_website_account(user_id)
    if not target_user:
        logging.error("Pro activation failed: local user not found user_id=%s payment_type=%s customer_id=%s subscription_id=%s", user_id, payment_type, stripe_customer_id, stripe_subscription_id)
        return None
    target_user_id = (target_user or {}).get("user_id") or user_id
    logging.info(
        "Pro activation starting user_id=%s before_plan=%s before_status=%s payment_type=%s customer_id=%s session_id=%s subscription_id=%s expires=%s",
        target_user_id,
        target_user.get("plan") or target_user.get("subscription_plan"),
        target_user.get("subscription_status"),
        payment_type,
        stripe_customer_id,
        stripe_session_id,
        stripe_subscription_id,
        pro_expires_at,
    )
    logging.info(
        "PAYMENT_SUCCESS_USER_BEFORE user_id=%s plan=%s status=%s trial_status=%s subscription_id=%s",
        target_user_id,
        target_user.get("plan") or target_user.get("subscription_plan"),
        target_user.get("subscription_status"),
        target_user.get("trial_status"),
        target_user.get("stripe_subscription_id"),
    )
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
            stripe_session_id=COALESCE(?, stripe_session_id),
            stripe_subscription_id=COALESCE(?, stripe_subscription_id),
            trial_status=CASE WHEN ?='active' THEN 'converted' ELSE COALESCE(trial_status, '') END,
            pro_expires_at=?,
            subscription_expires_at=?
        WHERE user_id=? OR telegram_user_id=?
        """,
        (
            subscription_status,
            datetime.now().isoformat(),
            datetime.now().isoformat(),
            payment_type,
            stripe_customer_id,
            stripe_session_id,
            stripe_subscription_id,
            subscription_status,
            pro_expires_at,
            pro_expires_at,
            user_id,
            user_id,
        )
    )
    cur.execute(
        """
        INSERT INTO subscriptions
        (user_id, plan, status, payment_type, stripe_customer_id, stripe_subscription_id, current_period_end, pro_expires_at, created_at, updated_at)
        VALUES (?, 'pro', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_user_id,
            subscription_status,
            payment_type,
            stripe_customer_id,
            stripe_subscription_id,
            pro_expires_at,
            pro_expires_at,
            datetime.now().isoformat(),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    after_user = load_account_by_id(target_user_id) or {}
    logging.info(
        "Pro activation database commit success user_id=%s after_plan=%s after_status=%s has_pro=%s",
        target_user_id,
        after_user.get("plan") or after_user.get("subscription_plan"),
        after_user.get("subscription_status"),
        has_pro_access(after_user),
    )
    logging.info(
        "PAYMENT_SUCCESS_USER_AFTER user_id=%s plan=%s status=%s trial_status=%s subscription_id=%s",
        target_user_id,
        after_user.get("plan") or after_user.get("subscription_plan"),
        after_user.get("subscription_status"),
        after_user.get("trial_status"),
        after_user.get("stripe_subscription_id"),
    )
    if (target_user.get("subscription_status") or "").lower() == "trialing" and subscription_status == "active":
        logging.info("TRIAL_TO_PAID_CONVERSION user_id=%s subscription_id=%s", target_user_id, stripe_subscription_id)
    logging.info("PRO_UPGRADE_COMPLETED user_id=%s status=%s", target_user_id, subscription_status)
    log_product_event(target_user_id, "pro_subscription_active" if subscription_status == "active" else "pro_subscription_updated", {"payment_type": payment_type, "status": subscription_status})
    user = load_account_by_id(target_user_id)
    if user:
        sync_brevo_contact_safe({**user, "source": payment_type or "pro_upgrade"}, entity_type="user", entity_id=user.get("user_id") or target_user_id)
    return target_user_id


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
    return day_signal_service.generate(answers)
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

    if data in {"menu_main", "main_menu"}:
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
        day_signal_service.save_result(user_id, result, answers)
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
        logging.info("TELEGRAM_ACCOUNT_BUTTON_CLICKED telegram_user_id=%s", user_id)
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
        log_product_event(user_id, "telegram_upgrade_clicked", {"source": "bot_button"})
        await query.message.reply_text(pro_upgrade_message(user_id), reply_markup=upgrade_payment_menu(user_id))
        return

    if data == "pay_btc":
        await query.message.reply_text(
            "⭐ Website Checkout Required\n\n"
            "For security and account consistency, Pro payments now happen only on the CoinPilotXAI website.\n\n"
            "Create or log in to your website account, upgrade there, then return here and send your activation code with /connect CODE.\n\n"
            "CoinPilotXAI Inc. never asks for seed phrases, private keys, or wallet passwords.",
            reply_markup=upgrade_payment_menu(user_id)
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


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("TELEGRAM_COMMAND_ERROR update=%s error=%s", update, getattr(context, "error", None))


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
    run_trial_maintenance(force=True)
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
    app.add_handler(CommandHandler("watchlist", website_watchlist_command))
    app.add_handler(CommandHandler("portfolio", website_portfolio_command))
    app.add_handler(CommandHandler("alerts", website_alerts_command))
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
    app.add_handler(CommandHandler("market", market_command))
    app.add_handler(CommandHandler("btc", btc_command))
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
    app.add_handler(CommandHandler("upgrade", upgrade_command))
    app.add_handler(CommandHandler("chat", chat_command))
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
    app.add_error_handler(telegram_error_handler)

    job_queue.run_repeating(market_signal_job, interval=SIGNAL_CHECK_SECONDS, first=10)
    job_queue.run_repeating(hourly_market_update, interval=HOURLY_UPDATE_SECONDS, first=30)
    job_queue.run_repeating(whale_alert_job, interval=WHALE_CHECK_SECONDS, first=45)
    job_queue.run_repeating(portfolio_tracking_job, interval=PORTFOLIO_TRACK_SECONDS, first=60)
    job_queue.run_repeating(trial_maintenance_job, interval=3600, first=120)

    app.run_polling()


if __name__ == "__main__":
    threading.Thread(target=run_webhook, daemon=True).start()
    main()
