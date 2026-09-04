# Media Security Model

## Threat: one account reading another's media

A shared device with account switching means user B can be one tap away from bytes that
were downloaded while user A was signed in. Two mechanisms address it.

### Scope lives in the path, not in the key

`setMediaCacheScope(userId)` sets a scope that becomes a path segment:

```
.../pulsesoc-media/u1234/<digest>.jpg
.../pulsesoc-media/u5678/<digest>.jpg
```

The digest is identical for the same media in both accounts, and that is fine — what
differs is the directory. Putting the scope in the *key* instead would have been the easier
change and the wrong one: a key is a string that any caller can construct, whereas a path
segment is applied by `cacheFileUriFor` on every read and write, with no say in the matter
left to the caller. There is
no code path that resolves a key without also resolving a scope, so account B cannot name
account A's file even by accident.

A hostile or malformed user id is normalised: `\`u${String(userId).replace(/[^A-Za-z0-9]/g, "")}\``.
`"../../etc"` becomes `uetc`, not an escape from the cache root. The unauthenticated scope
is `anon`.

### Scoping happens at the single identity choke point

`setMediaCacheScope` is called from `stateFor` in `src/session/auth.ts` — the one documented
constructor for every `AuthState`. Sign-in, refresh, restore-from-secure-store and account
switch all pass through it. Scattering the call across the individual transitions would have
meant that the next transition someone adds silently inherits the previous account's scope.

## Sign-out purges every scope, not just the active one

`clearUserScopedMediaState()` in `src/media/mediaSessionCleanup.ts` is called by `signOut()`
and `signOutEverywhere()`. It stops playback, calls `clearAllMediaCaches()`, resets the
scope to `null`, and removes every user-scoped media key from AsyncStorage.

`clearAllMediaCaches` — not `clearMediaCache` — is the correct call here. Clearing only the
active scope would leave the *previous* account's directory on disk indefinitely, which is
the exact state this model exists to prevent. Every step is individually `.catch`-guarded:
a failure to purge must not block the sign-out itself, because a user who cannot sign out
on a shared device is a worse outcome than a cache that survives one boot.

## Threat: leaking URLs through logs

`MediaEvent` in `mediaTelemetry.ts` has nowhere to put a URL. There is no `url`, `uri`,
`href`, `caption`, `body` or `filename` field, and the ownership test asserts this stays
true. The only identifier is `key` — an opaque digest, or `id:<n>` for canonical media —
neither of which is fetchable by anyone reading a log.

`mediaFailureReason(error)` maps a thrown value to a code from a closed vocabulary by
reading only *structural* properties: `status`, `name`, `code`. It never pattern-matches
the message string, because the message is the one part of an error that carries the signed
URL. `fetch` and `expo-file-system` both embed the failing URL in `error.message`.

The same constraint applies to user-facing text. `downloadMessageFor(reason)` derives copy
from the reason code, and the test suites assert that no message returned by the download,
save or share paths matches `/https?:/`.

## Threat: a fake "Saved"

Reporting success for a write that did not happen is a security-adjacent failure: the user
deletes the original believing they have a copy. `saveMediaToGallery` returns
`{status: "saved"}` only after `MediaLibrary.saveToLibraryAsync` has resolved. iOS *limited*
access returns `{status: "saved", limited: true}` — add-only really does write, so calling
it a failure would be its own lie, but the caller can still tell the difference.

## Permissions

`getPermissionsAsync(true)` / `requestPermissionsAsync(true)` — `writeOnly: true` — request
the narrowest entitlement that can do the job. Saving a download does not require read
access to the user's library, so the app does not ask for it.

The download runs *before* the permission prompt. A save that is going to fail because the
file is gone should not first cost the user a permission dialog, and a permission granted
for a doomed operation is a permission granted for nothing.

When `canAskAgain` is `false` the result message directs the user to Settings, because the
in-app prompt will no longer appear and a retry button would do nothing.

## Realtime audio boundary

The media foundation does not touch audio session management. No module under `src/media/`
calls `setAudioModeAsync`, `AVAudioSession` or `setCategory`; the ownership test asserts it.
`python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` passes, and
every path in `config/realtime-audio-protected-paths.json` was cross-checked against
`git status --porcelain` with zero overlap.
