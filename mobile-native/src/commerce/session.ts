/**
 * The correlation key that ties one app launch's commerce events together.
 *
 * The server stores this verbatim on the placement row and copies it onto every
 * impression, engagement and feedback event, which is what lets an analyst ask
 * "of the cards served in this session, how many were seen, tapped, hidden?".
 * That question needs the events to share *a* key. It does not need the key to
 * mean anything.
 *
 * So this is deliberately **not** a device id, an install id, or anything
 * derived from the user. It is random, it lives in memory, and it dies with the
 * process — a new launch is a new session and nothing joins the two. Reaching
 * for something persistent here (`expo-application`'s install id is right
 * there) would turn an analytics grouping key into a cross-session behavioural
 * identifier stamped on every product a person ever scrolled past, which is a
 * different data collection than the one the settings screen describes.
 *
 * Generated lazily rather than at import so a build that never renders a
 * commerce surface never mints one.
 */

let sessionId = "";

/** Random, in-memory, per-launch. Stable for the life of the process. */
export function commerceSessionId(): string {
  if (!sessionId) {
    // Not `crypto.randomUUID` — Hermes has no `crypto` global, and a polyfill
    // for a value that only needs to be distinct would be a dependency bought
    // for nothing. Two fields of entropy plus the clock is comfortably enough
    // to keep two launches on one device apart, which is the whole requirement.
    const left = Math.random().toString(36).slice(2, 10);
    const right = Math.random().toString(36).slice(2, 10);
    sessionId = `cs_${Date.now().toString(36)}${left}${right}`;
  }
  return sessionId;
}

/** Test-only: forget the current session so the next call mints a fresh one. */
export function __resetCommerceSessionId() {
  sessionId = "";
}
