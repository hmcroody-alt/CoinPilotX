"""Ads intelligence — measurement and decision layer for PulseSoc advertising.

This package owns events, delivery decisions, interest, performance rollups,
pacing, frequency, and diagnostics. It owns no advertiser, campaign, creative,
audience, wallet, ledger, price, bill, review queue, or admin approval — each of
those already has exactly one home and keeps it.

See `docs/advertising_current_architecture.md` for the end-to-end map and the
per-component KEEP / EXTEND / NORMALIZE / MIGRATE classification.
"""
