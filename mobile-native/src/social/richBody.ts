/**
 * One segmentation of a body that carries both mentions and links.
 *
 * ## Why the two segmenters have to be composed rather than chosen between
 *
 * `segmentMentions` and `segmentLinks` were written to the same shape —
 * `{ text, ...meta }[]` — and `messageLinks.ts` says why in as many words: a
 * segment list composes with the other things that decorate a body, where a
 * string rewrite fights them. This is that composition, and it is the "future
 * body renderer" that comment was anticipating.
 *
 * Until now the choice was exclusive. `CommentThread`'s `CommentBody` ran
 * mentions *or* translation, and links not at all; so a comment containing a
 * link rendered it as prose while a comment containing a mention got a tappable
 * span. Both are bodies of user text and there is no reason for them to
 * disagree about what is in them.
 *
 * ## Links are found first, and that ordering is the whole correctness argument
 *
 * It is not stylistic. `MENTION_PATTERN` is `/(^|[^\w@])@([A-Za-z0-9_.]{1,30})/`
 * — the character before the `@` only has to be a non-word character, and `/`
 * qualifies. So a perfectly ordinary URL with a handle in its path is torn in
 * half by the mention pass. Measured, not inferred:
 *
 *     segmentMentions("see https://pulsesoc.com/@bob and hi @carol")
 *       -> "see https://pulsesoc.com/" | @bob(mention) | " and hi " | @carol(mention)
 *
 * Composing in that order and then running the link pass over the prose is worse
 * than losing the link, because the truncated fragment is still a *valid* URL
 * and the link pass duly claims it:
 *
 *     mentionsFirst("see https://pulsesoc.com/@bob")
 *       -> "see " | https://pulsesoc.com/(link) | @bob(mention)
 *
 * So the reader gets an underlined tap target over the domain half of the URL
 * that silently navigates to the site root, plus a mention of a user who was
 * never mentioned. Nothing is dead and nothing throws; the destination is just
 * wrong. The link pass has no matching failure in the other direction — it
 * already keeps
 * `https://pulsesoc.com/@bob` whole and already leaves a bare `@carol` in the
 * prose it hands back:
 *
 *     segmentLinks("see https://pulsesoc.com/@bob and hi @carol")
 *       -> "see " | https://pulsesoc.com/@bob(link) | " and hi @carol"
 *
 * So links are segmented over the whole body, and mentions are segmented only
 * over the runs that the link pass did not claim. A mention inside a URL is
 * part of the URL, which is the reading every other product has converged on
 * and the only one where the tap target matches what the text says.
 *
 * ## What this does not do
 *
 * It does not decide where anything goes. A `url` segment is a string for
 * `openContentLink` to classify and a `username` segment is a string for the
 * caller's mention handler. No routing table is consulted here and none is
 * duplicated.
 */

import { segmentLinks } from "../links/messageLinks";
import { segmentMentions } from "./mentions";

/**
 * A run of body text, optionally carrying what it is.
 *
 * `url` and `username` are mutually exclusive by construction — mentions are
 * only sought inside text the link pass left alone — so a renderer may check
 * them in either order without the two disagreeing.
 */
export type RichSegment = {
  text: string;
  /** The normalised URL, when this run is a link. Not the displayed slice. */
  url?: string;
  /** The username without its "@", when this run is a mention. */
  username?: string;
};

/**
 * Split `body` into link, mention and plain runs, in source order.
 *
 * Returns `[]` for an empty body rather than `[{ text: "" }]`, matching
 * `segmentMentions`, so `segments.length` stays usable as "is there anything to
 * draw".
 */
export function segmentRichBody(body: string): RichSegment[] {
  if (!body) return [];
  const out: RichSegment[] = [];
  for (const linkSegment of segmentLinks(body)) {
    if (linkSegment.url) {
      // A claimed link is atomic. Nothing inside it is looked at again, which
      // is precisely what stops the "@" in a profile URL becoming a mention.
      out.push({ text: linkSegment.text, url: linkSegment.url });
      continue;
    }
    for (const mentionSegment of segmentMentions(linkSegment.text)) {
      out.push(
        mentionSegment.username
          ? { text: mentionSegment.text, username: mentionSegment.username }
          : { text: mentionSegment.text }
      );
    }
  }
  return out;
}

/** Whether a body has anything worth rendering as more than a single `<Text>`. */
export function hasRichContent(body: string): boolean {
  return segmentRichBody(body).some((segment) => Boolean(segment.url || segment.username));
}
