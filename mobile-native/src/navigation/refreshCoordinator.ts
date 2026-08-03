export type RefreshDestination =
  | "home"
  | "reels"
  | "social-messages"
  | "profile"
  | "business-home"
  | "store"
  | "marketplace-buying"
  | "marketplace-selling"
  | "commerce-inbox"
  | "orders-buying"
  | "orders-selling"
  | "advertising"
  | "payments"
  | "insights"
  | "events"
  | "verification"
  | "notifications";

export type RefreshIntent = {
  destination: RefreshDestination;
  source: "double-tap" | "pull-to-refresh" | "retry" | "programmatic";
  scrollToTop: boolean;
  preserveFilters: boolean;
  preserveDrafts: true;
};

export type RefreshDestinationRegistration = {
  scrollToTop: () => void | Promise<void>;
  refresh: (intent: RefreshIntent) => void | Promise<void>;
  isRefreshing?: () => boolean;
  lastRefreshedAt?: () => number;
  canRefresh?: () => boolean;
};

export type NavigationTapResolution =
  | { type: "navigate" }
  | { type: "root"; destination: RefreshDestination }
  | { type: "refresh"; intent: RefreshIntent }
  | { type: "create" };

const DOUBLE_TAP_MS = 300;
const registry = new Map<RefreshDestination, RefreshDestinationRegistration>();
const runningRefreshes = new Set<RefreshDestination>();

let lastTap: { destination: RefreshDestination; controlId: string; at: number } | null = null;

export function registerRefreshDestination(destination: RefreshDestination, registration: RefreshDestinationRegistration) {
  const existing = registry.get(destination);
  if (existing && existing !== registration) {
    throw new Error(`Refresh destination already registered: ${destination}`);
  }
  registry.set(destination, registration);
  return () => {
    if (registry.get(destination) === registration) registry.delete(destination);
  };
}

export function registeredRefreshDestinations() {
  return Array.from(registry.keys());
}

export function clearRefreshCoordinatorForTests() {
  registry.clear();
  runningRefreshes.clear();
  lastTap = null;
}

export function resolveNavigationTap({
  active,
  destination,
  controlId,
  now = Date.now()
}: {
  active: boolean;
  destination: RefreshDestination | null;
  controlId: string;
  now?: number;
}): NavigationTapResolution {
  if (!destination) return { type: "create" };
  if (!active) {
    lastTap = null;
    return { type: "navigate" };
  }

  const repeated =
    lastTap?.destination === destination &&
    lastTap.controlId === controlId &&
    now - lastTap.at > 0 &&
    now - lastTap.at <= DOUBLE_TAP_MS;

  if (repeated) {
    lastTap = null;
    return {
      type: "refresh",
      intent: {
        destination,
        source: "double-tap",
        scrollToTop: true,
        preserveFilters: true,
        preserveDrafts: true
      }
    };
  }

  lastTap = { destination, controlId, at: now };
  return { type: "root", destination };
}

export function cancelRefreshTapWindow() {
  lastTap = null;
}

export function scrollRefreshDestinationToTop(destination: RefreshDestination) {
  const registration = registry.get(destination);
  if (!registration) return false;
  Promise.resolve(registration.scrollToTop()).catch(() => undefined);
  return true;
}

export function triggerRefreshDestination(intent: RefreshIntent) {
  const registration = registry.get(intent.destination);
  if (!registration) return false;
  if (registration.canRefresh && !registration.canRefresh()) return false;
  if (registration.isRefreshing?.() || runningRefreshes.has(intent.destination)) return false;

  runningRefreshes.add(intent.destination);
  Promise.resolve()
    .then(async () => {
      if (intent.scrollToTop) await registration.scrollToTop();
      await registration.refresh(intent);
    })
    .catch(() => undefined)
    .finally(() => runningRefreshes.delete(intent.destination));
  return true;
}
