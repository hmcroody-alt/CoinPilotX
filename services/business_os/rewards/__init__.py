"""Rewards domain (Wave D): Pulse Credits + Cash Rewards.

Pulse Credits are an internal, non-cash ledger — never withdrawable, never
real money. Cash Rewards pay real money exclusively through the Wave B seller
payout rails (Stripe Connect). See ``engine.py``.
"""

from .engine import (  # noqa: F401
    CREDIT_TO_CENT,
    RewardError,
    ensure_schema,
    grant_reward,
    set_fraud_state,
    approve_cash_reward,
    disburse_cash_reward,
    sync_from_payout,
    get_reward,
    get_credit_balance,
    list_credit_ledger,
    list_rewards,
    redeem_credits_to_ad_promo,
)
