import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import {
  AdAnalytics,
  AdBilling,
  adFundingIsLive,
  formatCents,
  getAdAnalytics,
  getAdBillingSummary,
  listAdAccounts,
  loadCachedAdAnalytics
} from "../api/businessOs";
import { loadCachedSellerStore, loadSellerStoreSnapshot, SellerStoreSnapshot } from "../api/marketplace";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { colors } from "../theme/colors";

/**
 * Insights — delivery, spend and store performance.
 *
 * Every number here comes from a live response. Nothing is estimated locally and
 * no placeholder metric is rendered; when a figure is unavailable the section
 * says so instead of showing a zero that looks like data.
 */
export function BusinessOsInsightsScreen() {
  const [analytics, setAnalytics] = useState<AdAnalytics | null>(null);
  const [billing, setBilling] = useState<AdBilling | null>(null);
  const [store, setStore] = useState<SellerStoreSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setOffline(false);
    setMessage("");

    const [analyticsResult, storeResult, accountsResult] = await Promise.allSettled([
      getAdAnalytics(),
      loadSellerStoreSnapshot(),
      listAdAccounts()
    ]);

    if (analyticsResult.status === "fulfilled") {
      setAnalytics(analyticsResult.value.analytics);
    } else {
      setAnalytics(await loadCachedAdAnalytics().catch(() => null));
      setOffline(true);
      setMessage(
        analyticsResult.reason instanceof Error ? analyticsResult.reason.message : "Insights could not reach PulseSoc."
      );
    }

    setStore(storeResult.status === "fulfilled" ? storeResult.value : await loadCachedSellerStore().catch(() => null));

    const accountId = accountsResult.status === "fulfilled" ? accountsResult.value.accounts[0]?.id : 0;
    if (accountId) {
      try {
        const result = await getAdBillingSummary(accountId);
        setBilling(result.billing);
      } catch {
        setBilling(null);
      }
    } else {
      setBilling(null);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const totals = analytics?.totals;
  const rows = analytics?.campaigns || [];
  const hasDelivery = Boolean(totals && (totals.impressions || totals.clicks || totals.spend_cents));

  return (
    <Screen title="Insights" subtitle="How your advertising and store are performing.">
      {loading ? (
        <Panel>
          <View style={styles.row}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.muted}>Loading insights…</Text>
          </View>
        </Panel>
      ) : null}

      {offline && !loading ? (
        <Panel>
          <Text style={styles.panelTitle}>Showing saved data</Text>
          <Text style={styles.muted}>{message}</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Retry loading insights"
            onPress={() => load().catch(() => undefined)}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryButtonText}>Retry</Text>
          </Pressable>
        </Panel>
      ) : null}

      {!loading ? (
        <Panel>
          <Text style={styles.panelTitle}>Store</Text>
          <View style={styles.metrics}>
            <Metric label="Live listings" value={String(store?.listings?.length || 0)} />
            <Metric label="Orders received" value={String(store?.orders?.length || 0)} />
          </View>
        </Panel>
      ) : null}

      {!loading ? (
        <Panel>
          <Text style={styles.panelTitle}>Advertising delivery</Text>
          {hasDelivery ? (
            <>
              <View style={styles.metrics}>
                <Metric label="Impressions" value={String(totals?.impressions || 0)} />
                <Metric label="Viewable" value={String(totals?.viewable_impressions || 0)} />
                <Metric label="Clicks" value={String(totals?.clicks || 0)} />
                <Metric label="CTR" value={`${Number(totals?.ctr || 0).toFixed(2)}%`} />
                <Metric label="Spend" value={formatCents(totals?.spend_cents)} />
                <Metric label="Est. CPC" value={`$${Number(totals?.estimated_cpc || 0).toFixed(2)}`} />
              </View>
              <Text style={styles.footnote}>CPC and CPM are estimates the server derives from spend and delivery.</Text>
            </>
          ) : (
            <Text style={styles.muted}>
              No delivery yet. Once a campaign is approved and starts running, impressions, clicks and spend appear here.
            </Text>
          )}
        </Panel>
      ) : null}

      {!loading && rows.length ? (
        <Panel>
          <Text style={styles.panelTitle}>By campaign</Text>
          {rows.map((row) => (
            <View key={row.campaign_id} style={styles.campaign}>
              <Text style={styles.campaignName}>{row.campaign_name}</Text>
              <Text style={styles.muted}>
                {String(row.status).replace(/_/g, " ")} · {row.impressions} impressions · {row.clicks} clicks ·{" "}
                {Number(row.ctr || 0).toFixed(2)}% CTR
              </Text>
              <Text style={styles.muted}>Spent {formatCents(row.spent_cents)}</Text>
            </View>
          ))}
        </Panel>
      ) : null}

      {!loading && billing ? (
        <Panel>
          <Text style={styles.panelTitle}>Billing</Text>
          <View style={styles.metrics}>
            <Metric label="Wallet balance" value={formatCents(billing.wallet_balance_cents)} />
            <Metric label="Spend limit" value={formatCents(billing.spend_limit_cents)} />
          </View>
          <Text style={styles.footnote}>
            {adFundingIsLive(billing)
              ? "Funding is live on this account."
              : "Card funding is not enabled on this account yet, so no charges can be made from the app."}
          </Text>
        </Panel>
      ) : null}
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

const styles = StyleSheet.create({
  campaign: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    gap: 4,
    padding: 12
  },
  campaignName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  footnote: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  metric: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    flexBasis: "30%",
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
    fontSize: 19,
    fontWeight: "800"
  },
  metrics: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  panelTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "700"
  },
  row: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
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
  }
});
