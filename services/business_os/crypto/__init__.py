"""Business OS — Crypto intelligence vertical (Stage 5).

Informational-only. This package builds a canonical ``business_os_crypto_*``
surface for cost-basis / P&L accounting, a unified market/quote read layer over
the three existing market services, and durable restart-safe price alerts.

It NEVER takes custody of funds and NEVER executes a trade or transfer. Everything
here is gated behind the ``BUSINESS_OS_CRYPTO`` flag and is additive: the legacy
``portfolio_items`` / ``manual_portfolio`` / ``user_alerts`` / ``watchlist_items`` /
``watchlists`` tables are left completely untouched.
"""
