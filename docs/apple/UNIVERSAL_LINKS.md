# Universal Links — verified working, and the two documents that said otherwise

Written 2026-09-19. Capability #7 in `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md`,
and the only one of the fourteen that is **already shipped and working in
production**. So this is not a plan. It is a verification, run end-to-end
against the live site on the date above, plus the correction of two stale
descriptions that the verification turned up.

If you are here to add a URL family, skip to *"Adding a path family"* at the
bottom. Everything above it is evidence that the current state is what it
claims to be.

---

## What was actually measured

One `GET` against production, and the repo's own health check pointed at it:

```
$ curl -s -i https://pulsesoc.com/.well-known/apple-app-site-association
HTTP/2 200
content-type: application/json
cache-control: public, max-age=3600
```

No redirect (`num_redirects=0`), correct content type, 1,703 bytes. All three
matter: iOS fetches this file without following redirects and will reject a
non-JSON content type, and both failures are silent.

```
$ python scripts/web_rebuild/aasa_health.py --url https://pulsesoc.com
claims (in order): /pulse, NOT /pulse/app, NOT /pulse/app/*, /pulse/*,
                   /search*, /dashboard, /dashboard/*, /account/*,
                   /settings/*, /notifications, /saved, /education/*

family            urls  missed  decision   status
account              2       0  CLAIMED    ok
dashboard           10       0  CLAIMED    ok
education            1       0  CLAIMED    ok
help                 1       1  WEB_ONLY   ok
notifications        1       0  CLAIMED    ok
privacy-center       1       1  WEB_ONLY   ok
pulse               93       0  CLAIMED    ok
saved                1       0  CLAIMED    ok
scam-shield          2       2  WEB_ONLY   ok
search               1       0  CLAIMED    ok
security             1       1  WEB_ONLY   ok
settings             1       0  CLAIMED    ok
trust-center         1       1  WEB_ONLY   ok

OK — every declared family is either claimed or deliberately web-only.
```

**Every one of the 110 declared native paths in a CLAIMED family is claimed by
the AASA. Zero missed.** The five `missed` counts are all in WEB_ONLY families,
which is the intended result, not a shortfall.

---

## The two stale claims this corrected

Both said the AASA claims **two** paths. It claims **ten**, plus two excludes.

1. `scripts/web_rebuild/aasa_health.py`'s own docstring, failure mode #2:
   *"The served AASA claims two: `/pulse/*` and `/search*`. Every path in the
   other eleven families is a deep link the app knows how to open and will never
   be handed."* That gap was real when written and has since been closed — the
   component list was widened — but the prose was never updated. **Fixed in the
   same commit as this document.** The script's actual check was always driven
   by its `DECISIONS` table, not by the docstring, so the check itself was never
   wrong; only its explanation was.

2. A session memory note carrying the same two-path claim. Corrected.

The pattern is worth naming because it will recur across this plan: **a
description of a gap outlives the gap.** The health check is executable and
stayed correct; the sentence next to it is not executable and rotted. Where a
fact can be asserted by a script rather than a paragraph, assert it by script.

---

## Why the `exclude` entries sit where they do

This is the one genuinely subtle thing in the file, and
`services/native_app_links.py:46-61` already explains it. Reproducing the
reasoning because it is the part most likely to be broken by a well-meaning
edit:

```
{"/": "/pulse"}                          <- home
{"/": "/pulse/app",    "exclude": True}  <- web client shell
{"/": "/pulse/app/*",  "exclude": True}  <- web client routes
{"/": "/pulse/*"}                        <- everything else native
```

**Order is load-bearing.** iOS evaluates `components` top to bottom and stops at
the first match. If `/pulse/*` were moved above the two excludes, it would match
`/pulse/app` first and the excludes would become dead entries.

**Two exclude entries, not one,** because Apple's `*` matches a run of
characters but the literal `/` preceding it must still be present — so
`/pulse/app/*` does not match `/pulse/app` itself. The same reason `/pulse` and
`/pulse/*` are separate entries.

And the reason to exclude at all is the sharpest failure mode in the whole
mechanism, quoted from that file:

> an unresolvable universal link does not fall back to Safari: the app opens on
> whatever screen was last showing. The user asked for a page and got an
> unrelated screen, with nothing logged anywhere.

That is the cost of claiming a path the app cannot route. It is not a 404, it is
a *wrong screen*, and nothing anywhere records that it happened. This is why the
health check's "claimed but undeclared" direction matters as much as the
"declared but unclaimed" direction.

---

## The host question, resolved

The entitlement claims exactly one host:

```xml
<key>com.apple.developer.associated-domains</key>
<array><string>applinks:pulsesoc.com</string></array>
```

But `www.pulsesoc.com` is also a live host that serves the site, also returns a
200 AASA, and appears in `ALLOWED_APP_LINK_HOSTS` in `services/app_links.py:68`.
That combination looks like a bug — a shared `www` link that can never open the
app — so it was chased down.

**It is not a bug.** `ALLOWED_APP_LINK_HOSTS` governs only what
`build_app_link()` will *accept as input*; the function's return statement is
unconditional:

```python
return f"{CANONICAL_APP_ORIGIN}{normalized}?{urlencode(query)}"
```

Every generated link is rebuilt on the apex origin regardless of the host it came
in on, so `www` is normalized away rather than propagated. The `www` entry exists
so an existing `www` link can be *recognised and rewritten* instead of being
passed through untouched.

The residual exposure is a `www` link a **user** types or shares by hand, which
no code path can normalize. That will open Safari rather than the app. The fix,
if it is ever judged worth one, is one more entitlement string
(`applinks:www.pulsesoc.com`) — which requires a new build, and is therefore a
Wave-2-or-later decision, not a code change. Recorded here rather than actioned.

---

## Two app IDs are claimed, on purpose

The live production AASA claims both:

- `87ZC69AGSR.com.pulsesoc.app` — production
- `87ZC69AGSR.com.pulsesoc.nativeapp.dev` — development

Generated by `services/native_app_links.py:96` from `PULSESOC_APPLE_TEAM_ID`
plus a comma-separated bundle-id list.

This is deliberate and benign: it lets a development build resolve real
production universal links on a test device, which is the only way to exercise
the association at all (see the constraint below). Both entries share one Team
ID, so only this team can sign a binary that claims them. The alternative —
production serving only the production bundle id — would mean universal links
could never be tested before release, which is precisely how this mechanism
breaks silently.

**The 503 path is the real operational risk.** `native_app_links.py:79-81`
returns 503 when `PULSESOC_APPLE_TEAM_ID` is unset or malformed. iOS caches the
last good AASA for a period and then gives up, at which point *every* universal
link in the product degrades to a web page, with no error surfaced anywhere. One
lost Railway variable is sufficient. This is why `aasa_health.py` exists as a
post-deploy probe and not only as a CI check.

---

## How a link actually arrives, and why that matters for #4 and #10

A universal link enters the app through `AppDelegate.swift`:

```swift
application(_:continue:restorationHandler:)
```

which forwards the `NSUserActivity` to `RCTLinkingManager`. It already exists,
it already works, and per `APPLE_NATIVE_ARCHITECTURE.md` it is one of the few
things the app target is allowed to contain — iOS calls it before JS is
guaranteed to be ready.

**Handoff (#10) and Core Spotlight result-opening (#4) arrive through the exact
same method.** Neither needs a new delegate method, a new entitlement, or a new
native module entry point. Each needs an activity *type* and a JS-side handler.
That is the single most useful fact in this document for the rest of the plan,
and it is the reason Universal Links was verified first: it de-risks two later
capabilities by proving the shared entry point is sound.

---

## The same-domain trap

Worth restating because it is counter-intuitive and already cost this repo a
design iteration. From `services/app_links.py`:

> From a page already on pulsesoc.com it does not. iOS treats a same-domain tap
> as ordinary in-site navigation and does not consult associated domains

So a `?pulse_app=1` link works from Messages, Mail, or any other origin — and
does *not* work from a button on pulsesoc.com itself. Worse, the server cannot
distinguish "app not installed" from "same-domain navigation", because both
reach Flask identically; a naive on-site app-open button therefore sends users
who already have the app to an App Store listing for the app in their hand.

That is why on-site buttons route through `open_interstitial_url()` and a
`pulsesoc://` interstitial instead. **A future capability that wants to open the
app from within the site must use the interstitial, not a universal link.**

---

## Adding a path family

1. Add it to `APPLE_LINK_COMPONENTS` in `services/native_app_links.py`, minding
   the ordering rule above — excludes before the wildcard that would swallow
   them.
2. Add it to `DECISIONS` in `scripts/web_rebuild/aasa_health.py` as `CLAIMED` or
   `WEB_ONLY` **with a reason**. An unclassified family fails the check by
   design; that is the mechanism working.
3. Confirm `mobile-native/src/navigation/linking.ts` declares a route for every
   path the family claims. A claimed-but-undeclared path is the wrong-screen
   failure above.
4. Re-run `aasa_health.py --url https://pulsesoc.com` **after deploy**, not only
   in CI. The file is generated from environment variables at request time, so
   CI cannot prove what production will serve.

No new build is required for steps 1–2: the AASA is server-generated, so
claiming a path the shipped binary already routes takes effect without an App
Store release. The converse is not true — a *new* route in `linking.ts` needs a
build.

---

## What was verified, and what was not

**Verified live, this session:** the production AASA returns 200 with
`content-type: application/json` and no redirects; its exact component list
(10 claims, 2 excludes, in the order shown); that both app IDs are present under
Team ID `87ZC69AGSR`; that `www.pulsesoc.com` also serves 200 without redirect;
and that `aasa_health.py` reports every one of 13 declared families correctly
classified with zero missed paths in any CLAIMED family.

**Verified by reading:** the route at `bot.py:128713` and its 503/no-store
branch; `APPLE_LINK_COMPONENTS` and the exclude-ordering comment
(`native_app_links.py:44-71`); the appID construction (`:96`) and Team ID
validation (`:79-81`); `build_app_link`'s unconditional rebuild on
`CANONICAL_APP_ORIGIN` (`app_links.py:1077-1102`); the entitlement's single
`applinks:` host; and `application(_:continue:restorationHandler:)` in
`AppDelegate.swift`.

**Not verified.** No device test was performed. Specifically untested:

- That tapping a claimed link on a physical iPhone opens the app on the correct
  screen. This **cannot** be tested on the simulator — ad-hoc signing strips the
  associated-domains entitlement, so universal links fail there regardless of
  server correctness. It needs the iPhone 16 Pro.
- That iOS's CDN has re-fetched the widened component list. Apple caches
  aggressively and the widening predates this session by an unknown interval, so
  the live file being correct does not prove every installed copy has seen it.
- The `www` gap above is reasoned from the entitlement contents, not
  demonstrated by a failing tap.
