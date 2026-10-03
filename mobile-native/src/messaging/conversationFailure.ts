/**
 * What a conversation is allowed to claim when it fails to open.
 *
 * The defect this exists to remove: ChatScreen stored one `error` string and
 * drove three independent claims off its truthiness. A conversation that came
 * back 404 rendered "Conversation not found." in the panel, "Messages
 * unavailable" in the header, and "PULSE LINK · RECONNECTING" above an enabled
 * composer — three different failure models on one screen, two of them false.
 * There is no realtime transport in that screen at all, so "RECONNECTING" was
 * describing a subsystem that does not exist, and the composer was offering to
 * send into a conversation the server had just said was not there.
 *
 * So the rejection is classified once, here, and everything the screen says is
 * read off the result. A kind that cannot send cannot have a live composer; a
 * kind that re-fetching cannot fix does not get a Retry button that re-fetches.
 *
 * Kept a pure function of a thrown value — duck-typed on `status` rather than
 * importing `PulseApiError` — so the classification can be tested with plain
 * objects, without expo-secure-store, AsyncStorage, or a mounted conversation.
 */

/**
 * The communications blueprint answers a rejection as
 * `{ok:false, status:"<code>", message, http_status}`, and `_json` lifts
 * `http_status` onto the response. But `pulseApi` reads a code from
 * `error_code` or `error` only, so `PulseApiError.code` is *empty* for every
 * v2 messaging rejection and the code survives only inside `details.status`.
 * Both are consulted, and the HTTP status is the primary signal because it is
 * the one thing always present.
 */
type ThrownLike = {
  message?: unknown;
  status?: unknown;
  code?: unknown;
  details?: { status?: unknown } | null;
};

/** The taxonomy. Named for what is wrong, not for what the screen will show. */
export type ConversationFailureKind =
  /** The session is gone. Nothing will load until the person signs in again. */
  | "auth_required"
  /** The id does not resolve to a conversation this person is in. */
  | "conversation_not_found"
  /** The conversation exists; this viewer is not a participant. */
  | "forbidden"
  /** A block or privacy setting stands between the two people. */
  | "messaging_not_allowed"
  /** Nothing reached PulseSoc, or nothing came back in time. */
  | "network_error"
  /** PulseSoc answered, and the answer was its own failure. */
  | "server_error"
  /** Classified as little as possible, on purpose. See `classify`. */
  | "load_failed";

/**
 * The story the screen tells. Several kinds share one posture because the
 * person reading it has the same thing to do about them — a 403 on someone
 * else's thread and a 404 on a stale id are both "this conversation will not
 * open", and splitting the copy would only invite the two to drift apart.
 */
export type ConversationFailurePosture = "signed_out" | "unavailable" | "messaging_off" | "offline" | "retrying";

/** What recovery means for this failure. The control is labelled from it. */
export type ConversationRecovery =
  /** Ask for the same conversation again. */
  | "refetch"
  /** The id itself is suspect: resolve the conversation again, then replace. */
  | "resolve"
  /** Nothing on this screen can help. */
  | "back";

export type ConversationFailure = {
  kind: ConversationFailureKind;
  posture: ConversationFailurePosture;
  /** The server's own words, kept for the inline banner and for logging. */
  message: string;
  /** HTTP status, or 0 when the request never got an answer. */
  status: number;
  /** Sending cannot succeed, so no composer control may be live. */
  blocksSending: boolean;
  /** Re-requesting the same conversation id could plausibly work. */
  refetchable: boolean;
  /** The conversation id is the suspect part, not the request. */
  needsResolution: boolean;
};

const POSTURE_OF: Record<ConversationFailureKind, ConversationFailurePosture> = {
  auth_required: "signed_out",
  conversation_not_found: "unavailable",
  forbidden: "unavailable",
  messaging_not_allowed: "messaging_off",
  network_error: "offline",
  server_error: "retrying",
  load_failed: "retrying"
};

/** Kinds where a send would be refused too, so the composer must go quiet. */
const BLOCKS_SENDING: Record<ConversationFailureKind, boolean> = {
  auth_required: true,
  conversation_not_found: true,
  forbidden: true,
  messaging_not_allowed: true,
  // The thread is fine as far as anyone knows; a failed send is what the
  // optimistic bubble's own Retry is for.
  network_error: false,
  server_error: false,
  load_failed: false
};

function statusOf(error: ThrownLike): number {
  return typeof error.status === "number" && Number.isFinite(error.status) ? error.status : 0;
}

function codeOf(error: ThrownLike): string {
  if (typeof error.code === "string" && error.code) return error.code;
  const detail = error.details && typeof error.details === "object" ? error.details.status : undefined;
  return typeof detail === "string" ? detail : "";
}

/** React Native's fetch rejects with a plain `TypeError` carrying no status. */
function looksLikeTransportFailure(message: string): boolean {
  return /network request failed|network error|failed to fetch|timed out|timeout/i.test(message);
}

function kindOf(error: ThrownLike, message: string): ConversationFailureKind {
  const status = statusOf(error);
  const code = codeOf(error);
  if (status === 401) return "auth_required";
  if (status === 404) return "conversation_not_found";
  if (status === 403) return code === "blocked" ? "messaging_not_allowed" : "forbidden";
  if (status === 504 || code === "request_timeout") return "network_error";
  if (status >= 500) return "server_error";
  // A thrown value with no status never reached a server, or never came back
  // from one. Only the shapes that say so are called a network failure: a
  // client-side bug that happens to throw here must not be reported to the
  // person as "check your connection".
  if (!status) return looksLikeTransportFailure(message) ? "network_error" : "load_failed";
  return "load_failed";
}

/**
 * @param error the thrown value
 * @param fallbackMessage shown when the rejection carried no message of its own
 */
export function classifyConversationFailure(error: unknown, fallbackMessage: string): ConversationFailure {
  const thrown: ThrownLike = error && typeof error === "object" ? (error as ThrownLike) : {};
  const message = typeof thrown.message === "string" && thrown.message.trim() ? thrown.message : fallbackMessage;
  const kind = kindOf(thrown, message);
  return {
    kind,
    posture: POSTURE_OF[kind],
    message,
    status: statusOf(thrown),
    blocksSending: BLOCKS_SENDING[kind],
    refetchable: kind === "network_error" || kind === "server_error" || kind === "load_failed",
    needsResolution: kind === "conversation_not_found"
  };
}

/**
 * Whether the composer may accept input.
 *
 * Two reasons it may not. The conversation refuses sends — then a live
 * composer is an offer the server will reject. Or the load left nothing on
 * screen at all, in which case the fatal panel owns the view, and a composer
 * underneath it invites someone to type into a conversation that never opened.
 * A failure *with* history on screen is a different thing: that is a paging or
 * polling hiccup over a thread that works, and taking the composer away would
 * be the lie in the other direction.
 */
export function conversationComposerAvailable(failure: ConversationFailure | null, hasMessages: boolean): boolean {
  if (!failure) return true;
  if (failure.blocksSending) return false;
  return hasMessages;
}

/**
 * @param canResolve whether the screen knows who the other party is, and can
 *   therefore ask the server for the conversation again rather than re-asking
 *   for an id the server has already rejected.
 */
export function conversationRecovery(failure: ConversationFailure, canResolve: boolean): ConversationRecovery {
  if (failure.needsResolution) return canResolve ? "resolve" : "back";
  return failure.refetchable ? "refetch" : "back";
}

/** The copy for each posture. A record so the mapping is testable too. */
export const CONVERSATION_FAILURE_COPY: Record<
  ConversationFailurePosture,
  { header: string; state: string; title: string; body: string }
> = {
  signed_out: {
    header: "messaging:chat.headerSignedOut",
    state: "messaging:chat.stateSignedOut",
    title: "messaging:chat.signedOutTitle",
    body: "messaging:chat.signedOutBody"
  },
  unavailable: {
    header: "messaging:chat.headerConversationUnavailable",
    state: "messaging:chat.stateUnavailable",
    title: "messaging:chat.unavailableTitle",
    body: "messaging:chat.unavailableBody"
  },
  messaging_off: {
    header: "messaging:chat.headerUnavailable",
    state: "messaging:chat.stateMessagingOff",
    title: "messaging:chat.messagingOffTitle",
    body: "messaging:chat.messagingOffBody"
  },
  offline: {
    header: "messaging:chat.headerOffline",
    state: "messaging:chat.stateOffline",
    title: "messaging:chat.offlineTitle",
    body: "messaging:chat.offlineBody"
  },
  // The only posture that may still say "reconnecting": PulseSoc answered, so
  // there is something to reconnect to, and asking again is the way back.
  retrying: {
    header: "messaging:chat.headerReconnecting",
    state: "messaging:chat.stateReconnecting",
    title: "messaging:chat.loadFailedTitle",
    body: "messaging:chat.loadFailedBody"
  }
};
