/**
 * What leaves the app when someone shares a post.
 *
 * A share is the one moment a post's text crosses from PulseSoc into somewhere
 * PulseSoc has no control over — a tweet, an SMS, a clipboard, another person's
 * notification shade. Everything here is about being deliberate at that
 * boundary, in two directions at once: the share should carry enough for the
 * recipient to want to tap it, and it should carry nothing the author did not
 * publish.
 *
 * ## The body is opt-in, not opt-out
 *
 * `visibility` is read as an allowlist of exactly one value. A post previews its
 * body when the server says `"public"` and in no other case — not for an
 * unrecognised visibility, not for a missing field, not for a value added to the
 * product after this file was written. The failure mode of the other direction
 * is a private caption sitting in someone's SMS thread forever, which is not a
 * bug you can take back, so the direction to be wrong in is "this share is
 * blander than it needed to be".
 *
 * That costs nothing in practice: `_public_post` emits
 * `item.get("visibility") or "public"`, so a genuinely public post always
 * carries the literal. If that ever stops being true, shares get quieter rather
 * than louder.
 *
 * ## Only the body is ever read
 *
 * The preview is built from `post.body` alone. No media URL, no avatar URL, no
 * author id, no media row id reaches the text — not because they are filtered
 * out but because they are never looked up. The canonical post URL is the only
 * identifier that leaves, and it is the same one the web uses publicly.
 *
 * The one thing that *is* filtered is a credentialed URL inside the body. A
 * pre-signed storage link carries `X-Amz-Signature` in its query string, and a
 * body that somehow contains one would otherwise hand a working credential to
 * whoever received the share.
 */

import { PulsePost, pulsePostUrl } from "../api/feed";
import { resolvePulseEntity } from "../links/pulseEntity";
import { PulseShareMetadata } from "./nativeShare";

/** The single visibility that permits a body preview. */
const PUBLIC_VISIBILITY = "public";

/** Long enough to be worth reading, short enough to survive an SMS preview. */
const MAX_PREVIEW_LENGTH = 180;

const HEADLINE = "Check this out on PulseSoc 👀";
const RESTRICTED_LINE = "Someone shared a post with you on PulseSoc.";
const CALL_TO_ACTION = "View the post:";

/**
 * Query parameters that turn a URL into a credential. Presence of any one of
 * them means the whole URL is redacted rather than trimmed — a signed URL with
 * its signature removed is still an internal storage path, and publishing the
 * bucket layout is its own small leak.
 */
const CREDENTIAL_PARAMS = /[?&](x-amz-signature|x-amz-credential|x-goog-signature|signature|token|access_token|sig|key-pair-id)=/i;

const URL_IN_TEXT = /\bhttps?:\/\/[^\s<>"'`]+/gi;

function redactCredentialedUrls(value: string) {
  return value.replace(URL_IN_TEXT, (match) => (CREDENTIAL_PARAMS.test(match) ? "[link removed]" : match));
}

/**
 * Collapse to a single readable line.
 *
 * Newlines go because the share payload is itself line-structured — a body with
 * its own line breaks would blur into the call to action and make the link look
 * like part of the caption.
 */
function flatten(value: string) {
  return String(value || "")
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Truncate on a word boundary when there is one close to the limit.
 *
 * Cutting mid-word reads as a rendering bug rather than an excerpt, but backing
 * up to the previous space is only worth it if the space is reasonably near the
 * end — otherwise a single long token would shrink the preview to almost
 * nothing.
 */
export function truncatePreview(value: string, maxLength = MAX_PREVIEW_LENGTH) {
  const flat = flatten(value);
  if (flat.length <= maxLength) return flat;
  const clipped = flat.slice(0, maxLength);
  const lastSpace = clipped.lastIndexOf(" ");
  const base = lastSpace > maxLength * 0.6 ? clipped.slice(0, lastSpace) : clipped;
  return `${base.replace(/[\s.,;:!?-]+$/, "")}…`;
}

export function isPubliclyShareable(post: Pick<PulsePost, "visibility">) {
  return String(post?.visibility || "").trim().toLowerCase() === PUBLIC_VISIBILITY;
}

function hasMediaOfType(post: PulsePost, type: "video" | "image") {
  return (post.media || []).some((media) => String(media?.media_type || media?.type || "").toLowerCase().includes(type));
}

/**
 * The line used when there is no caption to show — either because the post has
 * none, or because it is not public enough to quote. It describes the shape of
 * the post, which is public information already implied by the link.
 */
function defaultPreviewLine(post: PulsePost) {
  if (hasMediaOfType(post, "video") || post.video_url) return "A video on PulseSoc.";
  if (hasMediaOfType(post, "image") || post.image_url) return "A photo on PulseSoc.";
  return "A post on PulseSoc.";
}

export function buildPostSharePreview(post: PulsePost) {
  if (!isPubliclyShareable(post)) return { preview: "", restricted: true };
  const body = truncatePreview(redactCredentialedUrls(String(post.body || post.text || post.content || "")));
  return { preview: body || defaultPreviewLine(post), restricted: false };
}

export function buildPostShareMessage(post: PulsePost, url: string) {
  const { preview, restricted } = buildPostSharePreview(post);
  const lines = restricted ? [RESTRICTED_LINE] : [HEADLINE, preview];
  return [...lines, `${CALL_TO_ACTION} ${url}`].join("\n");
}

/**
 * The metadata a share sheet receives for a post.
 *
 * Title, author and preview image are all dropped for a restricted post. The
 * share sheet passes them to whatever target the person picks, and a target
 * that renders a title and an author picture has published exactly the thing
 * the visibility setting existed to prevent.
 */
/**
 * What to actually put in the message when sharing into PulseSoc Messenger.
 *
 * Everywhere else, the composed share text *is* the share: an SMS or a tweet
 * has no idea what a PulseSoc post is and needs the caption spelled out.
 * Messenger does know, and renders the link as a card with the author, the
 * thumbnail and the live caption on it. Sending the composed text there would
 * put a second, frozen copy of the caption directly above the card showing the
 * current one — and that copy would stay frozen after an edit, a takedown or a
 * visibility change, which is the whole problem with storing a preview instead
 * of deriving one.
 *
 * So for anything Messenger can card, the message is the canonical URL and
 * nothing else. There is no raw URL on screen either, because a body that is
 * only a link is replaced by its card. Anything Messenger cannot card keeps the
 * composed text, which is still the most useful thing that surface can show.
 */
export function messengerShareBody(metadata: Pick<PulseShareMetadata, "url">, composed: string) {
  return resolvePulseEntity(metadata.url) ? metadata.url : composed;
}

export function buildPostShareMetadata(post: PulsePost): PulseShareMetadata {
  const url = pulsePostUrl(Number(post.post_id || post.id || 0));
  const restricted = !isPubliclyShareable(post);
  const author = post.author || post.user || {};
  return {
    kind: "post",
    url,
    message: buildPostShareMessage(post, url),
    title: restricted ? "" : flatten(post.title || "") || "PulseSoc Post",
    author: restricted ? "" : flatten(author.display_name || author.name || author.username || post.author_name || ""),
    description: restricted ? "" : buildPostSharePreview(post).preview,
    previewImageUrl: restricted ? "" : post.thumbnail_url || post.image_url || ""
  };
}
