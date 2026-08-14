"""Sentinel Mission 5 — canonical financial entity references (Stage 2).

Sentinel refers to financial objects by REFERENCE, never by copying their
records (NO second ledger). A reference is `TYPE:identifier`, e.g.
`ORDER:ord_123`, `SELLER:42`, `PAYOUT:po_9`.

Hard privacy rule: Sentinel never stores card data, bank account numbers,
credentials, tax documents, or provider secret tokens. `assert_payload_safe`
enforces this recursively on every payload Sentinel records.
"""

from __future__ import annotations

from typing import Any, Tuple

FINANCIAL_ENTITY_TYPES: Tuple[str, ...] = (
    "USER",
    "BUYER",
    "SELLER",
    "ADVERTISER",
    "ORDER",
    "PAYMENT",
    "REFUND",
    "PAYOUT",
    "PAYMENT_METHOD_REF",     # opaque provider ref only (e.g. pm_...)
    "PROVIDER_ACCOUNT_REF",   # opaque provider ref only (e.g. acct_...)
    "AD_WALLET",
    "WALLET_ENTRY",
    "CAMPAIGN",
    "FINANCIAL_LEDGER",
    "SETTLEMENT",
    "TRANSACTION",
)

# Substrings that must never appear as field names in any Sentinel financial
# payload. Superset of classification.py's forbidden set, extended with
# payment-instrument and tax identifiers.
FORBIDDEN_FIELD_TOKENS: Tuple[str, ...] = (
    "password", "secret", "token", "api_key", "private_key",
    "card_number", "cardnum", "pan", "cvv", "cvc", "expiry_full",
    "bank_account", "account_number", "routing_number", "iban", "swift",
    "bic", "sort_code", "tax_id", "ssn", "ein", "vat_number",
    "credential", "seed_phrase", "mnemonic",
)


class UnsafeFinancialPayload(ValueError):
    """Raised when a payload contains a forbidden financial field."""


def make_ref(entity_type: str, identifier: Any) -> str:
    """Build a canonical entity reference string."""
    etype = str(entity_type).strip().upper()
    if etype not in FINANCIAL_ENTITY_TYPES:
        raise ValueError(f"unknown financial entity type {entity_type!r}")
    ident = str(identifier).strip()
    if not ident:
        raise ValueError("entity identifier must be non-empty")
    if ":" in ident:
        raise ValueError("entity identifier must not contain ':'")
    return f"{etype}:{ident}"


def parse_ref(ref: str) -> Tuple[str, str]:
    """Parse `TYPE:identifier`; raises ValueError on malformed refs."""
    if not isinstance(ref, str) or ":" not in ref:
        raise ValueError(f"malformed entity ref {ref!r}")
    etype, _, ident = ref.partition(":")
    etype = etype.strip().upper()
    ident = ident.strip()
    if etype not in FINANCIAL_ENTITY_TYPES:
        raise ValueError(f"unknown financial entity type in ref {ref!r}")
    if not ident:
        raise ValueError(f"empty identifier in ref {ref!r}")
    return etype, ident


def is_valid_ref(ref: Any) -> bool:
    try:
        parse_ref(ref)
        return True
    except (ValueError, TypeError):
        return False


def _field_forbidden(name: str) -> bool:
    lowered = name.lower()
    return any(tok in lowered for tok in FORBIDDEN_FIELD_TOKENS)


def assert_payload_safe(payload: Any, _path: str = "payload") -> None:
    """Recursively refuse payloads containing forbidden financial fields.

    Checks dict KEYS (field names) at every nesting level, including inside
    lists/tuples. Raises UnsafeFinancialPayload naming the offending path.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_s = str(key)
            if _field_forbidden(key_s):
                raise UnsafeFinancialPayload(
                    f"forbidden financial field {key_s!r} at {_path}")
            assert_payload_safe(value, f"{_path}.{key_s}")
    elif isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            assert_payload_safe(item, f"{_path}[{i}]")
    # scalars are fine — we forbid field NAMES, not values; values are
    # additionally screened by classification.py's redaction layer.


def payload_is_safe(payload: Any) -> bool:
    try:
        assert_payload_safe(payload)
        return True
    except UnsafeFinancialPayload:
        return False
