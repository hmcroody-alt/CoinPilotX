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
import { reconcileList } from "./messengerReconciler";

/**
 * Monotonic within this JS runtime. `Date.now()` alone is not a safe identity:
 * two sends in the same millisecond -- a double tap, a queue drain, a share to
 * several people at once -- produce the same value. That used to be merely
 * untidy. Now that the server enforces uniqueness on
 * (conversation, sender, client_message_id), a collision would make the server
 * treat a genuinely new message as a repeat of the previous one and silently
 * discard it. Losing a message is far worse than showing one twice, so identity
 * carries a sequence and entropy as well as a clock.
 */
let clientMessageSequence = 0;

export function mintClientMessageId(prefix = "native"): string {
  clientMessageSequence += 1;
  const clock = Date.now().toString(36);
  const sequence = clientMessageSequence.toString(36);
  const entropy = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${clock}-${sequence}-${entropy}`.slice(0, 120);
}

/**
 * The identity every layer must agree on. A local bubble, the REST response,
 * a realtime echo, a reconnect replay and a push event are five observations of
 * ONE logical message; they reconcile here or they duplicate on screen.
 */
export function messageKey(message: MessengerMessage): string {
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
 * Merge `incoming` into `current`, deduping by message identity and preserving the
 * pending/acked local status semantics ChatScreen relies on.
 *
 * This is now a thin adapter over `messengerReconciler`, which is the single owner
 * of the "new message or one I already have?" decision. It used to key purely on
 * `client_message_id || id`, and that had a hole with teeth: an event describing a
 * message by only ONE of its two identities -- a push payload that knows the server
 * id but not the client id, a replayed payload that knows the client id but not the
 * server id -- landed under a different key from the row it belonged to and rendered
 * as a second bubble. The reconciler keeps both identities pointing at one row.
 */
export function mergeConversationMessages(
  current: MessengerMessage[],
  incoming: MessengerMessage[]
): MessengerMessage[] {
  return reconcileList(current, incoming);
}
