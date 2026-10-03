# PULSE PRIVACY — Visibility & Exposure Architecture

**Phase:** architecture / threat model only. No implementation in this phase (§44).
**Status:** proposal, awaiting owner approval. The §0 hotfixes are exempt and
have shipped; see the status box below.
**Date:** 2026-10-02. §0 status updated 2026-10-03.

---

## 0. ACTIVE PRODUCTION DEFECTS (§46) — read before the design

The owner's instruction was explicit: do not sit behind a design document while
sensitive signals leak. Three defects were found. Each is stated with how it was
verified, who can see it, and the smallest safe fix.

> **Status, 2026-10-03.** 0.1 and 0.2 are **fixed and merged** — PR #130,
> squash-merged to `main` as `078329545`, which Railway auto-deploys. 0.3 is
> **open**, deferred to Phase 2 below.
>
> Each finding is left in the tense it was written in. The design that follows
> is an argument *from* these two defects — §C's ACCESS ≠ EXPOSURE claim is
> 0.1, and §G's "a correct allowlist aimed at the wrong audience" is 0.2 — and
> rewriting them into the past tense would quietly turn the evidence into
> anecdote. The per-defect status lines below say which are closed.

### 0.1 — P1, ANONYMOUS: `/pulse/post/<id>` prints an internal risk assessment

Verified today, anonymous, Googlebot UA:

```
GET https://pulsesoc.com/pulse/post/2516   ->  HTTP 200, body contains "Risk score: 45"
```

Also confirmed on posts 2532 and 2512. No login, no cookie, no client.

The byline under a post renders
`Type: {post_type} · Status: {moderation_status} · Risk score: {risk_score}`.
`risk_score` is produced by `services/pulse_moderation_engine.py` — a scam-shield
output in the range 0–100. Posts scoring ≥85 become `needs_review`; everything
below is `approved`. The handler 404s non-approved posts, so what reaches the
open web is the 0–84 band: **2,023 approved posts in production, 22 of which
carry a nonzero score, max 45.** The author is told a number the platform
privately assigned to them, and so is everyone else.

`Status` is the less visible half only by accident: because the gate 404s
anything not approved, the field has settled into printing the constant
`"approved"`. That is a property of today's access gate, not of this string. It
starts narrating review state the moment the gate loosens.

These three posts are currently `noindex,follow`, so they are not in Google's
index — but the page is openly readable and scrapeable. "Not indexed" is not
"not exposed."

**This is the exact ACCESS ≠ EXPOSURE split of §9, demonstrated in production.**
The access gate is correct. The field projection is not.

**Smallest safe hotfix:** cherry-pick the single template edit from `44e37e4a9`
(`bot.py`, the `Type:` byline). One string. No schema, no route, no serializer
contract. Zero client impact — this is server-rendered HTML read by browsers and
crawlers, not by the native app.

**✅ FIXED — merged 2026-10-03, PR #130, commit `d8fc3054b` (squashed into
`078329545`).** Exactly that: the byline reads `Type: …` and stops. The hotfix
was branched off `origin/main` rather than cherry-picked onto the unpushed
series, so it carries none of `44e37e4a9`'s other changes. Covered by
`tests/test_privacy_p1_exposure.py::PostPageExposure`, which asserts the label
*and* the value, the latter against visible text rather than markup — `45`'s
complement `55` is a substring of the page's own `rgba(255,255,255)`, so a
naive `assertNotIn` on raw HTML fails on a clean page.

### 0.2 — P1, AUTHENTICATED: every logged-in account can read any other account's email address and legal name

Found while mapping the profile entity for §13. Not previously reported.

`GET /api/pulse/profile/<profile_key>` (bot.py:114948) requires login, then
returns `pulse_native_profile_payload(cur, target_user_id, viewer_user_id)` for
an **arbitrary** target. That builder starts from:

```python
account = dict(cur.execute("SELECT * FROM users WHERE user_id=? …").fetchone())
payload = pulse_mobile_user_payload(account)
payload.update({ … })
```

`pulse_mobile_user_payload` (bot.py:8191) *is* a positive allowlist — the §11
structure is right — but the allowlist it encodes is the **owner's** field set,
because the function was written for `/api/mobile/auth/session`, where subject
and viewer are the same person. It includes:

```python
"email":     user.get("email") or "",
"full_name": user.get("full_name") or "",
```

Nothing downstream removes them. The route `jsonify`s the payload whole.

The gate only blocks: deleted/deactivated/disabled/closed (410),
suspended/restricted/banned (403), and `profile_visibility == 'private'` for a
non-self viewer (403). A public account — the default, `TEXT DEFAULT 'public'` —
hands over its email to any authenticated requester. The same builder backs
`/api/pulse/identity/<pulse_id>`, which resolves a permanent public ID to the
same payload.

This is a **single allowlist serving two different audiences**. It is the
clearest possible argument for the design below: the field set is correct for
one audience and wrong for the other, and nothing in the type system, the tests,
or the route knows which one it is talking to.

**Not verified by live request** — doing so would require authenticating as a
real account, which I will not do. The source path is unambiguous and has no
branch, but the owner should treat "confirmed by reading three functions" as the
evidence standard here, not "confirmed by response body" as in 0.1.

> **Evidence upgraded, 2026-10-03.** Still not probed against production, but no
> longer inferred either. Running `tests/test_privacy_p1_exposure.py` against
> the *unfixed* tree — the mutation check for the fix below — produced the real
> response body from the real route, and the failure dump contains
> `'email': 'subject-7f31b2@privacy-fixture.invalid'` and
> `'full_name': 'Rosalind Quartermaine-Okonjo'` in the profile a *different*
> logged-in account received for a public target. That is "confirmed by
> response body" against a local instance of the production code path, which is
> the standard 0.1 was held to. The same dump also shows `can_message: True`
> for two accounts with no relationship — 0.3, visible in passing.

**Smallest safe hotfix:** in `pulse_native_profile_payload`, drop `email` and
`full_name` when `target_user_id != viewer_user_id`. Do **not** change
`pulse_mobile_user_payload` itself — `/api/mobile/auth/session` and
`/api/pulse/profile/me` legitimately carry them, and the shipped App Store build
reads `authState.user?.email` from the session route. Checked `mobile-native/src`:
no screen reads `email` or `full_name` off an *other-user* profile payload, so
the non-self strip has no client impact. Four lines, reversible.

**✅ FIXED — merged 2026-10-03, PR #130, commit `0b2c13e66` (squashed into
`078329545`).** Four lines, as scoped. `is_self` was hoisted out of the payload
dict so the flag the client reads and the rule that decides what the client
receives are the same expression; the strip then runs last, after the PulseDrop
overlay, so nothing downstream can reintroduce the fields.

`tests/test_privacy_p1_exposure.py::ProfilePayloadExposure` pins both
directions — a stranger gets neither field by value *or* by key name, and the
owner still gets both via `/api/pulse/profile/me` and via their own profile
key. One test asserts `pulse_mobile_user_payload` is **unchanged**, which is
the one that matters in a year: without it a later cleanup "simplifies" by
deleting the fields at the source, every other assertion still passes, and the
owner silently loses their own email from the account settings screen.

`services/profile_viewer_permissions.py:156`:

```python
preference = str(account.get("message_privacy") or account.get("dm_privacy") or "everyone")…
```

Neither column exists. `users` has 106 columns and neither is among them, and a
repo-wide grep finds `message_privacy` and `dm_privacy` at exactly this one line
— the read, with no writer anywhere. The setting the UI actually offers is
`message_requests ∈ {everyone, followers, none}` (bot.py:12621), stored in the
account-command-center settings store, a different table.

So `_can_message` returns `True` unconditionally. A user who set "Followers" or
"None" is told their preference was saved and it does nothing.

This is the same failure family as 0.1 and as the broadcast-gate near-miss found
last phase: **a `.get()` on an absent key is `None`, not an error**, and an
`or`-chain downstream supplies a plausible default. Green tests, no exception,
wrong behaviour. It is why §34's static gate must read schema and AST, not text.

**Smallest safe hotfix:** out of scope for a "smallest" patch — it needs the
settings store joined in, which is a real change. Recommend filing it and fixing
it inside Phase 2 below (the audience resolver), not hotfixing it. Flagged here
because the owner asked for current exposure, and a privacy control that silently
does nothing is an exposure.

### 0.4 — what is clean

Checked anonymously against production today, Googlebot UA, for
`risk_score` / `moderation_status` / `supplier_cost` / `platform_fee` /
`seller_net` / email patterns / `date_of_birth` / `stripe_customer_id`:

| Surface | Result |
| --- | --- |
| `/pulse/marketplace` | 200, clean (the `phone` hits are product-category words; `support@pulsesoc.com` is intentional) |
| `/pulse/marketplace/163` | 200, clean |
| `/sitemap.xml` → 5 child sitemaps | 200, clean |
| `/pulse/merchant/<username>` | 302 to `/login` — the merchant `risk_score` leak found in Phase 1 reaches authenticated users only |
| `/store/<slug>` | 404 — the canonical slug route is in unpushed `b1b1e3b26` |

### 0.5 — recommended order

1. Deploy 0.1 today, standalone. It is one string and it is anonymous. — **done**
2. Deploy 0.2 next, as its own commit with a test. It is larger blast radius
   (PII) but narrower audience (authenticated). — **done**
3. Fold 0.3 into Phase 2. — open
4. Then build the architecture below. — awaiting approval

### 0.6 — what actually shipped, 2026-10-03

PR **#130**, branched off `origin/main` (`32c65d21c`), squash-merged as
**`078329545`**. Two commits, each independently revertible:

| | |
| --- | --- |
| `d8fc3054b` | 0.1 — the `/pulse/post/<id>` byline |
| `0b2c13e66` | 0.2 — the non-self `email`/`full_name` strip |

The functional diff is **five lines**: one template string, plus a hoisted
`is_self` and a three-line `pop`. The other 38 changed lines in `bot.py` are
the comments explaining why, and a 341-line test file registered in
`config/ci_test_manifest.json`.

Branched off `main` rather than cherry-picked onto the unpushed series on
purpose. `44e37e4a9` also carries a marketplace/merchant change that belongs to
a different review, and landing a privacy hotfix should not smuggle it in.

CI: 17 checks, zero failures — 8 backend shards, the backend protection suite
(756 checks across 55 suites), architecture boundary, the audio gates, contract,
drift.

**Verified live on production, anonymous, Googlebot UA, after the Railway
auto-deploy:**

```
before:  GET /pulse/post/2516 -> 200, "Risk score: 45", "Status: approved"
after:   GET /pulse/post/2516 -> 200, byline reads "<p class='muted'>Type: text</p>"
```

Posts 2512 and 2532 likewise clean, and all three still render — the title and
body are present, so this is a removal rather than a 404 that happens to
contain no score.

0.2 has no equivalent anonymous probe by design: confirming it against
production would mean authenticating as a real account. Its evidence is the
mutation run described above, which is the same response-body standard applied
to a local instance of the same code path.

#### Still unpushed (§45)

Commit `7a56d7f70` ("Stop telling every viewer what we privately think of a
post") is on branch **`claude/seo-phase1-defects`**, parent **`b1b1e3b26`**. The
full unpushed series is `b548452a7` → `44e37e4a9` → `b1b1e3b26` → `7a56d7f70`.
Not an ancestor of `main`. Preserved, nothing pushed.

**One blocker found while deploying, recorded here so it is not rediscovered.**
`bot.py:44569` — the `/pulse` feed's client JS renders, for `scam_report` posts:

```js
`<span class="muted">Risk score ${Number(p.risk_score||0)}. Community-generated warning, educational only.</span>`
```

`7a56d7f70` removes `risk_score` from `_public_post`, so landing that commit
unchanged turns the Scam Shield card into a permanent "Risk score 0" — the
`||0` swallows the absent key rather than failing. This is the silent-absence
failure of §G in miniature, and it is *caused* by the fix. Nothing is wrong
today; the string must be removed in the same commit whenever that series
moves. It is a third `risk_score` surface and was not in §0 above.

---

## A. EXECUTIVE SUMMARY

PulseSoc has two visibility modules that are each individually good and that
together cover roughly a third of the problem. `discovery_visibility.py` answers
"may this account appear in a list," as a SQL fragment. `search_visibility.py`
answers "may this URL be indexed," as a path classifier plus a per-record content
gate. Both are allowlist-shaped, both are documented, both should survive.

What does not exist is the layer between them: **a rule about which fields of an
entity a given audience may receive.** Every serializer answers that question
privately, in its own idiom, by hand. There are three idioms in use —

- a positive allowlist written for one audience and then reused for another
  (`pulse_mobile_user_payload`, defect 0.2);
- a template that interpolates whatever column it was handed
  (`/pulse/post/<id>`, defect 0.1);
- a denylist by subtraction (`item.pop("email", None)`, bot.py:34897).

— and the first two are how both P1 defects happened.

The proposal is therefore **not** a new visibility module. It is:

1. **A field classification** attached to the schema, so a column's audience is
   declared once, next to the column, rather than re-decided in each serializer.
2. **An audience resolver** that turns (viewer, entity) into one of a small
   closed set of audiences, reusing `profile_viewer_permissions` as its
   relationship input.
3. **A projection function** that takes (entity type, row, audience) and returns
   only the fields that classification permits — default-deny, so a new column is
   invisible until someone classifies it.
4. **Keeping both existing modules**, with `search_visibility` gaining the
   external-search-consent dimension it already half-implements and
   `discovery_visibility` unchanged.

Six dimensions, not six booleans. They compose in a fixed precedence where
privacy always wins and SEO never overrides anything.

No creator profile is opened in this design. External search eligibility for a
human creator remains **default off** and remains unimplemented until a separate
approval (§21, §44).

---

## B. THE SIX POLICY DIMENSIONS

Each is a separate question with a separate answer. They are not reducible to
each other and must not be collapsed into one flag.

### B1. SOCIAL VISIBILITY — *may this viewer access this entity at all?*

- **PURPOSE** — the access gate. Yes/no on the whole object.
- **INPUTS** — `users.account_status`, `users.profile_visibility`,
  `blocked_users` (both directions), `pulse_follows`, `pulse_friendships` /
  `pulse_friends`, the entity's own `visibility` / `deleted_at` /
  `moderation_status`.
- **OUTPUT** — a permission set: the 14 booleans in
  `profile_viewer_permissions.DENY_ALL`, plus per-entity access decisions made
  inline by route handlers.
- **DEFAULT** — deny. `DENY_ALL` is literally the starting dict.
- **WHO CONTROLS IT** — the entity owner (`profile_visibility`, block list,
  per-post `visibility`), overridden by moderation (`account_status`).
- **WHERE IMPLEMENTED** — `services/profile_viewer_permissions.py` for profiles
  (one caller, bot.py:114862); ad-hoc inline checks everywhere else.
- **WHAT OVERRIDES IT** — moderation status and block state override owner
  preference. An owner cannot make a suspended account visible.
- **WHAT MUST NEVER OVERRIDE IT** — discovery, search, SEO, commerce,
  recommendation, or "the native client already fetched it."

### B2. PULSESOC DISCOVERY VISIBILITY — *may this entity appear in an on-platform list it was not asked for?*

- **PURPOSE** — keeps QA/test/suspended accounts out of creator search, suggested
  people, and content search, without scattering `LIKE 'qa%'` filters.
- **INPUTS** — `users.hidden_from_discovery`, `users.account_status`.
- **OUTPUT** — a SQL boolean fragment, appended to each discovery query.
- **DEFAULT** — visible. `COALESCE(hidden_from_discovery, 0) = 0` and
  `COALESCE(account_status, 'active')` not in the hidden set. NULL-tolerant by
  design, because a missing column must not hide every account.
- **WHO CONTROLS IT** — admin tooling and
  `scripts/qa_account_classification.py`. **Not** the user today.
- **WHERE IMPLEMENTED** — `services/discovery_visibility.py`, complete and clean.
- **WHAT OVERRIDES IT** — nothing. It is a floor.
- **WHAT MUST NEVER OVERRIDE IT** — being discoverable must never imply being
  accessible (B1) or indexable (B4). It is strictly narrower than both.

### B3. PUBLIC WEB ACCESS — *may an unauthenticated HTTP request fetch this?*

- **PURPOSE** — the anonymous-reachability question, separate from indexability.
- **INPUTS** — the route's auth decorator, and `pulse_social_shell` (bot.py:50820),
  which is the actual login wall for the social surfaces.
- **OUTPUT** — 200 vs 302-to-`/login` vs 404.
- **DEFAULT** — deny. `services/route_auth.py` is default-deny; a route must
  carry `@public_route(reason=…)` to be anonymous, and the protection suite fails
  any new route that does not declare.
- **WHO CONTROLS IT** — engineering, at the route. Not the user, not the entity.
- **WHERE IMPLEMENTED** — `services/route_auth.py` decorators + the shell. 18
  `@public_route` declarations repo-wide.
- **WHAT OVERRIDES IT** — B1. A publicly-routed handler still 404s an entity the
  anonymous audience may not access (this is why `/pulse/post/<id>` correctly
  hides `needs_review` posts).
- **WHAT MUST NEVER OVERRIDE IT** — being in a sitemap must never make a route
  public. The sitemap is downstream of this, never upstream.

### B4. EXTERNAL SEARCH ELIGIBILITY — *may a search engine index this?*

- **PURPOSE** — consent to be findable off-platform. Strictly narrower than B3.
- **INPUTS** — URL path (`search_visibility._RULES`, ordered, first-match-wins)
  **and** the record (`content_eligibility`: deleted → takedown → non-public
  visibility → non-approved moderation → draft/pending/scheduled/archived →
  creator opt-out → automated author → empty body → too-thin body).
- **OUTPUT** — a frozen `Decision(directive, indexable, sitemap_eligible, reason)`.
- **DEFAULT** — for paths, index (unlisted paths fall through to `index,follow`).
  For records, a chain of vetoes. **For human creators specifically: off, and not
  yet implemented** (§21).
- **WHO CONTROLS IT** — engineering for paths; the creator for records, via the
  `search_opt_out` / `noindex` / `hide_from_search` check that
  `content_eligibility` already honours.
- **WHERE IMPLEMENTED** — `services/search_visibility.py`.
- **WHAT OVERRIDES IT** — every one of B1, B2, B3, and the creator opt-out. The
  module already says so: *"A creator's opt-out is the one signal that outranks
  everything above it being correct. Making a profile public is not consent to be
  indexed."*
- **WHAT MUST NEVER OVERRIDE IT** — nothing may override it upward. Traffic,
  growth targets, and sitemap size are not inputs.

### B5. INDEX DIRECTIVES — *what do we tell the crawler, and where?*

- **PURPOSE** — the mechanism B4 speaks through: `robots` meta, `X-Robots-Tag`,
  `rel=canonical`, `robots.txt` Disallow, sitemap membership.
- **INPUTS** — B4's `Decision`, plus `_CANONICAL_ALIASES` and
  `_CRAWLABLE_DESPITE_NOINDEX`.
- **OUTPUT** — `index,follow,max-image-preview:large,…` / `noindex,follow` /
  `noindex,nofollow`, a canonical URL, a Disallow list, a sitemap entry.
- **DEFAULT** — whatever B4 returns; `index,follow` for an unclassified path.
- **WHO CONTROLS IT** — `search_visibility`, exclusively.
- **WHERE IMPLEMENTED** — `search_visibility.robots_meta(path)` /
  `canonical_url(path)` / `robots_disallow_prefixes()` / `sitemap_eligible()`,
  called **per template** at ~15 sites in `bot.py`. This is the gap: it is
  opt-in. A new public HTML route that forgets to call `robots_meta` emits no
  directive at all and is indexable by default.
- **WHAT OVERRIDES IT** — B4. Directives are rendering, not policy.
- **WHAT MUST NEVER OVERRIDE IT** — a directive must never be the *only* thing
  protecting a secret. `noindex` is a request, not an access control. The module
  already encodes the converse trap correctly: only `noindex,nofollow` paths may
  be `Disallow`ed, because a Disallow stops the crawl and the `noindex` is then
  never read.

### B6. COMMERCE PUBLICNESS — *is this a published commercial offer?*

- **PURPOSE** — a listing, variant, and storefront are public because a seller
  *published a commercial offer*, not because a person chose a social setting.
  Different consent, different owner, different lifecycle.
- **INPUTS** — `marketplace_listings.status` / `approval_status` /
  `published_at`, `business_os_store_storefront.status`, variant `status` and
  `stock_state`, seller `verification_status`.
- **OUTPUT** — sellable / browsable / indexable for the commerce entity.
- **DEFAULT** — deny; production is uniformly `published`, and Phase 1 found that
  the hand-rolled status predicates had drifted to match **zero** production rows.
- **WHO CONTROLS IT** — the seller (publish), overridden by moderation (approval).
- **WHERE IMPLEMENTED** — spread across the six marketplace surfaces, now
  statically guarded after the Phase 1 fix.
- **WHAT OVERRIDES IT** — B1 (a blocked viewer still cannot see the seller),
  moderation, and takedown.
- **WHAT MUST NEVER OVERRIDE IT** — the seller's **personal** profile settings.
  A seller who sets `profile_visibility='private'` has not unpublished their
  store, and a seller whose store is public has not consented to their personal
  profile being indexed. This is §22 and it is the single most important
  separation in the commerce half of the model.

### B7. The relationship model, as it actually is (§3)

Stated explicitly because the design must not invent semantics:

- `pulse_follows(follower_user_id, followed_user_id, …)` — a **one-directional,
  unapproved** edge. There is no pending/accepted column. **There is no
  "follower-only" content tier and no follow-request flow.**
- `profile_visibility ∈ {public, private}` — binary. The settings UI offers
  exactly these two options.
- `private` in practice means **friends-only**, not followers-only:
  `profile_viewer_permissions` opens content for a private profile only when
  `_friends()` is true, checked against both `pulse_friendships` and
  `pulse_friends`.
- Blocks are symmetric in effect: `_blocked_either_way` closes the profile if
  either party blocked the other.
- The only follower-scoped *setting* in the product is `message_requests`, and it
  is the one that is currently inert (defect 0.3).

So the audience ladder the product actually supports is:
**anonymous → authenticated → follower → friend → owner**, with follower
currently carrying no exclusive content, and with moderator/admin and
seller/buyer as orthogonal roles rather than rungs.

---

## C. ACCESS ≠ EXPOSURE (§9) — the central claim

Two questions, never one:

> **(A) May this viewer access this entity?**
> **(B) Which fields may this viewer receive?**

Today PulseSoc answers (A) in several reasonably principled places and answers
(B) nowhere. Every serializer re-decides it from scratch.

Production proves the split rather than arguing it. On `/pulse/post/2516`:

| | |
| --- | --- |
| (A) access | **correct** — the handler 404s any post that is not `approved`, so an anonymous viewer cannot reach a post under review |
| (B) exposure | **broken** — for a post they *may* reach, the same handler prints `Risk score: 45` |

A perfect access gate did not prevent the leak, because the leak was never about
access. Conversely, defect 0.2 is the same split one rung up the ladder: the
access check on `/api/pulse/profile/<key>` is deliberate and layered (410 / 403 /
403 / private), and it hands over an email address anyway.

Three consequences for the design:

1. **The two answers must come from different code.** Merging them produces a
   function that returns "yes" and a caller that then decides for itself what
   "yes" contains — which is exactly today.
2. **(B) must be total.** (A) can be answered per route, because a route knows
   its entity. (B) cannot, because the field set is a property of the *schema*,
   and the schema grows. A per-route answer to (B) is a snapshot that rots; the
   `users` table has 106 columns and nobody re-audits 106 columns when they add
   the 107th.
3. **(B) must be default-deny** for the same reason (§11). The failure mode to
   design against is not a careless `SELECT *` — those are findable. It is a
   *correct* allowlist written for one audience and later reused for another,
   which is defect 0.2 exactly, and which no amount of grepping for `SELECT *`
   will ever find.

---

## D. FIELD CLASSIFICATION (§12)

Twelve classifications. The test for whether a classification earns its place is
whether two fields in it ever need *different* answers for the same audience; if
not, it should be merged. Each below is distinguished by at least one audience
column in §E/§F.

| # | Classification | Meaning | Default audience |
| --- | --- | --- | --- |
| 1 | **PUBLIC CONTENT** | what the author published as the thing itself | anonymous |
| 2 | **PUBLIC IDENTITY** | how the author chose to be seen | anonymous |
| 3 | **RELATIONSHIP-SENSITIVE** | true facts whose *aggregate* is the privacy concern (follower lists, who-liked-what, presence) | authenticated, often follower |
| 4 | **OWNER PRIVATE** | the owner's own settings and drafts | owner |
| 5 | **PII** | identifies a natural person off-platform | owner (+ narrow, logged internal) |
| 6 | **FINANCIAL** | money movement, payout, tax, card | owner party to the transaction |
| 7 | **MODERATION INTERNAL** | what the platform decided about this content | moderator |
| 8 | **FRAUD / RISK INTERNAL** | what the platform *suspects* | fraud role only — **never the subject** |
| 9 | **SECURITY INTERNAL** | credentials, tokens, device identity, 2FA state | nobody over the wire |
| 10 | **ADMIN / OPERATIONS** | who reviewed, when, with what note | admin |
| 11 | **SUPPLIER INTERNAL** | wholesale cost, provider IDs, sync state | seller (cost), platform (provider) |
| 12 | **DERIVED INTERNAL SIGNAL** | scores the platform computed for ranking | nobody, unless deliberately published |

Three distinctions that are doing real work and are not taxonomy for its own
sake:

**7 vs 8 — MODERATION vs FRAUD/RISK.** `moderation_status` is a *decision* the
subject is entitled to know about their own content ("your post is under
review"). `risk_score` is a *suspicion*, and telling the subject their suspicion
score is a direct anti-abuse regression: it hands an attacker a free oracle for
tuning content until the number drops. Same table, same row, same audience for
everyone *except* the subject — which is why they cannot share a class.
Defect 0.1 shipped both to everyone.

**8 vs 12 — FRAUD/RISK vs DERIVED SIGNAL.** `engagement_score` is also derived
and also internal-ish, and it is also an oracle — but it is one the product
*chose* to publish, and clients render it. The distinction is consent, not
sensitivity. Keeping them apart is what lets `engagement_score` stay in the feed
allowlist while `risk_score` leaving it is a hard error.

**3 — RELATIONSHIP-SENSITIVE is not a weaker PUBLIC.** A follower count is
public. The follower *list* is not, and neither is "is this account online."
Both are composed of individually-public facts. The class exists because
aggregation changes the answer, and a per-field classification that ignored
aggregation would classify both as PUBLIC IDENTITY and be wrong.

**Logging is classified separately (§38).** A field's log audience is not its
client audience. `risk_score` is class 8 — never to the subject, never to the
wire — and must still be logged at full fidelity, because moderation review
depends on it. Conversely `email` is class 5 and must be *hashed or omitted* in
logs even though the owner may see it over the wire. The two axes are
independent and a single "sensitivity" number cannot express them.

---

## E. LOCATING THE NAMED FIELDS (§14)

Real schema names, verified against the live tables. "Reaches" = confirmed
present in a response body or serializer output today.

| Named field | Real column(s) | Class | Reaches whom today |
| --- | --- | --- | --- |
| email | `users.email` | PII | **any authenticated viewer** (defect 0.2) |
| phone | `users.phone`, `users.phone_number`, `marketplace_sellers.phone` | PII | owner; seller phone is seller-row only |
| legal name | `users.full_name` | PII | **any authenticated viewer** (defect 0.2) |
| display name | `users.display_name` | PUBLIC IDENTITY | anonymous |
| username | `users.username` | PUBLIC IDENTITY | anonymous |
| avatar | `users.avatar_url`, `avatar_thumbnail_url`, `avatar_filter` | PUBLIC IDENTITY | anonymous |
| bio | `users.bio` | PUBLIC IDENTITY | anonymous |
| date of birth | `users.date_of_birth`, `age_confirmed` | PII | nobody — **zero writers** (ads age targeting is sold and never enforced) |
| location | `users.country`, `marketplace_sellers.country` / `state_region` | PUBLIC IDENTITY (coarse) | anonymous |
| shipping address | `business_os_seller_profile_addresses`, order metadata | PII | buyer + seller of that order |
| billing | `users.stripe_customer_id`, `payment_provider`, `provider_customer_id`, `last_payment_status`, `payment_amount` | FINANCIAL | owner |
| risk_score (user) | `user_trust_profiles.risk_score` | FRAUD/RISK | internal |
| risk_score (post) | `pulse_posts.risk_score` | FRAUD/RISK | **anonymous web** (defect 0.1) |
| risk_score (seller) | `marketplace_sellers.risk_score` | FRAUD/RISK | authenticated, via `/pulse/merchant/<u>` behind the login wall |
| moderation_status | `pulse_posts`, `pulse_comments`, `pulse_reels`, `comm_v2_messages` | MODERATION | **anonymous web** on posts (defect 0.1); removed from the feed payload in `7a56d7f70` (unpushed) |
| sentiment | `pulse_posts.sentiment` | DERIVED INTERNAL | removed from the feed payload in `7a56d7f70` (unpushed) |
| scam / fraud scores | `pulse_reels.safety_score`, `marketplace_listings.safety_score` / `safety_flags_json`, `user_trust_profiles.scam_hunter_score` / `safety_score` / `trust_score` | FRAUD/RISK | **unaudited — see §G** |
| badges | `pulse_user_badges.badge_key`, `users.verified_badge`, `premium_status` | PUBLIC IDENTITY | anonymous |
| follower counts | `COUNT(*) FROM pulse_follows` | PUBLIC IDENTITY (count) / RELATIONSHIP-SENSITIVE (list) | count: anonymous |
| seller metrics | `business_os_mkt_seller_ratings`, `seller_transactions` aggregates | mixed: rating = PUBLIC CONTENT, volume = FINANCIAL | rating public |
| supplier IDs | `marketplace_product_sources.provider`, `provider_product_id`, `provider_variant_id`, `external_sku`, `supplier_connection_id`, `source_snapshot_id` | SUPPLIER INTERNAL | internal |
| supplier cost | `marketplace_product_sources.supplier_cost_cents` / `supplier_cost_currency`; `marketplace_listing_variants.cost_cents` | SUPPLIER INTERNAL | seller only |
| platform fee | `seller_transactions.platform_fee_cents` | FINANCIAL | parties to the transaction |
| seller proceeds | `seller_transactions.seller_net_cents` | FINANCIAL | seller only |
| Stripe IDs | `users.stripe_customer_id` / `stripe_subscription_id` / `stripe_session_id`; `seller_transactions.stripe_checkout_session_id` / `stripe_payment_intent_id`; `marketplace_orders.provider_payment_id` | SECURITY/FINANCIAL | **nobody over the wire** |
| device tokens | `notification_device_tokens.push_token` / `endpoint` / `p256dh` / `auth` / `token_hash` / `device_id` | SECURITY INTERNAL | **nobody over the wire** |
| notification prefs | `notification_preferences.*`, `pulse_notification_preferences` | OWNER PRIVATE | owner |
| hidden_from_discovery | `users.hidden_from_discovery` | ADMIN / OPERATIONS | internal — it is an admin classification, not a user setting, and exposing it tells a QA-flagged account it was flagged |
| profile_visibility | `users.profile_visibility` | OWNER PRIVATE (value) / PUBLIC (effect) | currently **emitted in the profile payload** to other viewers |
| `index_in_search` (proposed) | does not exist | OWNER PRIVATE | n/a — **not to be created in this phase** (§44) |

Two notes the table cannot carry:

- `users.date_of_birth` has **zero writers**. Age-gating and ads age targeting
  are sold against a column nothing fills. It is listed as PII because it will
  be PII the moment it is populated, and the classification has to exist before
  the writer does, not after.
- `profile_visibility` being echoed back in another user's profile payload is
  mild but real: it tells a stranger that an account is deliberately private
  rather than merely empty. It belongs in Phase 3's projection, not in a hotfix.

---

## F. FIELD-BY-FIELD EXPOSURE MATRIX (§13)

**This is the target state, not the current state.** Cells marked ⚠ are where
production disagrees with the target today.

Audience columns: **ANON** anonymous web · **AUTH** any logged-in account ·
**OWN** the subject/author · **SELL** the seller party · **MOD** moderator ·
**ADM** admin/internal service · **SE** search engine (always ≤ ANON, §27 — a
crawler receives the anonymous representation, never more and never less).

Legend: ✓ allowed · — denied · ƒ allowed to friends only · ∑ aggregate only
(count yes, list no).

### F1. USER / CREATOR PROFILE (`users`, 106 columns)

| Field | Class | ANON | AUTH | OWN | SELL | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `user_id` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | already the join key in every public URL |
| `username`, `display_name`, `pulse_id`, `public_player_id` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | chosen handle |
| `avatar_url`, `avatar_thumbnail_url`, `banner_url`, `cover_url`, `*_filter`, `cover_position` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | published presentation |
| `bio`, `social_links_json`, `expertise_tags_json` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | authored self-description |
| `verified_badge`, `premium_status`, `premium_mark_type` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | verification is a public claim |
| `country` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | coarse enough to be identity, not location |
| follower / following counts (derived) | PUBLIC IDENTITY ∑ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | count public, list is F1-next-row |
| follower *list* | RELATIONSHIP-SENSITIVE | — | ∑ | ✓ | — | ✓ | ✓ | — | aggregation changes the answer (§D) |
| `last_seen_at`, `last_login_at` | RELATIONSHIP-SENSITIVE | — | — | ✓ | — | ✓ | ✓ | — | presence is a stalking primitive |
| `email`, `full_name`, `phone`, `phone_number`, `recovery_email`, `recovery_phone` | PII | — | **—** ⚠ | ✓ | — | — | ✓ | — | **defect 0.2: AUTH currently ✓** |
| `date_of_birth`, `age_confirmed` | PII | — | — | ✓ | — | — | ✓ | — | zero writers today; classify before the writer exists |
| `email_verified`, `phone_verified`, `sms_verified_at` | SECURITY INTERNAL | — | — | ✓ | — | — | ✓ | — | owner needs it for the verify flow |
| `password_hash`, `two_factor_enabled`, `security_score`, `trust_level` | SECURITY INTERNAL | — | — | — | — | — | ✓ | — | `password_hash` never leaves the DB |
| `stripe_customer_id`, `stripe_subscription_id`, `stripe_session_id`, `provider_*`, `payment_*`, `latest_stripe_event` | FINANCIAL | — | — | — | — | — | ✓ | — | owner sees *state* via a billing endpoint, never the provider IDs |
| `subscription_*`, `trial_*`, `plan`, `is_pro`, `pro_*`, `usage_ai_count` | OWNER PRIVATE | — | — | ✓ | — | — | ✓ | — | entitlement is between owner and platform |
| `marketing_email_opt_in`, `notification_email_opt_in`, `security_email_opt_in`, `payment_receipt_opt_in`, `sms_opt_in`, `alerts_enabled`, `preferred_language` | OWNER PRIVATE | — | — | ✓ | — | — | ✓ | — | consent state is not content |
| `profile_visibility`, `message_requests` | OWNER PRIVATE | — | — ⚠ | ✓ | — | ✓ | ✓ | — | the *effect* is public; the *value* is not (currently echoed) |
| `account_status` | MODERATION | ∘ | ∘ | ✓ | — | ✓ | ✓ | ∘ | ∘ = only as an availability *outcome* (410/403), never the reason string |
| `restricted_reason`, `suspended_reason` | MODERATION | — | — | ✓ | — | ✓ | ✓ | — | subject is entitled to the reason for their own restriction |
| `hidden_from_discovery` | ADMIN/OPS | — | — | — | — | ✓ | ✓ | — | an admin classification; telling the subject tells a flagged QA account it was flagged |
| `is_super_user`, `access_enabled`, `login_enabled` | ADMIN/OPS | — | — | — | — | — | ✓ | — | privilege topology |
| `telegram_user_id`, `telegram_chat_id`, `telegram_username` | PII | — | — | ✓ | — | — | ✓ | — | a second-platform identifier is a correlation key |
| `referral_code`, `referred_by` | RELATIONSHIP-SENSITIVE | — | — | ✓ | — | — | ✓ | — | `referred_by` discloses an off-platform relationship |
| `risk_profile`, `preferred_exchange_goal`, `roast_call_sign*`, `auto_signals_*` | OWNER PRIVATE | — | — | ✓ | — | — | ✓ | — | crypto-subsystem residue; owner-scoped |
| `user_trust_profiles.*` (trust/creator/influence/safety/risk/invite/education/market_accuracy/scam_hunter, `frozen`) | FRAUD/RISK | — | — | **—** | — | ✓ | ✓ | — | **not even the owner** — a self-visible risk score is a tuning oracle |

### F2. POST / SIGNAL (`pulse_posts`, 32 columns)

Allowlist verified empirically last phase by probing both branches of
`pulse_feed_engine._public_post` with distinct per-column sentinels.

| Field | Class | ANON | AUTH | OWN | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `user_id`, `public_player_id`, `post_type` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | addressing |
| `title`, `body` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the post itself; `body` is the only stored copy of a caption |
| `visibility` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the author's own declared scope |
| `created_at`, `updated_at`, `edited_at` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | `dateModified` is a legitimate SEO signal |
| `ai_summary` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | retired in practice; kept allowlisted, must not be re-fed |
| `engagement_score` | DERIVED (published) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | deliberately published; clients render it |
| `live_session_id`, `live_status`, `live_viewer_count`, `playback_url`, `preview_url`, `replay_url` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | live branch only; playback is the content |
| `page_id` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | selects the author path |
| `view_count`, `share_count` | DERIVED | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | conventional public metrics |
| `media_ids_json`, `tags_json`, `ai_tags_json` | internal refs | — | — | — | — | ✓ | — | raw id arrays; media is resolved separately |
| `moderation_status` | MODERATION | **—** ⚠ | — | ✓ | ✓ | ✓ | — | **defect 0.1**; subject is entitled, nobody else |
| `risk_score` | FRAUD/RISK | **—** ⚠ | — | **—** | ✓ | ✓ | — | **defect 0.1**; never the subject |
| `sentiment` | DERIVED INTERNAL | — | — | — | ✓ | ✓ | — | a judgement about the author's tone |
| `status` | MODERATION | — | — | ✓ | ✓ | ✓ | — | the envelope's copy is the author-facing one |
| `pinned_at`, `pinned_by` | ADMIN/OPS | ∘ | ∘ | ✓ | ✓ | ✓ | ∘ | ∘ = the *fact* of being pinned, never *by whom* |
| `deleted_at` | MODERATION | — | — | ✓ | ✓ | ✓ | — | absence is the public signal |
| `repost_of_post_id` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | attribution; not currently serialized |

### F3. COMMENT / REPLY (`pulse_comments`, 11 columns)

| Field | Class | ANON | AUTH | OWN | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `post_id`, `user_id`, `parent_comment_id` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | thread structure |
| `body` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the comment |
| `created_at`, `updated_at`, `edited_at` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `media_ids_json` | internal refs | — | — | — | — | ✓ | — | resolved separately |
| `moderation_status` | MODERATION | — | — | ✓ | ✓ | ✓ | — | `moderate_comment` promotes needs_review→approved below 85, so this field carries the threshold's shape |
| `deleted_at` | MODERATION | — | — | ✓ | ✓ | ✓ | — | — |

### F4. REEL (`pulse_reels`, 39 columns)

| Field | Class | ANON | AUTH | OWN | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `post_id`, `user_id`, `category`, `caption` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the reel |
| `video_url`, `poster_url`, `mux_playback_id`, `duration_seconds` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | playback; `mux_playback_id` is the public half of the Mux pair |
| `sound_title`, `audio_track_id`, `sound_start_seconds`, `sound_end_seconds` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | attribution for the sound page |
| `share_count`, `replay_count` | DERIVED (published) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `comments_disabled`, `reactions_disabled` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the client must render the state |
| `pinned_at` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `watch_duration_ms`, `completion_rate`, `reel_score`, `educational_value` | DERIVED INTERNAL | — | — | ∘ | ✓ | ✓ | — | ∘ = creator analytics, via a creator-scoped endpoint, never the public payload |
| `safety_score` | FRAUD/RISK | — | — | — | ✓ | ✓ | — | same class as `pulse_posts.risk_score` |
| `moderation_status`, `status` | MODERATION | — | — | ✓ | ✓ | ✓ | — | — |
| `processing_status`, `transcoding_status`, `mux_asset_created_at`, `mux_ready_at`, `webhook_received_at`, `db_ready_update_at` | ADMIN/OPS | — | — | ∘ | — | ✓ | — | ∘ = owner sees "processing/ready", not the six timestamps |
| `mux_asset_id` | SECURITY INTERNAL | — | — | — | — | ✓ | — | the asset id is a control-plane handle; only `mux_playback_id` is public |
| `editor_state_json`, `audio_baked_in`, `thumbnail_frame_seconds`, `source_live_id` | OWNER PRIVATE | — | — | ✓ | — | ✓ | — | authoring state |

### F5. MERCHANT / SELLER (`marketplace_sellers`, 19 columns + `store_slug` pending)

| Field | Class | ANON | AUTH | OWN/SELL | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `user_id`, `display_name`, `business_name`, `logo_url`, `bio`, `website` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | a published commercial identity (B6, not B1) |
| `country`, `state_region` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | buyers are entitled to jurisdiction |
| `seller_type`, `created_at` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | "selling since" is a trust signal |
| `verification_status` | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the *positive* state only; "pending"/"rejected" render as unverified |
| `store_slug` (pending, `b1b1e3b26`) | PUBLIC IDENTITY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | a canonical address that survives a rename |
| `status` | B6 publicness | ∘ | ∘ | ✓ | ✓ | ✓ | ∘ | ∘ = the storefront 404s or renders; the string is not published |
| `phone` | PII | — | — | ✓ | — | ✓ | — | a seller's contact phone is still a person's phone |
| `risk_score` | FRAUD/RISK | — | **—** ⚠ | **—** | ✓ | ✓ | — | **currently reaches authenticated viewers** via `/pulse/merchant/<username>`; seller user_id 4 carries 45 |
| `reviewed_by`, `reviewed_at`, `review_notes` | ADMIN/OPS | — | — | — | ✓ | ✓ | — | review notes are free text about a person |
| `seller_intent_json` | OWNER PRIVATE | — | — | ✓ | ✓ | ✓ | — | onboarding answers |

### F6. STOREFRONT (`business_os_store_storefront`, 11 columns)

| Field | Class | ANON | AUTH | SELL | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `storefront_id`, `slug`, `name`, `headline`, `about` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the storefront as published |
| `theme_json`, `currency` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | presentation + the price unit |
| `created_at`, `updated_at` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `business_id` | internal ref | — | — | ✓ | — | ✓ | — | the tenant key; `slug` is the public address |
| `status` | B6 publicness | ∘ | ∘ | ✓ | ✓ | ✓ | ∘ | gate, not field |

**`/store/<slug>` is currently `noindex,nofollow`** in `search_visibility._RULES`
with the reason *"canonical storefront still behind require_account."* That line
is deliberately the thing someone must edit to publish storefronts — a decision
typed out rather than caused by a handler edit. This matrix describes what the
storefront *may* expose once that decision is made; it does not make it.

### F7. MARKETPLACE PRODUCT / LISTING (`marketplace_listings`, 42 columns)

| Field | Class | ANON | AUTH | SELL | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `seller_user_id`, `title`, `description`, `short_description` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the offer |
| `category`, `subcategory`, `tags_json`, `product_type`, `listing_type` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | taxonomy drives the Merchant Center feed |
| `price_label`, `currency` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | price is the offer |
| `cover_image_url`, `gallery_json`, `media_url`, `video_url` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `quantity`, `delivery_type`, `estimated_delivery`, `refund_policy`, `digital_version` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | terms of the offer; required by structured data |
| `lesson_count`, `duration`, `difficulty`, `prerequisites` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | course attributes |
| `featured` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the badge is rendered |
| `created_at`, `updated_at`, `published_at` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | `published_at` is the SEO date |
| `status`, `approval_status` | B6 publicness | ∘ | ∘ | ✓ | ✓ | ✓ | ∘ | gate, not field — Phase 1 found these predicates had drifted to match **zero** production rows |
| `safety_score`, `safety_flags_json` | FRAUD/RISK | — | — | — | ✓ | ✓ | — | same class as post `risk_score` |
| `moderation_reason`, `moderation_category`, `review_version` | MODERATION | — | — | ✓ | ✓ | ✓ | — | seller is entitled to why their listing was rejected |
| `reviewed_by`, `reviewed_at`, `submitted_at`, `approved_at` | ADMIN/OPS | — | — | ∘ | ✓ | ✓ | — | ∘ = seller sees the timestamps, never the reviewer |
| `seller_notes` | OWNER PRIVATE | — | — | ✓ | ✓ | ✓ | — | the name says internal; verify no template renders it |
| `listing_metadata_json` | **unclassifiable blob** | — | — | — | — | ✓ | — | see §G |
| `marketplace_product_sources.supplier_cost_cents`, `supplier_cost_currency` | SUPPLIER INTERNAL | — | — | ✓ | — | ✓ | — | publishing wholesale cost next to retail price destroys the seller's margin |
| `…provider`, `provider_product_id`, `provider_variant_id`, `external_sku`, `supplier_connection_id`, `source_snapshot_id`, `inventory_source`, `inventory_reference` | SUPPLIER INTERNAL | — | — | ∘ | — | ✓ | — | ∘ = seller sees "synced from CJ", not the provider's id space |
| `…sync_state`, `last_synced_at`, `last_sync_error`, `attention_json` | ADMIN/OPS | — | — | ∘ | — | ✓ | — | `last_sync_error` can embed a provider response verbatim |
| `…fulfillment_mode`, `overridden_fields_json` | SUPPLIER INTERNAL | — | — | ✓ | — | ✓ | — | dropship vs stocked is a competitive fact |

### F8. VARIANT (`marketplace_listing_variants`, 17 columns)

| Field | Class | ANON | AUTH | SELL | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `listing_id`, `variant_key`, `options_json`, `position` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the choosable axes |
| `price_cents`, `currency` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | per-variant price |
| `stock_state` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | in/out of stock drives `schema.org/Offer availability` |
| `stock_quantity` | DERIVED INTERNAL | ∘ | ∘ | ✓ | — | ✓ | ∘ | ∘ = a *band* ("only a few left"), never the integer — exact stock is a competitor-intelligence feed and a scarcity-gaming oracle |
| `sku` | PUBLIC CONTENT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | the seller's own public SKU |
| `provider_variant_id` | SUPPLIER INTERNAL | — | — | ∘ | — | ✓ | — | names the upstream supplier |
| `cost_cents` | SUPPLIER INTERNAL | — | — | ✓ | — | ✓ | — | margin |
| `stock_synced_at`, `status` | ADMIN/OPS | — | — | ∘ | — | ✓ | — | — |

All 3,797 production variants are positional (`option1/2/3`) with no stored axis
name, so any heading a surface prints is PulseSoc's invention, never the
importer's. That is a correctness note, not a privacy one, but it belongs on the
variant row because a projection that passes `options_json` through unchanged is
also passing through the absence of a label.

### F9. ORDER / PURCHASE (`marketplace_orders` 15 + `seller_transactions` 16)

The only entity with **two** owner-ish audiences that must not see the same
fields.

| Field | Class | ANON | AUTH | BUYER | SELLER | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `listing_id`, `quantity`, `created_at` | PUBLIC CONTENT (scoped) | — | — | ✓ | ✓ | ✓ | ✓ | — | both parties |
| `unit_price_cents`, `amount_cents`, `currency` | FINANCIAL | — | — | ✓ | ✓ | ✓ | ✓ | — | what the buyer paid |
| `status`, `paid_at` | FINANCIAL | — | — | ✓ | ✓ | ✓ | ✓ | — | — |
| `buyer_user_id` | PII (in context) | — | — | ✓ | ∘ | ✓ | ✓ | — | ∘ = seller gets the shipping identity, not the account's email/handle graph |
| `seller_user_id` | PUBLIC IDENTITY | — | — | ✓ | ✓ | ✓ | ✓ | — | — |
| shipping address | PII | — | — | ✓ | ✓ | — | ✓ | — | the seller genuinely needs it to fulfil; moderator does not |
| `platform_fee_cents` | FINANCIAL | — | — | **—** | ✓ | — | ✓ | — | the buyer paid a total; the split is between seller and platform |
| `seller_net_cents` | FINANCIAL | — | — | **—** | ✓ | — | ✓ | — | seller proceeds |
| `payment_provider`, `provider_payment_id`, `stripe_checkout_session_id`, `stripe_payment_intent_id` | SECURITY/FINANCIAL | — | — | — | — | — | ✓ | — | a session id is a capability, not a receipt field |
| `seller_transaction_id`, `item_type`, `item_id` | internal refs | — | — | — | — | — | ✓ | — | — |
| `metadata_json` | **unclassifiable blob** | — | — | — | — | — | ✓ | — | see §G |

Production money tables are empty (treasury/wallets/tickets 0 rows, no
`charge.refunded` ever), so this matrix is being written *before* the data
exists — which is the right order and will not be available again.

### F10. BRIEFING (`daily_briefs`, 9 columns)

| Field | Class | ANON | AUTH | OWNER | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `user_id`, `brief_date`, `created_at` | OWNER PRIVATE | — | — | ✓ | — | ✓ | — | a briefing is addressed to one person |
| `market_pulse`, `watchlist_notes`, `ai_insight` | OWNER PRIVATE | — | — | ✓ | — | ✓ | — | derived from that user's watchlist — i.e. from their holdings |
| `risk_alerts`, `scam_warning` | OWNER PRIVATE + FRAUD/RISK | — | — | ✓ | — | ✓ | — | owner-visible **only in the aggregate, de-identified form already generated**; must never name another account's score |

The whole entity is owner-private, which is why it is in this matrix: it is the
control case. Any projection that gives a briefing a non-owner audience is
wrong by construction, and a test should assert that the briefing projection has
exactly one allowed audience. Note the §15-17 precedent here: the briefing
pipeline has four pre-CLAIM vetoes and a global push opt-out that stops
*generation*, not just delivery — so "no row" is a normal state, not an error,
and a privacy test must not treat an empty briefing as a pass by accident.

### F11. NOTIFICATION (`pulse_notifications`, 15 columns)

| Field | Class | ANON | AUTH | RECIPIENT | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `type`, `title`, `body`, `created_at`, `is_read`, `read_at` | OWNER PRIVATE | — | — | ✓ | — | ✓ | — | addressed to one account |
| `actor_user_id`, `entity_type`, `entity_id`, `target_url`, `deep_link` | OWNER PRIVATE | — | — | ✓ | — | ✓ | — | a deep link is a capability-shaped string; see below |
| `delivery_status` | ADMIN/OPS | — | — | ∘ | — | ✓ | — | — |
| `metadata_json` | **unclassifiable blob** | — | — | — | — | ✓ | — | this is where defect-class leaks land |
| `notification_device_tokens.push_token`, `endpoint`, `p256dh`, `auth`, `token_hash`, `device_id`, `app_version`, `user_agent` | SECURITY INTERNAL | — | — | — | — | ✓ | — | a push token is a send capability; `user_agent` + `app_version` is a fingerprint |
| `notification_preferences.*` | OWNER PRIVATE | — | — | ✓ | — | ✓ | — | includes `blocked_users_json` and `muted_users_json` — disclosing a mute list to the muted party is a harassment vector |

Two architectural regression cases live here (§17). First, the
`"moderation_status"` the author's "Post published" notification carries must
come from the **result envelope**, never from the public serializer — the
`or`-chain at bot.py:94817 kept producing the right value while reading a key
the serializer no longer set. Second, notification **content** is derived from
entities whose field classification differs from the recipient's: a notification
about someone else's post must be projected for *the recipient's* audience, not
the post author's. The deep link must resolve to something the recipient is
allowed to open — and PulseSoc has three deep-link resolvers, one of which
rewrites unknown targets, so an unresolvable link does not fail closed, it fails
somewhere else.

### F12. MESSAGE / CONVERSATION (`comm_v2_messages` 19, `comm_v2_conversations` 29)

| Field | Class | ANON | AUTH | PARTICIPANT | SENDER | MOD | ADM | SE | Rationale |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| `id`, `public_id`, `conversation_id`, `sender_user_id`, `message_type`, `created_at` | participant-scoped | — | — | ✓ | ✓ | ∘ | ✓ | — | ∘ = moderator only on report |
| `body`, `rich_body_json`, `media_id` | participant-scoped | — | — | ✓ | ✓ | ∘ | ✓ | — | — |
| `reply_to_message_id`, `thread_root_message_id` | participant-scoped | — | — | ✓ | ✓ | ∘ | ✓ | — | — |
| `delivery_status` | participant-scoped | — | — | ✓ | ✓ | — | ✓ | — | read receipts are bound by anti-join, not a watermark |
| `client_message_id` | internal ref | — | — | — | ✓ | — | ✓ | — | the idempotency key; **unenforced in prod**, duplicates still accruing (4.65% in Sep) |
| `moderation_status`, `wallet_guardian_status` | MODERATION / FRAUD-RISK | — | — | — | ∘ | ✓ | ✓ | — | ∘ = sender learns their own message was withheld, not its score |
| `metadata_json` | **unclassifiable blob** | — | — | — | — | — | ✓ | — | see §G |
| `edited_at`, `deleted_at` | participant-scoped | — | — | ✓ | ✓ | ∘ | ✓ | — | — |
| conv. `title`, `description`, `avatar_url`, `conversation_type` | participant-scoped | — | ∘ | ✓ | ✓ | ∘ | ✓ | — | ∘ = only when `is_discoverable` and the conversation is a public community |
| conv. `privacy`, `visibility`, `is_discoverable`, `status` | gates | ∘ | ∘ | ✓ | ✓ | ✓ | ✓ | ∘ | gate, not field |
| conv. `member_count` | RELATIONSHIP-SENSITIVE ∑ | — | ∘ | ✓ | ✓ | ✓ | ✓ | — | count for a discoverable community; never the roster |
| conv. participant roster | RELATIONSHIP-SENSITIVE | — | — | ✓ | ✓ | ∘ | ✓ | — | the roster is the privacy object, not the count |
| conv. `owner_user_id`, `created_by_user_id`, `direct_key`, `linked_*_id` | internal refs | — | — | ∘ | ∘ | — | ✓ | — | `direct_key` is a derived pair key; exposing it lets anyone test whether two accounts have a DM |

**Messaging must never gain an anonymous or search audience under any
composition.** It is the one entity in this matrix where the correct value of
both the ANON and the SE column is "not representable," and the projection
should refuse rather than return `{}` — an empty public projection is a
behaviour someone will later "fix" by filling it in.

**App Store screenshots currently claim end-to-end encryption that does not
exist.** That is a separate defect, but it bears on this entity's threat model:
the stated guarantee is stronger than the implemented one, so the implemented
one must at least be honest about `ADM` having access.

---

## G. DEFAULT-DENY, NEW FIELDS, AND THE BLOB PROBLEM (§11, §37)

### G1. The requirement

A column added to `pulse_posts` tomorrow must be invisible to every audience
until someone classifies it. That is the whole of §11 and it is not satisfied by
"remember to check the serializer," because the two P1 defects were both written
by people who were looking at a serializer.

Default-deny has to be a property of the mechanism, not a convention. Concretely:
the projection function takes the *classification map* as its allowlist, not the
row's keys. A column with no entry in the map is absent from every output, and
the system does not need to know the column exists.

### G2. What makes this harder than it sounds

The classification map is a hand-listed set, and **hand-listed schema sets rot**
— there is no migration framework, so a column appears in `init_db()` and
nothing forces a corresponding map entry. The same decay already happened to
`discovery_visibility.REQUIRED_USER_COLUMNS`, which only survives because its
docstring explains the coupling and asks the next author to maintain it.

So the map needs an **active reconciler**, not a docstring:

1. A CI gate enumerates the real columns of each mapped table (from `init_db()`'s
   DDL by AST, or from a live SQLite built by `init_db()` — the latter is more
   faithful and is what `tests/` already does elsewhere).
2. Every column must appear in the map. An unmapped column fails the build with
   the column name and a one-line prompt to classify it.
3. Deny is still the runtime default, so a column that slips through a skipped
   CI run is *invisible*, not exposed. The gate makes it loud; the default makes
   it safe. Both, not either.

This is the inverse of today's posture, where an unclassified column is
published by whichever serializer happens to `SELECT *` near it.

### G3. The `new_risk_metric = 812739` test (§37)

The owner's named mechanism, stated as an executable requirement:

> Add a column `new_risk_metric` to `pulse_posts`, set it to `812739` on a seeded
> post, request every public surface that can reach that post, and assert the
> string `812739` appears in none of them.

The test must add a **real column to a real table in the test database** — not a
dict key, not a monkeypatched row. A dict key proves the serializer ignores
unknown keys; only a real column proves `SELECT *` does not pick it up.

Two properties the value must have, both learned the hard way last phase:

- **Distinct from every other sentinel.** Giving all numeric columns the same
  placeholder is precisely what made the first version of the feed sentinel test
  blind to `risk_score` — the one column it existed for. `812739` must appear
  nowhere else in the fixture.
- **Type-compatible with the serializer's coercions.** `_public_post` runs
  `int()`/`float()` over eleven columns; a string sentinel in one of them raises
  before any assertion runs, and the test reports a `ValueError`, not a leak.
  Numeric sentinels for numeric columns.

### G4. The blob problem

Six fields in §F are marked **unclassifiable blob**:
`pulse_posts.media_ids_json` and `ai_tags_json`,
`marketplace_listings.listing_metadata_json`,
`seller_transactions.metadata_json`,
`pulse_notifications.metadata_json`,
`comm_v2_messages.metadata_json`,
plus `marketplace_product_sources.overridden_fields_json` and `attention_json`.

A JSON blob defeats column-level classification completely. Its *keys* are the
real fields and they are invented at the write site, so the map cannot enumerate
them and the CI reconciler cannot see them. `metadata_json` is where a future
leak will land, because it is the path of least resistance for any engineer who
needs to attach one more thing to an entity.

Three options, in order of preference:

1. **Classify the blob as a whole, deny-by-default, and never project it.**
   Consumers that need one key read it server-side and emit a named, classified
   field. This is the only option that preserves the guarantee.
2. **Per-blob key allowlists** — a nested map. Works, doubles the surface of the
   map, and the reconciler cannot verify it because there is no DDL to compare
   against. Acceptable for one or two blobs, not eight.
3. Project the blob and audit it periodically. This is today's behaviour and it
   is how `metadata_json` will leak.

**Recommendation: option 1 for all eight.** The cost is real — some surface
somewhere will need a new named field instead of a free-form key — and that cost
is the point.

---

## H. THE COMPOSITION API

The owner named `can_expose_field(entity_type, field, audience, context)` and
explicitly said not to implement that exact signature merely because it was
named (§10). I am proposing something close in spirit and different in shape,
for two reasons specific to this repo.

**Why not a per-field predicate.** A function called once per field is called
~200 times per feed page of 20 posts. More importantly it is *opt-in at the call
site*: a serializer that never calls it is never wrong, which is exactly the
failure mode in defect 0.2. A predicate makes correct code correct; it does not
make incorrect code fail.

**Why a projection instead.** A function that *returns the payload* cannot be
partially adopted. Either the serializer's output came out of it — in which case
every field in it was classified — or it did not, and a static gate can see that
the serializer builds a dict literal from a row and flag it.

### H1. Three pieces

```python
# services/exposure/classification.py  — declarative, no logic
FIELDS: dict[str, dict[str, Classification]] = {
    "user":    {"username": PUBLIC_IDENTITY, "email": PII, ...},
    "post":    {"body": PUBLIC_CONTENT, "risk_score": FRAUD_RISK, ...},
    ...
}

# services/exposure/audience.py — (viewer, entity) -> one closed-set Audience
def resolve_audience(cur, entity_type, entity, viewer_user_id) -> Audience: ...

# services/exposure/project.py — the only way a row becomes a payload
def project(entity_type, row, audience, *, extra=None) -> dict: ...
```

`Audience` is a small closed enum, derived from §B7's real relationship model —
`ANONYMOUS, AUTHENTICATED, FOLLOWER, FRIEND, OWNER, SELLER, BUYER, MODERATOR,
ADMIN, SEARCH_ENGINE` — and nothing else. A closed set is what makes the §F
matrix expressible as data and what makes an exhaustiveness test possible.

`Classification` carries the per-audience answer as a frozenset of audiences,
so §F is literally the source file:

```python
PUBLIC_CONTENT  = Classification(frozenset(ALL_AUDIENCES))
PII             = Classification(frozenset({OWNER, ADMIN}))
FRAUD_RISK      = Classification(frozenset({MODERATOR, ADMIN}))   # never OWNER
```

`project()` is a dict comprehension over `FIELDS[entity_type]`, not over
`row.keys()`. That inversion is the whole design.

### H2. The three things this deliberately does *not* do

- **It does not decide access.** `project()` is called only after the route has
  already decided the viewer may have the entity. B1 stays where it is. Merging
  them would reproduce §C's conflation inside the new module.
- **It does not query.** `resolve_audience` queries; `project` is pure. A pure
  projection is testable with a sentinel row and no database, which is what
  makes §W's mutation suite cheap enough to actually run.
- **It does not replace `search_visibility` or `discovery_visibility`.** Those
  answer B2/B4/B5. `project` answers (B) from §C. They compose; see §I.

### H3. Why `SEARCH_ENGINE` is an audience and not a flag

A crawler is an anonymous viewer with one extra property: what it receives
becomes durable and public. Modelling it as a distinct audience with
`SEARCH_ENGINE ⊆ ANONYMOUS` enforced by a test (§W) gives two things a boolean
cannot. It makes §27's no-cloaking rule checkable — the assertion is literally
`allowed(SEARCH_ENGINE) ⊆ allowed(ANONYMOUS)` for every field. And it lets a
field be anonymously fetchable but not indexable (§5) without inventing a
parallel mechanism: `stock_quantity` is the live example, where the anonymous
audience gets a band and the search engine gets the same band, but
`view_count` could legitimately be ANON-yes / SE-no if we decided a crawler
should not cement a changing number into a snippet.

### H4. The migration shape

`project()` is adopted one serializer at a time. During migration, a serializer
that has not adopted it is unchanged and unprotected — which is honest, visible,
and countable. The static gate (§W) reports the count. That number going to zero
is the definition of done for Phase 5.

---

## I. PRECEDENCE (§19, §20)

### I1. Privacy precedence — evaluation order

Evaluated top to bottom. **The first veto wins and nothing below it is
consulted.** Order matters: a lower rule must never be able to un-veto a higher
one, and the way to guarantee that is to stop evaluating.

| # | Rule | Source | Can be overridden by |
| --- | --- | --- | --- |
| 1 | Entity deleted / account deleted | `deleted_at`, `account_status` | nothing |
| 2 | Legal takedown | takedown record | nothing |
| 3 | Block, either direction | `blocked_users` | nothing |
| 4 | Account suspended / restricted / banned | `account_status` | nothing |
| 5 | Moderation withheld | `moderation_status != approved` | nothing — **not by the author**, who may see their own withheld item but cannot publish it |
| 6 | Owner privacy setting | `profile_visibility`, per-post `visibility` | only 1–5 |
| 7 | Audience field classification | §F / `FIELDS` | only 1–6 |
| 8 | Discovery visibility | `discovery_visibility` | only 1–7 |
| 9 | External search eligibility | `search_visibility` | only 1–8 |
| 10 | Index directive rendering | `robots_meta`, sitemap | only 1–9 |

**SEO can never override privacy.** Rules 9 and 10 are at the bottom and have no
upward edges. There is no input to 9 or 10 that can change the answer at 1–8,
and the design must make that structurally true rather than conventionally true:
`search_visibility` must not be *called* until the privacy chain has already
returned "visible," so there is no code path in which an SEO decision is
available to be mistaken for a privacy decision.

The existing code already honours this shape in one place worth copying.
`content_eligibility` runs its vetoes in exactly this order — deleted, takedown,
visibility, moderation, lifecycle status, creator opt-out — and returns on the
first one. That function is the precedence chain for records, already written.

### I2. Discovery precedence

Discovery is narrower than access, and search is narrower than discovery:

```
accessible  ⊇  discoverable  ⊇  externally indexable
```

Each containment must be a test, not a comment, because each has a plausible
violation:

- **accessible ⊋ discoverable** — a `hidden_from_discovery` QA account is still
  reachable by direct URL (correct: the flag is about lists, not addresses).
  The violation to test is the reverse — an account appearing in creator search
  that a direct fetch would 403.
- **discoverable ⊋ indexable** — being suggestable on-platform is not consent to
  be in Google. This is §21 and it is the containment most likely to be eroded
  by a growth argument.

### I3. The one-way door

Three of the six dimensions are reversible and three are not, and the design
should say which:

- **Reversible**: B1 access, B2 discovery, B3 public web access. Flip the flag,
  the next request is gated.
- **Not reversible in practice**: B4/B5 external indexing, and B6 commerce
  publicness once an order exists. A page that was indexed stays in third-party
  caches, scrapers, and Common Crawl long after `noindex` is set. A storefront
  that was public was scraped.

This asymmetry is why B4 for human creators must be opt-in (§J) and why §R's
cache model is required rather than nice-to-have. An access mistake is a bad
day. An indexing mistake is permanent.

---

## J. EXTERNAL SEARCH CONSENT FOR HUMAN CREATORS (§21)

**Proposed data model. Not to be created, migrated, or implemented in this
phase (§44).**

### J1. The rule

A human creator's profile and posts are **not eligible for external search
indexing by default.** Consent must be an affirmative, specific, revocable act.

Specifically: neither `profile_visibility = 'public'` nor
`hidden_from_discovery = 0` may be read as consent to be indexed. The first is a
statement about who on PulseSoc may read the profile. The second is an *admin*
classification the user does not control at all — inferring consent from it
would mean the platform consented on the user's behalf.

`search_visibility.content_eligibility` already states the principle in its own
comment: *"Making a profile public is not consent to be indexed."* It is
currently enforced as an **opt-out** (`search_opt_out` / `noindex` /
`hide_from_search`, checked but with no writer). The proposal is to keep that
veto exactly as it is and add an opt-**in** above it, so the chain reads:
no consent → noindex; consent but opted out → noindex; consent and not opted out
→ eligible, subject to everything in §I1 above it.

Retaining both is not redundancy. The opt-out is the emergency brake and must
keep working even if the opt-in is later mis-defaulted.

### J2. Proposed shape

A column rather than a table, because there is no migration framework and a
join adds a failure mode on a hot path:

```
users.search_indexing_consent   TEXT     DEFAULT 'none'
users.search_indexing_consent_at  TEXT   DEFAULT NULL
```

Values: `'none'` (default, no consent), `'profile'` (profile page only),
`'profile_and_posts'`. A string rather than a boolean, so that widening the
grant later is a new value instead of a second column — the `index_in_search`
boolean the owner rejected is the thing this avoids.

Why not a boolean: the two grants are genuinely different consents. A creator
may want a findable profile without their individual posts being indexed, and a
boolean forces one answer to both. Why not a full table: three columns of
consent do not need a row per user per surface, and `users` already carries
`hidden_from_discovery` and `profile_visibility`, so the privacy state stays in
one place.

Deliberately *not* included: anything resembling `index_in_search`. Adding that
name would reintroduce exactly the collapsed model the owner rejected.

### J3. What must be true before this is built

1. A consent UI that states what indexing means in plain terms, including that
   it is not fully reversible (§I3).
2. Revocation that removes the URL from the sitemap, flips the directive to
   `noindex,follow`, and submits a removal — three actions, because setting the
   column alone changes nothing already crawled.
3. §F1's matrix enforced, so that consenting to be indexed exposes the
   PUBLIC IDENTITY row and nothing else. Consent to be *findable* is not consent
   to have `email` indexed, and today those two would ship together.

**Order matters: 3 before 1.** Shipping consent before the projection means the
first creator who opts in exports their email address to Google.

---

## K. COMMERCE PUBLICNESS IS A DIFFERENT CONTRACT (§22)

A listing is public because a seller executed a **commercial publication act**.
That is a different consent, with a different owner, a different revocation
path, and a different legal basis, from a person choosing a social privacy
setting. The design keeps them in separate dimensions (B6 vs B1/B4) and forbids
either from deriving the other.

Four concrete consequences:

1. **A seller with `profile_visibility = 'private'` still has a public store.**
   Their personal profile is friends-only; their published offers are offers.
   Any implementation that gates the storefront on the owner's personal
   visibility is wrong and will silently unpublish stores.
2. **A public store does not make the owner's profile indexable.** The reverse
   inference is the more tempting one — "they're clearly fine with being found"
   — and it is the one §21 forbids.
3. **Commerce publicness is revoked by unpublishing, not by a privacy toggle.**
   The lifecycle is `draft → submitted → approved → published → archived`, owned
   by `marketplace_listings.status`/`approval_status`, and it has a moderation
   override the personal setting does not.
4. **The seller's PII does not inherit the listing's publicness.**
   `marketplace_sellers.phone` is a person's phone number that happens to sit in
   a commerce table. §F5 denies it to every audience but owner and admin.

The counter-case that makes this concrete: PulseSoc's production marketplace is
**one seller**, and that seller is a real person whose personal profile is a
normal `users` row. Deriving either direction would have been undetectable at
this scale and catastrophic at the next one.

---

## L. THE AUTHORIZATION MECHANISM AS IT EXISTS (§29)

Four mechanisms, no shared abstraction.

**1. Route-level authentication — `services/route_auth.py`.** Decorators
`@auth_required`, `@admin_required`, `@public_route(reason=…)`. **Default-deny
and CI-enforced**: `tests/protection/test_route_auth.py` fails any branch that
adds a route without declaring its auth posture. 18 `@public_route` declarations
exist repo-wide. This is the strongest of the four and the model the others
should follow. Its one known blind spot: the gate reads source text, so a prose
comment can satisfy it — the same class of failure as §W's `getsource` problem.

**2. The social shell — `pulse_social_shell` (bot.py:50820).** The actual login
wall for reels, profiles, stores and spaces. It redirects anonymous traffic to
`/login` *above* the handler, which is why relaxing a handler's own
`require_account` is a no-op. Anyone reasoning about anonymous reachability must
check the shell, not the decorator.

**3. Per-entity access checks, inline in handlers.** The 410/403/403/private
ladder in `api_pulse_public_profile` is a good example: explicit, ordered,
readable. There are hundreds of these and no two are quite alike.

**4. `services/profile_viewer_permissions.py`.** The only piece that resembles
the design proposed here: a declarative deny-by-default dict, relationship
inputs kept deliberately narrow, grouped flags so a caller cannot open half of
them, and a docstring that states the doctrine — *"a hidden button is not access
control."*

Its three limitations are the argument for §H:

- **It answers (A), never (B).** Fourteen booleans about *surfaces*, zero about
  *fields*. The profile route calls it and then leaks an email anyway. Access
  control working perfectly did not help.
- **One caller.** bot.py:114862. The canonical mechanism covers one route.
- **It reads a column that does not exist** (defect 0.3), and its
  `_fetch_account` does `SELECT * FROM users` into a dict that it hands back to
  the caller as the `account` parameter — the §11 hazard in the one module built
  to prevent this class of problem.

That last point is not a criticism of the module. It is the evidence that
discipline at the call site is not sufficient, which is the premise of this
whole document.

---

## M. SERIALIZER DEPENDENCY AUDIT (§18)

> *"Public projection should be a terminal presentation boundary."*

Audited: `_public_post`, `pulse_feed_engine.get_post`, `list_feed`,
`intelligence_panel`, and their downstream consumers across workers,
notifications, realtime, moderation, analytics, search, recommendation, admin,
payment and fulfilment code.

**Finding: the boundary is nearly clean. Two violations, both in the same route,
both found by removing three keys from one serializer.**

| # | Site | Reads | Used for | Why it was invisible |
| --- | --- | --- | --- | --- |
| 1 | bot.py ~94850 | `result["post"]["moderation_status"]` | gates the realtime "new post" broadcast | `.get()` on a removed key is `None`, so the gate would have gone **permanently false** — live posts stop appearing, no exception, every test green |
| 2 | bot.py 94817 | `sync_post.get("moderation_status") or result.get("status")` | the author's "Post published" notification metadata | the `or`-chain kept producing the correct value while reading a key that no longer exists; survived by luck, not design |

Both are fixed in `7a56d7f70` (unpushed), reading the **envelope**
(`result.get("status")`) rather than the serialized post. Violation 1 is pinned
by a static test; it is a silent *absence*, so a runtime test proves only that
the gate fires today.

**Verified clean** (consume the public projection but make no decisions from it):

- `bot.py:92870` `pulse_post_page` → `search_visibility.content_eligibility(post)`.
  Safe by construction: a missing `moderation_status` reads as `""`, fails
  `!= "approved"`, and defaults to **noindex**. Fails closed.
- `services/feed_intelligence_service.py:54-75` `_post_record` — pure reshaping.
- `bot.py:51961` `pulse_reel_payload` — merges a reel row with the post payload;
  presentation only.
- `services/business_os/profile/service.py:1164` `public_profile` — has its own
  allowlist; nothing reads back from its output.

**Root cause.** Internal control flow read a *presentation DTO* as if it were a
*domain object*. The two violations are not carelessness; they are the
predictable result of the serializer's output being the most convenient dict in
scope. The fix is architectural: once `project()` exists, its return value
should be marked as terminal (a distinct type, or at minimum a naming
convention plus a static gate), so that reading a field off it inside business
logic is a visible mistake rather than an ordinary dict access.

**The remaining risk this audit cannot cover.** It traced the *post* serializers
because those are the ones whose keys changed. The profile, listing, order and
message serializers have not had keys removed, so an equivalent violation in
them is currently unobservable. Phase 5 should re-run this audit per serializer
*as* each one adopts `project()` — the adoption is what makes the violations
visible, which is an argument for adopting rather than auditing first.

---

## N. DISPOSITION OF THE TWO EXISTING MODULES (§39, §40)

**Neither should be replaced.** Both are better than what would be written to
replace them, and a big-bang rewrite would be the single riskiest action
available in this mission.

### N1. `services/discovery_visibility.py` (65 lines) — KEEP AS IS

**What is good.** It does one thing: emit a SQL boolean fragment for dimension
B2. It is NULL-tolerant, so a missing column reads as visible rather than hiding
every account. It is dialect-safe for SQLite and PostgreSQL with no bind
parameters, which is what lets callers append it to arbitrary queries. It
validates its alias (`_safe_alias`) instead of trusting the caller. And it
co-locates `REQUIRED_USER_COLUMNS` with the predicate that depends on them, with
a docstring explaining exactly why: *"adding a third column to the predicate
without adding it here would reintroduce exactly that outage."* That is a
module that has already learned something and written it down.

**What should remain.** All of it, unchanged.

**What overlaps.** `HIDDEN_ACCOUNT_STATUSES` and
`profile_viewer_permissions.UNAVAILABLE_STATUSES ∪ RESTRICTED_STATUSES` are two
hand-maintained lists of the same concept, and they **already disagree**:
`deactivated` and `closed` appear in one, `disabled_qa` in the other. That
divergence is a real bug waiting to happen and it is the only change this module
needs — not a rewrite, a single shared status vocabulary that both import.

**What should move.** Nothing out. One thing in, eventually: the status
vocabulary above should live in the new `services/exposure/` package and be
imported here, so there is one list.

**What should be deprecated.** Nothing.

### N2. `services/search_visibility.py` (423 lines) — KEEP, EXTEND IN ONE PLACE

**What is good.**

- The `Decision` dataclass is frozen and carries its own `reason`. Every
  classification can explain itself, which is what makes an SEO regression
  debuggable.
- `indexable` is *derived* from the directive (`directive.startswith("index")`)
  rather than stored alongside it, so the two cannot disagree.
- `_RULES` is ordered and first-match-wins, with a reason per entry.
- `robots_disallow_prefixes()` only disallows `noindex,nofollow` paths, because
  a `Disallow` stops the crawl and the `noindex` is then never read. That is a
  subtle, correct, and widely-got-wrong detail.
- `content_eligibility` is the precedence chain of §I1 already written, in the
  right order, returning on the first veto.
- The creator opt-out outranks everything above it, with the comment that states
  the §21 doctrine.
- The `/store` rule is deliberately shaped so that publishing storefronts is a
  line someone edits on purpose.

**What should remain.** The path classifier, `Decision`, `_RULES`,
`robots_disallow_prefixes`, `canonical_url`, `_CANONICAL_ALIASES`, and the whole
of `content_eligibility`'s ordering.

**What overlaps.** `content_eligibility`'s first five gates — deleted, takedown,
`visibility != public`, `moderation_status != approved`, lifecycle status — are
the same facts the access layer (B1) and the projection layer (§H) evaluate.
Three evaluations of one truth. The overlap is currently harmless because all
three agree; it becomes a defect the first time one is updated alone.

**What should move.** The five shared gates should become one shared predicate
that all three layers call, living in `services/exposure/`. `search_visibility`
would then be: shared-gates → creator consent → automated author → thinness.
That is a ~40-line change, not a rewrite, and it is the *only* structural change
this module needs.

**What should be deprecated.** `bot.py:42005` reaches into `_RULES` — a private
name — to list noindex prefixes. That needs a public accessor. Minor.

**The real gap is not in the module; it is in how it is called.**
`robots_meta(path)` is invoked per template, at ~15 sites. A new public HTML
route that forgets to call it emits **no robots directive at all** and is
indexable by default. `search_visibility` cannot fix this from the inside. It
needs an `after_request` hook (or a shared template base) that applies
`classify(request.path)` to every HTML response that did not set the header
itself — making the directive the default rather than an opt-in. There is no
`X-Robots-Tag` on `/pulse/post/<id>` today; the only directive is the meta tag,
which also means non-HTML responses (JSON endpoints, feeds) have no directive
mechanism at all.

---

## O. THREAT MODEL — ABUSE AND HARASSMENT (§23)

| # | Threat | Live today? | Enabled by | Mitigation in this design |
| --- | --- | --- | --- | --- |
| O1 | **Scoring oracle.** An abuser posts variations until `risk_score` drops, then publishes the scam with a score the system trusts. | **Yes** — anonymous, `/pulse/post/<id>` | FRAUD/RISK field projected to everyone | §F2 denies `risk_score` to every audience including the subject; hotfix 0.1 |
| O2 | **Moderation reconnaissance.** Learning which content types trigger review lets an abuser route around the queue. | **Yes** | `moderation_status` on the public page | §F2: MODERATION is subject + moderator only |
| O3 | **Email harvesting at scale.** One throwaway account enumerates profile keys and collects every member's email and legal name. | **Yes** — authenticated | defect 0.2 | §F1 denies PII to AUTH; hotfix 0.2. Rate limiting is a second layer, not the fix |
| O4 | **Deanonymisation by correlation.** Matching a pseudonymous handle to a legal name, Telegram handle, or phone. | **Yes** (via O3) | `full_name`, `telegram_username` in a shared allowlist | §F1 classifies all second-platform identifiers as PII |
| O5 | **Presence stalking.** `last_seen_at` lets an abuser build a daily schedule for a target. | Unverified | RELATIONSHIP-SENSITIVE unclassified | §F1 denies presence to non-owners |
| O6 | **Follower-graph mining.** Harvesting a target's followers to find secondary contacts to harass. | Unverified | list vs count not distinguished | §D's aggregation rule; §F1 denies the list, allows the count |
| O7 | **Mute-list disclosure.** Telling someone they were muted escalates rather than de-escalates. | Unverified | `notification_preferences.muted_users_json` / `blocked_users_json` | §F11: OWNER PRIVATE |
| O8 | **Block circumvention via a second surface.** A blocked viewer reads the target through search, a sitemap, a reel sound page, or a commerce listing. | Plausible | block is checked in `profile_viewer_permissions`, which has one caller | §I1 puts block at rank 3, above every other rule, evaluated before projection on every surface |
| O9 | **Permanence.** A harassment target revokes visibility; the indexed copy persists. | Structural | §I3's one-way door | §J3's revocation must do three things, not one |
| O10 | **Report-reveal.** A reported user learns who reported them from an exposed `reviewed_by` / `review_notes`. | Unverified | ADMIN/OPS fields near public ones | §F5/§F7 deny reviewer identity to every non-admin audience |

O8 deserves emphasis. Block is the one control users understand as absolute,
and it is currently enforced in a module with **one caller**. Every other
surface that can render another user's content — feed, search, reels, comments,
marketplace, notifications — makes its own decision. A block that holds on the
profile and leaks on the reel page is, to the person being harassed, not a
block. **Phase 2 should make the block check shared before it makes anything
else shared.**

---

## P. THREAT MODEL — COMMERCE ABUSE (§24)

| # | Threat | Enabled by | Mitigation |
| --- | --- | --- | --- |
| P1 | **Margin disclosure.** A competitor reads `supplier_cost_cents` next to the retail price and undercuts exactly. | SUPPLIER INTERNAL projected | §F7: seller + admin only |
| P2 | **Supply-chain mapping.** `provider`, `provider_product_id`, `external_sku` identify the upstream supplier; a competitor contacts them directly, or a buyer buys from them. | supplier IDs projected | §F7/§F8: never public; seller sees a provider *name*, not the id space |
| P3 | **Scarcity gaming.** Exact `stock_quantity` lets a bad actor buy out inventory to force a stockout, or lets a competitor measure sales velocity daily. | integer stock | §F8: a band, never the integer, for every audience below seller |
| P4 | **Fee reverse-engineering.** A buyer who sees `platform_fee_cents` learns the seller's take rate and can pressure them off-platform. | FINANCIAL projected to the buyer | §F9: fee and `seller_net_cents` are seller-only; the buyer gets the total they paid |
| P5 | **Payment-handle replay.** `stripe_payment_intent_id` / `checkout_session_id` in a response body. | SECURITY/FINANCIAL | §F9: nobody over the wire |
| P6 | **Safety-score oracle.** A fraudulent seller tunes a listing against `safety_score` until it clears review. | FRAUD/RISK | §F7: moderator + admin only. Same class as O1 |
| P7 | **Seller doxxing.** `marketplace_sellers.phone` is a person's phone on a commerce entity. | commerce publicness assumed to cover the whole row | §K's rule 4; §F5 |
| P8 | **Buyer-identity leakage to the seller.** A seller gets the shipping identity and parlays it into the buyer's social account. | `buyer_user_id` projected | §F9: the seller gets fulfilment identity, not the account graph |
| P9 | **Review manipulation via `reviewed_by`.** Knowing which admin approves which listings enables targeted social engineering. | ADMIN/OPS | §F5/§F7 |
| P10 | **Sync-error disclosure.** `last_sync_error` can embed a provider's verbatim response, including credentials-adjacent detail. | blob-adjacent free text | §F7: ADMIN/OPS, never projected |

Two structural notes. The offers checkout lane can double-charge and is latent
only because nothing calls it — a projection change must not accidentally give
it a caller. And the commerce catalogue has exactly **one seller** in
production, which means none of P1–P10 would be observable today; this matrix is
being written against the second seller, not the first.

---

## Q. THREAT MODEL — SEARCH-SPECIFIC (§25)

| # | Threat | Why search makes it worse | Mitigation |
| --- | --- | --- | --- |
| Q1 | **Durability.** Anything indexed survives revocation in third-party caches, scrapers, and Common Crawl. | a privacy mistake becomes permanent | §I3; §J's opt-in; §J3's three-action revocation |
| Q2 | **Snippet exfiltration.** `max-snippet:-1` in the index directive means an unbounded snippet. A leaked field becomes a search-result snippet even if the page is later fixed. | the leak is copied out of the page | audit the directive per surface; unbounded snippets belong on marketing pages, not on entity pages |
| Q3 | **Name-plus-handle joins.** Google becomes the join engine between a pseudonymous handle and a legal name indexed from one careless page. | search does the correlation an attacker would have to do by hand | §F1 keeps PII out of every anonymous representation, so there is nothing to join |
| Q4 | **Sitemap as a disclosure channel.** A sitemap entry reveals that an entity exists, its id, and its last-modified date — before anyone fetches it. | enumeration without a request per entity | `sitemap_eligible` requires *both* path and record eligibility; keep that conjunction |
| Q5 | **Default-indexable paths.** `_RULES` falls through to `index,follow`, and `robots_meta` is opt-in per template. A new public route is indexed unless someone remembers. | silent, and discovered by Google first | §N2's `after_request` default |
| Q6 | **Non-HTML surfaces have no directive.** JSON endpoints and feeds cannot carry a meta tag; there is no `X-Robots-Tag` today on entity pages. | an indexable JSON API is uncontrollable | add `X-Robots-Tag` at the same `after_request` hook |
| Q7 | **Disallow-hides-noindex.** Adding a `Disallow` to a `noindex` page keeps it indexed, because the crawler never reads the directive. | counterintuitive; very commonly got wrong | **already correct** — `robots_disallow_prefixes()` only disallows `noindex,nofollow` paths |
| Q8 | **Thin-content and automated-author indexing.** Indexing automated posts dilutes the domain and publishes machine output under a human-looking byline. | reputational, and a quality penalty | **already handled** by `is_automated_author` and `MIN_INDEXABLE_BODY_CHARS`; note the automated-author veto currently misses PulseDrop promos, which is what `b548452a7` fixes |

---

## R. THREAT MODEL — CACHE (§26) — REQUIRED

Measured against production today.

### R1. The four layers, as they actually are

| Layer | Present? | Evidence |
| --- | --- | --- |
| **CDN** | **Not** in front of `pulsesoc.com`. Cloudflare fronts `cdn.coinpilotx.app` (R2 media) only. | no `cf-cache-status` on any `pulsesoc.com` response; `server: railway-hikari` |
| **Server / reverse proxy** | Railway edge. Normalises `Host`, overwrites `X-Forwarded-Proto`. Caching behaviour not documented by Railway. | prior probing |
| **Application** | Redis is optional and not universally enabled; several services memoise in-process. | config |
| **Browser / service worker** | Yes — PWA. Static assets served with a 1-year immutable header keyed by path prefix. | `static/` cache-token rule |

### R2. Headers in production today

| Surface | `Cache-Control` | `Vary` |
| --- | --- | --- |
| `/pulse/post/<id>` | `no-store, max-age=0` | `Cookie`, `accept-encoding` |
| `/pulse/marketplace` | `public, max-age=300` | `Cookie`, `accept-encoding` |
| `/pulse/marketplace/<id>` | `public, max-age=300` | `Cookie`, `accept-encoding` |
| `/sitemap-posts.xml` | `public, max-age=300` | `Cookie`, `accept-encoding` |

### R3. Findings

**R3a — `public, max-age=300` + `Vary: Cookie` is load-bearing and fragile.**
It is *currently* safe: a session cookie is unique per user, so a shared cache
keyed on the cookie never serves one user's entry to another. But it is safe by
accident of key cardinality, not by design, and it has two failure modes. If a
handler ever drops the `Vary: Cookie` — or a proxy normalises it away, which
some do precisely because it destroys hit rates — a personalised response
becomes shareable. And because `Vary: Cookie` makes the hit rate approximately
zero, someone will eventually "fix" the caching by removing it.

**The rule this design adopts: a response whose body depends on the viewer's
audience must be `private` or `no-store`. Never `public` plus `Vary`.** The
audience the projection used is the cache key, and it must be expressed as
`Cache-Control: private` rather than as a header a proxy is free to collapse.

**R3b — `/pulse/post/<id>` is `no-store`, which is why the risk-score leak was
not also a cache-poisoning incident.** Worth stating because it is luck: the
same leak on a `public, max-age=300` surface would have been served to viewers
other than the one whose request produced it.

**R3c — no CDN means no CDN purge.** The remediation path for a leaked field is
"deploy and wait 300s," with no way to purge an intermediary. That is acceptable
at `max-age=300` and would not be at a longer TTL. **Any future TTL increase on
an entity page must come with a purge mechanism.**

**R3d — the PWA cache is the longest-lived layer.** A leaked field rendered into
a cached HTML shell or a `static/` asset persists on the device for up to a
year, and editing a `static/` asset without bumping its `?v=` token ships an
undeliverable fix. A privacy fix that touches a tokenised asset must bump the
token, and the boot-profile strips that `.replace()` on rendered HTML must be
updated for every profile, token included.

### R4. Invalidation requirements

1. **Audience is part of the cache key or the response is not shared.**
   `private` / `no-store` for anything above the ANONYMOUS audience.
2. **A revoked consent must invalidate within one TTL**, and the TTL must be
   short enough that one TTL is an acceptable exposure window. 300s is.
3. **Sitemap and feed caches must not outlive the record gate.** An unpublished
   listing must leave `/sitemap-products.xml` and the Merchant Center feed within
   one TTL, or a crawler fetches a URL that now 404s and the 404 is what gets
   indexed.
4. **Index-directive changes need a crawl signal, not just a cache expiry.**
   `noindex` only takes effect when re-crawled; the IndexNow submission at
   bot.py:31451 is the existing mechanism and revocation should use it.
5. **No privacy guarantee may rest on a cache layer.** Caches are an
   availability mechanism. If the only thing keeping a field private is that the
   page is not cached, the field is not private.

---

## S. NO CLOAKING; THE NATIVE CLIENT IS NOT A ROLE (§27, §28)

### S1. Googlebot gets the anonymous representation

Not more, not less. No user-agent branch, no IP-based branch, no "render the
full page for the crawler and gate it for humans."

Expressed in the model as a containment, so it is testable rather than
aspirational:

```
allowed_fields(SEARCH_ENGINE)  ⊆  allowed_fields(ANONYMOUS)     for every field
```

A field may be ANON-yes / SE-no (fetchable but not worth cementing into a
snippet — §H3). A field may never be SE-yes / ANON-no: that is cloaking, it is a
Google policy violation, and it means the privacy model and the SEO model
disagree about the same viewer.

Note the one legitimate asymmetry this does *not* forbid: the **directive**
differs by surface (`noindex,follow` vs `index,follow`) while the **content**
does not. Telling a crawler not to index a page it can read is not cloaking.
Showing it different content is.

### S2. "Native client" is not a privileged security role

The iOS app is an untrusted client. Its bearer token identifies a *user*, not a
*trust tier*. Nothing in the audience enum corresponds to "is the native app,"
and nothing may.

Three concrete prohibitions:

1. **No field is granted because the request came from the app.** If the native
   profile payload may carry a field, the web payload for the same audience may
   too; if it may not, the app does not get it either. Defect 0.2 exists partly
   because `pulse_mobile_user_payload` was a *mobile* payload — a client-shaped
   boundary, which is the wrong axis — and then got reused for a different
   audience.
2. **Client-side hiding is not access control.** Already the stated doctrine of
   `profile_viewer_permissions`: *"a hidden button is not access control, and
   'fetch the private payload and hide it in the UI' is exactly the failure this
   module exists to remove."* That principle extends from surfaces to fields.
3. **App version is not a capability.** Contract versioning (§U) may change the
   *shape* of a response for an older build; it may never change the *audience*.
   "Old builds still get `email` because they'd crash otherwise" is not a
   migration strategy, it is a permanent exemption.

---

## T. DATA MINIMIZATION AND LOGGING (§30, §38)

### T1. Minimization

Three rules, each aimed at a pattern that exists in the repo today:

1. **Query the columns you project.** `SELECT *` into a dict that becomes a
   payload is the mechanism of defect 0.2 and the shape of
   `profile_viewer_permissions._fetch_account`. Once `project()` exists it knows
   the column list for an (entity, audience) pair; the query should be generated
   from the same list, so an unprojected column is never read.
2. **Resolve, do not forward.** A payload should carry `"sound_title"`, not
   `audio_track_id` plus the client's instruction to go look it up. Forwarding
   internal ids is how `media_ids_json`-shaped fields end up public.
3. **Counts, bands and outcomes instead of values.** `stock_quantity` → a band.
   `account_status` → an availability outcome (410/403), not the string.
   `risk_score` → nothing at all. Each of these is a minimization that also
   closes an oracle in §O/§P.

### T2. Logging is a separate axis

The two are independent and a single sensitivity number cannot express both:

| Field | Client exposure | Log exposure | Why they differ |
| --- | --- | --- | --- |
| `risk_score` | nobody | **full fidelity** | moderation review depends on it; it is internal by definition, which is exactly what a log is |
| `moderation_status` | subject + moderator | full | — |
| `email` | owner | **hashed or omitted** | logs are replicated, retained, and read by more people than any endpoint |
| `full_name`, `phone`, `date_of_birth` | owner | **omitted** | — |
| `push_token`, `p256dh`, `auth` | nobody | **omitted** — log `token_hash` instead | the column already exists for this purpose |
| `stripe_*_id` | nobody | reference only, never in an error body | — |
| shipping address | buyer + seller | omitted | — |
| `body` (post/message) | per §F | **message bodies omitted**; post bodies truncated | a message body in a log defeats the participant scope entirely |

Two repo-specific hazards:

- **`last_sync_error`** (§P10) stores a provider's verbatim response in a
  *database column*, which is a log that is also a field. It must be classified
  ADMIN/OPS and must be scrubbed on write, not on read.
- **Exception bodies.** `logging.exception` with a row in scope serialises the
  row. The PulseDrop overlay at bot.py:114878 does exactly this pattern
  (correctly, with only a `user_id`), and it is the pattern to standardise:
  log identifiers, never rows.

**Retention.** Not designed here, but the classification makes the question
answerable: PII and FINANCIAL logs need a retention ceiling; FRAUD/RISK and
MODERATION logs need a retention *floor* for appeals. Those are opposite
requirements and they are a reason the two axes cannot be merged.

### T3. The existing ip_hash precedent

The `ip_hash` inventory already has three traps grep will not find, and the
largest hash store is `visitor_logs.ip_address` — a column whose *name* says it
holds an IP and whose contents are a hash. A classification keyed on column
names would get that backwards. It is the argument for classifying by declared
intent in a map, not by inference from the schema.

---

## U. SURFACE COMPATIBILITY AND CONTRACT VERSIONING (§31, §32)

### U1. Compatibility matrix

| Surface | What the change touches | Risk | Mitigation |
| --- | --- | --- | --- |
| **Web (server-rendered)** | the `/pulse/post/<id>` byline; future `after_request` directive default | low — HTML, no contract | render tests |
| **iOS (shipped App Store build)** | profile, feed, listing, order payloads | **highest** — a shipped build cannot be patched | §U2 |
| **Realtime** | the broadcast gate already found reading a removed key (§M) | **high and silent** | static gate; the gate reads the envelope, never the projection |
| **Notifications** | notification content is derived from entities with a *different* audience than the recipient (§F11) | medium | project for the recipient's audience; three deep-link resolvers, one of which rewrites unknowns |
| **Pulse Loop / feed** | `_public_post` is the hottest serializer | medium | already has a sentinel allowlist test with both branches covered |
| **PulseDrop** | an ordinary `users` row with an additive overlay; the overlay merges last and narrowly | low | its failure already degrades rather than 500s |
| **Discovery / search (on-platform)** | `discovery_visibility` unchanged | low | — |
| **Marketplace** | listing/variant/seller projections | medium | tests pin source literals, so a projection change will surface as a test diff |
| **Checkout** | order + transaction projections; the latent double-charge lane | **high** | do not touch the offers lane; freshness window must track `supplier_worker`'s real 20-jobs/300s throughput, not nominal cadence |
| **Admin** | admin surfaces legitimately see everything | low | ADMIN audience exists precisely so admin code does not bypass `project()` |

### U2. Versioning rule for the shipped client

**Removing a field from a payload is a breaking change to a build that cannot be
updated.** The rule:

1. **Additive is free.** New fields, new audiences, new entity types.
2. **Removal requires evidence of non-use.** Grep `mobile-native/src` for the
   key. For defect 0.2 this was done: no screen reads `email` or `full_name`
   from an *other-user* profile payload; the app reads `authState.user?.email`,
   which comes from `/api/mobile/auth/session` — a different route, owner
   audience, untouched.
3. **Where a shipped build genuinely depends on a field it should not have,
   narrow rather than remove.** Keep the key, empty the value. An empty string
   is a shape the client already handles (every field in
   `pulse_mobile_user_payload` is `… or ""`), and it closes the exposure
   immediately. Remove the key in the next major.
4. **Never version by audience.** See §S2.3.
5. **`pulseApi` specifics.** The client reads `error_code`, not `code`; a
   projection that starts refusing a request must answer in the shape the client
   parses, or the user sees a generic failure. And the client's 15s read
   deadline bounds writes too, so a projection that adds a query to a write path
   has a budget.

### U3. The deployment reality

`main` takes ~60 commits/day from parallel sessions and cannot be frozen.
Phases must be independently shippable and independently revertible, and each
must leave the tree green on its own. A phase that only makes sense alongside
the next one will be half-deployed.

---

## V. TEST STRATEGY (§33, §36)

Ten kinds. Each names the specific failure it exists to catch, because a test
whose failure mode cannot be named is a test that will be deleted during the
next refactor.

1. **Classification completeness.** Every column of every mapped table has a
   classification. *Catches:* the 107th column on `users`. Reads the real schema
   from an `init_db()`-built database, not a hardcoded list.
2. **Projection allowlist, per entity × audience.** Seed a row with one distinct
   sentinel per column; project; assert every unallowed sentinel is absent.
   *Catches:* a field reaching an audience it was not classified for. **Distinct
   sentinels are mandatory** and numeric columns need numeric sentinels (§G3).
3. **Branch coverage within a serializer.** Run the probe against *every*
   branch. *Catches:* the live branch of `_public_post`, which emits five keys
   the ordinary branch does not; a single-branch probe calls those columns clean.
4. **Case- and type-insensitivity of the assertion.** The live branch lowercases
   `live_status`; a case-sensitive search reported a leaking column as clean.
   *Catches:* an assertion that silently matches nothing.
5. **Non-empty preconditions.** Every payload assertion first asserts the
   payload is non-empty. *Catches:* the most common way a privacy test passes —
   by iterating an empty list. The `scam_warnings` test needed exactly this.
6. **Audience containment.** `SEARCH_ENGINE ⊆ ANONYMOUS ⊆ AUTHENTICATED` for
   PUBLIC classes; `accessible ⊇ discoverable ⊇ indexable` (§I2). *Catches:*
   cloaking, and a discovery surface outrunning the access gate.
7. **Precedence order.** Construct an entity that trips two rules at different
   ranks and assert the higher one's answer. *Catches:* a lower rule un-vetoing
   a higher one — specifically, any path where SEO reaches privacy.
8. **New-field safety (§37).** The `new_risk_metric = 812739` test, with a real
   column on a real table. *Catches:* a future column becoming public by
   default.
9. **Terminal-boundary test.** Project an entity, then assert that no internal
   decision path reads a key off the result. Static, because the failure is a
   silent *absence* (§M). *Catches:* the realtime broadcast gate going
   permanently false with every test green.
10. **Runtime end-to-end, per surface.** Real HTTP request as each audience
    against a seeded database; assert the sentinel does not appear anywhere in
    the response body — including headers, JSON-LD, meta tags, and inline JS.
    *Catches:* everything the unit tests do not, specifically a template that
    interpolates a column the serializer never carried. **This is the only kind
    that would have caught defect 0.1**, because the leak was in a Jinja string,
    not in a serializer.

Two process requirements: every new test file must join
`config/ci_test_manifest.json` or the default-deny manifest gate fails, and
backend tests need `.venv` — system `python3` lacks `pytest`/`requests` and
fakes a protection-suite failure.

---

## W. MUTATION STRATEGY AND THE CI GATE (§34, §35)

### W1. Nine mutations

Each must turn the suite **red**, and the specific test that catches it must be
named. A mutation caught only by an unrelated test is not covered.

| # | Mutation | Must be caught by |
| --- | --- | --- |
| W1 | Re-add `risk_score` to a public projection | V2 sentinel (per entity) |
| W2 | Re-add `moderation_status` to a public projection | V2 |
| W3 | Add a new unclassified column and project it | V1 completeness + V8 |
| W4 | Add a new unclassified column and *do not* project it | V1 completeness **only** — this is the mutation that proves the completeness test is not redundant with the sentinel test |
| W5 | Grant `PII` to `AUTHENTICATED` in the classification map | V2 on the user entity + V6 containment |
| W6 | Grant a field to `SEARCH_ENGINE` but not `ANONYMOUS` | V6 containment |
| W7 | Reorder the precedence chain so SEO precedes privacy | V7 |
| W8 | Change a serializer to build its dict from `row.keys()` instead of the map | V2 (every unclassified sentinel appears) + the static gate |
| W9 | Revert the realtime gate to read the projection | V9 static |

Three further mutations specific to the *tests themselves*, because a privacy
suite that cannot fail is worse than none:

| # | Mutation | Must be caught by |
| --- | --- | --- |
| W10 | Make all numeric sentinels identical | a meta-test asserting sentinel uniqueness — this is the exact blindness that hid `risk_score` |
| W11 | Make the seeded fixture produce an empty payload | V5 non-empty precondition |
| W12 | Make the assertion case-sensitive where the serializer casefolds | V4 |

### W2. The static CI gate must read structure, not text

The owner cited the `getsource` incident as evidence, and it is the right
evidence: a static assertion searched a function's source for a forbidden
spelling, and matched **the comment explaining why the spelling had been
removed**. The fix at the time was to strip comment lines — correct locally,
and a demonstration that text matching is the wrong tool.

The gate must therefore:

1. **Parse with `ast`, not `re`.** Comments and docstrings are not nodes, so
   they cannot match. The current workaround — stripping lines that start with
   `#` — misses a trailing comment on a code line.
2. **Match on node shape.** "A `Subscript`/`Call` whose base resolves to a
   projection result and whose key is a FRAUD/RISK or MODERATION field" is a
   structural query. `'post["risk_score"]'` is a string that a refactor to
   `post.get(KEY)` defeats silently.
3. **Read the schema from a built database**, not from parsed DDL text. There is
   no migration framework; `init_db()` is imperative, and `add_columns_if_missing`
   calls are not statically enumerable. Build the DB, `PRAGMA table_info`.
4. **Guard the import.** `import bot` connects and runs `init_db()` at module
   scope, and a cold `__pycache__` makes it ~200× slower. The gate needs the
   guard above the import that the existing suites use.
5. **Beware `splitlines()`.** `bot.py` contains raw U+2028/U+2029 inside JS
   strings; `str.splitlines()` splits on them and `ast` does not, so line
   indices computed from `splitlines()` are shifted. Use `ast` line numbers
   throughout or the gate reports the wrong function.
6. **Fail closed on parse failure.** A gate that skips when it cannot parse is a
   gate that is off.
7. **Do not let the ambient CI environment satisfy the check.** Actions sets
   variables on every step, and a fail-closed check that reads one of them
   passes vacuously in CI while failing locally — or worse, the reverse.

### W3. Runtime privacy test (§36)

Distinct sentinel values are mandatory, and the reasoning is now a documented
production lesson rather than a preference: giving every numeric column the same
placeholder made the first sentinel test structurally unable to detect
`risk_score`, which was the single column it had been written for. The test was
green, the mutation harness said it was green for the right reason, and it was
blind.

Requirements: one distinct value per column; numeric where the serializer
coerces; the assertion lowercases both sides; the payload is asserted non-empty
first; and the search covers the **entire response body**, not the parsed JSON —
headers, JSON-LD blocks, `<meta>` tags and inline `<script>` payloads are all
places a column has reached the open web in this codebase.

---

## WORKED EXAMPLES (§43) — the model evaluated against nine real cases

Nine cases drawn from the live product, run through §B's dimensions and §I's
precedence. The point of this section is to show the model producing answers,
including answers that are *different from today's behaviour* and one where the
model refuses.

### Example A — Anonymous visitor opens `/pulse/post/2516`, an approved public post

| Dimension | Answer |
| --- | --- |
| B1 access | ✓ — public visibility, approved, not deleted |
| B3 public web | ✓ — `@public_route`, outside the social shell |
| Audience | `ANONYMOUS` |
| Projection | title, body, author identity, timestamps, `engagement_score`, counts |
| **Denied** | `risk_score`, `moderation_status`, `sentiment`, `status`, `pinned_by`, `media_ids_json` |
| B4/B5 | `noindex,follow` — not in the 15-URL sitemap set |

**Today:** the page prints `Risk score: 45`. The model's answer and production
differ, and the model is right. This is defect 0.1.

### Example B — The *author* of that same post opens it

Audience `OWNER`. Everything in A, **plus** `moderation_status` and `status` —
they are entitled to know their own content's review state — **and still not
`risk_score`**.

This is the case that justifies splitting MODERATION from FRAUD/RISK (§D). A
model with one "internal" class gives the author both or neither, and both is
the scoring oracle of §O1 while neither withholds a decision they are owed.

### Example C — Googlebot fetches the same URL

Audience `SEARCH_ENGINE`. Field set **identical to A** — that is §S1, enforced
by the containment test. The directive differs (`noindex,follow` here), which is
a B5 answer, not a content answer.

If the post were one of the 15 sitemap posts, the directive becomes
`index,follow` and the field set is *still* identical to A. Indexability never
widens exposure.

### Example D — A logged-in stranger opens another member's profile

| Dimension | Answer |
| --- | --- |
| B1 access | ✓ — `profile_visibility='public'`, not blocked, active |
| Audience | `AUTHENTICATED` |
| Projection | username, display name, avatar, banner, bio, badges, country, follower/following **counts** |
| **Denied** | `email`, `full_name`, `phone`, `telegram_*`, `date_of_birth`, `last_seen_at`, the follower **list**, `profile_visibility`, `hidden_from_discovery`, every `user_trust_profiles` score |

**Today:** `email` and `full_name` are returned. Defect 0.2.

### Example E — The same stranger, after the profile owner blocks them

B1 rank 3 vetoes. `viewer_permissions` returns `DENY_ALL` plus
`can_report`/`can_block`. **Nothing below rank 3 is evaluated** — the projection
is never called, so there is no field set to get wrong.

The open question this example exposes: that veto currently lives in a module
with one caller. The same viewer hitting the blocking user's *reel*, *comment*,
or *listing* takes a different code path. §O8; Phase 2.

### Example F — Anonymous visitor opens `/pulse/marketplace/163`

| Dimension | Answer |
| --- | --- |
| B6 commerce publicness | ✓ — `status='published'`, approved |
| B1 | ✓ — seller active, not blocked (anonymous cannot be blocked) |
| Audience | `ANONYMOUS` |
| Projection | title, description, price, currency, media, category, delivery terms, refund policy, seller display name + logo + country + verification, variant options, `stock_state`, SKU |
| **Denied** | `supplier_cost_cents`, `cost_cents`, `provider_*`, `external_sku`, `safety_score`, `safety_flags_json`, `moderation_reason`, `reviewed_by`, `seller_notes`, `listing_metadata_json`, exact `stock_quantity`, seller `phone`, seller `risk_score` |
| B4/B5 | `index,follow` — `/pulse/marketplace` is deliberately absent from `_RULES` and falls through |

**Today:** verified clean on production. This example is the control case —
the model agrees with production, which is what makes the two defects legible as
defects rather than as the model being stricter than the product.

### Example G — The seller of listing 163 opens their own listing

Audience `SELLER`. Everything in F, **plus** `cost_cents`,
`supplier_cost_cents`, exact `stock_quantity`, `moderation_reason`,
`seller_notes`, `fulfillment_mode`, sync state as a status — **and still not**
`safety_score` (FRAUD/RISK, §P6), `reviewed_by` (ADMIN/OPS, §P9), or the
provider's raw id space.

Note that `SELLER` is **not** a rung above `AUTHENTICATED` on a ladder — it is
orthogonal. A seller viewing *someone else's* listing is `ANONYMOUS`+auth, not
`SELLER`. The audience is resolved per (viewer, entity) pair, which is why
`resolve_audience` takes the entity.

### Example H — A seller whose personal profile is `private` has a published store

Two entities, two dimensions, two answers, and they do not interact:

- Personal profile: B1 says friends-only. An anonymous visitor gets nothing.
- Storefront and listings: B6 says published. An anonymous visitor gets the full
  commerce projection of F.

The store does not unpublish, and the profile does not open. §K.

The trap this example exists to catch: a "simplifying" implementation that
resolves commerce audience by first resolving the seller's personal audience. It
produces a storefront that vanishes when its owner tightens an unrelated social
setting — a silent revenue bug that no privacy test would catch, because
privacy tests check for *too much* exposure.

### Example I — A creator with a public profile, a popular post, and no indexing consent

| Dimension | Answer |
| --- | --- |
| B1 access | ✓ public |
| B2 discovery | ✓ — appears in on-platform creator search |
| B3 public web | ✓ — post page is anonymously fetchable |
| B4 external search | **✗ — no consent** |
| B5 directive | `noindex,follow`; absent from every sitemap |

**This is the §5 case stated as a single row: publicly fetchable and noindex, at
the same time, with neither derived from the other.** It is also §21: being
public (B1) and discoverable (B2) is explicitly *not* consent to B4.

The containment §I2 requires holds: accessible ⊋ discoverable is not exercised
here, but discoverable ⊋ indexable is, and it is the containment a growth
argument will attack.

**What the model refuses.** Asked "can we index this creator because their
profile is public and engagement is high," the model has no input that accepts
engagement and no path from B1 or B2 to B4. The only way to a yes is
`search_indexing_consent` (§J), which does not exist and is not being built in
this phase.

---

## X. IMPLEMENTATION PHASES (§49)

Seven phases. Each is independently shippable, independently revertible, and
leaves the tree green alone — required, because `main` takes ~60 commits/day
from parallel sessions and cannot be frozen (§U3). No phase after 0 begins
without owner approval of this document.

### Phase 0 — Stop the active leaks *(hours, no new architecture)* — ✅ DONE

- ~~Deploy the `/pulse/post/<id>` byline fix from `44e37e4a9`. One string.~~
  Shipped as `d8fc3054b`, re-implemented off `main` (§0.6).
- ~~Strip `email` and `full_name` from `pulse_native_profile_payload` for
  non-self viewers. Four lines + a test.~~ Shipped as `0b2c13e66`.
- **Do not** generalise. This phase deliberately builds nothing. *Held:* the
  functional diff is five lines, and `pulse_mobile_user_payload` was not
  touched — a test pins it so a later tidy-up cannot move the strip to the
  shared builder and sign the owner out of their own settings screen.

*Exit:* **met.** `/pulse/post/2516` serves no risk score (verified anonymous,
Googlebot UA, before and after); `tests/test_privacy_p1_exposure.py` asserts no
PII in an other-user profile payload, and both halves were mutation-checked.
*Revert:* `git revert`, two commits — still true, they were kept separate for
exactly that reason.

Phase 1 is the first phase that needs owner approval.

### Phase 1 — Classification as data, enforced but inert *(1–2 days)*

- `services/exposure/classification.py` — §F as a literal map. Covers the 12
  entities, no behaviour change.
- V1 completeness gate: every column of every mapped table is classified. Reads
  the schema from an `init_db()`-built database.
- Shared status vocabulary, resolving the `discovery_visibility` /
  `profile_viewer_permissions` divergence (§N1).

*Exit:* the map exists, CI fails on an unclassified column, **zero runtime
behaviour changed.** *Revert:* delete two files.

### Phase 2 — Audience resolution, and the block check made shared *(2–3 days)*

- `services/exposure/audience.py`. Reuses `profile_viewer_permissions` for
  relationship inputs rather than reimplementing them.
- **Make the block veto shared** before anything else is shared (§O8). This is
  the highest-value user-facing fix in the whole plan.
- Fix defect 0.3 here: wire `_can_message` to the `message_requests` setting
  that actually exists.

*Exit:* one block check, called from every surface that renders another user's
content; the message-privacy preference works. *Revert:* the shared check is
additive; surfaces keep their existing inline checks until Phase 5.

### Phase 3 — `project()`, adopted by one serializer *(2 days)*

- `services/exposure/project.py`, pure, no queries.
- Adopt in **one** serializer — `_public_post`, because it already has the
  sentinel suite and the mutation harness.
- V2/V3/V4/V5 for that one entity. Mutations W1, W2, W8, W10, W11, W12.

*Exit:* one serializer's output is provably the map's output. *Revert:* one
function.

### Phase 4 — The gates: static boundary, containment, new-field safety *(2–3 days)*

- The AST-based CI gate of §W2.
- V6 containment and V7 precedence tests.
- V8, the `new_risk_metric = 812739` test.
- V9 terminal-boundary test, generalised from the one that already exists.

*Exit:* a new column cannot become public, and a projection result cannot be
read by business logic, without CI going red.

### Phase 5 — Adopt `project()` across the remaining serializers *(iterative, weeks)*

One serializer per PR, in risk order: profile → listing → variant → seller →
storefront → order → comment → reel → notification → message. Re-run the §M
dependency audit per serializer *as* it adopts, because adoption is what makes
the violations visible.

The static gate reports the count of unadopted serializers. **That count
reaching zero is the definition of done.**

*Revert:* per serializer, always.

### Phase 6 — Close the directive and cache gaps *(2–3 days)*

- `after_request` default for robots directives, so a new public route is
  classified rather than indexed by default (§Q5).
- `X-Robots-Tag` for non-HTML responses (§Q6).
- `Cache-Control: private` for any audience above `ANONYMOUS`; stop relying on
  `Vary: Cookie` (§R3a).
- Move `search_visibility`'s five shared gates onto the shared predicate (§N2).

### Phase 7 — External search consent for human creators *(separate approval)*

**Not authorised by this document.** Requires its own decision. Order within it
is fixed: projection correctness (Phase 5 for the user entity) → consent UI →
revocation with the three actions of §J3. Shipping consent before the projection
exports the first opting-in creator's email to Google.

### What is explicitly *not* in any phase

Per §44: no `index_in_search` column, no profile-route changes beyond Phase 0's
PII strip, no opening of creator profiles, no `users` migration, no global
serializer rewrite, no sitemap membership change, and no rewrite of
`search_visibility.py` or `discovery_visibility.py`.

---

## DECISION TABLE (§48)

| Component | Current owner | Proposed owner | Keep / Modify / Replace | Migration risk | Client impact | Security impact |
| --- | --- | --- | --- | --- | --- | --- |
| Route authentication | `services/route_auth.py` | unchanged | **Keep** | none | none | positive — already default-deny + CI-gated |
| Login wall | `pulse_social_shell` (bot.py:50820) | unchanged | **Keep** | none | none | neutral |
| Profile access gate | inline in `api_pulse_public_profile` | `services/exposure/audience.py` (Phase 2) | **Modify** | low — additive | none | positive |
| Block enforcement | `profile_viewer_permissions`, **1 caller** | shared, every surface | **Modify** | medium — many call sites | none | **high positive** — §O8 |
| Message-privacy preference | `profile_viewer_permissions._can_message`, reads a nonexistent column | same module, wired to `message_requests` | **Modify** | low | none | positive — the control currently does nothing |
| Field exposure | each serializer, ad hoc | `services/exposure/project.py` | **Replace** | **high** — the core change; mitigated by one-serializer-per-PR | low with §U2's narrow-don't-remove rule | **high positive** |
| `pulse_mobile_user_payload` | bot.py:8191, shared by owner + other-user paths | owner audience only | **Keep** — the strip landed one level up, in `pulse_native_profile_payload`, and a test pins this builder unchanged | low | **none verified** — no screen reads other-user `email` | **closed** (defect 0.2, `0b2c13e66`) |
| `/pulse/post/<id>` byline | bot.py template | unchanged shape, two fields removed | **Modified** | none | none (HTML) | **closed** (defect 0.1, `d8fc3054b`) |
| Field classification | does not exist | `services/exposure/classification.py` | **New** | low — inert in Phase 1 | none | positive |
| `services/discovery_visibility.py` | itself | itself | **Keep** (one import: shared status vocabulary) | very low | none | neutral |
| `services/search_visibility.py` | itself | itself | **Keep + Modify** (~40 lines: five gates → shared predicate) | low | none | neutral |
| `content_eligibility` precedence chain | `search_visibility` | shared predicate, still called from here | **Modify** | low | none | positive — one evaluation instead of three |
| Robots directive emission | ~15 per-template calls | `after_request` default | **Modify** | medium — could change directives on pages nobody audited | none | positive — closes the default-indexable gap |
| `X-Robots-Tag` / non-HTML | does not exist | same hook | **New** | low | none | positive |
| Cache headers | `public, max-age=300` + `Vary: Cookie` | `private` above `ANONYMOUS` | **Modify** | low (hit rate is already ~0) | none | positive — removes an accidental-safety dependency |
| Sitemap membership | `sitemap_eligible` | unchanged | **Keep** | none | none | neutral |
| JSON blobs (8 columns) | projected ad hoc | never projected | **Replace** | medium — some surface needs a named field instead | possible, per-surface | positive — closes the main future leak path |
| External search consent | does not exist | `users.search_indexing_consent` | **New, Phase 7, separate approval** | deferred | none | positive |
| Creator search opt-out | `content_eligibility`, no writer | unchanged, kept as the emergency brake | **Keep** | none | none | neutral |
| `user_trust_profiles` scores | unclassified | FRAUD/RISK, denied to all incl. owner | **Modify** | low | none | positive |
| Logging classification | none | §T2 table | **New** | low | none | positive |
| Static privacy gate | text-matching, one case | AST + schema | **Replace** | low | none | positive — §W2 |

---

## PRESERVED WORK (§45)

Branch **`claude/seo-phase1-defects`**, not an ancestor of `origin/main`.
Nothing pushed, nothing deployed.

| SHA | Parent | Subject |
| --- | --- | --- |
| `b548452a7` | — | Phase 1: PulseDrop promos out of the sitemap |
| `44e37e4a9` | `b548452a7` | Stop publishing internal risk assessments to the people they judge — **contains the defect-0.1 fix** |
| `b1b1e3b26` | `44e37e4a9` | Give a store an address that survives being renamed |
| **`7a56d7f70`** | **`b1b1e3b26`** | **Stop telling every viewer what we privately think of a post** |

Should a later architecture supersede any of it, the **behaviour and the
regression tests must be preserved semantically** — specifically: `risk_score`,
`moderation_status` and `sentiment` absent from `_public_post`; the realtime
broadcast gate reading the envelope; the `scam_warnings` rail carrying no score;
and the byline carrying no status or risk.

**Partially superseded, 2026-10-03.** The last of those — the byline — shipped
independently in PR #130 (§0.6), re-implemented off `main` rather than merged
from here, because `44e37e4a9` bundles a marketplace change that belongs to a
different review. The branch is otherwise untouched and still holds the only
copy of the other three commits. Two consequences for whoever picks it up:

- `44e37e4a9` will conflict on the byline hunk. Take `main`'s version; it is
  the same edit.
- `7a56d7f70` needs the `bot.py:44569` feed-JS string removed in the same
  commit, or the Scam Shield card starts rendering "Risk score 0". See §0.6.
