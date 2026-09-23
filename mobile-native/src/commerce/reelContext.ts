/**
 * What the reel carrying a chip is *about*, in the four fields ranking reads.
 *
 * ## Why this exists
 *
 * Until this module, the Reels surface asked the recommendation engine for a
 * product while telling it nothing about the video the product would sit on.
 * `ranking.relevance()` answers `NEUTRAL` (0.5) when it is given no context, so
 * every reels chip was ranked on user history and listing quality alone and the
 * only thing standing between the viewer and an unrelated product was the reels
 * relevance floor. That is the brief's §8 inverted: matching was not happening,
 * it was merely being screened for afterwards.
 *
 * ## Why a mismatch is *better* than no context
 *
 * This is the non-obvious half. Sending context is not a strict improvement to
 * the score — it is a two-sided bet. With context, an unrelated listing scores
 * `0.0` on relevance, which is *below* the neutral 0.5 it used to get for free.
 * Combined with `min_score("reels")` being the strictest floor in the system
 * (base + 0.20), an off-topic product now falls under the floor and the response
 * is an empty list. That is the intended outcome, in the brief's own words: *no
 * recommendation is better than a bad recommendation*. Expect fewer chips after
 * this change, not more, and expect the ones that survive to be about the video.
 *
 * ## What is sent, and why it is not a privacy widening
 *
 * Only the reel's own public metadata — the category, the title or caption, and
 * the tags — every field of which this client received from the same server, on
 * the same session, moments earlier. Nothing about the viewer is added here; the
 * viewer-side signals ranking uses are read server-side from the account, never
 * posted by the client. The server re-bounds all of it through the allowlist in
 * `_context_from_request` (`services/commerce_discovery_routes.py:189`), so the
 * caps below are a courtesy to the wire, not the security boundary.
 */
import type { CommerceContext } from "../api/commerceDiscovery";
import type { PulseReel } from "../api/reels";

/** Mirrors the server's allowlist caps so the wire carries nothing it will trim. */
const MAX_FIELD_CHARS = 80;
const MAX_TAGS = 12;
const MAX_TAG_CHARS = 40;

/**
 * `ranking._tokens` discards words of two characters or fewer as stopwords, so a
 * shorter tag cannot contribute to a match — it only consumes one of the twelve
 * slots that a real signal could have used.
 */
const MIN_TAG_CHARS = 3;

const HASHTAG = /#([\p{L}\p{N}_]{2,40})/gu;

function trimmed(value: unknown, max: number): string {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, max);
}

/**
 * A tag, or "" for anything that cannot be one.
 *
 * The leading `#` is stripped because the listing side has none: a reel tagged
 * `#sneakers` and a listing tagged `sneakers` are the same word, and leaving the
 * hash on makes them two, which is a silent total miss rather than a weak match.
 */
function normalizeTag(raw: unknown): string {
  if (typeof raw !== "string") return "";
  const tag = raw.trim().replace(/^#+/, "").toLowerCase().slice(0, MAX_TAG_CHARS);
  return tag.length >= MIN_TAG_CHARS ? tag : "";
}

/**
 * Hashtags out of free text, in order.
 *
 * Captions are frequently the only topical signal a reel carries — plenty of
 * reels have no category and no tag array, and their entire subject is three
 * hashtags. The caption is also passed as `topic`, but only its first 80
 * characters, and hashtags conventionally sit at the *end*; pulling them out
 * separately is what stops the one usable signal from being truncated away.
 */
function hashtagsIn(text: string): string[] {
  const found: string[] = [];
  if (!text) return found;
  for (const match of text.matchAll(HASHTAG)) {
    const tag = normalizeTag(match[1]);
    if (tag) found.push(tag);
  }
  return found;
}

/**
 * The ranking context for one reel, or `null` when the reel says nothing.
 *
 * `null` rather than an empty object is deliberate and the caller must pass it
 * through as an omitted field: an empty context and a *wrong* context are not
 * the same request. With nothing to match on, `relevance` returning NEUTRAL is
 * the honest answer and a chip may still be worth showing on history and
 * quality alone — the pre-§8 behaviour, correctly scoped to the reels that
 * genuinely have no topic rather than applied to all of them.
 */
export function reelCommerceContext(reel: PulseReel | null | undefined): CommerceContext | null {
  if (!reel) return null;

  const category = trimmed(reel.category, MAX_FIELD_CHARS);
  // Title first: an author-written title is a deliberate summary, where a
  // caption is as often a greeting as a description. Either beats `body`.
  const topic =
    trimmed(reel.title, MAX_FIELD_CHARS) ||
    trimmed(reel.caption, MAX_FIELD_CHARS) ||
    trimmed(reel.body, MAX_FIELD_CHARS);

  const tags: string[] = [];
  const seen = new Set<string>();
  const push = (candidate: string) => {
    if (!candidate || seen.has(candidate) || tags.length >= MAX_TAGS) return;
    seen.add(candidate);
    tags.push(candidate);
  };

  // Author-supplied tags rank ahead of machine-derived ones, and both rank
  // ahead of hashtags scraped out of prose — the twelve slots are filled in
  // descending order of how deliberate the signal is.
  for (const raw of Array.isArray(reel.tags) ? reel.tags : []) push(normalizeTag(raw));
  for (const raw of Array.isArray(reel.ai_tags) ? reel.ai_tags : []) push(normalizeTag(raw));
  for (const tag of hashtagsIn(`${reel.caption || ""} ${reel.title || ""}`)) push(tag);

  if (!category && !topic && tags.length === 0) return null;

  const context: CommerceContext = {};
  if (category) context.category = category;
  if (topic) context.topic = topic;
  if (tags.length > 0) context.tags = tags;
  return context;
}
