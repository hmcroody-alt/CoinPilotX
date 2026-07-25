"""Business OS — Marketplace vertical (Stage 3).

Canonical, additive ``business_os_mkt_*`` surface built alongside the untouched
legacy marketplace (inline ``bot.py`` listings/checkout + ``marketplace_*`` tables).
Everything is gated behind ``BUSINESS_OS_MARKETPLACE`` and rides the shared
canonical double-entry ledger for all money movement. Nothing here mutates any
legacy table.
"""
