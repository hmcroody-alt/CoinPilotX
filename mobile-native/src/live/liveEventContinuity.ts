/**
 * Stages 27, 28 and 30 — the things around the stage that must not notice when
 * the stage changes.
 *
 * A Live is not only its video. It is a comment stream people are mid-argument
 * in, a reaction layer that tells a host the room is still awake, and an invite
 * that may arrive twice because it travelled by push and by poll. All three are
 * shared by everyone in the session, and none of them has any business
 * reacting to a guest coming on stage — but each is a list held in client state
 * that a naive refresh replaces wholesale, and "replaced wholesale" is
 * indistinguishable from "cleared" to whoever was reading it.
 *
 * The failure this file prevents is specific and has a shape people recognise:
 * a host brings a guest up, and every viewer's comment column blinks, loses the
 * last thirty seconds of conversation, or shows the same message twice. Nobody
 * files that as "guest promotion broke the chat". They file it as "the app is
 * buggy during Lives".
 *
 * Pure: no React, no network, no clocks that are not passed in.
 */

import type { PulseLiveChatMessage } from "../api/live";

// ---------------------------------------------------------------------------
// Stage 27 — comments survive a guest joining
// ---------------------------------------------------------------------------

/**
 * How many messages a client keeps. Comfortably more than the server's window,
 * because the point of merging is to hold onto what the window has scrolled
 * past, and small enough that a six-hour Live does not accumulate a list that
 * costs more to diff than to render.
 */
export const LIVE_CHAT_RETENTION = 400;

/** A message the local user has sent but the server has not yet echoed back. */
export type PendingLiveChatMessage = PulseLiveChatMessage & {
  /** Client-generated. Distinguishes an optimistic row from a server row. */
  pendingKey: string;
};

function messageKey(message: PulseLiveChatMessage): string {
  const id = Number(message?.id || 0);
  if (id > 0) return `id:${id}`;
  const pending = (message as PendingLiveChatMessage).pendingKey;
  if (pending) return `pending:${pending}`;
  // Last resort for a server row with no usable id: the tuple that actually
  // identifies a comment. Collides only for one person posting identical text
  // in the same second, where showing one row is the better wrong answer.
  return `t:${message?.user_id || 0}:${message?.created_at || ""}:${message?.body || ""}`;
}

function orderValue(message: PulseLiveChatMessage): number {
  const id = Number(message?.id || 0);
  if (id > 0) return id;
  // Optimistic messages have no id yet and belong at the end, where the person
  // who just typed them expects to see them.
  return Number.MAX_SAFE_INTEGER;
}

/**
 * Fold a freshly polled window into the comments already on screen.
 *
 * Three properties, in order of how badly their absence is felt:
 *
 *   1. An empty or short response never shortens the stream. The server returns
 *      the most recent N comments, so a quiet minute legitimately returns fewer
 *      rows than the client is holding; treating that as the new truth deletes
 *      conversation that is still on screen.
 *   2. A message appears once. The same comment arriving from a poll and from a
 *      realtime event is one comment.
 *   3. An optimistic message is replaced by its server row rather than joined
 *      by it. This is why `pendingKey` exists: without it the local echo and
 *      the confirmed message are two different rows saying the same thing.
 */
export function mergeLiveChat(
  existing: readonly PulseLiveChatMessage[],
  incoming: readonly PulseLiveChatMessage[],
  options: { retention?: number } = {}
): PulseLiveChatMessage[] {
  const retention = Math.max(1, Math.floor(options.retention ?? LIVE_CHAT_RETENTION));
  const byKey = new Map<string, PulseLiveChatMessage>();

  for (const message of existing || []) {
    if (!message) continue;
    byKey.set(messageKey(message), message);
  }

  for (const message of incoming || []) {
    if (!message) continue;
    const key = messageKey(message);
    // A server row supersedes the pending row it confirms. Matching is by
    // body-and-author rather than by id, because the client cannot know the id
    // the server will assign.
    const confirmed = resolvePending(byKey, message);
    if (confirmed) byKey.delete(confirmed);
    byKey.set(key, { ...byKey.get(key), ...message });
  }

  const merged = Array.from(byKey.values()).sort((a, b) => orderValue(a) - orderValue(b));
  return merged.length > retention ? merged.slice(merged.length - retention) : merged;
}

/** The key of a pending message this server row confirms, if any. */
function resolvePending(
  byKey: Map<string, PulseLiveChatMessage>,
  incoming: PulseLiveChatMessage
): string | null {
  if (!(Number(incoming?.id || 0) > 0)) return null;
  for (const [key, candidate] of byKey) {
    if (!key.startsWith("pending:")) continue;
    if (Number(candidate?.user_id || 0) !== Number(incoming?.user_id || 0)) continue;
    if (String(candidate?.body || "") !== String(incoming?.body || "")) continue;
    return key;
  }
  return null;
}

/**
 * Whether a change in the stage should cause the comment stream to be refetched
 * from scratch.
 *
 * It should not, ever — which is why this is a named function returning a
 * constant rather than an absent behaviour. A future author wiring a guest
 * count into a chat effect's dependency array has to come here and change a
 * documented answer instead of adding a variable.
 */
export function stageChangeInvalidatesChat(): false {
  return false;
}

// ---------------------------------------------------------------------------
// Stage 28 — reactions belong to the room, not to the stage
// ---------------------------------------------------------------------------

export type LiveReactionEvent = {
  /** Server id when the reaction has been persisted. */
  id?: number;
  /** Client id for a reaction this device just sent. */
  localKey?: string;
  reactionType: string;
  userId?: number;
  createdAt?: string;
};

/** How many recent reactions a client keeps for de-duplication purposes. */
export const LIVE_REACTION_WINDOW = 120;

/**
 * Who may send a reaction.
 *
 * Everyone. A reaction is how an audience of a hundred thousand people who will
 * never be given a microphone participates, and restricting it to publishers
 * would turn the one channel the audience has into a stage privilege. Stated as
 * a function so the rule is testable and so the answer is written down in the
 * place a future permission check would be added.
 */
export function canSendLiveReaction(_input: { role: string; onStage: boolean }): true {
  return true;
}

/** Whether reactions from a participant should be rendered to everyone. */
export function reactionIsAudienceWide(): true {
  return true;
}

function reactionKey(event: LiveReactionEvent): string {
  if (Number(event?.id || 0) > 0) return `id:${event.id}`;
  if (event?.localKey) return `local:${event.localKey}`;
  return `t:${event?.userId || 0}:${event?.reactionType || ""}:${event?.createdAt || ""}`;
}

/**
 * Fold new reactions into the window, returning both the full window and only
 * the events that had not been seen — the second is what should be animated.
 *
 * Re-bursting the whole window on every poll is the bug this exists to prevent:
 * it looks like a wave of enthusiasm that is really the same six hearts being
 * redrawn every two seconds, and it is worst on the busiest Lives.
 */
export function mergeLiveReactions(
  existing: readonly LiveReactionEvent[],
  incoming: readonly LiveReactionEvent[],
  options: { window?: number } = {}
): { window: LiveReactionEvent[]; fresh: LiveReactionEvent[] } {
  const limit = Math.max(1, Math.floor(options.window ?? LIVE_REACTION_WINDOW));
  const seen = new Set<string>();
  const window: LiveReactionEvent[] = [];
  for (const event of existing || []) {
    if (!event) continue;
    const key = reactionKey(event);
    if (seen.has(key)) continue;
    seen.add(key);
    window.push(event);
  }

  const fresh: LiveReactionEvent[] = [];
  for (const event of incoming || []) {
    if (!event) continue;
    const key = reactionKey(event);
    if (seen.has(key)) continue;
    seen.add(key);
    fresh.push(event);
    window.push(event);
  }

  return {
    window: window.length > limit ? window.slice(window.length - limit) : window,
    fresh
  };
}

// ---------------------------------------------------------------------------
// Stage 30 — an invite is one invite however many times it arrives
// ---------------------------------------------------------------------------

export type LiveInviteEvent = {
  /** Server-issued and stable across redelivery. See live_participants.build_invite_id. */
  inviteId: string;
  liveId: number;
  requestId?: number;
  hostUserId?: number;
  expiresAt?: string;
  /** Where this copy arrived from. Useful for telemetry, never for identity. */
  source?: "push" | "realtime" | "poll" | "deeplink";
};

export type InviteInbox = {
  /** Invite ids that have been surfaced to the user. */
  readonly handled: readonly string[];
};

export const EMPTY_INVITE_INBOX: InviteInbox = { handled: [] };

/** How many invite ids to remember. A Live does not issue thousands. */
const INVITE_MEMORY = 64;

/**
 * Decide whether an arriving invite should be shown.
 *
 * The same invite genuinely does arrive more than once: a push notification, a
 * realtime event and the next state poll all carry it, and on a cold start a
 * deep link carries it a fourth time. Without a stable id the client would have
 * to guess from timestamps, which is why `invite_id` is server-issued and why
 * re-inviting the same person returns the *same* id rather than minting a new
 * one. This function is the client half of that contract.
 *
 * An invite with no id is refused rather than shown. A duplicate prompt is
 * annoying; a prompt the client can never mark as handled is a modal that
 * reappears every poll until the user force-quits.
 */
export function acceptInviteEvent(
  inbox: InviteInbox,
  event: LiveInviteEvent | null | undefined,
  options: { nowMs?: number } = {}
): { show: boolean; inbox: InviteInbox; reason: string } {
  const inviteId = String(event?.inviteId || "").trim();
  if (!inviteId) return { show: false, inbox, reason: "missing_invite_id" };
  if (!(Number(event?.liveId || 0) > 0)) return { show: false, inbox, reason: "missing_live_id" };
  if (inbox.handled.includes(inviteId)) return { show: false, inbox, reason: "already_handled" };

  const expiresAt = String(event?.expiresAt || "").trim();
  if (expiresAt) {
    const expiry = Date.parse(expiresAt);
    const now = options.nowMs ?? Date.now();
    // An expired invite is still recorded as handled. Otherwise a stale push
    // sitting in the tray is re-evaluated, and re-refused, on every delivery.
    if (Number.isFinite(expiry) && expiry <= now) {
      return { show: false, inbox: rememberInvite(inbox, inviteId), reason: "expired" };
    }
  }

  return { show: true, inbox: rememberInvite(inbox, inviteId), reason: "new_invite" };
}

function rememberInvite(inbox: InviteInbox, inviteId: string): InviteInbox {
  const handled = [...inbox.handled, inviteId];
  return { handled: handled.length > INVITE_MEMORY ? handled.slice(handled.length - INVITE_MEMORY) : handled };
}

/**
 * Collapse a batch of invite events to the ones worth showing, newest wins.
 *
 * Used when a client comes back from background holding a queue rather than a
 * single event.
 */
export function dedupeInviteEvents(
  inbox: InviteInbox,
  events: readonly LiveInviteEvent[],
  options: { nowMs?: number } = {}
): { show: LiveInviteEvent[]; inbox: InviteInbox } {
  let cursor = inbox;
  const show: LiveInviteEvent[] = [];
  for (const event of events || []) {
    const result = acceptInviteEvent(cursor, event, options);
    cursor = result.inbox;
    if (result.show) show.push(event);
  }
  return { show, inbox: cursor };
}
