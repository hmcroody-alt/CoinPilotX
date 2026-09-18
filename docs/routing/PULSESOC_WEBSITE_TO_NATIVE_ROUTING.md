# PulseSoc website-to-native routing

One system decides which pulsesoc.com actions stay on the web and which open the
iPhone app. This document is the map of it: where the decision lives, what it
decides, what it deliberately does not do yet, and what has to be true before the
next phase can ship.

The authority is `services/app_links.py`. Nothing else may hand-write an App
Store URL, a `pulsesoc://` URL, or an app-intent marker.

## The contract

- App installed, iOS → the exact native destination, via the existing Universal
  Link. iOS matches the association before Flask ever sees the request.
- App absent, iOS → the App Store listing, `id6777591572`.
- Desktop or Android → the finished web page when one exists; otherwise a page
  that names the destination and offers the listing plus a QR code.
- The destination survives auth and installation. Nothing is dropped at a login
  boundary.
- No one is ever sent to a blank, unfinished, or unrelated screen.

## How a link is marked

`/pulse/post/123?pulse_app=1&pulse_src=email`

The marker is a query parameter, not a path. That is the whole reason this works
on the binary that is in the App Store today: query strings do not participate in
`apple-app-site-association` path matching, so adding the marker changes nothing
about which URLs iOS claims. The association already claims the canonical paths;
the marker only tells *Flask* what to do on the requests iOS did not claim.

`app_links.app_intent_url()` is the only thing that writes the marker, and
`bot.route_app_intent_links_to_the_app_store` (a `before_request` hook) is the
only thing that reads it.

An app-intent link reaching Flask at all means the app did not claim it, which
means it is not installed or the platform is not iOS. That inference is the
entire mechanism.

## The decision

`app_links.fallback_decision(path, is_ios, is_app_intent)` returns one of:

| Action | When | Result |
| --- | --- | --- |
| `FALLBACK_IGNORE` | unmarked traffic, or a web-intent path | request proceeds untouched |
| `FALLBACK_APP_STORE` | iOS, marked, native destination | 302 to the listing |
| `FALLBACK_WEB` | non-iOS with a real web page; or any unknown destination | the web page |
| `FALLBACK_APP_ONLY` | non-iOS, marked, no finished web surface | the interstitial, HTTP 200 |

Two properties are load-bearing and are asserted in tests:

- **The redirect target is never derived from request input.** The hook takes it
  from `bot.pulsesoc_app_store_url()`. `next=`, `redirect=`, `url=`, `Host:` and
  `X-Forwarded-Host:` cannot move it.
- **Only GET is ever considered.** A marked POST would lose its body to a 302.

### Why desktop does not get redirected to the App Store

It used to. That is a dead end: nobody installs an iPhone app on the Mac they are
sitting at, so the session ended on a page the visitor could do nothing with.
Once the Marketplace family became app-first, it would have become the single
most common desktop outcome on the site.

So desktop gets `templates/app_only_destination.html` instead — a 200 that names
the destination it was asked for, offers the listing, and shows a QR code. The QR
is the affordance that actually moves the destination from the desk to the phone.

## The destination registry

`app_links.DESTINATIONS`. Each entry carries two facts that are checked against
reality rather than trusted:

- `native_supported` — does the **shipped App Store binary** resolve this path?
  Verified against `mobile-native/src/navigation/linking.ts` by
  `tests/test_app_links.py`.
- `web_equivalent` — does pulsesoc.com render this resource? Verified against the
  live `bot.webhook_app.url_map` by `tests/test_app_intent_fallback_router.py`.

`web_equivalent` used to be an annotation and is now a behavioural switch: it
decides whether a desktop visitor gets a page or the interstitial. It had already
drifted while it was only an annotation (`order` and `orders` carried the note
"no web route" long after `/pulse/orders` shipped), which is why it is now gated.

App-first decisions — destinations that have a web route but deliberately do not
use it — are declared in `app_links.APP_FIRST_DESPITE_WEB_ROUTE`. That is the
only permitted way to disagree with the url_map, and each entry states why.

### CTA honesty

`build_app_link()` raises `AppLinkError` rather than emit a link whose label
promises a destination the shipped binary cannot resolve. Collections and Roast
Battle are registered with `native_supported=False` precisely so this rule does
the enforcing: no button naming them can ship by accident, and `/open/collections`
is a 404 rather than a page with a button that opens nothing.

They both have finished web pages, so they are `web_equivalent=True` and web
visitors are simply served the page.

## Marketplace is temporarily app-first

A product decision, not a technical one. The web Marketplace was never designed;
the native one is the real product. Affected destinations:

`marketplace`, `marketplace_create`, `product`, `store`, `seller`, `seller_apply`,
`seller_dashboard`, `orders`, `order`, `purchases`.

All are `web_equivalent=False` with a reason recorded in
`APP_FIRST_DESPITE_WEB_ROUTE`. On iOS an app-intent link goes to the app or the
listing; on desktop it reaches the interstitial.

### Returning Marketplace to web-first

When the web Marketplace is rebuilt, for each destination:

1. Delete its entry from `APP_FIRST_DESPITE_WEB_ROUTE`.
2. Set `web_equivalent=True`.
3. Remove its `display_name` (only the interstitial reads it).
4. Run `tests/test_app_intent_fallback_router.py`. The url_map gate will now
   *require* the route to exist, so a half-built page fails loudly.

No other file needs to change. The decision is one flag per destination.

## `/open/<destination>` is a compatibility alias, not the canonical link

It renders the same interstitial. It is deliberately **not** a Universal Link,
and it is not the primary URL for anything.

Every binary in the App Store today claims a fixed component list that does not
include `/open/...` (`services/native_app_links.py`), so iOS hands these URLs to
Safari no matter who is asking. Both obvious shortcuts are worse than rendering:

- **A 302 to the canonical `/pulse/...` link does not open the app.** iOS does
  not re-evaluate associated domains on a redirect target, and never opens the
  app for a same-domain navigation. It would also punish members who *do* have
  the app, by bouncing them to the App Store.
- **An automatic `pulsesoc://` navigation** shows an OS "cannot open" sheet to
  every visitor without the app — on this page, most of them.

So it renders and the member chooses. On iOS the page offers a user-initiated
`pulsesoc://` button; elsewhere that button is absent, because the scheme opens
nothing on a desktop and reads as a broken control.

`pulsesoc://` is declared in `linking.ts` alongside `https://pulsesoc.com`, so it
routes through the same table as the universal link and needs no new binary. It
is never used for anything shareable: a `pulsesoc://` link in an email is a dead
end for everyone without the app.

## Telemetry

All six emit through `logging.info` with an `app_link_*` prefix, except the
native-open click, which the server cannot observe and which beacons to the
existing `/api/track`.

| Event | Constant | Where |
| --- | --- | --- |
| destination requested | `app_link_open_requested` | `/open/...` |
| native open selected | `app_link_native_open_selected` | beacon from the interstitial |
| App Store fallback | `app_link_fallback` | the `before_request` hook |
| desktop interstitial shown | `app_link_app_only_interstitial` | `render_app_only_destination` |
| unknown destination | `app_link_unknown_destination` | hook and `/open/...` |
| routing failure | `app_link_invalid` | hook and `/open/...` |

The beacon uses `sendBeacon` because the `pulsesoc://` navigation tears the page
down immediately and a `fetch` would usually be cancelled. It never calls
`preventDefault`, so it cannot gate the navigation it is measuring.

## Security

- No redirect target is ever built from request input.
- `app_store_url()` ignores a `PULSESOC_APP_STORE_URL` override that is not on
  `apps.apple.com`.
- `app_scheme_url()` resolves against the registry before the value reaches an
  `href`, so a scheme URL cannot be assembled from an arbitrary path — that is
  how `javascript:` or another app's scheme would otherwise get into the page.
- The interstitial is `noindex, nofollow` and `Cache-Control: no-store`. It is a
  handoff; the resource it names is indexed at its own canonical URL.
- `/open/...` is a `@public_route` on purpose. It reads no member data, and the
  people who reach it are the ones without the app — bouncing them to `/login`
  is how the destination gets lost.

## Known limitations

1. **iPadOS Safari sends a desktop UA by default**, so `is_ios_user_agent` treats
   an iPad as desktop and it gets the interstitial. Pinned in
   `tests/test_app_intent_fallback_router.py` so the gap stays visible.
2. **`undx` (`/pulse/ai`) and `private_office` (`/pulse/private-office`) are
   classified `web_equivalent=False` although both render a web page.** These
   predate the app-first decision. `True` is arguably the truthful value, but
   changing it alters two subsystems this work did not otherwise touch, so it is
   recorded here as an open question rather than changed quietly. They sit in
   `APP_FIRST_DESPITE_WEB_ROUTE` with that reason.
3. **Unmarked traffic still reaches the web Marketplace.** The hook only fires on
   `?pulse_app=1`. Intercepting ordinary browsing would remove a working surface
   from signed-in members and would touch the Stripe Connect return URL
   (`/pulse/merchant/payouts`), so it is out of scope here and needs its own
   decision.
4. **The new server behaviour has not been observed on a physical iPhone against
   the App Store binary**, because it is not deployed. What is verified is the
   binary's route table (asserted against `linking.ts`), the server's decision
   for every UA (route-level tests), and the rendered desktop page (screenshot).
   Device confirmation is a post-deploy step.

## Phase 2 — making `/open/*` the canonical Universal Link family (Option B)

Not started. `/pulse/...?pulse_app=1` remains the production authority until
every one of these is true, in order:

1. **Native routes exist.** `linking.ts` declares `open/:destination` and
   `open/:destination/:resourceId`, mapping to the same screens the canonical
   paths reach. Landing in the app on an unresolvable universal link does not
   fall back to Safari — it leaves the member on whatever screen was showing — so
   an incomplete route table is worse than no claim at all.
2. **AASA updated.** `services/native_app_links.py` adds `/open/*` to
   `APPLE_LINK_COMPONENTS`. Ordering matters: an `exclude: true` component must
   sit *above* the pattern it carves out, and `*` matches across `/`.
3. **A binary declaring both has been released to the App Store** and the
   association file it fetches is the updated one. Not TestFlight — the claim has
   to be true for the public binary.
4. **Adoption measured.** `app_link_open_requested` against install base tells us
   what share of traffic is on a binary that can honour `/open/*`. Until that is
   high, `/open/*` as a canonical link is a downgrade for the remainder.
5. **Only then** may callers start minting `/open/...` links.

Throughout and afterwards, existing `/pulse/...?pulse_app=1` links keep working.
They are in emails, messages and posts that we do not control and cannot recall.
There is no cutover in which they stop being honoured.

The `native_supported` flag is what gates step 5 per destination: it is the
deliberate act that turns a destination on, and it is only correct once the route
exists in a **released** binary.

## Test evidence

| Suite | Covers |
| --- | --- |
| `tests/test_app_links.py` | the registry, CTA honesty, `linking.ts` agreement |
| `tests/test_app_intent_fallback_router.py` | the hook, the interstitial, url_map agreement, security |
| `tests/test_open_destination_interstitial.py` | `/open/...`, the scheme button, the beacon |
| `tests/test_share_link_app_intent.py` | share links carry the marker |
| `tests/test_resource_page_app_ctas.py` | resource pages emit honest CTAs |
| `tests/web_parity/test_aasa_claims.py` | the association file's claims |
| `tests/protection/test_route_auth.py` | `/open/...` satisfies the default-deny auth gate |
