"""Business OS — Section 10: Verification (the ONE canonical cross-domain trust domain).

Every other Business OS section owns a single concern (business, store, orders, messages,
insights, events…). Section 10 is the capstone that owns *no* business data of its own —
instead it runs a battery of **read-only integrity checks** across the canonical domains and
records a signed-off ``verification run`` attesting the state is internally consistent. It is
the in-product embodiment of the mission rule "never claim PASS without end-to-end evidence."

Reuse over duplication:

  * identity + who-may-verify a business is resolved against S1 canonical RBAC
    (``business.service._effective_role``) — never re-modeled here;
  * the checks read the ONE canonical ledger (:mod:`services.business_os.ledger.ledger`) and
    the canonical Events tables directly — Verification computes nothing new and moves no
    money; it only *observes* and attests;
  * the ``business_os_verification_*`` tables are genuinely new (there is no prior attestation
    store), additive, and never mutate a legacy table.

Gated behind ``BUSINESS_OS_VERIFICATION``. When off the surface is dark (404).
"""
