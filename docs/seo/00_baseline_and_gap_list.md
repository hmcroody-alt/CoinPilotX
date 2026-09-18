# Baseline and prioritized gap list

Measured 2026-09-18 against production (`https://pulsesoc.com`, commit `469eea07`).
Nothing below is inferred from source alone; every number has a stated method.

This document is the gate both briefs ask for: the SEO operation's Stage 0 evidence
baseline, and the growth foundation's §1 "prioritized gap list before implementation".
It is written before any behaviour changes so that the after-state can be compared
against something that was not chosen to flatter it.

---

## How each number was obtained

| Evidence | Method |
|---|---|
| Live page markup | `GET` against production, parsed quote-agnostically |
| Sitemap contents | `GET /sitemap.xml`, 354 `<loc>` entries |
| HTTP status per URL | one request per sitemap URL, no concurrency, redirects not followed |
| Template similarity | `difflib.SequenceMatcher` on rendered HTML, with a negative control |
| Search Console | property `sc-domain:pulsesoc.com`, `resource_id` re-verified after each navigation |
| Core Web Vitals | **not measured** — see D4 |

A note on one measurement that went wrong first: an early pass reported 189 pages
missing `rel=canonical` and 190 missing a robots meta. That was false. `bot.py`
emits inline HTML with single-quoted attributes, and the probe regex matched only
double quotes. The corrected counts are 1 and 6. The lesson is recorded here rather
than quietly dropped, because the same regex shape will be reached for again.

---

## What already exists and must not be rebuilt

The briefs both warn against creating competing systems. These are the incumbents:

- `services/app_links.py` / `services/native_app_links.py` — App Store URL, deep
  links, `pulse_app=1`, AASA generation. Every app link must come from here.
- `services/app_promotion.py` + `static/js/pulse_app_promotion.js` — promotion
  surfaces and the one-surface-at-a-time frequency policy. Shipped in `469eea07`.
- `/api/track` and `window.coinPilotXTrack` — the analytics path. No second one.
- `seo/content.py`, `seo/schema.py`, `services/seo_engine.py` — the existing SEO
  layer. Two of the three are partly dead (see B1); the fix is to reconcile, not
  to add a fourth.
- `pulse_post_page()` already gates its own robots directive on visibility and
  moderation. The central policy must agree with it, not contradict it.

---

## The gap list

Priority is by expected harm, not by effort. P0 means Google is currently being
given something false or broken; P1 means a real opportunity is closed; P2 means
quality and durability work; P3 is deferred with a reason.

### P0 — we are actively telling Google something untrue

**A1. 16 sitemap URLs return HTTP 500.**
`/pulse/post/{1378,1382,1499,1505,1661,1720,1731,1732,1781,1782,1811,2057,2058,2080,2081,2409}`.
A sitemap is a recommendation; recommending a 500 sixteen times is a crawl-quality
signal against the whole host, and it wastes crawl budget the real pages need.
*Fix:* every sitemap entry passes one eligibility gate that verifies the record
still renders, not merely that the id was once selected.

**A2. `noindex` URLs are in the sitemap.**
`/signup` ships `noindex,nofollow` *and* sits in `sitemap.xml`. We are asking Google
to crawl a page we told it to ignore. It is hardcoded in
`seo/content.py:all_public_paths()`.
*Fix:* the same gate. A sitemap entry that is `noindex` is a self-contradiction.

> **Correction, 2026-09-18.** This entry originally also named `/day-signal` as
> shipping `noindex`. Re-probing all 76 remaining sitemap URLs as Googlebot showed
> that is wrong: `/day-signal` calls `require_account()` and returns **302** to
> `/signup?next=/day-signal`, so Googlebot has never seen a meta tag on it at all.
> The `noindex` I recorded is on the *signed-in* render, which no crawler reaches.
> The URL still had to leave the sitemap — recommending a URL that cannot be
> fetched anonymously is its own defect — but it is A3's kind of defect, not A2's,
> and the distinction changes the fix: a redirecting page must not be given
> `nofollow`, because three public pages in `seo/content.py` link to it.

**A3. Two sitemap URLs never resolve to themselves.**
`/support` declares `canonical: /help` — the two are decorators on one handler, so
`/support` is not a near-duplicate of `/help`, it *is* `/help`. `/day-signal` 302s
anonymous visitors away entirely (see the A2 correction). Listing either splits or
wastes the signal it was meant to consolidate.

Both were found by probing every URL the sitemap emits rather than by reasoning
about which ones looked suspicious. That is the only method that establishes there
are exactly two: the same sweep confirmed the other 74 return 200 with a
self-referential canonical and no `noindex`.

**A4. `lastmod` is stamped with today's date for all 354 URLs, every day.**
`bot.py:sitemap_xml()` computes `today` once and applies it to everything. A
`lastmod` that always says "today" carries no information, and Google explicitly
discounts the field once it stops correlating with real change.
*Fix:* real timestamps where we have them, field omitted where we do not. An absent
`lastmod` is honest; a fabricated one is not.

**A5. Structured data describes a product that no longer exists.**
`seo/schema.py` still emits `@type: FinanceApplication`, `operatingSystem:
"Telegram, Web, PWA"`, and the legal name **"CoinPlotXAI Inc."** The live product is
a social platform with an iPhone app. The company-name conflict is flagged for owner
confirmation rather than silently rewritten — which legal entity is correct is not
an engineering decision.

**A6. `https://www.pulsesoc.com` returns 200 instead of redirecting to the apex.**
Currently mitigated only by a correct `rel=canonical`, which is a hint. Two hosts
serving identical 200s is a duplicate-host condition.
*Fix:* 301 `www` → apex at the edge of the app, canonical tag retained as a backstop.

### P1 — the app-discovery objective is structurally blocked

**B1. There is no public page about the iPhone app.**
`/app` is taken: `bot.py:12203` binds it to the Pro-gated AI command center, which
302s anonymous visitors to `/signup`. Googlebot is an anonymous visitor, so the most
obviously named app page on the site is, to a crawler, a redirect to a `noindex`
registration form.
*Fix:* branch on auth state — anonymous gets a public landing page, signed-in users
keep the command center exactly as it is. This is not cloaking: Googlebot is treated
identically to any logged-out human, and the distinction is authentication, not
user-agent.

**B2. All eight `/features/*` pages 404.**
The information architecture the brief asks for does not exist yet.

**B3. No sitemap has ever been submitted to Search Console.**
Property `sc-domain:pulsesoc.com` is verified (DNS TXT, 2026-09-18) but shows
**0 sitemaps** and is still "Processing data" with no query or coverage history.
*Sequencing matters here:* submitting today would hand Google the 16 500s and the
108 near-duplicates described below. Repair first, submit second.

**B4. `seo_engine.page_meta()` is dead code for page rendering.**
Its homepage title does not match the title the live homepage serves. It is still
live for `robots.txt` and sitemap generation. A module that is half-dead is worse
than one that is fully dead, because the next engineer will edit the wrong half.

### P1 — content quality is the dominant risk

**C1. 80% of the sitemap is low-differentiation machine output.**

- **176 "Quick Insight" posts** from a system account sharing only **9 distinct
  titles** between them.
- **108 templated pages**: `/markets/<symbol>{,/prediction,/live}` and
  `/country-intelligence/<slug>`.

The templated pages measure **99.2%** and **98.9%** textually identical to their
siblings. That number means nothing on its own, so a negative control was measured:
two genuinely different templates on the same site score **59.6%**. An opcode diff
confirms the only variation between siblings is the substituted name.

Google calls this scaled content abuse. The response is deliberately *not* deletion:
the pages stay reachable for anyone who wants them and stay crawlable for their
outbound links, but they stop asking to be ranked (`noindex,follow`) and they leave
the sitemap. "follow" is load-bearing — dropping it would amputate the crawl paths
through those sections.

**C2. There is no single answer to "may this be indexed?"**
Before this work the answer was spread across **six** places that disagreed:

1. `seo_engine.robots_txt()`'s hand-maintained Disallow list,
2. per-template hard-coded `<meta name=robots>` values,
3. `seo/content.py:all_public_paths()`, which decided the sitemap,
4. route-body redirects, which decided what a crawler actually received,
5. `/api/indexnow`, whose `urlList` was `all_public_paths()` unfiltered — and
   which declared `host: coinpilotx.app` against URLs on `pulsesoc.com`, a
   payload IndexNow rejects outright,
6. `/admin/seo`, whose hardcoded `noindex` list named `/app` and
   `/command-center` (neither is excluded) and omitted a dozen prefixes that are.

The count rose from four to six during implementation: 5 and 6 were found by
grepping for callers of `all_public_paths()` rather than by reading templates,
which is how the first four were found. A2 is a direct consequence of the
disagreement, and 5 was independently broken in a way no amount of SEO work
elsewhere would have surfaced.

*Fix:* `services/search_visibility.py`, with all six surfaces now reading from it.

**C3. Creator search opt-out has no representation anywhere.**
Growth §2 is explicit that public visibility must not silently override an existing
search opt-out. Today there is no opt-out to override — the concept does not exist
in the schema or the render path. The policy module models it now so that the
storage and UI can follow without re-deciding the semantics.

### P2 — quality, durability, measurement

**D1.** Duplicate titles and descriptions across pulse post pages.
**D2.** Internal linking: the eight feature pages and the app page need real,
editorial links from existing pages, not a footer dump.
**D3.** Automated gates, so a future commit cannot silently reintroduce A1–A4.
This is the only item that makes the rest durable.
**D4.** **Core Web Vitals are unmeasured.** PageSpeed Insights returned
quota-exceeded (no API key) for both form factors, and the Search Console field
report is still processing. LCP/INP/CLS are therefore *unknown*, not *passing*. No
performance claim will be made until they are measured.
**D5.** Image and video SEO; Discover readiness. Schema validity, rich-result
eligibility and actual appearance are three different things and will be reported
as three different things.
**D6.** Multi-engine coverage: Bing Webmaster Tools, IndexNow with a bounded queue,
dedup, retries and rate limits. An accepted IndexNow submission is proof of
acceptance and nothing else.

### P3 — deferred, with reasons

| Deferred | Why |
|---|---|
| Embeds (Growth §4) | Own subsystem: oEmbed endpoint, iframe sandboxing, a cross-origin security review. The brief itself permits deferral. |
| Radio / interview / video pages (§5) | Cannot be built without reading audio and livestream code, and both are inside the hard-locked realtime-audio boundary. Page-level work only, scheduled after the policy module lands, with transcripts as drafts. |
| Newsletter (§11) | Requires auditing the existing Brevo integration before deciding whether anything new is warranted. Audit first, build only if the audit says to. |
| Resource center (§6), press center (§7) | Five and N owner-review drafts respectively. Drafts, not published pages — publication needs owner authorization. |
| Creator sharing tools, profile QR (§3) | Depends on the creator-page work, which depends on C2 and C3. Correct order, not a lower value. |
| International (§14) | English first, per the brief. |

---

## What this pass will and will not do

**Will:** A1–A6, B1, B2, B4, C1, C2, C3, D1, D3, and the documentation set.

**Will not, without explicit authorization:** push, deploy, publish, submit to any
third party, send email, purchase anything, or expand DNS. B3 in particular is
*prepared* here and submitted only on the owner's word.

**Cannot yet:** D4, until a PSI key exists or the GSC field report finishes.

---

## The claim this work does and does not make

Correct technical SEO makes a site eligible to be discovered, understood and
ranked. It does not guarantee ranking, indexing, downloads or revenue. Removing
108 near-duplicate pages from the sitemap is a defensible decision about what we
ask Google to rank; it is not a prediction that anything will rank. Where a result
depends on Google's judgement rather than ours, this documentation says so.
