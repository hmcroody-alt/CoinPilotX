# Home Discovery Suggestions — Rollback & Delivery Record

Branch: `feature/spatial-console`. Commits `7e8e5e44` (engine, sources, view) and
`fec0bda3` (Home wiring).

## Rollback is a flag flip, never a revert

All eight flags default OFF. With no env vars set:

- `homeDiscoveryEnabled()` is `false`,
- `loadDiscoveryModules()` returns `{ modules: [] }` without issuing a request,
- `injectDiscoveryRows(rows, [])` returns **the same array elements it was
  given** — identity, not deep equality,
- so Home renders exactly the rows `injectAds` produced before this feature
  existed.

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

| Env var | Gate | Ships as |
|---|---|---|
| `EXPO_PUBLIC_HOME_DISCOVERY` | master | OFF |
| `EXPO_PUBLIC_HOME_DISCOVERY_REELS` | Reels for you | OFF |
| `EXPO_PUBLIC_HOME_DISCOVERY_PEOPLE` | People you may know | OFF |
| `EXPO_PUBLIC_HOME_DISCOVERY_STATUSES` | Statuses | OFF |
| `EXPO_PUBLIC_HOME_DISCOVERY_GROUPS` | Groups | OFF |
| `EXPO_PUBLIC_HOME_DISCOVERY_CREATORS` | Creators to follow | OFF — no source |
| `EXPO_PUBLIC_HOME_DISCOVERY_TOPICS` | Topics | OFF — no destination |
| `EXPO_PUBLIC_HOME_DISCOVERY_SPONSORED` | Sponsored carousel | OFF — see below |

Every module flag is ANDed with the master, so clearing
`EXPO_PUBLIC_HOME_DISCOVERY` alone disables all seven regardless of their own
values. Fail-closed: an unset or unparseable value is OFF, and a source that
throws yields no module rather than a partial one.

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

1. Remove the `EXPO_PUBLIC_HOME_DISCOVERY*` vars from the EAS profile / Railway
   env for the build in question.
2. Rebuild. There is no server-side component to revert and no schema change.
3. Verify: Home's rows are `injectAds` output, and `HomeScreen.discovery.test.ts`
   still passes — it asserts the composition order and the §1 preservation set
   (Pulse Network hero, status rail, composer, feed tabs, refresh control, the
   shared `keyExtractor`) directly against the source.

No commit needs to come out. Reverting `fec0bda3` is *not* the rollback path and
should not be used as one: it would also remove the tests that pin the
untouched-Home guarantees.

## Device QA build

The build installed to P3r7or and to the iPhone 17 Pro Max simulator for §18 QA
was made with five flags exported at build time (master, reels, people,
statuses, groups). Creators, topics and sponsored were left off for the reasons
above. The repo's shipped defaults were not modified to produce it.
