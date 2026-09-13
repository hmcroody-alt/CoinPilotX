# PulseSoc — Inline-HTML Website Layer Inventory

Scope: the server-rendered website generated as **inline HTML strings inside `bot.py`**.
This is the layer a web rebuild replaces. Jinja `templates/` is a small minority of it.

- Working tree at `ffd356c5` (2026-09-12), `bot.py` **dirty** at time of measurement.
- `bot.py` = **123,711** physical lines / **7,927,279** chars.
- Method: Python `ast` parse of `bot.py` + decorator extraction. Not grep-guessing, except
  where noted as a cross-check. All line numbers are from this working tree.
- Read-only audit. Nothing in the repo was modified except this file.

---

## 1. Route census

`webhook_app.route(...)` decorators, counted individually (stacked decorators on one view
function each count once — an earlier naive pass collapsed them and undercounted by 203):

| | count |
|---|---|
| total route decorators | **1,777** |
| `/api/*` | 1,117 |
| **non-`/api/` (candidate page routes)** | **660** |

All 1,777 are on the single `webhook_app` object; there are no blueprints in `bot.py`.

### How the 660 non-API routes produce a response

Classified by walking each view function body **plus one to three levels of
`return other_view(...)` delegation** (bot.py has ~90 thin alias views, e.g.
`pulse_ai_alias` bot.py:10986, `pulse_dashboard_alias` bot.py:10981, that are 4-line
wrappers; classifying without following them mislabels them "unclassified").

| response strategy | routes |
|---|---|
| inline f-string HTML (own full document or fragment) | **264** |
| `pulse_social_shell(...)` | **164** |
| `render_template(...)` (Jinja) | 58 |
| `redirect(...)` only | 35 |
| `send_file` / `send_from_directory` | 12 |
| `Response(...)` non-HTML (CSV, XML, robots.txt) | 12 |
| `pulse_page_html(...)` | 10 |
| `jsonify(...)` — JSON API living outside `/api/` | 101 |
| unclassified (`stripe_webhook`, bot.py:105164-105168) | 4 |

**HTML-producing page routes = 496** (inline + both shells + Jinja).
The other 164 are JSON endpoints, webhooks, redirects, file serves and feeds.

Note the 101 `jsonify` routes outside `/api/` — the `/api/` prefix is not a reliable
signal of what is an API. `/debug/*` (5), `/health/*` (5), `/webhook*` (7) and 80 under
`/admin/*` are JSON but unprefixed.

### Families (non-`/api/`; columns are response strategy)

| family | routes | HTML pages | social_shell | page_html | inline | jinja | redirect | json |
|---|---|---|---|---|---|---|---|---|
| **admin** | **281** | **188** | 1 | 0 | 187 | 0 | 4 | 80 |
| pulse/* long tail | 81 | 68 | 53 | 9 | 1 | 5 | 13 | 0 |
| dashboard | 45 | 44 | 38 | 0 | 2 | 4 | 1 | 0 |
| arena | 37 | 33 | 0 | 0 | 33 | 0 | 4 | 0 |
| pulse/dashboard | 12 | 11 | 10 | 0 | 0 | 1 | 1 | 0 |
| pulse/premium | 8 | 8 | 7 | 0 | 0 | 1 | 0 | 0 |
| pulse/live | 7 | 6 | 6 | 0 | 0 | 0 | 1 | 0 |
| pulse/settings | 7 | 7 | 7 | 0 | 0 | 0 | 0 | 0 |
| education | 6 | 6 | 0 | 0 | 6 | 0 | 0 | 0 |
| pulse/camera | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| pulse/private-office | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| pulse/profile | 5 | 3 | 3 | 0 | 0 | 0 | 2 | 0 |
| debug | 5 | 0 | — | — | — | — | — | 5 |
| health | 5 | 0 | — | — | — | — | — | 5 |
| pulse/creator, pulse/merchant | 8 | 8 | 8 | 0 | 0 | 0 | 0 | 0 |
| account | 4 | 3 | 0 | 0 | 0 | 3 | 1 | 0 |
| pulse/{marketplace,messages,groups,pages,courses} | 15 | 15 | 15 | 0 | 0 | 0 | 0 | 0 |
| pulse/roast-battle | 3 | 3 | 0 | 0 | 3 | 0 | 0 | 0 |
| legal, markets, chat, predictions, sports-edge | 15 | 13 | 0 | 0 | 7 | 5 | 1 | 0 |
| checkout/billing/payments/upgrade/verify-email/reset-password | 12 | 12 | 3 | 0 | 0 | 9 | 0 | 0 |
| webhooks (stripe, telegram, …) | 11 | 0 | — | — | — | — | — | 6 |
| singleton families (68 distinct 1-route prefixes) | 68 | 47 | | | | | | |

**The headline is `admin`.** 281 of 660 non-API routes (43%) are `/admin/*`, and 188 of
those are HTML pages built by inline f-strings — not one of them uses the social shell or
Jinja. The admin console is the single largest "website" in this repo, larger than the
consumer PulseSoc surface, and it is entirely bespoke string-built HTML.

Consumer PulseSoc (`/pulse/*` across all subfamilies) is ~160 routes, ~140 of them HTML.

---

## 2. `pulse_social_shell` — bot.py:47004-47111 (108 lines)

Signature `(title, description, main_html, side_html="", script_html="", show_intro=True)`.
Called **97 times** in `bot.py`; 164 route decorators resolve into it (aliases multiply).

Structure — the function is ~107 lines of Python assembling nav data, then **one single
`return Response(f"""...""")` on line 47111 that is 11,677 characters long**. That one
physical line contains the entire document: doctype, head, 8 `<link>` stylesheets, a
~9KB inline `<style>` block, body markup, 6 `<script src>` tags and a trailing inline
`<script>`. It is the third-longest line in `bot.py`.

**Emits:**
- `<head>`: `noindex,nofollow` robots meta, viewport with `viewport-fit=cover`,
  `<title>{clean_html(title)} | PulseSoc</title>`.
- 8 CSS links, all cache-busted by hand-edited query strings
  (`pulsesoc-tokens.css?v=parity-20260806a`, `pulse_desktop_feed.css?v=shell-nav-20260909a`, …).
- A ~9KB inline `<style>` with the whole design system: CSS custom properties, dark
  `color-scheme`, card/button/input styling, `.avatar`, drawer, mobile bottom nav, FAB,
  and 4 media queries (`max-width:900px`, `min-width:1024px`, `min-width:1100px`).
  So yes — **responsive, mobile-first, with a real desktop breakpoint.**
- Chrome: left drawer (`#pulseDrawer`) + backdrop, desktop top nav
  (`pulse_desktop_top_nav_html`, bot.py:39901-39970), desktop left rail
  (`pulse_desktop_rail_nav_html`, bot.py:40088-40111), mobile sticky topbar with
  search/notifications/avatar, mobile bottom nav (5 items), a FAB, a create-sheet dialog
  (bot.py:47094-47110), and a toast div. **No footer.**
- Nav data: 7 primary links (47016-47024) + 20 "apps" links (47025-47046) + drawer extras
  (`pulse_shell_drawer_extras`, bot.py:40022-40045) + 6 tail links. `PulseSoc Labs` is
  injected for super users only (47047-47048).

**Auth state is embedded, not fetched.** Lines 47005-47007 call `require_account()` and
`redirect(url_for("login_page", next=request.path))` on failure — so the shell is itself
the auth gate. It then re-loads the account (47008) and bakes avatar URL, display name and
initials into the markup (47009-47013), and `user_is_super_user` / `admin_current_user`
drive nav visibility.

**Inline JS** (tail of 47111): a `toast()` helper, drawer open/close wiring, and a
client-side `pulseApi(url, opts)` fetch wrapper (`credentials:'same-origin'`,
`cache:'no-store'`, throws on `ok===false`). Then `{script_html}` is spliced in, then
three hydration calls (`CoinPilotTime`, `PulseMediaRenderer`, `PulseReactionSystem`).

### `script_html` — injection assessment

`script_html` is **concatenated raw into a `<script>` block** (47015 prepends two
generated scripts, 47111 splices the result). There is no escaping and no JSON encoding.
This is by design — callers pass server-authored JS literals (e.g.
`pulse_marketplace_listing_page` bot.py:54148 passes a static handler string).

I checked the 97 call sites: I did **not** find a call that interpolates request data
directly into `script_html`. So this is **not a live XSS today** — but it is an unguarded
sink one careless f-string away from being one, and it should be treated as a hard rule in
any rebuild. Related, line 47014 builds `shell_avatar_script` using `json.dumps(...)` to
embed HTML into JS; `json.dumps` does **not** escape `</script>`, so that path is also
only safe because avatar HTML is server-derived.

---

## 3. `pulse_page_html` — bot.py:40754-41607 (854 lines)

Signature `(title, active_feed="for_you", topic="", profile_id="")`. Called **3 times**;
10 route decorators reach it — essentially only `/pulse` (bot.py:41623, `pulse_page`,
which maps `/pulse/trending|following|questions|my-posts|scam-alerts|arena|roast-clips`
onto a `feed` argument, bot.py:41632-41651).

### How the two shells differ

| | `pulse_social_shell` | `pulse_page_html` |
|---|---|---|
| line span | 47004-47111 (**108 lines**) | 40754-41607 (**854 lines**) |
| call sites | 97 | 3 |
| routes reaching it | 164 | 10 |
| role | **generic wrapper** — caller supplies `main_html` | **one page** — the feed, self-contained |
| params | takes caller HTML | takes feed/topic/profile *selectors* only |
| nav | 7 + 20 links, flat drawer | 29 links, **5 grouped drawer sections** (40801-40807) |
| rails | top nav + left rail | top nav + left rail + **right rail** (40845) + **status rail** (40846) |
| extras | — | `boot_profile` query-param perf switch with 7 modes (40848-40850) |
| escaping | `clean_html` | `clean_html`, applied to nav labels *and* hrefs (40800, 40816) |

They are **not** generations of each other — neither supersedes the other. `pulse_page_html`
is a single bespoke mega-page (the feed) that happens to also contain a shell;
`pulse_social_shell` is the reusable chrome extracted for the other ~160 pages. They
**duplicate** the chrome: both independently compute `shell_avatar_url` / `shell_name` /
`shell_initials` with identical code (40794-40799 vs 47009-47013), both build a 5-item
mobile bottom nav with the same chat SVG (40820-40842 vs 47056-47067), both hard-code the
same nav destinations in different orders and different groupings.

Evidence they have drifted: `pulse_page_html` line 40821 assigns `mobile_bottom_icons`
and line 40822 immediately reassigns it — dead line, a merge artifact. And the comment at
bot.py:47078-47088 records that the desktop top nav was only recently back-ported *into*
`pulse_social_shell`, because "`/pulse` rendered a topbar carrying search, notifications,
an account menu and a 24-destination rail, while these 96 pages rendered a seven-link row
with no search, no notifications and no route to a profile at all above 900px … Two
products on one domain, and the smaller one was what most pages got."

There are **three further shells**: `pulse_section_shell` (bot.py:44342-44353, 2 calls),
`pulse_web_section_shell` (bot.py:81914-81928, 10 calls), `private_office_web_shell`
(bot.py:81931-81941, 7 calls). Five shells total.

---

## 4. HTML/CSS/JS mass inside `bot.py`

**This is the number that drives the rebuild estimate. Line counts are misleading here**
because `bot.py` packs entire documents onto single physical lines (longest line = 12,858
chars, at bot.py:33374). I measured both ways.

### Method A — AST string-literal spans (line-based)

Parse `bot.py` with `ast`, take every outermost `Constant(str)` and `JoinedStr` (f-string)
node, classify its source text as HTML / CSS / JS / SQL / other by regex, union the
physical line ranges. 81,133 string nodes.

| | lines | % of 123,711 |
|---|---|---|
| inside HTML literals | 35,050 | 28.3% |
| CSS-only literals | 176 | 0.1% |
| JS-only literals | 416 | 0.3% |
| **markup total** | **35,642** | **28.8%** |
| SQL literals | 10,818 | 8.7% |
| other strings | 33,923 | 27.4% |

### Method B — AST string-literal spans (character-based)

Same nodes, summing characters instead of lines.

| | chars | % of 7,927,279 |
|---|---|---|
| HTML literals | 3,638,991 | 45.9% |
| CSS-only | 14,569 | 0.2% |
| JS-only | 44,809 | 0.6% |
| **markup total** | **3,698,369** | **46.7%** |
| SQL | 373,299 | 4.7% |
| other strings | 1,273,665 | 16.1% |

(The HTML bucket subsumes CSS and JS that live *inside* HTML strings — e.g. bot.py:47111
is one literal containing all three. The split is not meaningful; the total is.)

### Method C — grep cross-check (lower bound)

- lines containing any `<tag` token: **12,460**
- lines matching a common HTML element specifically: 6,183
- `<div` 2,180 · `<span` 1,867 · `<section` 1,346 · `<button` 742 · `<input` 392
- `class=` 6,093 · `style=` 202 · `<script` 98 · `<style` 97

Method C is a floor (only lines that *directly contain* a tag); Method A is the ceiling
(whole literal spans, including text/CSS/JS lines inside a block with no tag on them).
They bracket the answer and are consistent.

### Verification

The four longest lines in `bot.py` — 33374, 37992, 47111, 85084 — were each read and are
all `return Response(f"""<!doctype html>...` full documents. Classification confirmed.

> **Estimate: ~3.70M characters (46.7% of `bot.py`) is HTML/CSS/JS string literal.
> Expressed as conventionally-formatted source at ~80 cols that is ≈46,000 LOC of
> front-end. By physical lines touched, 35,642 (28.8%).**
> Use ~35k–46k LOC of markup as the rebuild input, spread over 496 HTML routes
> (≈70–90 lines of markup per page route, median much lower, a few pages enormous).

Separately: **269,368 chars of inline `<script>` JS** across 66 literal script blocks
(≈3,400 80-col lines), versus **754,646 bytes** in the 23 files of `static/js/`. Roughly a
third again as much JS lives in `bot.py` as in the JS directory.

---

## 5. How real are these pages?

Measured across all 496 HTML page routes (following delegation up to 2 levels):

| | routes | share |
|---|---|---|
| executes a **server-side DB query** before rendering | **203** | 41% |
| calls a service/engine but no direct DB | 167 | 34% |
| **no data access at all** (static markup / link hub) | **126** | **25%** |
| auth-guarded | 403 | 81% |
| inject client-side `fetch`/`pulseApi` calls | 212 | 43% |

So: **~75% of pages touch real data; ~25% are static.** This is a real website, not a
Potemkin one — but a quarter of it is navigation furniture.

Six representative pages read end to end:

**`/pulse` — feed** (bot.py:41623 `pulse_page` → `pulse_page_html` 40754).
Genuine. Auth-required (40755-40757). Server-renders the full shell with top/left/right/
status rails; feed content is hydrated client-side. Has a `boot_profile` perf switch with
7 modes. Responsive. The single most complex surface in the file.

**`/pulse/profile/<slug>`** (bot.py:84883 → `pulse_profile_page` 84884-84894).
Genuine. Auth-required, resolves a public key to a user id against the DB
(`pulse_user_id_from_public`), 404s cleanly via `pulse_profile_not_found_page`, delegates
to `pulse_profile_page_for_user`. Server-side rendered.

**`/pulse/messages`** (bot.py:83960 `pulse_messages_page` 83961-83983).
Genuine, and **the exception to the thesis** — when the `pulse_communications_v2` flag is
on it returns `render_template("pulse_messages_v2.html")` with a real Jinja template and a
client SPA, falling back to inline `pulse_communications_page("")`. Messaging has already
been migrated off the inline-HTML layer. Note the whole block is wrapped in
`try/except Exception` (83962/83981) that logs and silently falls back to legacy.

**`/pulse/marketplace/<int:listing_id>`** (bot.py:54068 `pulse_marketplace_listing_page`
54069-54156). Genuine. Auth-required, real SQL with lifecycle + discovery-visibility
gating, 404s rather than leaking that an unapproved listing exists (deliberate — see the
comment at 54092-54098). Renders a media gallery, price, seller identity, and three buyer
actions wired to real APIs. Interactive, server-rendered, client-hydrated.

**`/pulse/settings`** (bot.py:80938 `pulse_settings_page` 80939-80951).
**A stub.** 13 lines. It is `pulse_gateway_card_html` with a title, a sentence, and five
links to the pages that actually do the work. No data, no state, no settings. The helper
(bot.py:80814-80831) is used 10 times, so this pattern is present but not dominant.

**`/pulse/premium`** (bot.py:56361 `pulse_premium_page` 56362-56484, 123 lines).
Genuine and data-heavy: `premium_entitlement_service` founder membership, entitlements,
payment config, a live Founder Wall from the DB, Stripe checkout and billing-portal
buttons, and an iOS-native gate (`ios_paid_digital_unavailable_response`). **Caveat:** the
copy advertises features that are not shipped — "Founder checkout is being connected"
(56392), and benefits described as "prepared in backend entitlements" (56398) and
"tracked backend-side for future routing" (56400). The page is real; some of what it sells
is not.

**Verdict:** these are real product surfaces, not SEO/app-promo landing pages. The
marketing-landing pattern exists but is small and isolated (`ads_landing_page` serving 4
routes at bot.py:1619-1622; `seo_content_hub` at 2465-2467; `seo_topic_page` at 2474). The
genuine weakness is not fakeness — it is the 126 static link-hub pages and the fact that
43% of the consumer surface is chrome rendered server-side then filled in by client fetch,
i.e. already half a SPA.

---

## 6. Client-side JS coupling

`static/js/` holds **23 files / 754,646 bytes**. Referenced from `bot.py` inline HTML
(count = number of `<script src>` occurrences):

`pulse_status_viewer.js` 7 · `time.js` 6 · `notifications.js` 5 (`/static/`, not
`/static/js/`) · `pulsesoc_promotions.js` 5 · `pulseshell_bridge.js` 4 ·
`pulse_media_renderer.js` 4 · `pulse_media_picker.js` 3 · `pulse_environment_engine.js` 3 ·
`pulse_upload_manager.js` 2 · `pulse_reaction_system.js` 2 · `pulse_pwa_install.js` 2 ·
`pulse_i18n.js` 2 · then `pulse_search_bridge.js`, `pulse_radio.js`, `pulse_home_core.js`,
`pulse_camera_engine.js`, `pulse_ads_hooks.js`, `admin_ops_center.js`,
`service-worker.js`, `analytics.js` at 1 each.

Every `<script src>` carries a hand-maintained `?v=` cache-buster
(`?v=feed-actions-v2-20260629a`, `?v=status-v4-20260703b`, …). There is no build step and
no content hashing — cache correctness depends on a human editing the right string in the
right f-string.

**Duplication is confirmed.** `pulseApi` is defined inline in the shell (bot.py:47111) and
does **not** exist in any `static/js/` file — so 160+ pages get a fetch wrapper that only
exists as a string in Python, while `static/js/` modules must implement their own. `toast`
is worse: defined inline at bot.py:47111 *and* independently in
`static/js/pulse_home_core.js` *and* in `static/js/pulse_radio.js` — three
implementations of the same primitive, one of which is unversioned Python string data.

---

## 7. Auth on page routes

**Session cookie, via Flask's signed `session`.** There is **no decorator** — `grep` finds
**0** occurrences of `@login_required`, `@require_login` or `@admin_required` in `bot.py`.
The gate is an inline call repeated by hand:

```
user = require_account()
if not user:
    return redirect(url_for("login_page", next=request.path))
```

`require_account()` is called **152 times**; the redirect-to-login line appears **102**
times.

- `require_account` (bot.py:5461-5470) → `load_account_by_id(account_user_id())`, then
  re-checks `account_login_restriction_message(user)` and, if the account is restricted,
  pops the session and flags the persistent cookie for clearing.
- `account_user_id` (bot.py:3658-3659) resolves in order:
  `session["account_user_id"]` → `account_user_id_from_mobile_access_token()` (bearer) →
  `restore_account_from_persistent_cookie()` (remember-me).
- `load_account_by_id` (bot.py:4951-4960) hits `users` on **every request** — one extra
  round trip per page, uncached.

For `/pulse/messages`: the browser holds the Flask session cookie; `pulse_messages_page`
calls `require_account()` at bot.py:83964 and, signed out, **302s to
`/login?next=/pulse/messages`**. Pages built on either shell are gated by the shell itself
(bot.py:40755-40757 and 47005-47007), so the guard is structural for those ~174 routes and
manual for the rest. 403 of 496 HTML page routes (81%) are guarded; the unguarded 19% are
legal/SEO/landing/health surfaces.

Risk of the no-decorator pattern: a new page route that forgets the three-line preamble
and does not use a shell is silently public. That is a review-discipline dependency, not
an enforced invariant.

---

## 8. Injection surface

### Finding 1 (primary) — `clean_html` is a tag stripper, not an HTML escaper

**bot.py:119744-119747:**

```python
def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text
```

It removes substrings that look like complete tags. It does **not** escape `<`, `>`, `&`,
`"` or `'`. Executed against the real implementation:

| input | output |
|---|---|
| `x' onmouseover='alert(1)` | `x' onmouseover='alert(1)` (unchanged) |
| `x" onfocus="alert(1)" autofocus="` | unchanged |
| `<img src=x onerror=alert(1)` (no `>`) | unchanged |
| `A & B` | `A & B` (unchanged) |
| `<script>alert(1)</script>` | `alert(1)` |

Two bypass classes: **(a) attribute breakout** — a single or double quote needs no angle
bracket at all and escapes any quoted attribute; **(b) unterminated tag** — the regex
requires a closing `>`, so `<img src=x onerror=alert(1)` passes through intact and the
next `>` in the surrounding template closes the tag.

**Scale.** `clean_html(` appears **2,217** times in `bot.py`. The correct helper is
imported at **bot.py:20** (`from html import escape as html_escape`) and is used
**4 times**. The codebase has an escaper and does not use it.

Of the 2,217 call sites, **264 interpolate into an HTML attribute delimiter** (210 into
single-quoted, 54 into double-quoted attributes) — the contexts where bypass (a) applies
with no angle bracket required. 67 of those reference user-controllable field names.

### Finding 2 — stored XSS chain, marketplace listing title (gated by approval)

The same non-escaping function is used on **both write and read**, so nothing ever escapes:

- **Write:** `title = clean_html(payload.get("title") or "")[:140]` (bot.py:~94609,
  in the listing-create handler that INSERTs at bot.py:94716).
- **Read, text context, cross-user:** `f"<h1>{clean_html(row.get('title'))}</h1>"` at
  **bot.py:54126** in `pulse_marketplace_listing_page` — rendered to *any* authenticated
  viewer of `/pulse/marketplace/<id>`.
- **Read, grid card:** `<h2><a href='...'>{clean_html(row.get('title'))}</a></h2>` at
  **bot.py:54005** in `marketplace_card`.

Payload `<img src=x onerror=alert(1)` is 26 chars, inside the 140 limit, and survives both
ends.

**Severity qualifier — do not overstate.** The listing page query is gated by
`marketplace_listing_lifecycle.public_sql(...)` and `discovery_visible_sql(...)`
(bot.py:54084-54086), so the row must reach an approved/public state. This is stored XSS
**behind a moderation step**, not a drive-by. It is still a real finding: the moderator
reviews the title as text, and approving it is what arms it.

### Finding 3 — unescaped DB values in the admin analytics console

**bot.py:28760**, inside `admin_analytics_page` (28754-28819):

```python
body = "".join("<tr>" + "".join(f"<td>{str(cell)[:180]}</td>" for cell in row) + "</tr>" for row in rows)
```

Raw database cells — including email/SMS opt-in values originating from end users — are
interpolated into HTML with **no escaping of any kind**, not even `clean_html`. The
audience is an administrator. This is the highest-value target in the file.

### Finding 4 — reflected, unescaped, admin-gated

**bot.py:28779:**

```
<a href="/admin/analytics/export/emails?password={request.args.get('password','')}">
```

`request.args` straight into a double-quoted attribute, no escaping. The route requires
`require_admin_password()` first (bot.py:28755-28756), so an attacker must already know the
admin password — **not exploitable by an unauthenticated attacker**. The more serious
issue on this line is design: the admin password travels in the **query string**, where it
lands in proxy logs, browser history and `Referer` headers.

This is the *only* direct `{request.args|form|values|cookies}` interpolation into HTML in
all 123,711 lines — the codebase does not generally reflect request data.

### Checked and clean

- `next_url` at **bot.py:29445/29452** is `quote(request.path, safe="")` (bot.py:29442) —
  URL-encoded, and sourced from the routed path. **Safe.**
- `quote_symbol_page` (**bot.py:30297-30299**) does `clean_html(symbol).upper()[:12]` into
  single-quoted attributes. Quote breakout is technically possible but the payload budget
  is 12 uppercase-folded characters. Noted as an instance of the pattern; **not a
  practical finding.**
- `script_html` (§2): no call site found that feeds it request data. **Not live today.**
- SQL: parameterised bindings (`?`) throughout the paths sampled. The f-strings inside
  SQL interpolate server-controlled fragments (`discovery_visible_sql('u')`,
  `store_name_select('ms')`, generated `?` placeholder lists at bot.py:94694), not user
  input. **No SQL injection found.**

### Recommendation for the rebuild

`clean_html` cannot be fixed in place — 2,217 call sites depend on its tag-stripping
behaviour, and switching it to `html.escape` would double-escape content that is already
stripped and would visibly break pages that rely on stripping. The two concerns must be
separated: strip-on-write, escape-on-render. Any rebuild should adopt an
autoescaping template engine and treat the 264 attribute-context sites as the migration
checklist.

---

## Unverified / out of scope

- **UNVERIFIED:** whether the 496 HTML routes all currently register at boot. Optional
  route packs are registered inside `except Exception` blocks, so a subsystem can be
  absent in production while present in source. Not checked against a running instance.
- **UNVERIFIED:** runtime behaviour of any page. This is a static read; nothing was
  executed against a server, and no XSS payload was fired at a live instance.
- **UNVERIFIED:** whether `pulse_communications_v2.flags.is_enabled()` is on in
  production, i.e. whether `/pulse/messages` serves the Jinja SPA or the inline fallback.
- **UNVERIFIED:** `display_name` write-path escaping for the *account* record feeding
  bot.py:84050-84051. The seller-application path (bot.py:92237) was traced and uses
  `clean_html`; the account path was not traced to its INSERT.
- Measurements are against a **dirty** `bot.py`. Re-run before using these numbers for a
  contract.
