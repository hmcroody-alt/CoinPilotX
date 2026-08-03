/**
 * Hub bindings — one independent, subscribable store per owner source.
 *
 * This is the performance half of the Business Hub. The hub is the seller's
 * front door: it must paint instantly and it must never let one slow or broken
 * section hold the other nine hostage. Both properties come from the same
 * decision — the grid does NOT load. Each card subscribes to exactly one
 * binding, and a binding is the only thing that can make its cards re-render.
 *
 * WHY NOT ONE MODEL. The obvious shape is `loadBusinessHub()` returning one
 * object with every card's data on it. That shape has two defects the mission
 * names directly. It cannot paint until the slowest of seven calls returns, and
 * because every card reads fields off one object, any refresh re-renders all
 * ten. Splitting into per-source stores makes both impossible rather than
 * merely unlikely: a card that never reads another binding cannot re-render
 * when that binding changes, and there is no code path that waits for all of
 * them.
 *
 * WHY THIS SHAPE. `core/unreadCounts.ts` already established the app's idiom
 * for a shared, subscribable value — a module singleton, a listener `Set`, a
 * `useSyncExternalStore` hook, and `registerSyncInvalidation` wiring. This is
 * that idiom, generalised once so seven sources do not each hand-roll it.
 *
 * NO NEW POLLING. Nothing here sets an interval. Refresh happens when the
 * owners' existing sync events fire (`registerSyncInvalidation`), when the
 * screen is focused, or when the seller pulls. The hub adds no background load
 * to a device that is already running the app's sync poller.
 *
 * UNREAD IS NOT HERE, DELIBERATELY. The Messages card and the strip's unread
 * cell read `core/unreadCounts` directly. Wrapping it in a binding would create
 * a second store for a number that already has one — precisely the duplication
 * this whole design exists to prevent.
 */

import { useSyncExternalStore } from "react";
import { NativeSyncSubsystem, registerSyncInvalidation } from "./eventSync";
import { AdsMarketplaceModel, loadAdsMarketplace } from "../api/adsDashboard";
import { InsightsLoad, loadInsights } from "../api/insightsDashboard";
import { SellerOrdersModel, loadSellerOrdersModel } from "../api/ordersDashboard";
import {
  SellerApplicationView,
  loadCachedSellerApplication,
  loadSellerApplication
} from "../api/sellerApplication";
import { StoreLoadResult, loadStoreDashboard } from "../api/storeDashboard";
import {
  VerificationState,
  loadCachedVerificationState,
  loadVerificationState
} from "../api/verification";
import { isStale } from "../api/businessHub";

/* ------------------------------------------------------------------ *
 * Snapshot
 * ------------------------------------------------------------------ */

export type HubBindingStatus =
  /** Nothing attempted yet. The card shows its static subtitle, not a spinner. */
  | "idle"
  /** First load in flight and nothing to show yet. */
  | "loading"
  /** Holding data — possibly from cache, see `fromCache`. */
  | "ready"
  /** Load failed and there is no cached fallback. */
  | "error";

export type HubBindingSnapshot<T> = {
  status: HubBindingStatus;
  data: T | null;
  /**
   * Epoch ms the held data describes the world at. For a cached hydrate this is
   * the cache's own write time, NOT the moment it was read off disk — otherwise
   * a ten-minute-old cache would look fresh the instant it loaded, which is
   * exactly the mistake the staleness windows exist to catch.
   */
  loadedAt: number;
  fromCache: boolean;
  error: string | null;
};

const IDLE: HubBindingSnapshot<never> = {
  status: "idle",
  data: null,
  loadedAt: 0,
  fromCache: false,
  error: null
};

export type HubBinding<T> = {
  /** Matches a key in `HUB_STALENESS_MS`, so staleness is looked up not passed. */
  readonly key: string;
  getSnapshot: () => HubBindingSnapshot<T>;
  subscribe: (listener: () => void) => () => void;
  /** Paint from disk if the binding has not loaded yet. Never overwrites live data. */
  hydrate: () => Promise<HubBindingSnapshot<T>>;
  /** Fetch. De-duped: a concurrent call shares the in-flight request. */
  refresh: () => Promise<HubBindingSnapshot<T>>;
  /** Which sync subsystems refresh this binding. Empty means focus/pull only. */
  readonly invalidatedBy: readonly NativeSyncSubsystem[];
  /** Test-only. */
  __reset: () => void;
};

type HubBindingConfig<T> = {
  key: string;
  load: () => Promise<T>;
  /**
   * Optional cached read for instant first paint. Returns the value AND the
   * time it was written; a binding whose owner has no cache simply omits this
   * and starts at "loading" instead of painting stale.
   */
  hydrate?: () => Promise<{ data: T; savedAt: number } | null>;
  invalidatedBy?: NativeSyncSubsystem[];
};

/* ------------------------------------------------------------------ *
 * Factory
 * ------------------------------------------------------------------ */

export function createHubBinding<T>(config: HubBindingConfig<T>): HubBinding<T> {
  let snapshot: HubBindingSnapshot<T> = IDLE as HubBindingSnapshot<T>;
  const listeners = new Set<() => void>();
  let inFlight: Promise<HubBindingSnapshot<T>> | null = null;

  /**
   * Publish only on real change. `useSyncExternalStore` compares snapshots by
   * identity, so returning a fresh object every call would spin forever, and
   * emitting an equal snapshot would re-render this binding's cards for nothing.
   */
  function publish(next: HubBindingSnapshot<T>) {
    if (
      next.status === snapshot.status &&
      next.data === snapshot.data &&
      next.loadedAt === snapshot.loadedAt &&
      next.fromCache === snapshot.fromCache &&
      next.error === snapshot.error
    ) {
      return;
    }
    snapshot = next;
    listeners.forEach((listener) => listener());
  }

  async function hydrate(): Promise<HubBindingSnapshot<T>> {
    // A cache read that finishes after the network must not undo the network.
    // Only an untouched binding accepts a hydrate.
    if (!config.hydrate || snapshot.status !== "idle") return snapshot;
    try {
      const cached = await config.hydrate();
      if (!cached || snapshot.status !== "idle") return snapshot;
      publish({
        status: "ready",
        data: cached.data,
        loadedAt: cached.savedAt,
        fromCache: true,
        error: null
      });
    } catch {
      // A missing or corrupt cache is not an error state — it just means the
      // binding paints when the network answers.
    }
    return snapshot;
  }

  async function refresh(): Promise<HubBindingSnapshot<T>> {
    if (inFlight) return inFlight;
    if (snapshot.status === "idle") {
      publish({ ...snapshot, status: "loading" });
    }
    inFlight = (async () => {
      try {
        const data = await config.load();
        publish({ status: "ready", data, loadedAt: Date.now(), fromCache: false, error: null });
      } catch (error) {
        // A binding that already holds data keeps it. Losing a good number
        // because a later refresh failed is worse than showing it a bit old —
        // the "as of {time}" affordance is what tells the seller which it is.
        if (snapshot.data !== null) {
          publish({ ...snapshot, error: messageFor(error) });
        } else {
          publish({
            status: "error",
            data: null,
            loadedAt: 0,
            fromCache: false,
            error: messageFor(error)
          });
        }
      } finally {
        inFlight = null;
      }
      return snapshot;
    })();
    return inFlight;
  }

  return {
    key: config.key,
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    hydrate,
    refresh,
    invalidatedBy: config.invalidatedBy || [],
    __reset: () => {
      snapshot = IDLE as HubBindingSnapshot<T>;
      listeners.clear();
      inFlight = null;
    }
  };
}

function messageFor(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error || "");
  return raw.trim() || "Couldn't refresh.";
}

/* ------------------------------------------------------------------ *
 * The bindings
 *
 * One per owner source. The `invalidatedBy` lists are not guesses — they are
 * the subsystems `subsystemsForSyncEvent` actually emits for the events that
 * would change each number.
 * ------------------------------------------------------------------ */

/** Orders awaiting the seller. Owner: `api/ordersDashboard`. */
export const ordersBinding = createHubBinding<SellerOrdersModel>({
  key: "orders",
  load: loadSellerOrdersModel,
  // A purchase, a shipment and a refund all emit "orders"; a marketplace sale
  // emits "marketplace" before the order row exists.
  invalidatedBy: ["orders", "marketplace"]
});

/** Listing health. Owner: `api/storeDashboard`. */
export const storeBinding = createHubBinding<StoreLoadResult>({
  key: "store",
  load: loadStoreDashboard,
  // `loadStoreDashboard` already falls back to the seller-store cache internally
  // when both halves fail, so a separate hydrate here would read the same disk
  // twice. Stock edits emit "seller_inventory"; a sale emits "marketplace".
  invalidatedBy: ["seller_inventory", "marketplace"]
});

/** Today's revenue, for the strip. Owner: `api/insightsDashboard`. */
export const insightsTodayBinding = createHubBinding<InsightsLoad>({
  key: "insightsToday",
  load: () => loadInsights("today"),
  invalidatedBy: ["orders"]
});

/** The 7-day trend, for the Insights card. Owner: `api/insightsDashboard`. */
export const insights7dBinding = createHubBinding<InsightsLoad>({
  key: "insights7d",
  load: () => loadInsights("7d"),
  invalidatedBy: ["orders"]
});

/**
 * Ad accounts, campaigns and the wallet. Owner: `api/adsDashboard`.
 *
 * ONE binding feeds TWO cards (Advertising and Payments) because it is one
 * network round of one owner. That is fan-out from a single source, not
 * duplication: neither card re-derives anything, and the wallet figure on
 * Payments is the same object the Advertising card's account state came from,
 * so the two can never disagree.
 *
 * Ads has no sync subsystem — no server event says "your campaign changed" —
 * so this refreshes on focus and pull only. Named here rather than left to be
 * noticed as a bug.
 */
export const adsBinding = createHubBinding<AdsMarketplaceModel>({
  key: "ads",
  load: loadAdsMarketplace,
  invalidatedBy: []
});

/**
 * Verification. Owner: `api/verification`.
 *
 * Feeds the header tick, the context line AND the Verification card — three
 * renderings of one status, which is the point. Hydrated from cache because the
 * tick is the first thing the seller looks at and an absent tick reads as
 * "not verified" rather than as "still loading".
 */
export const verificationBinding = createHubBinding<VerificationState>({
  key: "verification",
  load: loadVerificationState,
  hydrate: async () => {
    const cached = await loadCachedVerificationState();
    // The verification cache stores no write time. Rather than invent one,
    // report 0 — `isStale` treats a zero `loadedAt` as stale, which is the safe
    // reading, and verification carries an infinite window anyway so nothing
    // downstream degrades. Recorded in the report as a small owner gap.
    return cached ? { data: cached, savedAt: 0 } : null;
  },
  invalidatedBy: ["verification"]
});

/** Seller profile completeness. Owner: `api/sellerApplication`. */
export const profileBinding = createHubBinding<SellerApplicationView>({
  key: "profile",
  load: loadSellerApplication,
  hydrate: async () => {
    const cached = await loadCachedSellerApplication();
    return cached ? { data: cached, savedAt: 0 } : null;
  },
  invalidatedBy: []
});

/**
 * Every binding, for the lifecycle helpers. Typed as `HubBinding<unknown>`
 * because `T` appears only in output positions, so the widening is safe and the
 * list stays iterable without a union of seven call signatures.
 */
export const HUB_BINDINGS: readonly HubBinding<unknown>[] = [
  ordersBinding,
  storeBinding,
  insightsTodayBinding,
  insights7dBinding,
  adsBinding,
  verificationBinding,
  profileBinding
];

/* ------------------------------------------------------------------ *
 * Lifecycle
 * ------------------------------------------------------------------ */

/**
 * Wire every binding to the owners' sync events. Opt-in, called once by the
 * screen, so importing this module never triggers network and the bindings stay
 * unit-testable. Returns an unsubscribe.
 *
 * Each registration refreshes exactly one binding. An orders event does not
 * touch the store binding, so a sale re-renders the Orders card and the strip's
 * fulfil cell and nothing else.
 */
export function initHubBindings(): () => void {
  const offs: Array<() => void> = [];
  HUB_BINDINGS.forEach((binding) => {
    binding.invalidatedBy.forEach((subsystem) => {
      offs.push(
        registerSyncInvalidation(subsystem, () => {
          void binding.refresh();
        })
      );
    });
  });
  return () => offs.forEach((off) => off());
}

/**
 * First paint. Hydrates every binding from cache and kicks off every fetch,
 * without awaiting any of them together — the caller is not blocked, and each
 * card paints the moment its own source lands.
 *
 * `void` rather than `Promise.all` is the load-bearing detail: there is
 * deliberately no promise here that resolves "when the hub is ready", because
 * no such moment exists and inventing one would recreate the all-or-nothing
 * load this design removes.
 */
export function startHubBindings(): void {
  HUB_BINDINGS.forEach((binding) => {
    void binding.hydrate().then(() => binding.refresh());
  });
}

/** Focus and pull-to-refresh. Fires all bindings in parallel; awaits for the spinner. */
export async function refreshHubBindings(): Promise<void> {
  await Promise.allSettled(HUB_BINDINGS.map((binding) => binding.refresh()));
}

/** Test-only reset of every module singleton. */
export function __resetHubBindings(): void {
  HUB_BINDINGS.forEach((binding) => binding.__reset());
}

/* ------------------------------------------------------------------ *
 * React binding
 * ------------------------------------------------------------------ */

/**
 * Subscribe a component to one source. A card calling this re-renders when that
 * binding publishes and at no other time — the per-card isolation the mission
 * requires is a consequence of the hook's argument, not of discipline.
 */
export function useHubBinding<T>(binding: HubBinding<T>): HubBindingSnapshot<T> {
  return useSyncExternalStore(binding.subscribe, binding.getSnapshot, binding.getSnapshot);
}

/**
 * True when this binding's data is too old to support a deadline claim.
 *
 * Evaluated at render against the binding's own window from `HUB_STALENESS_MS`.
 * Sources with an infinite window always return false, so this costs nothing on
 * the nine bindings that are facts rather than countdowns.
 */
export function isBindingStale<T>(
  binding: HubBinding<T>,
  snapshot: HubBindingSnapshot<T>,
  now: number = Date.now()
): boolean {
  if (snapshot.data === null) return false;
  return isStale(binding.key, snapshot.loadedAt, now);
}

/**
 * The "as of {time}" label, shown only when the screen is displaying something
 * it could not just re-verify. Returns null when the data is live, so the
 * common case renders no chrome at all.
 */
export function asOfLabel(snapshot: HubBindingSnapshot<unknown>): string | null {
  if (!snapshot.fromCache && !snapshot.error) return null;
  if (!snapshot.loadedAt) return "as of earlier";
  const when = new Date(snapshot.loadedAt);
  const hours = when.getHours();
  const minutes = String(when.getMinutes()).padStart(2, "0");
  const suffix = hours >= 12 ? "PM" : "AM";
  const hour12 = hours % 12 === 0 ? 12 : hours % 12;
  return `as of ${hour12}:${minutes} ${suffix}`;
}
