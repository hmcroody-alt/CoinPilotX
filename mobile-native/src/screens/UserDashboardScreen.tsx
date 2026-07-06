import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Animated, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { DashboardCard, DashboardModuleKey, loadUserDashboardState, UserDashboardState } from "../api/dashboard";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type DashboardNavigation = NativeStackNavigationProp<RootStackParamList>;

export function UserDashboardScreen() {
  const navigation = useNavigation<DashboardNavigation>();
  const [state, setState] = useState<UserDashboardState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, []);

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1800, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 1800, useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  async function load(mode: "initial" | "refresh") {
    setError("");
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      setState(await loadUserDashboardState());
    } catch (err) {
      setError(err instanceof Error ? err.message : "PulseSoc dashboard is unavailable.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  const topCards = useMemo(() => (state?.cards || []).slice(0, 4), [state?.cards]);
  const dashboardCards = useMemo(() => (state?.cards || []).slice(4), [state?.cards]);

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Opening PulseSoc Mission Control</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
    >
      <View style={styles.hero}>
        <Animated.View
          pointerEvents="none"
          style={[
            styles.energyRing,
            {
              opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.3, 0.76] }),
              transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.96, 1.04] }) }]
            }
          ]}
        />
        <Text style={styles.eyebrow}>PULSESOC COMMAND LAYER</Text>
        <Text style={styles.heroTitle}>User Dashboard</Text>
        <Text style={styles.heroText}>
          {state?.profile?.display_name || state?.user?.display_name || "Your PulseSoc systems"} are synchronized across social, commerce, trust, creator, and intelligence surfaces.
        </Text>
        <View style={styles.heroMeta}>
          <Signal label="Activity" value={`${state?.activity?.unreadTotal || 0} unread`} tone={(state?.activity?.unreadTotal || 0) > 0 ? "attention" : "ready"} />
          <Signal label="Orders" value={`${state?.buyerOrders.length || 0}`} tone="ready" />
          <Signal label="System" value={state?.warnings.length ? "Partial" : "Stable"} tone={state?.warnings.length ? "attention" : "ready"} />
        </View>
      </View>

      {error ? (
        <View style={styles.warningPanel}>
          <Text style={styles.warningTitle}>Dashboard refresh failed</Text>
          <Text style={styles.warningText}>{error}</Text>
        </View>
      ) : null}

      {state?.warnings.length ? (
        <View style={styles.warningPanel}>
          <Text style={styles.warningTitle}>Some modules are using safe fallback data</Text>
          {state.warnings.map((warning) => (
            <Text key={warning} style={styles.warningText}>{warning}</Text>
          ))}
        </View>
      ) : null}

      <Section title="At A Glance" subtitle="The highest-signal account systems for this session.">
        <View style={styles.metricGrid}>
          {topCards.map((card) => (
            <DashboardMetric key={card.key} card={card} onPress={() => openModule(navigation, card.key)} />
          ))}
        </View>
      </Section>

      <Section title="Quick Actions" subtitle="Jump directly into the native module that owns the workflow.">
        <View style={styles.actionGrid}>
          {(state?.quickActions || []).map((card) => (
            <QuickAction key={`quick-${card.key}`} card={card} onPress={() => openModule(navigation, card.key)} />
          ))}
        </View>
      </Section>

      <Section title="Dashboard Systems" subtitle="Native cards reuse existing PulseSoc APIs, database rules, permissions, and provider boundaries.">
        <View style={styles.cardGrid}>
          {dashboardCards.map((card) => (
            <SystemCard key={card.key} card={card} onPress={() => openModule(navigation, card.key)} />
          ))}
        </View>
      </Section>

      <Section title="Recent Activity" subtitle="Server-authoritative activity and commerce events feed this timeline.">
        {state?.recentActivity.length ? (
          state.recentActivity.map((item) => (
            <Pressable key={item.id} style={styles.timelineRow} onPress={() => openActivityTarget(navigation, item.target)}>
              <View style={styles.timelineDot} />
              <View style={styles.timelineCopy}>
                <Text style={styles.timelineTitle}>{item.title}</Text>
                <Text style={styles.timelineBody}>{item.body}</Text>
              </View>
            </Pressable>
          ))
        ) : (
          <View style={styles.emptyTimeline}>
            <Text style={styles.emptyTitle}>No recent activity yet</Text>
            <Text style={styles.emptyText}>Messages, calls, orders, trust updates, alerts, and marketplace signals will appear here as the backend emits them.</Text>
          </View>
        )}
      </Section>
    </ScrollView>
  );
}

function Section({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <View style={styles.section}>
      <View>
        <Text style={styles.sectionTitle}>{title}</Text>
        <Text style={styles.sectionSubtitle}>{subtitle}</Text>
      </View>
      {children}
    </View>
  );
}

function Signal({ label, value, tone }: { label: string; value: string; tone: DashboardCard["state"] }) {
  return (
    <View style={[styles.signal, tone === "attention" ? styles.signalAttention : null]}>
      <Text style={styles.signalLabel}>{label}</Text>
      <Text style={styles.signalValue}>{value}</Text>
    </View>
  );
}

function DashboardMetric({ card, onPress }: { card: DashboardCard; onPress: () => void }) {
  return (
    <Pressable style={[styles.metricCard, stateStyle(card.state)]} onPress={onPress}>
      <Text style={styles.cardTitle}>{card.title}</Text>
      <Text style={styles.metricValue}>{card.value}</Text>
      <Text style={styles.cardDetail}>{card.detail}</Text>
    </Pressable>
  );
}

function QuickAction({ card, onPress }: { card: DashboardCard; onPress: () => void }) {
  return (
    <Pressable style={styles.quickAction} onPress={onPress}>
      <Text style={styles.quickTitle}>{card.title}</Text>
      <Text style={styles.quickDetail}>{quickActionCopy(card.key)}</Text>
    </Pressable>
  );
}

function SystemCard({ card, onPress }: { card: DashboardCard; onPress: () => void }) {
  return (
    <Pressable style={[styles.systemCard, stateStyle(card.state)]} onPress={onPress}>
      <View style={styles.systemHeader}>
        <Text style={styles.cardTitle}>{card.title}</Text>
        <Text style={styles.statePill}>{stateLabel(card.state)}</Text>
      </View>
      <Text style={styles.systemValue}>{card.value}</Text>
      <Text style={styles.cardDetail}>{card.detail}</Text>
    </Pressable>
  );
}

function openModule(navigation: DashboardNavigation, key: DashboardModuleKey) {
  switch (key) {
    case "home":
      navigation.navigate("Tabs", { screen: "Home" });
      break;
    case "activity":
      navigation.navigate("ActivityInbox", { title: "Activity Inbox" });
      break;
    case "messenger":
      navigation.navigate("Tabs", { screen: "Messenger" });
      break;
    case "calls":
      navigation.navigate("Call", { title: "Calls" });
      break;
    case "profile":
      navigation.navigate("Tabs", { screen: "Profile" });
      break;
    case "reels":
      navigation.navigate("Reels", { title: "Reels" });
      break;
    case "status":
      navigation.navigate("Tabs", { screen: "Status" });
      break;
    case "marketplace":
      navigation.navigate("Tabs", { screen: "Marketplace" });
      break;
    case "seller":
      navigation.navigate("SellerStore", { title: "Seller / Store" });
      break;
    case "orders":
      navigation.navigate("BuyerOrders", { title: "Purchase History" });
      break;
    case "premium":
      navigation.navigate("Premium");
      break;
    case "verification":
      navigation.navigate("VerificationCenter", { title: "Verification Center" });
      break;
    case "security":
      navigation.navigate("AccountCenter", { section: "security", title: "Security Center" });
      break;
    case "trust":
      navigation.navigate("SafetyHub", { title: "Safety Hub" });
      break;
    case "creator":
      navigation.navigate("CreatorStudio");
      break;
    case "growth":
      navigation.navigate("GrowthCenter", { title: "Growth Center" });
      break;
    case "intelligence":
      navigation.navigate("IntelligenceCenter", { title: "Intelligence" });
      break;
    case "camera":
      navigation.navigate("CameraStudio", { target: "feed", mode: "photo", title: "Camera Studio" });
      break;
  }
}

function openActivityTarget(navigation: DashboardNavigation, target?: string) {
  if (!target) {
    navigation.navigate("ActivityInbox", { title: "Activity Inbox" });
    return;
  }
  if (target.startsWith("/pulse/orders/")) {
    const orderId = Number(target.split("/").pop() || 0);
    if (orderId) navigation.navigate("BuyerOrderDetail", { orderId, title: "Order Detail" });
    return;
  }
  if (target.startsWith("/pulse/orders")) {
    navigation.navigate("BuyerOrders", { title: "Purchase History" });
    return;
  }
  if (target.startsWith("/pulse/messages/")) {
    const conversationId = Number(target.split("/").pop() || 0);
    if (conversationId) navigation.navigate("Chat", { conversationId, title: "Chat" });
    return;
  }
  navigation.navigate("ActivityInbox", { title: "Activity Inbox" });
}

function quickActionCopy(key: DashboardModuleKey) {
  if (key === "camera") return "Capture";
  if (key === "activity") return "Review";
  if (key === "messenger") return "Reply";
  if (key === "seller") return "Manage";
  if (key === "creator") return "Create";
  if (key === "intelligence") return "Scan";
  return "Open";
}

function stateLabel(state: DashboardCard["state"]) {
  if (state === "attention") return "Watch";
  if (state === "fallback") return "Linked";
  if (state === "offline") return "Cached";
  return "Ready";
}

function stateStyle(state: DashboardCard["state"]) {
  if (state === "attention") return styles.attentionCard;
  if (state === "fallback") return styles.fallbackCard;
  if (state === "offline") return styles.offlineCard;
  return null;
}

const styles = StyleSheet.create({
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  attentionCard: {
    borderColor: "rgba(243,185,78,0.56)"
  },
  cardDetail: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  cardGrid: {
    gap: 12
  },
  cardTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900",
    textTransform: "uppercase"
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
    marginTop: 12
  },
  content: {
    gap: 16,
    padding: 16,
    paddingBottom: 36
  },
  emptyText: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 6
  },
  emptyTimeline: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 16
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  energyRing: {
    backgroundColor: "rgba(37,208,167,0.12)",
    borderColor: "rgba(79,140,255,0.32)",
    borderRadius: 8,
    borderWidth: 1,
    bottom: 10,
    left: 10,
    position: "absolute",
    right: 10,
    top: 10
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0
  },
  fallbackCard: {
    borderColor: "rgba(79,140,255,0.48)"
  },
  hero: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 14,
    overflow: "hidden",
    padding: 18
  },
  heroMeta: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  heroText: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22
  },
  heroTitle: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "900"
  },
  metricCard: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexGrow: 1,
    flexBasis: "47%",
    gap: 8,
    minHeight: 138,
    padding: 14
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12
  },
  metricValue: {
    color: colors.text,
    fontSize: 23,
    fontWeight: "900"
  },
  offlineCard: {
    opacity: 0.82
  },
  quickAction: {
    backgroundColor: "rgba(37,208,167,0.12)",
    borderColor: "rgba(37,208,167,0.28)",
    borderRadius: 8,
    borderWidth: 1,
    minWidth: 104,
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  quickDetail: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    marginTop: 4
  },
  quickTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  section: {
    gap: 12
  },
  sectionSubtitle: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 3
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "900"
  },
  signal: {
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  signalAttention: {
    borderColor: "rgba(243,185,78,0.54)"
  },
  signalLabel: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800"
  },
  signalValue: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900",
    marginTop: 2
  },
  statePill: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900"
  },
  systemCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    padding: 14
  },
  systemHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 10
  },
  systemValue: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  timelineBody: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18,
    marginTop: 3
  },
  timelineCopy: {
    flex: 1
  },
  timelineDot: {
    backgroundColor: colors.accent,
    borderRadius: 5,
    height: 10,
    marginTop: 5,
    width: 10
  },
  timelineRow: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    padding: 14
  },
  timelineTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  warningPanel: {
    backgroundColor: "rgba(243,185,78,0.1)",
    borderColor: "rgba(243,185,78,0.42)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 6,
    padding: 14
  },
  warningText: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  warningTitle: {
    color: colors.warning,
    fontSize: 14,
    fontWeight: "900"
  }
});
