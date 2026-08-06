import { useCallback, useEffect, useState } from "react";
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
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [analytics, setAnalytics] = useState<AdAnalytics | null>(null);
  const [store, setStore] = useState<SellerStoreSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setMessage("");
    setOffline(false);
    const [adAccounts, adAnalytics, storeSnapshot] = await Promise.allSettled([
      listAdAccounts(),
      getAdAnalytics(),
      loadSellerStoreSnapshot()
    ]);

    if (adAccounts.status === "fulfilled") {
      setAccounts(adAccounts.value.accounts);
    } else {
      setAccounts(await loadCachedAdAccounts().catch(() => []));
    }

    if (adAnalytics.status === "fulfilled") {
      setAnalytics(adAnalytics.value.analytics);
    } else {
      setAnalytics(await loadCachedAdAnalytics().catch(() => null));
    }

    if (storeSnapshot.status === "fulfilled") {
      setStore(storeSnapshot.value);
    } else {
      setStore(await loadCachedSellerStore().catch(() => null));
    }

    // `loadSellerStoreSnapshot` swallows its own failures and always resolves,
    // so it cannot be used as a liveness signal. The ad calls do reject, so they
    // are what tells us whether we reached PulseSoc at all.
    if (adAccounts.status === "rejected" && adAnalytics.status === "rejected") {
      setOffline(true);
      const reason = adAccounts.reason;
      setMessage(reason instanceof Error ? reason.message : "Business OS could not reach PulseSoc.");
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    // Owner-only fetch: skip entirely on a visitor route (no fetch-then-hide).
    if (!routeContext.isOwnProfile) return;
    load().catch(() => undefined);
  }, [load, routeContext.isOwnProfile]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    const refresh = () => {
      load().catch(() => undefined);
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
      {loading ? (
        <Panel>
          <View style={styles.loadingRow}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.muted}>Loading your business…</Text>
          </View>
        </Panel>
      ) : null}

      {offline && !loading ? (
        <Panel>
          <Text style={styles.panelTitle}>Showing saved data</Text>
          <Text style={styles.muted}>{message || "PulseSoc could not be reached, so this is your last synced view."}</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Retry loading Business OS"
            onPress={() => load().catch(() => undefined)}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryButtonText}>Retry</Text>
          </Pressable>
        </Panel>
      ) : null}

      {!loading ? (
        <Panel>
          <Text style={styles.panelTitle}>At a glance</Text>
          <View style={styles.metrics}>
            <Metric label="Live listings" value={String(listings)} />
            <Metric label="Orders" value={String(sellerOrders)} />
            <Metric label="Active campaigns" value={String(activeCampaigns)} />
            <Metric label="Ad spend" value={formatCents(spendCents)} />
          </View>
          <Text style={styles.footnote}>
            {accounts.length
              ? verifiedAccount
                ? "Your ad account is active and can run campaigns."
                : "Your ad account is awaiting verification, so campaigns cannot deliver yet."
              : "No ad account yet. Create one in Advertising to start running campaigns."}
          </Text>
        </Panel>
      ) : null}

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
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  loadingRow: {
    alignItems: "center",
    flexDirection: "row",
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
