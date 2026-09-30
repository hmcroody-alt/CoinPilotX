# PulseSoc — Legal Review Packet

Mission PULSE LEGAL: §107 (required document set), §110 (the packet, A–Z),
§111 (gap report), §113 (release gate).

**This packet contains no legal text and proposes none.** §2 of the brief forbids
acting as counsel, fabricating legal conclusions, or adapting another platform's
terms. What follows is the engineering record a lawyer needs in order to draft:
what the product does, what it currently tells users, where those two disagree, and
which questions are theirs rather than ours.

Read with:

* `PULSE_LEGAL_SURFACE_AUDIT.md` — §4, what is published today; register D-L1..D-L11
* `PULSE_DATA_AND_VENDOR_INVENTORY.md` — §11–14, data held and who receives it; register D-P1..D-P10
* `PULSE_PRODUCT_AND_ROLE_MAP.md` — §5–6, what the product does and who its users are; register D-A1
* `MARKETPLACE_SELLER_AGREEMENT_DRAFT.md` — a draft that forbids its own publication pending review

Baseline: `origin/main` at `7ef111a8d36d0e00fdf17ba2804797843e4bcb8d`, probed
2026-09-29.

---

## §107 — The document set PulseSoc requires, and what it actually has

The set is derived from the capability groups in §5, not from a template. A document
appears below because the product does something that needs it.

| # | Document | Required because | Exists? | State |
|---|---|---|---|---|
| 1 | **Terms of Service** (consumer) | every capability | yes, twice | Web `/terms` is crypto-era May 2026; iOS ships separate in-app text; no shared source, no test asserting they agree |
| 2 | **Privacy Notice** (member) | §11 data inventory | yes, twice | same split; does not describe the AI vendors, Mux replays, or ad targeting |
| 3 | **Privacy Notice (staff/employee)** | `admin_users` + `employees` hold DOB, home address, emergency contact | **absent** | D-P9. The heaviest personal data in the schema has no notice at all |
| 4 | **Community Guidelines / content policy** | UGC at scale, feed + reels + live | yes | `/community-rules` is 3 cards on web; iOS ships full text. Web version is not a policy |
| 5 | **Moderation, enforcement and appeals policy** | `AccountHealthAppealsScreen` exists and works | **absent as a document** | the process is real; nothing describes it |
| 6 | **Marketplace buyer terms / terms of sale** | live checkout taking real card payments | partial | `/returns`, `/refund-policy`, `/shipping` are good; nothing is disclosed at authorisation (D-L5) |
| 7 | **Seller / merchant agreement** | sellers approved by staff, listings live, payouts promised | **draft, unpublishable** | D-L9: acceptance is *collectable* while the document is unpublishable |
| 8 | **Platform fee schedule** | fee mechanism exists and is gated | yes, as code | fee is 0% and held there by three unset attestations; **no document may state 5%** |
| 9 | **Advertiser terms** | self-serve ads with a funded wallet | `/advertising-policy`, crypto era | does not describe targeting, wallet, billing, or the age control that does not work (D-A1) |
| 10 | **Creator monetisation terms** | `/creator-monetization-policy` + creator surfaces | yes, crypto era | no payout has ever executed; document must not promise one |
| 11 | **Crypto / market-data disclaimer** | `/api/crypto`, portfolios, price alerts | yes, in `/terms` | **must be retained.** Analytics with alerting invites an advice reading |
| 12 | **AI / automated-processing disclosure** | UNDX routes content to up to 5 model vendors | **absent** | D-P10: `META_MUSE_MODEL` can enable training by env var |
| 13 | **Cookie & tracking notice** | web + analytics | **iOS only** | D-L1: `/legal/cookies` is 404; the app calls its own copy non-operative |
| 14 | **Open-source licence notice** | bundled dependencies | **iOS only** | D-L1: `/legal/licenses` is 404 |
| 15 | **Data subject rights procedure** (access, export, deletion) | product offers all three | offered, partly non-functional | D-P1 deletion never executes on iOS; D-P2 export produces nothing |
| 16 | **Retention schedule** | nothing expires | **absent** | D-P5 |
| 17 | **Subprocessor list** | 20+ vendors receive personal data | **absent as a published list** | inventoried in §14; never disclosed to users |
| 18 | **Minimum-age statement** | three surfaces, three different answers | contradictory | D-L8, and D-A1 shows no age is ever recorded |

**Twelve of eighteen are absent, draft-only, single-platform, or describing a
different product.** Four (items 6, 8, 11 and the commerce trio behind 6) are the
model the rest should follow, for the reason given in the surface audit: they read
from the same authority that enforces the behaviour.

---

## §110 — The packet

### A. Legal entity and brand

Brand is **PulseSoc**; the contracting entity is **CoinPlotXAI Inc.** The split is
deliberate and pinned by `tests/test_site_identity.py`. Legal prose must name the
entity, product copy the brand. Do not mass-replace either. D-L11 records the places
where crypto-era prose names the old product where it should name the brand.

### B. What the product is

A **social-commerce platform**: user-generated content, private messaging and calls,
live streaming, a marketplace taking real card payments, a self-serve advertising
platform, and a crypto-analytics subsystem inherited from the previous product. Any
document describing only one half describes a different company. §5 has the full map.

### C. Who the users are

Members (`users`) and staff (`admin_users`, plus `employees`). A member's role is not
a stored value — it is the existence of a row in a capability table, so "seller" and
"advertiser" are both **granted after staff review**. 25 staff roles and 40
permissions, enforced by `admin_has_permission` (bot.py:20004). §6 has the detail,
including the fact that the hardcoded fallback map means the database is not the sole
authority on staff permissions.

### D. What data is held

§11 of the data inventory. Summary of the categories a notice must name: identity and
contact, credentials, device and push registrations, IP addresses (three tables, one
mixed raw/hashed), message bodies **in plain text**, media and live recordings, buyer
shipping addresses **inside a JSON metadata blob**, payment references, subscription
and billing state, moderation history, AI conversation content, and — for staff only —
date of birth, home address and emergency contact.

### E. Who receives it

§14. Stripe, Agora, Mux, Brevo, Cloudflare R2, Firebase/FCM, APNs, Google Cloud
Translation, CoinGecko, Telegram, CJ, and up to five model vendors (OpenAI,
Anthropic, Google, DeepSeek, Groq) depending on routing. **No subprocessor list is
published.**

### F. What users are currently told

§2 of the surface audit. Three generations of legal surface coexist and none is aware
of the others. Two refund policies are published simultaneously and nothing
reconciles them.

### G. Where the telling and the doing disagree

The consolidated register in §111 below. This is the section a lawyer should read
first: every row is a representation the product makes that the code does not
perform, or vice versa.

### H. What has been repaired, and the boundary observed

Six defects fixed on this branch. None writes, edits or publishes a sentence of
policy:

* **D-L1** routing — three canonical URLs the shipped app advertises now resolve.
* **D-L2** persistence — terms acceptance is recorded against the version read.
* **D-L3** disclosure plumbing — the seller fee statement now renders the fee
  authority's own answer instead of a hardcoded 10% nobody was ever charged.
* **D-L4** read-back — the Privacy Center shows the decisions that are in force
  instead of erasing them on submit.
* **D-P3** deletion now blanks seven further personal-data columns.
* **D-P4** deletion now clears all four push registries, including the web-push
  credential blob in `pulse_notification_devices`.

Not done, deliberately: no legal text drafted, no policy published, **no attestation
variable set** (`MARKETPLACE_STANDARD_V1_OWNER_APPROVED`,
`…_SELLER_DISCLOSURE_READY`, `…_EFFECTIVE_AT` untouched, so the platform fee remains
0%), nothing deployed, no production writes.

### I. Questions reserved for counsel

1. The minimum age floor (D-L8) — jurisdictional, and everything in D-A1 waits on it.
2. The deletion scope (D-P1) — `users` also carries billing, subscription and
   moderation history. Whether a deleted account may retain those is a retention
   question, and the answer defines an irreversible destructive job.
3. The statutory deadline for a data export request (D-P2).
4. A retention schedule for every category in §11 (D-P5).
5. Whether buyer street addresses may sit in `seller_transactions.metadata_json`
   (D-P6).
6. Exposure from having represented an age-restriction control to advertisers while
   not operating it (D-A1).
7. Stripe Connect's required seller terms, and the September 2026 Stripe terms update.
8. Whether PulseSoc is merchant of record or the seller is.
9. Tax: `/shipping` promises tax is shown at checkout; it is never calculated (D-L6).

### J. Decisions reserved for the owner

Publication of `/legal/cookies` and `/legal/licenses`; which of the two refund
policies governs; the `/community-rules` revision date; splitting the single iOS
age-plus-terms consent field; the three Privacy Center controls enforced by nothing;
whether the marketplace card rail stays enabled; the App Privacy disclosure and the
empty `PrivacyInfo.xcprivacy`; the E2E-encryption claim in the App Store screenshots
(D-P7); pinning `META_MUSE_MODEL` (D-P10); withdrawing or implementing ads age
targeting (D-A1); the $0.50 refund on `seller_transactions.id=27`; and the three fee
attestations — **which must not be set by an engineer.**

### K–Z. Reserved

The remaining packet sections in the brief's A–Z schema are document drafts and
counsel's own analysis. They are intentionally empty: filling them would be the act
§2 forbids.

---

## §111 — Gap report

Severity is engineering severity: how wrong the product currently is, not a legal
risk rating, which is counsel's to assign.

| ID | Gap | Sev | Status | Owner |
|---|---|---|---|---|
| D-P1 | iOS deletion is scheduled and promised by date, never executed | **P0** | open | owner + counsel (destructive job) |
| D-P2 | Data export promises an emailed archive; nothing assembles it | **P0** | open | counsel (deadline) then engineering |
| D-L1 | Canonical legal URLs in the shipped binary were 404 | High | **fixed** (3 of 5) | `/legal/cookies`, `/legal/licenses` → owner |
| D-L2 | Terms acceptance validated then discarded | High | **fixed** | — |
| D-L3 | Seller fee disclosure stated a rate never charged | High | **fixed** | — |
| D-L4 | Privacy Center wrote preferences it never read back | High | **fixed** | 3 unenforced controls → owner |
| D-P3 | Deletion left `recovery_email` / `recovery_phone` live | High | **fixed** | — |
| D-P4 | Deletion cleared 1 of 4 push registries | High | **fixed** | — |
| D-L5 | Checkout discloses no terms at the point of authorisation | High | open | needs text counsel has seen |
| D-L6 | `/shipping` promises tax at checkout; tax never calculated | High | open | counsel (tax) |
| **D-A1** | **Ads age targeting: offered, stored, displayed; estimated against a column with no writer; ignored at delivery** | **High** | **open, not fixed by design** | owner (withdraw or implement) + counsel |
| D-P5 | No retention schedule or expiry sweep for anything | High | open | counsel |
| D-P6 | Buyer street addresses in `seller_transactions.metadata_json` | High | open | counsel |
| D-P7 | Plaintext DMs vs App Store E2E-encryption claim | High | open | owner |
| D-L7 | iOS privacy manifest declares no data collection | Med | open | owner |
| D-L8 | Three surfaces, three different minimum ages | Med | open | counsel — **blocks D-A1** |
| D-L9 | Sellers accept a document with no body | Med | correctly blocked | counsel must clear the draft |
| D-P8 | Raw IP addresses in three tables | Med | plan exists | engineering |
| D-P9 | Staff home address, DOB, next of kin; no staff notice | Med | open | counsel |
| D-P10 | `META_MUSE_MODEL` can enable training by env var alone | Med | open | owner |
| D-L10 | Platform's own readiness gate says commerce is not ready | Info | accurate | — |
| D-L11 | Stale brand in legal prose | Low | open | owner |

**22 gaps. 6 fixed. 2 P0 open. 12 blocked on a decision that is not an engineer's to
make.**

One structural observation, offered because it explains most of the table rather than
any single row: **there is no migration framework.** Schema arrives as
`CREATE TABLE IF NOT EXISTS` and `add_columns_if_missing` in whichever module wanted
it. That is the direct cause of D-P3 and D-P4 — a routine that enumerates columns and
tables by hand cannot be visited by a change that adds one — and it is why the two
fixes derive their expectations from the live schema rather than from a list. The same
mechanism will produce the next D-P3 in some other routine.

---

## §113 — Release gate

The brief gates a pre-commerce/payments release on this work. Commerce and payments
are **already live in production** and have taken real money, which changes the
question from "may we launch" to "what is currently being represented".

### Blocking, in the sense that shipping further commerce makes them worse

| # | Item | Why blocking | Cleared? |
|---|---|---|---|
| 1 | D-P1 — iOS deletion never executes | the app states a deletion date and no deletion occurs | ☐ |
| 2 | D-P2 — export produces nothing | a rights request is answered with silence | ☐ |
| 3 | D-L5 — no terms at authorisation | every card payment today | ☐ |
| 4 | D-L9 — seller agreement has no body | sellers are already accepting it | ☐ |
| 5 | D-A1 — ads age control not operated | advertisers are being sold it now | ☐ |
| 6 | D-L8 — minimum age | contradictory on the surface a minor would see | ☐ |
| 7 | Subprocessor list unpublished | 20+ recipients, none disclosed | ☐ |
| 8 | Staff privacy notice absent | the heaviest data in the schema | ☐ |

### Deployed, and verified live (2026-09-30 01:35 PT)

Merged to `main`, which is what deploys; a `railway up` CLI deploy would have been
silently reverted by the next `main` deploy. Railway deployment `6219bd95` SUCCESS.
Verified against production rather than against the commit:

| Claim | Probe | Result |
|---|---|---|
| D-L1 — the app's canonical URLs resolve | `GET https://pulsesoc.com/legal/{terms,privacy,guidelines}` | **200, 200, 200** (were 404) |
| D-L1 — the other two are still absent | `GET /legal/{cookies,licenses}` | **404, 404** — as documented; no page exists to point at, owner decision |
| D-L2 — the new code actually booted | `to_regclass('public.user_legal_acceptances')` on prod Postgres | **table exists**, 6 columns as written, 0 rows (no signup since deploy) |
| D-P3 — the fix operates on real columns | `information_schema.columns` on `users` | **all 7 present** in prod: `date_of_birth`, `expertise_tags_json`, `recovery_email`, `recovery_phone`, `roast_call_sign`, `roast_call_sign_slug`, `social_links_json` |
| D-P4 — four registries, not one | `information_schema.tables` | **all 4 present**: `notification_device_tokens`, `pulse_notification_devices`, `push_subscriptions`, `user_device_tokens` — confirming three really were being left behind |
| Fee still 0% | `railway run printenv` × 3 | **all three attestations empty** in production |
| No boot damage | 300 lines of prod logs | **0** matches for traceback / critical / failed-to-register |

The D-P4 row is the one worth reading twice: the fix is not theoretical. All four
registries exist in production, so before this deploy a deleted account left live rows
in three of them, including the complete web-push credential.

**Not deployed: D-L3.** It is a change to `mobile-native/src/screens/SellerStoreScreen.tsx`
and reaches users only in a new iOS build. Until that ships, the App Store binary still
shows sellers the 10% figure.

### Cleared by this branch

| # | Item | Evidence |
|---|---|---|
| 9 | Canonical legal URLs resolve (3 of 5) | D-L1 |
| 10 | Terms acceptance is provable and versioned | D-L2 |
| 11 | No surface states a fee that is not in force | D-L3 |
| 12 | Privacy Center states what is enforced | D-L4 |
| 13 | Deletion removes every contact channel | D-P3, test derives from live schema |
| 14 | Deletion removes every push registration | D-P4, mutation-proved 4-of-6 red at `HEAD~1` |
| 15 | Platform fee remains 0%; no attestation set | three env vars untouched |

### Explicitly not gates

A fee document, a published seller agreement, or a set attestation. Each requires
counsel's clearance first, and an engineer producing any of them would be the failure
§2 exists to prevent.

---

## §112 — Final report

### What the product is

1. PulseSoc is a social-commerce platform: UGC, private messaging and calls, live
   streaming, a marketplace, a self-serve ads platform, and a crypto-analytics
   subsystem. Any single-half description is wrong.
2. It runs as one Flask application: 1,818 routes in `bot.py`, 118 more from
   `services/` blueprints, and a 122-screen iOS app that is not a subset of the web.
3. Commerce and payments are **already live in production** and have taken real money.
   This audit is therefore not a pre-launch review; it is a review of what is being
   represented right now.
4. The contracting entity is CoinPlotXAI Inc.; the brand is PulseSoc. The split is
   pinned by a test and must not be mass-replaced.
5. The crypto subsystem is live and is the easiest thing to forget. Price analytics
   with alerting invites an investment-advice reading; its disclaimer must be retained,
   not dropped as legacy.

### What users are told today

6. Three generations of legal surface coexist — crypto-era hardcoded HTML, mid-era
   Jinja templates, and a modern sourced commerce-policy system — and none is aware of
   the others.
7. Two refund policies are published simultaneously and nothing reconciles them.
8. Two seller-terms surfaces exist and are unrelated artefacts: published prose no
   version points at, and a version identifier acceptance is recorded against.
9. Web and iOS maintain two independent sets of core policy text with no shared source
   and no test asserting they agree.
10. The footer carrying every legal link lives in a shell 6 of 30 templates extend. The
    signed-in application does not use it.
11. The checkout page contains zero references to terms, privacy, returns, refunds or
    shipping.
12. The iOS app ships two documents the website does not publish at all — a cookie
    notice and an open-source licence notice — while telling the user its own copies
    are not the operative ones.
13. The commerce-era pages (`/returns`, `/refund-policy`, `/shipping`, `/contact`) are
    the model the rest should follow: every sentence's provenance is documented and
    they refuse to claim what the code does not do.

### What was found

14. 22 gaps registered across three documents: D-L1..D-L11 (legal surface),
    D-P1..D-P10 (data and vendors), D-A1 (ads targeting).
15. Two are P0 and both are promises the product makes in its own words: iOS deletion
    names a date and deletes nothing (D-P1); a data export request promises an emailed
    archive nothing assembles (D-P2).
16. The single most serious *silent* finding was D-P4. Deletion cleared one of four
    push registries. The worst survivor, `pulse_notification_devices`, holds the entire
    web-push subscription in `subscription_json`, and its unsubscribe path only sets
    `active=0` — so to every reader a leftover row is a live, addressable device
    belonging to an account that no longer exists.
17. D-A1 is new and was found while enumerating features, not while reading policy. The
    ads product accepts, stores and displays an age range; estimates its reach against
    `users.date_of_birth`, a column with **no writer anywhere** for members; and ignores
    age entirely at delivery.
18. The two halves of D-A1 point in opposite directions, which is worse than either
    alone: the estimate tells the advertiser *nobody* while delivery reaches
    *everybody*.
19. The platform holds no member age data at all. Signup collects a boolean whose web
    label states no number, while iOS states two different numbers (D-L8). Age
    targeting is not merely unimplemented — it is currently unimplementable.
20. Staff carry the heaviest personal data in the schema — `admin_users` and
    `employees` hold date of birth, home address and emergency contact — and no notice
    addresses them (D-P9).
21. Message bodies are stored in plain text while the App Store screenshots claim
    end-to-end encryption (D-P7). The claim is in the pixels, not the text.
22. Buyer street addresses sit inside `seller_transactions.metadata_json` (D-P6), which
    makes them undiscoverable by schema inspection and unreachable by a column-based
    deletion routine.
23. No retention schedule exists for any category of personal data, and nothing expires
    (D-P5).
24. 20+ vendors receive personal data, including up to five model vendors depending on
    routing. No subprocessor list is published.
25. `META_MUSE_MODEL` can enable training on user content by environment variable alone
    (D-P10), which means a no-training decision is reversible without a code change.
26. Twelve of the eighteen required documents are absent, draft-only, single-platform,
    or describing the previous company.
27. Sellers can *accept* an agreement whose prose is unpublishable pending review
    (D-L9). The block is correct; the defect is that acceptance is collectable anyway.
28. The platform's own readiness gate already reports that commerce is not ready
    (D-L10). It is accurate.

### Root causes, not symptoms

29. **There is no migration framework.** Schema arrives as `CREATE TABLE IF NOT EXISTS`
    and `add_columns_if_missing` in whichever module wanted it. A routine that
    enumerates columns and tables by hand can never be visited by the change that adds
    one. This is the direct and sufficient cause of D-P3 and D-P4.
30. Consequently both fixes derive their expectations from the **live schema** rather
    than from a list. A test naming `recovery_email` would have passed the day before
    that column existed and gone on passing after — which is precisely how the defect
    got in.
31. The cause of D-L2, D-L3, D-L4 and D-L6 is one pattern violated four times: **the
    disclosure did not read from the authority that enforces the behaviour.** The
    commerce-era pages obey that rule; everything else states a constant.
32. The cause of D-A1 is the same rule applied to a control instead of a disclosure:
    the write path, the estimate and the delivery path were each built against their own
    idea of what age targeting means, and nothing tied them together.
33. Three generations of legal surface exist because each new product era added pages
    without retiring the previous era's. Nothing enumerates the set, so nothing noticed.

### What was repaired

34. Six defects fixed, none of which writes, edits or publishes a sentence of policy.
35. **D-L1** — three of five canonical legal URLs the shipped iOS binary calls "legally
    operative" now resolve to the text that was always there.
36. **D-L2** — terms acceptance is recorded against the version of each document the
    member actually read, using the versioned-acceptance pattern the seller path
    already had. The consent requirement is unchanged; what changed is that the platform
    can now prove it was given, and can tell who has not seen a rewrite.
37. **D-L3** — the one seller-facing fee statement read 10%, a rate no code path yields
    and no seller was ever charged. It now renders whatever the fee authority discloses,
    and states nothing when that read fails.
38. **D-L4** — the Privacy Center could not be submitted without erasing a choice
    already made, and rendered the one enforced control inverted. It now states the
    decision in force.
39. **D-P3** — deletion blanks seven further personal-data columns, including both
    recovery channels. A deletion that blanks `email` and `phone` while leaving a
    working recovery email has not removed the member's contact details.
40. **D-P4** — deletion now clears all four push registries.
41. Deliberately left alone by D-P3/D-P4: `referral_code`/`referred_by` (clearing
    rewrites another member's history), `pulse_id` (permanent internal identity), and
    all billing, subscription and moderation columns — a retention question for counsel,
    not something to settle by writing an assertion.

### Evidence

42. New tests: deletion (6), legal acceptance (10), Privacy Center (7), canonical legal
    URLs (11), seller fee disclosure jest (7). All passing.
43. D-P4 was **mutation-proved**: the test file run in a detached `HEAD~1` worktree
    produced 4 failed / 2 passed with the exact survivor lists recorded in the data
    inventory. A hardlink sandbox was tried first and discarded as invalid — the test's
    own `abspath(__file__)` resolved back to the real repository, which would have made
    the proof vacuous.
44. Protection gates re-run green: CI test manifest (9), route auth (12), environment
    contract (14), site identity (7), email uniqueness (24), realtime-audio change gate
    (no protected path touched), `ast.parse` on `bot.py`, and a full mobile typecheck.
45. Every load-bearing claim in these documents was verified against source directly
    rather than taken from a search summary. That discipline is why the fourth push
    registry was found: the hand-written inventory said three, a shape query against the
    live schema said four, and the inventory was corrected rather than the count quietly
    adjusted.

### What was deliberately not done

46. No legal text drafted, proposed or published. No policy page created.
47. **No attestation variable set.** `MARKETPLACE_STANDARD_V1_OWNER_APPROVED`,
    `…_SELLER_DISCLOSURE_READY` and `…_EFFECTIVE_AT` are untouched, so the platform fee
    remains 0%. Setting one would activate a 5% fee, which is an owner's act.
48. **D-A1 not fixed**, for three reasons in order of weight: the shipped iOS wizard
    always sends `min_age >= 13`, so a server-side refusal would break every campaign
    creation; the surface belongs to another mission already in flight; and the honest
    remedy is to withdraw a capability or begin collecting dates of birth, which is an
    owner decision that first requires a minimum age the platform has never stated.
49. Nothing deployed. No production writes. No other agent's files modified — `main` has
    not moved from the baseline, so the branch merges cleanly.

### Handoff

50. Branch `claude/pulse-legal-audit`, 10 commits, pushed; remote SHA verified identical
    to local. Documents: surface audit, data and vendor inventory, product and role map,
    and this packet.
51. The next action is **not** engineering. It is counsel answering the nine questions
    in §110.I and the owner answering the decisions in §110.J. Twelve of the 22 gaps
    cannot move until then, and the minimum-age question (D-L8) blocks the most
    downstream work of any single item.

---

## §114 — Principal-engineer final check

Asked of this work as if reviewing someone else's:

**Did anything here state a legal conclusion?** No. Every gap is stated as a
disagreement between two artefacts in the repository — a screen and a table, a
document and a code path — with both sides cited. Where a judgement was needed, it is
marked for counsel or the owner and left undone.

**Is any fix load-bearing on an assumption that could be false in production?** The
deletion change adds columns behind a pre-existing `if column in user_columns:` guard
and tables behind `table_columns()`, which returns `[]` on any error. A deployment whose
schema lacks any of them is unaffected. `roast_call_sign_slug` was checked for a UNIQUE
index before being blanked; it has none.

**Could any of the new tests pass vacuously?** Each has a guard against its own
vacuity: the deletion fixture asserts it seeded something, and a separate test asserts
the vocabulary still matches the columns that actually regressed plus a floor of four
registries. The mutation run confirms four of the six genuinely go red without the fix.

**Is there a defect found and then quietly dropped?** One, now recorded: an earlier
version of the data inventory said "three device registries". A mechanical query said
four. The document was corrected in place rather than the number adjusted, because the
miscount was itself evidence of how the defect arose.

**Is anything represented as more complete than it is?** Two statements are
deliberately narrowed. D-P3/D-P4 does **not** claim deletion empties the whole row —
billing, subscription and moderation history remain, pending counsel. And the fix does
**not** help the iOS deletion flow, which still never executes; that is D-P1 and it is
said in the same paragraph as the fix, so nobody reads the resolution as broader than
it is.

**What would a reviewer most reasonably object to?** That D-A1 was documented rather
than fixed. The objection is fair and the reasons are in item 48. The decisive one is
that the available server-side fix would break the shipped Ads wizard for every
advertiser — a regression traded for a documentation improvement, which is the wrong
trade under the priority order this work follows.
