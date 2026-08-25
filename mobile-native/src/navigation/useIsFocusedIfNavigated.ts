/**
 * `useIsFocused`, but tolerant of there being no navigator overhead.
 *
 * The library's own hook throws "Couldn't find a navigation object" outside a
 * `NavigationContainer`. That is survivable for a screen that is only ever
 * mounted by the navigator; it is not survivable for anything rendered in
 * isolation — a screen test, a preview, a component pulled into a modal — which
 * crashes on a concern that has nothing to do with what is being rendered.
 *
 * The subscription logic is the library's, minus the throw: a screen with no
 * navigator is never blurred, so it reads as focused.
 *
 * WHY ITS OWN FILE
 *
 * It began life private to `BottomNavVisibility`, which is the right home for a
 * dock concern. It is not only a dock concern any more: the launch gate uses it
 * to stop a locked card's animation loop while the screen is covered. Importing
 * it from `BottomNavVisibility` made the gate — and therefore any screen with a
 * locked card — depend on a chrome module that a dozen screen tests module-mock,
 * so those tests started failing on a hook they had no reason to know about.
 * Moving it here gives both callers one implementation and gives neither of them
 * the other's mocking surface.
 */

import { NavigationContext } from "@react-navigation/native";
import { createContext, useContext, useEffect, useState } from "react";

/**
 * Stand-in for `NavigationContext` when the real one is unavailable.
 *
 * Only ever read, never provided. It exists so the `useContext` call below is
 * unconditional even if `@react-navigation/native` is module-mocked without
 * `NavigationContext` — a shape several existing screen tests use.
 */
const AbsentNavigationContext = createContext<undefined>(undefined);

export function useIsFocusedIfNavigated() {
  const navigation = useContext((NavigationContext || AbsentNavigationContext) as typeof AbsentNavigationContext) as
    | { isFocused: () => boolean; addListener: (event: string, callback: () => void) => () => void }
    | undefined;
  const [focused, setFocused] = useState(() => (navigation ? navigation.isFocused() : true));

  useEffect(() => {
    if (!navigation) {
      setFocused(true);
      return;
    }
    setFocused(navigation.isFocused());
    const unsubscribeFocus = navigation.addListener("focus", () => setFocused(true));
    const unsubscribeBlur = navigation.addListener("blur", () => setFocused(false));
    return () => {
      unsubscribeFocus();
      unsubscribeBlur();
    };
  }, [navigation]);

  return focused;
}
