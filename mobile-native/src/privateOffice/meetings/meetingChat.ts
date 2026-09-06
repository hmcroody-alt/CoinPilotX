/**
 * Private Meetings — chat/reaction projection helpers.
 *
 * Pure functions only. The room screen owns the poll timer and the component
 * state; these helpers own the merge/dedupe/derive logic so it stays
 * deterministic and testable. The server is the only authority on messages —
 * nothing here invents, reorders beyond id-sort, or rewrites a message.
 */

import { MeetingMessage } from "./types";

/** Hard cap so an all-day meeting cannot grow the in-memory list unbounded. */
export const MESSAGE_CAP = 200;

/** Reaction chips linger this long in the overlay before pruning. */
export const REACTION_TTL_MS = 5000;

/** Poll cadence for the in-meeting message feed. */
export const CHAT_POLL_MS = 3000;

/** The fixed reaction palette. Sent as the message body, kind "reaction". */
export const REACTION_PALETTE: readonly string[] = ["👍", "❤️", "😂", "👏", "🎉", "😮"];

/**
 * Merge a poll page into the existing list: dedupe by id, ascending id order,
 * capped to the newest MESSAGE_CAP. Returns the SAME array instance when the
 * merge changes nothing, so React state updates can be skipped cheaply.
 */
export function mergeMeetingMessages(
  existing: MeetingMessage[],
  incoming: MeetingMessage[],
  cap: number = MESSAGE_CAP
): MeetingMessage[] {
  if (!incoming.length) return existing;
  const byId = new Map<number, MeetingMessage>();
  for (const message of existing) byId.set(message.id, message);
  let changed = false;
  for (const message of incoming) {
    if (!message || typeof message.id !== "number" || message.id <= 0) continue;
    if (!byId.has(message.id)) changed = true;
    byId.set(message.id, message);
  }
  if (!changed) return existing;
  const merged = Array.from(byId.values()).sort((a, b) => a.id - b.id);
  return merged.length > cap ? merged.slice(merged.length - cap) : merged;
}

/** Highest message id seen — the `since_id` for the next poll. */
export function maxMessageId(messages: MeetingMessage[]): number {
  let max = 0;
  for (const message of messages) if (message.id > max) max = message.id;
  return max;
}

/** Reaction-kind messages newer than `afterId`, in id order (overlay feed). */
export function newReactionEvents(
  messages: MeetingMessage[],
  afterId: number
): MeetingMessage[] {
  return messages.filter((m) => m.kind === "reaction" && m.id > afterId);
}

/**
 * Unread badge count: text messages from OTHER people newer than the last
 * read id. Reactions and system lines never count as unread — they are
 * ambient, not correspondence.
 */
export function unreadTextCount(
  messages: MeetingMessage[],
  lastReadId: number,
  selfUserId: number
): number {
  let count = 0;
  for (const message of messages) {
    if (message.kind !== "text") continue;
    if (message.id <= lastReadId) continue;
    if (message.sender_user_id === selfUserId) continue;
    count += 1;
  }
  return count;
}
