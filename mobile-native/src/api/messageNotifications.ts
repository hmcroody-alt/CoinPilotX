import { pulseApi } from "./pulseApi";
import { MESSAGE_READ_STATE_BATCH_LIMIT } from "../notifications/messageNotificationContract";

const MESSENGER_API = "/api/pulse/communications/v2";

/**
 * The server's verdict on a batch of message ids, for the AUTHENTICATED user.
 *
 * Four buckets, not two, because "not read" and "we have no idea" lead to
 * different actions and collapsing them is how a notification for an unread
 * message gets dismissed. See `message_notification_read_state` in
 * `pulse_communications_v2/service.py` for the classification rules.
 *
 * - `read`      the signed-in user has read it (or sent it).
 * - `unread`    it exists, they can see it, they have not read it.
 * - `obsolete`  the message or conversation is gone, hidden, or they are not a
 *               participant. NOTE the conflation: "deleted" and "not yours"
 *               both land here, which is why the reconciler will not act on
 *               `obsolete` unless the payload independently proved ownership.
 * - `unknown`   no row, or the lookup itself failed. Always means preserve.
 */
export type MessageReadStateResponse = {
  read: number[];
  unread: number[];
  obsolete: number[];
  unknown: number[];
  /** True when the server answered from a failed lookup rather than real data. */
  degraded?: boolean;
};

const EMPTY_STATE: MessageReadStateResponse = { read: [], unread: [], obsolete: [], unknown: [] };

function numberList(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const out: number[] = [];
  for (const entry of value) {
    const parsed = Number(entry);
    if (Number.isSafeInteger(parsed) && parsed > 0) out.push(parsed);
  }
  return out;
}

/**
 * Ask which of these message ids the signed-in user has already read.
 *
 * Failure is expressed as "everything is unknown", never as an empty answer or
 * a throw. An empty answer would read, downstream, as "none of these are
 * unread" — and the set-difference that follows would then dismiss nothing,
 * which is safe, but a caller that reasoned the other way round would dismiss
 * everything. Returning explicit `unknown` makes the safe reading the only
 * available one.
 */
export async function fetchMessageReadState(messageIds: number[]): Promise<MessageReadStateResponse> {
  const unique = Array.from(new Set(messageIds.filter((id) => Number.isSafeInteger(id) && id > 0)));
  if (!unique.length) return EMPTY_STATE;

  // Chunked rather than truncated. The server clamps an oversized request
  // without saying so, so the ids past the cap would come back in no bucket at
  // all — and an id in no bucket is an id the caller has to invent a meaning
  // for. Chunking keeps the invariant that every id asked about is answered.
  const merged: MessageReadStateResponse = { read: [], unread: [], obsolete: [], unknown: [], degraded: false };
  for (let index = 0; index < unique.length; index += MESSAGE_READ_STATE_BATCH_LIMIT) {
    const batch = unique.slice(index, index + MESSAGE_READ_STATE_BATCH_LIMIT);
    const answer = await fetchOneBatch(batch);
    merged.read.push(...answer.read);
    merged.unread.push(...answer.unread);
    merged.obsolete.push(...answer.obsolete);
    merged.unknown.push(...answer.unknown);
    if (answer.degraded) merged.degraded = true;
  }
  return merged;
}

async function fetchOneBatch(batch: number[]): Promise<MessageReadStateResponse> {
  try {
    const response = await pulseApi<Partial<MessageReadStateResponse>>(`${MESSENGER_API}/notifications/read-state`, {
      method: "POST",
      body: JSON.stringify({ message_ids: batch })
    });
    const parsed = {
      read: numberList(response?.read),
      unread: numberList(response?.unread),
      obsolete: numberList(response?.obsolete),
      unknown: numberList(response?.unknown),
      degraded: Boolean(response?.degraded)
    };
    // An id we asked about that came back in no bucket is not "not read" — it
    // is unanswered, and the only honest bucket for it is `unknown`. Without
    // this, a server that silently dropped ids would look like a server saying
    // "none of those are unread".
    const answered = new Set([...parsed.read, ...parsed.unread, ...parsed.obsolete, ...parsed.unknown]);
    for (const id of batch) {
      if (!answered.has(id)) parsed.unknown.push(id);
    }
    return parsed;
  } catch {
    return { read: [], unread: [], obsolete: [], unknown: batch, degraded: true };
  }
}
