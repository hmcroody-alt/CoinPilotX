import { PulseAuthor } from "../api/feed";

/**
 * Mention parsing and insertion for comment and reply composers.
 *
 * The only mention-adjacent tool that existed before this module was
 * PostCard.tsx:562-565, which appended the literal character "☺" to the draft —
 * a placeholder, not a feature. Comments are required to support mentions across
 * every content type, so the token grammar lives in exactly one place here.
 *
 * Grammar: "@" followed by 1-30 characters of [A-Za-z0-9_.], not preceded by a
 * word character (so an email address does not produce a mention), and not
 * ending in "." (so "@name." mentions "name" and leaves the period as prose).
 */

const MENTION_PATTERN = /(^|[^\w@])@([A-Za-z0-9_.]{1,30})/g;
const TRAILING_DOTS = /\.+$/;

export type MentionToken = {
  /** Username without the leading "@". */
  username: string;
  /** Index of the "@" in the source string. */
  start: number;
  /** Index one past the last character of the mention. */
  end: number;
};

/** Every mention token in a body, in order, de-duplicated by position. */
export function parseMentions(body: string): MentionToken[] {
  if (!body) return [];
  const tokens: MentionToken[] = [];
  MENTION_PATTERN.lastIndex = 0;
  let match = MENTION_PATTERN.exec(body);
  while (match) {
    const prefix = match[1] || "";
    const raw = match[2] || "";
    const username = raw.replace(TRAILING_DOTS, "");
    if (username) {
      const start = match.index + prefix.length;
      tokens.push({ username, start, end: start + 1 + username.length });
    }
    match = MENTION_PATTERN.exec(body);
  }
  return tokens;
}

/** Distinct usernames mentioned in a body, lowercased, order preserved. */
export function mentionedUsernames(body: string): string[] {
  const seen = new Set<string>();
  const usernames: string[] = [];
  parseMentions(body).forEach((token) => {
    const key = token.username.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    usernames.push(token.username);
  });
  return usernames;
}

/**
 * Split a body into plain and mention segments for rendering. Callers style the
 * mention segments; this function decides where they are.
 */
export type MentionSegment = { text: string; username?: string };

export function segmentMentions(body: string): MentionSegment[] {
  const tokens = parseMentions(body);
  if (!tokens.length) return body ? [{ text: body }] : [];
  const segments: MentionSegment[] = [];
  let cursor = 0;
  tokens.forEach((token) => {
    if (token.start > cursor) segments.push({ text: body.slice(cursor, token.start) });
    segments.push({ text: body.slice(token.start, token.end), username: token.username });
    cursor = token.end;
  });
  if (cursor < body.length) segments.push({ text: body.slice(cursor) });
  return segments;
}

/** The username of the author, however the payload chose to spell it. */
export function authorHandle(author?: PulseAuthor | null): string {
  if (!author) return "";
  const raw = author.username || author.public_player_id || author.display_name || "";
  return String(raw).replace(/^@/, "").trim();
}

/**
 * The draft a reply composer should open with when replying to `author`.
 * Returns the existing draft untouched if it already mentions them, so
 * re-opening the composer cannot stack duplicate mentions.
 */
export function seedReplyDraft(draft: string, author?: PulseAuthor | null): string {
  const handle = authorHandle(author);
  if (!handle) return draft;
  const already = mentionedUsernames(draft).some((name) => name.toLowerCase() === handle.toLowerCase());
  if (already) return draft;
  const prefix = `@${handle} `;
  return draft ? `${prefix}${draft.replace(/^\s+/, "")}` : prefix;
}

/**
 * Insert a mention at `selectionStart`, replacing the partial "@qu" the user was
 * typing when they picked from the suggestion list. Returns the new body and the
 * caret position, because leaving the caret where it was is the classic way a
 * mention picker corrupts the next keystroke.
 */
export function insertMention(body: string, selectionStart: number, username: string): { body: string; selection: number } {
  const handle = String(username || "").replace(/^@/, "").trim();
  if (!handle) return { body, selection: selectionStart };
  const caret = Math.max(0, Math.min(selectionStart, body.length));
  const before = body.slice(0, caret);
  const partial = /(^|[^\w@])@([A-Za-z0-9_.]*)$/.exec(before);
  const replaceFrom = partial ? caret - (partial[2] || "").length - 1 : caret;
  const head = body.slice(0, replaceFrom);
  const tail = body.slice(caret);
  const needsSpace = !tail.startsWith(" ");
  const insertion = `@${handle}${needsSpace ? " " : ""}`;
  return { body: `${head}${insertion}${tail}`, selection: head.length + insertion.length };
}

/**
 * The partial handle the user is currently typing, or "" when the caret is not
 * inside a mention. Drives whether the suggestion list is shown.
 */
export function activeMentionQuery(body: string, selectionStart: number): string {
  const caret = Math.max(0, Math.min(selectionStart, body.length));
  const match = /(^|[^\w@])@([A-Za-z0-9_.]*)$/.exec(body.slice(0, caret));
  return match ? match[2] || "" : "";
}
