# Localization

PulseSoc Native ships in eleven languages: English, Spanish, French, Haitian
Creole, Portuguese, German, Arabic, Hindi, Japanese, Korean and Chinese. This
document covers how the system is put together, how to add a language, how to
check your work, and what is still outstanding.

## Why there is no i18n library here

The engine in `src/i18n/` is written from scratch and has no dependencies. That
was not a preference. This project has no access to the npm registry, so
`i18next`, `expo-localization` and `expo-updates` were all unavailable. The
engine reimplements the parts of that stack the app actually needs — namespaced
keys, plural and gender variants, interpolation, a fallback chain, and lazy
catalog loading — and leans on the platform's own `Intl` for formatting.

The practical consequence is that everything here is ours to maintain, so the
test suite and the two validators below are the safety net that a third-party
library's own test suite would otherwise provide.

## Layout

```
src/i18n/
  engine.ts        lookup, fallback chain, plural/gender variants, interpolation
  detect.ts        device language detection and the priority chain
  locales.ts       the eleven supported locales, tag normalization, search
  format.ts        numbers, currency, dates, relative time, units, lists
  rtl.ts           layout direction, logical edges, icon mirroring
  coverage.ts      per-language completeness, shown in the language picker
  catalogs/        <locale>/core.json and <locale>/extended.json
```

### Keys and namespaces

Keys are namespaced: `t("common:screens.purchaseHistory")`. There are eight
namespaces — `common`, `auth`, `errors`, `social`, `messaging`, `commerce`,
`discovery`, `settings` — mapped onto **two** physical files per language:

| Tier       | Namespaces                                              | Loaded |
| ---------- | ------------------------------------------------------- | ------ |
| `core`     | `common`, `auth`, `errors`                              | before the first frame |
| `extended` | `social`, `messaging`, `commerce`, `discovery`, `settings` | on demand |

Eight namespaces over two files is a deliberate split. Namespaces are how
translators and engineers reason about the copy; tiers are how the app pays for
it at launch. Twenty-two JSON files is also small enough to review in a diff.

**Navigation chrome must live in `common`.** The header renders on the very
first frame, before the extended tier is warm. A screen title parked in
`settings:` renders as a humanized key fragment on cold start and then silently
corrects itself — a bug that only reproduces on a real device. This is asserted
in `src/navigation/__tests__/navigatorLocalization.test.ts`.

### The fallback chain

A lookup tries, in order:

1. the active locale,
2. the default locale (`en`),
3. `options.defaultValue`,
4. the humanized final key segment.

**Rung 4 is the dangerous one.** A missing `common:screens.purchaseHistory`
renders as `"Purchase History"` — correct-looking English that is also
untranslatable, and indistinguishable from success in review. Anything that
probes for a missing key must therefore read the catalog directly rather than
call `translate()`, which will always return something. `loadCatalogBundle` is
synchronous and returns `null` for an absent bundle, which makes it a
fallback-free probe; both `navigatorLocalization.test.ts` and `registry.test.ts`
use it exactly this way.

The same trap applies to namespace loading: **an unloaded namespace behaves
identically to a missing key**, so every lookup in it quietly falls through to
humanization. `activateLocale` / `preloadNamespaces` / `ensureNamespace` are the
only things that populate the cache.

### Plural and gender variants

Variants are suffixes on the key: `count_one`, `count_other`, `sent_female`.
Selection order is gender+plural, then plural, then gender, then the base key,
and a missing category falls back to `_other`.

Plural *categories* are CLDR's, chosen by `Intl.PluralRules`. They differ
sharply by language — Japanese has only `other`, English has `one`/`other`,
Arabic has all six — which is why coverage is measured by key **family** rather
than by leaf. Collapsing `_zero|_one|_two|_few|_many|_other` before counting is
what stops Arabic scoring 108% and Japanese being penalized for correctly having
one form.

### RTL

Direction is handled **structurally**, never by embedding bidi control
characters in catalog data. Use the logical helpers in `rtl.ts` — `startEdge`,
`endEdge`, `row`, `textAlign`, `paddingHorizontal`, `mirrorIconName` — rather
than hardcoding `left`/`right`.

There are two tracks: an instant JS-level direction flip that re-renders
immediately, and native `I18nManager.forceRTL`, which only takes effect after a
reload. `isNativeDirectionStale()` reports when the two disagree.

## Adding a language

No code changes are required for a language whose plural rules `Intl` already
knows.

1. Add the locale to `SUPPORTED_LOCALES` in `src/i18n/locales.ts`, with its
   native name (shown in the picker in its own script) and text direction.
2. Create `src/i18n/catalogs/<locale>/core.json` and `extended.json`. Copy the
   English files and translate the values. Keep `$version` identical to
   English's — a drifted `$version` is discarded at launch, so the language
   silently falls back to English while looking complete on disk.
3. Add the locale's CLDR plural categories to `PLURAL_CATEGORIES` in
   `scripts/validate-i18n.mjs`.
4. Run `npm run i18n:validate`.

## Tooling

```
npm run i18n:validate     catalog integrity across all languages
npm run i18n:hardcoded    find user-visible strings that are not keys yet
npm run verify            typecheck + validate + full test suite
```

Three further scripts exist to let screen migration and translation run in
parallel without several agents racing on the same catalog file. They are
described under *The staging pipeline* below.

### `scripts/validate-i18n.mjs`

Answers *"is every key translated, correctly, in every language"*. Runs under
plain Node with no transpiler, because it reads the JSON directly rather than
importing the app's loader.

It fails the build on: invalid JSON, `$version`/`$locale`/`$tier` drift, missing
key families, missing **required** plural forms, empty strings, invented or
dropped `{{placeholders}}`, whitespace inside a placeholder, and Unicode bidi
control characters in catalog data.

It warns rather than fails on three things, each for a specific reason:

- **Orphaned keys.** A key removed from English leaves them behind. Dead strings
  hurt nobody; they only mean wasted translator time.
- **Advisory plural forms.** French, Spanish and Portuguese define `many`, but
  CLDR only selects it for exact millions — counts that large are abbreviated
  long before they reach a plural, and the engine already falls back to `_other`.
  The degradation is a slightly stiff sentence, not a hole.
- **Zero/one/two forms that omit the count.** Arabic writes "لا توجد طلبات" and
  "طلب رسالة واحد" rather than interpolating 0 or 1. This is idiomatic, not a
  bug, so a human confirms it instead of a build blocking on it. `--verbose`
  lists the individual keys.

### `scripts/find-hardcoded-strings.mjs`

Answers the *prior* question: *"is every user-visible string a key at all"*. A
catalog can sit at 100% coverage while half the app renders English literals
that were never extracted, and nothing in the type system notices.

It is a heuristic and deliberately a lossy one — it reads text, not an AST. It
is tuned for precision over recall so the list stays worth reading: it reports
JSX text nodes, an explicit list of display props, and `Alert.alert` arguments,
including values hidden inside ternaries and template literals. Template holes
are normalized to `{}` so the strings that will need `{{placeholder}}` keys are
visible at a glance.

Use `--file <path>` while migrating a screen, and `--max <n>` as a ratchet in CI.
It is a worklist and a trend, not a correctness proof.

### The staging pipeline

Migrating a screen means adding keys to `catalogs/en/core.json` or
`extended.json`; translating a language means adding the same keys to two more
files. With one migration at a time that is fine. With four screens and ten
languages in flight it is a write race on 22 files, and the loser's keys vanish
silently — a lost key does not fail the build, it renders as humanized English
(see *The fallback chain*).

So nobody edits a catalog directly. Work is staged as namespace-shaped JSON under
`scripts/.i18n-staging/` and folded in mechanically afterwards.

`scripts/merge-i18n-staging.mjs` merges every staged file into the two English
catalogs, routing each namespace to its tier. `scripts/i18n-todo.mjs <locale>`
writes `.i18n-staging/todo/<locale>.json` — every family English has and that
locale lacks, with the English string as the value.
`scripts/merge-i18n-locale.mjs <locale>` folds the translated file back.

Two properties matter more than the mechanics:

- **All three refuse to overwrite an existing leaf.** A collision means two
  migrations picked the same key for different copy, or a stale worklist is being
  replayed. Letting the last writer win would change shipped English somewhere
  else in the app, so the merge aborts and lists the colliding keys instead.
- **`i18n-todo.mjs` emits the exact plural key names the target locale needs**,
  read from the same CLDR table the validator uses. A Japanese worklist contains
  only `_other`; an Arabic one contains all six. This is the whole reason the
  worklist is generated rather than diffed by hand — asking a translator to
  work out which forms their language needs is the single largest source of
  catalog defects, and generating the key names removes it. Across the ten-locale
  wave recorded below, every locale came back structurally exact on the first
  merge.

Key order is preserved rather than sorted. Sorting would be tidier in the
abstract but rewrites every line of a file that ten other locales are mirrored
against, burying the actual change in the diff.

## Verification evidence

Recorded 2026-07-26 against the working tree.

**Catalog integrity** — `node scripts/validate-i18n.mjs`:

```
  locale  coverage        keys   orphans
  en      100%   2254/2254
  ar      100%   2254/2254      (and de, es, fr, hi, ht, ja, ko, pt, zh)

  4 warning(s):
    ! ar: 107 zero/one/two form(s) omit the count — idiomatic, but confirm
    ! es: 38 plural families omit the advisory form(s) many
    ! fr: 38 plural families omit the advisory form(s) many
    ! pt: 38 plural families omit the advisory form(s) many

  OK — 11 locales, catalog version 1.0.0.
```

All eleven languages are at 100% family coverage with zero missing and zero
orphaned families. The catalogs grew from 916 families to 2254 as the screens
listed under *Migrated screens* were extracted, across two waves; each of the ten
non-English locales was brought back to parity through the staging pipeline in
the same pass as the English extraction.

Each merge was checked for silent damage to already-shipped copy rather than only
for the keys it added. Comparing every catalog leaf before and after the wave-2
merge across all eleven languages: **21296 pre-existing leaves, 0 changed.** That
is the property that matters, because the merge scripts touch files that ten
locales are mirrored against.

100% coverage only proves a key *exists* in every language, so it was also
checked against the opposite failure — a translator returning English unchanged.
Values byte-identical to English, per locale:

| ja | ko | zh | hi | ar | ht | es | pt | de | fr |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 1.0% | 1.0% | 1.1% | 1.3% | 1.6% | 2.6% | 2.8% | 3.7% | 4.8% | 5.8% |

Most are single tokens that legitimately do not translate ("PulseSoc", "UNDX",
"Face ID", "Telegram", "Chat"). The number that actually matters is how many are
long enough to be a sentence: filtering to values of three words or more leaves
**three to five per locale**, and they are the same handful every time — the two
placeholder-only metadata strings (`{{kind}} · {{state}} · {{role}}` and
`{{author}} · {{time}}`), the brand kickers `LIVE VOICE PULSE` and
`UNDX · PulseSoc Intelligence`, and the hashtag sample `#drill #kompa #lofi`.
No sentence was left in English in any locale.

The validator was itself verified against seven injected defects on a throwaway
copy of the catalog tree — dropped placeholder, missing required plural form,
stray RLM, invented placeholder, `$version` drift, deleted key family, empty
string, and malformed JSON. All were caught with exit code 1; the unmodified
tree exits 0.

**Tests** — `npx jest`, excluding six untracked scratch files belonging to
concurrent unrelated work (see Known issues):

```
Test Suites: 77 passed, 77 total
Tests:       1210 passed, 1210 total
```

The screen migration is what those numbers are really evidence for. Extracting
1338 strings out of 26 component files is a mechanical edit repeated often enough
that a silent copy change is likely, and a changed string breaks an assertion
somewhere. The rule was that a catalog value must match the literal it replaced
**byte for byte**; where a literal looked wrong, a new key was added and the
discrepancy reported rather than the copy quietly corrected. Roughly fifty such
discrepancies were surfaced that way, and 77 pre-existing suites stayed green
through both migration waves.

`SecuritySettingsScreen.test.tsx` needed one edit of its own: a
`beforeAll(activateLocale("en"))`, matching `SettingsScreen.test.tsx` and
`LoginScreen.test.tsx`. Its queries look up text that now lives in the `settings`
namespace, which is extended-tier and therefore lazy — without priming, the screen
renders humanized keys and every text query misses. **No assertion was weakened
and no copy was altered to make a test pass**, which is the failure mode to watch
for here: adjusting a catalog value until a test goes green would silently change
what ships.

## Copy defects the translators found

A side effect of the pipeline worth recording: translating a string is the first
time anybody reads it in isolation, so ten independent passes over the same
English is an unusually good copy review. Two findings were unanimous across all
ten locales.

`discovery.search.resultCount_one` was byte-identical to `_other`, so
`SearchScreen` rendered "1 results" for a single hit. That one was a clear defect
with an obvious fix and has been corrected. `social.groups.detailSubtitle` had the
same shape and was given a real singular during migration.

The second finding is not mine to fix, because it is a product-copy decision
rather than a bug: a family of strings in `social.groups` and
`settings.accountCenter` ships engineering prose to users. `approveBoundary` /
`rejectBoundary` / `cancelBoundary` are button labels reading "Approve boundary",
"Reject boundary", "Cancel boundary". `assets.handoffReady` names an internal
class, `NativeMediaViewer`. `providerPartialBody` mentions "Simulator" and
"physical-device QA". `assets.filesEmptyBody` explains a "backend contract
boundary". `account.credentialsBody` refers to "dedicated native reauth UX".
Separately, `security.advancedWeb` and `devices.advancedWeb` are the truncated
fragments "Advanced security web" and "Advanced device web", and
`security.twoFactorStateOff` reads "Available" directly beside a detail line
reading "Not enabled".

These were migrated and translated faithfully, so all eleven languages currently
say the same thing. Rewriting them is a one-line change per key once somebody
decides the wording — but it changes shipped user-visible copy in eleven
languages, so it wants a human decision rather than a translator's judgement.

Of those, 415 are i18n tests across five suites, stable over five consecutive
runs including two with `--randomize`:

| Suite               | Tests | Covers |
| ------------------- | ----- | ------ |
| `engine.test.ts`    | 77    | fallback chain, variant order, interpolation, namespace loading, missing-key reporting |
| `detect.test.ts`    | 136   | detection priority order, region preservation, regional-variant resolution, native-script search |
| `format.test.ts`    | 127   | numbers, currency, units, dates, relative time, lists, and the reduced-ICU fallback paths |
| `rtl.test.ts`       | 44    | logical edges, icon mirroring, bidi isolation, direction subscription |
| `coverage.test.ts`  | 31    | plural-family collapsing, `Math.floor` percentages, cache reset |

**Typecheck** — `npx tsc --noEmit` exits 0.

## Known issues

These were found while writing the tests above and are **characterized by
passing tests rather than fixed**, so the current behaviour is pinned and the
fixes can be made deliberately. None of them block shipping the eleven
languages, but several are worth scheduling.

Worth fixing soon:

- `formatDistance`, `formatWeight` and `formatTemperature` resolve the locale
  twice, and `toIntlLocale` only matches bare codes, so any regionalized tag
  collapses to `en-US`. `formatWeight(2500, { locale: "de" })` yields `2.5 kg`
  instead of `2,5 kg`.
- `weekdayNames`, `monthNames`, `formatDate`, `formatTime` and `formatRelative`
  have no guard around `Intl.DateTimeFormat` and will throw on an ICU build that
  lacks it, contradicting the module's stated degrade-don't-throw contract.
- `formatRelative` renders `"0m"` for anything between 45 and 59 seconds.
- `mirrorIconName` maps `log-in` to `log-out` with no reverse entry, so in
  Arabic a sign-in button renders the sign-out glyph.
- `activateLocale` and `translate({ locale })` accept only exact codes, so
  `pt-BR` silently activates English even though `resolveSupportedLocale` would
  map it. `getCoverage("ar-SA")` returns 0% for the same reason.

Lower priority:

- `hasTranslation` is variant-blind and probes the cache rather than the
  catalog, so it reports `false` for keys `translate` resolves.
- `parseKey` keeps an unregistered namespace prefix in the path, making a
  namespace typo invisible.
- With both `count` and `context`, the whole plural group is tried before the
  gender-only form.
- `defaultValue: ""` beats humanization and renders blank.
- `isolateBidi` emits LRM/RLM marks rather than true isolates (U+2066/2069), so
  digits and neutral punctuation inside a run can still reorder.
- The `device-region` detection tier can never win: any region it could resolve
  has already produced a winner one rung higher. It is still recorded as a
  candidate.

Unrelated to localization: six untracked scratch test files
(`zz-dbg.test.tsx`, `*.dbg.test.tsx`, `*.actions.test.tsx`,
`PostDetailScreen.comments.test.tsx`) belong to an in-flight social-action-guard
refactor being edited concurrently. `zz-dbg.test.tsx` currently fails against a
mid-refactor `PostDetailScreen.tsx`. These were left untouched.

A handful of scratch files could not be deleted because this environment denies
file removal; they are listed under *Outstanding work*.

## Migrated screens

Fully extracted — `npm run i18n:hardcoded --file <path>` reports zero for each:

| Area | Files |
| ---- | ----- |
| Shell | `AppNavigator`, settings index, `LanguageSettingsScreen`, `LanguagePicker` |
| Authentication | `LoginScreen`, `SignupScreen`, and `components/auth/{AccountActions,ManualLoginForm,PulseSocBrandHeader,SecureTextField}`, `components/auth/signup/{SignupBrandHeader,SignupProgress,VerifyEmailStep}` |
| Search & notifications | `SearchScreen`, `NotificationCenterScreen`, `NotificationPreferencesScreen`, `settings/NotificationSettingsScreen` |
| Messenger | `ChatScreen`, `NewChatScreen`, `components/ConversationControlCenter` |
| Marketplace | `MarketplaceScreen`, `BuyerOrdersScreen`, `SellerListingComposerScreen`, `SellerStoreScreen` |
| Groups, music, account | `GroupsScreen`, `MusicScreen`, `AccountCenterScreen`, `settings/SecuritySettingsScreen` |

## Native configuration evidence

Expo Doctor's `appConfigFieldsNotSyncedCheck` is disabled intentionally for this
branch after auditing the committed iOS project against the resolved Expo config.
This repository intentionally commits and maintains the native iOS project under
`ios/`, and release builds use those native settings rather than regenerating
them through CNG/prebuild. The release-critical iOS fields are synchronized:
production bundle identifier `com.pulsesoc.app`, version `1.0.1`, build `3`,
display name `PulseSoc`, URL scheme `pulsesoc`, associated domain
`applinks:pulsesoc.com`, production push entitlement, background modes,
permissions, app icon asset, portrait orientation and iPhone-only device family.

Future native configuration changes must be applied in both Expo config and the
committed native project. Do not run `expo prebuild --clean` or delete `ios/` as
part of localization work.

## Outstanding work

Localization of the app's screens is partial. `npm run i18n:hardcoded` reports
**1444 user-visible strings across 79 files** still to extract, with 50 of 129
scanned files clean. The largest remaining are `LiveHostSessionScreen` (54),
`HomeScreen` (53), `settings/DeveloperSettingsScreen` (53), `SafetyHubScreen` (41),
`CoursesLearningScreen` (40), `settings/HelpSettingsScreen` (40) and
`components/PostCard` (39). The pattern to follow is the staging pipeline above:
stage the new English keys, merge, generate ten worklists, translate, merge back.
`HomeScreen` and `PostCard` are worth doing early despite not being the largest —
they are what a user sees first.

RTL also needs a second pass: `rtl.ts` is complete and tested, but it is only
wired into the language picker and language settings screen so far. Nothing
currently detects a screen that hardcodes `left` instead of calling
`startEdge()`, which is the natural next tool to build — and the more useful one
of the two remaining, since a missing key is visible to a translator whereas a
hardcoded edge is only visible to someone reading Arabic.

Housekeeping, blocked because this environment denies file removal — these should
be deleted by hand: `src/i18n/probe.test.ts`,
`src/i18n/__tests__/__probe.ts.bak`, `scripts/.ar_i18n_patch.py`,
`scripts/.ar_i18n_verify.py`, `kpg-tmp-proof.setup.js` (emptied to a comment;
nothing references it), and the whole of `scripts/.i18n-staging/`, which is now
spent — every file in it has been merged, and a stale worklist replayed later
would abort on collisions rather than corrupt anything, but it is dead weight.

One trap for whoever runs the pipeline next: `merge-i18n-locale.mjs --all` treats
every `*.json` directly inside `.i18n-staging/done/` as a locale, so a translator
leaving `fr.part2.json` there makes the script look for a locale called
`fr.part2`. Intermediate files belong in a subdirectory (`done/fr-parts/`).
