# PulseSoc Legal Surface Audit — what exists, what is published, what contradicts what

> **Not legal advice.** Written by an engineer. Nothing here is a legal conclusion.
> Items needing a lawyer are marked **LEGAL COUNSEL REVIEW REQUIRED**; items that
> are the owner's commercial call are marked **OWNER DECISION REQUIRED**.

Scope: every legal and consent surface reachable by a user, on the website, in the
iOS app, and in the backend that serves both. Audited against `origin/main`
(`7ef111a8d`) in a clean worktree, because the shared checkout is 466 commits
behind and inventorying it would describe a product that is not deployed.

Companion document: `docs/legal/MARKETPLACE_SELLER_AGREEMENT_DRAFT.md` already
covers the **seller commercial mechanics** in detail and is deliberately not
duplicated here. This document covers what that draft says it does not: the
consumer surfaces, privacy, the buyer side, and the iOS app.

---

## 1. The finding that frames everything else

PulseSoc's published Terms of Service and Privacy Policy describe a **crypto
intelligence product**, not a social-commerce platform.

`templates/terms.html` and `templates/privacy.html` are both dated "Last updated:
May 2026" and are organised around AI market analysis, wallet scanning, seed-phrase
safety, Sports Edge and a Telegram companion. The word "marketplace" appears once
in the Terms, inside a list of surfaces that can be moderated. There is no buyer
term, no seller term, no fee disclosure, no merchant-of-record statement, no tax
position, no shipping or delivery term, and no link to the four commerce policy
pages that *do* exist.

Meanwhile the marketplace card rail is **open in production**
(`MARKETPLACE_CARD_PAYMENTS_ENABLED=true`, verified 2026-09-29) and has already
taken a real card payment.

So the gap is not "some clauses are missing". The two documents a user is required
to accept do not describe the half of the product that moves money.

**LEGAL COUNSEL REVIEW REQUIRED** — the whole of §1. This is not a copy edit.

---

## 2. Inventory: what is actually published

### 2.1 Website — three generations, none aware of the others

| Surface | Source | Live | Generation |
|---|---|---|---|
| `/terms` | `templates/terms.html` | 200 | crypto era |
| `/privacy` | `templates/privacy.html` | 200 | crypto era |
| `/legal/payments` | hardcoded in `bot.py:2120` | 200 | crypto era |
| `/legal/refunds` | hardcoded in `bot.py:2125` | 200 | crypto era |
| `/legal/seller-terms` | hardcoded in `bot.py:2130` | 200 | crypto era |
| `/trust-center` | hardcoded in `bot.py` | 200 | crypto era |
| `/community-rules` | hardcoded in `bot.py` | 200 | crypto era |
| `/advertising-policy` | hardcoded in `bot.py:104980` | 200 | crypto era |
| `/creator-monetization-policy` | hardcoded in `bot.py:104992` | 200 | crypto era |
| `/privacy-center` | hardcoded in `bot.py:104910` | 200 | crypto era |
| `/returns` | `seo/commerce_policies.py` | 200 | commerce era |
| `/refund-policy` | `seo/commerce_policies.py` | 200 | commerce era |
| `/shipping` | `seo/commerce_policies.py` | 200 | commerce era |
| `/contact` | `seo/commerce_policies.py` | 200 | commerce era |

The commerce-era four are well built and carefully sourced; `seo/commerce_policies.py`
documents the provenance of every sentence and refuses to claim what the code does
not do. They are the model the rest should follow.

**Two refund policies are published simultaneously.** `/legal/refunds` (crypto era,
two paragraphs, names "CoinPlotXAI" as the product) and `/refund-policy` (commerce
era, four sourced sections). Nothing reconciles them and nothing links one to the
other. **OWNER DECISION REQUIRED** — which one governs.

**Two seller-terms surfaces.** `/legal/seller-terms` is published prose that no
version identifier points at; `CURRENT_TERMS_VERSION` is what a seller's acceptance
is recorded against. They are unrelated artefacts.

### 2.2 Website — reachability

The footer carrying all eleven legal links lives in `templates/_public_shell.html`,
which **6 of 30 templates extend**. The signed-in application, which is rendered
largely from page-local HTML inside `bot.py`, does not use it.

`templates/marketplace_cart.html` — the checkout page — contains **zero**
references to terms, privacy, returns, refunds or shipping. A buyer authorises a
card payment without any policy being disclosed or linked at the point of sale.

### 2.3 iOS app — five documents, all with dead canonical URLs

`mobile-native/src/screens/settings/legalContent.ts` ships five documents as
native in-app text: Terms of Service, Privacy Policy, Community Guidelines,
Cookie & Tracking Notice, and Open-source Licenses. The screen tells the user that
"the full canonical version — the one that is legally operative — is published at"
a URL.

All five of those URLs are 404 in production (probed 2026-09-29):

```
404  https://pulsesoc.com/legal/terms
404  https://pulsesoc.com/legal/privacy
404  https://pulsesoc.com/legal/guidelines
404  https://pulsesoc.com/legal/cookies
404  https://pulsesoc.com/legal/licenses
```

The app also carries **two documents the website does not publish at all** — a
Cookie & Tracking Notice and Open-source Licenses. So the in-app copy is
simultaneously the only version of two policies and, by its own statement, not the
operative one.

### 2.4 Divergence summary, web vs iOS

| Document | Website | iOS app |
|---|---|---|
| Terms of Service | crypto era, May 2026 | separate in-app text |
| Privacy Policy | crypto era, May 2026 | separate in-app text |
| Community Guidelines | `/community-rules`, 3 cards | full in-app text |
| Cookie notice | **absent** | in-app text only |
| Open-source licenses | **absent** | in-app text only |
| Seller agreement | `/legal/seller-terms`, 2 paragraphs | **never displayed** |
| Returns / refunds / shipping | 4 sourced pages | **absent** |

Two independently maintained sets of core policy text, with no shared source and
no test asserting they agree.

---

## 3. Verified defect register

Each item below was verified in source **and**, where it is a runtime claim,
against production. Severity is engineering severity, not legal.

### D-L1 — Five canonical legal URLs in the shipped iOS binary are 404 (HIGH)

Described in §2.3. The app is already released, so the binary cannot be corrected
retroactively; the fix belongs on the server, which must serve the URLs the shipped
app promises.

**Three of the five are fixed in this branch.** `/legal/terms`, `/legal/privacy` and
`/legal/guidelines` now answer `301` to `/terms`, `/privacy` and `/community-rules`
(`bot.py`, beside the existing `/legal/payments` family), each declared
`@public_route(reason=…)` so the default-deny route-auth gate is satisfied by a
declaration rather than by inference. A redirect, not a second rendering: the
operative text keeps one home, because a copy is a second document to keep current
and the one that drifts is still the one a user was shown.

`tests/web_surface/test_app_canonical_legal_urls.py` pins the contract. It reads the
`canonicalUrl` values out of the app's own `legalContent.ts` rather than restating
them, so a sixth in-app document pointing at an unserved URL fails the gate.

**`/legal/cookies` and `/legal/licenses` remain 404 — OWNER DECISION REQUIRED.**
There is nothing to redirect them to: the Cookie & Tracking Notice and the
open-source licence list exist only inside the app, and writing web versions would
mean drafting and publishing legal text, which §2 puts outside this mission. Two
options, and they are not equivalent:

1. Publish the app's existing text to the web at those URLs. Cheapest, and it makes
   the app's claim true for the build already in users' hands. It also creates two
   copies of each document to keep in step, which is the failure mode the three
   redirects above were shaped to avoid — so it wants a single source both sides
   render from, not a copy-paste.
2. Correct the app's canonical-URL claim in the next release. Honest, but it leaves
   live build 1.0.2 pointing at nothing for as long as it is installed, and the
   in-app text tells the member the web copy is "the one that is legally operative".

Both are recorded in the test as `PENDING_PUBLICATION`, which asserts they still
404: publishing one without removing its entry fails, so the known-gap list cannot
rot into a claim that is worse than reality.

### D-L2 — Terms acceptance is validated and then discarded (HIGH)

`bot.py:7787` reads `terms_accepted` from the signup form and `bot.py:7796` rejects
the signup without it. It is then never stored. `create_account()`
(`bot.py:7063`) takes `age_confirmed` as a parameter but has no parameter for
terms acceptance at all.

Consequences: there is no record that any user ever accepted the Terms; no record
of *which version* they accepted; and therefore no possible re-acceptance flow when
the Terms change — which §1 says they must.

`bot.py:7854` applies the same checkbox to **login**, also without persisting it, so
existing users are re-asked at every sign-in and the answer is discarded every time.

The seller side does this correctly, in the same repository:
`services/marketplace_commercial_operations.py` records
`(seller_id, terms_version, fee_policy_version, returns_policy_version,
payout_policy_version, acceptance_source, accepted_at)` with
`UNIQUE(seller_id, terms_version)` so that a version change forces re-acceptance.
The consumer path has none of it.

On iOS the same information is lost differently: `SignupScreen.tsx` uses **one**
checkbox for age and terms together ("I'm 16+ and agree to the…") and submits it as
`age_confirmed`. The terms half of the consent has no field.

**Fixed in this branch, by mirroring the seller design.**
`services/legal_acceptance.py` stores a row per `(user, document, version)` with
`UNIQUE` on the triple, created in `init_db()` — not on demand, because the
callers write from inside an open transaction and a second connection asking for
the write lock fails the signup it was recording. That was the first attempt and
it turned a missing audit row into total signup failure; the module docstring says
so, so the next person does not rediscover it.

`create_account()` now takes a keyword-only `accepted_terms_source` with **no
default**, carrying provenance rather than a boolean. There is no acceptance
without a place it came from, and no default because one of the three callers must
answer `None`: `/admin/users/new` creates an account for someone who was never
shown the documents. A default would let that path record a consent nobody gave,
or let a fourth signup path record none and be indistinguishable from the two that
do. A caller that omits it raises.

All three now answer: `web_signup`, `mobile_register` (from `age_confirmed`, which
on iOS *is* the whole consent — one checkbox reading "I'm 16+ and agree to the…"
over both links), and `None` for the admin path. **Login records too**, which is
what makes the tick it has always demanded mean something: every existing member
comes on file at the current version the next time they sign in, and re-accepting
a version already stored is a no-op rather than a row per visit.

Versions name what each document says about itself — both state "Last updated: May
2026" — and `tests/test_legal_acceptance.py` pins each constant against the
rendered page. Revising a document without bumping its constant fails there.
Without that pin the column would record which string was in the Python file, not
which text the member read.

`outstanding(user_id)` is why a version is stored instead of a boolean: when §1's
rewrite lands, `DOCUMENTS` changes and every member is correctly outstanding again.
A test asserts that by bumping a version rather than editing a document.

**Still open — OWNER DECISION REQUIRED.** The signup checkbox also binds the member
to the "no-tolerance rules", and `/community-rules` publishes no revision date, so
there is no version to record and nothing to detect a rewrite against. It is
declared in `UNVERSIONED_DOCUMENTS` rather than given an invented version, which
would look like coverage. Either publish a revision date there and add it to
`DOCUMENTS`, or stop naming it in what the member agrees to.

Also still open, and a client change: the iPhone app should send acceptance as its
own field so age and terms can be refused independently. Recording the combined
tick under a distinct source keeps the provenance legible in the meantime — a
reviewer can tell it from the web form's two separate boxes.

### D-L3 — The only seller-facing fee disclosure states a rate that has never been charged (HIGH)

`mobile-native/src/screens/SellerStoreScreen.tsx:709`:

> "Your applied fee is shown per order. Current Marketplace terms remain 10%; the
> proposed 5% policy is not active."

and `:737` repeats "10% current platform fee".

The true rate is **0%**. `bot.seller_fee_bps(cur, "merchant")` delegates to
`services/business_os/marketplace/policy.platform_fee_bps()`, which returns
`PROPOSED_PLATFORM_FEE_BPS` (500 = 5%) only when all three of
`MARKETPLACE_STANDARD_V1_OWNER_APPROVED`,
`MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY` and
`MARKETPLACE_STANDARD_V1_EFFECTIVE_AT` are set, and `0` otherwise. **None of the
three is set in production** (verified 2026-09-29). There is no code path that
yields 10%.

`bot.seller_fee_bps`'s own docstring says the 10% figure came from a
`platform_fee_rules` row that "was never disclosed to anyone", and
`CURRENT_TERMS_VERSION` was deliberately renamed off `LEGACY_TERMS_10_PERCENT_V1`
because "these terms no longer disclose 10%". `MARKETPLACE_SELLER_AGREEMENT_DRAFT.md`
§2 states "The commission is 5% or it is 0%. There is no third value."

So four authorities agree the rate is 0% or 5%, and the single screen a seller
reads says 10%. These two strings are also the only place any seller ever sees a
rate: no template and no i18n catalog discloses a commission.

**RESOLVED — technical fix only, no legal judgement.** Neither string names a rate
any more. The screen already fetched `/api/pulse/marketplace/commercial/terms` in
order to read `terms.acceptance`; the same response carries
`terms.current.platform_fee_bps` from `policy.platform_fee_bps()` — the identical
call `seller_fee_bps` makes to price a checkout — and the copy now renders that. So
the rate a seller reads is the rate their settlement will use, by construction, and
it follows the owner's three gates without another client release.

Deleting the literal was not the hard part. Two decisions are:

*A failed read names no rate at all.* `Number(null)` is `0`, so the obvious
coercion turns a dropped request into a quoted 0% commission — and the guard below
caught exactly that in the first version of this fix. The state is `number | null`
and only a real `number` counts; when it is null the headline says the commission
could not be loaded and the fee segment disappears from the terms summary
altogether. A seller is being asked to press **Review and Accept** directly beneath
that line, so a placeholder there would be a commission quote the platform never
made.

*The proposed 5% is no longer mentioned.* `commercial_operations.terms()` returns
`future_notice.published: False`. Announcing an unpublished future rate was the
other half of the old sentence and is not the client's call to make.

The guard is `mobile-native/src/screens/__tests__/SellerStoreScreenFeeDisclosure.test.tsx`.
It renders the same screen against three server answers — 0, 500 and 275 bps, the
last a value that appears nowhere in this codebase and so cannot be produced by a
client switching on rates it knows — and requires the rendered percentage to move
with the response. It pins no literal: a test asserting "0.00%" would need
rewriting on the day the owner activates the policy, which is the day it matters
most, and would pass against a second hardcoded string just as happily. Mutation-
proved in both directions — restoring either shipped line turns it red.

### D-L4 — The Privacy Center writes preferences it never reads back (HIGH)

`/privacy-center` (`bot.py:104910`) persists four choices into
`privacy_preferences` on POST. On GET it renders the form with **hardcoded**
checkbox states — `analytics_opt_out` always unchecked, the other three always
checked — and never queries the table it just wrote to.

A user who opts out of analytics, saves, and returns sees the box unchecked. If
they submit the form again for any reason, the stored opt-out is overwritten with
`0`. A privacy control that silently discards the user's choice is the most
sensitive possible place for this bug.

**RESOLVED — technical fix only, no legal judgement.**

The GET branch now reads the row back after the POST commits, so the form states
the stored decision instead of a constant. Three details of the read matter:

* An unsaved member is shown the state that is **enforced**, not a plausible
  default. `pulse_ads_service.user_personalized_ads_opt_out` treats a missing
  `privacy_preferences` row as opted out, so a page rendering that box unticked
  would tell a member they are being personalized while the ad server excludes
  them — the same class of misstatement as the original defect, pointing the
  other way.
* A NULL column keeps its declared default rather than being read as `0`. A
  half-written row is an absence of a decision, not a revocation of one.
* The row is read through `db_service.row_values()`, because `sqlite3.Row`
  iterates values while the Postgres compatibility row iterates column names.

The guard is `tests/test_privacy_center_controls.py`. It asserts agreement
between three things — what the form renders, what the table holds, and, for the
one control that is actually enforced, what the ad path itself concludes. A test
that only round-tripped the form would pass against a page storing a preference
nothing acts on. Mutation-proved: restoring the hardcoded form turns 4 of 7 red.

**OWNER DECISION REQUIRED — three of the four controls are enforced by nothing.**
`personalized_ads_opt_out` is read on the ad path. `analytics_opt_out`,
`public_profile` and `creator_visibility` are written by this page and read by no
code in the repository. The read-back fix makes that representation *more*
convincing, not less: the page now faithfully reports a stored value that has no
effect on anything. Either the enforcement is built or the controls are removed;
continuing to offer a privacy choice that does nothing is the decision that needs
making, and it is a product and legal one, not a technical defect.

### D-L5 — Checkout discloses no terms at the point of authorisation (HIGH)

Described in §2.2. The cart hands off to a Stripe-hosted Checkout Session
(`services/marketplace_cart_routes.py:1376`) created without `consent_collection`
or `custom_text`, so Stripe's page does not carry a terms acceptance either.
Neither surface presents or links a policy before the card is charged.

### D-L6 — `/shipping` publicly promises tax is shown at checkout; tax is never calculated (HIGH)

`/shipping` states, as its one explicit guarantee: "checkout shows you the seller
and store, the items and quantities, any discount, the delivery method, the
shipping cost, **tax**, the total and the payment method — before authorisation,
not after it."

`services/marketplace_quote_service.py:100` emits
`{"source": "not_calculated", "amount_minor": 0}`. No tax engine exists;
`policy.LEGAL_COMPLIANCE_REVIEW_REQUIRED` names `sales_tax`,
`marketplace_facilitator` and `tax_reporting` as unresolved.

This is a published commitment contradicted by the code, on the one page whose
module docstring insists every sentence is sourced. **LEGAL COUNSEL REVIEW
REQUIRED** for the tax position itself; the sentence is a defect either way.

### D-L7 — The iOS privacy manifest declares no data collection (MEDIUM)

`mobile-native/ios/PulseSoc/PrivacyInfo.xcprivacy` declares
`NSPrivacyCollectedDataTypes` as an **empty array** and `NSPrivacyTracking: false`,
while the app holds accounts, messages, purchases, media, device identifiers and
usage analytics. **OWNER DECISION REQUIRED** on the App Privacy disclosure; the
empty manifest is a repo-side defect regardless.

### D-L8 — Three different minimum ages across three surfaces (MEDIUM)

| Surface | Claim |
|---|---|
| iOS signup checkbox (`core.json:468`) | "I'm 16+" |
| iOS in-app Terms (`legalContent.ts:104`) | "at least 13 years old" |
| Web signup (`account.html:413`) | "I meet the age requirements for my country" |
| Web Privacy Policy | "not intended for children", no age |

The iOS signup asserts a stricter age than the document it links to.
**LEGAL COUNSEL REVIEW REQUIRED** — the correct floor is jurisdictional.

### D-L9 — Sellers accept a document that has no body (MEDIUM)

`marketplace_commercial_operations.terms()` returns a terms *version*, a fee rate,
and a list of six **section titles** — "Seller Terms", "Platform Fee Policy",
"Returns / Refunds", "Payout Policy", "Prohibited Goods", "Appeals / Enforcement".
There is no prose behind any of them. `accept_terms()` then records acceptance of
`MARKETPLACE_TERMS_STANDARD_V1`.

On iOS, `SellerStoreScreen.tsx` renders those titles as a caption and offers a
"Review and Accept" control that displays nothing and POSTs the acceptance. A
seller cannot read what they are agreeing to on any surface.

The prose exists — as `docs/legal/MARKETPLACE_SELLER_AGREEMENT_DRAFT.md` — and
that document explicitly forbids publishing itself pending legal review. So this is
correctly blocked, not merely missing: the defect is that acceptance is
*collectable* while the document is unpublishable.

### D-L10 — The platform's own readiness gate says commerce is not ready (INFORMATIONAL)

`marketplace_commercial_operations.readiness()` is a hardcoded literal reporting
`"seller_disclosure": "PASS"` alongside `"owner_approved": "NO"` and
`"activatable": "NO"`. The environment variable literally named
`MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY` is unset in production.

The system asserts that seller disclosure is not ready and the policy is not
activatable, while the card rail that collects buyer money is open. The
hardcoded `"PASS"` for `seller_disclosure` contradicts the unset variable and
should not be read as evidence of anything.

### D-L11 — Stale brand in legal prose (LOW)

The crypto-era hardcoded pages name **"CoinPlotXAI"** in product positions
("CoinPlotXAI is educational only", "CoinPlotXAI uses product signals"), and
`/creator-monetization-policy` states that "real payouts stay disabled until
compliance and policy readiness are complete" — which the live card rail
contradicts.

**Do not mass-replace the name.** `seo/commerce_policies.py` and
`tests/test_site_identity.py` pin a deliberate split: the brand is **PulseSoc**,
the legal entity is **CoinPlotXAI Inc.**, Apple records the App Store seller as
COINPLOTXAI INC., and a sentence stating who is liable must keep the company name.
Only the product-position uses are wrong.

---

## 4. What is genuinely good, and should be the template

- `seo/commerce_policies.py` — every claim sourced, every omission justified,
  and it refuses to promise a returns button that does not exist.
- `services/marketplace_commercial_operations.py` — versioned acceptance with
  `UNIQUE(seller_id, terms_version)`, so changing a fee disclosure forces
  re-acceptance.
- `docs/legal/MARKETPLACE_SELLER_AGREEMENT_DRAFT.md` — refuses to publish itself
  or to set the owner's attestation variables.

The pattern worth generalising: **the disclosure reads from the same authority
that enforces the behaviour.** D-L3 and D-L6 are both failures of exactly that
rule, and D-L2 is the absence of the versioned-acceptance pattern on the
consumer path.

---

## 5. Status

No legal text has been drafted, no policy published, and no attestation variable
set. `MARKETPLACE_STANDARD_V1_OWNER_APPROVED`, `…_SELLER_DISCLOSURE_READY` and
`…_EFFECTIVE_AT` are untouched, so the platform fee remains 0%.

Three repairs have landed. None of them writes, edits or publishes a sentence of
policy, which is the line §2 draws and the reason these three were in scope and the
rest are not:

* **D-L1** is routing. The three canonical legal URLs that had a published page to
  point at now point at it. It makes URLs the shipped app already advertises resolve
  to text that was always there.
* **D-L2** is persistence. Signup demanded agreement and then discarded the answer;
  it is now recorded against the version of each document the member actually read.
  The consent requirement is unchanged — what changed is that the platform can now
  show it was given, and can tell who has not yet seen a rewrite.
* **D-L3** is disclosure plumbing. The one seller-facing fee statement read 10%, a
  rate no code path yields and no seller was ever charged; it now renders whatever
  the fee authority discloses, and states nothing when that read fails.
* **D-L4** is read-back. The Privacy Center could not be submitted without erasing
  a choice already made, and showed the enforced ads preference inverted. The four
  controls it offers are unchanged; what changed is that the page now states the
  decision that is in force.

D-L5 and D-L6 are still open, plus the owner decisions each landed fix left behind
(listed in place above). Both change what a user is told before their card is
charged and need text a lawyer has seen, so neither is a §106 technical fix. The
largest non-legal gap D-L4 exposed is that three of its four controls are enforced
by nothing — recorded in place as an owner decision.

§11–14 — the personal data inventory, the data-flow map and the vendor/subprocessor
inventory — are in `PULSE_DATA_AND_VENDOR_INVENTORY.md`, with their own defect
register D-P1..D-P10. Two of those outrank everything still open here: the iOS
account-deletion flow names a date and then never deletes anything (D-P1), and the
data-export request promises an emailed archive that nothing assembles (D-P2). Both
are surfaces representing a state the backend does not hold, which is the same defect
shape as D-L2 and D-L4 — but the fix for D-P1 is a job that irreversibly destroys
accounts on a timer, so it needs the owner and it needs counsel to draw the deletion
scope first.
