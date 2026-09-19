# Apple Native Capability Expansion — final report

Written 2026-09-19, closing the mission that began at `7d66a558` on 2026-09-18. Twenty-eight
commits, twenty-two documents under `docs/apple/`, fourteen capabilities audited.

This is not a summary of those documents. Each of them already summarises itself, and a
report that restates them is the thing nobody reads twice. This one carries the four things
that only exist at the end:

- what actually shipped, and what conspicuously did not;
- the findings that **changed the plan**, as distinct from the many that confirmed it;
- the rules that generalise past the capability that produced them;
- and an honest account of what remains unknown, which on this mission is more than it looks.

---

## The headline, stated so it cannot be misread

**No Apple capability was implemented. That was the assignment.** The brief said *audit
first*, and the strongest evidence that the instruction was followed is the shape of the
diff: of twenty-eight commits, six touch anything that ships, and **three of those six are
removals or reversions.**

| Commit | What it did | Direction |
|---|---|---|
| `f8abd3bc` | dropped the `UIBackgroundModes` **`fetch`** declaration that no code implemented | removal |
| `5a87e753` | undid `expo prebuild`'s whole-file reformat of `Info.plist` | reversion |
| `c8f05637` | raised the deployment floor 15.1 → 16.1 (pbxproj + `Podfile.properties.json`) | change |
| `92b67e66` | `tests/protection/test_ios_native_target_inventory.py` — pins targets and the floor | addition (a test) |
| `589762f5` | declared `PyJWT` in `requirements.txt`; production APNs already imported it | addition (a declaration of an existing fact) |
| `47c4a1d9` | corrected `CLAUDE.md`'s RTC stack to Agora → Mux | correction |

Read the middle column. The app's surface area went **down**, its declared floor went **up**,
and one new test now fails if a stray `expo prebuild` deletes the native targets. Nothing was
added that a user can see, and **Build 28 was not touched** — the constraint the brief put in
capitals.

The second headline is the audit's own: **two of fourteen capabilities were already finished
and finished well.** Universal Links and StoreKit 2 are the highest-risk, highest-value Apple
integrations in the product — the link path and the payment path — and the correct
recommendation for both was *leave them alone*. A capability-expansion brief is a strong
prior toward finding work to do. Two-of-fourteen-already-done is the finding that prior was
most likely to suppress.

---

## The four findings that changed the plan

Most of the twenty-two documents confirm the audit with more evidence than the audit had.
Four contradict it, and those are the ones worth a reader's time.

### 1. `keychain-access-groups` left the plan entirely

The audit identified a prerequisite shared by four capabilities and recommended building it
first, as its own release. `DECISIONS_KEYCHAIN_ACCESS_GROUP.md` measured it and found the
failure mode: because `expo-secure-store` applies `accessGroup` through a **single shared
query builder** (`SecureStoreModule.swift:172`, serving get/set/delete), a build that starts
*reading* with an access group cannot find items written without one — and
`sessionStore.ts:66-73` degrades that to signed-out **deliberately and quietly**. A botched
rollout does not crash. Every user is simply logged out, and the app looks like it is working.

Then the four capabilities were designed, one at a time, and none of them needed it:

| Capability | Why not |
|---|---|
| #3 App Intents | `perform()` has no RN bridge; the shape is a Siri-addressable deep link and the app does the work in-process |
| #12 WidgetKit | `TimelineProvider` cannot report failure, so the widget must never fetch; the app writes a snapshot |
| #13 Share Extension | post-completion work is a system-cancellable background task, so it must not upload; it stages bytes |
| #2 Live Activities | `ActivityConfiguration` takes **no provider** and `ActivityViewContext` is four members handed to the renderer — there is no API through which it could fetch |

So the entitlement that was going to be Wave 2's centrepiece is now built by nobody. The
argument for doing it once rather than four times ends at a better place: **it is decided
zero times, and the app never holds an entitlement that lets another binary read the user's
refresh token.**

This is the mission's largest single change, and it is a deletion.

### 2. The first Live Activity should be a media upload, not a call

Everyone's instinct — including the audit's — is a call activity. `Activity.request` *throws*
(`ActivityKit.swiftinterface:46-73`), and its error list includes `globalMaximumExceeded`:
the user's phone already has too many activities **from other apps**. The audit's rule was "a
Live Activity may read call state, never drive it." The sharper rule is that **Live Activity
failure must be unobservable to the call path** — not merely non-driving, *invisible*.

And the first activity in a codebase is also the one that shakes out the extension target,
the App Group, the App ID and the provisioning profile. Doing that shake-out against the call
path is the worst available venue for it. A media-upload activity touches nothing locked,
renders a progress stream that already exists, needs no push, and has a definite end.

### 3. Core Spotlight needs no Info.plist key, and the real blocker is elsewhere

The audit named `NSUserActivityTypes` as the prerequisite. It is not: that key gates
cross-device continuation, and `CSSearchableItemActionType` is a system constant. The actual
blocker is that `AppDelegate.swift:62-69` forwards **every** `NSUserActivity` to
`RCTLinkingManager` without switching on `activityType` — so a Spotlight tap would foreground
the app and navigate nowhere. A prerequisite in the wrong place is worse than a missing one,
because it gets satisfied and the feature still does not work.

### 4. MapKit is blocked by a decision, not by an absence

The audit's verdict (do not implement) is right; its reason is the version most likely to be
reversed, because *"there is no location data" is falsifiable by a grep and the grep returns
hits*. What the grep returns: three Bitcoin addresses, and two tables of **staff home
addresses** — `admin_users` and `employees`, beside dates of birth and next of kin — which,
being the only structured addresses in a schema of free text, are the only rows a geocoder
would succeed on. Meanwhile the usable source was deliberately emptied: `bot.py:15058-15063`
records that region and city "record nothing rather than recording a claim."

So the finding inverts. The hazard was never a wasted sprint on an empty map. It was an
engineer told "find the location data" following the evidence straight to the staff table.

---

## The rule this mission converged on

Three capabilities arrived at the same architecture independently, each for its own reason,
and a fourth turned out to be a strict instance of it:

> **On every Apple surface that runs outside the app process, the app owns the network and
> the out-of-process surface owns only a file in the shared container.**

A widget renders a snapshot the app wrote. A share extension stages bytes for the app to
upload. An App Intent hands the app a destination. A Live Activity is handed its content by
the system and owns not even a file. **None of them authenticates, so none needs a
credential, so none needs `keychain-access-groups`.**

Independent convergence is the reason to trust it. Nobody set out to establish this rule; it
was extracted after the fact from four designs that had each been derived from a different
constraint — a missing bridge, a protocol that cannot express failure, a cancellable
background task, and an initialiser with no provider parameter.

**Its practical use is as a test.** Any future Apple surface that wants to make a network
call from outside the app process is, on the evidence of these four, misdesigned — and the
burden is on that design to explain why it is the exception, before an entitlement is
requested for it.

---

## The recurring failure shape, and why it deserves a name

The same hazard appeared in six unrelated frameworks. In every case **the permissive
behaviour is the default, and the failure is silent**:

| API | Default | What goes wrong |
|---|---|---|
| `AppIntent.openAppWhenRun` | `false` | the intent runs without the app, and there is no RN bridge |
| `AppIntent.authenticationPolicy` | `alwaysAllowed` | the intent runs on a **locked** phone |
| `CSSearchableItem.expirationDate` | 1 month | entries outlive the permission that produced them |
| `WidgetKit` `accessory*` families | — | the widget renders on the **lock screen**, continuously |
| `ActivityUIDismissalPolicy` | `.default` | an ended activity lingers on the lock screen |
| `CLLocationManager` without the plist key | — | `requestWhenInUseAuthorization` "will do nothing" — not an error, *nothing* |

None of these produces a crash, a log line, or a failing test. Each produces a working build
that does the wrong thing, and four of the six do it **on a locked device or a lock screen**,
which is the one surface where being wrong is most expensive.

The defensive pattern, applied three times in these documents and worth applying again:
**make the safe state the default so that forgetting is harmless.** `storageScope.ts`
inverted a delete-allowlist into a keep-allowlist for exactly this reason; the Core Spotlight
policy is an allowlist so that a content type added next year is not indexed until someone
writes down why it should be; the Share Extension refuses to stage while signed out because
it can otherwise write media that no purge path can ever see. In all three the cost of
forgetting is a missing feature, not a leak.

The App Intents version is the sharpest: `authenticationPolicy` should be a **mandatory
explicit declaration per intent**, precisely because its default is the wrong one and reading
the code will not remind you.

---

## What the audit got wrong, and the meta-point

Twenty-one dated correction blocks now sit inside these documents. A representative sample:

| Audit said | Actually |
|---|---|
| Core Spotlight needs `NSUserActivityTypes` | it needs no Info.plist key at all |
| Share Extensions need keychain access | they need no credential, because they make no requests |
| Live Activities need an App Group "to share state" | they share no state through a container |
| App Intents belong with the extension capabilities | they belong in the **app target** — an extension target would forfeit `ForegroundContinuableIntent` |
| Sign in with Apple uses ES256 | RS256; also a table, not a column |
| WidgetKit can fetch its own data | `TimelineProvider` has no way to express failure |
| The floor is 15.1 | 16.1 since `c8f05637` — app target only; RN's `post_install` pins 288 pods back to 15.1 |
| MapKit is blocked by missing data | blocked by a decision, and the data that exists is staff home addresses |
| "the only Swift files today are `AppDelegate.swift` and `pulse-now-playing`" | **three** local Swift modules, one of them fully worked — `pulse-apple-translation` has 8 implementation files, a 6-file Swift test harness, a podspec that weak-links its framework, and a typed error enum whose raw values are the wire contract with TypeScript |

That last row is the one with the widest blast radius, because it re-prices something rather
than correcting a fact. Every Swift effort estimate in the audit was written against a
codebase believed to have one Swift file. There is an existing, complete example of what a
new native capability looks like here, and estimates should be read against that.

The meta-point matters more than any row. **Every one of these was found by reading a
first-party header or `.swiftinterface` in the iOS 26.5 SDK and citing a line number.** Not
one was found by recalling how the framework works. The corrections are not evidence that the
audit was careless — it was written the way audits are usually written, from experience — they
are evidence that *experience is the wrong instrument at this resolution*, and that the
instrument that works is cheap and local and was sitting on the disk the whole time.

The standing rule that produced them: **first-party local SDK evidence beats memory, every
time, and a claim without a file and a line number is a hypothesis.**

A corollary that fired twice and is worth recording on its own: **a description of a gap
outlives the gap.** Prose written to describe a limitation keeps being falsified by evidence
gathered later, and the prose does not update itself. Several corrections above are of that
kind — the audit was right when written.

---

## Where to start

The waves in the audit still hold, with `keychain-access-groups` struck from Wave 2.

**1. Sign in with Apple (#6).** Highest value, no target work, no lock interaction, iOS
13.0 — the least constrained item in the set. But `SIGN_IN_WITH_APPLE.md` Finding 7 is a gate,
not a footnote: the "off-the-shelf" rating rests on five properties of
`expo-apple-authentication` that **have not been checked because the package is not
installed**. Install it and check them before estimating.

**2. The extension foundation — now two items, not three.** One App Group, a second App ID
and profile, and a committed extension target, shipped with a trivial placeholder widget to
prove the pipeline. The target-generation question is settled (`DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`:
the Xcode project is committed, so extensions are committed targets) and `92b67e66` already
guards it.

**3. Core Spotlight's education-articles row (#4).** `DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md`
makes the point that if Spotlight ships in one stage it should ship as that row alone: it is
first-party published content, identical for every user, revocable by nobody, and therefore
the only allowlist entry needing **no purge, no privacy check and no staleness reasoning.**
Its prerequisite is the AppDelegate `activityType` switch, which #3 and #10 also want.

Three things should be written once and shared, because all three have more than one consumer
and each would otherwise be written differently by whoever went first:

- **The purge**, inside `clearUserScopedMediaState()` — three consumers (Spotlight index,
  widget snapshot, staging directory). That placement inherits all six session-end paths and
  every path added later.
- **The content allowlist**, already written — it governs Spotlight entries, intent results
  and widget content alike. It decides one product question with no new argument: the
  unread-message-count widget is denied, because a count is a derived signal about DMs.
- **The AppDelegate `activityType` switch** — the shared entry point for #4, #10 and #3.

---

## The debt this mission could not discharge

### Nothing here has been run

Every document says so in its own "what was verified" section, and the point is worth
consolidating: this mission verified **code and SDK headers**. It executed nothing. The
claims are of the form "this API is declared this way" and "this repository contains this
line," which are strong claims, and not the same as "this works."

### The simulator cannot close the gap

Ad-hoc signing — which this project requires, because Agora ships prebuilt frameworks — strips
associated domains and the team id. Entitlement-gated behaviour therefore cannot be exercised
on the simulator **at all**, and a simulator "failure" of such a feature carries no
information. That is not a scheduling inconvenience: it means a whole class of item is
undischargeable without hardware. Consolidated, on the iPhone 16 Pro:

| Owed on device | Why it cannot be read off |
|---|---|
| `NSExtensionContext.openURL:` from a share extension | `NSExtensionContext.h:25` states no extension-point restriction; Apple's prose historically did. **Decides the product shape**, not just the code |
| A keychain read with `kSecAttrAccessGroup` against an item written without one | the load-bearing assumption of the entire migration analysis — twenty minutes, and it retires the assumption either way |
| The real share-extension memory budget with a 4K video | the ~120 MB figure could not be sourced; the design does not depend on it but the copy buffer does |
| `CSSearchableIndex.isIndexingAvailable`, and `deleteAllSearchableItems()` on an empty index | the launch-time repair trigger assumes the empty case is a cheap no-op |
| Any BackgroundTasks execution | the simulator returns `Unavailable` — owed **by construction** |
| A universal-link tap; a sandbox purchase → grant → restore → refund | entitlement-gated; the simulator's "failure" is meaningless |
| Locked-device keychain read; biometric invalidation; background audio continuity; the lock screen | all require a device that actually locks |

### One thing was owed that predates this mission — and it closed while this was being written

`BACKGROUND_TASKS.md` found a **live** exposure, not a future one: `ios/PulseSoc/Info.plist`
holds the `UIBackgroundModes` array with `audio` in it, and it was **not** in
`config/realtime-audio-protected-paths.json` — while `app.json`, which also declares the
array, was. The protection test read one file. An edit to the other removed background audio
and passed CI.

It was the single highest-priority item in this report, it has nothing to do with Apple
capability expansion, and it could not be carried here: it needed an **audio-scoped** change
with the full declaration battery, which this mission is not permitted to make. That is the
policy working as designed, and the correct output of a non-audio mission that finds an audio
hole is a precise description of it, not a fix.

**It landed at `b19894da`, between this report being drafted and being pushed.** The plist is
now in `dependency_watch.files`, `.github/CODEOWNERS` covers it, and the test asserts `audio`
and `voip` in **both** files and that the two agree. The smaller companion item went with it —
`required_ios_configuration` is now *read* by the test rather than duplicated in it. And the
exploit `BACKGROUND_TASKS.md` Finding 1 described but declined to run was run there, under a
change declaration, where it belonged: **the old suite passed 19/19 with
`<string>audio</string>` deleted from the shipped plist.** The prediction was exact.

That sequence is worth leaving in rather than editing into a clean past tense, because it is
this report's own rule firing on this report:

> **A description of a gap outlives the gap.**

The paragraph above was accurate when written and false by the time it was read, and nothing
in it would have announced that. Every "what is owed" table in these twenty-two documents has
the same property. **Check a claimed gap against the tree before acting on it** — the check
is cheap, and the alternative is re-fixing something, or worse, trusting a stale "this is
guarded" and finding it is not.

---

## What this mission was not

It was not an implementation. It did not touch the protected real-time audio paths —
`scripts/realtime_audio_change_gate.py` was run against every commit and reported no protected
path changed. It did not modify Build 28. It did not create any feature flag, because it
created no feature.

And it did not produce eighteen documents to satisfy a filename list. The brief asked for
eighteen and forbade empty ones; twenty-two exist because four capabilities each needed both a
*decision* document and a *mechanism* document, and collapsing them would have buried the
decisions. Every one of them contains findings that changed something — a plan, a prerequisite,
a recommendation, or a line in `CLAUDE.md`.

The most useful sentence to carry forward is not any of the recommendations. It is the
instrument that produced them:

> Open the header. It is on the disk, it is authoritative, and it disagrees with your memory
> more often than is comfortable.
