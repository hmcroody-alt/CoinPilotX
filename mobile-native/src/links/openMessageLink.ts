/**
 * Following a link that was tapped inside a message.
 *
 * Split out from `LinkedText.tsx` so the decision ("in-app, browser, or
 * nowhere") can be tested without rendering anything, and from
 * `messageLinks.ts` so that module stays free of React Native imports.
 *
 * There are exactly three outcomes and no fourth:
 *
 * - `internal` → `openNativeRoute`, the same function `AppNavigator` calls for a
 *   drawer entry and `notificationRouting` calls for a push deep link. No new
 *   navigation path is introduced by making links tappable.
 * - `external` → the OS, via `Linking.openURL`.
 * - `blocked` → nothing happens, and nothing is reported. A tap that goes
 *   nowhere is a worse experience than one that works and a better one than a
 *   `javascript:` payload running because the alternative looked untidy.
 *
 * `Linking.openURL` is given a URL that has already been parsed and had its
 * protocol checked against an allowlist. It is still wrapped: on iOS the promise
 * rejects when no installed app claims the scheme, and an unhandled rejection
 * from a message bubble would be a crash for a mistyped link.
 */

import { Linking } from "react-native";
import { openNativeRoute, NativeRouteNavigation } from "../navigation/nativeRouteActions";
import { classifyLink, LinkDestination } from "./messageLinks";

export type MessageLinkOutcome = LinkDestination["kind"];

export type OpenMessageLinkDeps = {
  /** Injected in tests. Defaults to the platform's own opener. */
  openExternal?: (url: string) => Promise<unknown>;
};

/**
 * Open `rawUrl` and report which of the three paths was taken.
 *
 * The return value exists for tests and for callers that want to react to a
 * blocked link; nothing in the UI depends on it today.
 */
export function openMessageLink(
  navigation: NativeRouteNavigation,
  rawUrl: string,
  deps: OpenMessageLinkDeps = {}
): MessageLinkOutcome {
  const destination = classifyLink(rawUrl);
  if (destination.kind === "blocked") return "blocked";
  if (destination.kind === "internal") {
    try {
      openNativeRoute(navigation, destination.path);
    } catch {
      // A navigation that throws must not take the conversation down with it.
    }
    return "internal";
  }
  const openExternal = deps.openExternal ?? ((url: string) => Linking.openURL(url));
  try {
    void Promise.resolve(openExternal(destination.url)).catch(() => undefined);
  } catch {
    // Some hosts throw synchronously rather than rejecting.
  }
  return "external";
}
