# PulseSoc App Link Foundation Map

How a PulseSoc link decides between the native app, the App Store, and the
website — what exists, what changed, and what is still blocked.

> **Production status: iOS Universal Links are live and verified on device.**
> `PULSESOC_APPLE_TEAM_ID` was set on Railway on 2026-09-07; the AASA returns
> HTTP 200, Apple's crawler has refetched it, and a tapped link opens the app on
> the post it names. Existing installs keep the old cached association until they
> are reinstalled. `assetlinks.json` still returns 503 by design.
> See [Association file status](#6-association-file-status).

---

## 1. The contract

```
APP-INTENT LINK  →  is PulseSoc installed?
                      YES →  open the native app at the EXACT destination named
                      NO  →  open the official production App Store listing

The normal PulseSoc website is NOT the fallback for an app-intent link.
```

Two things follow from this that are easy to get wrong:

- **A link that opens the app but lands on Home is a failure, not a partial
  success.** A button saying "Open this post" must open *that post*.
- **Not every link should be an app-intent link.** Legal pages, billing,
  password reset and the browser-based livestream studio are web workflows.
  Sending those to the App Store would be a regression.

---

## 2. The single authority

`services/app_links.py` is the one link builder. There is deliberately no
second one.

| Concern | Where it lives |
| --- | --- |
| Canonical origin | `CANONICAL_APP_ORIGIN` |
| App-intent marker | `APP_INTENT_PARAM` (`pulse_app=1`) |
| Attribution source | `APP_SOURCE_PARAM` (`pulse_src=...`) |
| Destination registry | `DESTINATIONS` — 28 entries |
| Web-only classification | `WEB_INTENT_PREFIXES`, `WEB_INTENT_PATHS` |
| App Store listing | `app_store_url()` |

### Why the marker is a query parameter

The shipped iOS binary matches routes on **path only**. A query parameter is
therefore invisible to it — a marked link opens exactly the same screen an
unmarked one would. A path-based marker (`/app/pulse/post/1`) would have
required a new build, which this work was explicitly not allowed to produce.

### Valid sources

`email`, `push`, `sms`, `share`, `invite`, `qr`, `web`, `system`.

Anything else normalizes to `system` rather than being echoed back — the
parameter is reflected into pages, so accepting arbitrary input would be an
injection surface.

### App-only destinations

`event`, `notification`, `order`, `orders`, `private_office`, `product`, `undx`
are marked `web_equivalent=False`. There is no per-resource web page for these,
so they get a list/feed page as their web fallback and **no contextual CTA** —
there is no page on which to put one.

---

## 3. Website placement inventory (§3)

"Add, do not convert." Every existing web navigation control on these pages
still works and is pinned by a test.

| Surface | Renderer | Placement decision | Destination | Status | Test evidence |
| --- | --- | --- | --- | --- | --- |
| Homepage `/` | `templates/index.html` | **Added** one CTA + App Store badge in the `final-cta` band. Existing "Open PulseSoc" / "Explore PulseSoc" buttons deliberately left as web links. | `home` | Done | `test_website_app_link_ctas.py` |
| Post page `/pulse/post/<id>` | inline f-string | **Added** beside "Back to PulseSoc" / "My Posts", which stay. | `post` (that post) | Done | `test_resource_page_app_ctas.py` |
| Reel page `/pulse/reels/<id>` | inline f-string | **Added** beside "More Reels". | `reel` (that reel) | Done | `test_resource_page_app_ctas.py` |
| Profile page `/pulse/profile/<id>` | inline f-string | **Added** to the profile action grid (`grid-column:1/-1` so it does not orphan a column). | `profile` | Done | `test_resource_page_app_ctas.py` |
| Group page `/pulse/groups/<slug>` | inline f-string | **Added** to `.group-community-actions`, which already sizes `.button` children. | `group` (that group) | Done | `test_resource_page_app_ctas.py` |
| Event page | — | **None.** No per-event web page exists; `/pulse/events` is a gateway list. | `event` is app-only | N/A | registry assertion |
| Marketplace listing page | — | **None.** No per-listing web page exists; `/pulse/marketplace` is a browse gallery. | `product` is app-only | N/A | registry assertion |
| Global header / mobile nav / global footer | shared shell | **Deliberately none.** §3 forbids a banner repeated down the scroll; a site-wide header CTA is exactly that. The homepage band is the one designated location. | — | Won't do | — |
| Legal, support, pricing, login, password reset | various | **Deliberately none.** Classified web-intent; these are the workflows §2 protects. | — | Won't do | `test_notification_system_links.py` |
| `/dashboard/*`, `/pulse/live/studio` | various | **Deliberately none.** Browser-designed analytics and broadcasting surfaces with no native equivalent. Classified web-intent so link handling leaves them alone. | — | Won't do | `test_notification_system_links.py` |

### Accessibility

The CTA is a real `<a>` with an href — keyboard reachable, focusable, and
readable by a screen reader without a JS handler. It reuses the existing
design-system `.button` class, so it inherits the site's focus ring and touch
target. It is not hover-revealed, not absolutely positioned, and emits no `id`,
so it cannot collide with page markup. All four resource pages are asserted
free of duplicate element ids.

---

## 4. Delivery channels

| Channel | Before | After |
| --- | --- | --- |
| Email | Website URLs | Canonical app-intent links, `pulse_src=email` |
| SMS | **Bare relative path** — members received the literal text `/pulse/post/123`, which is not a link at all. Whole message truncated to 480 chars, so a long preview ate the end of the URL. | Absolute app-intent link, `pulse_src=sms`. Truncation now spends the budget on the preview and never on the URL. |
| Push | Relative path in the payload | **Unchanged, deliberately.** The shipped app parses the payload string itself (`notificationRouting.ts`). Converting push to absolute marked URLs would require a new binary. |
| Reel share | `request.host_url` + plain web path | Canonical builder, `pulse_src=share` |
| Group invite | `request.host_url` + plain web path | Canonical builder, `pulse_src=invite` |
| Referral `/r/<code>` | Already correct | Unchanged. Already redirects iOS visitors to the App Store with deferred attribution. |

### Dead destinations found and fixed

Three notification builders pointed at paths that are not routes:

| Was | Is | Why it mattered |
| --- | --- | --- |
| `/dashboard/security` | `/account/security` | A **security alert** — the one notification a member must act on immediately — opened a 404. |
| `/pulse/dashboard/creator` | `/dashboard/creator` | Creator payout notifications landed nowhere. |
| `/pulse/marketplace/orders` | order destination | (fixed earlier in this work) |

The test that guards this reads the `deep_link=` literals out of the module
with `ast` rather than restating them, so the *next* dead route fails the suite
instead of waiting for someone to add it to a hand-written list.

---

## 5. Safety properties

- **No open redirect.** `app_store_url()` ignores a `PULSESOC_APP_STORE_URL`
  override that is not an `apps.apple.com` URL. This value is used as a redirect
  target, so trusting arbitrary input would turn every app-intent link into an
  open redirect.
- **No request-derived links.** Shared links are built from the canonical
  origin, never `request.host_url`. Previously the URL a member handed a friend
  inherited whatever `Host` header the sharing request carried.
- **Hostile input falls back safely.** `javascript:`, `data:`, protocol-relative
  `//evil.example.com`, absolute off-origin URLs, and `/api/` or `/admin` paths
  all resolve to `/pulse/notifications` rather than being resurrected.
- **No path traversal.** `../` in a resource id is rejected by the builder.
- **Failures cost the button, not the page.** On a resource page an unbuildable
  link renders nothing rather than 500-ing a page someone is trying to read.

### One caveat worth stating plainly

`request.host_url` independence is *not* asserted by sending a spoofed `Host`
through the test client, and the test file says why: changing `Host` moves the
cookie domain so the endpoint 401s first, and `X-Forwarded-Host` proves nothing
because this app does not install `ProxyFix`. It is pinned at the builder
instead, which needs no request context at all.

---

## 6. Association file status

### iOS — serving

`PULSESOC_APPLE_TEAM_ID=87ZC69AGSR` was set on the Railway `CoinPilotX`
production service on 2026-09-07 and the service redeployed:

```
GET https://pulsesoc.com/.well-known/apple-app-site-association
  → HTTP 200  application/json
      87ZC69AGSR.com.pulsesoc.app             /pulse/*  /search*
      87ZC69AGSR.com.pulsesoc.nativeapp.dev   /pulse/*  /search*
```

An earlier revision of this document said neither value could be derived from
the repository. That was wrong about the Apple half: the Team ID appears in
`mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj` as both `DevelopmentTeam`
and `DEVELOPMENT_TEAM`, and independently in a signed build log as
`AppIdentifierPrefix`.

The dev bundle `com.pulsesoc.nativeapp.dev` is published alongside the
production one because `PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS` is unset and the
builder's default list carries both. That is the intended state — it gives
development builds working universal links for device testing. Setting the
variable to `com.pulsesoc.app` would ship production-only.

### Verified end to end, 2026-09-07

After a reinstall on the physical iPhone P3r7or (build 22, `com.pulsesoc.app`),
Apple's crawler refetched the file — `AASA-Bot/1.0.0 → 200` in the Railway
access log — and a tapped `https://pulsesoc.com/pulse/post/2330` opened the app
**on that post**. Not the feed, not Safari. That is the §1 contract, whole.

Three things to know before re-testing this, each of which produces a
convincing false negative:

- **iOS honours a universal link only from a *tapped* link in another app.**
  Typing the URL into Safari's address bar never routes to the app, by design.
- **Existing installs keep whatever Apple's CDN last handed them.** A device
  that saw the 503 goes on opening the website until the app is reinstalled on
  it. Fixing the server does not retroactively fix installed apps.
- **The iOS Simulator cannot test this at all here.** The simulator `.app` has
  to be ad-hoc re-signed to work around the unsigned Agora framework, and that
  strips its entitlements — leaving no `associated-domains` and no team
  identifier. It will open Safari no matter how correct the server is.

### Android — 503, deliberately

```
GET https://pulsesoc.com/.well-known/assetlinks.json
  → HTTP 503  native_link_configuration_missing
    "PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS must contain a valid
     SHA-256 certificate fingerprint."
```

This blocks nothing. `eas.json` declares no Android build profile at all, so no
Android binary has been produced — there is no installed app for the file to
associate. `app.json` does declare the package and `autoVerify` intent filters,
but those describe an app that does not yet ship.

The only keystore in the repo is `mobile-native/android/app/debug.keystore`, and
`build.gradle` signs the release variant with `signingConfigs.debug`. The
Android debug key is a publicly known shared key, so publishing its fingerprint
here would let any party sign an app that claims this domain. A 503 is strictly
safer than a fingerprint anyone can reproduce.

When there is a real Android release, the fingerprint comes from
`eas credentials` or the Play Console's App Signing page and goes into
`PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS`. It cannot be derived from this
repository.

---

## 7. Known gaps, deliberately not changed

- **Invite / "Share PulseSoc" actions in `services/pulsesoc_intelligence_engine.py`
  hand out the *App Store* URL.** An Android recipient of a shared invite gets
  an Apple listing. Fixing this needs a platform-aware download page, which is a
  product decision rather than a link-plumbing one.
- **~73 hardcoded `https://pulsesoc.com` strings remain in `bot.py`.** They are
  all the correct origin, so this is duplication rather than a defect. Sweeping
  them is a separate refactor with real regression surface and no effect on the
  app-link contract.
- **Arena share links** (`/arena/player/<id>`) stay web links. Arena is not a
  registered app destination — the app has no screen to open.
- **The reel share client fallback** (`bot.py`, inline JS) still computes
  `location.origin + '/pulse/reels/' + id` if the API response lacks
  `share_url`. The server now always returns one, so this is a dead defensive
  path, but it lives inside a large minified JS blob where an edit carries more
  risk than the stale fallback does.
