import { AppTabParamList } from "./types";

// Typed per-route bottom-navigation policy (mission §04). This is the single
// source of truth for how the global bottom navigation behaves on each tab,
// replacing route-name string matching. Immersive and modal surfaces
// (Camera, native Live host, Chat, composer) are stack screens that never
// render the tab bar, so they are structurally "not-rendered" and are not
// listed here.
export type BottomNavScreenPolicy =
  | "always-visible"
  | "scroll-responsive"
  | "immersive-hidden"
  | "temporarily-hidden"
  | "not-rendered";

export const BOTTOM_NAV_POLICY: Record<keyof AppTabParamList, BottomNavScreenPolicy> = {
  Home: "scroll-responsive",
  Reels: "scroll-responsive",
  Search: "scroll-responsive",
  Saved: "scroll-responsive",
  Groups: "scroll-responsive",
  Status: "scroll-responsive",
  Messenger: "scroll-responsive",
  Notifications: "scroll-responsive",
  Profile: "scroll-responsive",
  Marketplace: "scroll-responsive",
  Dashboard: "always-visible",
  Live: "always-visible",
  PulseAI: "always-visible",
  Settings: "always-visible",
  Create: "not-rendered"
};

export function resolveBottomNavPolicy(routeName?: string | null): BottomNavScreenPolicy {
  if (routeName && routeName in BOTTOM_NAV_POLICY) {
    return BOTTOM_NAV_POLICY[routeName as keyof AppTabParamList];
  }
  return "scroll-responsive";
}

export function isScrollResponsivePolicy(routeName?: string | null): boolean {
  return resolveBottomNavPolicy(routeName) === "scroll-responsive";
}
