/**
 * What the UNDX thread is allowed to claim about its own connection.
 *
 * The header sits under the assistant's name and says one of four things. Three
 * of them are cheap to be wrong about and one is not: "Always available ·
 * PulseSoc Intelligence" is a claim that the live service answered, and a
 * screen that shows it while nothing has reached the service is lying to the
 * person reading it. So the claim is derived here, from the load state, rather
 * than assembled inline from whichever booleans happen to be in scope.
 *
 * The defect this replaces was a nested ternary that asked `usingCachedMessages`
 * first. That flag is false in two very different situations — before the first
 * request resolves, and after one succeeds — so the opening fetch rendered as
 * "Always available". The cold start is precisely when the service is least
 * likely to be reachable, which made the one untruthful state also the most
 * frequently seen one.
 *
 * Keeping this a pure function of four booleans is what makes it testable
 * without mounting a conversation. Mounting one to assert a subtitle would pull
 * in the composer, the media gallery, the wallpaper and the whole message list,
 * and would still only exercise whichever combination the mocks happened to
 * produce.
 */

/**
 * - `reconnecting` — a load failed and there was nothing cached to fall back
 *   on, so there is neither a live service nor history to show.
 * - `connecting` — the first load is still in flight. Nothing is known yet.
 * - `cached` — a load failed but cached history is on screen. The messages are
 *   real; the connection is not.
 * - `live` — a load came back from the network. This is the only state that
 *   may claim availability.
 */
export type AssistantConnectionState = "reconnecting" | "connecting" | "cached" | "live";

export type AssistantConnectionInput = {
  /** A load failed and left no history to show. */
  error: boolean;
  /** A request is in flight. */
  loading: boolean;
  /** The opening load has resolved, one way or the other. */
  initialFetchComplete: boolean;
  /** History on screen came from the on-device cache, not the network. */
  usingCachedMessages: boolean;
};

export function assistantConnectionState(input: AssistantConnectionInput): AssistantConnectionState {
  // An outright failure outranks everything: there is nothing on screen to
  // qualify, so no softer description would be honest.
  if (input.error) return "reconnecting";
  // Order matters here. This has to be asked before `usingCachedMessages`,
  // because that flag cannot distinguish "not cached" from "not yet asked".
  if (!input.initialFetchComplete || input.loading) return "connecting";
  if (input.usingCachedMessages) return "cached";
  return "live";
}

/** The i18n key for each state. Exported so the mapping is testable too. */
export const ASSISTANT_CONNECTION_KEYS: Record<AssistantConnectionState, string> = {
  reconnecting: "messaging:chat.assistantReconnecting",
  connecting: "messaging:chat.assistantConnecting",
  cached: "messaging:chat.headerCachedHistory",
  live: "messaging:chat.assistantAlwaysAvailable"
};

/**
 * Whether the status dot beside the words should warn.
 *
 * It has to agree with the text it sits next to. Warning on `error` alone left
 * a live-green dot beside "Cached history" — two opposite claims about one
 * connection, printed a few pixels apart.
 */
export function assistantConnectionDegraded(state: AssistantConnectionState): boolean {
  return state !== "live";
}
