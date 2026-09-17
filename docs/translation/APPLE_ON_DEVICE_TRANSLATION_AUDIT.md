# Apple On-Device Translation — Stage 0 Audit

Audited 2026-09-17 against `origin/main` @ `9cbaf759`. Device facts verified against the
physical iPhone 16 Pro (`P3r7or`, `F45E640F-6D02-514E-877C-B764E8D6818F`) and Xcode's
iPhoneOS 26.5 SDK.

This document records what exists **before** any Apple Translation code is written. It is
deliberately written so a reader can tell which claims were verified by execution and which
are code reading.

---

## 1. Executive summary

PulseSoc's translation system is **architecturally in good shape and functionally switched
off for real users.**

The good news, and it is genuinely good: there is exactly **one** translation UI component
and exactly **one** client API module. Ten call sites across the app funnel into
`ContentTranslation`, which calls `translatePulseContent()`, which posts to one route. No
screen talks to Google. That means the provider swap this mission asks for is a change at
*one* seam, not ten.

The bad news is the reason the feature has never appeared to work:

> **`TRANSLATION_QA_ONLY` defaults to `true` and is `true` in production right now.**
> Every user whose id is not in `TRANSLATION_QA_USER_IDS` receives HTTP 403
> `rollout_restricted`, which the client renders as *"Translation isn't available for your
> account yet."*

Verified by execution, not inference:

```
$ curl -s https://pulsesoc.com/internal/health/translation
{"cache":"available","configured":true,"degraded":false,"enabled":true,
 "healthy":true,"ok":true,"provider":"google","qa_only":true,
 "supported_languages_available":false}
```

Two things to read there. `qa_only: true` is the rollout gate described above.
`supported_languages_available: false` is a second, independent defect: the provider's
supported-language list has never been successfully fetched or cached, so
`/api/pulse/translations/languages` cannot return a real list.

So the honest answer to "why has translation previously failed" is **not** a bug in the
translation path. The translation path is configured, healthy and reachable. It is gated to
a QA allowlist, and the language catalogue behind it is empty.

---

## 2. Current architecture

```
  10 UI call sites
        │
        ▼
  ContentTranslation.tsx ............ single shared component, owns Translate button,
        │                             Always/Never policy, retry, View-original
        ▼
  src/api/translation.ts ........... translatePulseContent()
        │
        ▼  POST /api/pulse/translations
  bot.py:7583
        │
        ▼
  services/content_translation.py .. authorization, moderation, cache, event log
        │
        ▼
  services/translation_providers.py  GoogleAdvancedProvider (Cloud Translation v3)
        │
        ▼
  translation.googleapis.com/v3
```

### 2.1 The ten call sites

| File | Line | Content type |
|---|---|---|
| `src/components/PostCard.tsx` | 392 | post |
| `src/social/CommentThread.tsx` | 264 | comment / reply |
| `src/screens/ChatScreen.tsx` | 2173 | chat (**private**, `controlsMode="compact"`) |
| `src/screens/ReelsScreen.tsx` | 1265 | reel |
| `src/components/ReelPlayerCard.tsx` | 573 | reel |
| `src/components/StatusViewerCard.tsx` | 265, 337 | status |
| `src/components/ProfileHeader.tsx` | 458 | profile |
| `src/screens/MarketplaceProductScreen.tsx` | 436 | product / marketplace |

`ContentTranslation` is the **only** consumer of `src/api/translation.ts` outside of tests
and `TranslationPreferencesBootstrap.tsx`. This is the single most useful fact in the audit:
**no screen duplicates translation logic**, so Stage 1's "no screen may call Google
directly" is already structurally satisfied and will remain so.

### 2.2 Where a native host can mount

`App.tsx:359` already mounts a translation-related root child, keyed on user id:

```tsx
{authState.status === "signedIn" ? (
  <TranslationPreferencesBootstrap key={authState.user?.user_id || "signed-in"} />
) : null}
```

This is the correct mount point and the correct keying for the Apple SwiftUI host: it is
inside the themed navigation tree, it is present for the whole signed-in session, and the
`key` already tears the subtree down on account switch — which is exactly the cache-isolation
boundary Stage 6 requires.

---

## 3. Answers to the ten Stage 0 questions

**1. Which content currently calls Google?**
All of it, indirectly. Every content type resolves to `GoogleAdvancedProvider`.
`services/translation_providers.py:259` hard-rejects any other provider name:

```python
selected = (name or os.getenv("TRANSLATION_PRIMARY_PROVIDER", "google")).strip().lower()
if selected != "google":
    raise ProviderError("unsupported_provider", "…not implemented.")
```

There is no fallback chain and no second provider. Supported content types
(`content_translation.py:24`): post, comment, reply, chat, marketplace, product, business,
review, support, profile, reel, status, group, event.

**2. On tap, or automatic?**
Both, and this matters for cost. Default policy is `ask` — translation happens on tap. But
the per-language-pair policy is server-stored, and when it is `always`,
`ContentTranslation.tsx:133` fires a translation automatically on mount:

```tsx
if (preference.policy === "always") requestTranslation(false);
```

So a user who once chose "Always" for `fr→en` causes an automatic billable request for
**every French item that mounts**, including feed cells scrolling past. This is the
cost-exposure surface Stage 7 and Stage 8 are worried about, and it is live today.

**3. Is private text sent to the backend?**
**Yes — and redundantly.** `src/api/translation.ts:158` puts the message body on the wire:

```ts
body: JSON.stringify({ …, text: input.text, … })
```

The server then **ignores** it. `content_translation.py:603` overwrites the caller's text
with a canonical record fetched from the database after an authorization check:

```python
text = canonical["text"]
```

So today: private message plaintext leaves the device, is discarded, is re-read from
`comm_v2_messages`, and is then **sent to Google**. The client-supplied copy is pure
unnecessary exposure. Moving chat to Apple on-device translation removes both the transit
and the Google hop. This is the strongest privacy argument for the migration.

*Design note:* the server-authoritative model is a real security property — the caller
cannot translate text it is not allowed to read. Apple on-device translation necessarily
gives that up, because the text is supplied by the client. The mitigation is that the client
is translating text it **has already rendered on screen**, so no new content becomes
readable. This trade-off must be stated explicitly in the architecture doc, not glossed.

**4. Why has translation previously failed?**
`TRANSLATION_QA_ONLY` defaults `true` (`content_translation.py:82`) and is `true` in
production. Non-allowlisted users get 403 `rollout_restricted`. Secondary:
`supported_languages_available: false` in production. `TRANSLATION_ENABLED` also defaults
`false` (line 81) though it is `true` in production.

**5. Which screens duplicate translation logic?**
None. One component, one API module.

**6. Is translated text persisted centrally?**
Yes, server-side, in `pulse_content_translations` (`content_translation.py:354`). Columns:
`translation_id, user_id, content_type, content_ref, source_hash, source_language,
target_language, translated_text, provider, provider_model, created_at`.

Unique key is **`(user_id, source_hash, target_language)`** — note that `content_ref` is
*not* in the key. Two different posts with identical text share one cache row per user. TTL
`TRANSLATION_CACHE_TTL_SECONDS`, default 86400s.

Because `user_id` is in the key there is no cross-user leak, but **private-message
translations are persisted server-side today**, which Stage 6 forbids for the Apple path.

**7. iOS versions supported?**
Deployment target is **iOS 15.1** (`ios/Podfile:19` fallback — `Podfile.properties.json`
does not set `ios.deploymentTarget`; `IPHONEOS_DEPLOYMENT_TARGET = 15.1` in the pbxproj).

Apple's `TranslationSession` requires **iOS 18.0**. This does **not** require a deployment
target bump: the framework is weak-linked and all call sites are `@available(iOS 18.0, *)`
gated. Raising the target to 18.0 would drop iOS 15/16/17 users for no benefit and is
explicitly **not** proposed.

**8. Which language pairs matter?**
The app ships 11 locales at 100% key coverage (verified via `npm run i18n:validate`):
`en, ar, de, es, fr, hi, ht, ja, ko, pt, zh`.

**9. Is Haitian Creole required?**
Yes. `ht` is a first-class shipped locale with a full catalogue
(`src/i18n/catalogs/ht/`). Nothing special-cases it today — the locale validator
(`content_translation.py:106`) accepts any RFC-5646-shaped tag, so `ht` is passed straight
to Google. **Apple's support for `ht` must be probed at runtime via `LanguageAvailability`,
not assumed**, and if unsupported it must route to the controlled fallback rather than
reporting a translation failure.

**10. Google character usage and cost?**
**Not observable today.** Spend is recorded — `translation_providers.py:67` calls
`undx_capabilities.record_spend(CALL_KIND_TRANSLATION, "google", units=len(text))` on
success — but the `('translation','google')` pair is **unpriced** in `undx_capabilities`, so
every call lands as `uncosted_calls=1, cost_micro_usd=0`
(`tests/test_translation_spend.py:140`). Character volume is captured; dollar cost is not.

Detection is billed separately from translation
(`tests/test_translation_spend.py:186`), so an auto-detect translation costs **two**
billable calls.

---

## 4. Existing controls, and the gaps

| Control | Status |
|---|---|
| Per-request timeout | `TRANSLATION_REQUEST_TIMEOUT_SECONDS`, default 10s, clamped 1–30 |
| Retries | `TRANSLATION_MAX_RETRIES`, default 2, clamped 0–3, on 408/429/5xx |
| Character spend recording | Present but **unpriced** |
| Moderation pre/post translation | Both default on |
| Server cache | Present, 24h TTL, per-user |
| **Per-user rate limit** | **Absent** |
| **Per-device rate limit** | **Absent** |
| **Daily / monthly character budget** | **Absent** |
| **Circuit breaker on budget** | **Absent** |
| **Client-side request dedupe across cells** | **Absent** |
| **Cancellation on scroll-away** | **Absent** |

`ContentTranslation` does have a `busyRef` single-flight guard and a bounded 2-attempt
backoff with an `activeRequest` key check, so rapid taps on *one* item are already handled.
What is missing is anything that limits *aggregate* spend across items, users or time.

---

## 5. Native iOS baseline

| Item | Value | Source |
|---|---|---|
| Deployment target | iOS 15.1 | `ios/Podfile:19`, pbxproj |
| RN architecture | **Old** (`newArchEnabled: false`) | `app.json`, `Podfile.properties.json`, `Info.plist` `RCTNewArchEnabled=false` |
| Native module pattern | **Expo Modules API** (`ModuleDefinition`) | `modules/pulse-now-playing/`, `modules/pulse-video-mixer/` |
| Swift version | 5.9 | module podspecs |
| SwiftUI in app code | **None** | only inside Stripe Pods |
| Patches | 1 — `react-native+0.81.5.patch` (Hermes C++ headers) | `mobile-native/patches/` |

Two existing local Expo modules give us the exact template. `pulse-now-playing` shows
`Function` + `Events` + `OnCreate`; `pulse-video-mixer` shows `AsyncFunction` with a
`Promise`. JS side uses `requireOptionalNativeModule()`, which degrades gracefully when the
native module is absent — the right pattern for an availability-gated feature.

**We will introduce SwiftUI hosting to app code for the first time.** That is unavoidable:
Apple vends `TranslationSession` only through the SwiftUI `.translationTask` modifier.

### 5.1 Verified Apple API surface

Read from the real SDK interface, not from documentation or memory
(`iPhoneOS26.5.sdk/…/Translation.framework/…/arm64e-apple-ios.swiftinterface`):

**Available at iOS 18.0** — usable on P3r7or (iOS 18.7.3):

```swift
.translationTask(_ configuration: TranslationSession.Configuration?,
                 action: @escaping (_ session: TranslationSession) async -> Void) -> some View

TranslationSession.Configuration(source: Locale.Language?, target: Locale.Language?)
  var version: Int            // bumped by invalidate()
  mutating func invalidate()

session.translate(_ string: String) async throws -> Response
session.translate(batch: [Request]) -> BatchResponse        // AsyncSequence
session.translations(from batch: [Request]) async throws -> [Response]
session.prepareTranslation() async throws                   // triggers Apple's download UI

Request(sourceText: String, clientIdentifier: String? = nil)
Response { sourceLanguage, targetLanguage, sourceText, targetText, clientIdentifier }

LanguageAvailability()
  var supportedLanguages: [Locale.Language]                     // programmatic, not hardcoded
  func status(from: Locale.Language, to: Locale.Language?) async -> Status
  func status(for text: String, to: Locale.Language?) async throws -> Status   // auto-detect
  enum Status { case installed, supported, unsupported }

TranslationError
```

**NOT available at iOS 18.0** — gated behind iOS 26.0/26.4 and therefore unusable on the
verification device. This list is the most important output of the API audit:

| Symbol | Introduced | Consequence for our design |
|---|---|---|
| `session.cancel()` | iOS 26.0 | **Cancellation must use Swift `Task` cancellation**, not the session API |
| `session.isReady` | iOS 26.0 | Cannot pre-check readiness; must rely on `LanguageAvailability.status` |
| `session.canRequestDownloads` | iOS 26.0 | Cannot pre-check download permission |
| `TranslationSession(installedSource:target:)` | iOS 26.0 | Must go through `.translationTask` |
| `Strategy` / `preferredStrategy` | iOS 26.4 | No latency/fidelity tuning |
| `attributedSourceText` / `attributedTargetText` | iOS 26.4 | Plain `String` only |

Writing against any of those six on the assumption that "Translation framework = iOS 18"
would compile against SDK 26.5 and then be unavailable at runtime on the actual test
device. Availability must be checked per-symbol, not per-framework.

Three notes that shape the architecture:

- `Request.clientIdentifier` / `Response.clientIdentifier` is a first-class correlation
  channel. This is what makes Stage 7's "stale responses must not update recycled feed
  cells" solvable cleanly — we put our request id there and match it on the way back.
- `Configuration` is `Equatable` and carries a `version` bumped by `invalidate()`. SwiftUI
  re-runs `.translationTask` when the configuration changes, which is how a session is
  legitimately recycled per language pair.
- The `action` closure **is** the session's lifetime. The session is not storable outside
  it. A design that keeps a long-lived queue must keep that closure alive (awaiting a
  stream) rather than capture the session and return.

---

## 6. Wire contract to preserve

Five routes exist. The migration must not change their shapes, because the web client and
any older app build still use them.

- `POST /api/pulse/translations` — primary. Request `{content_type, content_ref, text,
  source_language, target_language, force}`. Response `{ok, result:{status, original_text,
  translated, cached, translated_text, source_language, target_language, provider,
  provider_model, policy, content_version, …}}`.
- `GET|PUT|POST /api/pulse/translations/preference` — `{source_language, target_language,
  policy}` where policy ∈ `ask|always|never`.
- `POST /api/translation/translate` — camelCase spec-shaped adapter returning
  `{ok, translatedText, detectedSourceLanguage, targetLanguage, provider, cached, requestId}`.
- `GET /api/pulse/translations/languages` — `{ok, provider, languages[]}`.
- `GET /internal/health/translation` — the health shape quoted in §1.

Client error taxonomy already exists in `src/api/translation.ts:60-81`: a `RETRYABLE_CODES`
set and a `PERMANENT_MESSAGES` map. The Apple error contract should extend this, not replace
it, so that retry affordances behave identically regardless of provider.

---

## 7. What the migration must therefore do

1. **Do not raise the iOS deployment target.** Weak-link `Translation.framework`, gate every
   call site on `@available(iOS 18.0, *)`, and ship `requireOptionalNativeModule()` on the JS
   side so iOS 15–17 and Android fall through to the existing cloud path unchanged.
2. **Insert the provider router beneath `translatePulseContent`, not beside it.** Because
   there is exactly one seam, all ten surfaces migrate in one edit and none can regress to
   calling Google directly.
3. **Never send `text` to the server on the Apple path.** The redundant client copy of
   private message bodies (§3.3) should stop being transmitted for on-device translations.
4. **Do not persist Apple-produced private-message translations server-side.** Today they
   land in `pulse_content_translations`; Stage 6 forbids it.
5. **Probe `ht` at runtime.** Do not claim Apple supports Haitian Creole until
   `LanguageAvailability.status` says so; route to controlled fallback otherwise.
6. **Add the missing cost controls** (per-user/device/IP rate limits, daily and monthly
   character budgets, circuit breaker) *before* relaxing `TRANSLATION_QA_ONLY`. The
   `always` policy auto-translating every mounting feed cell (§3.2) means opening the
   rollout without budgets would be the first time this system ever saw real volume.
7. **Fix, or explicitly accept, `supported_languages_available: false`.** Apple's
   `supportedLanguages` gives us a real list on-device, which sidesteps this for the Apple
   path but leaves the cloud path's catalogue still empty.

---

## 8. Protected-system check

Translation shares screens with protected realtime-media features — `ChatScreen.tsx` (2173)
and `ReelsScreen.tsx` (1265) both contain audio/video paths. Per the mission's absolute
protected-system rule, only the translation-specific code on those screens will be touched.
No file listed in `config/realtime-audio-protected-paths.json` is in scope, and the
realtime-audio diff gate
(`python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`) will be run
on the final diff to prove it.
