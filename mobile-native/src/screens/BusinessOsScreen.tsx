import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import {
  AdAccount,
  AdAnalytics,
  adAccountCanTransact,
  BusinessOsSection,
  businessOsHubSections,
  businessOsNavigationArgs,
  formatCents,
  getAdAnalytics,
  listAdAccounts,
  loadCachedAdAccounts,
  loadCachedAdAnalytics
} from "../api/businessOs";
import { loadCachedSellerStore, loadSellerStoreSnapshot, SellerStoreSnapshot } from "../api/marketplace";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { registerSyncInvalidation } from "../core/eventSync";
import type { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  navigation: { navigate: (...args: any[]) => void };
  route?: { params?: RootStackParamList["BusinessOs"] };
};

export const BUSINESS_OS_LOAD_TIMEOUT_MS = 12_000;

/**
 * How long a canonical Business OS load stays authoritative for re-entry.
 *
 * The hub is a launcher: the user opens a tile, comes back, opens another. Every
 * one of those returns used to re-issue all four requests. Within this window a
 * re-entry paints the values already held and issues none, which is what makes
 * going back feel free. A real mutation does not wait for it — the sync
 * invalidations below force past it — so this can only ever delay the discovery
 * of a change made on another device, never one made here.
 *
 * It bounds DISPLAY counts only. Nothing on this screen is an authority: the
 * money figure is a summary, and every action lives behind a tile that fetches
 * its own fresh state.
 */
export const BUSINESS_OS_FRESHNESS_WINDOW_MS = 30_000;

/**
 * Module scope on purpose: navigating to a tile unmounts this screen, so a ref
 * or state would reset exactly when the freshness window is meant to pay off.
 *
 * Keyed by viewer. Module scope outlives a sign-out, and an unkeyed timestamp
 * would let a second account inherit the first one's window and skip the very
 * fetch that would have replaced the first account's cached numbers. The owner
 * is recorded with the timestamp so that can't happen.
 */
let lastCanonicalLoad: { userId: number | null; at: number } = { userId: null, at: 0 };

function canonicalLoadIsFresh(userId: number | null) {
  if (!lastCanonicalLoad.at) return false;
  if (lastCanonicalLoad.userId !== userId) return false;
  return Date.now() - lastCanonicalLoad.at < BUSINESS_OS_FRESHNESS_WINDOW_MS;
}

/** For sign-out and for test isolation. */
export function resetBusinessOsFreshness() {
  lastCanonicalLoad = { userId: null, at: 0 };
}

function withBusinessOsDeadline<T>(operation: Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("PulseSoc took too long to load your business.")),
      BUSINESS_OS_LOAD_TIMEOUT_MS
    );
    operation.then(
      (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      (error) => {
        clearTimeout(timeout);
        reject(error);
      }
    );
  });
}

/**
 * Business OS — the single entry point for running a business on PulseSoc.
 *
 * The hub shows only what the backend can actually report. Every tile leads to
 * a registered screen backed by a live `/api/pulse/*` contract; sections without
 * one are absent from the registry rather than rendered as dead controls.
 */
export function BusinessOsScreen({ navigation, route }: Props) {
  const { authState } = useAuth();
  // Wrong-subject guard: ad accounts, spend and the seller store are the
  // signed-in viewer's business. On another profile's route params this screen
  // refuses instead of rendering the viewer's business under that person's name.
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  // Owner of the freshness window. `null` when signed out, which never matches
  // a recorded load and so always revalidates.
  const viewerId = typeof authState.user?.user_id === "number" ? authState.user.user_id : null;
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [analytics, setAnalytics] = useState<AdAnalytics | null>(null);
  const [store, setStore] = useState<SellerStoreSnapshot | null>(null);
  // `hydrated` means "there is something real to paint", from cache or network.
  // It is deliberately not the same thing as "a request finished": the whole
  // point is that At a glance appears before any request finishes.
  const [hydrated, setHydrated] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const [stale, setStale] = useState(false);
  const [offline, setOffline] = useState(false);
  const [message, setMessage] = useState("");

  // Rule 1 of the performance constitution: a cached value may be painted, but
  // it must never overwrite a canonical response that has already landed. These
  // are per-slice because the three requests settle independently — analytics
  // can land while the store is still in flight.
  const canonical = useRef({ accounts: false, analytics: false, store: false });
  const mounted = useRef(true);
  // Collapses the three sync-invalidation subscriptions below. Without it a
  // single marketplace write that touches inventory and orders fans out into
  // concurrent full reloads of the same four endpoints.
  const inFlight = useRef<Promise<void> | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /** Paint whatever the last session left behind. Never overwrites canonical. */
  const hydrateFromCache = useCallback(async () => {
    const [cachedAccounts, cachedAnalytics, cachedStore] = await Promise.all([
      loadCachedAdAccounts().catch(() => [] as AdAccount[]),
      loadCachedAdAnalytics().catch(() => null),
      loadCachedSellerStore().catch(() => null)
    ]);
    if (!mounted.current) return false;
    let painted = false;
    if (!canonical.current.accounts && cachedAccounts.length) {
      setAccounts(cachedAccounts);
      painted = true;
    }
    if (!canonical.current.analytics && cachedAnalytics) {
      setAnalytics(cachedAnalytics);
      painted = true;
    }
    if (!canonical.current.store && cachedStore) {
      setStore(cachedStore);
      painted = true;
    }
    if (painted) {
      setHydrated(true);
      setStale(true);
    }
    return painted;
  }, []);

  const runCanonicalLoad = useCallback(async () => {
    setMessage("");
    setOffline(false);
    setRevalidating(true);
    const [adAccounts, adAnalytics, storeSnapshot] = await Promise.allSettled([
      withBusinessOsDeadline(listAdAccounts()),
      withBusinessOsDeadline(getAdAnalytics()),
      withBusinessOsDeadline(loadSellerStoreSnapshot())
    ]);
    if (!mounted.current) return;

    // Each slice is applied the moment its own request settles, so one slow
    // module cannot hold the other three off the screen.
    if (adAccounts.status === "fulfilled") {
      canonical.current.accounts = true;
      setAccounts(adAccounts.value.accounts);
    }
    if (adAnalytics.status === "fulfilled") {
      canonical.current.analytics = true;
      setAnalytics(adAnalytics.value.analytics);
    }
    if (storeSnapshot.status === "fulfilled") {
      canonical.current.store = true;
      setStore(storeSnapshot.value);
    }

    // `loadSellerStoreSnapshot` swallows its own failures and always resolves,
    // so it cannot be used as a liveness signal. The ad calls do reject, so they
    // are what tells us whether we reached PulseSoc at all.
    const unreachable = adAccounts.status === "rejected" && adAnalytics.status === "rejected";
    if (unreachable) {
      setOffline(true);
      const reason = adAccounts.reason;
      setMessage(reason instanceof Error ? reason.message : "Business OS could not reach PulseSoc.");
      // Nothing canonical landed. Fall back to cache so the screen still says
      // something true, and keep the stale marker on.
      await hydrateFromCache();
    } else {
      lastCanonicalLoad = { userId: viewerId, at: Date.now() };
      setStale(false);
    }
    setHydrated(true);
    setRevalidating(false);
  }, [hydrateFromCache, viewerId]);

  const load = useCallback(
    (options: { force?: boolean } = {}) => {
      // Collapse concurrent callers onto one load rather than racing four
      // requests against another four. Assigned synchronously below, so three
      // sync invalidations firing in one tick see it.
      if (inFlight.current) return inFlight.current;

      const withinWindow = !options.force && canonicalLoadIsFresh(viewerId);

      const run = (async () => {
        if (withinWindow) {
          // Re-entry inside the window. Paint what the last canonical load
          // produced and ask for nothing.
          const painted = await hydrateFromCache();
          if (painted) {
            if (mounted.current) {
              setStale(false);
              setHydrated(true);
            }
            return;
          }
          // Nothing cached to honour the window with, so fall through and fetch
          // rather than showing an empty hub on a technicality.
        }
        // The cache read runs ALONGSIDE the network, never in front of it.
        // Awaiting it first would put an AsyncStorage bridge hop between the tap
        // and the three requests — a waterfall introduced by the very change
        // meant to remove one. `hydrateFromCache` is safe to race because every
        // write it makes is gated on the canonical flags.
        const hydration = hydrateFromCache().catch(() => false);
        await runCanonicalLoad();
        await hydration;
      })().finally(() => {
        inFlight.current = null;
      });

      inFlight.current = run;
      return run;
    },
    [hydrateFromCache, runCanonicalLoad, viewerId]
  );

  useEffect(() => {
    // Owner-only fetch: skip entirely on a visitor route (no fetch-then-hide).
    if (!routeContext.isOwnProfile) return;
    load().catch(() => undefined);
  }, [load, routeContext.isOwnProfile]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    // A real mutation always revalidates: `force` skips the freshness window.
    const refresh = () => {
      load({ force: true }).catch(() => undefined);
    };
    const unregisterSeller = registerSyncInvalidation("seller_inventory", refresh);
    const unregisterMarketplace = registerSyncInvalidation("marketplace", refresh);
    const unregisterOrders = registerSyncInvalidation("orders", refresh);
    return () => {
      unregisterSeller();
      unregisterMarketplace();
      unregisterOrders();
    };
  }, [load, routeContext.isOwnProfile]);

  function openSection(section: BusinessOsSection) {
    const [route, params] = businessOsNavigationArgs(section);
    navigation.navigate(route, params);
  }

  const listings = store?.listings?.length || 0;
  const sellerOrders = store?.orders?.length || 0;
  const activeCampaigns = analytics?.campaigns.filter((row) => String(row.status) === "active").length || 0;
  const spendCents = analytics?.totals.spend_cents || 0;
  const verifiedAccount = accounts.some(adAccountCanTransact);

  // Visitor destination with no visitor variant: refuse rather than render the
  // viewer's business under another person's name. All hooks have already run.
  if (!routeContext.isOwnProfile) {
    return (
      <Screen title="Business OS">
        <Panel>
          <Text style={styles.muted}>{PRIVATE_CONTENT_MESSAGE}</Text>
        </Panel>
      </Screen>
    );
  }

  return (
    <Screen title="Business OS" subtitle="Run your store, marketplace listings and advertising in one place.">
      {offline ? (
        <Panel>
          <Text style={styles.panelTitle}>Showing saved data</Text>
          <Text style={styles.muted}>{message || "PulseSoc could not be reached, so this is your last synced view."}</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Retry loading Business OS"
            onPress={() => load({ force: true }).catch(() => undefined)}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryButtonText}>Retry</Text>
          </Pressable>
        </Panel>
      ) : null}

      {/*
        At a glance renders as soon as there is anything true to show, and stays
        rendered while a revalidation runs. It used to be gated on `!loading`,
        which meant every refresh — including the three sync invalidations —
        removed the numbers from the screen and put a spinner in their place.
        The values only ever move now; they never disappear.
      */}
      <Panel>
        <View style={styles.glanceHeader}>
          <Text style={styles.panelTitle}>At a glance</Text>
          {revalidating ? (
            <ActivityIndicator
              accessibilityLabel="Refreshing your business summary"
              color={colors.accent}
              size="small"
            />
          ) : null}
        </View>
        {hydrated ? (
          <>
            <View style={styles.metrics}>
              <Metric label="Live listings" value={String(listings)} />
              <Metric label="Orders" value={String(sellerOrders)} />
              <Metric label="Active campaigns" value={String(activeCampaigns)} />
              <Metric label="Ad spend" value={formatCents(spendCents)} />
            </View>
            {stale ? <Text style={styles.footnote}>Last synced view — refreshing now.</Text> : null}
            <Text style={styles.footnote}>
              {accounts.length
                ? verifiedAccount
                  ? "Your ad account is active and can run campaigns."
                  : "Your ad account is awaiting verification, so campaigns cannot deliver yet."
                : "No ad account yet. Create one in Advertising to start running campaigns."}
            </Text>
          </>
        ) : (
          <View style={styles.metrics}>
            {/*
              Placeholders, not fake data: each corresponds to a request that is
              genuinely in flight, and they carry the real labels so the panel
              does not change shape when the numbers arrive.
            */}
            <Metric label="Live listings" value="—" />
            <Metric label="Orders" value="—" />
            <Metric label="Active campaigns" value="—" />
            <Metric label="Ad spend" value="—" />
          </View>
        )}
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Sections</Text>
        <View style={styles.grid}>
          {businessOsHubSections().map((section) => (
            <Pressable
              key={section.key}
              accessibilityRole="button"
              accessibilityLabel={`${section.label}. ${section.blurb}`}
              onPress={() => openSection(section)}
              style={styles.tile}
            >
              <Ionicons name={section.icon as never} size={20} color={colors.accent} />
              <Text style={styles.tileLabel}>{section.label}</Text>
              <Text style={styles.tileBlurb} numberOfLines={2}>
                {section.blurb}
              </Text>
            </Pressable>
          ))}
        </View>
      </Panel>
    </Screen>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View accessible accessibilityLabel={`${label}: ${value}`} style={styles.metric}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  footnote: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  glanceHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  metric: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    flexBasis: "47%",
    flexGrow: 1,
    gap: 4,
    padding: 12
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12
  },
  metricValue: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "800"
  },
  metrics: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  panelTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "700"
  },
  secondaryButton: {
    alignSelf: "flex-start",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  secondaryButtonText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "600"
  },
  tile: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    flexBasis: "47%",
    flexGrow: 1,
    gap: 6,
    padding: 12
  },
  tileBlurb: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  tileLabel: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  }
}));
