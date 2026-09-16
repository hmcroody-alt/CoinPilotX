/**
 * Where "open UNDX" goes, and how to get back.
 *
 * UNDX has exactly one conversation, and two ways in: the PulseAI tab, and a
 * contextual "Ask UNDX" on an asset screen. Both land on the same `Chat` route
 * with the same conversation id, so the route params are built here rather than
 * spelled out twice — a second copy is how one entry point quietly acquires a
 * different title or loses the task id.
 *
 * ## The return target
 *
 * A contextual entry also records where it came from. This exists because the
 * stack cannot always answer that question:
 *
 * The tab entry deliberately *replaces* the tab screen with `Chat` (the tab
 * screen is a redirect; pushing on top of it would bounce straight back into
 * `Chat` the moment the member pressed Back). That replacement is correct for
 * the tab and fatal for a drill-in — before this module existed, "Ask UNDX"
 * navigated to the tab, which popped the asset screen off the stack, and the
 * redirect then replaced the tab too. The stack ended up holding a single
 * entry, `Back` did nothing at all, and the only way out of UNDX was to kill
 * the app.
 *
 * So a contextual entry now pushes `Chat` directly, keeping the asset screen
 * underneath it where `goBack()` can find it, and carries `undxReturn` as the
 * belt to that pair of braces: if the stack entry is ever lost — a deep link, a
 * state restore, a future caller that resets — the screen still knows the one
 * destination it is allowed to send the member back to.
 *
 * `undxReturn` is a narrow union rather than a generic route name and params
 * bag. It travels through route state, which is not a place to accept an
 * arbitrary "navigate here" instruction; a contextual handoff describes the
 * subject of a conversation and must never widen into a way of reaching
 * screens.
 */

import { PULSE_AI_CONVERSATION_ID, PULSE_AI_DISPLAY_NAME } from "../api/messenger";
import { RootStackParamList } from "../navigation/types";

/** The only screens a UNDX drill-in is allowed to return to. */
export type UndxReturnTarget = {
  screen: "AssetDetail";
  params: { symbol: string; name?: string; title?: string };
};

export type UndxChatTarget = RootStackParamList["Chat"];

/**
 * Route params for the canonical UNDX conversation.
 *
 * `presence` is "available" for both entries: UNDX is not a person whose
 * presence varies, and the header reads better than an empty subtitle.
 */
export function undxChatTarget(options?: {
  taskId?: string;
  returnTo?: UndxReturnTarget;
}): UndxChatTarget {
  return {
    conversationId: PULSE_AI_CONVERSATION_ID,
    title: PULSE_AI_DISPLAY_NAME,
    presence: "available",
    ...(options?.taskId ? { undxTaskId: options.taskId } : {}),
    ...(options?.returnTo ? { undxReturn: options.returnTo } : {})
  };
}

/**
 * The slice of the navigation object the back rule needs. Method shorthand on
 * purpose: methods are checked bivariantly, which keeps React Navigation's
 * precisely-typed screen prop assignable without this module importing any
 * screen's prop types.
 */
export type UndxBackNavigation = {
  canGoBack(): boolean;
  goBack(): void;
  navigate(screen: string, params?: object): void;
};

/** Where Back lands when the stack has nothing to offer. */
export type ChatBackFloor = { screen: "Tabs"; params: { screen: "Dashboard" | "Messenger" } };

/**
 * The floor for the UNDX conversation.
 *
 * UNDX earns the dashboard because of how it is entered: the PulseAI tab
 * *replaces* itself with Chat, so an untouched stack legitimately holds nothing
 * beneath it, and the member who opened UNDX from the tab was on the dashboard
 * side of the app. There is no conversation list standing behind UNDX to return
 * to — it is a tab, not an item in the recents the way a person is.
 */
export const UNDX_BACK_FLOOR: ChatBackFloor = { screen: "Tabs", params: { screen: "Dashboard" } };

/**
 * The floor for a conversation with a person, a group, or a room.
 *
 * This is the half that was wrong, and it was wrong because one rule written
 * for UNDX was running for every conversation in the app. Reproduced on device:
 * cold-launch `pulsesoc://pulse/messages/6` — the same `navigate` a tapped
 * message notification performs — press the `‹`, and the app lands on Mission
 * Control. A screen the member never asked for, from a thread they opened
 * deliberately, while the button's own accessibility label was promising "Back
 * to conversations".
 *
 * The messages list is not a guess here in the way the dashboard is. A
 * conversation is an item *in* that list, so going up from it is structurally
 * true no matter which conversation it was; that keeps the "Back always does
 * something" guarantee without inventing a destination to satisfy it.
 */
export const CONVERSATION_BACK_FLOOR: ChatBackFloor = { screen: "Tabs", params: { screen: "Messenger" } };

/**
 * Which floor a conversation gets.
 *
 * Keyed on the conversation id rather than on a boolean the caller hands in,
 * because the id is the thing that is actually true about the screen and a flag
 * is something a second call site can get wrong. `PULSE_AI_CONVERSATION_ID` is
 * a sentinel, and negative, so it can never collide with a real row id — which
 * is what makes it safe to branch on at all.
 */
export function chatBackFloor(conversationId: number): ChatBackFloor {
  return conversationId === PULSE_AI_CONVERSATION_ID ? UNDX_BACK_FLOOR : CONVERSATION_BACK_FLOOR;
}

/**
 * Back, from a chat, guaranteed to land somewhere.
 *
 * Three tiers, strictly ordered. The real stack first, because it knows about
 * screens the member visited in between and no recorded fallback does. The
 * recorded `undxReturn` second, for the cases where the stack cannot answer —
 * a deep link, a restored session, a future caller that resets. A floor last,
 * so Back can never be a no-op and a chat can never become a screen you have to
 * kill the app to leave.
 *
 * Only the floor varies, and it varies by conversation rather than by caller:
 * see `chatBackFloor`. The first two tiers are deliberately identical for every
 * conversation — a live stack entry beneath you is the best answer that exists,
 * and it does not become a worse answer because of who you are talking to.
 *
 * This lives here rather than inline in ChatScreen so the rendered navigation
 * regression test exercises the exact rule the screen runs.
 */
export function goBackFromChat(
  navigation: UndxBackNavigation,
  options: { conversationId: number; undxReturn?: UndxReturnTarget }
): void {
  if (navigation.canGoBack()) {
    navigation.goBack();
    return;
  }
  if (options.undxReturn) {
    navigation.navigate(options.undxReturn.screen, options.undxReturn.params);
    return;
  }
  const floor = chatBackFloor(options.conversationId);
  navigation.navigate(floor.screen, floor.params);
}

/** The asset screen a member drilled in from, or null if they did not. */
export function assetReturnTarget(input: {
  symbol: string;
  name?: string | null;
}): UndxReturnTarget | null {
  const symbol = String(input.symbol || "").trim().toUpperCase();
  if (!symbol) return null;
  const name = String(input.name || "").trim();
  return {
    screen: "AssetDetail",
    params: { symbol, ...(name ? { name, title: name } : {}) }
  };
}
