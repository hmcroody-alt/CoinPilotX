# Agent 09 — Image + Video Search Engine

**Scope:** media search eligibility, image/video crawlability, media provenance,
media↔product relationship truth, delivery readiness, poster/thumbnail readiness,
alt-text architecture, media-search diagnostics, media Search Truth.

**Baseline:** `5bdf4e431`, which is `origin/main` and is the commit deployed to
production (confirmed at `/api/service/health`). Every number below is either a
read-only `SELECT` against production Postgres or a response taken off the
production wire. Nothing here is inferred from a filename, a title, an array
position, or a model's guess.

**Status:** audit complete. **No code changed.** This document is the deliverable;
the remediations it names belong to the owners identified in §10.

---

## 0. Two findings that reverse what this mission assumed

The brief carried two suspicions about PulseSoc's images. Both are false, and
saying so is more useful than confirming them would have been.

### 0.1 Product images are not broken, blocked, or expiring

All 43 distinct product image URLs reachable from the live catalogue return
`200`, with real image bytes, an `image/*` content type, `cache-control: public,
max-age=31536000`, zero redirects, and **no `X-Robots-Tag`** — probed as
`Googlebot-Image/1.0`. Both supplier hosts serve `robots.txt` as `Allow: /`.
`pulsesoc.com/robots.txt` disallows no media path and carries no
`Disallow: /static/`. There is no signing and no expiry on product media:
`services/media_storage.py:67-72` concatenates a public base with a key.

Image crawlability is **healthy**. The eligibility gap in this mission is
entirely on the video side and in the *projection* layer (§3, §4).

### 0.2 "Image unavailable" in the page source is not evidence of a broken image

`services/marketplace_storefront.py:368` emits
`<span class="mkt-media-fallback">Image unavailable</span>` **inside every image
box, unconditionally**. `static/css/pulse_marketplace.css:1004` sets it
`display: none`; only `.mkt-media.is-broken` (`:1018`) reveals it, and that class
is applied client-side.

Measured across all 42 sitemapped product pages:

```
total <img>            = 87
.mkt-media boxes       = 87
mkt-media-fallback     = 87
```

87 images, 87 "Image unavailable" strings, and zero actually-broken images. A
grep of the HTML — or a human reading view-source — finds the phrase on a
perfectly healthy page. **Treat a raw-HTML hit on this string as a non-signal.**
A listing with no photograph at all gets different copy (`No photo yet`,
`:351`), and that appears **0** times live.

---

## 1. Where PulseSoc's commerce images actually live

202 marketplace listings. `cover_image_url` is populated on 202 of 202;
`media_url` on **0** of 202.

| Host | Listings | Note |
| --- | --- | --- |
| `cf.cjdropshipping.com` / `oss-cf.cjdropshipping.com` | 194 | CJ Dropshipping, published |
| `cj-product-center.oss-accelerate.aliyuncs.com` | 2 | Aliyun OSS, published |
| `cdn.coinpilotx.app` | 6 | ids 8–13, **never published** |

**The entire live commerce image surface is third-party hosted.** Zero product
images are served from `pulsesoc.com` or from PulseSoc's own CDN. The six
R2-hosted listings are all `seller_deleted` / `review_ready`, were never
published, and point at `chat_media/` keys — a provenance smell worth a look by
whoever owns listing ingestion, but not a search issue since they are not
indexable.

This is a **structural risk, not a defect**: image indexing, Merchant image
requirements, and any future `ImageObject` all depend on hosts PulseSoc does not
control and cannot set headers on. It is recorded here so the fleet plans
against it rather than discovering it later.

### 1.1 There are no galleries

`gallery_json` is populated on 6 of 202 listings, and each of those six arrays
has length exactly **1**. All 42 live product pages emit exactly one JSON-LD
image. PulseSoc has a single-image catalogue. Any plan that assumes multi-image
products — image sitemaps with several `<image:image>` per URL, a gallery
carousel, Merchant additional images — has no data to draw on today.

### 1.2 The media entity model exists and is unused

`marketplace_product_media` is a real and largely adequate model: `media_type`,
`media_url`, `thumbnail_url`, `position`, `is_cover`, `mime_type`, `width`,
`height`, `duration_seconds`, `moderation_status`, `source_media_id`
(`bot.py:126826-126864`).

It holds **8 rows** (product_ids `0`, `0`, `8`–`13`), `width`/`height` are
`NULL` on all 8, and 5 of 8 are `pending_review`. It is vestigial test data, not
the live path — the live path is the single `cover_image_url` string on the
listing.

The useful conclusion: **the schema needed for media search truth mostly already
exists.** It does not need designing, it needs populating. Do not build a second
media table.

### 1.3 Variant→image truth does not exist

`marketplace_listing_variants` holds **3,797 rows across 196 listings** and has
**no image-bearing column of any kind** — verified against
`information_schema.columns` for every `%image%`/`%photo%`/`%thumb%`/`%poster%`/
`%media%` column on every `%variant%` table. It is the only variant table.

So the P0 this mission was told to watch for — *a variant showing the wrong
image* — **cannot currently occur**, because no variant has an image. Listing
163 declares `Color: Chuck air outlet accessories` in `additionalProperty` and
serves one generic photograph. That is the honest behaviour: the page does not
claim a per-colour image it does not have.

**Contract:** variant imagery is *absent*, not *wrong*. Anyone adding per-variant
images must add the mapping to `marketplace_listing_variants` (or
`marketplace_product_media.source_media_id`) and must never derive it from option
position, option label wording, or filename similarity. An unmapped variant must
fall back to the listing cover, never to a guess.

---

## 2. Image delivery: what is sound and what is not

### 2.1 Sound — and a retraction

`media_box()` (`services/marketplace_storefront.py:308-370`) is the single
component every product photo passes through, and it does more than a bare
`<img>`:

* The **wrapper** carries the aspect ratio in CSS — `.mkt-media { aspect-ratio:
  1/1 }`, `.is-wide` 4/3, `.is-tall` 3/4 (`pulse_marketplace.css:905-930`).
* `object-fit: contain`, so nothing is cropped through the subject. This
  catalogue has no focal-point data, so a destructive crop would behead products.
* `loading`/`decoding`/`fetchpriority` are set per position: hero is
  `eager` + `fetchpriority="high"`, everything below the fold is `lazy`.
* `alt` is present on **87 of 87** images and is the product title — factual,
  never invented.

**Retraction:** an earlier pass in this audit recorded "every `<img>` lacks
`width`/`height`, therefore CLS risk." **That was wrong.** All 87 images on all
42 sitemapped pages sit inside a ratio-locked box (87 imgs / 87 boxes, no
orphans), so page geometry is fixed before any image byte arrives. The missing
attributes are not a layout-shift risk on this surface. Recorded as a retraction
rather than quietly dropped, because the false version was plausible and would
have sent someone to fix a non-problem.

### 2.2 Real: no responsive images, and the weight that follows

There is no `srcset` and no `sizes` anywhere, and the in-repo comment at
`:338-344` explains why — there is no image transform service, no resize
endpoint, no Cloudflare Images binding, so a `srcset` of URLs that do not exist
would break every image. The reasoning is correct.

Its premise is now **partly stale**: it says product images "are served from an
R2 bucket behind `cdn.coinpilotx.app`", but §1 shows the live catalogue is 100%
supplier-hosted. The conclusion survives anyway — PulseSoc controls neither host,
so it cannot add a transform to either.

The measured cost, across the 44 distinct image URLs reachable from the 42
sitemapped product pages:

```
total        10.79 MB
mean           251 KB
worst        2.87 MB  at 2000x2000
```

Full-size supplier images ship to every phone, including related-product
thumbnails rendered a few hundred pixels wide. This is a performance finding with
a real remediation (a transform origin in front of a re-hosted copy), and it
belongs in `media_box()` and only there when such an origin exists.

### 2.3 Real, and the likely cause of human "images don't show" reports

`pulse_marketplace.css:993`:

```css
.mkt-media img:not([data-mkt-loaded]) { opacity: 0; }
```

The **only** writer of `data-mkt-loaded` is `settleImage()` at
`static/js/pulse_marketplace.js:43-51`. So if that one script fails to load,
parse, or execute, **every product image on the storefront stays fully
transparent** — while the HTML is valid, the `src` is correct, and the bytes are
fetched and decoded. A healthy catalogue renders as a wall of blank plates.

* **Search impact: none.** Googlebot reads `src` from the HTML; Google Images does
  not require the image to be painted.
* **Human impact: total.** This is the one mechanism found in this audit that
  produces "the images are not showing" while every wire probe returns `200`.
  That contradiction is exactly what the mission was asked to resolve.

The script is live and healthy right now (`200`, 26,546 bytes, at
`?v=storefront-20261002b`), so this is a latent single point of failure, not an
active outage.

**Remediation (not applied here):** invert the default so the image is visible
unless the script marks it otherwise — e.g. gate the fade on an
`html.js`/`no-js` class, or have the script *add* a fade-in class rather than
have CSS hide by default. It is a CSS-only change inside an asset shared with
other storefront surfaces, so it wants its owner's hands, and **it must bump the
`?v=` token** or it ships undeliverable.

### 2.4 MIME truth

Many supplier images declare the non-standard `image/jpg` rather than
`image/jpeg`. The bytes are genuinely JPEG (`ffd8ff…` verified). Both hosts are
third-party, so this is not PulseSoc's header to fix; noted so nobody chases it
as a PulseSoc bug.

### 2.5 "Image unavailable" has two unrelated sources — do not conflate them

| Source | Path | Expiring? |
| --- | --- | --- |
| `bot.py:101808-101810` | messenger/DM media access: `expired`→`media_access_expired` 403, `denied`→403, `not_found`→404; consumed by `mobile-native/src/screens/ChatScreen.tsx:3213-3215` | **Yes** |
| `marketplace_storefront.py:368` | storefront hidden fallback span (§0.2) | No |

The brief's escalation trigger "expiring signed image URLs" is **true for private
messenger media and false for product media**. Private chat media is a
time-bounded access path by design and is outside Agent 9's remit. Product media
has no signing and no expiry. A report of "Image unavailable" must be attributed
to one of these two before it is actioned.

---

## 3. Video: nothing is search-eligible, and a landing page alone will not fix it

### 3.1 Inventory

```
pulse_reels    141   131 active+approved,  10 deleted+approved
pulse_videos   293   all visibility=public, moderation=approved
                     237 active, 56 archived
```

### 3.2 There is no public video surface at all

Probed as Googlebot:

```
302  /pulse/reels     -> /login?next=/pulse/reels
302  /pulse/live      -> /login?next=/pulse/live
302  /pulse/videos    -> /login?next=/pulse/videos
404  /pulse/reel/1    /reels    /reels/1
404  /pulse/video/1   /videos   /video/1
404  /pulse/watch/1   /watch/1
200  /sitemap-live.xml      0 <loc>
200  /sitemap-replays.xml   0 <loc>
404  /sitemap-videos.xml  /sitemap-reels.xml  /sitemap-images.xml
200  /sitemap.xml           6 <loc>   (index)
200  /sitemap-products.xml 42 <loc>
```

Zero `<video:video>` tags and zero `<image:image>` tags exist anywhere on the
site. There is no `VideoObject` and no `ImageObject` JSON-LD. 434 video entities
have no stable, crawlable public URL.

### 3.3 P0-class: Mux stamps `noindex` on every asset it serves

This is the finding that changes the plan. Verified across **4 distinct playback
IDs**, on `thumbnail.jpg`, `animated.gif` and `.m3u8`, across **both**
`image.mux.com` and `stream.mux.com`, under **both** `Googlebot-Image` and a
Chrome UA — and even on Mux's own `404` responses:

```
x-robots-tag: noindex, nofollow
cache-control: max-age=604800
```

It is unconditional platform policy, not a per-asset artifact and not something a
PulseSoc setting changes.

Exposure:

| Column | On Mux (noindex) | On `cdn.coinpilotx.app` | NULL |
| --- | --- | --- | --- |
| `pulse_reels.poster_url` | 64 | 72 | 5 |
| `pulse_videos.thumbnail_url` | 137 | 14 | 142 |
| `pulse_reels.video_url` | 136 (`stream.`) | — | 5 |
| `pulse_videos.playback_url` | 285 (`stream.`) | 8 (`live.coinpilotxai.app`) | 0 |

So **86 of 434** video entities (72 reels + 14 videos) currently have a poster on
a host that permits indexing.

`cdn.coinpilotx.app` is that host, and it is verified good: a real reel poster
returns `200 image/jpeg` with real JPEG bytes under **both** `Googlebot-Image`
and Chrome, with **no** `X-Robots-Tag` and no Cloudflare challenge.

**Contract — the consequence for the whole fleet:** opening a public video
landing page is **necessary but not sufficient**. A page whose `VideoObject`
`thumbnailUrl` points at `image.mux.com` ships a `noindex` thumbnail and will not
earn a video result. Posters must be re-hosted onto `cdn.coinpilotx.app` and the
structured data must reference that copy. Mux stays the playback origin; it
cannot be the *search* origin.

### 3.4 Even with a page, the metadata is not there yet

`VideoObject` wants `name`, `description`, `thumbnailUrl`, `uploadDate`.

```
pulse_videos   duration_seconds NULL   293 / 293
pulse_videos   description empty       237 / 293
pulse_videos   title missing             2 / 293
pulse_videos   width/height present    293 / 293   <- good
pulse_reels    duration_seconds NULL    53 / 141
pulse_reels    caption empty            32 / 141
pulse_reels    poster missing             5 / 141
```

Dimensions are complete, which is the expensive half. Duration is absent
everywhere and description is absent on most videos. These are populate-from-Mux
facts, not authored copy — the ingest already receives them.

**Contract:** a video with no `duration_seconds` and no description must be
*omitted* from a video sitemap, not padded with a generated description or a
guessed duration. Omission is a known-unknown; invention is a false claim.

### 3.5 Deleted reels remain publicly streamable

Of the 10 `status='deleted'` reels, 4 were probed; **4 of 4** returned a live
poster (`200 image/jpeg`, real bytes) *and* a live HLS manifest (`200
application/vnd.apple.mpegurl`), unsigned and unauthenticated. Soft-delete is a
database status change; it does not revoke the Mux asset. Anyone holding a
playback ID — which the app served while the reel was live — can still fetch the
deleted video.

This is **not** a search-index leak: Mux sets `noindex`, no public landing page
exists, and nothing links these IDs. It is a **retention** leak. It is recorded
and escalated rather than fixed, because Mux asset lifecycle is not Agent 9's
surface.

---

## 4. The projection rule

Media is a *projection* of canonical product and video truth. It never creates
truth.

* An image does not establish which variant exists, what something costs, or
  whether it is in stock.
* A poster does not establish that a video is public — `visibility` and
  `moderation_status` do.
* A filename, a pixel colour, an array index and a title word are **not**
  identity. None of them may be used to bind media to a variant.
* When the binding is unknown, emit the listing cover or emit nothing. Never
  emit a guess. **Unknown is better than false.**

---

## 5. Ingestion boundary — reviewed, no finding

`services/business_os/suppliers/normalize.py:248-303` `safe_media_url()` is
genuinely designed, not nominal: https-only; rejects embedded credentials and
`@`; rejects private, loopback, link-local, reserved, multicast and unspecified
addresses including `ipv4_mapped`; rejects dotless hosts and `.local`/
`.internal`. It performs **no DNS resolution**, and says why — a check-then-fetch
would open a TOCTOU window. `media_list()` (`:306-321`) bounds and dedupes, and
deliberately does **not** re-host supplier URLs.

That last point is the mechanical reason §1 is true: supplier URLs are stored
verbatim, so the catalogue is third-party hosted by construction. Any re-hosting
initiative starts here.

---

## 6. Inherited constraints this agent is bound by

From `docs/search_os/AGENT_02_URL_INDEXABILITY.md` (branch
`search-os/agent-02-url-indexability`, `80c057163`) — the only upstream contract
that exists:

1. **`/static/` and `/.well-known/` are `noindex,nofollow` but deliberately
   crawlable** via `_CRAWLABLE_DESPITE_NOINDEX`; a `Disallow` would stop the
   `noindex` from ever being read. Do not propose disallowing them.
   Consequence for media: an image served from `/static/` is crawlable but
   `noindex`. Product imagery must not be placed there.
2. **Two canonical authorities.** For any `/pulse/marketplace` URL carrying
   `page`, the storefront's `RenderedPage.canonical_path` is authoritative and
   `search_visibility.canonical_url()` is not (it drops `page`). Agent 2 named
   Agents 5, 7, **9** and 10 as live for this hazard. **Contract:** any image
   sitemap entry or `ImageObject` that needs the page URL an image appears on
   must take it from `RenderedPage.canonical_path`.

---

## 7. Fleet reality at time of writing

Reported plainly because the brief assumed nine upstream contracts:

* There is **no Agent 0 coordination mechanism** and no fleet state file.
* `docs/search_os/` **does not exist on `main`**.
* Branches exist for agents 01, 02, 03, 05, 06, 07. **Only Agent 2 has committed
  work.** No branches exist for 04, 08, 09 (before this one), 10, 11, 12.
* So **8 of 9** nominal upstream contracts do not exist. This document depends on
  none of them and states its own assumptions.

This file follows Agent 2's agent-scoped filename convention deliberately, so no
two agents contend for one shared coordination file.

---

## 8. Media Search Truth — the current, verified answer

| Question | Answer | Basis |
| --- | --- | --- |
| Are product images crawlable? | **Yes**, all 43, no `X-Robots-Tag`, `Allow: /` | wire, Googlebot-Image |
| Do product images expire or need signing? | **No** | `media_storage.py:67-72` |
| Are product images hosted by PulseSoc? | **No** — 196/196 published are third-party | prod SELECT |
| How many images per product? | **Exactly one**, everywhere | `gallery_json`, JSON-LD |
| Is any variant image wrong? | **No** — no variant has an image | `information_schema` |
| Is alt text present and factual? | **Yes**, 87/87, product title | wire |
| Is there a CLS risk from images? | **No** — 87/87 in ratio-locked boxes | CSS + wire |
| Are responsive images emitted? | **No** `srcset` anywhere; 10.68 MB / 43 | wire |
| Is any video search-eligible? | **No** — no public landing page at all | wire |
| Is there an image or video sitemap? | **No** — both 404; no `<image:>`/`<video:>` | wire |
| Can Mux-hosted posters be indexed? | **No** — `noindex, nofollow`, unconditional | wire, 4 IDs, 2 hosts |
| Is `cdn.coinpilotx.app` crawlable? | **Yes** — `200`, real bytes, no `X-Robots-Tag` | wire, 2 UAs |
| Does deleting a reel remove its media? | **No** — 4/4 still streamable | wire |
| Is "Image unavailable" in HTML a fault? | **No** — unconditional, `display:none` | CSS + wire 87/87 |

---

## 8a. How to re-verify this

`scripts/search_os/verify_media_search_readiness.py` reproduces the image half of
§8 against any origin. Read-only, no database, no credentials, no environment
variables, and it does not follow redirects.

```
python3 scripts/search_os/verify_media_search_readiness.py
python3 scripts/search_os/verify_media_search_readiness.py --limit 5 --verbose
```

Against production at `5bdf4e431` it exits `0`:

```
42 pages, 87 <img>, 87 ratio-locked boxes, 87 fallback spans
alt text missing on 0, srcset present on 0
44 distinct image URLs, 10.79 MB total, 251 KB mean
PASS -- every image is crawlable, ratio-locked and labelled.
```

It fails on a real fault rather than passing vacuously — confirmed by running its
image check against four controls:

| Control | Result |
| --- | --- |
| `image.mux.com` poster | **fails** — `X-Robots-Tag 'noindex, nofollow'` |
| `cdn.coinpilotx.app` poster | passes (0 failures) |
| a 404 image URL | **fails** — `image 404` |
| an HTML page served as an image | **fails** — wrong content type *and* wrong magic bytes |

The last two controls matter because a host can serve `content-type: image/jpeg`
over an error page; the byte-signature check is what catches that, and the
content-type check alone would not.

---

## 9. Non-goals honoured

No change was made to price, inventory, checkout, Stripe, supplier ordering,
product publication, canonical URL policy, global structured-data architecture,
sitemap infrastructure, Merchant sync, IndexNow, or privacy policy. No second URL
system, competing JSON-LD, independent sitemap, competing event bus, second
moderation engine or rival ledger was created. No payment architecture was
touched, no charge or refund was issued, no auth secret was changed, no catalog
safety gate was bypassed, no held inventory was published, no variant was
guessed. Platform fee and buyer-facing free shipping are untouched. The App Store
build was not disturbed. **No media was created.**

---

## 10. Handoffs

**→ Agent 0 (coordination).** There is no fleet state mechanism; this file is
Agent 9's status of record. Two cross-cutting items need owners: the
§2.3 `opacity: 0` single point of failure (storefront CSS owner, must bump
`?v=`), and §3.5 deleted-reel media retention (Mux lifecycle owner).

**→ Agent 2 (URL/indexability).** Acknowledged and bound by both contracts in
§6. Confirmed independently that `/static/` carries no `X-Robots-Tag` on the wire
and that PulseSoc's `robots.txt` disallows no media path. Note for your model:
`image.mux.com` and `stream.mux.com` set `noindex, nofollow` at the origin, which
is a third-party `X-Robots-Tag` no PulseSoc route controls.

**→ Agent 4 / 5 / 7 (structured data, storefront, product pages).** Before any
`ImageObject` or `VideoObject` work: there is exactly **one image per product**
(§1.1), **no variant imagery at all** (§1.3), and the image's page URL must come
from `RenderedPage.canonical_path`, not `search_visibility.canonical_url()`
(§6.2). A `VideoObject` must not point `thumbnailUrl` at `image.mux.com` (§3.3).

**→ Agent 6 / 10 (sitemaps, discovery).** No image or video sitemap exists
(`sitemap-images.xml`, `sitemap-videos.xml`, `sitemap-reels.xml` all 404;
`sitemap-live.xml` and `sitemap-replays.xml` are 200 with zero entries). An image
sitemap is viable today for 42 product URLs at one image each. A video sitemap is
**not** viable: there is no public video landing page to list (§3.2), and
duration is missing on 293/293 videos (§3.4). Omit incomplete videos rather than
padding them.

**→ Agent 11 / 12 (trust, privacy, QA).** §3.5 — soft-deleted reels remain
publicly fetchable, poster and HLS manifest, unsigned, 4 of 4 probed. Retention
issue, not an index issue. Also §2.5 — private messenger media is an expiring
access path distinct from product media; the two share an error string and are
routinely conflated.

**→ Merchant/feed owner.** Every published product image is third-party hosted on
CJ or Aliyun (§1). Merchant image requirements will be met by hosts PulseSoc does
not control and cannot set headers on. Plan for a re-hosted copy on
`cdn.coinpilotx.app`, which §3.3 verifies is crawlable and header-clean.

---

## 11. Open — stated as unknown rather than guessed

* Whether any **currently-visible** media object anywhere on the platform is
  reachable by a crawler without authorisation. §3.5 proves Mux objects outlive
  deletion, but Mux sets `noindex` and nothing links them. A full crawl-reachable
  public/private media boundary sweep across every surface was not completed.
* Whether `cdn.coinpilotx.app` is header-clean for **every** key prefix. Two
  prefixes were probed (`pulse_media/`, `/static/`); both clean.
* The six R2-hosted, never-published listings (ids 8–13) point at `chat_media/`
  keys. Why a marketplace listing references a chat-media key is unexplained and
  belongs to whoever owns listing ingestion.

---

## Appendix — correction to a stale operations note

An internal note held that `cdn.coinpilotx.app` sits behind a Cloudflare bot
challenge and that `curl` on a real object returns `403` + `cf-mitigated:
challenge` + `text/html`, so objects must be read through the S3 API instead.

**Not reproducible on this path.** A real reel poster returned `200 image/jpeg`
with genuine JPEG bytes (`ffd8ffdb`) under both `Googlebot-Image/1.0` and a
Chrome UA, with no `cf-mitigated` header. The S3-API route remains correct for
*listing* or for keys that are genuinely absent, but a CDN `403` must not be
assumed — and, more to the point, the CDN being publicly fetchable is what makes
it the viable poster host in §3.3.
