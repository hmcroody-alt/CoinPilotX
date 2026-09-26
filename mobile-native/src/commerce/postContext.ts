/**
 * What the post carrying a card is *about*, in the four fields ranking reads.
 *
 * The sibling of `reelContext`, and deliberately a sibling rather than a shared
 * generic: the two payloads agree on almost nothing. A reel has `category`, a
 * `caption` and an `ai_tags` array. A `PulsePost` has a `title` and a `body`, and
 * that is the entire topical surface — see below. A function parameterised over
 * both would take a field-mapping argument, which is the same code with the
 * reasoning moved into the call sites and out of the docstring.
 *
 * ## A post carries less than it looks like it does
 *
 * Worth stating precisely, because three fields that would obviously belong here
 * are absent and two of them exist *nearby* under the same names:
 *
 *   * **No `category`.** Posts have none at any layer.
 *   * **No `topic`.** `topic` is a field of `FeedResponse` — the lane being
 *     browsed — not of a post in it. Reading `post.topic` type-errors, which is
 *     the only reason this is documented rather than shipped.
 *   * **No `tags`.** `CreatePostPayload` accepts `tags`, so the *author* can send
 *     them, but `PulsePost` does not carry them back and `normalizePost`
 *     constructs its result field by field, so anything the server returned
 *     under that name would be dropped before a caller could read it. Should
 *     tags ever be surfaced on a post, they belong at the top of the ordering
 *     below, ahead of the scraped hashtags.
 *
 * So the tags here are hashtags pulled out of prose and nothing else, and the
 * subject is the title or the body. That is a weaker signal than a reel's, and
 * weaker is the safe direction on this surface: `ranking.relevance()` answers
 * `NEUTRAL` (0.5) with no context and `0.0` for a context that matches nothing,
 * so a thin context *lowers* the ceiling on what can be shown rather than raising
 * it. With `min_score("post_detail")` sitting a tenth above the feed's floor, a
 * post whose subject we cannot read shows no card at all. That is the intended
 * outcome and the reason the floor was set where it was.
 *
 * ## Title before body
 *
 * An author-written title is a deliberate summary where a body is as often a
 * greeting as a description — the same ordering `reelContext` uses, for the same
 * reason.
 *
 * ## What is sent, and why it is not a privacy widening
 *
 * Only the post's own public text: its topic, its title or body, and the hashtags
 * the author typed into them. Every field arrived from the same server on the
 * same session moments earlier, and nothing about the *viewer* is added here —
 * the viewer-side signals ranking uses are read server-side from the account and
 * are never posted by a client. The server re-bounds all of it through the
 * allowlist in `_context_from_request`
 * (`services/commerce_discovery_routes.py:189`), so the caps below are a courtesy
 * to the wire, not the security boundary.
 *
 * A post's body is free text a user wrote, and the first 80 characters of it go
 * to the ranker as `topic`. That is the same exposure the caption already has on
 * reels, to the same first-party endpoint, and it is why `body` is last in the
 * ordering rather than first: when the author stated a topic we send the stated
 * one and never reach the prose.
 */
import type { CommerceContext } from "../api/commerceDiscovery";
import type { PulsePost } from "../api/feed";

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
 * The leading `#` is stripped because the listing side has none: a post tagged
 * `#sneakers` and a listing tagged `sneakers` are the same word, and leaving the
 * hash on makes them two — a silent total miss rather than a weak match.
 */
function normalizeTag(raw: unknown): string {
  if (typeof raw !== "string") return "";
  const tag = raw.trim().replace(/^#+/, "").toLowerCase().slice(0, MAX_TAG_CHARS);
  return tag.length >= MIN_TAG_CHARS ? tag : "";
}

/**
 * Hashtags out of free text, in order.
 *
 * On a post these are the *only* tags available, and they are frequently the only
 * topical signal of any kind: a photo with a three-word caption and four hashtags
 * is a common shape. The same text is also passed as `topic`, but only its first
 * 80 characters, and hashtags conventionally sit at the end — pulling them out
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
 * The ranking context for one post, or `null` when the post says nothing.
 *
 * `null` rather than an empty object, and the caller must pass it through as an
 * omitted field: an empty context and a *wrong* context are not the same
 * request. With nothing to match on, `relevance` returning NEUTRAL is the honest
 * answer and a card may still be worth showing on history and quality alone —
 * correctly scoped to the posts that genuinely have no readable subject rather
 * than applied to all of them.
 */
export function postCommerceContext(post: PulsePost | null | undefined): CommerceContext | null {
  if (!post) return null;

  // Sent as `topic` only. A post has no category of its own, and passing the
  // same string as `category` too would double one signal's weight against a
  // listing's real category field — a stronger claim than the text supports.
  const topic =
    trimmed(post.title, MAX_FIELD_CHARS) || trimmed(post.body, MAX_FIELD_CHARS);

  const tags: string[] = [];
  const seen = new Set<string>();
  for (const tag of hashtagsIn(`${post.title || ""} ${post.body || ""}`)) {
    if (seen.has(tag) || tags.length >= MAX_TAGS) continue;
    seen.add(tag);
    tags.push(tag);
  }

  if (!topic && tags.length === 0) return null;

  const context: CommerceContext = {};
  if (topic) context.topic = topic;
  if (tags.length > 0) context.tags = tags;
  return context;
}
