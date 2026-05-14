import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import requests


DISCLAIMER = "Do not share seed phrases, private keys, recovery phrases, wallet passwords, exchange passwords, or signing credentials."

URL_RE = re.compile(r"https?://[^\s<>'\"]+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>'\"]*)?", re.I)
ETH_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
BTC_ADDRESS_RE = re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{25,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")

TRUSTED_DOMAINS = {
    "coinbase.com", "kraken.com", "gemini.com", "crypto.com", "binance.com",
    "metamask.io", "ledger.com", "trezor.io", "etherscan.io", "bscscan.com",
    "polygonscan.com", "coinpilotx.app",
}

TYPO_TARGETS = {
    "metamask": "metamask.io",
    "coinbase": "coinbase.com",
    "binance": "binance.com",
    "kraken": "kraken.com",
    "ledger": "ledger.com",
    "trezor": "trezor.io",
    "etherscan": "etherscan.io",
}

RULES = [
    {
        "label": "Seed phrase or private key request",
        "threat": "Credential theft",
        "needles": ["seed phrase", "secret phrase", "recovery phrase", "private key", "12 words", "24 words", "mnemonic", "wallet password"],
        "weight": 75,
        "safe": "Do not enter or send any recovery phrase, private key, wallet password, or signing credential.",
    },
    {
        "label": "Fake wallet connect / approval request",
        "threat": "Wallet drainer",
        "needles": ["connect wallet", "sync wallet", "validate wallet", "verify wallet", "wallet connect", "approve transaction", "unlimited approval", "sign message", "permit signature"],
        "weight": 55,
        "safe": "Do not sign approvals or messages unless you understand the exact permission and verified the official domain.",
    },
    {
        "label": "Fake airdrop or claim lure",
        "threat": "Airdrop phishing",
        "needles": ["airdrop", "claim now", "claim reward", "free token", "token allocation", "bonus allocation", "mint now"],
        "weight": 26,
        "safe": "Verify claims from the project’s official website and channels. Use a separate low-value wallet for experiments.",
    },
    {
        "label": "Guaranteed profit claim",
        "threat": "Investment scam",
        "needles": ["guaranteed profit", "guaranteed return", "risk free", "sure profit", "10x guaranteed", "double your money", "daily roi", "fixed roi", "no risk"],
        "weight": 38,
        "safe": "Treat guaranteed crypto returns as a major red flag. Do not send funds to prove eligibility.",
    },
    {
        "label": "Withdrawal unlock / tax fee pressure",
        "threat": "Fake exchange deposit scam",
        "needles": ["pay tax to withdraw", "unlock withdrawal", "withdrawal fee", "anti money laundering fee", "verification deposit", "send crypto to verify", "release your funds"],
        "weight": 55,
        "safe": "Do not send extra crypto to unlock alleged funds. Contact the official platform through the app or typed URL.",
    },
    {
        "label": "Fake support or impersonation",
        "threat": "Impersonation",
        "needles": ["support agent", "official support", "admin support", "metamask support", "coinbase support", "telegram admin", "dm support", "customer service wallet"],
        "weight": 55,
        "safe": "Use support only inside the official app or a manually typed official domain. Real support never asks for seed phrases.",
    },
    {
        "label": "Urgency / fear pressure",
        "threat": "Social engineering",
        "needles": ["urgent", "act now", "limited time", "last chance", "wallet will be suspended", "account will be locked", "account is locked", "lose your funds", "final warning", "immediately"],
        "weight": 30,
        "safe": "Slow down. Scammers use deadlines to stop verification.",
    },
    {
        "label": "Recovery service claim",
        "threat": "Fake recovery scam",
        "needles": ["recover stolen crypto", "recovery expert", "fund recovery", "hack back", "recovery fee", "guaranteed recovery"],
        "weight": 36,
        "safe": "Be cautious with recovery services. Never give them wallet credentials or pay upfront recovery fees.",
    },
    {
        "label": "Romance / mentor investment pattern",
        "threat": "Pig-butchering scam",
        "needles": ["my analyst", "investment manager", "mentor", "special platform", "vip trading", "small withdrawal first", "trust me", "professor group"],
        "weight": 28,
        "safe": "Do not use trading platforms introduced by strangers or online relationships.",
    },
]


def _normalize_domain(domain):
    domain = (domain or "").lower().strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _extract_urls(text):
    urls = []
    for match in URL_RE.findall(text or ""):
        candidate = match.strip().rstrip(".,);]")
        parsed = urlparse(candidate if candidate.startswith(("http://", "https://")) else "https://" + candidate)
        if parsed.netloc:
            urls.append({"raw": candidate, "domain": _normalize_domain(parsed.netloc), "scheme": parsed.scheme})
    return urls


def _domain_findings(urls):
    findings = []
    for item in urls:
        domain = item["domain"]
        if item["scheme"] == "http":
            findings.append(("Non-HTTPS link", f"{item['raw']} uses HTTP instead of HTTPS.", 14))
        if domain.startswith("xn--"):
            findings.append(("Punycode domain", f"{domain} may be an internationalized lookalike domain.", 24))
        if any(domain.endswith(tld) for tld in [".xyz", ".top", ".click", ".bond", ".cam", ".zip", ".mov", ".quest"]):
            findings.append(("High-abuse domain ending", f"{domain} uses a domain ending commonly seen in phishing.", 22))
        if any(short in domain for short in ["bit.ly", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "rebrand.ly"]):
            findings.append(("Shortened URL", f"{domain} hides the final destination.", 18))
        for brand, official in TYPO_TARGETS.items():
            if brand in domain and not domain.endswith(official):
                findings.append(("Possible typo-squatting / impersonation", f"{domain} references {brand} but is not {official}.", 42))
        if domain in TRUSTED_DOMAINS or any(domain.endswith("." + trusted) for trusted in TRUSTED_DOMAINS):
            findings.append(("Recognized domain", f"{domain} appears to be a known domain, but still verify the exact page and action.", -8))
    return findings


def _address_findings(text):
    findings = []
    eth = ETH_ADDRESS_RE.findall(text or "")
    btc = BTC_ADDRESS_RE.findall(text or "")
    if eth:
        findings.append(("Public EVM address detected", f"Found {len(eth)} public EVM address(es). Analyze only public activity; never share private keys.", 4))
    if btc:
        findings.append(("Public BTC address detected", f"Found {len(btc)} public BTC address(es). Analyze only public activity; never share wallet secrets.", 4))
    if re.search(r"\b0x[a-fA-F0-9]{8,39}\b", text or ""):
        findings.append(("Partial contract/address fragment", "A partial contract or address can be used to confuse users. Verify full addresses in official explorers.", 10))
    return findings, {"evm_addresses": eth[:10], "btc_addresses": btc[:10]}


def _openai_assessment(text, local_level):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not text or len(text) > 5000:
        return None
    try:
        payload = {
            "model": os.getenv("OPENAI_SCAM_MODEL", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": "Classify crypto scam risk. Return compact JSON with scam_type, explanation, and safe_actions. Do not provide exploit instructions."},
                {"role": "user", "content": f"Local risk level: {local_level}\nText:\n{text[:5000]}"},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=12,
        )
        if not response.ok:
            return {"error": f"OpenAI unavailable: {response.status_code}"}
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as exc:
        return {"error": str(exc)[:240]}


def _level(score, flags_count=0, has_urls=False):
    if score >= 85:
        return "Critical"
    if score >= 55:
        return "High"
    if score >= 32:
        return "Medium"
    if flags_count == 0 and not has_urls:
        return "Unknown"
    return "Low"


def analyze_text(text):
    original = text or ""
    lowered = original.lower()
    urls = _extract_urls(original)
    threats = []
    red_flags = []
    safe_actions = []
    score = 0

    for rule in RULES:
        hits = [needle for needle in rule["needles"] if needle in lowered]
        if hits:
            score += rule["weight"] + min(12, len(hits) * 3)
            threats.append(rule["threat"])
            red_flags.append({"label": rule["label"], "matched": hits[:5], "weight": rule["weight"]})
            safe_actions.append(rule["safe"])

    for label, detail, weight in _domain_findings(urls):
        score += weight
        red_flags.append({"label": label, "matched": [detail], "weight": weight})
        if weight > 0:
            threats.append("Phishing link risk")

    address_findings, address_meta = _address_findings(original)
    for label, detail, weight in address_findings:
        score += weight
        red_flags.append({"label": label, "matched": [detail], "weight": weight})

    if re.search(r"\b(?:telegram|whatsapp|discord)\b.*\b(?:support|admin|agent)\b", lowered):
        score += 24
        threats.append("Social platform impersonation")
        red_flags.append({"label": "Social app support impersonation pattern", "matched": ["social app + support/admin language"], "weight": 24})

    score = max(0, min(100, score))
    risk_level = _level(score, len(red_flags), bool(urls))
    ai_note = _openai_assessment(original, risk_level)
    source_status = "Local rules + OpenAI AI review" if ai_note and not ai_note.get("error") else "Live threat intelligence unavailable; local scam rules were used."
    if ai_note and not ai_note.get("error"):
        explanation_ai = ai_note.get("explanation") or ""
        scam_type = ai_note.get("scam_type") or ""
        if scam_type:
            threats.append(str(scam_type))
        for action in ai_note.get("safe_actions") or []:
            safe_actions.append(str(action))
    elif ai_note and ai_note.get("error"):
        red_flags.append({"label": "AI review unavailable", "matched": [ai_note["error"]], "weight": 0})
        explanation_ai = ""
    else:
        explanation_ai = ""

    if not safe_actions:
        safe_actions = [
            "Verify URLs manually by typing the official website yourself.",
            "Do not connect a wallet or sign approvals unless you fully understand the permission.",
            "Use public block explorers for addresses and transactions only.",
        ]
    core_actions = [
        "Never share seed phrases, private keys, recovery phrases, wallet passwords, exchange passwords, or signing credentials.",
        "Never send crypto to unlock funds, verify a wallet, pay fake tax, or release a withdrawal.",
        "Use official websites only. Avoid links from DMs, ads, replies, or urgent messages.",
        "If you already approved a suspicious contract, consider revoking token approvals from a trusted official revoke tool.",
    ]
    safe_actions = list(dict.fromkeys(core_actions + safe_actions))[:10]

    if not red_flags:
        red_flags.append({"label": "No obvious high-risk pattern detected", "matched": ["Insufficient evidence to call it safe."], "weight": 0})

    threats = list(dict.fromkeys(threats)) or ["Unknown / insufficient evidence"]
    confidence = round(min(0.98, max(0.35, (score / 100) + (0.12 if urls else 0) + (0.08 if len(red_flags) > 2 else 0))), 2)
    explanation = explanation_ai or (
        "The scan combines crypto-specific scam rules, URL/domain inspection, wallet/address pattern checks, and social-engineering signals. "
        "Never treat a low-risk result as a guarantee of safety; verify independently before signing, sending funds, or sharing information."
    )
    red_flag_text = [
        flag["label"] + (f": {', '.join(flag['matched'])}" if flag.get("matched") else "")
        for flag in red_flags
    ]
    response = (
        "🛡 Scam Shield Scan\n\n"
        f"Risk Score: {score}/100\n"
        f"Risk Level: {risk_level}\n"
        f"Confidence: {confidence:.2f}\n"
        f"Source: {source_status}\n\n"
        "Threats Detected:\n" + "\n".join(f"• {item}" for item in threats) + "\n\n"
        "Red Flags:\n" + "\n".join(f"• {item}" for item in red_flag_text[:10]) + "\n\n"
        f"Why It Matters:\n{explanation}\n\n"
        "Safe Actions:\n" + "\n".join(f"• {item}" for item in safe_actions) + "\n\n"
        "CoinPilotXAI uses advanced AI and rule-based threat detection to identify many common and emerging scam patterns, but users should still verify independently.\n"
        f"{DISCLAIMER}"
    )
    return {
        "ok": True,
        "risk_score": score,
        "risk_level": risk_level,
        "threats_detected": threats,
        "red_flags": red_flag_text[:10],
        "safe_actions": safe_actions,
        "explanation": explanation,
        "confidence": confidence,
        "disclaimer": DISCLAIMER,
        "source_status": source_status,
        "urls": urls,
        **address_meta,
        "response": response,
    }
