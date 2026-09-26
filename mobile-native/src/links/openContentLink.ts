/**
 * Following a link tapped in content that is not a message.
 *
 * ## Why this exists at all, when `openMessageLink` already decides correctly
 *
 * `openMessageLink` takes a navigation object because `ChatScreen` has one to
 * give. A post body does not. `PostCard` is a presentational component driven
 * entirely by callbacks (`onOpen`, `onAuthorPress`, `onReport`, …) and is
 * rendered from four places; threading an `onOpenLink` prop through all of them
 * would make "links are tappable" a thing each screen has to remember, and the
 * failure when one forgets is silent — the links simply go back to being prose,
 * on one surface, with nothing red anywhere.
 *
 * Since the requirement is *every* link, the default has to be the working one.
 * So this supplies the navigation rather than asking for it, from the same
 * `navigationRef` that `AppNavigator` already hands to `openNativeRoute` for a
 * drawer entry (`AppNavigator.tsx:414`) and that the call banners use. Callers
 * pass a URL and nothing else.
 *
 * ## Why a ref rather than `useNavigation()`
 *
 * `useNavigation()` throws outside a navigation container, and `PostCard` is
 * rendered bare in `PostCard.test.tsx` — adopting the hook would have turned a
 * rendering test into a crash for reasons that have nothing to do with what it
 * asserts. `navigationRef.isReady()` is simply false there, so a tap in a test
 * is a no-op and the component still renders. That is the same degradation a
 * tap gets during the first frames of a cold start, which is the other moment
 * this can be called before navigation exists.
 *
 * ## It adds no routing knowledge
 *
 * Every decision is still `classifyLink` → `openNativeRoute`, which reads
 * `navigation/linking.ts`. This file contributes one thing: where the
 * navigation object comes from. A path the app stops claiming stops opening
 * natively here on the same day it stops opening natively from a push
 * notification, because it is the same table being asked.
 */

import { openMessageLink, MessageLinkOutcome } from "./openMessageLink";
import { navigationRef } from "../navigation/notificationRouting";

/**
 * Open `rawUrl` from post, comment or caption text.
 *
 * Returns the outcome for tests and for callers that want to know a link went
 * nowhere; no UI depends on it. `"blocked"` is also the answer when navigation
 * is not ready, because from the reader's side those are the same event: the
 * tap did nothing.
 */
export function openContentLink(rawUrl: string): MessageLinkOutcome {
  if (!navigationRef.isReady()) return "blocked";
  return openMessageLink(navigationRef, rawUrl);
}
