from .schema import SHARE_IMAGE_URL, SITE_URL

COMMON_FAQS = [
    {
        "question": "Is Pulse financial advice?",
        "answer": "No. CoinPilotXAI Inc. provides educational AI intelligence only and does not provide financial, betting, investment, or legal advice.",
    },
    {
        "question": "Does Pulse hold user funds?",
        "answer": "No. Pulse never holds user funds and never asks for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.",
    },
]

SEO_PAGES = {
    "wallet-security": {
        "title": "Crypto Wallet Security | Pulse",
        "description": "Learn how Pulse helps crypto users review public wallet activity, wallet-drain warning signs, and safer crypto security habits.",
        "h1": "Crypto Wallet Security Intelligence",
        "eyebrow": "Wallet Safety",
        "intro": "Pulse helps users slow down, review public wallet activity, and understand risk signals before trusting wallet approvals, links, or unknown counterparties.",
        "answer": "Wallet security starts with a simple rule: never share seed phrases, private keys, wallet passwords, or signing credentials. Pulse focuses on public wallet and transaction information only.",
        "points": ["Public wallet and TXID checks", "Wallet-drain warning patterns", "Approval-risk education", "Explorer links for public verification"],
        "faqs": [
            {"question": "What is Wallet Intel?", "answer": "Wallet Intel is Pulse's public wallet and transaction education tool. It helps users review public blockchain context without requesting private credentials."},
            {"question": "Can Pulse recover a wallet?", "answer": "No. CoinPilotXAI Inc. cannot recover wallets and will never ask for seed phrases or private keys."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/crypto-safety", "/ai-market-analysis"],
    },
    "crypto-scams": {
        "title": "Crypto Scam Protection | Pulse",
        "description": "Use Pulse Scam Shield to understand suspicious crypto messages, fake support, wallet-drainer language, phishing, and urgency tactics.",
        "h1": "Crypto Scam Protection and Scam Shield",
        "eyebrow": "Scam Awareness",
        "intro": "Scam Shield is built to identify suspicious language, unsafe wallet instructions, fake support patterns, fake airdrop pressure, and wallet-drainer red flags.",
        "answer": "Most crypto scams rely on speed, confusion, and trust abuse. Pulse gives users a structured risk read and safer next step before they click, connect, or sign.",
        "points": ["Suspicious phrase detection", "Fake support and impersonation patterns", "Seed phrase and private-key danger signs", "Wallet-drain approval warnings"],
        "faqs": [
            {"question": "How does Scam Shield work?", "answer": "Scam Shield uses deterministic risk rules and optional AI classification to flag dangerous phrases, urgency, fake support behavior, phishing patterns, and wallet-drain warning signs."},
            {"question": "Does Scam Shield guarantee a message is safe?", "answer": "No. It provides educational risk context. Users should verify independently and avoid sharing private wallet credentials."},
        ] + COMMON_FAQS,
        "related": ["/wallet-security", "/crypto-safety", "/telegram-crypto-bot"],
    },
    "whale-alerts": {
        "title": "Crypto Whale Alerts | Pulse",
        "description": "Track whale-style market pressure, large crypto movements, BTC intelligence, and educational risk context inside Pulse.",
        "h1": "Whale Alerts and Market Pressure Intelligence",
        "eyebrow": "Whale Intelligence",
        "intro": "Pulse translates whale-style movement, exchange pressure, and market context into plain-language intelligence for crypto users.",
        "answer": "Whale activity can matter, but it should never be treated as certainty. Pulse frames whale pressure as one context factor among trend, volatility, price movement, and risk.",
        "points": ["Whale movement summaries", "Market pressure context", "Risk-aware interpretations", "optional Telegram alerts"],
        "faqs": [
            {"question": "What are whale alerts?", "answer": "Whale alerts are large public-chain or market movement summaries that may indicate unusual market activity or pressure."},
            {"question": "Do whale alerts predict price?", "answer": "No. Whale alerts are context, not predictions or guaranteed signals."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc", "/ai-market-analysis", "/portfolio-intelligence"],
    },
    "sports-edge": {
        "title": "AI Sports Intelligence | Pulse",
        "description": "Sports Edge provides live game context, risk factors, position discipline, and educational sports intelligence without guaranteed picks.",
        "h1": "Sports Edge AI Intelligence",
        "eyebrow": "Sports Edge",
        "intro": "Sports Edge gives users a calmer way to review games, live score context, odds availability, risk factors, and why forcing a position can be dangerous.",
        "answer": "Sports Edge is informational only. It does not provide guaranteed bets, locks, or sure outcomes. It helps users think in probabilities, risk, and discipline.",
        "points": ["Live public scoreboard context", "Sport-specific risk notes", "Position intelligence prompts", "optional Telegram companion for deeper analysis"],
        "faqs": [
            {"question": "What is Sports Edge?", "answer": "Sports Edge is Pulse's informational sports intelligence feature for reviewing game state, risk factors, market context, and position discipline."},
            {"question": "Does Sports Edge give guaranteed picks?", "answer": "No. CoinPilotXAI Inc. does not guarantee sports outcomes or betting results."},
        ] + COMMON_FAQS,
        "related": ["/day-signal", "/ai-market-analysis", "/telegram-crypto-bot"],
    },
    "day-signal": {
        "title": "Pulse Day Signal | Pulse",
        "description": "Use Pulse Day Signal as a confidence, readiness, and risk-awareness check before crypto, sports, business, or personal decisions.",
        "h1": "Pulse Day Signal",
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
        "title": "AI Market Analysis Assistant | Pulse",
        "description": "Pulse provides AI-assisted crypto market explanations, momentum reads, risk context, and safer next steps across web, mobile, and optional Telegram alerts.",
        "h1": "AI Crypto Market Analysis Assistant",
        "eyebrow": "AI Market Intelligence",
        "intro": "Pulse helps users understand market signals, momentum, volatility, wallet risk, scams, and safer decision context without claiming certainty.",
        "answer": "AI market analysis should explain context and risk, not promise outcomes. Pulse uses clear sections so users can see what matters and what could change.",
        "points": ["Market Snapshot", "Momentum Read", "Risk Level", "What to Watch", "Safer Next Step"],
        "faqs": [
            {"question": "How does the AI crypto assistant work?", "answer": "The assistant uses Pulse intelligence workflows and optional OpenAI-powered responses to explain crypto, wallet, scam, sports, and market questions."},
            {"question": "Does the assistant tell me what to buy?", "answer": "No. It provides educational context, possible scenarios, and risk reminders."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc", "/crypto-scams", "/portfolio-intelligence"],
    },
    "telegram-crypto-bot": {
        "title": "Telegram Crypto AI Bot | Pulse",
        "description": "Pulse is an AI crypto intelligence platform for market intelligence, scam protection, wallet checks, portfolio context, and Sports Edge.",
        "h1": "Telegram Crypto AI Bot",
        "eyebrow": "Optional Telegram Companion",
        "intro": "Pulse brings AI crypto intelligence into a platform workflow so users can ask questions, scan risks, review markets, and continue from the website into the bot.",
        "answer": "The platform-first design keeps Pulse fast and easy to access while still connecting to website accounts, Pro access, and shared intelligence systems.",
        "points": ["Optional Telegram companion commands", "Website account linking", "AI assistant handoff", "Pro upgrade flow"],
        "faqs": [
            {"question": "What can the Telegram bot do?", "answer": "The bot can answer AI questions, show live market context, review scam messages, check public wallet data, run Day Signal, and open Sports Edge context."},
            {"question": "Do I need to create an account?", "answer": "Start with the web platform, then connect Telegram only if you want companion alerts and quick commands."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/crypto-safety", "/sports-edge"],
    },
    "crypto-safety": {
        "title": "Crypto Safety Tools | Pulse",
        "description": "Pulse provides crypto safety education, scam awareness, wallet risk context, and public blockchain intelligence tools.",
        "h1": "Crypto Safety Tools and Risk Awareness",
        "eyebrow": "Safety First",
        "intro": "Crypto users need safer habits, better context, and clearer warnings before trusting links, signing wallet approvals, or reacting to market pressure.",
        "answer": "Pulse is built around the idea that users should slow down, verify, and understand risk before acting.",
        "points": ["Scam Shield", "Wallet Intel", "Public TXID checks", "Educational disclaimers", "No custody of funds"],
        "faqs": [
            {"question": "Is Pulse safe to use?", "answer": "Pulse never asks for seed phrases, private keys, recovery phrases, wallet passwords, or wallet custody."},
            {"question": "What data should I avoid entering?", "answer": "Never enter private keys, seed phrases, recovery phrases, wallet passwords, or signing credentials anywhere in Pulse."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/wallet-security", "/telegram-crypto-bot"],
    },
    "portfolio-intelligence": {
        "title": "Crypto Portfolio AI Intelligence | Pulse",
        "description": "Pulse helps users review crypto portfolio value, manual balance context, upside/downside scenarios, and educational risk guidance.",
        "h1": "Crypto Portfolio Intelligence",
        "eyebrow": "Portfolio Context",
        "intro": "Portfolio Intelligence compares holdings, manual balance context, market movement, and scenario estimates so users can review risk before acting.",
        "answer": "Portfolio intelligence is not a prediction engine. It explains exposure, possible scenarios, risk level, and what could change a signal.",
        "points": ["Real-time portfolio context", "Manual balance override", "Upside/downside scenarios", "BUY / SELL / WAIT / HOLD educational logic"],
        "faqs": [
            {"question": "Does portfolio intelligence guarantee profit?", "answer": "No. It estimates possible scenarios and explains risk context for educational purposes."},
            {"question": "Can I track a manual balance?", "answer": "Yes. Pulse supports manual portfolio balance context while keeping live holdings tracking separate."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc", "/ai-market-analysis", "/whale-alerts"],
    },
}

SEO_PAGES.update({
    "platform": {
        "title": "Pulse Platform | AI Crypto Intelligence Command Center",
        "description": "Pulse is a web, mobile, and PWA crypto intelligence platform for AI analysis, wallet risk checks, scam protection, portfolio intelligence, and real-time alerts.",
        "h1": "Pulse AI Intelligence Platform",
        "eyebrow": "Platform",
        "intro": "Pulse is a standalone SaaS platform built around account-based AI intelligence, live market context, alerts, and portfolio tools.",
        "answer": "The platform is the source of truth. Telegram is optional companion access for users who want extra notifications and quick commands.",
        "points": ["Web and mobile command center", "Native AI chat", "Market and wallet intelligence", "Scam Shield", "Portfolio and alerts"],
        "faqs": COMMON_FAQS,
        "related": ["/features", "/pricing", "/ai-market-analysis", "/crypto-safety"],
    },
    "app-preview": {
        "title": "Pulse App Preview | Crypto Intelligence PWA",
        "description": "Preview Pulse's mobile-first command center for AI crypto intelligence, live market alerts, Scam Shield, Wallet Intel, and portfolio tools.",
        "h1": "Pulse App Preview",
        "eyebrow": "App Preview",
        "intro": "The Pulse PWA is designed as the primary app experience for users who want fast command-style crypto intelligence.",
        "answer": "The app preview explains the platform workflow without exposing private account data or indexing private dashboards.",
        "points": ["Mobile-first UI", "Command grid", "Native AI chat", "Pro-gated intelligence", "Optional companion alerts"],
        "faqs": COMMON_FAQS,
        "related": ["/platform", "/features", "/pwa-crypto-app"],
    },
    "live-market": {
        "title": "Live Crypto Market Intelligence | Pulse",
        "description": "Track live crypto market data, BTC and ETH movement, top movers, volatility, risk context, and AI market summaries with Pulse.",
        "h1": "Live Crypto Market Intelligence",
        "eyebrow": "Live Market",
        "intro": "Pulse helps users understand live market movement with source transparency and educational risk context.",
        "answer": "Live market intelligence should explain price movement, volatility, and uncertainty without pretending to predict guaranteed outcomes.",
        "points": ["BTC and ETH context", "Top movers", "Volatility notes", "AI market summary", "Source status"],
        "faqs": COMMON_FAQS,
        "related": ["/markets/btc", "/ai-market-analysis", "/platform"],
    },
    "scam-guide": {
        "title": "Crypto Scam Guide | Pulse",
        "description": "Learn common crypto scam patterns, fake support warnings, wallet-drainer language, phishing signs, and safer verification habits.",
        "h1": "Crypto Scam Guide",
        "eyebrow": "Safety Guide",
        "intro": "Pulse teaches users how to spot crypto scam pressure before clicking, connecting, or signing.",
        "answer": "The safest crypto habit is to never share seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.",
        "points": ["Fake support scripts", "Urgency tactics", "Wallet-drainer warnings", "Phishing red flags"],
        "faqs": COMMON_FAQS,
        "related": ["/crypto-scams", "/crypto-scam-checker", "/crypto-safety"],
    },
    "safety": {
        "title": "Pulse Safety Center | Crypto Risk and Account Protection",
        "description": "Pulse safety rules: never share seed phrases, never enter private keys, use public wallet data only, and review crypto risk carefully.",
        "h1": "Pulse Safety Center",
        "eyebrow": "Trust and Safety",
        "intro": "Pulse is built around public data, account safety, educational AI intelligence, and clear limits.",
        "answer": "CoinPilotXAI Inc. never holds user funds and never asks for private wallet credentials.",
        "points": ["Never holds funds", "Public wallet data only", "Stripe website billing", "Educational AI intelligence only"],
        "faqs": COMMON_FAQS,
        "related": ["/support", "/privacy", "/terms", "/crypto-safety"],
    },
    "faq": {
        "title": "Pulse FAQ | AI Crypto Intelligence Platform",
        "description": "Answers about Pulse Pro, AI crypto intelligence, wallet safety, Scam Shield, portfolio tools, alerts, billing, and optional Telegram access.",
        "h1": "Pulse FAQ",
        "eyebrow": "Frequently Asked Questions",
        "intro": "Quick answers for users evaluating Pulse as a web and mobile crypto intelligence platform.",
        "answer": "Pulse is a standalone platform for AI-powered crypto education, live market context, scam awareness, wallet intelligence, portfolio tools, and alerts.",
        "points": ["Account and Pro access", "Billing and support", "AI safety", "Optional companion access"],
        "faqs": COMMON_FAQS,
        "related": ["/features", "/pricing", "/support"],
    },
    "features": {
        "title": "Pulse Features | AI Crypto Intelligence Platform",
        "description": "Explore Pulse platform features: AI crypto assistant, live market intelligence, Scam Shield, Wallet Intel, portfolio tracking, alerts, Sports Edge, and optional Telegram companion access.",
        "h1": "Pulse Platform Features",
        "eyebrow": "Platform Features",
        "intro": "Pulse is a standalone web and mobile-first crypto intelligence platform with optional Telegram companion access.",
        "answer": "The platform combines AI analysis, live market context, public wallet intelligence, scam detection, portfolio monitoring, alerts, and educational decision support in one account-based SaaS workflow.",
        "points": ["AI Crypto Assistant", "Live Market Intelligence", "Scam Shield", "Wallet Intel", "Portfolio and Watchlist", "Optional Telegram Alerts"],
        "faqs": COMMON_FAQS,
        "related": ["/app", "/ai-market-analysis", "/crypto-safety", "/portfolio-intelligence"],
    },
    "pricing": {
        "title": "Pulse Pricing | Free Market Tools and Pro Arena Training",
        "description": "Compare Pulse Free and Pro access for market tracking, limited AI, alerts, education, Scam Shield, Pro Arena battles, live rooms, and immersive fake-money crypto training.",
        "h1": "Pulse Pricing",
        "eyebrow": "Free, Pro Trial, and Pro",
        "intro": "Start free with basic market tracking, limited AI, limited alerts, and education. Upgrade to Pro for Arena access, live multiplayer fake-money battles, AI tactical systems, advanced alerts, and immersive training.",
        "answer": "Pulse Pro unlocks the Arena ecosystem and deeper intelligence tools. Arena remains educational simulation only: no real-money wagering, no trading execution, and no guaranteed outcomes.",
        "points": ["Free market tracking", "Limited AI and alerts", "Pro Arena access", "Live rooms and fake portfolio battles", "AI tactical commentary", "Pro Trial temporary premium access"],
        "faqs": COMMON_FAQS,
        "related": ["/signup", "/features", "/arena-preview", "/scam-shield", "/support"],
    },
    "crypto-scam-checker": {
        "title": "Crypto Scam Checker | Pulse",
        "description": "Check suspicious crypto messages, fake airdrops, fake support scripts, wallet-drainer language, and phishing risk with Pulse Scam Shield.",
        "h1": "Crypto Scam Checker",
        "eyebrow": "Scam Shield",
        "intro": "A focused landing page for users searching for a crypto scam checker that explains red flags before they click, connect, or sign.",
        "answer": "Pulse Scam Shield reviews suspicious text and URLs for seed phrase requests, fake support pressure, wallet-drainer language, fake airdrops, urgency, and guaranteed-return claims.",
        "points": ["Suspicious message review", "Fake support detection", "Phishing and airdrop warnings", "Safer next-step guidance"],
        "faqs": [
            {"question": "Can I paste suspicious messages into Pulse?", "answer": "Yes. Paste suspicious text into Scam Shield, but never paste private keys, seed phrases, passwords, or signing credentials."},
            {"question": "Is a low scam score a guarantee?", "answer": "No. It is educational context only. Always verify independently."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/wallet-security", "/crypto-safety"],
    },
    "ai-crypto-assistant": {
        "title": "AI Crypto Assistant | Pulse",
        "description": "Ask Pulse AI crypto questions about markets, scams, wallets, portfolio risk, whale activity, and optional Telegram companion workflows.",
        "h1": "AI Crypto Assistant",
        "eyebrow": "Ask Pulse",
        "intro": "Pulse works as a cautious AI crypto assistant for users who want clearer market, scam, wallet, and portfolio context.",
        "answer": "The AI assistant is designed to explain context, risk factors, and safer next steps, not to give guaranteed buy or sell instructions.",
        "points": ["Market context answers", "Scam and wallet education", "Portfolio scenario prompts", "web-first platform workflow"],
        "faqs": [
            {"question": "Can Pulse answer general crypto questions?", "answer": "Yes. It can explain crypto topics, wallet safety, market context, scams, and portfolio awareness in plain language."},
            {"question": "Does Pulse replace professional advice?", "answer": "No. It is educational AI intelligence only."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/telegram-crypto-bot", "/crypto-safety"],
    },
    "telegram-trading-assistant": {
        "title": "Telegram Trading Assistant | Pulse",
        "description": "Pulse is an AI crypto trading education platform for crypto market context, risk awareness, alerts, and safer decision support.",
        "h1": "Telegram Trading Assistant for Risk Awareness",
        "eyebrow": "Optional Telegram Companion",
        "intro": "Pulse supports trading-related education across the Pulse web app and optional Telegram companion while avoiding guaranteed signals, sure wins, or pressure tactics.",
        "answer": "A responsible Telegram trading assistant should help users understand risk, context, and scenarios before acting. Pulse is built around that standard.",
        "points": ["Optional Telegram AI questions", "Live market board", "Scam safety reminders", "Portfolio context"],
        "faqs": [
            {"question": "Does Pulse provide guaranteed trading signals?", "answer": "No. Pulse is operated by CoinPilotXAI Inc. and provides educational signal context, not guaranteed outcomes."},
            {"question": "Can I use Pulse from Telegram?", "answer": "Yes. Pulse is platform-first, with Telegram available as an optional companion."},
        ] + COMMON_FAQS,
        "related": ["/telegram-crypto-bot", "/ai-market-analysis", "/portfolio-intelligence"],
    },
    "ai-wallet-scanner": {
        "title": "AI Wallet Scanner | Pulse",
        "description": "Review public wallet addresses and TXIDs with Pulse Wallet Intel, explorer links, and crypto safety education.",
        "h1": "AI Wallet Scanner for Public Wallet Intelligence",
        "eyebrow": "Wallet Intel",
        "intro": "Pulse Wallet Intel helps users review public wallet and transaction context without asking for private wallet credentials.",
        "answer": "Wallet Intel accepts public wallet addresses and TXIDs only. It never needs seed phrases, private keys, wallet passwords, or recovery phrases.",
        "points": ["Public wallet address checks", "Public TXID context", "Explorer link handoff", "Credential safety warnings"],
        "faqs": [
            {"question": "What wallet data can I enter?", "answer": "Only public wallet addresses and public transaction IDs."},
            {"question": "Can Pulse scan private wallets?", "answer": "No. Pulse never requests private credentials and does not access private wallet data."},
        ] + COMMON_FAQS,
        "related": ["/wallet-security", "/crypto-scam-checker", "/crypto-safety"],
    },
    "sports-intelligence-ai": {
        "title": "Sports Intelligence AI | Pulse",
        "description": "Pulse Sports Edge gives informational game context, risk factors, and position discipline without guaranteed picks.",
        "h1": "Sports Intelligence AI",
        "eyebrow": "Sports Edge",
        "intro": "Sports Edge helps users review live games and risk context more carefully, especially when emotions or live action pressure are high.",
        "answer": "Sports intelligence should explain why a position may be risky, why waiting can be valid, and what missing data could change the view.",
        "points": ["Live game context", "Risk labels", "Sport-specific reasoning", "optional Telegram companion analysis"],
        "faqs": [
            {"question": "Does Sports Edge guarantee bets?", "answer": "No. Sports Edge is informational only and never guarantees betting outcomes."},
            {"question": "What if odds are unavailable?", "answer": "Pulse says odds are unavailable and avoids pretending to know market pricing."},
        ] + COMMON_FAQS,
        "related": ["/sports-edge", "/day-signal", "/telegram-crypto-bot"],
    },
    "whale-tracker": {
        "title": "Crypto Whale Tracker | Pulse",
        "description": "Track whale-style pressure and large movement context with Pulse whale alerts and educational market intelligence.",
        "h1": "Crypto Whale Tracker",
        "eyebrow": "Whale Intelligence",
        "intro": "Pulse whale tracking focuses on context and risk, not sensational claims or guaranteed market direction.",
        "answer": "Whale activity can affect sentiment, but it should be reviewed alongside price trend, volatility, market breadth, and portfolio exposure.",
        "points": ["Large movement context", "Market pressure education", "Risk-aware whale summaries", "Telegram alerts"],
        "faqs": [
            {"question": "Can whale tracking predict price?", "answer": "No. It provides context that may be useful but cannot guarantee direction."},
            {"question": "Where can I review whale alerts?", "answer": "Use the Pulse web app, dashboard, or optional Telegram alerts."},
        ] + COMMON_FAQS,
        "related": ["/whale-alerts", "/markets/btc", "/portfolio-intelligence"],
    },
    "portfolio-ai": {
        "title": "Portfolio AI | Pulse",
        "description": "Use Pulse Portfolio AI for educational crypto exposure context, manual balance comparison, and scenario awareness.",
        "h1": "Crypto Portfolio AI",
        "eyebrow": "Portfolio Intelligence",
        "intro": "Portfolio AI helps users review exposure, possible upside/downside scenarios, manual balance context, and risk language before acting.",
        "answer": "Pulse portfolio outputs are educational scenarios. They do not promise future returns or tell users to risk money they cannot afford to lose.",
        "points": ["Exposure context", "Manual balance comparison", "Scenario estimates", "Risk-level explanation"],
        "faqs": [
            {"question": "Can Portfolio AI show possible gain/loss scenarios?", "answer": "Yes, it can estimate possible scenarios using conservative percentages for educational context."},
            {"question": "Is Portfolio AI investment advice?", "answer": "No. It is educational AI intelligence only."},
        ] + COMMON_FAQS,
        "related": ["/portfolio-intelligence", "/markets/btc", "/ai-market-analysis"],
    },
    "crypto-learning": {
        "title": "Crypto Learning Tools | Pulse",
        "description": "Learn crypto safety, market context, wallet security, scam awareness, and AI-assisted risk thinking with Pulse.",
        "h1": "Crypto Learning Tools",
        "eyebrow": "Education",
        "intro": "Pulse is built to help users learn safer crypto habits, understand market context, and avoid rushed decisions.",
        "answer": "Good crypto learning focuses on risk, safety, public verification, and emotional discipline before hype or prediction.",
        "points": ["Crypto safety basics", "Wallet security education", "Scam awareness", "Market context explainers"],
        "faqs": [
            {"question": "Is Pulse good for beginners?", "answer": "Yes. Pulse explains crypto risk and safety concepts in plain language."},
            {"question": "Does Pulse encourage risky behavior?", "answer": "No. It encourages users to slow down, verify, and avoid risking money they cannot afford to lose."},
        ] + COMMON_FAQS,
        "related": ["/crypto-safety", "/crypto-scams", "/telegram-crypto-bot"],
    },
    "crypto-risk-intelligence": {
        "title": "Crypto Risk Intelligence | Pulse",
        "description": "Understand crypto risk signals, volatility, scams, wallet exposure, whale pressure, and market context with Pulse",
        "h1": "Crypto Risk Intelligence",
        "eyebrow": "Risk Intelligence",
        "intro": "Pulse turns crypto risk factors into clearer educational context so users can slow down before reacting to market pressure.",
        "answer": "Crypto risk intelligence combines market movement, scam awareness, wallet safety, public blockchain signals, and user discipline. It should explain uncertainty instead of pretending to remove it.",
        "points": ["Volatility context", "Scam and phishing risk", "Wallet safety reminders", "Whale pressure as one factor", "Safer decision prompts"],
        "faqs": [
            {"question": "What is crypto risk intelligence?", "answer": "It is structured education about crypto risks such as volatility, scams, wallet exposure, market pressure, and emotional decisions."},
            {"question": "Does CoinPilotXAI Inc. remove crypto risk?", "answer": "No. CoinPilotXAI Inc. explains risk context but cannot remove risk or guarantee outcomes."},
        ] + COMMON_FAQS,
        "related": ["/crypto-safety", "/wallet-security", "/ai-market-analysis"],
    },
    "blockchain-wallet-intelligence": {
        "title": "Blockchain Wallet Intelligence | Pulse",
        "description": "Review public blockchain wallet context, TXID safety, explorer links, and wallet-risk education with Pulse.",
        "h1": "Blockchain Wallet Intelligence",
        "eyebrow": "Public-Chain Context",
        "intro": "Pulse helps users interpret public wallet addresses and public TXIDs without requesting private credentials.",
        "answer": "Blockchain wallet intelligence should use public data only. Pulse never asks for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.",
        "points": ["Public address checks", "TXID context", "Explorer handoff", "Wallet-drain education", "Credential safety warnings"],
        "faqs": [
            {"question": "Can Pulse analyze public wallets?", "answer": "Yes. Pulse can provide educational context for public wallet addresses and TXIDs where public data is available."},
            {"question": "Does wallet intelligence require private keys?", "answer": "No. Private keys and seed phrases should never be entered."},
        ] + COMMON_FAQS,
        "related": ["/wallet-security", "/ai-wallet-scanner", "/crypto-scam-checker"],
    },
    "whale-alerts-telegram-bot": {
        "title": "Whale Alerts Telegram Bot | Pulse",
        "description": "Use Pulse as an AI whale alerts and crypto market pressure education platform.",
        "h1": "Whale Alerts Telegram Bot",
        "eyebrow": "Telegram Whale Context",
        "intro": "Pulse brings whale-style movement context into the web platform and optional Telegram alerts so users can review market pressure without treating it as certainty.",
        "answer": "Whale alerts can help users ask better questions, but they are not guaranteed signals. Pulse frames whale movement alongside trend, volatility, and risk.",
        "points": ["Optional Telegram whale alerts", "Market pressure education", "Risk-aware alert language", "No guaranteed direction claims"],
        "faqs": [
            {"question": "Can whale alerts be viewed in Telegram?", "answer": "Yes. Pulse supports website-first whale alert and market pressure workflows with optional Telegram alerts."},
            {"question": "Are whale alerts trading advice?", "answer": "No. They are educational context only."},
        ] + COMMON_FAQS,
        "related": ["/whale-alerts", "/whale-tracker", "/telegram-crypto-bot"],
    },
    "ai-market-intelligence": {
        "title": "AI Market Intelligence | Pulse",
        "description": "AI market intelligence for crypto users: market snapshot, momentum read, risk level, what to watch, and safer next steps.",
        "h1": "AI Market Intelligence",
        "eyebrow": "Market Context",
        "intro": "Pulse structures market explanations into clear sections so users understand what is known, what is uncertain, and what could change.",
        "answer": "AI market intelligence should summarize context, risk, and uncertainty. Pulse avoids certainty claims and keeps users focused on review before action.",
        "points": ["Market Snapshot", "Momentum Read", "Risk Level", "What to Watch", "Safer Next Step"],
        "faqs": [
            {"question": "What does AI market intelligence include?", "answer": "Pulse includes market snapshot, momentum read, risk level, what to watch, and safer next-step sections."},
            {"question": "Does AI market intelligence guarantee trades?", "answer": "No. It provides educational context only."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/markets/btc", "/crypto-risk-intelligence"],
    },
    "crypto-safety-alerts": {
        "title": "Crypto Safety Alerts | Pulse",
        "description": "Prepare for safer crypto habits with scam story warnings, wallet safety reminders, phishing education, and public-risk signals.",
        "h1": "Crypto Safety Alerts",
        "eyebrow": "Safety Alerts",
        "intro": "Pulse is designed to support ethical safety alerts without fear addiction, spam, or fake urgency.",
        "answer": "Good safety alerts help users pause and verify. Pulse focuses on seed phrase warnings, fake support patterns, wallet drain language, and safer verification habits.",
        "points": ["Scam story learning", "Fake support warnings", "Wallet approval reminders", "No private credential requests"],
        "faqs": [
            {"question": "Will Pulse spam users with fear alerts?", "answer": "No. The retention strategy is daily awareness and education, not panic or over-alerting."},
            {"question": "What is the most important safety rule?", "answer": "Never share seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/crypto-safety", "/crypto-scam-checker"],
    },
    "pwa-crypto-app": {
        "title": "Crypto Intelligence PWA | Pulse",
        "description": "Install CoinPilotXAI Inc. as a progressive web app for faster access to Pulse crypto intelligence and platform workflows.",
        "h1": "Crypto Intelligence Progressive Web App",
        "eyebrow": "Installable App",
        "intro": "Pulse supports a mobile-friendly PWA experience for faster access to AI crypto intelligence, Scam Shield, Day Signal, and optional Telegram companion.",
        "answer": "The Pulse PWA is designed for convenience while live market, AI, sports, and account intelligence still require an internet connection.",
        "points": ["Installable mobile experience", "Fast access to tools", "optional Telegram companion", "Offline fallback safety"],
        "faqs": [
            {"question": "Can Pulse be installed on a phone?", "answer": "Yes. Supported browsers can add the PWA to the home screen."},
            {"question": "Does the PWA work fully offline?", "answer": "No. Live intelligence requires an internet connection."},
        ] + COMMON_FAQS,
        "related": ["/telegram-crypto-bot", "/ai-crypto-assistant", "/crypto-safety"],
    },
})

MARKET_PAGES = {
    "btc": {"name": "Bitcoin", "symbol": "BTC", "title": "Bitcoin AI Market Intelligence | Pulse"},
    "eth": {"name": "Ethereum", "symbol": "ETH", "title": "Ethereum AI Market Intelligence | Pulse"},
    "sol": {"name": "Solana", "symbol": "SOL", "title": "Solanan AI Market Intelligence | Pulse"},
    "bnb": {"name": "BNB", "symbol": "BNB", "title": "BNB AI Market Intelligence | Pulse"},
    "xrp": {"name": "XRP", "symbol": "XRP", "title": "XRP AI Market Intelligence | Pulse"},
    "ada": {"name": "Cardano", "symbol": "ADA", "title": "Cardano AI Market Intelligence | Pulse"},
    "doge": {"name": "Dogecoin", "symbol": "DOGE", "title": "Dogecoin AI Market Intelligence | Pulse"},
    "ton": {"name": "Toncoin", "symbol": "TON", "title": "Toncoin AI Market Intelligence | Pulse"},
    "link": {"name": "Chainlink", "symbol": "LINK", "title": "Chainlink AI Market Intelligence | Pulse"},
    "avax": {"name": "Avalanche", "symbol": "AVAX", "title": "Avalanche AI Market Intelligence | Pulse"},
    "matic": {"name": "Polygon", "symbol": "MATIC", "title": "Polygon AI Market Intelligence | Pulse"},
    "dot": {"name": "Polkadot", "symbol": "DOT", "title": "Polkadot AI Market Intelligence | Pulse"},
}

MARKET_PAGES.update({
    "shib": {"name": "Shiba Inu", "symbol": "SHIB", "title": "Shiba Inu AI Market Intelligence | Pulse"},
    "ltc": {"name": "Litecoin", "symbol": "LTC", "title": "Litecoin AI Market Intelligence | Pulse"},
    "bch": {"name": "Bitcoin Cash", "symbol": "BCH", "title": "Bitcoin Cash AI Market Intelligence | Pulse"},
    "near": {"name": "NEAR Protocol", "symbol": "NEAR", "title": "NEAR AI Market Intelligence | Pulse"},
    "atom": {"name": "Cosmos", "symbol": "ATOM", "title": "Cosmos AI Market Intelligence | Pulse"},
    "arb": {"name": "Arbitrum", "symbol": "ARB", "title": "Arbitrum AI Market Intelligence | Pulse"},
    "op": {"name": "Optimism", "symbol": "OP", "title": "Optimism AI Market Intelligence | Pulse"},
    "apt": {"name": "Aptos", "symbol": "APT", "title": "Aptos AI Market Intelligence | Pulse"},
    "sui": {"name": "Sui", "symbol": "SUI", "title": "Sui AI Market Intelligence | Pulse"},
})

KEYWORD_CLUSTERS = {
    "ai_crypto_intelligence": [
        "ai crypto intelligence",
        "crypto ai assistant",
        "ai market analysis",
        "telegram crypto bot",
    ],
    "crypto_safety": [
        "crypto scam protection",
        "crypto scam checker",
        "wallet drainer warning",
        "crypto safety tools",
    ],
    "wallet_intelligence": [
        "crypto wallet scanner",
        "blockchain wallet intelligence",
        "wallet risk scanner",
        "public txid checker",
    ],
    "sports_edge": [
        "sports betting intelligence",
        "ai sports intelligence",
        "sports edge ai",
        "position risk analysis",
    ],
    "market_pages": [
        "bitcoin prediction",
        "btc price prediction",
        "live crypto market",
        "trending crypto",
    ],
    "telegram_discovery": [
        "telegram crypto bot",
        "telegram ai crypto bot",
        "crypto bot for telegram",
        "coinpilotx telegram",
    ],
    "pwa_growth": [
        "crypto intelligence app",
        "installable crypto app",
        "pwa crypto tools",
        "mobile crypto intelligence",
    ],
}

SEO_PAGES.update({
    "trending-crypto": {
        "title": "Trending Crypto Intelligence | Pulse",
        "description": "Track trending crypto market themes with AI context, live market links, risk reminders, and scam-aware education from Pulse",
        "h1": "Trending Crypto Intelligence",
        "eyebrow": "Trending Markets",
        "intro": "Pulse organizes trending crypto search intent into safer market context, live market data links, and risk-aware AI explanations.",
        "answer": "Trending crypto pages should help users understand why an asset or theme is moving without implying certainty or pushing rushed decisions.",
        "points": ["Live market board", "Top-volume asset context", "Gainer and loser awareness", "Scam and volatility reminders"],
        "faqs": [
            {"question": "Does trending mean a coin is safe?", "answer": "No. Trending only means attention or movement may be elevated. It does not guarantee quality or future performance."},
            {"question": "How does Pulse review trending crypto?", "answer": "Pulse connects market movement, volatility, public data, and safety reminders so users can review context before acting."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc/live", "/ai-market-intelligence", "/crypto-risk-intelligence"],
    },
    "bitcoin-prediction": {
        "title": "Bitcoin Prediction Context | Pulse",
        "description": "Review Bitcoin prediction scenarios with AI risk context, volatility awareness, market pressure, and educational BTC intelligence.",
        "h1": "Bitcoin Prediction Context",
        "eyebrow": "BTC Scenarios",
        "intro": "Pulse frames Bitcoin prediction searches as scenario education, not certainty. The goal is to understand what could support or weaken a BTC view.",
        "answer": "A responsible Bitcoin prediction page explains possible scenarios, risk factors, and what could change the market view. It should never claim a guaranteed BTC price target.",
        "points": ["BTC momentum context", "Volatility and downside risk", "Whale and market-pressure awareness", "Scenario-based thinking"],
        "faqs": [
            {"question": "Can Pulse predict Bitcoin with certainty?", "answer": "No. Pulse is operated by CoinPilotXAI Inc. and provides educational BTC scenario context and risk reminders only."},
            {"question": "What affects a Bitcoin prediction?", "answer": "Trend, volume, macro news, ETF flows, whale pressure, liquidity, sentiment, and volatility can all affect BTC scenarios."},
        ] + COMMON_FAQS,
        "related": ["/markets/btc", "/markets/btc/prediction", "/crypto-risk-intelligence"],
    },
    "btc-price-prediction": {
        "title": "BTC Price Prediction Scenarios | Pulse",
        "description": "BTC price prediction scenarios explained with educational AI market context, risk factors, and live Bitcoin intelligence links.",
        "h1": "BTC Price Prediction Scenarios",
        "eyebrow": "Bitcoin Risk Scenarios",
        "intro": "BTC prediction searches deserve clear risk language. Pulse explains what could support upside, what could create downside, and why certainty is dangerous.",
        "answer": "BTC price prediction should be treated as scenario planning. Pulse avoids fixed guaranteed targets and focuses on market context, volatility, and safer next steps.",
        "points": ["Upside scenario education", "Downside scenario education", "Risk and confidence language", "Live BTC market handoff"],
        "faqs": [
            {"question": "Is a BTC prediction financial advice?", "answer": "No. It is educational scenario context only."},
            {"question": "Where can I see live BTC data?", "answer": "Use the Pulse live market board or the BTC market intelligence page."},
        ] + COMMON_FAQS,
        "related": ["/bitcoin-prediction", "/markets/btc/live", "/ai-market-analysis"],
    },
    "ai-crypto-analysis-tools": {
        "title": "AI Crypto Analysis Tools | Pulse",
        "description": "Explore Pulse AI crypto analysis tools for market context, Scam Shield, Wallet Intel, whale alerts, portfolio scenarios, and platform workflows.",
        "h1": "AI Crypto Analysis Tools",
        "eyebrow": "Analysis Toolkit",
        "intro": "Pulse connects AI market explanations with wallet safety, scam awareness, whale pressure, and portfolio context.",
        "answer": "AI crypto analysis tools are most useful when they explain risk, uncertainty, and context instead of telling users what to do.",
        "points": ["AI assistant", "Market snapshot", "Scam Shield", "Wallet Intel", "Portfolio intelligence"],
        "faqs": [
            {"question": "What AI crypto analysis tools does Pulse include?", "answer": "Pulse includes AI Assistant, live market context, Scam Shield, Wallet Intel, Day Signal, Sports Edge, whale alerts, and portfolio intelligence workflows."},
            {"question": "Can AI crypto tools guarantee results?", "answer": "No. AI tools can explain context but cannot guarantee outcomes."},
        ] + COMMON_FAQS,
        "related": ["/ai-crypto-assistant", "/ai-market-analysis", "/telegram-crypto-bot"],
    },
    "live-crypto-market": {
        "title": "Live Crypto Market Board | Pulse",
        "description": "View live crypto market board context, top volume assets, gainers, losers, and AI risk interpretation from Pulse",
        "h1": "Live Crypto Market Board",
        "eyebrow": "Live Market",
        "intro": "Pulse turns live crypto market data into educational context with top volume, market cap, gainers, losers, and risk reminders.",
        "answer": "Live market data is useful only when paired with risk awareness. Pulse explains movement without promising future direction.",
        "points": ["Top-volume crypto assets", "24h price change", "Market cap context", "Gainer and loser filters", "AI risk interpretation"],
        "faqs": [
            {"question": "Is Pulse market data live?", "answer": "Pulse uses public market APIs where available and clearly shows fallback states when data providers are unavailable."},
            {"question": "Does live market data mean I should trade?", "answer": "No. It is informational context only."},
        ] + COMMON_FAQS,
        "related": ["/trending-crypto", "/markets/btc/live", "/markets/eth/live"],
    },
    "sports-betting-intelligence": {
        "title": "Sports Betting Intelligence | Pulse",
        "description": "Informational sports betting intelligence for game context, risk factors, position discipline, and Sports Edge AI without guaranteed picks.",
        "h1": "Sports Betting Intelligence",
        "eyebrow": "Sports Risk Context",
        "intro": "Sports Edge helps users review game context, position risk, and why waiting can be smarter than forcing a bet.",
        "answer": "Sports betting intelligence should never claim locks or guaranteed wins. Pulse focuses on risk, probability, missing data, and discipline.",
        "points": ["Game context", "Market and odds availability", "Momentum and risk factors", "Why to avoid forcing positions"],
        "faqs": [
            {"question": "Does Pulse provide sports betting locks?", "answer": "No. CoinPilotXAI Inc. does not provide guaranteed sports picks, locks, or risk-free outcomes."},
            {"question": "What does Sports Edge explain?", "answer": "Sports Edge explains game state, risk factors, market context where available, and what could change the view."},
        ] + COMMON_FAQS,
        "related": ["/sports-edge", "/sports-intelligence-ai", "/day-signal"],
    },
    "crypto-education-hub": {
        "title": "Crypto Education Hub | Pulse",
        "description": "Crypto education for scam awareness, wallet security, AI market analysis, portfolio risk, whale movement, and safer platform workflows.",
        "h1": "Crypto Education Hub",
        "eyebrow": "Education",
        "intro": "Pulse organizes crypto education around safety, context, and practical decision support for beginners and serious learners.",
        "answer": "Good crypto education teaches users to verify public data, protect private credentials, understand volatility, and avoid emotional decisions.",
        "points": ["Wallet safety", "Scam awareness", "Market context", "Portfolio risk", "platform workflow education"],
        "faqs": [
            {"question": "Is Pulse for beginners?", "answer": "Yes. Pulse explains market and safety topics in plain language."},
            {"question": "Does Pulse teach private wallet recovery?", "answer": "No. Pulse never asks for seed phrases or private keys and cannot recover wallets."},
        ] + COMMON_FAQS,
        "related": ["/crypto-learning", "/wallet-security", "/crypto-safety"],
    },
    "scam-alerts": {
        "title": "Crypto Scam Alerts | Pulse",
        "description": "Crypto scam alerts and safety education for phishing, fake support, wallet drainers, fake airdrops, urgency tactics, and private-key danger.",
        "h1": "Crypto Scam Alerts",
        "eyebrow": "Scam Alerts",
        "intro": "Pulse Scam Shield helps users identify suspicious patterns before clicking, connecting, signing, or sending funds.",
        "answer": "Scam alerts should be clear and practical. Pulse prioritizes critical safety warnings for every user, not just Pro users.",
        "points": ["Fake support detection", "Wallet-drainer warnings", "Airdrop risk patterns", "Urgency and pressure tactics"],
        "faqs": [
            {"question": "What should I do if a message asks for my seed phrase?", "answer": "Stop immediately. CoinPilotXAI Inc. will never ask for seed phrases, private keys, recovery phrases, or wallet passwords."},
            {"question": "Can scammers copy real brands?", "answer": "Yes. Always verify domains, official channels, and wallet permissions before acting."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/crypto-scam-checker", "/intel/wallet-drainer-warning-signs"],
    },
})

COUNTRY_PAGES = {
    "us": "United States",
    "uk": "United Kingdom",
    "canada": "Canada",
    "mexico": "Mexico",
    "brazil": "Brazil",
    "argentina": "Argentina",
    "france": "France",
    "germany": "Germany",
    "uae": "United Arab Emirates",
    "saudi-arabia": "Saudi Arabia",
    "nigeria": "Nigeria",
    "south-africa": "South Africa",
    "kenya": "Kenya",
    "india": "India",
    "china": "China",
    "japan": "Japan",
    "south-korea": "South Korea",
    "singapore": "Singapore",
    "australia": "Australia",
    "haiti": "Haiti",
    "el-salvador": "El Salvador",
}

HUBS = {
    "news": {
        "title": "Pulse News Infrastructure | Pulse",
        "description": "A responsible news and updates hub for future crypto market updates, scam alerts, and product announcements from Pulse",
        "h1": "Pulse News",
        "intro": "This hub is prepared for high-quality updates only: product launches, safety notices, crypto education, and platform status. Pulse does not publish fake news or mass-generated spam.",
    },
    "insights": {
        "title": "Pulse Insights | Pulse",
        "description": "Educational insights for AI crypto intelligence, wallet safety, scam awareness, Sports Edge, and portfolio risk context.",
        "h1": "Pulse Insights",
        "intro": "A future home for thoughtful educational guides, scam prevention notes, market context explainers, and AI safety workflows.",
    },
    "intel": {
        "title": "Pulse Intelligence Feed | Pulse",
        "description": "A future intelligence feed for market context, whale movement summaries, scam warnings, and wallet safety updates.",
        "h1": "Pulse Intel",
        "intro": "This indexable feed infrastructure is designed for quality over volume, with clear sourcing and no fake signal claims.",
    },
}

SPORTS_SEO_PAGES = {
    "nba": {
        "title": "NBA Sports Edge Intelligence | Pulse",
        "description": "NBA Sports Edge intelligence for pace, scoring runs, live game context, market discipline, and risk-aware position review.",
        "h1": "NBA Sports Edge Intelligence",
        "eyebrow": "NBA Risk Context",
        "intro": "Pulse Sports Edge helps users review NBA game state, pace pressure, scoring swings, and why forcing a position can be risky.",
        "answer": "NBA intelligence should consider game state, pace, scoring runs, market context where available, and the danger of chasing live momentum.",
        "points": ["Pace and scoring runs", "Spread and total context when available", "Live status awareness", "Avoid forcing positions"],
    },
    "nfl": {
        "title": "NFL Sports Edge Intelligence | Pulse",
        "description": "NFL Sports Edge intelligence for game script, scoring volatility, market context, and risk-aware position discipline.",
        "h1": "NFL Sports Edge Intelligence",
        "eyebrow": "NFL Risk Context",
        "intro": "Pulse helps users review NFL game state, time pressure, game script, and uncertainty without guaranteed pick language.",
        "answer": "NFL context can change quickly through turnovers, field position, injuries if publicly available, and late-game strategy. Pulse stays cautious.",
        "points": ["Game script", "Clock and field-position context", "Scoring volatility", "Market discipline"],
    },
    "mlb": {
        "title": "MLB Sports Edge Intelligence | Pulse",
        "description": "MLB Sports Edge intelligence for inning context, bullpen risk, low-scoring volatility, and safer position discipline.",
        "h1": "MLB Sports Edge Intelligence",
        "eyebrow": "MLB Risk Context",
        "intro": "Pulse reviews MLB games with inning state, low-scoring volatility, bullpen risk, and the danger of forcing late positions.",
        "answer": "MLB intelligence should be careful when pitcher, bullpen, lineup, or odds data is unavailable. Pulse says what is missing instead of pretending.",
        "points": ["Inning context", "Bullpen risk where available", "Low-scoring volatility", "Missing-data warnings"],
    },
    "nhl": {
        "title": "NHL Sports Edge Intelligence | Pulse",
        "description": "NHL Sports Edge intelligence for period context, goalie pressure, shot pressure if available, and risk-aware game review.",
        "h1": "NHL Sports Edge Intelligence",
        "eyebrow": "NHL Risk Context",
        "intro": "Pulse frames NHL games around period state, scoring volatility, goalie pressure where available, and disciplined position review.",
        "answer": "NHL outcomes can turn on low-event volatility, penalties, goalie performance, and late empty-net scenarios. Pulse keeps the language probabilistic.",
        "points": ["Period state", "Low-event volatility", "Penalty and goalie context where available", "Avoid chase behavior"],
    },
    "soccer": {
        "title": "Soccer Sports Edge Intelligence | Pulse",
        "description": "Soccer Sports Edge intelligence for time remaining, draw risk, low-scoring volatility, and cautious position analysis.",
        "h1": "Soccer Sports Edge Intelligence",
        "eyebrow": "Soccer Risk Context",
        "intro": "Pulse helps users review soccer matches with draw risk, time remaining, low-scoring volatility, and red-card context if available.",
        "answer": "Soccer intelligence should respect draw risk and low-scoring volatility. Pulse avoids claiming certainty from incomplete match data.",
        "points": ["Draw risk", "Time remaining", "Low-scoring volatility", "Red-card context when available"],
    },
    "tennis": {
        "title": "Tennis Sports Edge Intelligence | Pulse",
        "description": "Tennis Sports Edge intelligence for set momentum, serve advantage, break risk, and responsible position context.",
        "h1": "Tennis Sports Edge Intelligence",
        "eyebrow": "Tennis Risk Context",
        "intro": "Pulse reviews tennis through set state, serve pressure, break risk, and why one momentum swing should not create overconfidence.",
        "answer": "Tennis context can shift through serve holds, break points, fatigue, and set pressure. Pulse explains risk without guarantees.",
        "points": ["Set and game state", "Serve advantage", "Break risk", "Momentum caution"],
    },
    "live-games": {
        "title": "Live Sports Edge Games | Pulse",
        "description": "Live Sports Edge game context, risk labels, odds availability notes, and optional Telegram companion for deeper AI analysis.",
        "h1": "Live Sports Edge Games",
        "eyebrow": "Live Sports",
        "intro": "Pulse Sports Edge organizes live and upcoming games into risk-aware cards with honest source and odds availability notes.",
        "answer": "Live sports context is not a betting guarantee. Pulse highlights what is known, what is missing, and why waiting can be the safest move.",
        "points": ["Live/upcoming status", "Odds availability", "Risk labels", "Telegram deep-analysis handoff"],
    },
}

ARTICLE_PAGES = {
    "crypto-scam-alert-checklist": {
        "title": "Crypto Scam Alert Checklist | Pulse",
        "description": "A practical crypto scam alert checklist covering fake support, wallet drainers, seed phrase requests, urgency tactics, and safer next steps.",
        "h1": "Crypto Scam Alert Checklist",
        "eyebrow": "Safety Guide",
        "intro": "Use this checklist before clicking crypto links, connecting wallets, joining airdrops, or trusting support messages.",
        "answer": "If a message asks for seed phrases, private keys, recovery phrases, wallet passwords, urgent deposits, or guaranteed returns, treat it as high risk.",
        "points": ["Seed phrase request", "Fake support pressure", "Wallet-drainer approval", "Guaranteed-return claim", "Urgency or isolation tactic"],
        "sections": [
            {"title": "Why this matters", "body": "Scams usually work by rushing the user into trusting a fake authority or signing a dangerous wallet approval."},
            {"title": "Safer response", "body": "Pause, verify through official channels, avoid signing unknown transactions, and never share private wallet credentials."},
        ],
        "faqs": [
            {"question": "What is the biggest crypto scam red flag?", "answer": "Any request for a seed phrase, private key, recovery phrase, wallet password, or signing credential should be treated as critical risk."},
            {"question": "Can Pulse verify every scam?", "answer": "No. Pulse provides risk context and safer next steps, but users must verify independently."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/crypto-scam-checker", "/wallet-security"],
        "keywords": KEYWORD_CLUSTERS["crypto_safety"] + ["crypto phishing", "fake crypto support"],
    },
    "wallet-drainer-warning-signs": {
        "title": "Wallet Drainer Warning Signs | Pulse",
        "description": "Learn wallet drainer warning signs, unsafe approval patterns, fake airdrops, impersonation, and public wallet risk education.",
        "h1": "Wallet Drainer Warning Signs",
        "eyebrow": "Wallet Safety",
        "intro": "Wallet drainers often hide behind fake airdrops, fake support, malicious approvals, and pressure to connect quickly.",
        "answer": "The safest rule is simple: never sign wallet actions you do not understand, and never enter seed phrases or private keys.",
        "points": ["Unknown approval requests", "Fake claim pages", "Urgent connect-wallet prompts", "Impersonated support accounts"],
        "sections": [
            {"title": "What to verify", "body": "Check the domain, official channels, contract permissions, and whether the action asks for broad token approvals."},
            {"title": "What to avoid", "body": "Avoid connecting wallets to unfamiliar links, signing blind approvals, or trusting DMs that create urgency."},
        ],
        "faqs": [
            {"question": "Can a wallet drainer steal funds without my seed phrase?", "answer": "A malicious approval or transaction signature can create serious risk even if you never share your seed phrase."},
            {"question": "Should I paste private keys into a scanner?", "answer": "No. Never paste private keys, seed phrases, wallet passwords, or recovery phrases into any tool."},
        ] + COMMON_FAQS,
        "related": ["/wallet-security", "/ai-wallet-scanner", "/intel/crypto-scam-alert-checklist"],
        "keywords": KEYWORD_CLUSTERS["wallet_intelligence"] + ["wallet drainer", "unsafe wallet approval"],
    },
    "bitcoin-risk-scenarios": {
        "title": "Bitcoin Risk Scenarios | Pulse",
        "description": "Bitcoin risk scenarios explained with AI market context, volatility, whale pressure, liquidity, and educational BTC scenario planning.",
        "h1": "Bitcoin Risk Scenarios",
        "eyebrow": "BTC Education",
        "intro": "Bitcoin risk work should focus on what could change the view, not on pretending any forecast is certain.",
        "answer": "A useful Bitcoin scenario compares upside drivers, downside risks, volatility, whale pressure, and portfolio exposure.",
        "points": ["Upside catalysts", "Downside risks", "Volatility", "Whale pressure", "Portfolio exposure"],
        "sections": [
            {"title": "Upside scenario", "body": "Upside may be supported by improving trend, stronger volume, favorable macro context, or lower sell pressure."},
            {"title": "Downside scenario", "body": "Downside may be driven by rapid volatility, exchange inflows, negative news, or weak market breadth."},
        ],
        "faqs": [
            {"question": "Does Pulse publish Bitcoin price targets?", "answer": "Pulse focuses on educational scenarios and avoids guaranteed price targets."},
            {"question": "What should change a BTC view?", "answer": "Trend, liquidity, volatility, whale pressure, news, and risk appetite can all change the view."},
        ] + COMMON_FAQS,
        "related": ["/bitcoin-prediction", "/markets/btc/prediction", "/markets/btc/live"],
        "keywords": KEYWORD_CLUSTERS["market_pages"] + ["bitcoin risk", "btc scenario analysis"],
    },
    "ai-crypto-analysis-framework": {
        "title": "AI Crypto Analysis Framework | Pulse",
        "description": "A responsible AI crypto analysis framework for market snapshots, momentum, risk level, what to watch, and safer next steps.",
        "h1": "AI Crypto Analysis Framework",
        "eyebrow": "AI Framework",
        "intro": "Pulse uses structured sections so crypto users can separate market context from speculation.",
        "answer": "A responsible AI analysis should explain market snapshot, momentum read, risk level, what to watch, safer next step, and the limits of the data.",
        "points": ["Market Snapshot", "Momentum Read", "Risk Level", "What to Watch", "Safer Next Step"],
        "sections": [
            {"title": "Why structure matters", "body": "Structured analysis reduces emotional decision-making by making assumptions, missing data, and risk visible."},
            {"title": "What AI should not do", "body": "AI should not claim guaranteed profit, insider knowledge, or certainty about price direction."},
        ],
        "faqs": [
            {"question": "Can AI replace risk management?", "answer": "No. AI can organize context, but users remain responsible for decisions and risk limits."},
            {"question": "Does Pulse use OpenAI?", "answer": "Pulse can use OpenAI-powered responses server-side where configured, without exposing API keys in the browser."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/ai-crypto-assistant", "/ai-market-intelligence"],
        "keywords": KEYWORD_CLUSTERS["ai_crypto_intelligence"] + ["crypto analysis framework"],
    },
    "sports-edge-position-discipline": {
        "title": "Sports Edge Position Discipline | Pulse",
        "description": "Sports Edge position discipline for live games, odds uncertainty, risk factors, and avoiding forced bets or guaranteed-pick thinking.",
        "h1": "Sports Edge Position Discipline",
        "eyebrow": "Sports Discipline",
        "intro": "Sports Edge is built around patience: understand game context and risk before considering any position.",
        "answer": "Position discipline means accepting that no game is guaranteed and that missing data can be a reason to wait.",
        "points": ["Avoid forced positions", "Review missing data", "Respect volatility", "Never chase losses"],
        "sections": [
            {"title": "What to watch", "body": "Game state, odds availability, scoring pace, injuries if publicly available, and market movement can all matter."},
            {"title": "Final caution", "body": "Sports Edge is informational only and not betting advice. Never risk money you cannot afford to lose."},
        ],
        "faqs": [
            {"question": "Does Sports Edge give locks?", "answer": "No. CoinPilotXAI Inc. does not provide locks, sure bets, or guaranteed outcomes."},
            {"question": "When should a user wait?", "answer": "Waiting may be appropriate when data is incomplete, risk is elevated, or the user feels pressured."},
        ] + COMMON_FAQS,
        "related": ["/sports-edge", "/sports-betting-intelligence", "/sports-edge/live-games"],
        "keywords": KEYWORD_CLUSTERS["sports_edge"] + ["sports risk management"],
    },
    "telegram-crypto-bot-safety-guide": {
        "title": "Telegram Crypto Bot Safety Guide | Pulse",
        "description": "A Telegram crypto bot safety guide for avoiding fake support, private-key requests, wallet-drainer links, and risky bot interactions.",
        "h1": "Telegram Crypto Bot Safety Guide",
        "eyebrow": "Telegram Safety",
        "intro": "Telegram is fast and convenient, but crypto users need strong safety habits before trusting links, bots, or direct messages.",
        "answer": "CoinPilotXAI Inc. will never ask for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials in Telegram.",
        "points": ["Avoid fake support DMs", "Verify official bot links", "Never share wallet secrets", "Use public data only"],
        "sections": [
            {"title": "Safer Telegram habits", "body": "Use official links, verify bot usernames carefully, and avoid clicking wallet-connect links sent through unsolicited messages."},
            {"title": "Pulse workflow", "body": "Pulse is platform-first, and account, subscription, and safety flows should remain transparent and user-controlled."},
        ],
        "faqs": [
            {"question": "Can Telegram bots ask for seed phrases?", "answer": "A legitimate Pulse flow never asks for seed phrases or private keys. Treat those requests as dangerous."},
            {"question": "What is the official Pulse bot?", "answer": "Telegram is an optional Pulse companion. The official link used on the site is https://t.me/DocShieldX_bot."},
        ] + COMMON_FAQS,
        "related": ["/telegram-crypto-bot", "/crypto-safety", "/crypto-scam-checker"],
        "keywords": KEYWORD_CLUSTERS["telegram_discovery"] + ["telegram crypto safety"],
    },
}


def default_keywords(slug, page):
    text = " ".join([
        slug.replace("/", " "),
        page.get("title", ""),
        page.get("h1", ""),
        page.get("description", ""),
    ]).lower()
    keywords = set()
    for cluster_name, cluster_terms in KEYWORD_CLUSTERS.items():
        cluster_tokens = cluster_name.replace("_", " ").split()
        if any(token in text for token in cluster_tokens):
            keywords.update(cluster_terms)
    if "scam" in text or "safety" in text:
        keywords.update(KEYWORD_CLUSTERS["crypto_safety"])
    if "wallet" in text:
        keywords.update(KEYWORD_CLUSTERS["wallet_intelligence"])
    if "sports" in text:
        keywords.update(KEYWORD_CLUSTERS["sports_edge"])
    if "telegram" in text:
        keywords.update(KEYWORD_CLUSTERS["telegram_discovery"])
    if "market" in text or "bitcoin" in text or "btc" in text:
        keywords.update(KEYWORD_CLUSTERS["market_pages"])
    if not keywords:
        keywords.update(KEYWORD_CLUSTERS["ai_crypto_intelligence"])
    return sorted(keywords)[:10]


def enrich_page(slug, page):
    enriched = dict(page)
    enriched["slug"] = slug
    enriched["path"] = "/" + slug
    enriched["canonical"] = SITE_URL + enriched["path"]
    enriched["image"] = SHARE_IMAGE_URL
    enriched.setdefault("cta", "Launch Pulse Free")
    enriched.setdefault("updated", "2026-05-10")
    enriched.setdefault("published", "2026-05-10")
    enriched.setdefault("keywords", default_keywords(slug, enriched))
    enriched.setdefault("og_type", "website")
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
        "description": f"Review {item['name']} ({item['symbol']}) with Pulse AI market context, live price links, risk reminders, wallet safety, and AI crypto intelligence with optional Telegram alerts.",
        "h1": f"{item['name']} AI Market Intelligence",
        "eyebrow": "Live Market Intent",
        "intro": f"Pulse helps users review {item['symbol']} market context, momentum, volatility, scam risk, whale pressure, and portfolio exposure without promising outcomes.",
        "answer": f"{item['symbol']} analysis should combine live price movement, volume, volatility, market sentiment, and personal risk limits. Pulse keeps the output educational.",
        "points": ["Live market board context", "AI assistant explanations", "Risk and momentum sections", "platform command center"],
        "faqs": [
            {"question": f"Can Pulse analyze {item['symbol']}?", "answer": f"Yes. Pulse can explain {item['symbol']} market context, risk factors, momentum, and safer next steps using available live market data."},
            {"question": f"Is {item['symbol']} analysis financial advice?", "answer": "No. CoinPilotXAI Inc. provides educational AI intelligence only."},
        ] + COMMON_FAQS,
        "related": ["/ai-market-analysis", "/portfolio-intelligence", "/whale-alerts"],
    }
    enriched = enrich_page(f"markets/{key}", page)
    enriched["market_symbol"] = item["symbol"]
    return enriched


def market_prediction_page(symbol):
    key = (symbol or "").lower()
    item = MARKET_PAGES.get(key)
    if not item:
        return None
    page = {
        "title": f"{item['name']} Prediction Scenarios | Pulse",
        "description": f"Educational {item['symbol']} prediction scenarios with AI market context, volatility, risk factors, and live crypto intelligence links.",
        "h1": f"{item['name']} Prediction Scenarios",
        "eyebrow": "Scenario Analysis",
        "intro": f"Pulse frames {item['symbol']} prediction searches as scenario education: what could support upside, what could create downside, and what would change the view.",
        "answer": f"{item['symbol']} prediction work should never be treated as certainty. Pulse focuses on market context, volatility, volume, risk, and safer review before action.",
        "points": ["Upside scenario education", "Downside scenario education", "Risk and confidence language", "Live market handoff"],
        "sections": [
            {"title": "Upside context", "body": "Upside may be supported by stronger trend, improving breadth, rising volume, positive news, and lower visible sell pressure."},
            {"title": "Downside context", "body": "Downside may emerge from volatility spikes, rapid reversals, weak market breadth, negative news, or broader risk-off behavior."},
        ],
        "faqs": [
            {"question": f"Is this a guaranteed {item['symbol']} prediction?", "answer": "No. It is educational scenario context only, not a guaranteed forecast."},
            {"question": f"What can change a {item['symbol']} view?", "answer": "Trend, volume, volatility, liquidity, news, whale pressure, and overall market risk can all change the view."},
        ] + COMMON_FAQS,
        "related": [f"/markets/{key}", f"/markets/{key}/live", "/ai-market-analysis"],
        "og_type": "article",
        "breadcrumb": f"{item['name']} Prediction",
    }
    enriched = enrich_page(f"markets/{key}/prediction", page)
    enriched["market_symbol"] = item["symbol"]
    return enriched


def market_live_page(symbol):
    key = (symbol or "").lower()
    item = MARKET_PAGES.get(key)
    if not item:
        return None
    page = {
        "title": f"Live {item['name']} Market Data | Pulse",
        "description": f"Live {item['symbol']} market context, price movement, 24h change, volume awareness, and Pulse educational risk interpretation.",
        "h1": f"Live {item['name']} Market Data",
        "eyebrow": "Live Market",
        "intro": f"Track {item['symbol']} live market context with Pulse and connect movement to safer educational analysis.",
        "answer": f"Live {item['symbol']} data is useful when it is reviewed with trend, volatility, volume, and risk context. Pulse avoids turning live movement into certainty.",
        "points": ["Live price context", "24h change", "Volume and market cap context", "Risk-aware interpretation"],
        "faqs": [
            {"question": f"Is {item['symbol']} live market data available?", "answer": "Pulse uses public market APIs where available and shows fallback states when providers are unavailable."},
            {"question": "Does live data tell me what to buy?", "answer": "No. Live data is informational and should be reviewed with risk management."},
        ] + COMMON_FAQS,
        "related": [f"/markets/{key}", f"/markets/{key}/prediction", "/live-crypto-market"],
        "breadcrumb": f"Live {item['symbol']}",
    }
    enriched = enrich_page(f"markets/{key}/live", page)
    enriched["market_symbol"] = item["symbol"]
    return enriched


def country_page(slug):
    country = COUNTRY_PAGES.get((slug or "").lower())
    if not country:
        return None
    page = {
        "title": f"{country} Crypto Intelligence | Pulse",
        "description": f"Country-level crypto intelligence for {country}: adoption, regulation, exchange access, scam awareness, remittance context, and market education.",
        "h1": f"{country} Crypto Intelligence",
        "eyebrow": "Country Intelligence",
        "intro": f"Pulse prepares country-level crypto context for {country}, including adoption, regulation, exchange access, scams, remittance use, and regional risk awareness.",
        "answer": "Country crypto context should be educational, current where data is connected, and honest when live regional data is unavailable.",
        "points": ["Adoption context", "Regulation awareness", "Exchange access education", "Scam and remittance risk notes"],
        "faqs": [
            {"question": f"Does Pulse support {country} country intelligence?", "answer": f"Yes. Pulse includes educational country-level crypto intelligence for {country} and other major regions."},
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
        "answer": "Pulse publishes only useful, safety-focused, educational content. The system is ready for future articles, but it avoids low-quality AI spam.",
        "points": ["Crypto market updates", "Scam alerts", "Wallet safety guides", "AI sports insights", "Whale movement summaries", "Exchange security alerts"],
        "faqs": [
            {"question": "Will Pulse publish AI spam articles?", "answer": "No. The content system is designed for quality, helpfulness, and trust rather than mass-generated pages."},
            {"question": "What topics will the content hub support?", "answer": "Crypto safety, wallet risk, AI market education, scam awareness, Sports Edge context, whale intelligence, and product updates."},
        ] + COMMON_FAQS,
        "related": ["/crypto-scams", "/ai-market-analysis", "/wallet-security"],
    }
    return enrich_page(slug, page)


def sports_page(slug):
    key = (slug or "").lower()
    item = SPORTS_SEO_PAGES.get(key)
    if not item:
        return None
    page = {
        **item,
        "faqs": [
            {"question": "Does Pulse Sports Edge guarantee outcomes?", "answer": "No. Sports Edge is informational only and does not provide guaranteed picks, locks, or betting outcomes."},
            {"question": "What happens if live odds are missing?", "answer": "Pulse clearly states that odds are unavailable and avoids inventing market pricing."},
        ] + COMMON_FAQS,
        "related": ["/sports-edge", "/sports-betting-intelligence", "/sports-intelligence-ai"],
        "og_type": "article",
    }
    return enrich_page(f"sports-edge/{key}", page)


def article_page(slug):
    item = ARTICLE_PAGES.get((slug or "").lower())
    if not item:
        return None
    page = {
        **item,
        "og_type": "article",
        "breadcrumb": item["h1"],
    }
    return enrich_page(f"intel/{slug}", page)


def seo_index_payload():
    pages = searchable_pages()
    return {
        "brand": "Pulse",
        "legal_name": "CoinPilotXAI Inc.",
        "site": SITE_URL + "/",
        "telegram_bot": "https://t.me/DocShieldX_bot",
        "purpose": "Educational AI intelligence for crypto market context, scam awareness, wallet safety, portfolio scenarios, Sports Edge, and web-first platform workflows.",
        "safety": [
            "Educational only, not financial, betting, investment, or legal advice.",
            "Pulse never asks for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.",
            "No guaranteed profit, guaranteed sports picks, or certainty claims.",
        ],
        "keyword_clusters": KEYWORD_CLUSTERS,
        "public_pages": [
            {
                "title": page["title"],
                "url": page["canonical"],
                "description": page["description"],
                "keywords": page.get("keywords", []),
            }
            for page in pages
        ],
    }


def searchable_pages():
    pages = []
    pages.extend(seo_page(slug) for slug in SEO_PAGES)
    pages.extend(market_page(slug) for slug in MARKET_PAGES)
    pages.extend(market_prediction_page(slug) for slug in MARKET_PAGES)
    pages.extend(market_live_page(slug) for slug in MARKET_PAGES)
    pages.extend(country_page(slug) for slug in COUNTRY_PAGES)
    pages.extend(sports_page(slug) for slug in SPORTS_SEO_PAGES)
    pages.extend(article_page(slug) for slug in ARTICLE_PAGES)
    pages.extend(hub_page(slug) for slug in HUBS)
    return [page for page in pages if page]


def search_pages(query, limit=12):
    terms = [term for term in (query or "").lower().split() if len(term) > 1]
    if not terms:
        return []

    results = []
    for page in searchable_pages():
        haystack_parts = [
            page.get("title", ""),
            page.get("h1", ""),
            page.get("description", ""),
            page.get("intro", ""),
            page.get("answer", ""),
            " ".join(page.get("points", [])),
        ]
        for item in page.get("faqs", []):
            haystack_parts.extend([item.get("question", ""), item.get("answer", "")])
        haystack = " ".join(haystack_parts).lower()
        score = sum(haystack.count(term) for term in terms)
        exact_title_bonus = sum(4 for term in terms if term in page.get("title", "").lower())
        exact_h1_bonus = sum(3 for term in terms if term in page.get("h1", "").lower())
        total_score = score + exact_title_bonus + exact_h1_bonus
        if total_score > 0:
            results.append({
                "title": page["h1"],
                "description": page["description"],
                "url": page["path"],
                "score": total_score,
            })
    results.sort(key=lambda item: (-item["score"], item["title"]))
    return results[:limit]


def all_public_paths():
    paths = ["/", "/about", "/signup", "/support", "/privacy", "/terms", "/quote", "/quote/crypto/BTC", "/quote/crypto/ETH", "/predictions/crypto", "/sports-edge", "/arena-preview"]
    paths += ["/" + slug for slug in SEO_PAGES]
    paths += ["/markets/" + slug for slug in MARKET_PAGES]
    paths += ["/markets/" + slug + "/prediction" for slug in MARKET_PAGES]
    paths += ["/markets/" + slug + "/live" for slug in MARKET_PAGES]
    paths += ["/country-intelligence/" + slug for slug in COUNTRY_PAGES]
    paths += ["/sports-edge/" + slug for slug in SPORTS_SEO_PAGES]
    paths += ["/intel/" + slug for slug in ARTICLE_PAGES]
    paths += ["/" + slug for slug in HUBS]
    return paths
