# Marketplace Payout Policy

Seller earnings accrue separately from provider disbursement. Unconnected sellers may receive orders, but earnings remain `pending_onboarding`; reconciliation after Connect onboarding must be idempotent.

Shipped orders become payout-eligible after confirmed delivery plus the configured two-calendar-day protection window. Pickup requires buyer confirmation or a fraud-safe confirmation fallback. Open disputes, returns, chargebacks, fraud holds, seller holds, and compliance holds block payout. Every extended hold requires an explainable reason code.
