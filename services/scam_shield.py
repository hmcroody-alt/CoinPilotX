def analyze_text(text):
    lowered = (text or "").lower()
    checks = [
        ("Seed phrase/private key request", ["seed phrase", "private key", "recovery phrase", "12 words", "24 words"], 45),
        ("Fake support/admin pressure", ["support agent", "official support", "telegram admin", "verify your wallet", "sync your wallet"], 32),
        ("Wallet-drainer approval language", ["approve", "unlimited approval", "connect wallet", "sign message", "permit"], 30),
        ("Fake airdrop or claim lure", ["airdrop", "claim now", "free token", "reward", "allocation"], 22),
        ("Urgency and pressure", ["urgent", "limited time", "act now", "last chance", "account will be locked"], 20),
        ("Guaranteed return language", ["guaranteed profit", "risk free", "double your", "daily roi", "sure win"], 34),
        ("Suspicious link pattern", ["http://", "bit.ly", "tinyurl", ".xyz", ".top", ".click"], 18),
    ]
    flags = []
    score = 0
    for label, needles, weight in checks:
        hits = [needle for needle in needles if needle in lowered]
        if hits:
            score += weight
            flags.append({"label": label, "matched": hits[:3], "weight": weight})
    score = min(100, score)
    if score >= 80:
        level = "Critical"
    elif score >= 55:
        level = "High"
    elif score >= 25:
        level = "Medium"
    else:
        level = "Low"
        if not flags:
            flags.append({"label": "No obvious high-risk phrase detected", "matched": [], "weight": 0})
    red_flags = [flag["label"] + (f" ({', '.join(flag['matched'])})" if flag["matched"] else "") for flag in flags]
    recommendations = [
        "Never enter seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.",
        "Verify links through official websites, not DMs, ads, or replies.",
        "Reject wallet approvals you do not fully understand.",
        "Slow down when a message uses urgency, fear, or guaranteed returns.",
    ]
    response = (
        "🛡 Scam Shield Scan\n\n"
        f"Risk Score: {score}/100\n"
        f"Risk Level: {level}\n\n"
        "Red Flags:\n" + "\n".join(f"• {item}" for item in red_flags) + "\n\n"
        "Why It Matters:\nA single wallet approval or seed phrase request can transfer control of funds. Scam messages often use urgency to stop careful thinking.\n\n"
        "Safer Next Step:\n" + "\n".join(f"• {item}" for item in recommendations) + "\n\n"
        "CoinPilotX will never ask for your seed phrase, private key, recovery phrase, wallet password, or signing credentials.\n"
        "Informational only — not financial advice."
    )
    return {"risk_score": score, "risk_level": level, "red_flags": red_flags, "recommendations": recommendations, "response": response}
