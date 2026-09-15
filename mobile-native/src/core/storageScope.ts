/**
 * Which stored keys belong to the account, and which belong to the handset.
 *
 * WHY THIS IS AN INVERSION
 *
 * Sign-out used to sweep an explicit list of six prefixes — feed, post, reels,
 * status, messenger, mediacache. Every cached surface added after that list was
 * written silently opted out of account isolation, and by now that is most of
 * them: profiles, the activity inbox, account state, saved library, recent
 * searches, the radio queue and every composer draft all survived a sign-out
 * under a bare key and were read straight back by the next account.
 *
 * The root cause is the direction of the list, not its contents. An allowlist of
 * things to delete has to be updated by someone who remembers it exists, and the
 * failure mode when they forget is a privacy leak that nothing reports. So it is
 * inverted here: everything the app stores is treated as the account's unless it
 * is named as the device's. A new cache key added tomorrow is cleared at
 * sign-out by default, and the cost of forgetting is a cache miss instead of one
 * user reading another's content.
 *
 * WHAT IS DELIBERATELY NOT SWEPT
 *
 * The keep-list is not "things that are safe to leave" — it is things that would
 * be WRONG to remove from here, either because they describe the handset rather
 * than a person, or because another layer owns them and is already making a more
 * careful decision. Removing a key out from under its owner is how a sign-out
 * breaks push registration or cancels a deliberate biometric retention.
 */

/** The namespace every key this app writes is expected to live under. */
export const STORAGE_NAMESPACE = "pulsesoc.native.";

/**
 * Prefixes that survive a sign-out.
 *
 * Each entry carries a note saying what BREAKS if it is swept. "It seemed
 * harmless" is not a reason — an entry that cannot justify itself belongs on the
 * other side of the line, because the default has to be deletion.
 *
 * The notes are comments rather than data on purpose. As string fields they were
 * indistinguishable, to any tool reading this file, from copy the app might
 * render; the repo's user-facing-copy gate flagged them on exactly that basis
 * and it was right to.
 */
const DEVICE_KEEP_PREFIXES: readonly string[] = [
  // Owned by `unregisterPushDevice`, which runs FIRST on the sign-out path and
  // reads the cached registration in order to revoke the endpoint remotely.
  // Sweeping it here would destroy the token that call needs, leaving the
  // handset registered for an account that has left it. The installation id is
  // also the handset's identity across accounts, not any one user's.
  "pulsesoc.native.push.",

  // The auth layer's own state, including the deliberately retained
  // Face-ID-gated refresh token. `signOut({ clearBiometrics })` decides whether
  // that survives; overriding that decision from a cache sweep would quietly
  // turn every ordinary sign-out into a full biometric un-enrollment. The
  // remembered-accounts list is what the sign-in screen reads to offer a return.
  "pulsesoc.native.session.",

  // Device preferences — language, appearance, accessibility. These describe the
  // handset and the person holding it rather than the session, and resetting
  // them would hand the next account a phone in the wrong language.
  "pulsesoc.native.settings.",

  // Private Office lock state, owned by the office layer and enforced offline. A
  // lock is the one thing a generic sweep must not touch: deleting a passcode
  // record is indistinguishable, from the outside, from unlocking it.
  "pulsesoc.native.office.",

  // A once-per-device dedupe marker. Clearing it would let a referral be claimed
  // again on the same handset by signing out and back in.
  "pulsesoc.native.referral.claimAttempted",

  // Writes the user has already committed to and is waiting on. This is the one
  // entry that is NOT device state, and it is the entry the inversion most
  // nearly got wrong: the old six-prefix sweep did not match the outbox key, so
  // queued messages survived a sign-out by accident, and inverting the rule
  // started deleting them on purpose.
  //
  // A queued message is not a cache and not a draft. The user pressed Send and
  // has been looking at a bubble ever since; dropping it means a message they
  // believe was sent silently never sends, which is the exact failure
  // `core/mutations/outbox` exists to prevent. It is safe to leave because the
  // outbox namespaces its own storage per account and drains only the active
  // scope — the reason drafts must go, that the next account's composer reads
  // them back from a bare key, has no equivalent here. B cannot see or send A's
  // queue; A signing back in resumes it.
  "pulsesoc.native.outbox."
];

/**
 * True when a key outlives the account that wrote it.
 *
 * Anything else under {@link STORAGE_NAMESPACE} is the account's, including
 * content caches and unsent drafts. A draft must never be dropped for storage
 * pressure, but a sign-out is not storage pressure: leaving it would put one
 * person's half-written post in the next person's composer.
 */
export function isDeviceScopedKey(key: string): boolean {
  return DEVICE_KEEP_PREFIXES.some((prefix) => key.startsWith(prefix));
}

/**
 * The subset of `keys` that must not survive the account switch.
 *
 * Keys outside the namespace are left alone: they belong to a library rather
 * than to us, and deleting another package's storage is not this function's
 * decision to make.
 */
export function accountScopedKeys(keys: readonly string[]): string[] {
  return keys.filter((key) => key.startsWith(STORAGE_NAMESPACE) && !isDeviceScopedKey(key));
}
