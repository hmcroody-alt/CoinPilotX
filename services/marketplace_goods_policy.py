"""Server-side enforcement for the versioned Marketplace goods catalog."""
from __future__ import annotations
import re
from typing import Any, Mapping
from services.business_os.marketplace import policy

_ALIASES = {"weapons": "weapons", "weapon": "weapons", "firearms": "weapons",
            "explosives": "explosives", "alcohol": "alcohol", "tobacco": "tobacco_nicotine",
            "nicotine": "tobacco_nicotine", "prescription drugs": "prescription_drugs",
            "controlled substances": "controlled_substances", "medical devices": "medical_devices",
            "luxury goods": "luxury_goods", "high value collectibles": "high_value_collectibles"}
_PROHIBITED_SIGNALS = {
    "counterfeit_goods": (r"\bcounterfeit\b", r"\bfake designer\b", r"\breplica branded\b"),
    "stolen_goods": (r"\bstolen (?:goods?|property|phones?|cars?)\b",),
    "illegal_drugs": (r"\billegal drugs?\b",),
    "personal_data_credentials": (r"\bstolen credentials?\b", r"\bpassword database\b"),
    "malware": (r"\bmalware\b", r"\bransomware\b", r"\bcredential stealer\b"),
    "financial_fraud_tools": (r"\bstolen credit cards?\b", r"\bcarding kit\b"),
}

def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

def category_key(listing: Mapping[str, Any]) -> str:
    explicit = _normalize(listing.get("category_key"))
    if explicit:
        return explicit.replace(" ", "_")
    category = _normalize(listing.get("category"))
    return _ALIASES.get(category, category.replace(" ", "_"))

def evaluate(listing: Mapping[str, Any]) -> dict[str, Any]:
    key = category_key(listing)
    decision = policy.listing_category_decision(key)
    reason = key if decision != "ALLOWED" else ""
    if decision == "ALLOWED":
        text = " ".join(_normalize(listing.get(field)) for field in ("title", "description", "subcategory"))
        for signal_key, patterns in _PROHIBITED_SIGNALS.items():
            if any(re.search(pattern, text) for pattern in patterns):
                decision, reason = "PROHIBITED", signal_key
                break
    return {"decision": decision, "reason_code": reason, "category_key": key,
            "policy_version": policy.PROHIBITED_GOODS_VERSION,
            "compliance_ready": decision == "ALLOWED"}

def purchasable(listing: Mapping[str, Any]) -> bool:
    return evaluate(listing)["decision"] == "ALLOWED"
