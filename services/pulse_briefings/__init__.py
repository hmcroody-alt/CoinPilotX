"""Pulse Briefings — periodic network + crypto intelligence notifications.

Architecture (see docs/briefings/PULSE_BRIEFING_ARCHITECTURE.md):

    authoritative data -> fact pack -> significance -> UNDX summary
    -> suppression/dedupe -> push -> deeplink

Rules:
- The LLM (UNDX) receives a bounded fact payload and never invents numbers.
- Scheduling and delivery are server-side (alert_worker tick); an evaluation
  window is NOT a mandatory send.
- Principle: docs/never_block_the_user.md — briefings never block requests.
"""

from .engine import (  # noqa: F401
    BRIEFING_WINDOWS,
    FREQUENCIES,
    briefings_enabled,
    delivery_status,
    ensure_schema,
    evaluate_user_briefing,
    get_briefing,
    get_preferences,
    list_briefings,
    list_briefings_page,
    mark_briefings_seen,
    run_scheduled_cycle,
    unseen_briefings_count,
    update_preferences,
)
from .crypto_provider import get_market_overview  # noqa: F401
