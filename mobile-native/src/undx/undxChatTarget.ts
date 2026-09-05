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

/**
 * Back, from the UNDX chat, guaranteed to land somewhere.
 *
 * Three tiers, strictly ordered. The real stack first, because it knows about
 * screens the member visited in between and the recorded origin does not. The
 * recorded `undxReturn` second, for the cases where the stack cannot answer —
 * a deep link, a restored session, a future caller that resets. The dashboard
 * last: the tab entry replaces itself with Chat, so an untouched stack can
 * legitimately hold nothing beneath it, and landing on the dashboard is not a
 * guess about where the member wanted to be — it is the guarantee that Back
 * always does something, so UNDX can never become a screen you have to kill
 * the app to leave.
 *
 * This lives here rather than inline in ChatScreen so the rendered navigation
 * regression test exercises the exact rule the screen runs.
 */
export function goBackFromUndxChat(
  navigation: UndxBackNavigation,
  undxReturn?: UndxReturnTarget
): void {
  if (navigation.canGoBack()) {
    navigation.goBack();
    return;
  }
  if (undxReturn) {
    navigation.navigate(undxReturn.screen, undxReturn.params);
    return;
  }
  navigation.navigate("Tabs", { screen: "Dashboard" });
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
