/**
 * Finding links in a body of text, and deciding where each one goes.
 *
 * Messenger rendered message bodies as one flat `<Text>`, so a URL someone sent
 * was a string of characters and nothing more: visibly a link, structurally
 * prose. This module is the half of the fix that has no opinion about pixels —
 * it says *where the links are* and *what kind each one is*. `LinkedText.tsx`
 * draws them and `openMessageLink.ts` follows them.
 *
 * It is written as a segmenter rather than a regex-replace for the same reason
 * `social/mentions.ts` is: a segment list composes with the other things that
 * decorate a body, where a string rewrite fights them. The two files deliberately
 * share a shape (`{ text, ...meta }[]`) so a future body renderer can run both.
 *
 * ## The routing decision is not made here — it is *asked* here
 *
 * The one thing this module must not do is grow its own table of which PulseSoc
 * paths the app can open. That table exists, it is `navigation/linking.ts`, and
 * it is the same table that answers a Universal Link arriving from Safari and a
 * push notification's deep link. So `classifyLink` *calls* it. A path the app
 * claims is `internal`; a path it does not claim is `external`, and that
 * distinction stays correct on its own as screens are added and retired.
 *
 * That matters most for a link the app must NOT swallow. `https://pulsesoc.com/r/<code>`
 * is an invite: the server user-agent-detects iOS behind it and sends a visitor
 * without the app to the App Store, writing the deferred-attribution row that
 * `POST /api/mobile/referral/claim` later redeems (`sharing/inviteMessage.ts`).
 * `/r/` is not in the linking config and is not in the app's `applinks:`
 * association, so it classifies as external and opens in the browser — which is
 * the only place it works. Had this module routed "any pulsesoc.com URL" into
 * `openNativeRoute`, the total fallback at the end of that chain
 * (`dashboardRouting.ts:237`) would have opened the invite code in a dashboard
 * module shell and silently broken referral attribution.
 *
 * ## Scheme handling is an allowlist, in both directions
 *
 * `javascript:`, `data:` and `file:` are not blocked by a denylist — they are
 * never *detected*, because the pattern only recognises `http://`, `https://`
 * and a leading `www.`. `classifyLink` then re-checks the parsed protocol, so a
 * value that reaches it by some other path is still rejected. Two independent
 * checks, because a denylist has to be right about every scheme that will ever
 * exist and an allowlist only has to be right about two.
 *
 * ## The displayed text is a slice of the destination
 *
 * There is no href/label split to disagree with itself: a segment's `text` is
 * `body.slice(start, end)` and its `url` is that same slice, normalised. The
 * classic "displayed text differs from where it goes" attack is not defended
 * against so much as made unrepresentable.
 */

import { linking } from "../navigation/linking";

/**
 * Deliberately requires a scheme, or `www.`.
 *
 * Bare-domain detection ("pulsesoc.com/pulse/post/1") was considered and left
 * out: the same rule that catches it also catches "node.js", "etc.", version
 * numbers and sentence-ending abbreviations, and a false positive here is a
 * piece of someone's sentence rendered as a tappable link. A missed link is a
 * smaller failure than a fabricated one.
 *
 * The character class excludes whitespace and the quote/angle characters that
 * wrap a URL in prose. Everything else — including `(`, `)` and trailing
 * punctuation — is captured greedily and trimmed afterwards by `trimTrailing`,
 * which is the only way to get `(see https://x.com/a_(b))` right.
 */
const URL_PATTERN = /\b(?:https?:\/\/|www\.)[^\s<>"'`]+/gi;

/** Punctuation that ends a sentence, never a URL. */
const TRAILING_PROSE = ".,!?:;\"'“”‘’…«»*_~";

/** The live site. Exact, so `pulsesoc.com.example.net` is not the live site. */
const PULSESOC_HOST = /^(www\.)?pulsesoc\.com$/i;

/** The only two schemes this app will render as a link or hand to the OS. */
const ALLOWED_PROTOCOLS = new Set(["http:", "https:"]);

export type LinkToken = {
  /** The URL as it appears in the body, after trailing punctuation is trimmed. */
  text: string;
  /** Index of the first character in the source string. */
  start: number;
  /** Index one past the last character. */
  end: number;
};

export type TextSegment = {
  text: string;
  /**
   * Present only on link segments. Normalised (a `www.` link gains `https://`),
   * so it is what should be opened; `text` is what should be shown.
   */
  url?: string;
};

export type LinkDestination =
  /** A PulseSoc path the app claims. `path` is what `openNativeRoute` wants. */
  | { kind: "internal"; url: string; path: string }
  /** Safe to hand to the OS: http/https, and nothing this app can render. */
  | { kind: "external"; url: string }
  /** Unparseable, or a scheme that is not http/https. Never opened. */
  | { kind: "blocked"; url: string };

function occurrences(value: string, character: string): number {
  let count = 0;
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] === character) count += 1;
  }
  return count;
}

/**
 * Strip the punctuation that belongs to the sentence rather than to the URL.
 *
 * Closing brackets are the interesting case and are handled by counting: a
 * trailing `)` is dropped only when it is unmatched inside the candidate, so
 * `https://en.wikipedia.org/wiki/Foo_(bar)` keeps its parenthesis while
 * `(see https://pulsesoc.com/)` loses the one that closes the aside. The loop
 * repeats because real text produces runs — `...post/2432).` is both cases.
 */
function trimTrailing(candidate: string): string {
  let url = candidate;
  for (;;) {
    const last = url[url.length - 1];
    if (!last) break;
    if (last === ")" || last === "]" || last === "}") {
      const opener = last === ")" ? "(" : last === "]" ? "[" : "{";
      if (occurrences(url, last) > occurrences(url, opener)) {
        url = url.slice(0, -1);
        continue;
      }
      break;
    }
    if (TRAILING_PROSE.includes(last)) {
      url = url.slice(0, -1);
      continue;
    }
    break;
  }
  return url;
}

/** `www.x` is a URL without a scheme; everything else is returned untouched. */
export function normalizeLinkUrl(raw: string): string {
  const value = String(raw || "").trim();
  if (/^www\./i.test(value)) return `https://${value}`;
  return value;
}

/**
 * Whether a trimmed candidate is a URL at all.
 *
 * `https://` on its own survives the pattern and the trim, and a host with no
 * dot in it ("http://localhost" in prose, "https://x") is more likely to be
 * someone typing than a link worth offering.
 */
function isUsableCandidate(candidate: string): boolean {
  const normalized = normalizeLinkUrl(candidate);
  try {
    const url = new URL(normalized);
    if (!ALLOWED_PROTOCOLS.has(url.protocol.toLowerCase())) return false;
    return url.hostname.includes(".") && url.hostname.length > 3;
  } catch {
    return false;
  }
}

/** Every link in a body, in order, with its position in the source string. */
export function detectLinks(body: string): LinkToken[] {
  const source = String(body || "");
  if (!source) return [];
  const tokens: LinkToken[] = [];
  URL_PATTERN.lastIndex = 0;
  let match = URL_PATTERN.exec(source);
  while (match) {
    const raw = match[0];
    const trimmed = trimTrailing(raw);
    if (trimmed && isUsableCandidate(trimmed)) {
      tokens.push({ text: trimmed, start: match.index, end: match.index + trimmed.length });
    }
    match = URL_PATTERN.exec(source);
  }
  return tokens;
}

/**
 * Split a body into plain and link segments for rendering.
 *
 * Mirrors `segmentMentions`. Every character of the input appears in exactly one
 * segment and in the original order, so the concatenation of all `text` values
 * is the input — which is the property that makes "surrounding text is
 * preserved" testable rather than eyeballed.
 */
export function segmentLinks(body: string): TextSegment[] {
  const source = String(body || "");
  const tokens = detectLinks(source);
  if (!tokens.length) return source ? [{ text: source }] : [];
  const segments: TextSegment[] = [];
  let cursor = 0;
  tokens.forEach((token) => {
    if (token.start > cursor) segments.push({ text: source.slice(cursor, token.start) });
    segments.push({ text: token.text, url: normalizeLinkUrl(token.text) });
    cursor = token.end;
  });
  if (cursor < source.length) segments.push({ text: source.slice(cursor) });
  return segments;
}

/**
 * Whether the app claims a path, asked of the one table that decides it.
 *
 * `linking.getStateFromPath` is the same function React Navigation runs for a
 * Universal Link, so "would tapping this in Messenger and tapping it in Safari
 * land in the same place" is true by construction rather than by review. An
 * unclaimed path returns `undefined` and the link goes to the browser.
 */
function appClaimsPath(relativePath: string): boolean {
  try {
    const state = linking.getStateFromPath?.(relativePath, linking.config as never);
    return Boolean(state);
  } catch {
    return false;
  }
}

/**
 * Where a detected link should go.
 *
 * The site root is mapped to `/pulse` rather than asked about: nothing claims
 * `/`, but "open pulsesoc.com" from inside PulseSoc plainly means the feed, and
 * bouncing the user out to a browser to see the same content signed in again is
 * the worst of the three available answers.
 */
export function classifyLink(rawUrl: string): LinkDestination {
  const normalized = normalizeLinkUrl(rawUrl);
  let url: URL;
  try {
    url = new URL(normalized);
  } catch {
    return { kind: "blocked", url: normalized };
  }
  if (!ALLOWED_PROTOCOLS.has(url.protocol.toLowerCase())) {
    return { kind: "blocked", url: normalized };
  }
  if (!PULSESOC_HOST.test(url.hostname)) {
    return { kind: "external", url: url.toString() };
  }
  const path = url.pathname.replace(/\/+$/, "") || "/";
  if (path === "/") return { kind: "internal", url: url.toString(), path: "/pulse" };
  const relative = `${path}${url.search}${url.hash}`;
  if (appClaimsPath(relative)) return { kind: "internal", url: url.toString(), path: relative };
  return { kind: "external", url: url.toString() };
}
