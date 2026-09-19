# Share Extensions — capability #13

Written 2026-09-19 against the iOS 26.5 SDK. `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md` §13
rates this **Large** effort, wave 5, and already names the right pattern:

> Share extensions have a hard memory limit (~120 MB), which makes large video payloads a
> genuine engineering problem, not a detail. The correct pattern is to copy the item into
> the App Group container and let the main app do the work.

That conclusion is correct. This document finds that the *reason* given for it is the weaker
of the two available reasons, and that the difference matters — a team told "the constraint
is memory" will conclude that small payloads may be uploaded from the extension, which the
SDK says is unsafe at any size.

It also finds that the audit's headline prerequisite for this capability — a keychain access
group — is not needed at all, and that the half of it the audit calls impossible is already
shipped in a dependency.

---

## Finding 1 — the item you are handed is a temporary file that dies when your callback returns

`NSItemProvider.h` offers three ways to get at an attachment, and the choice is not a
performance question:

| API | Line | What you get |
|---|---|---|
| `loadDataRepresentationForTypeIdentifier:` | `:116-117` | "Copies the provided data into an `NSData` object." |
| `loadFileRepresentationForTypeIdentifier:` | `:119-121` | a `NSURL` to a temporary file |
| `loadInPlaceFileRepresentationForTypeIdentifier:` | `:123-126` | the original file if possible, a copy otherwise |

The first is the one that looks simplest and is unusable here: an `NSData` is the whole
payload resident in memory, in the process that has the tightest memory budget on the
system. A 4K video from Photos is not a borderline case for it, it is a guaranteed kill.

The second carries the sentence that makes the audit's pattern mandatory rather than
advisable — `NSItemProvider.h:119`:

> `// Writes a copy of the data to a temporary file. This file will be deleted when the completion handler returns. Your program should copy or move the file within the completion handler.`

So the copy into the App Group container is not an optimisation layered on top of the normal
flow. It **is** the normal flow. There is no version of this extension that holds a usable
reference to the shared item after its own completion handler returns.

The third is the trap, because it reads like the efficient answer to the second. It is not,
for two reasons stated in its own comment (`:123-125`):

> `// If a file is not available for opening in place, a copy of the file is written to a temporary location, and `isInPlace` is set to NO. Your program may then copy or move the file, or the system will delete this file at some point in the future.`

`isInPlace` is an output, not a request — the caller cannot force the in-place case, so the
code must handle the copy case anyway and has gained nothing. And in the copy case the
lifetime becomes *"at some point in the future"*, which is worse than a deadline you can see:
an unbounded, unobservable deletion window is not something an upload can be planned around.

**Decision: use `loadFileRepresentationForTypeIdentifier:` and copy inside the completion
handler.** Not `loadDataRepresentation` (memory), not `loadInPlaceFileRepresentation`
(indeterminate lifetime, no guaranteed benefit).

---

## Finding 2 — the extension must never upload, and memory is the second reason, not the first

The ~120 MB figure the audit quotes is widely repeated and this session could not verify it
from any first-party source — it is not in a header, and extension memory budgets are not a
documented API. It may well be right. It is also not the constraint that decides the design.

The deciding constraint is in `NSExtensionContext.h:18`:

> `// Signals the host to complete the app extension request with the supplied result items. The completion handler optionally contains any work which the extension may need to perform after the request has been completed, as a background-priority task. The `expired` parameter will be YES if the system decides to prematurely terminate a previous non-expiration invocation of the completionHandler. Note: calling this method will eventually dismiss the associated view controller.`

Three things are stated there, and each one independently forbids uploading from the
extension:

1. post-completion work runs **as a background-priority task** — it is not the foreground
   work the user is waiting on;
2. the system **may prematurely terminate it**, and the API's only accommodation is to tell
   you afterwards via `expired`;
3. completing **dismisses the view controller**, so there is no UI left to report failure
   into.

This is a documented, unconditional property of the extension lifecycle. It does not get
better for a 200 KB JPEG. A team that believes the obstacle is the ~120 MB budget will
reasonably conclude that photos can be uploaded inline and only video needs the hand-off —
and will ship an upload path that silently loses items on a busy device, with no crash, no
error, and no way for the user to tell the difference between "posted" and "gone."

**Decision: the Share Extension performs no network I/O of any kind.** It copies bytes,
writes a manifest, and completes. Everything after that is the main app's job.

This is the same conclusion `WIDGETKIT.md` Finding 2 reached for a different surface by a
different route, and the convergence is worth naming: **on every Apple surface that runs
outside the app process, the right architecture is that the app owns the network and the
out-of-process surface owns only a file in the shared container.** That now holds for
WidgetKit and Share Extensions, and App Intents' recommended shape (a) is the same idea
expressed as a deep link.

---

## Finding 3 — the extension needs no session token, because it makes no requests

The audit's §0 prerequisite block (`:77-85`) names "a decision about how the extension reads
the session" as the real cost of this capability, and
`DECISIONS_KEYCHAIN_ACCESS_GROUP.md` then measured that cost in full: the entitlement, the
read-old/write-new migration, the ordering constraint between steps 4 and 5, and the failure
mode if it is botched, which is that **every existing user is silently signed out**.

That document is right about all of it. This finding is not a correction to it. It is the
answer to the one question it explicitly left open:

> **Not decided here**, and deliberately left for when the first extension has a concrete
> shape: whether the extension reads the session envelope directly or reads a *narrower*,
> purpose-built item. Handing a widget the live refresh token so it can render a follower
> count is more authority than the job needs, and a read-only projection written by the app
> would be a smaller blast radius.

The Share Extension is now a concrete shape, and the answer is one step further than the
narrower item: **neither.** Finding 2 removed the extension's only reason to hold a
credential — it performs no network I/O, so there is no request for a token to authorise.

There is one thing it genuinely needs to know: **is anybody signed in.** A share sheet entry
that accepts a video and drops it into a void for a signed-out user is worse than one that
declines. But that is a boolean about the app's state, not a credential — it grants nothing,
proves nothing, and is useless to anyone who extracts it. It belongs in the App Group's
`UserDefaults`, written by the app, alongside the widget snapshot that `WIDGETKIT.md`
Finding 2 establishes.

**Decision: the Share Extension takes `com.apple.security.application-groups` and explicitly
not `keychain-access-groups`.** No credential crosses the process boundary, so none can be
read out of an extension's sandbox — and this capability never has to run the migration whose
worst case is a mass sign-out.

---

## Finding 4 — and that retires the keychain access group from the extension foundation

`DECISIONS_KEYCHAIN_ACCESS_GROUP.md` decides to build the access group **before the first
extension, as its own release**, and gives three reasons. The third is the load-bearing one:

> It is genuinely shared. Four capabilities need it, and doing it inside the first one means
> the second, third and fourth inherit a design decided by whichever happened to go first.

That reason is now false, and the evidence accumulated one capability at a time:

| Capability | Prerequisite as stated | Where it landed |
|---|---|---|
| #3 App Intents | App Group + keychain access group | recommended shape is a Siri-addressable deep link; the app does the work in-process (`APP_INTENTS_SIRI_SHORTCUTS.md` Finding 1) |
| #12 WidgetKit | App Group + keychain access group | the app writes a snapshot and calls `reloadTimelines`; the widget never fetches (`WIDGETKIT.md` Finding 2) |
| #13 Share Extensions | App Group + keychain access group | the extension stages bytes and completes; it never fetches (this document, Findings 2–3) |
| #2 Live Activities | App Group + keychain access group | the extension is handed its content and has no API with which to fetch (`LIVE_ACTIVITIES.md` Finding 7, added after this table) |

> **Closed 2026-09-19.** The fourth row was written as "not yet re-examined," with the
> prediction that it would land the same way. It did, and more strictly than predicted:
> `ActivityViewContext` has four members — id, attributes, state, staleness — and
> `ActivityConfiguration`'s initialiser has **no provider parameter**, where a widget's
> `StaticConfiguration` requires one. The Live Activity extension does not own even a file;
> it cannot fetch because there is no API through which it could. `LIVE_ACTIVITIES.md`
> Finding 7 also corrects the audit's claim that #2 needs an App Group — it does not; only
> the WidgetKit widgets sharing the same target do.
>
> So **all four** of the capabilities `DECISIONS_KEYCHAIN_ACCESS_GROUP.md` cited need the
> App Group and none needs the keychain. The recommendation below now rests on a complete
> enumeration rather than three-of-four plus a prior.

**Recommendation: do not build the keychain access group as a foundation item.** Build the
App Group as the foundation — it is genuinely shared by every extension, it carries no
credential, and its own risk is bounded and local. Leave `keychain-access-groups` unbuilt
until some capability's design actually requires a credential outside the app process, and
make that capability carry the justification, the migration and the release.

This is a stronger version of the same instinct that document already had. Its argument for
building up front was "four capabilities need it, so decide it once rather than four times."
The better outcome is that nothing needs it, so it is decided zero times — and the app never
holds an entitlement that lets another binary read the user's refresh token.

Two things this does **not** retire, and they should not be lost when the decision is
revisited:

- The migration analysis itself. If the entitlement is ever adopted, the read-old/write-new
  pattern and the step-4-before-step-5 ordering are still exactly right, and re-deriving them
  under deadline is how the mass sign-out happens.
- The verification it flagged as its load-bearing gap — proving on hardware that a read with
  `kSecAttrAccessGroup` set does not match an item written without one. That experiment stays
  owed by whoever eventually needs the entitlement, not by the extension foundation.

---

## Finding 5 — the staging directory is user media, and it has a purge problem the widget does not

`WIDGETKIT.md` Finding 4 established that the App Group container is a **third** storage tier,
invisible to both of `mediaSessionCleanup.ts`'s existing sweeps — `accountScopedKeys` walks
AsyncStorage, `clearAllMediaCaches` walks the app's own cache directories, and neither can see
a shared container that does not exist yet.

A share-extension staging directory is the same tier holding much worse content. A widget
snapshot is a derived summary the app chose to publish. A staged share is **original media
bytes the user selected out of Photos** — the highest-value, least-recoverable thing on the
device — sitting in a container that survives sign-out, account switch, and app deletion order
of operations, unless something deliberately removes it.

Three obligations, and the third is unique to this surface:

| Obligation | Why |
|---|---|
| Purge on sign-out, inside `clearUserScopedMediaState()` | Same placement, same reasoning, as the Spotlight purge (`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` → Purge) and the widget snapshot. One placement inherits all six session-end paths and every path added later. |
| Purge at launch anything older than a bounded age | A staged item whose hand-off never completed — app killed, user never reopened — is otherwise immortal. |
| **The extension must refuse to stage while signed out** | This is the one the widget does not need. The extension can write to the container when no session exists and no purge path can possibly run, creating user media that nothing in the app's lifecycle will ever be responsible for. Declining is the only way to keep the invariant "everything in the staging directory belongs to the currently signed-in account." |

The third obligation is also why Finding 3's signed-in boolean is not a UX nicety. It is the
enforcement point for a data-retention rule.

---

## Finding 6 — the staged path is an identity, because the upload manager keys on it

The hand-off target already exists and is close to free, which is the good news in this
document. `MediaUploadManager` takes an asset shaped `{ uri, size, name, mimeType, duration }`
and does not care where the `uri` came from — `MediaUploadManager.ts:80` is literally
`new File(asset.uri)`.

But two lines make the *stability* of that path a contract rather than a detail:

- `:48` — the in-flight dedupe identity is `` `${asset.uri}|${asset.size || 0}|${options.contextType}|${options.contextId || ""}` ``
- `:84-86` — a resumable upload is matched by `candidate.asset.uri === asset.uri && candidate.asset.size === actualSize && candidate.options.contextType === options.contextType`

So the App Group file path **is** the upload's identity. Which yields three rules for the
staging code, none of which are obvious from the extension side:

1. Copy once, to a stable, deterministic path. A `tmp`-flavoured name that changes on retry
   defeats both the dedupe and the resume, and the symptom is a duplicate post, not an error.
2. Never re-stage an item that is already staged under a different name.
3. A purge that deletes a staged file mid-upload surfaces at `:82` as
   `"The selected media file is no longer available."` — an existing, translated, user-facing
   string that was written about the photo picker. It will be read by the user as "PulseSoc
   lost my video," which it did. The age-based purge in Finding 5 must therefore exclude
   anything with a live or resumable upload, not just anything recent.

---

## Finding 7 — whether the extension can open the app is unverified, and it decides the product

The pleasant version of this feature is: the user shares a video from Photos, the extension
stages it and immediately foregrounds PulseSoc on the composer with the item attached. The
unpleasant version is: the extension stages it silently, says "Saved to PulseSoc," and the
user has to go find it.

Which one is buildable turns on one API. `NSExtensionContext.h:25`:

> `// Asks the host to open a URL on the extension's behalf`
> `- (void)openURL:(NSURL *)URL completionHandler:(void (NS_SWIFT_SENDABLE ^ _Nullable)(BOOL success))completionHandler;`

The header states **no** extension-point restriction and no availability qualifier beyond the
class's own `ios(8.0)`. Apple's prose documentation has historically restricted this method to
Today extensions. The header and the prose disagree, this session cannot settle which governs
on iOS 26, and the completion handler's `BOOL success` suggests failure is expected to be
runtime rather than compile-time — meaning a build that calls it will compile either way and
the answer only appears on a device.

`linking.ts:65` already registers the `pulsesoc://` prefix, so if `openURL` does work the
target URL costs nothing.

**This is a device-verification item that changes the product, not just the code.** If the
answer is no, the app needs a "1 item waiting to post" affordance that does not exist today,
and that is scope the audit's Large estimate does not contain. It should be settled on the
iPhone 16 Pro **before** the extension target is created, because it is the difference between
a feature people use and a feature people are confused by.

---

## Plumbing: the target is already a solved problem, and the Podfile is not

The third part of the audit's prerequisite — "a target-generation strategy that survives
`expo prebuild`" — is settled and guarded. `DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`
Decision 2 found that `ios/` is a **committed bare workflow** (26 tracked files including
`project.pbxproj`), so an extension target is ordinary Xcode work rather than a config plugin
that synthesizes targets; and `tests/protection/test_ios_native_target_inventory.py` already
ships the mitigation, pinning the target count, each target's name and product type, the 16.1
floor across all four configurations, and — `test_the_project_is_committed_not_generated` —
that `ios/` has not been gitignored out from under the rest.

So the Share Extension target costs a pbxproj edit and one line in `EXPECTED_TARGETS`. Nothing
in this document is blocked on it.

The one piece of plumbing **not** yet covered is the Podfile, and it will be got wrong by
copying the existing block. `ios/Podfile:23` is a single `target 'PulseSoc' do` whose first
line (`:24`) is `use_expo_modules!`. A second target must not repeat that: it links the React
Native and Expo module stack into a process whose entire design constraint is that it stays
small, and it would put a JS runtime inside a target that — per Findings 2 and 3 — exists to
copy one file and exit. Whatever the real extension memory budget turns out to be, spending
it on a bridge the extension never calls is the first way to lose it.

This is worth an assertion rather than a comment, because it is invisible in review: a
Podfile block that reads exactly like the working one is not what a reviewer stops on. The
target inventory test is the natural home — it already parses committed native project state
for exactly this class of "somebody did the obvious thing and deleted a capability" failure.

---

## Floor and entitlements

| Item | Value | Source |
|---|---|---|
| `NSExtensionContext` | iOS 8.0 | `NSExtensionContext.h:12` |
| `NSExtensionItem` | iOS 8.0 | `NSExtensionItem.h:12` |
| `NSExtensionRequestHandling` | iOS 8.0 (unversioned protocol) | `NSExtensionRequestHandling.h:13` |
| `loadFileRepresentationForTypeIdentifier:` | iOS 11.0 | `NSItemProvider.h:120-121` |
| Effective floor | far below the project's 15.1 | — |
| New entitlement | `com.apple.security.application-groups` **only** | Finding 4 |
| Entitlement explicitly *not* taken | `keychain-access-groups` | Finding 4 |

The audit's "Minimum iOS — satisfied" is correct and the floor is not interesting here. The
cost is entirely in the target, the container, and the purge.

---

## What is owed

| Owed | Why |
|---|---|
| Device-verify `NSExtensionContext.openURL:` from a share extension on the iPhone 16 Pro | Finding 7. Decides the product shape, and no amount of reading settles it. |
| Device-verify the actual extension memory budget with a 4K video | Finding 2. The ~120 MB figure is unverified here. The design does not depend on it, but the staging copy's buffer size does. |
| The App Group container purge, inside `clearUserScopedMediaState()` | Finding 5. Shared with WidgetKit and Core Spotlight — one purge, three consumers, written once when the first of the three lands. |
| The signed-in boolean in App Group `UserDefaults` | Findings 3 and 5. It is the enforcement point for the retention rule, not a UX nicety. |
| ~~Re-examine #2 Live Activities against the "the app owns the network" pattern~~ | **Done 2026-09-19** — `LIVE_ACTIVITIES.md` Finding 7. It satisfies the rule strictly, and nothing now keeps the keychain access group on the foundation list. |
| Revisit `DECISIONS_KEYCHAIN_ACCESS_GROUP.md`'s "build it before the first extension" decision | Finding 4. Its third reason no longer holds for **any** of its four capabilities. The document's migration analysis and its owed hardware experiment both survive the revisit. |
| Assert the extension target does not `use_expo_modules!` | Plumbing. Natural home is `test_ios_native_target_inventory.py`, which already parses committed native project state. |
| A `NSExtensionActivationRule` decision — which UTTypes PulseSoc claims in the share sheet | Not researched here. Claiming too much puts PulseSoc in share sheets where it cannot help, which users read as a broken app. |

---

## What was verified, and what was not

**Verified by reading first-party SDK headers (iOS 26.5):** the three `NSItemProvider` load
APIs and their lifetime comments (`NSItemProvider.h:116-126`); the completion/cancellation API
and the `expired` semantics (`NSExtensionContext.h:18-22`); `openURL:completionHandler:` and
the absence of any extension-point restriction in the header (`:25`); `NSExtensionItem`'s
`attachments` array of `NSItemProvider` (`NSExtensionItem.h:22`);
`beginRequestWithExtensionContext:` (`NSExtensionRequestHandling.h:18`); that
`kSecAttrAccessGroup` is a valid attribute for `kSecClassGenericPassword` (`SecItem.h:88`).

**Verified by reading the repo:** `MediaUploadManager.ts:48` and `:80-86` key dedupe and
resumption on `asset.uri`, and `:82` is the source of "The selected media file is no longer
available."; the entitlements file has no App Group; `ios/` is committed (26 tracked files)
with a single Podfile target (`Podfile:23-24`); `tests/protection/test_ios_native_target_inventory.py`
already pins the target set, product types and floor; `linking.ts:65` registers `pulsesoc://`;
`sessionStore.ts:38-50` passes no `accessGroup` (consistent with
`DECISIONS_KEYCHAIN_ACCESS_GROUP.md`, which measured that in detail and is not re-derived here).

**Not verified.** No extension target exists, so nothing here has been exercised.
Specifically:

- whether `openURL:` works from a share extension on iOS 26 (Finding 7);
- the extension memory budget, including the ~120 MB figure the audit quotes (Finding 2);
- that a second Podfile target omitting `use_expo_modules!` links and builds cleanly against
  a target that does use it — expected, not demonstrated.

None of the three changes the design. The first changes the product.
