# PulseSoc Performance Constitution

Ten rules that govern how PulseSoc is made fast, and — more importantly — how it
is *not* made fast. They exist because the failure mode of a performance mission
is not slowness. It is a build that feels quick in a demo and is wrong in
production: stale money, skipped checks, a spinner replaced by a lie.

Every rule below is enforceable, and most are enforced by a test named in the
rule. Where a rule is not yet enforced by a test, it says so.

---

## Rule 1 — Cache may be displayed. Cache is never authority.

A cached value can be painted to shorten the blank-shell window. It may never be
the basis of a decision.

Never from cache: account balances, payment state, entitlement and premium
status, permission and ownership checks, moderation state, auth. These are read
from the server, and the screen waits or degrades honestly.

The corollary is ordering: a cached value must never overwrite a canonical
response that has already landed. A slow disk read answering after the network
is a *stale* read, and it loses.

*Enforced by:* `src/screens/__tests__/ProfileScreen.perf.test.tsx` —
"never lets a slow cache read overwrite a canonical response that already landed".

## Rule 2 — A screen must not block on an optional dependency.

The critical path is the data without which the screen is meaningless. Anything
else — a badge count, a recommendation strip, a promo tile — loads alongside or
after, and its failure degrades that one element rather than the screen.

The characteristic violation is a `Promise.all` that mixes the profile with the
premium tile, so an unrelated outage renders an error page.

## Rule 3 — Requests that do not depend on each other are issued together.

A waterfall is two round trips charged to the user for one screen. Where the
second request's inputs are already known — from the route, from the auth store,
from cache — it is issued alongside the first, not after it.

Where the inputs are genuinely unknown, the waterfall stays. Guessing a key to
win a round trip fetches the wrong data, which is worse than being slow. When an
eagerly-fetched result is later contradicted by the canonical response, it is
discarded, not rendered.

*Enforced by:* `ProfileScreen.perf.test.tsx` — the four `profile load parallelism`
tests, including "refetches the grid when the server's canonical identity
disagrees with the eager key".

## Rule 4 — Every network request is bounded.

An unbounded request is a spinner that never resolves, which is indistinguishable
from a hang. Ordinary JSON requests are bounded at 20s; `FormData` uploads at
180s, because cancelling a legitimate video upload on a slow connection is a
data-loss bug wearing a performance costume.

A caller's own `AbortSignal` always wins and is forwarded, so screen teardown and
superseded search queries keep working. Only the budget expiring is reported as a
timeout — a caller cancellation is not, and must not be described to the user as
the server being slow.

*Enforced by:* `src/api/__tests__/pulseApiTimeout.test.ts` (5 tests).

## Rule 5 — Reads are deduplicated, never cached, at the request layer.

Components that enter focus together ask for the same canonical GET in the same
frame. Share the in-flight work; never retain the response. Server authority,
refresh semantics, and explicit reloads must all survive untouched.

Coalescing is restricted to canonical GETs — no body, no signal, no custom
headers, no `no-store`/`reload`. Writes are never combined.

*Enforced by:* `src/api/__tests__/pulseApiCoalescing.test.ts`.

## Rule 6 — Cache tiers are bounded and invalidated at every write path.

The memory tier is capped (64 entries, LRU). A long session that visits many
profiles and conversations must not grow the resident set without limit.

Any code path that removes or rewrites a cache key through `AsyncStorage`
directly, rather than through `writeJsonCache`, must call `invalidateJsonCache`.
Otherwise the memory tier keeps serving a value the caller believes it deleted —
which is how a discarded draft reappears.

*Enforced by:* `src/core/__tests__/cache.test.ts` — "bounds resident memory by
evicting least-recently-used keys" and "stops serving a key that was invalidated".
Both were mutation-tested: raising the cap to 100000 fails exactly the eviction
test and nothing else.

## Rule 7 — A TTL is only claimed where age is actually known.

`maxAgeMs` bounds the memory tier only. The on-disk cache format is a bare JSON
value with no stored timestamp, so the age of a disk entry is genuinely unknown —
it may predate the install of this build.

Pretending to enforce a TTL against it would be a lie that financial screens
might then trust. Do not add one until the disk format carries a timestamp.

## Rule 8 — Callers get their own object.

The memory tier stores the serialized string, not the parsed object. Returning a
shared parsed reference hands two screens the same mutable object, and a caller
that spread-merges into it corrupts the other's state.

Re-parsing per read keeps every caller's object private. The parse is not the
expensive part; the bridge is.

*Enforced by:* `cache.test.ts` — "hands every caller its own object".

## Rule 9 — Memoization requires stable props, or it is not memoization.

`React.memo` on a component whose parent recreates its callbacks every render
never bails out. It adds a shallow comparison over every prop and returns `false`
each time. That is pure overhead sold as an optimization.

Before memoizing a list row, the call site must first be made referentially
stable: handlers in `useCallback`, hook return objects memoized, no inline arrows
in `renderItem`. Memoize second. If the stabilization is out of scope, so is the
memoization — record it as debt rather than shipping a comparison that can never
succeed.

*Not currently enforced by a test.* See "Known debt" below.

## Rule 10 — Indexes are added on evidence, never on suspicion.

An index is justified by named query sites, not by a column looking like a
foreign key. Every index added must cite the code that reads it.

Where one composite index can serve both an equality filter and the sort those
readers apply, prefer it to two — the leftmost prefix still serves the plain
filter. Indexes are not free: they cost write throughput and storage on every
insert, forever.

---

## Anti-goals — things that are forbidden even when they would raise a metric

These make the app *measure* faster while making it worse:

- Fake loaders, skeletons that do not correspond to a real pending request, or
  artificial delays tuned to feel smooth.
- Static or placeholder data standing in for a real fetch.
- Disabling functionality, removing correctness checks, or weakening validation.
- Bypassing authentication, authorization, or payment verification.
- Preloading the entire app at startup. Memory and battery are user-visible too.
- Claiming an improvement without before/after data, or claiming device results
  that were not measured on a device.

## Known debt

**Feed row memoization (Rule 9).** `PostCard` (1634 lines) is rendered per feed
row and is not memoized. It cannot be memoized today: `HomeScreen`'s nine post
handlers are plain function declarations recreated every render, `renderItem`
creates roughly six inline arrows per row, and `useSocialActionGuard`
(`src/social/actionGuard.ts`) returns a fresh object literal on every render even
though its methods are individually `useCallback`'d.

Remediation order: memoize the `useSocialActionGuard` return object with
`useMemo`; wrap the nine handlers in `useCallback` (note `handleDelete` reads the
`activePostId` state value and genuinely needs it as a dependency); hoist the
`renderItem` arrows; only then apply `React.memo`. Doing only the last step is a
Rule 9 violation.

This was deliberately deferred rather than half-done: `actionGuard` is shared
infrastructure carrying the double-tap and optimistic-rollback guarantees for
every social action, and destabilizing it immediately before an App Store
submission is the wrong trade.
