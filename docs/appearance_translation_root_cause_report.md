# Root-Cause Report — Appearance System + Google Cloud Translation

Mission: PulseSoc Appearance System + Translation Repair. Owner: ROODY CHERIE.
This report precedes all fixes, per REPORT_BEFORE_FIXING. Date: 2026-08-06.

## Part A — Appearance: what exists, what's missing

### What already exists (do not rebuild)

- `mobile-native/src/theme/ThemeContext.tsx` — a real runtime theme system:
  `ThemeProvider`, `useTheme()`, `useThemedStyles()`, `buildTheme()`. Two
  palettes (DARK canonical, LIGHT derived), high-contrast overrides,
  reduce-transparency, density, font scale.
- **Legacy bridge**: `applyPaletteToLegacyColors()` mutates the shared
  `theme/colors` object and `AppRoot` keys the navigation tree on
  `useThemeEpoch()`, remounting legacy screens whose StyleSheets captured
  colors at module scope. Any screen importing `theme/colors` follows a theme
  change today. This is the single most important fact in the audit: adding
  new themes propagates automatically to those screens.
- Persistence: `settings/store.tsx` — AsyncStorage snapshot (offline durable)
  + server sync via `/api/pulse/mobile/settings` when signed in, with
  rollback on sync error. This already satisfies "secure persistence,
  account sync, offline fallback".
- `screens/settings/AppearanceSettingsScreen.tsx` — theme selector
  (system/dark/light), font scale, density, transparency, live preview card.
- Motion: `logiNexusMotion.ts` `useLogiNexusReducedMotion()` +
  `storeMotion/businessLiveMotion` all honor Reduce Motion.
- Surface palettes: `storeLight.ts` + 9 derived commerce palettes
  (marketplace/ads/orders/messages/insights/events/hub/payments).

### Root causes of the gap

1. **Only 3 modes.** `ThemeMode` in `settings/schema.ts` is
   `"system" | "light" | "dark"`. No `black`, no `white`; the current light
   theme is effectively "light futuristic".
2. **Token consumption is thin.** Only 19 files call `useTheme()` (all
   settings surfaces). 91 files under `src/` contain raw hex literals; the
   worst offenders outside `theme/` are `ConversationControlCenter` (29),
   `CallScreen` (18), `ChatScreen` (16), `EngineerAccessModal` (16),
   `LiveHostSessionScreen` (14). Screens using literals directly (not via
   `theme/colors`) do not follow theme changes.
3. **No system chrome management.** `StatusBar` is set only in
   `SettingsShell`; `keyboardAppearance` is set nowhere. Dark keyboards on
   light themes and wrong status-bar content are guaranteed today.
4. **Navigation is unthemed.** `NavigationContainer` receives no theme, so
   transition backgrounds/headers can flash the wrong scheme.
5. **Galactic backgrounds are ad-hoc.** No `GalacticAtmosphere` component
   exists; galactic visuals live individually in `LoginBackground`,
   `HomeScreen`, `ReelsScreen`, `ChatScreen`, `ProfileScreen`,
   `GalacticConstructionScreen`. There is no per-theme background profile
   token.
6. **Startup theme timing** must be verified: settings hydrate from
   AsyncStorage; whether the first frame waits for hydration decides the
   flash-on-launch behavior.

### Constraint

`CallScreen.tsx`, `LiveScreen.tsx`, `LiveHostSessionScreen.tsx`,
`ReelLiveViewerSurface.tsx` are in `config/realtime-audio-protected-paths.json`.
Any styling change there must pass
`scripts/realtime_audio_change_gate.py` and must be purely visual. These
surfaces are migrated last, alone in their own change, or deferred.

### Files to change (Part A)

- `settings/schema.ts` — extend `ThemeMode` to
  `"system" | "dark" | "light_futuristic" | "black" | "white"`, normalize
  legacy `"light"` → `"light_futuristic"`, default stays `"dark"`.
- `theme/ThemeContext.tsx` — add BLACK and WHITE palettes, rename LIGHT →
  LIGHT_FUTURISTIC (keep export), extend `Theme` with `statusBarStyle`,
  `keyboardAppearance`, `galacticBackground` profile; scheme resolution
  (black→dark chrome, white/light_futuristic→light chrome).
- `App.tsx` / AppRoot — render `<StatusBar>` from theme; pass a derived
  navigation theme to `NavigationContainer`; verify hydration-before-first-
  frame.
- `screens/settings/AppearanceSettingsScreen.tsx` — four theme options with
  live previews.
- Migration of hex-literal screens to `useTheme()`/`theme/colors` —
  incremental, highest-traffic first; audio-protected surfaces last/gated.

## Part B — Translation: what exists, what's broken

### What already exists (complete pipeline, tested)

- Native: `api/translation.ts` (client), `components/ContentTranslation.tsx`
  (tap-to-translate, loading, in-flight dedupe, "show original" toggle,
  per-language policy), `TranslationPreferencesBootstrap`,
  `LanguageSettingsScreen`.
- Backend: three routes on `bot.py` — `POST /api/pulse/translations`
  (~line 6713), `GET/PUT /api/pulse/translations/preference` (~6747),
  `GET /api/pulse/translations/languages` (~109327) →
  `services/content_translation.py` (authorization by content ref, 4000-char
  bound, moderation before/after, URL/email/hashtag protection, per-user
  cache table `pulse_content_translations`, event log, typed
  `TranslationError` codes) → `services/translation_providers.py`
  (`GoogleAdvancedProvider`, Translation **v3 REST**, service-account JSON
  env blob or API key).
- Health: `/health` includes `translation` via `health_status()`.
- Tests: `tests/test_content_translation.py` (13), native
  `translation.test.ts` + `ContentTranslation.test.tsx`.

### Root causes of breakage (ranked)

1. **Feature flag off**: `TRANSLATION_ENABLED=false` is the default. If
   Railway doesn't set it `true`, every request 503s.
2. **QA gate on**: `TRANSLATION_QA_ONLY=true` default → 403
   `rollout_restricted` for anyone not in `TRANSLATION_QA_USER_IDS`.
3. **Credentials unset**: `configured` requires `GOOGLE_CLOUD_PROJECT_ID`
   AND (`GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON` or
   `GOOGLE_CLOUD_TRANSLATION_API_KEY`). The API key var is documented at a
   distant line (512) of `.env.example`, easy to miss.
4. **Per-request client + credential refresh**: `configured_provider()`
   builds a new provider each call; service-account token is refreshed on
   every request (blocking I/O, quota pressure). Needs a cached client with
   token reuse.
5. **Env keys documented but dead**: `TRANSLATION_PRESERVE_ORIGINAL`,
   `TRANSLATION_SHOW_ORIGINAL_OPTION`, `TRANSLATION_USER_OVERRIDE_ENABLED`,
   `TRANSLATION_GLOSSARY_ENABLED`, `TRANSLATION_FAIL_OPEN` are in
   `.env.example` but read nowhere.
6. **No canonical `/api/translation/translate`**: the mission's contract
   (camelCase fields, `TRANSLATION_UNAVAILABLE`-style codes, `requestId`,
   `retryable`) does not exist. The existing `/api/pulse/translations`
   contract is live, tested, and used by the shipped native client.

### Decision on the endpoint contract

Breaking `/api/pulse/translations` would break every installed app build.
Plan: add `POST /api/translation/translate` as a canonical adapter over
`content_translation.translate_content()` implementing the mission's
request/response shape (spec error codes mapped from `TranslationError`
codes, `retryable`, `requestId`), plus `GET /internal/health/translation`.
The existing routes stay untouched.

### Files to change (Part B)

- `bot.py` — add canonical route + internal health route (registered in the
  same style as the existing three).
- `services/translation_providers.py` — module-level cached provider +
  cached service-account credentials (refresh only when expired).
- `.env.example` — group `GOOGLE_CLOUD_TRANSLATION_API_KEY` with the rest;
  remove or implement dead keys; comment the Railway checklist.
- Native `api/translation.ts` / `ContentTranslation.tsx` — retryable-aware
  failure copy ("Translation is temporarily unavailable." + Retry),
  bounded backoff on transient failures only.
- Tests for the canonical route mapping.

### What cannot be verified from this environment

- Railway variable values (which credential method is actually set, whether
  `TRANSLATION_ENABLED` / `TRANSLATION_QA_ONLY` are overridden) — requires
  ROODY's Railway dashboard.
- Live Google API calls, physical-device QA, visual regression on device.
No completion claim will be made for those steps from code inspection alone.

---

## Implementation record + evidence (2026-08-06)

### What was implemented

**Part A — Appearance**

- Canonical theme system in `mobile-native/src/theme/` with modes
  `dark | light_futuristic | black | white | system`, semantic tokens,
  `statusBarStyle`, `keyboardAppearance`, and a per-theme
  `galacticBackground` profile. Legacy stored `"light"` migrates to
  `"light_futuristic"` on load.
- Settings → Appearance → Background & Theme: live preview, immediate apply,
  persisted selection.
- First-frame correctness: `ThemeProvider` publishes the palette to the legacy
  mutable `colors` object synchronously in the render phase
  (`useMemo(() => applyPaletteToLegacyColors(theme.colors), [theme.colors])`),
  so no dark flash before hydration.
- Module-scope StyleSheet freeze (98 legacy files) solved by
  `src/theme/themedStyles.ts` — `createThemedStyles(factory)` returns a Proxy
  that lazily builds the sheet and rebuilds when the palette epoch advances
  (bumped by `applyPaletteToLegacyColors`). A codemod converted **92 files**
  (`grep -rln "createThemedStyles(() => ("` → 92); the 6 audio-protected files
  (`CallScreen`, `ChatScreen`, `LiveHostSessionScreen`, `LiveScreen`,
  `MusicScreen`, `ReelLiveViewerSurface`) were deliberately skipped and remain
  dark-styled full-screen media surfaces — follow-up item, not a regression.
- `GalacticAtmosphere` now reads `useTheme().galacticBackground`: White renders
  nothing, Black dims the whole layer via `intensity`, light themes swap to a
  bright haze gradient + darker stars. Motion still pauses for Reduce Motion,
  Low Power Mode, and background app state.

**Part B — Translation**

- `services/translation_providers.py`: SHA-256-keyed service-account credential
  cache (token reused ~1h, refresh only when invalid; tolerates escaped `\n` in
  `private_key`); provider instance cache in `configured_provider()` (no more
  per-request construction); final-attempt HTTP mapping → typed `ProviderError`
  codes (429 → `provider_quota_exceeded`, 401/403 → `invalid_credentials`,
  timeout → `provider_timeout`, connection error → `provider_unavailable`) with
  `retryable` flags.
- `services/content_translation.py`: `TranslationError` carries `retryable`;
  provider error codes propagate instead of collapsing to
  `translation_unavailable`.
- `bot.py`: canonical `POST /api/translation/translate` — camelCase payload,
  server-side content resolution (`text=None`, caller text never trusted),
  spec response `{ok, translatedText, detectedSourceLanguage, targetLanguage,
  provider, cached, requestId}`, spec failure codes via
  `TRANSLATION_SPEC_ERROR_CODES` + `retryable` + `requestId`. Plus
  `GET /internal/health/translation` (supports `?probe=1`).
- `.env.example`: Railway go-live checklist atop the translation block;
  `GOOGLE_CLOUD_TRANSLATION_API_KEY` grouped with it.
- Native: `classifyTranslationFailure()` in `src/api/translation.ts`
  (retryable-code set + permanent-message map); `ContentTranslation.tsx` does
  bounded auto-retry (2 attempts, 600ms/1200ms backoff) on transient failures
  only, and shows a Retry affordance only when `failure.retryable`.

### Verification evidence (sandbox)

- `tsc --noEmit` — clean.
- `npm run i18n:validate` — OK, 11 locales.
- Jest full suite via 8 shards (`npx jest --silent --shard=N/8`):
  **175 suites / 3,115 tests, all passing**.
- `python3 -m ast` parse of `bot.py`, `services/translation_providers.py`,
  `services/content_translation.py` — clean.
- Existing `tests/test_content_translation.py` assertions checked: none pin
  the old collapsed error code, so code preservation is compatible.

### Must be verified in ROODY's environment (no completion claim here)

1. **Backend pytest** — this sandbox has no PyPI access, so Flask-dependent
   tests could not run. Run `pytest tests/test_content_translation.py` (and
   the protection suite) locally.
2. **Railway variables** — set per the `.env.example` checklist, then hit
   `GET /internal/health/translation?probe=1` on the deployed app to confirm a
   live Google round-trip.
3. **Audio gate** — after committing, run
   `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.
   Note: `MusicScreen.tsx` (protected path) carries Mission B's intentional
   profile-context change — no audio APIs touched; this session's codemod
   skipped all protected files.
4. **Device QA** — the four themes (status bar, keyboard appearance, galactic
   backgrounds, no cold-start flash) and translation entry points
   (post/comment/chat/marketplace) on a physical device.
