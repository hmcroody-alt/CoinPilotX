"""Smart Scam Shield scanner facade for website, PWA, and Telegram."""

from __future__ import annotations

import hashlib

from . import scam_shield


EMPTY_MESSAGE = "Paste a suspicious crypto link, wallet address, token contract, or message to scan."


def input_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", errors="ignore")).hexdigest()


def analyze(value: str, scan_type: str = "auto"):
    text = (value or "").strip()
    if not text:
        return {"ok": False, "message": EMPTY_MESSAGE}
    result = scam_shield.analyze_text(text)
    risk_level = result.get("risk_level") or "Low"
    if risk_level == "Unknown":
        risk_level = "Low"
    summary = result.get("summary") or (
        f"{risk_level} risk scan completed. Review the red flags and safe actions before trusting the message."
    )
    why_it_matters = result.get("why_it_matters") or result.get("explanation") or (
        "Crypto scams often rely on urgency, impersonation, wallet approvals, and credential theft. Verify independently before acting."
    )
    return {
        "ok": True,
        "scan_type": (scan_type or "auto")[:80],
        "risk_score": int(result.get("risk_score") or 0),
        "risk_level": risk_level,
        "summary": summary,
        "red_flags": result.get("red_flags") or [],
        "why_it_matters": why_it_matters,
        "safe_actions": result.get("safe_actions") or [],
        "confidence": float(result.get("confidence") or 0),
        "source": result.get("source_status") or "Local rules + AI review",
        "source_status": result.get("source_status") or "Local rules + AI review",
        "response": result.get("response") or summary,
        "input_hash": input_hash(text),
        "urls": result.get("urls") or [],
    }
