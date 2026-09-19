/**
 * The message that travels with an invite link.
 *
 * Before this module the Progress screen shared `{ message: link }` — a bare
 * URL and nothing else. The recipient got a string of characters from someone
 * they know, with no sentence explaining what it was or why it had arrived.
 * That is the problem this file exists to fix, and the fix is entirely about
 * the words: the link itself is unchanged.
 *
 * **The link architecture is deliberately untouched.** Invites go out as
 * `https://pulsesoc.com/r/<code>`, built by the server and returned from
 * `/api/progress/invite`. That endpoint already does the hard part: `/r/`
 * user-agent-detects iOS and redirects those visitors straight to the App
 * Store, writing a deferred-attribution row keyed on ip hash and device family
 * that the app redeems after signup via `POST /api/mobile/referral/claim`. It
 * solves the case that dominates for an invite — the recipient does *not* have
 * the app yet — which a Universal Link cannot solve at all. Nothing here mints
 * a token, adds a route, or introduces a second link system.
 *
 * **Templates are a fixed set, chosen explicitly, never generated.** Five
 * approved tones live below and are the only sentences this app will ever put
 * in a user's outgoing message. Nothing is assembled from fragments at runtime
 * and nothing is picked at random, so the same inputs always produce the same
 * message — which is what makes the copy reviewable and translatable at all.
 *
 * **Personalisation uses the public username and nothing else.** No display
 * name, no email, no user id, no referral code beyond the one already inside
 * the URL. The handle is appended as its own trailing line rather than
 * interpolated mid-sentence, which means an unusable or missing username
 * degrades by dropping one line instead of rendering a sentence with a hole in
 * it. `inviteHandle` is deny-by-default: anything that is not a plain public
 * handle is discarded rather than passed through.
 */

import type { TranslateOptions } from "../i18n/engine";

/** Matches `t` from `useTranslation()` without importing the React binding. */
export type InviteTranslate = (key: string, options?: TranslateOptions) => string;

export type InviteTone = "default" | "casual" | "network" | "discovery" | "short";

export const INVITE_TONES: readonly InviteTone[] = [
  "default",
  "casual",
  "network",
  "discovery",
  "short"
];

export const DEFAULT_INVITE_TONE: InviteTone = "default";

/**
 * Written out rather than built with a template literal so that every catalog
 * key in this file is findable by grepping for it — the same reason the i18n
 * tooling can see them.
 */
const TONE_KEYS: Record<InviteTone, string> = {
  default: "progress:invite.message.default",
  casual: "progress:invite.message.casual",
  network: "progress:invite.message.network",
  discovery: "progress:invite.message.discovery",
  short: "progress:invite.message.short"
};

const HANDLE_KEY = "progress:invite.message.handle";
const SUBJECT_KEY = "progress:invite.message.subject";

/**
 * A public PulseSoc handle: letters, digits, dot, underscore, hyphen.
 *
 * Deliberately narrow. This value is about to be placed in a message the user
 * sends to someone else, so the question is not "could this be a username" but
 * "is this definitely nothing else" — a display name with a space, an email
 * address, or an empty string all fail here and cost one optional line.
 */
const PUBLIC_HANDLE = /^[A-Za-z0-9._-]{2,30}$/;

/** The handle to advertise, or `""` when there is nothing safe to advertise. */
export function inviteHandle(username?: string | null): string {
  const handle = String(username ?? "").trim().replace(/^@+/, "");
  return PUBLIC_HANDLE.test(handle) ? handle : "";
}

export type InviteMessageInput = {
  /** The canonical `https://pulsesoc.com/r/<code>` link from the server. */
  link: string;
  t: InviteTranslate;
  tone?: InviteTone;
  /** The sender's public username. Anything unusable is dropped. */
  username?: string | null;
};

/**
 * Builds the full outgoing message: the chosen tone, the link on its own line,
 * and the sender's handle as a trailing line when one is available.
 *
 * The link goes on its own line because messaging clients linkify a URL that
 * stands alone far more reliably than one trailed by punctuation — a link
 * ending in `:` or `.` regularly gets the punctuation swallowed into the href.
 *
 * Returns `""` when there is no link, which is the caller's signal that there
 * is nothing to share yet.
 */
export function buildInviteMessage({
  link,
  t,
  tone = DEFAULT_INVITE_TONE,
  username
}: InviteMessageInput): string {
  const url = String(link ?? "").trim();
  if (!url) return "";

  const key = TONE_KEYS[tone] ? TONE_KEYS[tone] : TONE_KEYS[DEFAULT_INVITE_TONE];
  const body = String(t(key) ?? "").trim();
  const lead = body ? `${body}\n${url}` : url;

  const handle = inviteHandle(username);
  if (!handle) return lead;

  const handleLine = String(t(HANDLE_KEY, { username: handle }) ?? "").trim();
  return handleLine ? `${lead}\n\n${handleLine}` : lead;
}

/** Subject line for share targets that have one, such as mail. */
export function inviteSubject(t: InviteTranslate): string {
  return String(t(SUBJECT_KEY) ?? "").trim();
}

export type InviteSharePayload = { message: string; subject: string };

/**
 * The payload for the OS share sheet.
 *
 * `url` is deliberately **not** a separate field. On iOS, passing `message`
 * and `url` together hands the share sheet two activity items, and a target is
 * free to take one and drop the other — which is exactly how the sentence
 * explaining the invite goes missing. Carrying the link inside the message
 * guarantees the two arrive together everywhere, at the cost of the richer
 * preview a standalone `url` would sometimes produce. For an invite the
 * sentence matters more than the preview; recipients still get a preview
 * because clients linkify the URL in the text anyway.
 */
export function buildInviteSharePayload(input: InviteMessageInput): InviteSharePayload | null {
  const message = buildInviteMessage(input);
  if (!message) return null;
  return { message, subject: inviteSubject(input.t) };
}
