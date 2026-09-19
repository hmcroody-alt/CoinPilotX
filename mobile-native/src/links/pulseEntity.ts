/**
 * Recognising *what* a PulseSoc link points at, once a link has been found.
 *
 * `messageLinks.ts` answers "is this a link, and does the app claim it".
 * This answers the next question — "which object is it" — and it is a separate
 * question because the two have different failure modes. A link the app claims
 * but this module does not recognise is still perfectly openable; it just gets
 * rendered as text rather than as a card. Nothing breaks, the experience is
 * merely plainer, which is the direction to be wrong in.
 *
 * ## It asks rather than knows
 *
 * The path table is still `navigation/linking.ts`. `classifyLink` is called
 * first and only its `internal` verdict is considered, so a path the app has
 * stopped claiming stops producing cards on the same day it stops opening
 * natively — the card and the destination cannot drift apart, because the card
 * only exists when the destination does.
 *
 * The patterns below are therefore not a second routing table. They are a
 * narrowing of one: every pattern here must already be claimed over there, and
 * if it is not, `classifyLink` rejects it before these are consulted.
 *
 * ## Three kinds, and the reason there are not six
 *
 * Posts, profiles and reels resolve. Marketplace listings, stores and
 * conversations deliberately do not, and the reason is the rule in
 * `entityPreview.ts`: a card may only exist where the preview can be fetched
 * with the *same request a tap makes*. That is not true for the others today —
 * marketplace has no read-one endpoint, stores have no public by-id read, and
 * the only conversation read is a message-page fetch that writes to the local
 * cache. Giving any of them a card would mean inventing a second,
 * differently-authorized way to read them, which is the one thing this pair of
 * modules exists to prevent.
 *
 * Reels were in that list until recently, and the way they left it is the
 * point. They did not get an exception; they got the missing read. There was
 * no `GET /api/pulse/reels/:id` — the route was PATCH/DELETE only, and the
 * only way to obtain a reel was `/api/pulse/reels/feed`, a *ranked page* that
 * may simply not contain the one you asked about. So "fetch reel 38" was not a
 * request anybody could make, and the rule held: no read, no card, naked url.
 * The route now answers GET by projecting the same viewer-scoped
 * `pulse_reel_payload` the Reels surface itself reads, so the rule is still
 * satisfied rather than bent.
 *
 * So the union grows when an entity gains a real by-id read, not before. A
 * resolver kind with no renderer behind it is dead code that reads as finished
 * work, so the kinds arrive with their cards.
 */

import { classifyLink } from "./messageLinks";

type EntityBase = {
  /** The canonical URL, normalised. What a tap opens and what the cache keys on. */
  url: string;
  /** The in-app path, from the routing table. `openMessageLink` wants this. */
  path: string;
};

export type PulseEntityRef =
  /** The server's id for the object. Public — it is in the URL the web serves. */
  | ({ kind: "post"; id: number } & EntityBase)
  /**
   * A profile is addressed by *key*, not by row id: the same person resolves
   * from a username, a public player id or a numeric id, and the server decides
   * which of those a given string is. The key is kept exactly as written so the
   * card and the tap look the same person up the same way.
   */
  | ({ kind: "profile"; id: string } & EntityBase)
  /**
   * A reel's id, from `pulse_reels.id`. Numeric like a post's and from a
   * different table, so the two are only distinguishable by `kind` — which is
   * why every comparison in this module carries `kind` alongside `id`.
   */
  | ({ kind: "reel"; id: number } & EntityBase);

/** `/pulse/post/2432` and nothing else — a trailing segment is a different page. */
const POST_PATH = /^\/pulse\/post\/(\d+)$/i;

/**
 * `/pulse/reels/38`. Plural, because that is what the route table claims.
 *
 * `/pulse/reel/38` is not here and is not an oversight: `classifyLink` rejects
 * it as external before this is ever consulted, so adding it would produce a
 * card for a link the app cannot open. `/open/reel/38` is likewise absent —
 * it is a public interstitial that *deliberately* does not open the app (it
 * renders an App Store link and a `pulsesoc://` button for the reader to
 * choose between), so a card claiming "View Reel →" over it would promise a
 * native destination the URL does not have.
 *
 * Bare `/pulse/reels` is the feed, not a reel, and the `\d+` keeps it out.
 */
const REEL_PATH = /^\/pulse\/reels\/(\d+)$/i;

/** `/pulse/profile/roody` — one segment, which is what the route table claims. */
const PROFILE_PATH = /^\/pulse\/profile\/([^/]+)$/i;

/**
 * Keys the server could plausibly resolve. Anything else is not worth a request.
 *
 * This is a *shape* check, not an existence check — an unknown-but-well-formed
 * key still goes to the server and comes back 404, because deciding locally
 * which handles exist is exactly the kind of second opinion that drifts.
 */
const PROFILE_KEY = /^[A-Za-z0-9._@-]{1,64}$/;

/**
 * Segments under `/pulse/profile/` that are screens rather than people.
 *
 * `linking.ts` routes `/pulse/profile/edit` to the settings screen before it
 * consults `profileTargetFromUrl`, so the path is shaped like a profile but is
 * not one. Without this the card would look up a member called "edit", get a
 * 404 and tell the reader that somebody's profile had been deleted.
 */
const PROFILE_RESERVED = new Set(["edit"]);

/**
 * The entity a URL points at, or `null` if it is not one this app can card.
 *
 * Query strings and fragments are deliberately ignored for *matching* but kept
 * on the `url`: `?utm_source=x` does not change which post it is, and dropping
 * it from the opened URL would quietly rewrite what someone sent.
 */
export function resolvePulseEntity(rawUrl: string): PulseEntityRef | null {
  const destination = classifyLink(rawUrl);
  if (destination.kind !== "internal") return null;
  let pathname: string;
  try {
    pathname = new URL(destination.url).pathname.replace(/\/+$/, "");
  } catch {
    return null;
  }
  const post = POST_PATH.exec(pathname);
  if (post) {
    const id = Number(post[1]);
    // `/pulse/post/0` parses and is claimed, but there is no post zero. Sending
    // it to the resolver would spend a request to learn that.
    if (!Number.isSafeInteger(id) || id <= 0) return null;
    return { kind: "post", id, url: destination.url, path: destination.path };
  }
  const reel = REEL_PATH.exec(pathname);
  if (reel) {
    const id = Number(reel[1]);
    // Same reasoning as post zero: `/pulse/reels/0` parses and is claimed, but
    // there is no reel zero, and asking would spend a request to be told so.
    if (!Number.isSafeInteger(id) || id <= 0) return null;
    return { kind: "reel", id, url: destination.url, path: destination.path };
  }
  const profile = PROFILE_PATH.exec(pathname);
  if (profile) {
    // The pathname is percent-encoded; the API layer encodes again when it
    // builds the request, so handing it the still-encoded segment would look
    // up `roody%2Echerie` rather than `roody.cherie`.
    let key: string;
    try {
      key = decodeURIComponent(profile[1]);
    } catch {
      return null;
    }
    if (!PROFILE_KEY.test(key)) return null;
    if (PROFILE_RESERVED.has(key.toLowerCase())) return null;
    return { kind: "profile", id: key, url: destination.url, path: destination.path };
  }
  return null;
}

/**
 * The entity a message is *about*, if it is about exactly one.
 *
 * A body with two entity links is not given a card. The card is a claim that
 * this message is that object, and with two candidates the claim is a guess —
 * whichever one came first would be promoted over the other for no reason the
 * sender chose. Those bodies keep their inline tappable links, which is the
 * honest rendering of "here are two things".
 *
 * A body with one entity link keeps its links too. The card is additive; it
 * never replaces the text, because the sender may have written a sentence
 * around the link and that sentence is theirs.
 */
export function messageEntity(body: string, links: readonly string[]): PulseEntityRef | null {
  return uniqueEntity(body, links);
}

/**
 * Whether the body says nothing except "this object".
 *
 * A message that is only a link has no sentence to preserve, so the card can
 * stand in for the text entirely and the raw URL never has to be shown. A
 * message with prose around the link keeps both: the prose is the sender's and
 * the card is additive.
 *
 * The comparison is on the body with every detected link removed, so
 * `"  https://…/post/1  "` counts as bare and `"look at this https://…/post/1"`
 * does not.
 */
export function bodyIsOnlyLinks(body: string, links: readonly string[]): boolean {
  let remainder = String(body || "");
  links.forEach((link) => {
    remainder = remainder.split(link).join(" ");
  });
  return remainder.trim().length === 0;
}

function uniqueEntity(body: string, links: readonly string[]): PulseEntityRef | null {
  if (!body) return null;
  const entities: PulseEntityRef[] = [];
  links.forEach((link) => {
    const entity = resolvePulseEntity(link);
    // Two links to the *same* post is one subject, not an ambiguity -- a body
    // that repeats the URL still unambiguously means that post.
    if (entity && !entities.some((seen) => seen.kind === entity.kind && seen.id === entity.id)) {
      entities.push(entity);
    }
  });
  return entities.length === 1 ? entities[0] : null;
}
