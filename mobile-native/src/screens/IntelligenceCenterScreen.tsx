import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, AppState, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import {
  alertConditionLabel,
  getIntelligenceState,
  IntelligenceCard,
  IntelligenceState,
  intelligenceStateLabel,
  listCryptoAlerts,
  loadCachedAlertList,
  loadCachedIntelligenceState,
  openIntelligenceWebFallback,
  PulseAlertRule
} from "../api/intelligence";
import { getNotificationBadgeCounts, NotificationBadgeCounts, unreadCount } from "../api/notifications";
import { getPremiumStatus, premiumStateLabel, PremiumStatus } from "../api/premium";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "IntelligenceCenter">;

export function IntelligenceCenterScreen({ route, navigation }: Props) {
  const { authState } = useAuth();
  // Wrong-subject guard: alerts, premium status and unread badges are all the
  // signed-in viewer's data. On another profile's route params this screen
  // refuses instead of showing the viewer's data under that person's name.
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  const [state, setState] = useState<IntelligenceState | null>(null);
  const [alerts, setAlerts] = useState<PulseAlertRule[]>([]);
  const [premium, setPremium] = useState<PremiumStatus | null>(null);
  const [badges, setBadges] = useState<NotificationBadgeCounts | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const alertId = Number(route.params?.alertId || 0);
  const subsystem = route.params?.subsystem || "";

  const selectedAlert = useMemo(() => alerts.find((alert) => alert.id === alertId) || null, [alertId, alerts]);
  const intelligence = state?.intelligence || {};
  const hub = intelligence.hub || {};
  const cards = intelligence.cards || [];
  const recommendations = hub.recommended_next_actions || [];

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const [nextState, nextAlerts, nextPremium, nextBadges] = await Promise.all([
        getIntelligenceState(),
        listCryptoAlerts().catch(() => ({ alerts: [] })),
        getPremiumStatus().catch(() => null),
        getNotificationBadgeCounts().catch(() => null)
      ]);
      setState(nextState);
      setAlerts(nextAlerts.alerts || []);
      setPremium(nextPremium);
      setBadges(nextBadges);
    } catch (loadError) {
      const [cachedState, cachedAlerts] = await Promise.all([
        loadCachedIntelligenceState(),
        loadCachedAlertList()
      ]);
      if (cachedState || cachedAlerts?.alerts?.length) {
        if (cachedState) setState(cachedState);
        if (cachedAlerts?.alerts) setAlerts(cachedAlerts.alerts);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Intelligence Center could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    // Owner-only fetch: skip entirely on a visitor route (no fetch-then-hide).
    if (!routeContext.isOwnProfile) return;
    load("initial").catch(() => undefined);
  }, [load, routeContext.isOwnProfile]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "active") load("refresh").catch(() => undefined);
    });
    return () => sub.remove();
  }, [load, routeContext.isOwnProfile]);

  // Visitor destination with no visitor variant: refuse rather than render the
  // viewer's intelligence state. All hooks above have already run.
  if (!routeContext.isOwnProfile) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{PRIVATE_CONTENT_MESSAGE}</Text>
      </View>
    );
  }

  if (loading && !state && !alerts.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Intelligence</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Intelligence Center</Text>
        <Text style={styles.subtitle}>{offline ? "Showing saved intelligence state" : "Your alerts, forecasts, and what has been sent to you."}</Text>
      </View>
      <Pressable style={styles.refreshButton} onPress={() => load("refresh").catch(() => undefined)}>
        <Text style={styles.refreshText}>{refreshing ? "Refreshing..." : "Refresh Intelligence"}</Text>
      </Pressable>
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Panel>
        <View style={styles.heroRow}>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>Guidance score</Text>
            <Text style={styles.score}>{hub.overall_intelligence_score || 0}%</Text>
            <Text style={styles.muted}>Premium: {premiumStateLabel(premium)}. Alert evaluation, forecasts, provider routing, and delivery remain backend-controlled.</Text>
          </View>
          <View style={styles.statusPill}>
            <Text style={styles.statusPillText}>{unreadCount(badges || undefined)} unread</Text>
          </View>
        </View>
      </Panel>

      <View style={styles.metricGrid}>
        <Metric label="Signal health" value={`${hub.platform_health || 0}%`} />
        <Metric label="Safety" value={`${hub.safety_score || 0}%`} />
        <Metric label="Threats" value={hub.active_threats || 0} />
        <Metric label="Forecasts" value={`${hub.prediction_confidence || 0}%`} />
        <Metric label="Opportunities" value={hub.new_opportunities || 0} />
        <Metric label="Alerts" value={alerts.length} />
      </View>

      {selectedAlert ? (
        <Panel>
          <Text style={styles.sectionTitle}>Alert detail</Text>
          <AlertRow alert={selectedAlert} selected onOpen={() => navigation.navigate("AlertManagement", { alertId: selectedAlert.id, title: "Alert Detail" })} />
        </Panel>
      ) : null}

      {subsystem ? (
        <Panel>
          <Text style={styles.sectionTitle}>Requested subsystem</Text>
          <Text style={styles.muted}>{subsystem.replace(/[_-]/g, " ")} is managed by PulseSoc Intelligence and will surface here natively once a dedicated payload is available.</Text>
        </Panel>
      ) : null}

      <Panel>
        <Text style={styles.sectionTitle}>Daily brief</Text>
        <Text style={styles.muted}>{hub.personalized_daily_brief || "Your daily brief will appear once there is enough to report."}</Text>
        {recommendations.slice(0, 4).map((item) => <Text key={item} style={styles.recommendation}>{item}</Text>)}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Streams and forecasts</Text>
        {cards.length ? cards.slice(0, 10).map((card) => (
          <IntelligenceCardRow key={`${card.key}-${card.label}`} card={card} onPress={() => openIntelligenceWebFallback(card.route || "/dashboard/intelligence").catch(() => undefined)} />
        )) : <Text style={styles.muted}>No intelligence stream cards returned yet.</Text>}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Alert overview</Text>
        {alerts.length ? alerts.slice(0, 8).map((alert) => (
          <AlertRow
            key={alert.id}
            alert={alert}
            selected={alert.id === alertId}
            onOpen={() => navigation.navigate("AlertManagement", { alertId: alert.id, title: "Alert Detail" })}
          />
        )) : <Text style={styles.muted}>No crypto or market alerts returned by the backend.</Text>}
        <Action label="Manage Alerts" onPress={() => navigation.navigate("AlertManagement", { title: "Alerts" })} />
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Navigation</Text>
        <View style={styles.actionGrid}>
          <Action label="Notifications" onPress={() => navigation.navigate("NotificationCenter")} />
          <Action label="Preferences" onPress={() => navigation.navigate("NotificationPreferences")} />
          <Action label="Growth" onPress={() => navigation.navigate("GrowthCenter")} />
          <Action label="Premium" onPress={() => navigation.navigate("Premium")} />
          <Action label="Creator Studio" onPress={() => navigation.navigate("CreatorStudio")} />
          <Action label="Alert Management" onPress={() => navigation.navigate("AlertManagement", { title: "Alerts" })} />
          <Action label="Search" onPress={() => navigation.navigate("Search", { title: "Search" })} />
          <Action label="Profile" onPress={() => navigation.navigate("ProfileDetail", undefined)} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Advanced tools</Text>
        <Text style={styles.muted}>Advanced editing, provider administration, collector management, intelligence sources, and unsupported alert operations stay server-managed by PulseSoc.</Text>
        <View style={styles.actionGrid}>
          <Action label="Manage Alerts" onPress={() => navigation.navigate("AlertManagement", { title: "Alerts" })} />
          <Action label="Create Alert" onPress={() => navigation.navigate("AlertManagement", { title: "Alerts" })} />
        </View>
      </Panel>
    </ScrollView>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue} numberOfLines={1}>{String(value ?? 0)}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function IntelligenceCardRow({ card, onPress }: { card: IntelligenceCard; onPress: () => void }) {
  return (
    <Pressable style={styles.row} onPress={onPress}>
      <View style={styles.rowHead}>
        <Text style={styles.rowTitle} numberOfLines={1}>{card.label || "Intelligence"}</Text>
        <Text style={styles.pill}>{intelligenceStateLabel(card.state)}</Text>
      </View>
      <Text style={styles.muted}>{card.detail || "Server-owned intelligence stream."}</Text>
      <Text style={styles.rowMeta}>{card.count || 0} signals · {card.confidence || 0}% confidence</Text>
    </Pressable>
  );
}

function AlertRow({ alert, selected, onOpen }: { alert: PulseAlertRule; selected?: boolean; onOpen: () => void }) {
  const channels = Object.entries(alert.channels || {}).filter(([, active]) => Boolean(active)).map(([key]) => key);
  return (
    <Pressable style={[styles.row, selected ? styles.rowSelected : undefined]} onPress={onOpen}>
      <View style={styles.rowHead}>
        <Text style={styles.rowTitle} numberOfLines={1}>{alert.asset_symbol || "MARKET"} alert</Text>
        <Text style={styles.pill}>{alert.status || "active"}</Text>
      </View>
      <Text style={styles.muted}>{alertConditionLabel(alert)}</Text>
      <Text style={styles.rowMeta}>{alert.history_count || 0} events · {channels.length ? channels.join(", ") : "server delivery"}</Text>
      {alert.last_triggered_at ? <Text style={styles.rowMeta}>Last triggered {alert.last_triggered_at}</Text> : null}
    </Pressable>
  );
}

function Action({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.secondaryButton} onPress={onPress}>
      <Text style={styles.secondaryText}>{label}</Text>
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
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
  error: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  header: {
    gap: 5
  },
  heroCopy: {
    flex: 1
  },
  heroRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 12
  },
  metric: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexBasis: "30%",
    flexGrow: 1,
    justifyContent: "center",
    minHeight: 76,
    padding: 12
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  metricValue: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  pill: {
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: 8,
    paddingVertical: 4,
    textTransform: "capitalize"
  },
  recommendation: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 8
  },
  refreshButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 42
  },
  refreshText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  row: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 5,
    paddingBottom: 12,
    paddingTop: 4
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
  rowSelected: {
    backgroundColor: "rgba(37, 208, 167, 0.10)",
    borderRadius: 8,
    padding: 10
  },
  rowTitle: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
    fontWeight: "900",
    textTransform: "capitalize"
  },
  score: {
    color: colors.text,
    fontSize: 38,
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 42,
    paddingHorizontal: 12
  },
  secondaryText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900",
    textAlign: "center"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  statusPill: {
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  statusPillText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "capitalize"
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
