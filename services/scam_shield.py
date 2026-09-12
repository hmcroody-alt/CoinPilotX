import json
import re
from datetime import datetime
from urllib.parse import urlparse

import undx_router

from services import undx_call_domain
from services import undx_privacy

# `requests` and `os` are gone from this module, and their absence is the migration.
# This file used to hold an API key lookup, a model default and a `requests.post` to
# `api.openai.com`, which is why a security control was the one AI call in the product
# outside the privacy ceiling, the spend ledger and the circuit breaker. The absence is
# asserted structurally in `tests/test_scam_shield_routing.py` rather than trusted to
# this comment: a module that imports no transport cannot grow a second one back.


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
        "needles": ["connect wallet", "sync wallet", "synchronize wallet", "validate wallet", "verify wallet", "wallet connect", "approve transaction", "unlimited approval", "set approval for all", "sign message", "permit signature", "token approval", "revoke later"],
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
        "needles": ["support agent", "official support", "admin support", "metamask support", "coinbase support", "telegram admin", "discord admin", "whatsapp support", "dm support", "customer service wallet", "account specialist"],
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
    {
        "label": "Remote access or security bypass request",
        "threat": "Account takeover",
        "needles": ["anydesk", "teamviewer", "remote access", "screen share", "disable 2fa", "turn off security", "install this app"],
        "weight": 52,
        "safe": "Never grant remote access or disable security controls for someone claiming to fix a wallet or exchange account.",
    },
    {
        "label": "Fake KYC / exchange verification",
        "threat": "Credential theft",
        "needles": ["kyc verification", "verify exchange", "exchange locked", "account verification deposit", "validate account", "upgrade your account limit"],
        "weight": 36,
        "safe": "Open the exchange manually from its official app or typed domain. Do not follow KYC links from DMs.",
    },
    {
        "label": "Fake presale / pump group",
        "threat": "Market manipulation",
        "needles": ["private presale", "exclusive presale", "pump group", "signal group", "insider allocation", "only today"],
        "weight": 30,
        "safe": "Treat private presales and pump groups as high-risk unless independently verified through official channels and audited contracts.",
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


#: What this call sends: free text a user pasted because they suspect it is a scam. It
#: routinely contains the message, the wallet address and the amount, so CONFIDENTIAL is
#: the floor and no routing convenience justifies lowering it (§4).
SCAM_SHIELD_PRIVACY_CLASS = undx_privacy.SENSITIVITY_CONFIDENTIAL

#: Provenance, not payload. Every caller reaches here through the Scam Shield surface,
#: so the domain is a fact about where the request came from rather than a summary of
#: what today's text happens to say.
SCAM_SHIELD_CALL_DOMAIN = undx_call_domain.CALL_DOMAIN_SCAM_SHIELD

#: The shape the rest of this module parses. Declared rather than merely described in
#: the prompt, so `require_json` can hold a provider to it instead of asking politely:
#: three keys, two of them strings, one a list of strings.
SCAM_ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "scam_type": {"type": "string"},
        "explanation": {"type": "string"},
        "safe_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scam_type", "explanation", "safe_actions"],
}

_ASSESSMENT_SYSTEM_PROMPT = (
    "Classify crypto scam risk. Return compact JSON with scam_type, explanation, and "
    "safe_actions. Do not provide exploit instructions."
)


def _ai_assessment(text, local_level):
    """The model's opinion, which is advice to this module and never its verdict.

    Routed through `undx_router` rather than posted to a vendor, so this call is inside
    the privacy ceiling, the spend ledger, provider health and the circuit breaker like
    everything else. Two things about it are specific to a security control and are the
    reason this was the hardest of the migrations rather than the most mechanical.

    **The answer has to be an object, so the requirement travels with the request.**
    `require_json` is a capability the router filters providers on, not a preference it
    tries to honour. Sent to a provider with no JSON mode this would come back as prose,
    `json.loads` would raise, and the `except` below would fold it into
    `{"error": ...}` — which `analyze_text` reports as "AI review unavailable". A scam
    check quietly downgrading itself to local-rules-only, on every request, with a
    reassuring "unavailable" note, is a worse outcome than the outage it imitates.

    **Everything deterministic stays above this.** The score, the risk level, the domain
    findings and the address findings are computed before this is called and are not
    shown to the model. What comes back may add a scam type, add safe actions, and
    supply the human-readable explanation; it cannot lower a score, clear a red flag or
    downgrade a level. That ordering is the §16 control and it is asserted, because it
    is one refactor away from "merge the AI's fields into the result" — which reads
    tidier and hands a stranger's pasted text a vote on its own risk rating.

    Returns `None` when there was nothing to ask about, or an error dict. The error is
    deliberately specific about *which* failure happened: a provider outage and a reply
    that was not the agreed shape need different responses from whoever reads the log,
    and the old code reported both as whatever `str(exc)` said.

    **One behaviour deliberately changed, and it is not a tidy-up.** The old code did two
    contradictory things about length: it returned `None` for any input over 5000
    characters, *and* sliced the prompt to `text[:5000]`. The slice made the guard
    redundant and the guard made the slice unreachable, so one of them was dead either
    way. The guard is the one that went, because "no AI review above 5000 characters" is
    an evasion an attacker can use on purpose: pad a scam message past the limit and the
    model layer switches itself off, silently, leaving only the local keyword rules — and
    a long message is the interesting case, not the cheap one. The cost bound is the
    slice, which is what a bound should be. This costs spend that the old code did not,
    on inputs the old code refused to look at, which is why it is recorded here and in
    the census rather than left for someone to discover in a bill.
    """
    if not text:
        return None
    envelope = undx_router.route_structured_request(
        None,
        _ASSESSMENT_SYSTEM_PROMPT,
        f"Local risk level: {local_level}\nText:\n{text[:5000]}",
        timeout=12,
        temperature=0.1,
        privacy_class=SCAM_SHIELD_PRIVACY_CLASS,
        call_domain=SCAM_SHIELD_CALL_DOMAIN,
        require_json=True,
        json_schema=SCAM_ASSESSMENT_SCHEMA,
    )
    if not envelope.get("ok"):
        # The router's own reason, not a rewritten one. It already distinguishes a
        # privacy refusal from a spend limit from an outage from a chain with no
        # JSON-capable provider in it, and collapsing those back into one string here
        # would throw away the only part of the envelope worth reading on failure.
        return {"error": str(envelope.get("error") or "AI review unavailable")[:240]}
    try:
        parsed = json.loads(envelope.get("response") or "")
    except Exception:
        # Reached only if a provider that was *required* to return JSON did not. That is
        # a provider defect rather than this module guessing wrong, so it says so.
        return {"error": "AI review returned an unparseable answer"}
    if not isinstance(parsed, dict):
        return {"error": "AI review returned a non-object answer"}
    # Overwrite, never `setdefault`. Everything else in `parsed` came from a model that
    # was reading text a stranger pasted, so a reply carrying its own `"source"` is a
    # thing that can happen on purpose. Attribution is a fact about which provider ran,
    # held by the envelope, and the payload does not get a vote on it.
    parsed["source"] = str(envelope.get("source") or envelope.get("provider") or "")
    return parsed


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
    ai_note = _ai_assessment(original, risk_level)
    # Named from the envelope, not from a constant. This said "Local rules + OpenAI AI
    # review" while the call was hard-wired to OpenAI; under the router the answer can
    # come from any provider in the chain, and the string is written to `scam_scans` and
    # shown in the admin "Source" column, so a hardcoded vendor name would be a stored
    # false attribution rather than a cosmetic one. Falls back to the generic label
    # `scam_shield_engine` already uses when the router reports no source.
    source_status = (
        f"Local rules + {ai_note['source']} AI review"
        if ai_note and not ai_note.get("error") and ai_note.get("source")
        else "Local rules + AI review"
        if ai_note and not ai_note.get("error")
        else "Live threat intelligence unavailable; local scam rules were used."
    )
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
        "CoinPlotXAI uses advanced AI and rule-based threat detection to identify many common and emerging scam patterns, but users should still verify independently.\n"
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
