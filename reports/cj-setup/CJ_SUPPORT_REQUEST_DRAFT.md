# CJ support request — sent, awaiting provider response

Verified in PulseSoc Sent mail on 2026-09-07 at approximately 18:53 UTC.
The mailbox displays September 7, 11:46 AM for both messages. The first message
contained only the existing signature image; the reply with subject prefixed
`Re:` contains the complete eight-question request below, preceded by an apology
for the missing body. That corrected body was opened and verified in Sent mail.
Sender: `support@pulsesoc.com`. No duplicate request was sent during the
verification continuation. Sent-folder evidence proves sending, not receipt
by CJ or affirmative provider permission.

To: developer@cjdropshipping.com

Official channel: https://developers.cjdropshipping.com/en/about.html

Subject: PulseSoc: permission for one merchant's hosted CJ sandbox integration

Hello CJ Developer Support,

CoinPlotXAI Inc. is preparing a PulseSoc integration with CJ. We are requesting
written permission to test ONE explicitly authorized merchant's own CJ account
through our isolated hosted sandbox backend. This is not a request for a
large-scale production rollout.

The design uses authenticated HTTPS ingestion and encrypted, merchant-scoped
credential storage with tenant-bound access controls. Credentials will not be
placed in mobile apps, AI prompts, public responses, logs or analytics. Testing
would enforce isSandbox=1, with real funding and production fulfillment disabled.
We will not purchase a CJ plan, points, inventory or fulfillment for this test.

Please confirm these eight points, or link the authoritative CJ agreement that
explicitly covers them:

1. May PulseSoc securely store a merchant-provided CJ API key server-side solely
   for that merchant's own CJ account?
2. May PulseSoc securely retain that account's openId solely for CJ webhook HMAC
   verification?
3. May a hosted SaaS backend process CJ API traffic for multiple independently
   authorized merchant accounts? This is a policy clarification for future use;
   our immediate test remains limited to one authorized sandbox merchant.
4. Does CJ's documented three-users-per-IP restriction apply to this hosted SaaS
   model, including shared hosting-provider outbound IPs?
5. Does CJ recommend or require a platform/partner authorization model for
   PulseSoc? If so, which official onboarding path should we use?
6. May one merchant's own API credentials be used from PulseSoc's hosted backend
   for this authorized sandbox test without violating CJ integration policy?
7. May CJ product, image and description data be displayed inside PulseSoc's
   merchant storefront tooling?
8. May CJ webhook callback traffic containing openId terminate at PulseSoc's
   secure backend for that merchant's integration?

Please distinguish permission for this one-merchant sandbox test from any
separate production-scale approval. We will keep hosted credential ingestion
disabled until we receive affirmative permission or an explicit authoritative
agreement. Successful infrastructure or API tests will not be treated as
provider authorization.

Thank you,
PulseSoc engineering, CoinPlotXAI Inc.

---

No credentials, openId values, webhook URL, merchant IDs or customer data are
included in the request. The corrected message also supplies the business reply
contact `support@pulsesoc.com` and quotes the signature-only original message.
Sending was explicitly authorized by the user's mission. No CJ reply was found
in the CJ-related Inbox or Spam search at approximately 18:57 UTC. Delivery to
CJ's mailbox and substantive review are not independently verified. Do not
treat this request or a delivery acknowledgment as provider approval.

See [CJ_LIVE_SANDBOX_ACCEPTANCE.md](CJ_LIVE_SANDBOX_ACCEPTANCE.md) for the current
gate status and the implementation-use caveat that must be resolved before
credential ingestion.
