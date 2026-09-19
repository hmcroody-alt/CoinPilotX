# MapKit — capability #9, and why "not appropriate" needs to be said differently

Written 2026-09-19. `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md` §9 rates this **NOT
APPROPRIATE — there is no location data to draw**, and recommends not implementing it.

**That verdict is correct and this document does not overturn it.** It exists because the
verdict as written is the version most likely to be reversed by the next person who goes
looking, for three reasons:

- "there is no location data" is falsifiable by a grep, and the grep returns hits;
- the data it does return is the worst possible data to put on a map;
- and the reason there is no *usable* location data is not an absence at all. It is a
  decision this codebase already made, deliberately, in a commit whose reasoning is still
  sitting in `bot.py`.

A verdict of "we don't have the data yet" invites someone to go get the data. A verdict of
"we removed this data on purpose and here is the table you must not reach for instead" does
not. The difference is the point of this document.

---

## Finding 1 — the latitude/longitude the audit warns about has never held a value

The audit points at "a request-logging table" and correctly says its lat/long is IP
geolocation that "must never be shown to a user as a business location." True, but it
understates the situation in a way that matters.

`latitude` and `longitude` appear in the entire repository **three times, at two locations**:

| Where | What |
|---|---|
| `bot.py:122504-122505` | the `visitor_sessions` column declarations (`latitude REAL,` / `longitude REAL,`) — two of the three |
| `bot.py:15039` | the column list of the one `INSERT INTO visitor_sessions` — the third |

And at that insert, `bot.py:15040`, the bound values are literal:

```
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
```

There is no `UPDATE ... SET latitude`, no other writer, and no reader — `visitor_sessions`
is selected from in six places (`bot.py:17873-17903`, `:22897`, `:104678`) and not one of
them mentions either column.

So these are **dead columns**. The audit's warning guards against displaying a value that
does not exist and never has. That is not a criticism of the warning — it is the right
instinct — but the accurate statement is stronger and simpler: *a map built today would
query two columns containing nothing but `NULL`.*

Worth saying plainly because "the lat/long is IP-derived, so don't show it" reads like a
presentation problem with a presentation fix. It is not. There is nothing to present.

---

## Finding 2 — the geo question was already asked here, and answered in the opposite direction

This is the finding that changes what the verdict is *about*.

The audit frames MapKit's blocker as missing infrastructure — "a schema change, a geocoding
pipeline, an address-entry UI, and a privacy decision." Three of those four are work items.
The fourth is not available to be decided, because it has already been decided, and the
reasoning is a comment in the visitor-tracking writer at `bot.py:15058-15063`:

> `# Geo went into visitor_sessions straight off the request headers.`
> `# Nothing in front of this app sets them, so every visitor row's`
> `# country/region/city was whatever the visitor typed -- analytics`
> `# any caller could steer. Country now comes from the trusted-edge`
> `# resolver; region and city have no trusted source at all, so they`
> `# record nothing rather than recording a claim.`

Read what that actually settles. The codebase found that its only source of user location
was **client-asserted and therefore steerable**, and it did not respond by validating the
claim or flagging it low-confidence. It responded by writing empty strings — the code binds
`request_country()[:80]` for country and literal `""` for region and city (`bot.py:15064-15066`).

*Record nothing rather than record a claim.* That is the same direction as
`storageScope.ts`'s inversion and the same direction as the Core Spotlight allowlist: when a
value cannot be trusted, the safe state is its absence, and the absence has to be the
default so that forgetting is harmless.

A map is an amplifier for exactly the class of claim that decision rejected. A pin is the
most credible-looking presentation a piece of data can have — it does not render as "the
seller typed this," it renders as *where the seller is*. And the audit's own revisit
condition — "if marketplace sellers gain structured addresses" — describes precisely the
steerable claim that fix removed, now with a stronger UI attached.

**So MapKit's prerequisite is not a schema change. It is a trusted source of user location,
which this codebase has already searched for and stated does not exist.** That is a much
harder thing to produce than a column, and anyone proposing the map should have to produce
it first rather than discover the problem afterwards.

---

## Finding 3 — five `address` columns, and three of them are Bitcoin

The obvious way to check the audit's claim is to grep the schema for `address`. That returns
five column declarations, which looks like a refutation. It is not:

| Column | Table | What it holds |
|---|---|---|
| `address TEXT` | `transaction_history` (`bot.py:116061`) | a **blockchain** address — sits beside `txid`, `amount_btc`, `fee_btc`, `confirmations` |
| `address TEXT` | `connected_wallets` (`:116074`) | a **blockchain** address — sits beside `chain TEXT DEFAULT 'BTC'` |
| `address TEXT` | `saved_wallets` (`:120483`) | a **blockchain** address — sits beside `chain TEXT` |
| `address TEXT` | `employees` (`:123809`) | a real postal address — see Finding 4 |
| `address_line1/2` + `city`/`state`/`zip_code`/`country` | `admin_users` (`:122347-122352`) | real postal addresses — see Finding 4 |

Three of the five are crypto wallet addresses, which is unsurprising given the repo's origin
as a crypto product and completely invisible to a grep for the word. Geocoding a `bc1q…`
string produces either nothing or, worse, a confident wrong pin.

This is worth writing down as a research note and not just a list, because the same trap fired
twice while gathering this evidence: an earlier pass used `grep -n "…\|venue"` to find venue
columns and matched every line containing **re**`venue` — of which this repo has many. A
substring search is how "we have address data" and "we have venue data" both become true
statements about a schema that has neither.

---

## Finding 4 — the only genuinely mappable addresses in the schema are staff home addresses

The two non-crypto hits are the two tables where a map would be actively harmful.

`admin_users` (`bot.py:122336-122359`) is the richest structured-address record in the entire
schema — and it is complete enough to geocode cleanly:

```
date_of_birth TEXT,
address_line1 TEXT,
address_line2 TEXT,
city TEXT,
state TEXT,
zip_code TEXT,
country TEXT,
emergency_contact_name TEXT,
emergency_contact_phone TEXT,
```

`employees` (`bot.py:123807-123812`) is the same shape in miniature — `address`,
`date_of_birth`, `emergency_contact`.

These are the home addresses of the platform's own administrators and staff, sitting beside
their dates of birth and next of kin. They are structured where every other address field in
the product is free text, which means **they are the only rows in the database a geocoder
would succeed on.**

That inverts the shape of the risk. The audit's framing is that a map would have nothing to
show, which sounds like a wasted sprint. The actual hazard is the opposite: an engineer told
"build the map, find the location data" greps for address columns, discards the Bitcoin ones,
and finds exactly one table that works. Nothing in the schema labels it as off-limits, and it
sorts to the top of any "which of these can we geocode" comparison precisely because it is
the best-structured.

**Decision: the prohibition is named, not implied.** `admin_users` and `employees` location
fields are never rendered on a map, never geocoded, and never exposed through any user-facing
API — for the same reason `DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` writes a denylist
with reasons rather than trusting that nobody would index DMs. An absence of data is not a
control. A control is a control.

---

## Finding 5 — MapKit is free; location is not, and the app currently collects nothing

The audit's "not blocked by an entitlement or an OS version" is correct, and the SDK confirms
how correct. Drawing a map costs nothing in entitlements or floor:

| Item | Value | Source |
|---|---|---|
| `MapKit.framework`, `_MapKit_SwiftUI.framework` | present in the iOS 26.5 SDK | `System/Library/Frameworks/` |
| SwiftUI `Map` | **iOS 14.0** — far below the 16.1 floor | `_MapKit_SwiftUI.swiftinterface:443-444` |
| Entitlement | none | — |
| Info.plist key | none, *for a map alone* | — |

So a static map of a coordinate someone handed you is genuinely cheap. But that is not the
feature anyone means. The feature means "near me," and that is `UserAnnotation`
(`_MapKit_SwiftUI.swiftinterface:324-326`) or any device-position query, which needs Core
Location — and Core Location's failure mode is the silent one this audit keeps finding.
`CLLocationManager.h:471-473`:

> `The NSLocationWhenInUseUsageDescription key must be specified in your Info.plist; otherwise, this method will do nothing, as your app will be assumed not to support WhenInUse authorization.`

Not an error. Not a rejected permission dialog. *Nothing.*

And the plist key is the small half of the cost. Two facts about the current app make the
real size of it visible:

**1. The app declares that it collects no data at all.**
`mobile-native/ios/PulseSoc/PrivacyInfo.xcprivacy` has four `NSPrivacyAccessedAPITypes`
entries (file timestamps, `UserDefaults`, boot time, disk space), `NSPrivacyTracking = false`,
and:

```
"NSPrivacyCollectedDataTypes" => [ ]
```

Empty. Adding location would be the **first entry that array has ever had**, and it would be
"Precise Location." In App Privacy terms that is the largest single step available — from a
nutrition label that says "Data Not Collected" to one with a location row — and it is a
permanent per-release declaration obligation thereafter, not a one-time form.

**2. The web product actively refuses the same capability.** `bot.py:2787` sets
`Permissions-Policy: camera=(self), microphone=(self), geolocation=()` — camera and mic are
granted to first-party frames, geolocation is granted to nobody, including PulseSoc itself.
The native app's `app.json` `infoPlist` block declares camera, microphone, photo library and
Face ID usage strings and no location string, which is the same posture expressed the other
way.

So shipping a location permission would make the iOS app claim a capability the web product
explicitly denies itself. That is not automatically wrong — native and web reasonably differ —
but it is a product-level inconsistency that should be decided on purpose, and it is not
mentioned anywhere in the plan today.

---

## Finding 6 — what *would* be appropriate, so "do not implement" does not read as "location is forbidden forever"

There is one location field in this product with a trusted source, and Finding 2 names it:
country, from the trusted-edge resolver. `marketplace_merchant_applications`
(`bot.py:117707-117734`) carries `country` and `state_region` as free text, and
`marketplace_sellers` the same.

If the marketplace ever needs a geography feature, the honest version is a **country-level
filter or badge** — "ships from Nigeria," "sellers in your country" — which:

- uses the one field with a source the codebase already trusts;
- needs no new schema, no geocoder, no address-entry UI;
- needs no Core Location, no plist key, and no change to the privacy manifest;
- and needs **no map**, because a country is a label, not a coordinate.

That is not MapKit. That is a `WHERE` clause, and it delivers most of the product value that
motivates asking for a map in the first place. Naming it here matters: the risk of a flat
"do not implement" is that it gets re-litigated as "but users want to find local sellers,"
and the answer to that is not a map.

**Recommendation, unchanged from the audit and now with a reason that survives contact:
do not implement MapKit.** Revisit only if a trusted source of user-supplied location appears
— which, per Finding 2, means reversing a decision this codebase made after finding the
available source was steerable.

---

## What is owed

| Owed | Why |
|---|---|
| A named prohibition on geocoding or mapping `admin_users` / `employees` location fields | Finding 4. An absence of usable data is not a control, and these are the only rows that *are* usable. |
| Consider dropping `visitor_sessions.latitude` / `longitude` | Finding 1. Dead columns that nothing writes and nothing reads, on a table whose geo fields were deliberately emptied. Leaving them invites a future writer to fill them. Low priority, but the cost of removal is also near zero. |
| If location is ever revisited: a written decision on the web/native inconsistency | Finding 5. `geolocation=()` on the web and a location permission on iOS is a real divergence, and it should be intentional. |

No test is owed here, because nothing is being built. The one enforceable item is the
prohibition in Finding 4, and the natural place for it is wherever the marketplace's
data-exposure rules eventually live — not a MapKit document nobody will read again.

---

## What was verified, and what was not

**Verified by reading the repo:** `latitude`/`longitude` exist only at `bot.py:122504-122505`
(declaration) and `:15039` (insert column list), with literal `NULL, NULL` bound at `:15040`,
and are never read by any of the six `visitor_sessions` selects (`:17873-17903`, `:22897`,
`:104678`); the geo decision comment and its `""` bindings at `:15058-15066`; all five
`address` columns and their surrounding table definitions (`:116061`, `:116074`, `:120483`,
`:123809`, `:122347-122352`); `marketplace_merchant_applications`' free-text `country` /
`state_region` (`:117707-117734`); `Permissions-Policy: … geolocation=()` at `:2787`;
`PrivacyInfo.xcprivacy`'s empty `NSPrivacyCollectedDataTypes`; and that `mobile-native/`
contains no `expo-location`, no `react-native-maps` and no `MapView` (case-insensitive grep
across `src/` and `package.json` returns nothing).

**Verified by reading the iOS 26.5 SDK:** SwiftUI `Map` is `@available(iOS 14.0, …)`
(`_MapKit_SwiftUI.swiftinterface:443-444`); `UserAnnotation` exists at `:324-326`;
`requestWhenInUseAuthorization` "will do nothing" without `NSLocationWhenInUseUsageDescription`
(`CLLocationManager.h:471-475`).

**Not verified.** Whether the *production* `visitor_sessions` table contains non-NULL
latitude/longitude rows written by some path that no longer exists in the source. The code
says no writer exists today; it does not prove none ever did. This does not change the
recommendation — a column of historical IP-derived coordinates would be a stronger reason not
to map it, not a weaker one — but Finding 1's "has never held a value" is a claim about the
current code, not a claim about the database, and the two are not the same thing in a repo
with no migration framework.
