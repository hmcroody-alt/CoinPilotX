/**
 * Pure, unit-testable ordering + dedupe for a conversation's message list.
 *
 * Extracted from ChatScreen so the "instant optimistic bubble" invariants can be
 * proven without a renderer:
 *
 *  1. A message is identified by `client_message_id` (preferred) or its id, so a
 *     local/pending message and the server row that later acks it collapse into a
 *     single bubble instead of appearing twice.
 *  2. Server-accepted rows carry a positive, monotonically-increasing id and sort
 *     chronologically by that id.
 *  3. A local/pending row uses a NEGATIVE id (`-Date.now()`), which is how the rest
 *     of the app recognizes "not yet acked" (reactions disabled, etc). Sorting on the
 *     raw id would push that negative value to the TOP of the ascending list, so the
 *     freshly-sent bubble would render at the top of the thread and then visibly jump
 *     to the bottom on ack. Instead we bucket pending rows AFTER every server row and
 *     order them among themselves by creation time — so the bubble appears at the
 *     bottom immediately and never jumps.
 */

import type { MessengerMessage } from "./messenger";

function messageKey(message: MessengerMessage): string {
  return message.client_message_id || String(message.id);
}

function isPending(message: MessengerMessage): boolean {
  return !(message.id > 0);
}

function creationOrder(message: MessengerMessage): number {
  const parsed = Date.parse(message.created_at || "");
  if (!Number.isNaN(parsed)) return parsed;
  // `-Date.now()` local ids encode creation time; recover it as a fallback.
  return Math.abs(message.id);
}

/** Stable, chronological ordering that keeps pending rows pinned to the bottom. */
export function compareMessengerMessages(a: MessengerMessage, b: MessengerMessage): number {
  const aPending = isPending(a);
  const bPending = isPending(b);
  if (aPending !== bPending) return aPending ? 1 : -1;
  if (aPending && bPending) return creationOrder(a) - creationOrder(b);
  return a.id - b.id;
}

/**
 * Merge `incoming` into `current`, deduping by client message id and preserving the
 * pending/acked local status semantics ChatScreen relies on.
 */
export function mergeConversationMessages(
  current: MessengerMessage[],
  incoming: MessengerMessage[]
): MessengerMessage[] {
  const byKey = new Map<string, MessengerMessage>();
  [...current, ...incoming].forEach((message) => {
    const key = messageKey(message);
    const existing = byKey.get(key);
    const serverAccepted = message.id > 0 && Boolean(message.client_message_id);
    byKey.set(key, {
      ...existing,
      ...message,
      local_status: serverAccepted ? undefined : message.local_status || existing?.local_status,
      local_error: serverAccepted ? undefined : message.local_error || existing?.local_error,
      delivery_status: serverAccepted
        ? message.delivery_status || "sent"
        : message.delivery_status || existing?.delivery_status
    });
  });
  return Array.from(byKey.values()).sort(compareMessengerMessages);
}
