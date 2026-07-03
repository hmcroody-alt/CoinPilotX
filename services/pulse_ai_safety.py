"""Safety policy helpers for Pulse AI.

The goal is to support defensive cybersecurity education while blocking requests
that ask Pulse AI to help with hacking, theft, malware, abuse, or evasion.
"""

from __future__ import annotations

import re
from typing import Any


CYBER_MODES = {
    "beginner_safety": "Beginner Safety",
    "account_protection": "Account Protection",
    "scam_shield": "Scam Shield",
    "small_business_security": "Small Business Security",
    "incident_response": "Incident Response",
    "learning_mode": "Learning Mode",
}

DISALLOWED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(hack|break into|take over|steal)\b.*\b(account|email|instagram|facebook|pulsesoc|wallet|phone)\b", "unauthorized_access"),
    (r"\b(bypass|disable|evade)\b.*\b(mfa|2fa|otp|captcha|detection|antivirus|edr|security)\b", "evasion_or_bypass"),
    (r"\b(phishing kit|credential harvester|stealer|keylogger|ransomware|malware|botnet|rat)\b", "malware_or_credential_theft"),
    (r"\b(write|build|create|code|make)\b.*\b(exploit|payload|malware|ransomware|phishing)\b", "weaponization"),
    (r"\b(sql injection|xss|rce|privilege escalation|zero day|0day)\b.*\b(exploit|attack|payload|bypass|shell)\b", "exploit_instructions"),
    (r"\b(dox|doxx|swat|stalk|track someone|spy on)\b", "privacy_abuse"),
    (r"\b(seed phrase|private key|recovery phrase)\b.*\b(steal|extract|recover from someone|find)\b", "crypto_theft"),
)

ALLOWED_DEFENSIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(phishing|scam|fake|fraud|romance scam|sim swap)\b", "scam_shield"),
    (r"\b(password|2fa|mfa|login|account|security settings|recovery)\b", "account_protection"),
    (r"\b(incident|compromised|hacked|breach|malware|ransomware)\b", "incident_response"),
    (r"\b(small business|wordpress|backup|patch|update|employees)\b", "small_business_security"),
    (r"\b(what is|explain|learn|teach me|how does)\b.*\b(cyber|security|phishing|malware|vulnerability)\b", "learning_mode"),
)

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sk-[A-Za-z0-9_\-]{20,}", "[redacted-api-key]"),
    (r"\b(?:[A-Za-z0-9+/]{40,}={0,2})\b", "[redacted-token]"),
    (r"\b(?:\d[ -]*?){13,19}\b", "[redacted-number]"),
)


def compact(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def redact_sensitive_text(value: Any, limit: int = 4000) -> str:
    text = compact(value, limit)
    for pattern, replacement in SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def classify_request(text: str) -> dict[str, Any]:
    normalized = compact(text, 4000).lower()
    if not normalized:
        return {"ok": True, "category": "empty", "mode": "", "disallowed": False, "reasons": []}

    reasons = [reason for pattern, reason in DISALLOWED_PATTERNS if re.search(pattern, normalized)]
    if reasons:
        return {
            "ok": False,
            "category": "cyber",
            "mode": "safe_refusal",
            "disallowed": True,
            "reasons": sorted(set(reasons)),
            "safe_alternative": "I can help with defensive security, account recovery, incident response, phishing prevention, and hardening steps.",
        }

    for pattern, mode_key in ALLOWED_DEFENSIVE_PATTERNS:
        if re.search(pattern, normalized):
            return {
                "ok": True,
                "category": "cyber",
                "mode": mode_key,
                "mode_label": CYBER_MODES.get(mode_key, "Learning Mode"),
                "disallowed": False,
                "reasons": [],
            }

    return {"ok": True, "category": "general", "mode": "", "disallowed": False, "reasons": []}


def refusal_message(classification: dict[str, Any] | None = None) -> str:
    classification = classification or {}
    alternative = classification.get("safe_alternative") or "I can help with defensive security and account protection instead."
    return (
        "I can't help with hacking, stealing accounts, malware, phishing kits, bypassing security, or unauthorized access. "
        f"{alternative}"
    )


def safety_prompt_addendum(mode: str = "") -> str:
    label = CYBER_MODES.get(mode, "")
    mode_line = f"Use cybersecurity mode: {label}." if label else "Use safe cybersecurity guidance when relevant."
    return (
        f"{mode_line}\n"
        "Allowed cyber help: defensive education, hardening, scam detection, account recovery, incident response, and safe checklists.\n"
        "Disallowed cyber help: exploit code, credential theft, phishing kits, malware creation, MFA bypass, detection evasion, unauthorized access, or instructions that enable harm.\n"
        "If the user asks for harmful cyber activity, refuse briefly and redirect to defensive guidance."
    )
