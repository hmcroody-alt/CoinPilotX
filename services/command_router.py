from datetime import datetime

from . import (
    day_signal,
    intelligence,
    market_data,
    portfolio_service,
    pro_access,
    scam_shield,
    sports_data,
    user_context,
    wallet_intel,
)


DISCLAIMER = "Educational information only. Not financial, betting, investment, or legal advice."


MENU_SECTIONS = [
    ("Market Intelligence", [
        ("live_btc", "Live BTC"),
        ("live_market", "Live Market"),
        ("btc_eth_charts", "BTC/ETH Charts"),
        ("fear_greed", "Fear & Greed"),
        ("market_events", "Market Events"),
        ("crypto_news", "Crypto News"),
    ]),
    ("AI Intelligence", [
        ("ai_analysis", "AI Analysis"),
        ("auto_signals", "Auto Signals"),
        ("ai_crypto_assistant", "AI Crypto Assistant"),
        ("chat_assistant", "Chat Assistant"),
        ("crypto_wisdom", "Crypto Wisdom"),
        ("today_day_signal", "Is Today My Day?"),
    ]),
    ("Safety / Risk", [
        ("scam_shield", "Scam Shield"),
        ("scam_stories", "Scam Stories"),
        ("wallet_intel", "Wallet Intel"),
        ("tx_explorer", "TX Explorer"),
        ("chain_intel", "Chain Intel"),
    ]),
    ("Portfolio", [
        ("live_portfolio", "Live Portfolio"),
        ("portfolio_advice", "Portfolio Advice"),
        ("add_holding", "Add Holding"),
        ("watchlist", "Watchlist"),
        ("alerts", "Alerts"),
    ]),
    ("Sports", [
        ("sports_edge", "Sports Edge"),
        ("live_sports_edge", "Live Sports Edge"),
    ]),
    ("Whale / Network", [
        ("whale_alerts", "Whale Alerts"),
        ("btc_network", "BTC Network"),
    ]),
    ("Account", [
        ("account", "Account"),
        ("upgrade_pro", "Upgrade Pro"),
        ("open_dashboard", "Open Dashboard"),
        ("billing", "Billing"),
        ("help", "Help"),
    ]),
]


COMMAND_ALIASES = {
    "/btc": "live_btc",
    "/price": "live_btc",
    "/market": "live_market",
    "/live": "live_market",
    "/news": "crypto_news",
    "/fear": "fear_greed",
    "/scam": "scam_shield",
    "/scan": "scam_shield",
    "/phishing": "scam_shield",
    "/token": "scam_shield",
    "/wallet": "wallet_intel",
    "/portfolio": "live_portfolio",
    "/day": "today_day_signal",
    "/sports": "sports_edge",
    "/help": "help",
}


def _user(user_id):
    return user_context.get_user_by_id(user_id) if user_id else {}


def _pro(user):
    return pro_access.has_pro_access(user or {})


def _card(action_key, title, summary, source="CoinPilotXAI platform", confidence="Medium", actions=None):
    return {
        "ok": True,
        "action_key": action_key,
        "title": title,
        "summary": summary,
        "source": source,
        "confidence": confidence,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "actions": actions or ["Ask AI follow-up", "Share"],
        "disclaimer": DISCLAIMER,
    }


def get_menu_items(user=None):
    pro = _pro(user or {})
    return {
        "ok": True,
        "pro": pro,
        "sections": [
            {"title": title, "items": [{"action_key": key, "label": label, "pro": key in {"whale_alerts", "portfolio_advice", "live_sports_edge"}} for key, label in items]}
            for title, items in MENU_SECTIONS
        ],
    }


def _market_summary(action_key="live_market", symbol=None):
    board = market_data.live_market_board(limit=12)
    if symbol:
        item = market_data.get_symbol(symbol)
        if not item:
            return _card(action_key, f"{symbol.upper()} Live Price", "Live data source temporarily unavailable.", board.get("source", "unavailable"), "Low")
        change = item.get("change_24h")
        summary = f"{item.get('symbol')}: ${float(item.get('price') or 0):,.2f}"
        if change is not None:
            summary += f" · 24h {float(change):+.2f}%"
        return _card(action_key, f"{item.get('symbol')} Live Price", summary, board.get("source", "public market feed"), "High" if item.get("price") else "Low", ["Save to watchlist", "Create alert", "Ask AI follow-up"])
    rows = []
    for item in board.get("markets", [])[:6]:
        change = item.get("change_24h")
        rows.append(f"{item.get('symbol')}: ${float(item.get('price') or 0):,.2f}" + (f" ({float(change):+.2f}%)" if change is not None else ""))
    summary = "\n".join(rows) if rows else "Live data source temporarily unavailable."
    return _card(action_key, "Live Market", summary, board.get("source", "public market feed"), "High" if rows else "Low", ["Open full dashboard", "Create alert", "Ask AI follow-up"])


def execute_menu_action(user_id, action_key, channel="web", payload=None):
    payload = payload or {}
    user = _user(user_id)
    pro = _pro(user)
    if action_key == "live_btc":
        symbol = (payload.get("symbol") or "BTC").upper()
        return _market_summary("live_btc", symbol)
    if action_key == "live_market":
        return _market_summary("live_market")
    if action_key == "btc_eth_charts":
        btc = _market_summary("live_btc", "BTC")
        eth = _market_summary("live_btc", "ETH")
        return _card(
            action_key,
            "BTC/ETH Charts",
            f"{btc.get('summary', '')}\n{eth.get('summary', '')}\nOpen full charts from the dashboard when chart providers are connected.",
            "Public market feed",
            "Medium",
            ["Open dashboard", "Create alert", "Ask AI follow-up"],
        )
    if action_key == "auto_signals":
        board = market_data.live_market_board(limit=12)
        summary = board.get("summary") or {}
        trend = summary.get("market_trend") or "mixed"
        risk = summary.get("risk_level") or "Unknown"
        avg = summary.get("average_change_24h")
        signal = "WAIT"
        if avg is not None and avg > 1.5 and risk == "Normal":
            signal = "WATCH"
        elif avg is not None and avg < -2:
            signal = "CAUTION"
        return _card(action_key, "Auto Signals", f"Educational signal: {signal}\nTrend: {trend}\nRisk: {risk}\nThis is context, not a buy/sell instruction.", board.get("source", "public market feed"), "Medium")
    if action_key in {"crypto_news", "market_events"}:
        return _card(action_key, "Crypto News and Market Events", "Live news source temporarily unavailable. Connect NEWS_API_KEY or CryptoPanic/NewsAPI to enable fresh market event summaries.", "news source unavailable", "Low", ["Ask AI for general context", "Open market board"])
    if action_key == "fear_greed":
        return _card(action_key, "Fear & Greed", "Fear & Greed source temporarily unavailable. The platform will show this live once the sentiment feed is connected.", "sentiment source unavailable", "Low")
    if action_key == "crypto_wisdom":
        return _card(action_key, "Crypto Wisdom", "Patience is a position. Slow decisions, smaller size, and clear invalidation rules usually beat emotional reaction.", "CoinPilotXAI education library", "High")
    if action_key in {"scam_stories", "chain_intel", "btc_network", "whale_alerts"}:
        title_map = {
            "scam_stories": "Scam Stories",
            "chain_intel": "Chain Intel",
            "btc_network": "BTC Network",
            "whale_alerts": "Whale Alerts",
        }
        source = "public-data service unavailable" if action_key != "scam_stories" else "CoinPilotXAI scam education library"
        summary = "Live data source temporarily unavailable." if action_key != "scam_stories" else "Common crypto scam patterns include fake support, wallet-drainer approvals, fake airdrops, guaranteed-return dashboards, and urgency pressure."
        return _card(action_key, title_map[action_key], summary, source, "Low" if action_key != "scam_stories" else "High")
    if action_key in {"ai_analysis", "ai_crypto_assistant", "chat_assistant"}:
        question = payload.get("question") or "Analyze BTC and the current crypto market."
        response = intelligence.assistant_response(user_id, question, pro=pro)
        user_context.log_interaction(user_id or 0, "website_ai_used", question, response, channel)
        return _card(action_key, "AI Crypto Assistant", response, "OpenAI + public market context", "Medium" if response else "Low", ["Ask follow-up", "Save insight", "Upgrade Pro"])
    if action_key == "scam_shield":
        text = payload.get("text") or payload.get("query") or "Paste suspicious text or a URL to scan."
        result = scam_shield.analyze_text(text)
        user_context.log_interaction(user_id or 0, "scam_shield_used", text, result.get("response", ""), channel)
        return _card(action_key, "Scam Shield", result.get("response", ""), "CoinPilotXAI risk rules", "Medium", ["Run another scan", "Share safety card"])
    if action_key in {"wallet_intel", "tx_explorer"}:
        value = payload.get("address") or payload.get("query") or ""
        if not value:
            return _card(action_key, "Wallet Intel", "Enter a public wallet address or transaction ID. Never enter seed phrases, private keys, or wallet passwords.", "Public explorer validation", "Low")
        result = wallet_intel.analyze_public_identifier(value)
        user_context.log_interaction(user_id or 0, "wallet_intel_used", value, result.get("response", ""), channel)
        return _card(action_key, "Wallet Intel", result.get("response", ""), "Public explorer validation", "Medium")
    if action_key == "today_day_signal":
        answers = payload.get("answers") or {}
        if not answers:
            return _card(action_key, "CoinPilotX Day Signal", "Answer the four quick readiness questions to generate your Day Signal score.", "CoinPilotXAI readiness model", "Medium", ["Start Day Signal"])
        result = day_signal.generate(answers, pro=pro)
        if result.get("ok"):
            day_signal.save_result(user_id or 0, result, answers)
        return _card(action_key, "CoinPilotX Day Signal", result.get("response", "Day Signal is unavailable right now."), "CoinPilotXAI readiness model", "Medium")
    if action_key in {"sports_edge", "live_sports_edge"}:
        result = sports_data.live_sports_edge()
        summary = result.get("warning") or "Live Sports Edge feed loaded."
        games = result.get("games") or []
        if games:
            summary += "\n" + "\n".join(f"{g.get('matchup')} · {g.get('status') or 'scheduled'}" for g in games[:5])
        else:
            summary = "Live data source temporarily unavailable. Odds unavailable in current feed."
        return _card(action_key, "Sports Edge", summary, result.get("source", "sports feed"), "Medium" if games else "Low")
    if action_key == "live_portfolio":
        if not user_id:
            return _card(action_key, "Portfolio", "Log in to view your portfolio tracker.", "Website account database", "High", ["Login", "Create account"])
        data = portfolio_service.get_user_dashboard_data(user_id)
        p = data.get("portfolio", {})
        return _card(action_key, "Live Portfolio", f"Tracked value: ${float(p.get('total_value') or 0):,.2f}\nP/L: ${float(p.get('pnl_value') or 0):,.2f} · {float(p.get('pnl_percent') or 0):+.2f}%", "Website account database + public market feed", "Medium", ["Add holding", "Open dashboard"])
    if action_key in {"portfolio_advice", "add_holding", "watchlist", "alerts"}:
        if not user_id:
            return _card(action_key, "Portfolio Tools", "Create a free account to save holdings, watchlist coins, alerts, and portfolio history.", "Website account database", "High", ["Create account"])
        data = portfolio_service.get_user_dashboard_data(user_id)
        p = data.get("portfolio", {})
        return _card(action_key, "Portfolio Tools", f"Open your dashboard to manage holdings, watchlist, and alerts.\nCurrent tracked value: ${float(p.get('total_value') or 0):,.2f}", "Website account database + public market feed", "Medium", ["Open dashboard", "Create alert"])
    if action_key in {"open_dashboard", "billing"}:
        return _card(action_key, "Dashboard", "Open the website dashboard to manage account, billing, portfolio, alerts, and optional Telegram connection.", "Website account database", "High", ["Open dashboard"])
    if action_key == "account":
        if not user_id:
            return _card(action_key, "Account", "Log in to view your CoinPilotXAI account.", "Website account database", "High", ["Login", "Create account"])
        access_type = pro_access.pro_access_type(user)
        label = "Paid Pro active" if access_type == "paid" else "Pro trial active" if access_type == "trial" else "Free"
        return _card(action_key, "Account", f"Plan: {label}\nSubscription: {user.get('subscription_status') or 'inactive'}\nTelegram: {'Connected' if user.get('telegram_user_id') else 'Optional and not connected'}", "Website account database", "High", ["Open dashboard", "Account settings"])
    if action_key == "upgrade_pro":
        if pro:
            return _card(action_key, "Upgrade Pro", "You already have CoinPilotXAI Pro active.", "Website account database", "High", ["Open dashboard", "Account"])
        return _card(action_key, "Upgrade Pro", "Open the website checkout while logged into your CoinPilotXAI account. No anonymous checkout is allowed.", "Stripe hosted checkout", "High", ["Upgrade on website"])
    if action_key == "help":
        return _card(action_key, "Help", "Use buttons or slash commands like /price BTC, /market, /scam, /wallet, /portfolio, /day, /sports, or ask naturally.", "CoinPilotXAI command router", "High")
    return _card(action_key, "CoinPilotXAI Command", "Live source temporarily unavailable or this command is being connected.", "CoinPilotXAI platform", "Low")


def handle_command(user_id, command_text, channel="web"):
    text = (command_text or "").strip()
    if not text:
        return _card("help", "Command Center", "Ask CoinPilotXAI anything or choose a command card.", "CoinPilotXAI command router", "High")
    first, _, rest = text.partition(" ")
    action = COMMAND_ALIASES.get(first.lower())
    payload = {"query": rest.strip(), "question": text}
    if first.lower() == "/price":
        payload["symbol"] = rest.strip().split()[0] if rest.strip() else "BTC"
    if first.lower() == "/scam":
        payload["text"] = rest.strip()
    if first.lower() in {"/scan", "/phishing", "/token"}:
        payload["text"] = rest.strip()
    if first.lower() == "/wallet":
        payload["address"] = rest.strip()
    if not action:
        action = "ai_crypto_assistant"
    return execute_menu_action(user_id, action, channel=channel, payload=payload)


def format_response_for_web(result):
    return result


def format_response_for_telegram(result):
    result = result or {}
    return f"{result.get('title', 'CoinPilotXAI')}\n\n{result.get('summary', '')}\n\nSource: {result.get('source', 'CoinPilotXAI platform')}\n{result.get('disclaimer', DISCLAIMER)}"
