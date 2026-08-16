# Home Discovery Suggestions — Rollback & Delivery Record

Branch: `feature/spatial-console`. Commits `7e8e5e44` (engine, sources, view) and
`fec0bda3` (Home wiring).

## Rollback is a flag flip, never a revert

The five shipped flags default **ON**; the three unfinished ones default **OFF**.
Rolling back means setting `EXPO_PUBLIC_HOME_DISCOVERY=0`, at which point:

- `homeDiscoveryEnabled()` is `false`,
- `loadDiscoveryModules()` returns `{ modules: [] }` without issuing a request,
- `injectDiscoveryRows(rows, [])` returns **the same array elements it was
  given** — identity, not deep equality,
- so Home renders exactly the rows `injectAds` produced before this feature
  existed.

### Why the defaults inverted

This shipped with all eight flags defaulting OFF, and the §18 device build that
proved the feature worked was made by exporting five of them by hand at the
shell. Nothing in the repo exported them — no profile in `mobile-native/eas.json`
sets any `EXPO_PUBLIC_HOME_DISCOVERY*` var, there is no `.env`, and `.gitignore`
excludes `.env` and `.env.*`, so there was nowhere committed for them to live.

The result was that the suggestion rows existed in exactly one build. The next
build, made for an unrelated Reels fix and deliberately made with an empty
environment, dropped them, and the rows were reported as having "come undone."
Nothing had been reverted; the code was present and correct in a build that did
not show it.

That is the same failure `mobile-native/src/core/envFlag.ts` documents for the
Reels pager and the immersive navigator, and the fix is the same: a feature that
has finished rolling out reads through `isFlagValueOnUnlessDisabled`, so silence
means on and turning it off costs somebody a deliberate `=0`. Rollback is still a
flag flip and never a revert — the flip just runs in the direction that should
cost an action. Pinned by `discovery/__tests__/flags.test.ts` → "a build that
sets no discovery variables at all".

The identity property is the one that matters and the one that is easy to break
silently. `PostCard` and `SponsoredAdCard` are memoized on the row objects; a
placement engine that mapped over its input and returned copies would re-render
the whole visible feed on every recomputation *with the feature turned off*, and
nothing in review would show it. It is pinned by
`discovery/__tests__/homeComposition.test.ts`:

```ts
composed.forEach((row, index) => expect(row).toBe(base[index]));
```

### The flags

| Env var | Gate | Default | To change it |
|---|---|---|---|
| `EXPO_PUBLIC_HOME_DISCOVERY` | master | **ON** | `=0` disables everything |
| `EXPO_PUBLIC_HOME_DISCOVERY_REELS` | Reels for you | **ON** | `=0` |
| `EXPO_PUBLIC_HOME_DISCOVERY_PEOPLE` | People you may know | **ON** | `=0` |
| `EXPO_PUBLIC_HOME_DISCOVERY_STATUSES` | Statuses | **ON** | `=0` |
| `EXPO_PUBLIC_HOME_DISCOVERY_GROUPS` | Groups | **ON** | `=0` |
| `EXPO_PUBLIC_HOME_DISCOVERY_CREATORS` | Creators to follow | OFF — no source | needs an endpoint first |
| `EXPO_PUBLIC_HOME_DISCOVERY_TOPICS` | Topics | OFF — no destination | needs a screen first |
| `EXPO_PUBLIC_HOME_DISCOVERY_SPONSORED` | Sponsored carousel | OFF — see below | needs a frequency-cap review |

Which reader a line in `FLAG_READERS` uses *is* the default, so there is no
separate table in the source to fall out of sync with this one:
`isFlagValueOnUnlessDisabled` for the five, `isFlagValueOn` for the three.

Every module flag is ANDed with the master, so setting
`EXPO_PUBLIC_HOME_DISCOVERY=0` disables all seven regardless of their own values.
Off is spelled `0`, `false`, `off` or `no`, case- and whitespace-insensitive;
anything else — including a typo like `flase` — leaves a shipped flag on, so a
misspelling in a build profile cannot silently delete the feature. A source that
throws still yields no module rather than a partial one.

The reads are written as one static `process.env.EXPO_PUBLIC_X` member
expression per flag. That form is load-bearing. `babel-preset-expo` inlines
these only when the key is a literal, and a Release bundle has no populated
`process.env` at runtime — a computed lookup reads `undefined` for every flag on
device while passing every jest test. This repo has already shipped that bug
once; see the note in `spatial/flags.ts`.

## What is NOT shipped

Three of the seven module types in the mission are **built but deliberately
sourceless**, because the mission forbids inventing destinations (§9) and
forbids inventing ad architecture (§10):

- **Topics** — topics exist only as the server-rendered `/pulse/topic/<topic>`
  page. The mobile app has no topic screen, so a topic card would be a tap that
  goes nowhere. Turning this flag on requires building that destination first.
- **Sponsored** — Home already places sponsored cards via `injectAds`, which has
  its own cadence and its own viewability accounting. A second sponsored surface
  must not appear without someone re-checking the frequency caps against both
  paths.
- **Creators to follow** — no ranked creator endpoint exists that is distinct
  from People. Shipping it fed by the People source would be the same row twice
  under two headings.

The row shell renders nothing for a kind with no approved card, so these three
are inert even if their flags are set. Pinned by
`DiscoveryRowView.test.tsx` → "renders nothing for a kind with no approved card".

## Rolling back

1. Set `EXPO_PUBLIC_HOME_DISCOVERY=0` in the EAS profile / Railway env for the
   build in question. *Removing* the variable is no longer a rollback — unset
   now means on, which is the whole point of the previous section.
2. Rebuild. There is no server-side component to revert and no schema change.
3. Verify: Home's rows are `injectAds` output, and `HomeScreen.discovery.test.ts`
   still passes — it asserts the composition order and the §1 preservation set
   (Pulse Network hero, status rail, composer, feed tabs, refresh control, the
   shared `keyExtractor`) directly against the source.

No commit needs to come out. Reverting `fec0bda3` is *not* the rollback path and
should not be used as one: it would also remove the tests that pin the
untouched-Home guarantees.

## Device QA build

The first §18 QA build was made with five flags exported by hand at build time
(master, reels, people, statuses, groups), without modifying the repo's shipped
defaults. That is precisely what made the feature disappear from the next build;
see "Why the defaults inverted" above.

Builds after the default inversion need **no environment at all**. A plain
`xcodebuild` with an empty environment now produces the shipped experience, which
is the property being tested — if a QA build needs a variable exported to show a
finished feature, the default is wrong, not the build.
