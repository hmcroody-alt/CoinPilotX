/**
 * What leaves the app when someone shares a Reel.
 *
 * This is `postShare.ts` applied to the other object, and it is a sibling
 * rather than a generalisation on purpose: the two disagree about what a
 * missing caption should say and about which fields decide whether there is a
 * caption at all, and a shared helper taking four predicates as arguments would
 * hide that disagreement instead of stating it.
 *
 * ## The same allowlist, for the same reason
 *
 * `visibility` permits a preview when the server says exactly `"public"`, and
 * in no other case. `pulse_reel_payload` emits
 * `post.get("visibility") or merged.get("visibility") or "public"`, so a
 * genuinely public Reel always carries the literal and nothing is lost; an
 * unrecognised value, a value added to the product later, or a field the server
 * stopped sending all fall to the quiet share. A private caption in somebody's
 * SMS thread is not a bug you can take back.
 *
 * ## Why this does not reuse `reelContentState`
 *
 * `ReelPlayerCard` already classifies a Reel's availability, and it would read
 * well to call it here. It must not be called here. That function is a
 * *denylist* — `["restricted", "blocked", "private", "followers_only"]` — and an
 * availability string it has never seen falls through to `playable`. That is
 * the correct direction for a player, where refusing to draw a Reel nobody
 * objected to is the worse failure. It is exactly backwards for a share, where
 * the unknown value is the one you most want to be careful about. Same
 * vocabulary, opposite default, because the question is different.
 *
 * So availability is read as an allowlist too: blank (the field is optional and
 * usually absent) or the literal `"available"`. Anything else is restricted.
 *
 * ## The poster is metadata, not a thumbnail
 *
 * `previewImageUrl` reaches the OS share sheet, which hands it to whatever
 * target the person picks. For a non-public Reel that is the creator's frame
 * published to a surface the audience setting existed to keep it off. It drops
 * with the caption, the title and the author name — all four or none, because
 * a share that names the creator and shows their still while withholding only
 * the words has not withheld anything that mattered.
 *
 * Mux playback ids are not secret — PulseSoc mints assets `playback_policy:
 * "public"` and `image.mux.com/<id>/thumbnail.jpg` is a public URL by design —
 * but a *signed* storage URL is, and a caption that somehow contains one is
 * redacted whole rather than trimmed, because a signed URL minus its signature
 * is still a map of the bucket.
 */

import { PulseReel, reelPosterUrl } from "../api/reels";
import { PulseShareMetadata } from "./nativeShare";
import { truncatePreview } from "./postShare";

/** The single visibility that permits a caption preview. */
const PUBLIC_VISIBILITY = "public";

/**
 * The availability values that permit one. Blank counts: the field is optional
 * and most payloads omit it entirely, so treating absence as restriction would
 * silence every ordinary share.
 */
const AVAILABLE_STATES = ["", "available"];

/**
 * Moderation outcomes that stop a share from carrying the Reel's own words.
 *
 * Unlike the two above this is a denylist, and deliberately so. Moderation
 * status is not an audience decision — a Reel sitting in a review queue is
 * still the author's public Reel, and blanking every share whose moderation
 * field says something unfamiliar would make the common case quiet for no
 * privacy gain. What the author *published* is answered by `visibility`; this
 * only catches the Reel that has been ruled against.
 */
const MODERATION_DENIED = ["rejected", "blocked", "unavailable", "removed"];

const CREDENTIAL_PARAMS = /[?&](x-amz-signature|x-amz-credential|x-goog-signature|signature|token|access_token|sig|key-pair-id)=/i;
const URL_IN_TEXT = /\bhttps?:\/\/[^\s<>"'`]+/gi;

const HEADLINE = "Check out this Reel on PulseSoc 🎬";
const RESTRICTED_LINE = "Someone shared a Reel with you on PulseSoc.";
const CALL_TO_ACTION = "Watch the Reel:";
const DEFAULT_PREVIEW = "A Reel on PulseSoc.";

function lower(value?: string) {
  return String(value || "").trim().toLowerCase();
}

function redactCredentialedUrls(value: string) {
  return value.replace(URL_IN_TEXT, (match) => (CREDENTIAL_PARAMS.test(match) ? "[link removed]" : match));
}

function flatten(value: string) {
  return String(value || "")
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Whether this Reel's own words and picture may cross out of PulseSoc.
 *
 * Four independent questions, all of which must say yes: did the author publish
 * it publicly, does it still exist, may this viewer see it, and has it survived
 * moderation. They are separate fields because they fail separately — a deleted
 * public Reel and a live private one are both unshareable for reasons the other
 * check would miss.
 */
export function isReelPubliclyShareable(reel: Pick<PulseReel, "visibility" | "availability" | "visibility_state" | "is_removed" | "deleted_at" | "moderation_status">) {
  if (reel?.is_removed || reel?.deleted_at) return false;
  if (lower(reel?.visibility) !== PUBLIC_VISIBILITY) return false;
  if (!AVAILABLE_STATES.includes(lower(reel?.availability))) return false;
  if (!AVAILABLE_STATES.includes(lower(reel?.visibility_state))) return false;
  if (MODERATION_DENIED.includes(lower(reel?.moderation_status))) return false;
  return true;
}

export function buildReelSharePreview(reel: PulseReel) {
  if (!isReelPubliclyShareable(reel)) return { preview: "", restricted: true };
  const caption = truncatePreview(redactCredentialedUrls(String(reel.caption || reel.body || "")));
  return { preview: caption || DEFAULT_PREVIEW, restricted: false };
}

/**
 * The share body.
 *
 * Headline, then the Reel's own words, then the link behind a call to action.
 * A Reel with no caption collapses to headline plus link, which is the shape
 * the brief asked for; the caption line is what a Reel that has something to
 * say earns on top of it.
 */
export function buildReelShareMessage(reel: PulseReel, url: string) {
  const { preview, restricted } = buildReelSharePreview(reel);
  const lines = restricted ? [RESTRICTED_LINE] : [HEADLINE, preview];
  return [...lines, `${CALL_TO_ACTION} ${url}`].join("\n");
}

/**
 * The metadata a share sheet receives for a Reel.
 *
 * `url` is passed in rather than rebuilt here because the caller has the better
 * one: `POST /api/pulse/reels/:id/share` answers a link carrying the share
 * source, and what the recipient taps should be the link PulseSoc minted for
 * this share, not one reconstructed from an id.
 */
export function buildReelShareMetadata(reel: PulseReel, url: string): PulseShareMetadata {
  const restricted = !isReelPubliclyShareable(reel);
  const author = reel.author || {};
  return {
    kind: "reel",
    url,
    message: buildReelShareMessage(reel, url),
    title: restricted ? "" : flatten(reel.title || "") || "PulseSoc Reel",
    author: restricted ? "" : flatten(author.display_name || author.name || author.username || ""),
    description: restricted ? "" : buildReelSharePreview(reel).preview,
    previewImageUrl: restricted ? "" : reelPosterUrl(reel) || reel.poster_url || ""
  };
}
