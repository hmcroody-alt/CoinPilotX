# CJ support request — draft, not sent

Subject: Authorized merchant API custody, sandbox callbacks, and hosted egress

Hello CJ Developer Support,

CoinPlotXAI Inc. is integrating CJ into PulseSoc using merchant-owned CJ
accounts. Each merchant would explicitly authorize PulseSoc to receive an API
key over authenticated HTTPS. We store the key, access/refresh tokens and openId
encrypted with tenant-bound access controls. No credential goes to mobile apps,
AI tools, public APIs, logs or analytics. PulseSoc will not fund merchant orders.

Please confirm or provide the applicable official contract for:

1. Whether this merchant-authorized hosted custody is permitted, including
   retaining openId to verify callbacks. If a partner/delegated authorization
   program is required, please provide its onboarding, scope and consent model.
2. Whether one explicitly authorized merchant may conduct create-only sandbox
   testing from a hosted backend using isSandbox=1 and payType=3, before any
   multi-merchant production rollout.
3. How the three-users-per-IP rule is counted for hosted services and API keys
   belonging to the same CJ account, and the approved architecture for legitimate
   growth beyond three merchants without rotating IPs or evading limits.
4. Whether a stable dedicated outbound IP is required and how to register or
   approve shared-backend egress, if that process exists.
5. Which sandbox callback/test mechanism supports order, stock, product and
   logistics events; required ACK deadline; and safe topic setup without
   disturbing an account's existing production subscriptions.
6. Whether product subscribe is additive or replaces a shop's existing set;
   response/readback semantics; and the safest bounded update contract.
7. Which order readback fields authoritatively prove sandbox mode, selected API
   shop, exact variant quantities and a merchant-supplied order reference after a
   create response is lost. Is a documented absence proof available?
8. Whether a payment interface provides an immutable expected/max-amount guard.
   All real funding remains disabled unless that contract is proven.

Please distinguish permissions for this single-merchant sandbox acceptance from
any separate multi-merchant production approval. We will not infer contractual
permission from successful API calls.

Thank you,
PulseSoc engineering, CoinPlotXAI Inc.

---

No credentials, openId, webhook URL, merchant IDs or customer data are included.
No support message was sent. Sending requires explicit authorization and an
approved recipient/channel.
