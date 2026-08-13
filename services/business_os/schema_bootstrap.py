"""One-shot, idempotent schema bootstrap for every Business OS subsystem.

Production runs Postgres and has no migration framework: each Business OS
subsystem ships an idempotent ``ensure_schema()`` (CREATE TABLE IF NOT
EXISTS), but historically those were only invoked at scattered points of use
(route-pack init, webhook write paths, dev/test setup). A fresh production
database that enables the ``BUSINESS_OS_*`` flags therefore 500s with
``psycopg2.errors.UndefinedTable`` on first read (observed 2026-08-07:
``relation "ledger_balances" does not exist`` behind
``/api/business-os/advertising/wallet``).

This module closes that gap. ``ensure_all_once()`` runs every subsystem's
``ensure_schema()`` exactly once per process, the first time any Business OS
surface is touched, provided at least one ``BUSINESS_OS_*`` flag is enabled.
Design decisions:

- **All subsystems, not per-flag mapping.** Subsystems share tables across
  flag boundaries (the advertising wallet reads the ledger; orders read
  marketplace + confirmations). A per-flag dependency map would just be a new
  place to be wrong, and an extra empty table exposes nothing — the route
  flags still keep disabled surfaces dark (404).
- **Per-subsystem isolation.** One failing ensure must not block the rest;
  each is wrapped and logged (``BUSINESS_OS_SCHEMA_BOOTSTRAP_FAILED``).
- **Never fatal.** Callers invoke this best-effort; a bootstrap failure
  surfaces as the same per-endpoint error it would today, plus a log line
  that actually names the broken subsystem.
"""

from __future__ import annotations

import logging
import os
import threading

_FLAG_PREFIX = "BUSINESS_OS_"

# Non-flag env vars that share the prefix and must not count as "enabled".
_NON_FLAG_SUFFIXES = ("UNDX_DEFAULT_ORG_ID",)

_TRUTHY = ("1", "true", "on", "yes", "enabled", "canonical", "shadow")

# (subsystem label, dotted module, attribute) — attribute is a zero-arg
# callable. Order roughly follows dependency direction (ledger and core
# tables first), though every ensure is independently idempotent.
_ENSURES = (
    ("ledger", "services.business_os.ledger.ledger", "ensure_schema"),
    ("webhook_inbox", "services.business_os.payments.webhook_inbox", "ensure_schema"),
    ("business", "services.business_os.business.schema", "ensure_schema"),
    ("profile", "services.business_os.profile.schema", "ensure_schema"),
    ("advertising", "services.business_os.advertising.schema", "ensure_schema"),
    ("ad_guardrails", "services.business_os.advertising.guardrails", "ensure_schema"),
    ("commerce", "services.business_os.commerce_gateway", "ensure_schemas"),
    ("reputation", "services.business_os.marketplace.reputation", "ensure_schema"),
    ("confirmations", "services.business_os.confirmations", "ensure_schema"),
    ("messages", "services.business_os.messages.schema", "ensure_schema"),
    ("commerce_links", "services.business_os.messages.commerce_links", "ensure_schema"),
    ("entitlements", "services.business_os.entitlements.schema", "ensure_schema"),
    ("insights", "services.business_os.insights.schema", "ensure_schema"),
    ("attribution", "services.business_os.attribution.schema", "ensure_schema"),
    ("performance", "services.business_os.performance.schema", "ensure_schema"),
    ("recommendations", "services.business_os.recommendations.schema", "ensure_schema"),
    ("crypto", "services.business_os.crypto.schema", "ensure_schema"),
    ("events", "services.business_os.events.schema", "ensure_schema"),
    ("verification", "services.business_os.verification.schema", "ensure_schema"),
    ("localization", "services.business_os.localization.schema", "ensure_schema"),
    ("creator_commerce", "services.business_os.creator_commerce.schema", "ensure_schema"),
    ("merchant_automation", "services.business_os.merchant_automation.schema", "ensure_schema"),
    ("undx_actions", "services.business_os.undx_actions.schema", "ensure_schema"),
    # Measurement/decision layer sitting over the existing ads platforms. It
    # adds no advertiser/campaign/wallet surface of its own — see
    # services/business_os/ads_intelligence/schema.py. Structural only: the
    # tables are inert until an ads-intelligence surface is actually used.
    ("ads_intelligence", "services.business_os.ads_intelligence.schema",
     "ensure_schema"),
)

_lock = threading.Lock()
_done = False


def any_flag_enabled() -> bool:
    """True when at least one BUSINESS_OS_* feature flag is truthy."""
    for key, value in os.environ.items():
        if not key.startswith(_FLAG_PREFIX):
            continue
        if key[len(_FLAG_PREFIX):] in _NON_FLAG_SUFFIXES:
            continue
        if (value or "").strip().lower() in _TRUTHY:
            return True
    return False


def ensure_all(force: bool = False) -> dict:
    """Run every subsystem ensure_schema. Returns {label: "ok"|"error"}.

    Safe to call repeatedly; each ensure is CREATE IF NOT EXISTS. ``force``
    is for tests/scripts — normal callers go through ``ensure_all_once``.
    """
    results = {}
    for label, module_path, attr in _ENSURES:
        try:
            module = __import__(module_path, fromlist=[attr])
            getattr(module, attr)()
            results[label] = "ok"
        except Exception:
            results[label] = "error"
            logging.exception(
                "BUSINESS_OS_SCHEMA_BOOTSTRAP_FAILED subsystem=%s module=%s",
                label, module_path)
    ok = sum(1 for v in results.values() if v == "ok")
    logging.info(
        "BUSINESS_OS_SCHEMA_BOOTSTRAP_DONE ok=%s failed=%s total=%s",
        ok, len(results) - ok, len(results))
    return results


def ensure_all_once() -> bool:
    """Bootstrap once per process when any Business OS flag is on.

    Returns True when the bootstrap has run (now or previously), False when
    skipped because no flag is enabled. Never raises.
    """
    global _done
    if _done:
        return True
    try:
        if not any_flag_enabled():
            return False
        with _lock:
            if _done:
                return True
            ensure_all()
            _done = True
        return True
    except Exception:
        logging.exception("BUSINESS_OS_SCHEMA_BOOTSTRAP_FAILED (outer, non-fatal)")
        return False
