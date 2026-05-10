from .schema import SHARE_IMAGE_URL, SITE_URL

COMMON_FAQS = [
    {
        "question": "Is CoinPilotXAI Inc. financial advice?",
        "answer": "No. CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.",
    },
    {
        "question": "Does CoinPilotX hold user funds?",
        "answer": "No. CoinPilotX never holds user funds and never asks for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.",
    },
]

SEO_PAGES = {
    "wallet-security": {
        "title": "Crypto Wallet Security | CoinPilotXAI Inc.",
        "description": "Learn how CoinPilotX helps crypto users review public wallet activity, wallet-drain warning signs, and safer crypto security habits.",
        "h1": "Crypto Wallet Security Intelligence",
        "eyebrow": "Wallet Safety",
        "intro": "CoinPilotX helps users slow down, review public wallet activity, and understand risk signals before trusting wallet approvals, links, or unknown counterparties.",
        "answer": "Wallet security starts with a simple rule: never share seed phrases, private keys, wallet passwords, or signing credentials. CoinPilotX focuses on public wallet and transaction information only.",
        "points": ["Public wallet and TXID checks", "Wallet-drain warning patterns", "Approval-risk education", "Explorer links for public verification"],
        "faqs": [
            {"question": "What is Wallet Intel?", "answer": "Wallet Intel is CoinPilotX's public wallet and transaction education tool. It helps users review public blockchain context without requesting private credentials."},
            {"question": "Can CoinPilotX recover a wallet?", "answer": "No. CoinPilotXAI Inc. cannot recover wallets and will never ask for seed phrases or private keys."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/crypto-safety", "/ai-market-analysis"],
    },
    "crypto-scams": {
        "title": "Crypto Scam Protection | CoinPilotXAI Inc.",
        "description": "Use CoinPilotX Scam Shield to understand suspicious crypto messages, fake support, wallet-drainer language, phishing, and urgency tactics.",
        "h1": "Crypto Scam Protection and Scam Shield",
        "eyebrow": "Scam Awareness",
        "intro": "Scam Shield is built to identify suspicious language, unsafe wallet instructions, fake support patterns, fake airdrop pressure, and wallet-drainer red flags.",
        "answer": "Most crypto scams rely on speed, confusion, and trust abuse. CoinPilotX gives users a structured risk read and safer next step before they click, connect, or sign.",
        "points": ["Suspicious phrase detection", "Fake support and impersonation patterns", "Seed phrase and private-key danger signs", "Wallet-drain approval warnings"],
        "faqs": [
            {"question": "How does Scam Shield work?", "answer": "Scam Shield uses deterministic risk rules and optional AI classification to flag dangerous phrases, urgency, fake support behavior, phishing patterns, and wallet-drain warning signs."},
            {"question": "Does Scam Shield guarantee a message is safe?", "answer": "No. It provides educational risk context. Users should verify independently and avoid sharing private wallet credentials."},
        ] + COMMON_FAQS,
        "related": ["/wallet-security", "/crypto-safety", "/telegram-crypto-bot"],
    },
    "whale-alerts": {
        "title": "Crypto Whale Alerts | CoinPilotXAI Inc.",
        "description": "Track whale-style market pressure, large crypto movements, BTC intelligence, and educational risk context inside CoinPilotX.",
        "h1": "Whale Alerts and Market Pressure Intelligence",
        "eyebrow": "Whale Intelligence",
        "intro": "CoinPilotX translates whale-style movement, exchange pressure, and market context into plain-language intelligence for crypto users.",
        "answer": "Whale activity can matter, but it should never be treated as certainty. CoinPilotX frames whale pressure as one context factor among trend, volatility, price movement, and risk.",
        "points": ["Whale movement summaries", "Market pressure context", "Risk-aware interpretations", "Telegram-first alerts"],
        "faqs": [
            {"question": "What are whale alerts?", "answer": "Whale alerts are large public-chain or market movement summaries that may indicate unusual market activity or pressure."},
            {"question": "Do whale alerts predict price?", "answer": "No. Whale alerts are context, not predictions or guaranteed signals."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc", "/ai-market-analysis", "/portfolio-intelligence"],
    },
    "sports-edge": {
        "title": "AI Sports Intelligence | CoinPilotXAI Inc.",
        "description": "Sports Edge provides live game context, risk factors, position discipline, and educational sports intelligence without guaranteed picks.",
        "h1": "Sports Edge AI Intelligence",
        "eyebrow": "Sports Edge",
        "intro": "Sports Edge gives users a calmer way to review games, live score context, odds availability, risk factors, and why forcing a position can be dangerous.",
        "answer": "Sports Edge is informational only. It does not provide guaranteed bets, locks, or sure outcomes. It helps users think in probabilities, risk, and discipline.",
        "points": ["Live public scoreboard context", "Sport-specific risk notes", "Position intelligence prompts", "Telegram handoff for deeper analysis"],
        "faqs": [
            {"question": "What is Sports Edge?", "answer": "Sports Edge is CoinPilotX's informational sports intelligence feature for reviewing game state, risk factors, market context, and position discipline."},
            {"question": "Does Sports Edge give guaranteed picks?", "answer": "No. CoinPilotXAI Inc. does not guarantee sports outcomes or betting results."},
        ] + COMMON_FAQS,
        "related": ["/day-signal", "/ai-market-analysis", "/telegram-crypto-bot"],
    },
    "day-signal": {
        "title": "CoinPilotX Day Signal | CoinPilotXAI Inc.",
        "description": "Use CoinPilotX Day Signal as a confidence, readiness, and risk-awareness check before crypto, sports, business, or personal decisions.",
        "h1": "CoinPilotX Day Signal",
        "eyebrow": "Readiness Check",
        "intro": "Day Signal asks a few quick questions to help users check emotional state, preparation, opportunity type, and willingness to walk away.",
        "answer": "Day Signal is not fate, luck, or prediction. It is a readiness score and discipline tool designed to slow down impulsive decisions.",
        "points": ["Readiness score", "Best move today", "Avoid today warning", "Motivational but responsible guidance"],
        "faqs": [
            {"question": "How does Day Signal work?", "answer": "Day Signal scores confidence, preparation, opportunity type, and walk-away discipline to produce a motivational risk-awareness result."},
            {"question": "Can Day Signal predict my day?", "answer": "No. Day Signal does not predict destiny, luck, trading wins, or betting outcomes."},
        ] + COMMON_FAQS,
        "related": ["/sports-edge", "/portfolio-intelligence", "/crypto-safety"],
    },
    "ai-market-analysis": {
        "title": "AI Market Analysis Assistant | CoinPilotXAI Inc.",
        "description": "CoinPilotX provides AI-assisted crypto market explanations, momentum reads, risk context, and safer next steps inside Telegram and the web.",
        "h1": "AI Crypto Market Analysis Assistant",
        "eyebrow": "AI Market Intelligence",
        "intro": "CoinPilotX helps users understand market signals, momentum, volatility, wallet risk, scams, and safer decision context without claiming certainty.",
        "answer": "AI market analysis should explain context and risk, not promise outcomes. CoinPilotX uses clear sections so users can see what matters and what could change.",
        "points": ["Market Snapshot", "Momentum Read", "Risk Level", "What to Watch", "Safer Next Step"],
        "faqs": [
            {"question": "How does the AI crypto assistant work?", "answer": "The assistant uses CoinPilotX intelligence workflows and optional OpenAI-powered responses to explain crypto, wallet, scam, sports, and market questions."},
            {"question": "Does the assistant tell me what to buy?", "answer": "No. It provides educational context, possible scenarios, and risk reminders."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc", "/crypto-scams", "/portfolio-intelligence"],
    },
    "telegram-crypto-bot": {
        "title": "Telegram Crypto AI Bot | CoinPilotXAI Inc.",
        "description": "CoinPilotX is a Telegram-first AI crypto bot for market intelligence, scam protection, wallet checks, portfolio context, and Sports Edge.",
        "h1": "Telegram Crypto AI Bot",
        "eyebrow": "Telegram-First",
        "intro": "CoinPilotX brings AI crypto intelligence into a Telegram workflow so users can ask questions, scan risks, review markets, and continue from the website into the bot.",
        "answer": "Telegram-first design keeps CoinPilotX fast and easy to access while still connecting to website accounts, Pro access, and shared intelligence systems.",
        "points": ["Telegram bot commands", "Website account linking", "AI assistant handoff", "Pro upgrade flow"],
        "faqs": [
            {"question": "What can the Telegram bot do?", "answer": "The bot can answer AI questions, show live market context, review scam messages, check public wallet data, run Day Signal, and open Sports Edge context."},
            {"question": "Do I need to create an account?", "answer": "You can start with Telegram, and a website account helps connect plan, subscription, preferences, and future retention features."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/crypto-safety", "/sports-edge"],
    },
    "crypto-safety": {
        "title": "Crypto Safety Tools | CoinPilotXAI Inc.",
        "description": "CoinPilotXAI Inc. provides crypto safety education, scam awareness, wallet risk context, and public blockchain intelligence tools.",
        "h1": "Crypto Safety Tools and Risk Awareness",
        "eyebrow": "Safety First",
        "intro": "Crypto users need safer habits, better context, and clearer warnings before trusting links, signing wallet approvals, or reacting to market pressure.",
        "answer": "CoinPilotX is built around the idea that users should slow down, verify, and understand risk before acting.",
        "points": ["Scam Shield", "Wallet Intel", "Public TXID checks", "Educational disclaimers", "No custody of funds"],
        "faqs": [
            {"question": "Is CoinPilotX safe to use?", "answer": "CoinPilotX never asks for seed phrases, private keys, recovery phrases, wallet passwords, or wallet custody."},
            {"question": "What data should I avoid entering?", "answer": "Never enter private keys, seed phrases, recovery phrases, wallet passwords, or signing credentials anywhere in CoinPilotX."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/wallet-security", "/telegram-crypto-bot"],
    },
    "portfolio-intelligence": {
        "title": "Crypto Portfolio AI Intelligence | CoinPilotXAI Inc.",
        "description": "CoinPilotX helps users review crypto portfolio value, manual balance context, upside/downside scenarios, and educational risk guidance.",
        "h1": "Crypto Portfolio Intelligence",
        "eyebrow": "Portfolio Context",
        "intro": "Portfolio Intelligence compares holdings, manual balance context, market movement, and scenario estimates so users can review risk before acting.",
        "answer": "Portfolio intelligence is not a prediction engine. It explains exposure, possible scenarios, risk level, and what could change a signal.",
        "points": ["Real-time portfolio context", "Manual balance override", "Upside/downside scenarios", "BUY / SELL / WAIT / HOLD educational logic"],
        "faqs": [
            {"question": "Does portfolio intelligence guarantee profit?", "answer": "No. It estimates possible scenarios and explains risk context for educational purposes."},
            {"question": "Can I track a manual balance?", "answer": "Yes. CoinPilotX supports manual portfolio balance context while keeping live holdings tracking separate."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc", "/ai-market-analysis", "/whale-alerts"],
    },
}

SEO_PAGES.update({
    "crypto-scam-checker": {
        "title": "Crypto Scam Checker | CoinPilotXAI Inc.",
        "description": "Check suspicious crypto messages, fake airdrops, fake support scripts, wallet-drainer language, and phishing risk with CoinPilotX Scam Shield.",
        "h1": "Crypto Scam Checker",
        "eyebrow": "Scam Shield",
        "intro": "A focused landing page for users searching for a crypto scam checker that explains red flags before they click, connect, or sign.",
        "answer": "CoinPilotX Scam Shield reviews suspicious text and URLs for seed phrase requests, fake support pressure, wallet-drainer language, fake airdrops, urgency, and guaranteed-return claims.",
        "points": ["Suspicious message review", "Fake support detection", "Phishing and airdrop warnings", "Safer next-step guidance"],
        "faqs": [
            {"question": "Can I paste suspicious messages into CoinPilotX?", "answer": "Yes. Paste suspicious text into Scam Shield, but never paste private keys, seed phrases, passwords, or signing credentials."},
            {"question": "Is a low scam score a guarantee?", "answer": "No. It is educational context only. Always verify independently."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/wallet-security", "/crypto-safety"],
    },
    "ai-crypto-assistant": {
        "title": "AI Crypto Assistant | CoinPilotXAI Inc.",
        "description": "Ask CoinPilotX AI crypto questions about markets, scams, wallets, portfolio risk, whale activity, and Telegram crypto workflows.",
        "h1": "AI Crypto Assistant",
        "eyebrow": "Ask CoinPilotX",
        "intro": "CoinPilotX works as a cautious AI crypto assistant for users who want clearer market, scam, wallet, and portfolio context.",
        "answer": "The AI assistant is designed to explain context, risk factors, and safer next steps, not to give guaranteed buy or sell instructions.",
        "points": ["Market context answers", "Scam and wallet education", "Portfolio scenario prompts", "Telegram-first workflow"],
        "faqs": [
            {"question": "Can CoinPilotX answer general crypto questions?", "answer": "Yes. It can explain crypto topics, wallet safety, market context, scams, and portfolio awareness in plain language."},
            {"question": "Does CoinPilotX replace professional advice?", "answer": "No. It is educational AI intelligence only."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/telegram-crypto-bot", "/crypto-safety"],
    },
    "telegram-trading-assistant": {
        "title": "Telegram Trading Assistant | CoinPilotXAI Inc.",
        "description": "CoinPilotX is a Telegram-first trading education assistant for crypto market context, risk awareness, alerts, and safer decision support.",
        "h1": "Telegram Trading Assistant for Risk Awareness",
        "eyebrow": "Telegram Workflow",
        "intro": "CoinPilotX supports trading-related education inside Telegram while avoiding guaranteed signals, sure wins, or pressure tactics.",
        "answer": "A responsible Telegram trading assistant should help users understand risk, context, and scenarios before acting. CoinPilotX is built around that standard.",
        "points": ["Telegram AI questions", "Live market board handoff", "Scam safety reminders", "Portfolio context"],
        "faqs": [
            {"question": "Does CoinPilotX provide guaranteed trading signals?", "answer": "No. CoinPilotX provides educational signal context, not guaranteed outcomes."},
            {"question": "Can I use CoinPilotX from Telegram?", "answer": "Yes. The primary workflow is Telegram-first."},
        ] + COMMON_FAQS,
        "related": ["/telegram-crypto-bot", "/ai-market-analysis", "/portfolio-intelligence"],
    },
    "ai-wallet-scanner": {
        "title": "AI Wallet Scanner | CoinPilotXAI Inc.",
        "description": "Review public wallet addresses and TXIDs with CoinPilotX Wallet Intel, explorer links, and crypto safety education.",
        "h1": "AI Wallet Scanner for Public Wallet Intelligence",
        "eyebrow": "Wallet Intel",
        "intro": "CoinPilotX Wallet Intel helps users review public wallet and transaction context without asking for private wallet credentials.",
        "answer": "Wallet Intel accepts public wallet addresses and TXIDs only. It never needs seed phrases, private keys, wallet passwords, or recovery phrases.",
        "points": ["Public wallet address checks", "Public TXID context", "Explorer link handoff", "Credential safety warnings"],
        "faqs": [
            {"question": "What wallet data can I enter?", "answer": "Only public wallet addresses and public transaction IDs."},
            {"question": "Can CoinPilotX scan private wallets?", "answer": "No. CoinPilotX never requests private credentials and does not access private wallet data."},
        ] + COMMON_FAQS,
        "related": ["/wallet-security", "/crypto-scam-checker", "/crypto-safety"],
    },
    "sports-intelligence-ai": {
        "title": "Sports Intelligence AI | CoinPilotXAI Inc.",
        "description": "CoinPilotX Sports Edge gives informational game context, risk factors, and position discipline without guaranteed picks.",
        "h1": "Sports Intelligence AI",
        "eyebrow": "Sports Edge",
        "intro": "Sports Edge helps users review live games and risk context more carefully, especially when emotions or live action pressure are high.",
        "answer": "Sports intelligence should explain why a position may be risky, why waiting can be valid, and what missing data could change the view.",
        "points": ["Live game context", "Risk labels", "Sport-specific reasoning", "Telegram analysis handoff"],
        "faqs": [
            {"question": "Does Sports Edge guarantee bets?", "answer": "No. Sports Edge is informational only and never guarantees betting outcomes."},
            {"question": "What if odds are unavailable?", "answer": "CoinPilotX says odds are unavailable and avoids pretending to know market pricing."},
        ] + COMMON_FAQS,
        "related": ["/sports-edge", "/day-signal", "/telegram-crypto-bot"],
    },
    "whale-tracker": {
        "title": "Crypto Whale Tracker | CoinPilotXAI Inc.",
        "description": "Track whale-style pressure and large movement context with CoinPilotX whale alerts and educational market intelligence.",
        "h1": "Crypto Whale Tracker",
        "eyebrow": "Whale Intelligence",
        "intro": "CoinPilotX whale tracking focuses on context and risk, not sensational claims or guaranteed market direction.",
        "answer": "Whale activity can affect sentiment, but it should be reviewed alongside price trend, volatility, market breadth, and portfolio exposure.",
        "points": ["Large movement context", "Market pressure education", "Risk-aware whale summaries", "Telegram alerts"],
        "faqs": [
            {"question": "Can whale tracking predict price?", "answer": "No. It provides context that may be useful but cannot guarantee direction."},
            {"question": "Where can I review whale alerts?", "answer": "Use CoinPilotX in Telegram or the website intelligence sections."},
        ] + COMMON_FAQS,
        "related": ["/whale-alerts", "/markets/btc", "/portfolio-intelligence"],
    },
    "portfolio-ai": {
        "title": "Portfolio AI | CoinPilotXAI Inc.",
        "description": "Use CoinPilotX Portfolio AI for educational crypto exposure context, manual balance comparison, and scenario awareness.",
        "h1": "Crypto Portfolio AI",
        "eyebrow": "Portfolio Intelligence",
        "intro": "Portfolio AI helps users review exposure, possible upside/downside scenarios, manual balance context, and risk language before acting.",
        "answer": "CoinPilotX portfolio outputs are educational scenarios. They do not promise future returns or tell users to risk money they cannot afford to lose.",
        "points": ["Exposure context", "Manual balance comparison", "Scenario estimates", "Risk-level explanation"],
        "faqs": [
            {"question": "Can Portfolio AI show possible gain/loss scenarios?", "answer": "Yes, it can estimate possible scenarios using conservative percentages for educational context."},
            {"question": "Is Portfolio AI investment advice?", "answer": "No. It is educational AI intelligence only."},
        ] + COMMON_FAQS,
        "related": ["/portfolio-intelligence", "/markets/btc", "/ai-market-analysis"],
    },
    "crypto-learning": {
        "title": "Crypto Learning Tools | CoinPilotXAI Inc.",
        "description": "Learn crypto safety, market context, wallet security, scam awareness, and AI-assisted risk thinking with CoinPilotX.",
        "h1": "Crypto Learning Tools",
        "eyebrow": "Education",
        "intro": "CoinPilotX is built to help users learn safer crypto habits, understand market context, and avoid rushed decisions.",
        "answer": "Good crypto learning focuses on risk, safety, public verification, and emotional discipline before hype or prediction.",
        "points": ["Crypto safety basics", "Wallet security education", "Scam awareness", "Market context explainers"],
        "faqs": [
            {"question": "Is CoinPilotX good for beginners?", "answer": "Yes. CoinPilotX explains crypto risk and safety concepts in plain language."},
            {"question": "Does CoinPilotX encourage risky behavior?", "answer": "No. It encourages users to slow down, verify, and avoid risking money they cannot afford to lose."},
        ] + COMMON_FAQS,
        "related": ["/crypto-safety", "/crypto-scams", "/telegram-crypto-bot"],
    },
})

MARKET_PAGES = {
    "btc": {"name": "Bitcoin", "symbol": "BTC", "title": "Bitcoin AI Market Intelligence | CoinPilotXAI Inc."},
    "eth": {"name": "Ethereum", "symbol": "ETH", "title": "Ethereum AI Market Intelligence | CoinPilotXAI Inc."},
    "sol": {"name": "Solana", "symbol": "SOL", "title": "Solana AI Market Intelligence | CoinPilotXAI Inc."},
}

COUNTRY_PAGES = {
    "us": "United States",
    "uk": "United Kingdom",
    "canada": "Canada",
}

HUBS = {
    "news": {
        "title": "CoinPilotX News Infrastructure | CoinPilotXAI Inc.",
        "description": "A responsible news and updates hub for future crypto market updates, scam alerts, and product announcements from CoinPilotXAI Inc.",
        "h1": "CoinPilotX News",
        "intro": "This hub is prepared for high-quality updates only: product launches, safety notices, crypto education, and platform status. CoinPilotXAI Inc. does not publish fake news or mass-generated spam.",
    },
    "insights": {
        "title": "CoinPilotX Insights | CoinPilotXAI Inc.",
        "description": "Educational insights for AI crypto intelligence, wallet safety, scam awareness, Sports Edge, and portfolio risk context.",
        "h1": "CoinPilotX Insights",
        "intro": "A future home for thoughtful educational guides, scam prevention notes, market context explainers, and AI safety workflows.",
    },
    "intel": {
        "title": "CoinPilotX Intelligence Feed | CoinPilotXAI Inc.",
        "description": "A future intelligence feed for market context, whale movement summaries, scam warnings, and wallet safety updates.",
        "h1": "CoinPilotX Intel",
        "intro": "This indexable feed infrastructure is designed for quality over volume, with clear sourcing and no fake signal claims.",
    },
}


def enrich_page(slug, page):
    enriched = dict(page)
    enriched["slug"] = slug
    enriched["path"] = "/" + slug
    enriched["canonical"] = SITE_URL + enriched["path"]
    enriched["image"] = SHARE_IMAGE_URL
    enriched.setdefault("cta", "Launch CoinPilotX Free")
    enriched.setdefault("updated", "2026-05-10")
    return enriched


def seo_page(slug):
    page = SEO_PAGES.get(slug)
    return enrich_page(slug, page) if page else None


def market_page(symbol):
    key = (symbol or "").lower()
    item = MARKET_PAGES.get(key)
    if not item:
        return None
    page = {
        "title": item["title"],
        "description": f"Review {item['name']} ({item['symbol']}) with CoinPilotX AI market context, live price links, risk reminders, wallet safety, and Telegram-first crypto intelligence.",
        "h1": f"{item['name']} AI Market Intelligence",
        "eyebrow": "Live Market Intent",
        "intro": f"CoinPilotX helps users review {item['symbol']} market context, momentum, volatility, scam risk, whale pressure, and portfolio exposure without promising outcomes.",
        "answer": f"{item['symbol']} analysis should combine live price movement, volume, volatility, market sentiment, and personal risk limits. CoinPilotX keeps the output educational.",
        "points": ["Live market board context", "AI assistant explanations", "Risk and momentum sections", "Telegram command handoff"],
        "faqs": [
            {"question": f"Can CoinPilotX analyze {item['symbol']}?", "answer": f"Yes. CoinPilotX can explain {item['symbol']} market context, risk factors, momentum, and safer next steps using available live market data."},
            {"question": f"Is {item['symbol']} analysis financial advice?", "answer": "No. CoinPilotXAI Inc. provides educational AI intelligence only."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/portfolio-intelligence", "/whale-alerts"],
    }
    enriched = enrich_page(f"markets/{key}", page)
    enriched["market_symbol"] = item["symbol"]
    return enriched


def country_page(slug):
    country = COUNTRY_PAGES.get((slug or "").lower())
    if not country:
        return None
    page = {
        "title": f"{country} Crypto Intelligence | CoinPilotXAI Inc.",
        "description": f"Country-level crypto intelligence for {country}: adoption, regulation, exchange access, scam awareness, remittance context, and market education.",
        "h1": f"{country} Crypto Intelligence",
        "eyebrow": "Country Intelligence",
        "intro": f"CoinPilotX prepares country-level crypto context for {country}, including adoption, regulation, exchange access, scams, remittance use, and regional risk awareness.",
        "answer": "Country crypto context should be educational, current where data is connected, and honest when live regional data is unavailable.",
        "points": ["Adoption context", "Regulation awareness", "Exchange access education", "Scam and remittance risk notes"],
        "faqs": [
            {"question": f"Does CoinPilotX support {country} country intelligence?", "answer": f"Yes. CoinPilotX includes educational country-level crypto intelligence for {country} and other major regions."},
            {"question": "Is country crypto intelligence legal advice?", "answer": "No. It is educational context only, not legal, financial, or investment advice."},
        ] + COMMON_FAQS,
        "related": ["/telegram-crypto-bot", "/crypto-safety", "/ai-market-analysis"],
    }
    enriched = enrich_page(f"country-intelligence/{slug}", page)
    enriched["country"] = country
    return enriched


def hub_page(slug):
    item = HUBS.get(slug)
    if not item:
        return None
    page = {
        **item,
        "eyebrow": "Content Engine",
        "answer": "CoinPilotXAI Inc. publishes only useful, safety-focused, educational content. The system is ready for future articles, but it avoids low-quality AI spam.",
        "points": ["Crypto market updates", "Scam alerts", "Wallet safety guides", "AI sports insights", "Whale movement summaries", "Exchange security alerts"],
        "faqs": [
            {"question": "Will CoinPilotXAI Inc. publish AI spam articles?", "answer": "No. The content system is designed for quality, helpfulness, and trust rather than mass-generated pages."},
            {"question": "What topics will the content hub support?", "answer": "Crypto safety, wallet risk, AI market education, scam awareness, Sports Edge context, whale intelligence, and product updates."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/ai-market-analysis", "/wallet-security"],
    }
    return enrich_page(slug, page)


def all_public_paths():
    paths = ["/", "/support", "/privacy", "/terms"]
    paths += ["/" + slug for slug in SEO_PAGES]
    paths += ["/markets/" + slug for slug in MARKET_PAGES]
    paths += ["/country-intelligence/" + slug for slug in COUNTRY_PAGES]
    paths += ["/" + slug for slug in HUBS]
    return paths
