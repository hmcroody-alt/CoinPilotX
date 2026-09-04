/**
 * Alert trigger history — paged, optionally filtered to one rule.
 *
 * Paging is offset-based because that is what the endpoint speaks
 * (`?limit&offset` with `has_more`). "Load more" appends; a pull-to-refresh
 * restarts from offset 0 so a rule that fired while the screen was open shows
 * up at the top rather than being stranded between pages.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { PremiumAlertTrigger, getPremiumAlertHistory } from "../api/cryptoPremium";
import { reconcilePremiumRequired } from "../entitlements/reconcile";
import { Panel } from "../components/Panel";
import { PremiumUpsellPanel } from "../components/crypto/PremiumUpsellPanel";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "CryptoAlertHistory">;

const PAGE_SIZE = 30;

export function CryptoAlertHistoryScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const alertId = Number(route.params?.alertId || 0) || undefined;

  const [items, setItems] = useState<PremiumAlertTrigger[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [gated, setGated] = useState(false);
  const [error, setError] = useState("");

  const loadPage = useCallback(
    async (offset: number, mode: "initial" | "refresh" | "more") => {
      if (mode === "refresh") setRefreshing(true);
      if (mode === "more") setLoadingMore(true);
      setError("");
      try {
        const response = await getPremiumAlertHistory({ limit: PAGE_SIZE, offset, alertId });
        setItems((current) => (mode === "more" ? [...current, ...response.items] : response.items));
        setHasMore(response.has_more);
        setGated(false);
      } catch (loadError) {
        if (reconcilePremiumRequired(loadError)) {
          setGated(true);
        } else {
          setError(loadError instanceof Error ? loadError.message : t("discovery:crypto.history.loadError"));
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
        setLoadingMore(false);
      }
    },
    [alertId, t]
  );

  useEffect(() => {
    loadPage(0, "initial").catch(() => undefined);
  }, [loadPage]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("discovery:crypto.common.loading")}</Text>
      </View>
    );
  }

  if (gated) {
    return (
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        <PremiumUpsellPanel
          body={t("discovery:crypto.upsell.alertsBody")}
          onUpgrade={() => navigation.navigate("Premium")}
        />
      </ScrollView>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => loadPage(0, "refresh").catch(() => undefined)}
          tintColor={colors.accent}
        />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("discovery:crypto.history.title")}</Text>
        <Text style={styles.subtitle}>{t("discovery:crypto.history.subtitle")}</Text>
      </View>

      {alertId ? (
        <View style={styles.filterRow}>
          <Text style={styles.muted}>{t("discovery:crypto.history.filtered")}</Text>
          <Pressable
            accessibilityRole="button"
            style={styles.linkButton}
            onPress={() => navigation.setParams({ alertId: undefined })}
          >
            <Text style={styles.linkButtonText}>{t("discovery:crypto.history.showAll")}</Text>
          </Pressable>
        </View>
      ) : null}

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Panel>
        {items.length ? (
          items.map((item, index) => (
            <View key={`${item.alert_id}-${item.triggered_at}-${index}`} style={styles.eventRow}>
              <View style={styles.rowHead}>
                <Text style={styles.rowTitle}>{item.symbol}</Text>
                <Text style={styles.rowMeta}>{item.triggered_at}</Text>
              </View>
              <Text style={styles.muted}>{item.condition_summary}</Text>
              <Text style={styles.rowMeta}>
                {item.observed_value !== null
                  ? t("discovery:crypto.history.observed", { value: String(item.observed_value) })
                  : null}
                {item.observed_value !== null && item.notification_result ? " · " : null}
                {item.notification_result
                  ? t("discovery:crypto.history.delivery", { result: item.notification_result })
                  : null}
              </Text>
            </View>
          ))
        ) : (
          <Text style={styles.muted}>{t("discovery:crypto.history.empty")}</Text>
        )}
        {hasMore ? (
          <Pressable
            accessibilityRole="button"
            disabled={loadingMore}
            style={[styles.loadMore, loadingMore ? styles.disabled : undefined]}
            onPress={() => loadPage(items.length, "more").catch(() => undefined)}
          >
            <Text style={styles.loadMoreText}>
              {loadingMore ? t("discovery:crypto.common.loading") : t("discovery:crypto.history.loadMore")}
            </Text>
          </Pressable>
        ) : null}
      </Panel>
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  content: {
    gap: 14,
    padding: 18,
    paddingBottom: 34
  },
  disabled: {
    opacity: 0.55
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  eventRow: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
    paddingBottom: 10
  },
  filterRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between"
  },
  header: {
    gap: 5
  },
  linkButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 10
  },
  linkButtonText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  loadMore: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 42,
    justifyContent: "center"
  },
  loadMoreText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  rowHead: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  rowMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  rowTitle: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  }
}));
