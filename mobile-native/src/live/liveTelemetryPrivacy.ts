/**
 * Stage 55. Making Live telemetry safe to emit.
 *
 * The problem is specific to this codebase and is easy to miss, because the
 * telemetry looks harmless. `emitAgoraLiveEvent` logs a field called `uid`, and
 * in PulseSoc the Agora uid *is* the user id — `services/live_participants.py`
 * defines `_agora_uid(user_id) == user_id`, and its own docstring flags the
 * consequence. So every one of the twenty-odd telemetry call sites in
 * `useAgoraLiveBroadcastRoom` was writing a PulseSoc account identifier into the
 * device log, where a crash reporter, an attached debugger, or anything else
 * reading `console` picks it up.
 *
 * Worse than the identifier itself is that it is *stable across Lives*. The same
 * number appears in the logs of every broadcast a person has ever been in, on
 * every device that watched them, which turns a stream of debug lines into a
 * record of who appeared with whom and when.
 *
 * The fix is a pseudonym rather than a redaction, because a redacted log is
 * useless: diagnosing "guest three never published audio" requires being able to
 * tell guest three apart from guest four. So each uid becomes a short tag that
 * is:
 *
 *   - **stable within one Live on one device**, so a session's events can be
 *     followed end to end;
 *   - **different in a different Live**, because the tag is salted with the live
 *     id — the cross-session correlation above is broken at the root;
 *   - **different after an app restart**, because the salt also includes a value
 *     generated once per process, so logs cannot be joined into a history;
 *   - **not reversible to a user id**, so a leaked log is not a leaked account.
 *
 * The honest cost: because the process salt is per-device, the host's log and
 * the guest's log give the same person two different tags, so a two-device
 * investigation cannot join on the tag and has to join on `liveId` and timing
 * instead. That is a real loss of debuggability, and it is accepted deliberately
 * — a telemetry scheme that lets *us* join logs across devices is one that lets
 * anyone else do it too.
 *
 * This is not cryptography and does not pretend to be. The uid space is small
 * enough that someone holding the salt could enumerate it. The salt is never
 * emitted, which is what the guarantee actually rests on.
 */

/**
 * Per-process salt. Generated once, never logged, never persisted.
 *
 * `Math.random` is the right tool here precisely because this is not a security
 * boundary against a local attacker — someone who can read the app's memory can
 * read the uids directly. It is a boundary against *log aggregation*, and any
 * value that differs per process achieves that.
 */
const PROCESS_SALT = Math.floor(Math.random() * 0xffffffff) >>> 0;

/** FNV-1a. Chosen for being short, dependency-free and well-distributed. */
function hash(input: string): number {
  let value = 0x811c9dc5;
  for (let index = 0; index < input.length; index += 1) {
    value ^= input.charCodeAt(index);
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value >>> 0;
}

/**
 * The pseudonym for one participant in one Live.
 *
 * Returns an empty string for a missing or non-positive uid rather than tagging
 * zero, so "we do not know who this is" stays visibly different from "this is
 * someone" in the logs.
 */
export function participantTag(liveId: unknown, uid: unknown): string {
  const numericUid = Math.floor(Number(uid) || 0);
  if (!(numericUid > 0)) return "";
  const scope = Math.floor(Number(liveId) || 0);
  return `p_${hash(`${PROCESS_SALT}:${scope}:${numericUid}`).toString(36)}`;
}

/**
 * Keys that must never reach a log, whatever a future call site is holding.
 *
 * An allowlist would be safer still, but it would also silently drop new
 * diagnostic fields and so would be quietly worked around. A denylist of the
 * things that are actually dangerous, enforced centrally, is the version that
 * survives contact with people adding fields under deadline.
 */
const FORBIDDEN_KEY = /token|secret|password|credential|appid|app_id|certificate|signature|authorization|cookie|email|phone/i;

/** Free-text fields are capped so a stack trace or a blob cannot ride along. */
const MAX_TEXT = 120;

/**
 * Strip a Live telemetry event down to what is safe to emit.
 *
 * Three transformations, in order of importance:
 *
 *   1. `uid` is replaced by `participant`. The key is *renamed*, not just
 *      rewritten, so that a reader of a log line cannot mistake the tag for a
 *      user id, and so that a grep for `uid` across the telemetry surface finds
 *      nothing.
 *   2. Any key that looks like a credential is dropped entirely.
 *   3. Strings are truncated and non-finite numbers are dropped.
 *
 * `liveId` is deliberately kept. It identifies a broadcast, which is a public
 * object — the Live appears in the feed — and it is the only field that makes
 * the rest of the telemetry navigable.
 */
export function sanitizeLiveTelemetry(event: Record<string, unknown>): Record<string, unknown> {
  const source = event && typeof event === "object" ? event : {};
  const liveId = source.liveId;
  const safe: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(source)) {
    if (FORBIDDEN_KEY.test(key)) continue;
    if (key === "uid") {
      const tag = participantTag(liveId, value);
      if (tag) safe.participant = tag;
      continue;
    }
    if (typeof value === "string") {
      safe[key] = value.length > MAX_TEXT ? `${value.slice(0, MAX_TEXT)}…` : value;
      continue;
    }
    if (typeof value === "number") {
      if (Number.isFinite(value)) safe[key] = value;
      continue;
    }
    if (value === null || value === undefined) continue;
    if (typeof value === "boolean") {
      safe[key] = value;
      continue;
    }
    // Objects and arrays are not emitted. Nothing in the event contract needs
    // them, and the one way an SDK stats object ends up in a log wholesale is
    // somebody passing it through "temporarily".
  }

  return safe;
}
