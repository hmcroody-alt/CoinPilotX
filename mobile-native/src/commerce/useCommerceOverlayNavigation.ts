/**
 * The one way a commerce overlay gets you somewhere.
 *
 * ## Why a shared hook rather than four inline handlers
 *
 * A PulseDrop overlay renders on at least four surfaces — the Reels player, the
 * Home feed, a post detail and the profile post viewer — and each of them owns
 * its own `navigation` object. Written inline, that is four copies of the same
 * two-line decision about which field to prefer and what an empty route means,
 * and the first one to be written differently is a screen where tapping the
 * product does nothing. That exact failure already happened once here: the Reel
 * overlay shipped gated behind a handler nobody passed, so PulseDrop Reels
 * rendered no commerce at all while every unit test of the overlay passed.
 *
 * ## Why it takes `navigation` instead of calling `useNavigation`
 *
 * Every screen in this app receives `navigation` as a prop from the native
 * stack. Reaching for the context instead would work, but it would also make
 * this hook unusable from a component rendered outside a navigator — and the
 * overlay is rendered by `PostCard`, which is also mounted by the draft preview.
 * Taking the object explicitly keeps the dependency visible.
 *
 * ## Why the route is a string the server chose
 *
 * The payload carries `/pulse/marketplace/77`, not `{ screen: "MarketplaceDetail",
 * params: { listingId: 77 } }`. `openNativeRoute` is the single resolver that
 * already turns one into the other, and it is the same one the OS-level deep
 * link layer uses. Naming a screen here would be a second resolver for paths the
 * deep-link layer owns — which is how `/pulse/merchant/<id>` ended up working
 * from a universal link and silently doing nothing from an in-app tap.
 */

import { useMemo } from "react";
import type { PulseCommerceOverlay } from "../api/pulseCommerceOverlay";
import { NativeRouteNavigation, openNativeRoute } from "../navigation/nativeRouteActions";

export type CommerceOverlayNavigation = {
  onOpenCommerceProduct: (commerce: PulseCommerceOverlay) => void;
  onOpenCommerceSeller: (commerce: PulseCommerceOverlay) => void;
};

/**
 * Empty is a real answer.
 *
 * A withdrawn listing arrives with `cta.route === ""` on purpose, because
 * `/pulse/marketplace/<id>` 404s for anything not publicly visible. Navigating
 * to `""` would resolve to nothing at best and to the feed root at worst; doing
 * nothing is the honest behaviour, and the overlay has already disabled the
 * button by the time we could be called for one.
 */
function openCommerceRoute(navigation: NativeRouteNavigation, route: string | undefined) {
  const target = String(route || "").trim();
  if (!target) return;
  openNativeRoute(navigation, target);
}

export function useCommerceOverlayNavigation(navigation: NativeRouteNavigation): CommerceOverlayNavigation {
  // Stable across renders so `PostCard`'s memo keeps holding. The card is the
  // most expensive row in the feed to re-render, and a fresh callback identity
  // every frame would defeat the memo for every post on screen, not just the
  // PulseDrop ones.
  return useMemo(
    () => ({
      // `cta.route` is the only route to the product. There is no second one.
      //
      // This read `commerce.cta?.route || commerce.product?.route` until
      // 2026-09-28, described as a fallback "for a payload whose CTA is a bare
      // label". No such payload exists: `_product` in
      // `services/pulsedrop/hydration.py` has never emitted a `route`, so the
      // right-hand side was always `undefined` and the fallback never once
      // fired. Removed rather than kept as insurance, because a safety net that
      // cannot catch anything is worse than none — it tells the next reader
      // that an empty `cta.route` still has somewhere to go, and it does not.
      onOpenCommerceProduct: (commerce: PulseCommerceOverlay) =>
        openCommerceRoute(navigation, commerce.cta?.route),
      // Deliberately a different destination. PulseDrop publishes, the seller
      // sells, and one tap that did both would make the two accounts look like
      // one — which is the attribution the whole payload exists to keep apart.
      onOpenCommerceSeller: (commerce: PulseCommerceOverlay) =>
        openCommerceRoute(navigation, commerce.seller?.route),
    }),
    [navigation]
  );
}
