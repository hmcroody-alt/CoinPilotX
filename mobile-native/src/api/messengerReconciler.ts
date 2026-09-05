/**
 * The single owner of the question "is this a new message, or one I already have?"
 *
 * One user intent produces ONE logical message, but the client observes it up to
 * five times: the optimistic local bubble, the REST response, a realtime echo, a
 * reconnect replay, and a push event. Every one of those observations arrives
 * through a different code path, and for as long as each path decided for itself
 * whether it was looking at something new, the answer was only as good as the
 * least careful path. PulseSoc has shown the same message twice in production.
 *
 * So identity is decided here and nowhere else. ChatScreen, the transport and the
 * offline queue hand observations to `reconcileMessage` and take the state it
 * returns; they do not filter, splice, or compare ids themselves.
 *
 * IDENTITY (2.2)
 * --------------
 * Before the server has answered, the only identity a message has is the
 * `client_message_id` the sender minted. After it answers, the canonical identity
 * is the server's numeric id. Both must keep working at once, because events
 * carrying only one of the two keep arriving afterwards: a push payload knows the
 * server id but not the client id, and a queued retry knows the client id but not
 * the server id. The reconciler therefore keeps BOTH indexes pointing at the same
 * row -- an alias, not a copy. Two bubbles must never appear merely because two
 * different identifiers were used to describe one message.
 *
 * ORDERING (2.7)
 * --------------
 * Identity and ordering are deliberately separate concerns. This module decides
 * *which* row an event belongs to; `messengerOrdering` decides where that row sits.
 * An acknowledgement changes a message's identity but must not make it visibly
 * jump, which is why the pending/server bucketing lives in the comparator rather
 * than in the raw id.
 */

import type { MessengerMessage } from "./messenger";
import { compareMessengerMessages, messageKey } from "./messengerOrdering";

/**
 * What the reconciler decided. Exposed because the outcome is the contract: a
 * test that asserts "this permutation produced exactly one message" is weaker
 * than one that also asserts the second observation was recognised as an
 * ACKNOWLEDGE rather than an accidental INSERT that happened to collapse later.
 */
export type ReconcileOutcome =
  /** Genuinely new. A row was appended. */
  | "INSERT"
  /** A pending local bubble was accepted by the server and now carries its id. */
  | "ACKNOWLEDGE"
  /** A known message changed: delivery state, reactions, edits, failure. */
  | "UPDATE"
  /**
   * Two rows turned out to be one message. Happens when an observation that
   * carried only a server id was recorded before an observation arrived carrying
   * both identifiers and joined them. The rows are collapsed.
   */
  | "REKEY"
  /** Already known, and the event carried nothing newer. State is untouched. */
  | "IGNORE";

/**
 * A conversation's messages plus the identity indexes.
 *
 * The indexes are carried in the state rather than rebuilt per event on purpose
 * (2.14). Rebuilding would make every incoming realtime frame a full scan of the
 * thread, which is precisely the cost that shows up as jank in a long, busy
 * conversation -- the one place this mission is trying to make feel instant.
 */
export interface MessengerState {
  readonly messages: readonly MessengerMessage[];
  readonly byClientId: ReadonlyMap<string, MessengerMessage>;
  readonly byServerId: ReadonlyMap<number, MessengerMessage>;
}

/**
 * The lifecycle from 2.8. Ranked because delivery state may only move forward:
 * a reconnect replay legitimately reports a message as "sent" long after the
 * reader has seen it, and applying that verbatim would flip a READ message back
 * to SENT on screen. Reporting a message as less delivered than it truly is is a
 * lie the user can see, so the reconciler keeps the strongest state observed.
 */
const DELIVERY_RANK: Record<string, number> = {
  pending: 0,
  sending: 0,
  queued: 0,
  accepted: 1,
  sent: 2,
  delivered: 3,
  seen: 4,
  read: 4
};

function deliveryRank(status?: string): number {
  if (!status) return -1;
  const rank = DELIVERY_RANK[status.toLowerCase()];
  return rank === undefined ? -1 : rank;
}

function isAcked(message: MessengerMessage | undefined): boolean {
  return Boolean(message && message.id > 0);
}

function serverIdOf(message: MessengerMessage): number | undefined {
  return message.id > 0 ? message.id : undefined;
}

/** Build indexed state from a plain list. O(n), paid once per list load. */
export function createMessengerState(messages: readonly MessengerMessage[] = []): MessengerState {
  const byClientId = new Map<string, MessengerMessage>();
  const byServerId = new Map<number, MessengerMessage>();
  const ordered = [...messages].sort(compareMessengerMessages);
  ordered.forEach((message) => {
    if (message.client_message_id) byClientId.set(message.client_message_id, message);
    const serverId = serverIdOf(message);
    if (serverId !== undefined) byServerId.set(serverId, message);
  });
  return { messages: ordered, byClientId, byServerId };
}

/**
 * Combine an existing row with a new observation of the same logical message.
 *
 * The bias throughout is that neither side is automatically authoritative: the
 * server is authoritative about durable facts (its id, delivery state, edits) and
 * the local row is authoritative about things the server has never seen (the
 * media the user picked before upload finished, a failure only this device knows
 * about). Blindly spreading one over the other loses one of those halves.
 */
function fold(existing: MessengerMessage, incoming: MessengerMessage): MessengerMessage {
  const incomingAcked = isAcked(incoming);
  const existingAcked = isAcked(existing);
  // A message never un-acks. An event that carries no server id -- an offline
  // replay of the original payload, say -- must not drag a message that has
  // already been accepted back into the pending bucket, which would move it on
  // screen and re-enable the retry affordance for a message that did send.
  const keepId = incomingAcked ? incoming.id : existingAcked ? existing.id : existing.id;
  const merged: MessengerMessage = { ...existing, ...incoming, id: keepId };
  merged.message_id = keepId;

  // Identity is a union: whichever half of the alias each observation knew about
  // is retained, so the row stays reachable by both keys afterwards.
  merged.client_message_id = incoming.client_message_id || existing.client_message_id;

  const settled = incomingAcked || existingAcked;
  const bestRank = Math.max(deliveryRank(incoming.delivery_status), deliveryRank(existing.delivery_status));
  const bestDelivery =
    deliveryRank(incoming.delivery_status) >= deliveryRank(existing.delivery_status)
      ? incoming.delivery_status
      : existing.delivery_status;
  // Once the server holds the message, the pre-ack markers ("sending", "queued")
  // are local fiction that has been overtaken by fact. Carrying one forward would
  // leave an accepted message displaying a spinner forever.
  merged.delivery_status = settled ? (bestRank >= DELIVERY_RANK.sent ? bestDelivery : "sent") : bestDelivery;

  if (settled) {
    // The server has the message. Any local-only failure or in-flight marker is
    // now stale by definition, and leaving it would show a delivered message
    // wearing a "failed, tap to retry" affordance.
    merged.local_status = undefined;
    merged.local_error = undefined;
  } else if (incoming.local_status) {
    // The local status and its error text are ONE fact, not two. An observation
    // reporting that a fresh attempt is in flight ("sending") is also asserting
    // that the previous attempt's error no longer describes this message. Taking
    // the status without the error is how a retrying bubble ends up still wearing
    // "Message could not be sent" while it is visibly resending.
    merged.local_status = incoming.local_status;
    merged.local_error = incoming.local_error;
  } else {
    merged.local_status = existing.local_status;
    merged.local_error = existing.local_error;
  }

  // Timestamps only ever move forward into existence: a replay that omits
  // delivered_at/seen_at must not erase a receipt that already arrived.
  merged.created_at = existing.created_at || incoming.created_at;
  merged.delivered_at = incoming.delivered_at || existing.delivered_at;
  merged.seen_at = incoming.seen_at || existing.seen_at;
  // Media identity survives a server row that does not echo it back, so an
  // optimistic bubble does not lose its preview the instant it is acked.
  merged.media_url = incoming.media_url || existing.media_url;
  merged.thumbnail_url = incoming.thumbnail_url || existing.thumbnail_url;
  merged.attachment_id = incoming.attachment_id ?? existing.attachment_id;
  // The optimistic bubble learns the foundation media id from the upload
  // response; a Comm-v2 echo that omits it must not erase it, or the bubble
  // loses the only id the media access endpoint accepts.
  merged.media_upload_id = incoming.media_upload_id ?? existing.media_upload_id;
  return merged;
}

/**
 * Field-wise equality, treating "absent" and "explicitly undefined" as the same
 * thing. Folding necessarily materialises keys that were previously missing, so a
 * key-count comparison would call every no-op event a change and re-render the
 * thread on every duplicate frame.
 */
function sameMessage(a: MessengerMessage, b: MessengerMessage): boolean {
  if (a === b) return true;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]) as Set<keyof MessengerMessage>;
  for (const key of keys) {
    if (a[key] !== b[key]) return false;
  }
  return true;
}

export interface ReconcileResult {
  state: MessengerState;
  outcome: ReconcileOutcome;
}

/**
 * Reconcile ONE observation into the conversation.
 *
 * Returns the same state object when nothing changed, so a caller holding this
 * in React state can skip the re-render entirely -- a reconnect that replays two
 * hundred already-known messages should cost nothing visually.
 */
export function reconcileMessage(state: MessengerState, incoming: MessengerMessage): ReconcileResult {
  const clientId = incoming.client_message_id || "";
  const serverId = serverIdOf(incoming);

  const byClient = clientId ? state.byClientId.get(clientId) : undefined;
  const byServer = serverId !== undefined ? state.byServerId.get(serverId) : undefined;

  if (!byClient && !byServer) {
    const messages = [...state.messages, incoming].sort(compareMessengerMessages);
    const byClientId = new Map(state.byClientId);
    const byServerId = new Map(state.byServerId);
    if (clientId) byClientId.set(clientId, incoming);
    if (serverId !== undefined) byServerId.set(serverId, incoming);
    return { state: { messages, byClientId, byServerId }, outcome: "INSERT" };
  }

  // Both indexes hit, but on different rows: one logical message was recorded
  // twice under two names. This is the duplicate the mission exists to prevent,
  // and reaching it means an earlier event arrived with only half the identity.
  // Collapse rather than pick, so nothing either row knows is thrown away.
  const collapsing = Boolean(byClient && byServer && byClient !== byServer);
  const anchor = byClient || byServer!;
  const other = collapsing ? (anchor === byClient ? byServer! : byClient!) : undefined;
  const base = other ? fold(other, anchor) : anchor;
  const next = fold(base, incoming);

  if (!collapsing && sameMessage(anchor, next)) {
    return { state, outcome: "IGNORE" };
  }

  const messages: MessengerMessage[] = [];
  state.messages.forEach((message) => {
    if (message === anchor) {
      messages.push(next);
      return;
    }
    if (other && message === other) return; // absorbed into `next`
    messages.push(message);
  });
  messages.sort(compareMessengerMessages);

  const byClientId = new Map(state.byClientId);
  const byServerId = new Map(state.byServerId);
  [anchor, other].forEach((stale) => {
    if (!stale) return;
    if (stale.client_message_id) byClientId.delete(stale.client_message_id);
    const staleServerId = serverIdOf(stale);
    if (staleServerId !== undefined) byServerId.delete(staleServerId);
  });
  if (next.client_message_id) byClientId.set(next.client_message_id, next);
  const nextServerId = serverIdOf(next);
  if (nextServerId !== undefined) byServerId.set(nextServerId, next);

  let outcome: ReconcileOutcome = "UPDATE";
  if (collapsing) outcome = "REKEY";
  else if (!isAcked(anchor) && isAcked(next)) outcome = "ACKNOWLEDGE";

  return { state: { messages, byClientId, byServerId }, outcome };
}

/** Reconcile a batch — a page load, a reconnect replay, a queue drain. */
export function reconcileMessages(
  state: MessengerState,
  incoming: readonly MessengerMessage[]
): { state: MessengerState; outcomes: ReconcileOutcome[] } {
  let next = state;
  const outcomes: ReconcileOutcome[] = [];
  incoming.forEach((message) => {
    const result = reconcileMessage(next, message);
    next = result.state;
    outcomes.push(result.outcome);
  });
  return { state: next, outcomes };
}

/**
 * Array-in/array-out reconciliation for callers that hold a plain list.
 *
 * The index is cached against the array instance, so the common path -- feed the
 * previous result back in with one new event -- reuses the maps instead of
 * rebuilding them. A caller that hands in a foreign array pays one O(n) build,
 * which is the correct cost for a list this reconciler has never seen.
 */
const stateCache = new WeakMap<object, MessengerState>();

export function stateForList(messages: readonly MessengerMessage[]): MessengerState {
  const cached = stateCache.get(messages as unknown as object);
  if (cached) return cached;
  const built = createMessengerState(messages);
  stateCache.set(messages as unknown as object, built);
  stateCache.set(built.messages as unknown as object, built);
  return built;
}

export function reconcileList(
  current: readonly MessengerMessage[],
  incoming: readonly MessengerMessage[]
): MessengerMessage[] {
  const { state } = reconcileMessages(stateForList(current), incoming);
  stateCache.set(state.messages as unknown as object, state);
  return state.messages as MessengerMessage[];
}
