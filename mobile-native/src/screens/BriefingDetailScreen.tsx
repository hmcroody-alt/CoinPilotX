/**
 * One briefing, in full: the UNDX summary plus the grounded facts it was
 * written from — and *only* the fact sections actually present in the payload.
 * A briefing generated with crypto disabled has no crypto section, so none is
 * rendered; nothing on this screen is ever synthesized client-side.
 *
 * The network section is counts only — the server never puts message bodies
 * in a fact pack, and this screen renders nothing it wasn't given. Crypto is
 * observed market data with an explicit informational-only note: briefings
 * report, they do not advise.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { BriefingDetail, BriefingFacts, getBriefing } from "../api/briefings";
import { trackBriefings } from "../briefings/briefingsAnalytics";
import { Panel } from "../components/Panel";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "BriefingDetail">;

const NETWORK_KEYS = [
  "unread_messages",
  "friend_requests",
  "new_followers",
  "mentions",
  "comments",
  "reactions",
  "marketplace_orders",
  "security_alerts",
  "community_events"
] as const;

function formatWhen(value?: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function formatPrice(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return value.toLocaleString(undefined, {
    maximumFractionDigits: value >= 100 ? 0 : 4
  });
}

function formatPct(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  const rounded = value.toFixed(2);
  return value > 0 ? `+${rounded}%` : `${rounded}%`;
}

/** Network rows worth showing: defined, numeric, and non-zero. */
function networkRows(facts: BriefingFacts): Array<{ key: (typeof NETWORK_KEYS)[number]; value: number }> {
  const network = facts.network;
  if (!network) return [];
  const rows: Array<{ key: (typeof NETWORK_KEYS)[number]; value: number }> = [];
  for (const key of NETWORK_KEYS) {
    const value = network[key];
    if (typeof value === "number" && Number.isFinite(value) && value > 0) rows.push({ key, value });
  }
  return rows;
}

export function BriefingDetailScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const briefingId = Number(route.params?.briefingId || 0);

  const [briefing, setBriefing] = useState<BriefingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      const detail = await getBriefing(briefingId);
      setBriefing(detail);
      if (detail.title) navigation.setOptions({ title: detail.title });
    } catch {
      setFailed(true);
      trackBriefings("briefings_load_failed", { surface: "detail", briefing_id: briefingId });
    } finally {
      setLoading(false);
    }
  }, [briefingId, navigation]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("briefings:hub.loading")}</Text>
      </View>
    );
  }

  if (failed || !briefing) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{t("briefings:detail.loadError")}</Text>
        <Pressable accessibilityRole="button" style={styles.retry} onPress={() => load().catch(() => undefined)}>
          <Text style={styles.retryText}>{t("briefings:hub.retry")}</Text>
        </Pressable>
      </View>
    );
  }

  const facts = briefing.facts || {};
  const network = networkRows(facts);
  const crypto = facts.crypto && facts.crypto.available !== false ? facts.crypto : null;
  const watchlist = crypto?.watchlist?.filter((entry) => entry && entry.symbol) || [];
  const proximity = crypto?.alert_proximity?.filter((entry) => entry && entry.symbol) || [];
  const gainers = crypto?.gainers?.slice(0, 3) || [];
  const losers = crypto?.losers?.slice(0, 3) || [];

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>{briefing.title}</Text>
        <Text style={styles.meta}>{formatWhen(briefing.sent_at || briefing.generated_at || facts.generated_at)}</Text>
      </View>

      <Panel>
        <Text style={styles.body}>{briefing.body}</Text>
      </Panel>

      {network.length ? (
        <>
          <Text style={styles.sectionLabel}>{t("briefings:detail.networkTitle")}</Text>
          <Panel>
            {network.map(({ key, value }) => (
              <View key={key} style={styles.factRow}>
                <Text style={styles.factLabel}>{t(`briefings:network.${key}`)}</Text>
                <Text style={styles.factValue}>{value.toLocaleString()}</Text>
              </View>
            ))}
            <Pressable
              accessibilityRole="button"
              style={styles.linkButton}
              onPress={() => navigation.navigate("ActivityInbox", { title: t("briefings:detail.openActivity") })}
            >
              <Text style={styles.linkButtonText}>{t("briefings:detail.openActivity")}</Text>
            </Pressable>
          </Panel>
        </>
      ) : null}

      {crypto ? (
        <>
          <Text style={styles.sectionLabel}>{t("briefings:detail.cryptoTitle")}</Text>
          <Panel>
            {typeof crypto.btc_price === "number" ? (
              <View style={styles.factRow}>
                <Text style={styles.factLabel}>{t("briefings:crypto.btc")}</Text>
                <Text style={styles.factValue}>
                  {formatPrice(crypto.btc_price)}
                  {typeof crypto.btc_change_24h === "number" ? `  ${formatPct(crypto.btc_change_24h)}` : ""}
                </Text>
              </View>
            ) : null}
            {typeof crypto.eth_price === "number" ? (
              <View style={styles.factRow}>
                <Text style={styles.factLabel}>{t("briefings:crypto.eth")}</Text>
                <Text style={styles.factValue}>
                  {formatPrice(crypto.eth_price)}
                  {typeof crypto.eth_change_24h === "number" ? `  ${formatPct(crypto.eth_change_24h)}` : ""}
                </Text>
              </View>
            ) : null}
            {typeof crypto.market_cap_change_24h_pct === "number" ? (
              <View style={styles.factRow}>
                <Text style={styles.factLabel}>{t("briefings:crypto.marketCap")}</Text>
                <Text style={styles.factValue}>{formatPct(crypto.market_cap_change_24h_pct)}</Text>
              </View>
            ) : null}
            {typeof crypto.btc_dominance === "number" ? (
              <View style={styles.factRow}>
                <Text style={styles.factLabel}>{t("briefings:crypto.dominance")}</Text>
                <Text style={styles.factValue}>{crypto.btc_dominance.toFixed(1)}%</Text>
              </View>
            ) : null}
            {gainers.length ? (
              <View style={styles.factRow}>
                <Text style={styles.factLabel}>{t("briefings:crypto.gainers")}</Text>
                <Text style={styles.factValue}>
                  {gainers.map((g) => `${g.symbol} ${formatPct(g.change_24h)}`).join(" · ")}
                </Text>
              </View>
            ) : null}
            {losers.length ? (
              <View style={styles.factRow}>
                <Text style={styles.factLabel}>{t("briefings:crypto.losers")}</Text>
                <Text style={styles.factValue}>
                  {losers.map((l) => `${l.symbol} ${formatPct(l.change_24h)}`).join(" · ")}
                </Text>
              </View>
            ) : null}
            <Text style={styles.disclaimer}>{t("briefings:detail.disclaimer")}</Text>
          </Panel>
        </>
      ) : null}

      {watchlist.length || proximity.length ? (
        <>
          <Text style={styles.sectionLabel}>{t("briefings:detail.watchlistTitle")}</Text>
          <Panel>
            {watchlist.map((entry) => (
              <View key={`w-${entry.symbol}`} style={styles.factRow}>
                <Text style={styles.factLabel}>{entry.symbol}</Text>
                <Text style={styles.factValue}>
                  {formatPrice(entry.price)}
                  {typeof entry.change_24h === "number" ? `  ${formatPct(entry.change_24h)}` : ""}
                </Text>
              </View>
            ))}
            {proximity.map((entry) => (
              <View key={`p-${entry.symbol}-${entry.threshold}`} style={styles.factRow}>
                <Text style={styles.factLabel}>
                  {t("briefings:detail.alertProximity", { symbol: entry.symbol })}
                </Text>
                <Text style={styles.factValue}>{formatPct(entry.distance_pct)}</Text>
              </View>
            ))}
            <Pressable
              accessibilityRole="button"
              style={styles.linkButton}
              onPress={() => navigation.navigate("Watchlists")}
            >
              <Text style={styles.linkButtonText}>{t("briefings:detail.openWatchlists")}</Text>
            </Pressable>
          </Panel>
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  body: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 23
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    gap: 12,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  content: {
    gap: 12,
    padding: 18,
    paddingBottom: 34
  },
  disclaimer: {
    color: colors.muted,
    fontSize: 12,
    fontStyle: "italic",
    lineHeight: 17,
    marginTop: 6
  },
  error: {
    color: colors.danger,
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center"
  },
  factLabel: {
    color: colors.muted,
    flexShrink: 1,
    fontSize: 14,
    lineHeight: 20
  },
  factRow: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between",
    minHeight: 38,
    paddingVertical: 6
  },
  factValue: {
    color: colors.text,
    fontSize: 14,
    fontVariant: ["tabular-nums"],
    fontWeight: "700",
    textAlign: "right"
  },
  header: {
    gap: 5
  },
  linkButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 8,
    minHeight: 42
  },
  linkButtonText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  meta: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18
  },
  retry: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 42,
    paddingHorizontal: 18
  },
  retryText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  sectionLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.8,
    marginTop: 6,
    textTransform: "uppercase"
  },
  title: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900",
    lineHeight: 30
  }
}));
