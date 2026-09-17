/**
 * What a delivered iOS notification has to say about itself before this app is
 * willing to take it away from the user.
 *
 * THE RULE THIS FILE EXISTS TO ENFORCE
 *
 * Dismissing a notification destroys the only copy of something the user has
 * not necessarily seen. So classification is default-DENY in the direction that
 * matters: a payload this parser does not fully understand is `other`, and
 * `other` is preserved. There is no branch anywhere below that guesses.
 *
 * WHAT IS NEVER READ
 *
 * Not the title. Not the body. Not the sender's name. Not the subtitle, the
 * attachment, or the thread id. Those are the fields a user can influence by
 * choosing what to type, and a classifier that reads them can be steered by
 * anyone who can send a message — "New message from…" is content, not a type.
 * Only server-set structural fields are consulted, and the tests assert that by
 * feeding in decoys.
 *
 * THE TWO IDENTIFIERS, WHICH ARE NOT THE SAME THING
 *
 * `notificationKey` is PulseSoc's LOGICAL name for an alert ("message:12:345").
 * It is useful for correlation and nothing else. The only handle that can
 * actually dismiss a delivered notification is `request.identifier`, which iOS
 * assigns after the server is out of the picture and which is readable only
 * from `getPresentedNotificationsAsync()`. Conflating the two produces code
 * that looks correct and dismisses nothing.
 *
 * SCHEMA VERSIONS
 *
 * v2 (current) carries `schemaVersion`, `notificationType`, `recipientUserId`
 * and `sentAt`. v1 is everything that shipped before: it says `type:
 * "message"` or `push_type: "chat_message"` and carries ids, but it cannot say
 * who it was for. A v1 payload is still recognisable as a message — it just
 * cannot prove ownership, and `ownership` records that difference so the
 * reconciler can hold v1 to a stricter dismissal rule.
 */

/**
 * Bumped in lockstep with `MESSAGE_PUSH_SCHEMA_VERSION` in
 * `pulse_communications_v2/service.py`. A cross-language test reads both and
 * fails if they drift, because the failure mode otherwise is silent: the
 * server starts emitting a shape this parser quietly declines, message alerts
 * stop being dismissed, and nothing errors.
 */
export const MESSAGE_PUSH_SCHEMA_VERSION = 2;

/**
 * Mirrors `MESSAGE_READ_STATE_BATCH_LIMIT` in
 * `pulse_communications_v2/service.py`. The server clamps silently rather than
 * rejecting, so a client that sent more would get a short answer and read the
 * missing ids as absent. Chunking on this number client-side keeps every
 * requested id accounted for; the same cross-language test pins both.
 */
export const MESSAGE_READ_STATE_BATCH_LIMIT = 200;

/** Values of `type`/`push_type` that a pre-v2 server used for a chat message. */
const LEGACY_MESSAGE_TYPES: readonly string[] = ["message", "chat_message", "private_message", "voice_message"];

/**
 * Whether the payload identified its recipient.
 *
 * - `claimed`  the payload named a recipient, so "is this mine?" is answerable.
 * - `unknown`  a v1 payload with no recipient field. NOT the same as "mine".
 */
export type MessageNotificationOwnership = "claimed" | "unknown";

export type MessageNotificationRef = {
  schemaVersion: number;
  messageId: number;
  conversationId: number;
  /** Null only when `ownership` is `unknown`. */
  recipientUserId: number | null;
  ownership: MessageNotificationOwnership;
  senderId: number | null;
  /**
   * Server-stamped send time, carried for diagnostics only. It is deliberately
   * NOT an input to any read decision: device clocks and server clocks
   * disagree, and a timestamp comparison would dismiss on skew. Read state is
   * decided by the server against message ids.
   */
  sentAt: string;
  /** PulseSoc's logical key. Never a dismissal handle — see the header. */
  notificationKey: string;
};

/**
 * A delivered notification, classified.
 *
 * `other` carries a machine reason rather than a free-form string so the
 * preserve path can be counted and asserted on. Every reason below means
 * PRESERVE; none of them is a soft "probably fine to remove".
 */
export type DeliveredClassification =
  | { kind: "message"; ref: MessageNotificationRef }
  | { kind: "other"; reason: OtherReason };

export type OtherReason =
  /** The payload explicitly declared a non-message type. The common case. */
  | "declared_other_type"
  /** Nothing in the payload identifies a type at all. */
  | "no_type_declared"
  /** Said "message" but had no usable message id, so there is nothing to ask about. */
  | "message_without_id"
  /** Said "message" but had no usable conversation id. */
  | "message_without_conversation"
  /** The data blob was missing, not an object, or otherwise unreadable. */
  | "unreadable_payload";

/**
 * Notification families that must survive reconciliation untouched.
 *
 * This list is documentation and a test fixture, NOT a filter. The filter is
 * the positive test for `notificationType === "message"`; anything that fails
 * that test is preserved whether or not it appears here. Writing it as a
 * denylist would mean a family invented next month gets deleted by default,
 * which is the exact inversion this file refuses.
 *
 * Call and Live entries are additionally covered by the mission's protected-
 * system lock: nothing in this module may participate in their lifecycle.
 */
export const PRESERVED_NOTIFICATION_FAMILIES: readonly string[] = [
  "call",
  "missed_call",
  "voip",
  "live",
  "livestream",
  "security",
  "login_alert",
  "marketplace",
  "order",
  "commerce",
  "payment",
  "payout",
  "crypto",
  "market_alert",
  "price_alert",
  "system",
  "moderation",
  "report",
  "follow",
  "mention",
  "comment",
  "reaction",
  "reel",
  "briefing"
];

function plainObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/**
 * Flatten the one level of nesting APNs/Expo introduce.
 *
 * Expo delivers the server's dictionary under `data`, and some transports nest
 * it once more. The outer keys win on conflict so a transport-added field
 * cannot be shadowed by a same-named field one level down.
 */
export function flattenNotificationData(data: unknown): Record<string, unknown> | null {
  const outer = plainObject(data);
  if (!outer) return null;
  const inner = plainObject(outer.data) || {};
  return { ...inner, ...outer };
}

function readString(payload: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

/**
 * Read a positive integer id, tolerating the string form.
 *
 * Returns 0 for anything that is not one — including `"12abc"`, `-1`, `0`,
 * `1.5` and `NaN`. `parseInt` is deliberately not used: it would read `"12abc"`
 * as 12 and happily dismiss notification 12 on the strength of a malformed
 * payload.
 */
function readId(payload: Record<string, unknown>, ...keys: string[]): number {
  for (const key of keys) {
    const raw = payload[key];
    if (typeof raw !== "number" && typeof raw !== "string") continue;
    const text = String(raw).trim();
    if (!/^\d+$/.test(text)) continue;
    const value = Number(text);
    if (Number.isSafeInteger(value) && value > 0) return value;
  }
  return 0;
}

/**
 * Is this payload a PulseSoc message notification, and if so, which message?
 *
 * Returns `null` for everything else, including payloads that claim to be
 * messages but cannot name one. The caller must treat `null` as "preserve".
 */
export function parseMessageNotification(data: unknown): MessageNotificationRef | null {
  const result = classifyNotificationData(data);
  return result.kind === "message" ? result.ref : null;
}

/**
 * The full classification, with a reason when the answer is "not a message".
 *
 * Split out from `parseMessageNotification` so the reconciler can log WHY it
 * preserved something without the log line having to invent a category.
 */
export function classifyNotificationData(data: unknown): DeliveredClassification {
  const payload = flattenNotificationData(data);
  if (!payload) return { kind: "other", reason: "unreadable_payload" };

  const declaredType = readString(payload, "notificationType", "notification_type").toLowerCase();
  const legacyType = readString(payload, "type", "push_type", "event_type").toLowerCase();

  if (declaredType) {
    // v2 speaks for itself, in both directions. A payload that declares
    // "security" is not a message even if it also carries a conversation id,
    // which some cross-posted alerts do.
    if (declaredType !== "message") return { kind: "other", reason: "declared_other_type" };
  } else if (!legacyType) {
    return { kind: "other", reason: "no_type_declared" };
  } else if (!LEGACY_MESSAGE_TYPES.includes(legacyType)) {
    return { kind: "other", reason: "declared_other_type" };
  }

  const messageId = readId(payload, "messageId", "message_id");
  if (!messageId) return { kind: "other", reason: "message_without_id" };

  const conversationId = readId(payload, "conversationId", "conversation_id");
  if (!conversationId) return { kind: "other", reason: "message_without_conversation" };

  const recipientUserId = readId(payload, "recipientUserId", "recipient_user_id");
  const senderId = readId(payload, "senderId", "sender_id");
  const rawVersion = readId(payload, "schemaVersion", "schema_version");

  return {
    kind: "message",
    ref: {
      // A payload that declares the v2 type but no version is treated as
      // unversioned (0) rather than assumed current. The version is not what
      // the reconciler decides on — `ownership` is — so an honest 0 costs
      // nothing and a flattering 2 would hide a server-side regression.
      schemaVersion: rawVersion,
      messageId,
      conversationId,
      recipientUserId: recipientUserId || null,
      ownership: recipientUserId ? "claimed" : "unknown",
      senderId: senderId || null,
      sentAt: readString(payload, "sentAt", "sent_at", "timestamp"),
      notificationKey: readString(payload, "notificationKey", "notification_key")
    }
  };
}

/**
 * Does this message notification belong to the signed-in account?
 *
 * Three answers, not two. `"unknown"` is the v1 case and is NOT collapsed into
 * either of the others: calling it `"mine"` would let account A's stale alerts
 * be dismissed while account B is signed in, and calling it `"theirs"` would
 * mean legacy alerts can never be cleared. The reconciler resolves it by
 * asking the server, which answers only for the authenticated user.
 */
export function ownershipFor(ref: MessageNotificationRef, signedInUserId: number): "mine" | "theirs" | "unknown" {
  if (ref.ownership === "unknown") return "unknown";
  if (!Number.isSafeInteger(signedInUserId) || signedInUserId <= 0) return "unknown";
  return ref.recipientUserId === signedInUserId ? "mine" : "theirs";
}
