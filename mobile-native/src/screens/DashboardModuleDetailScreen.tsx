import { RouteProp, useNavigation, useRoute } from "@react-navigation/native";
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { dashboardModuleGroups, DashboardModuleGroup, DashboardModuleItem } from "../data/dashboardModules";
import { classifyDashboardActionRoute, openDashboardAccessRoute, openDashboardRoute, DashboardNavigation } from "../navigation/dashboardRouting";
import { RootStackParamList } from "../navigation/types";
import { DashboardLiveStatePanel, loadDashboardModuleLiveState } from "../api/dashboardLiveState";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type DetailRoute = RouteProp<RootStackParamList, "DashboardModuleDetail">;

export function DashboardModuleDetailScreen() {
  const navigation = useNavigation<DashboardNavigation>();
  const route = useRoute<DetailRoute>();
  const group = dashboardModuleGroups.find((candidate) => candidate.key === route.params?.groupKey);
  const module = group?.modules.find((candidate) => candidate.key === route.params?.moduleKey);
  const [liveState, setLiveState] = useState<DashboardLiveStatePanel | null>(null);
  const [liveLoading, setLiveLoading] = useState(true);
  const [liveError, setLiveError] = useState("");

  useEffect(() => {
    let active = true;
    if (!group || !module) return undefined;
    setLiveLoading(true);
    setLiveError("");
    loadDashboardModuleLiveState(group, module)
      .then((panel) => {
        if (!active) return;
        setLiveState(panel);
      })
      .catch((error) => {
        if (!active) return;
        setLiveError(error instanceof Error ? error.message : "Live dashboard state is unavailable.");
      })
      .finally(() => {
        if (active) setLiveLoading(false);
      });
    return () => {
      active = false;
    };
  }, [group, module]);

  if (!group || !module) {
    return (
      <View style={styles.center}>
        <View style={styles.centerPanel}>
          <Text style={styles.centerTitle}>Dashboard module unavailable</Text>
          <Text style={styles.centerText}>This dashboard card could not be matched to the production module map.</Text>
          <Pressable style={styles.primaryButton} onPress={() => navigation.navigate("UserDashboard", { title: "Dashboard" })}>
            <Text style={styles.primaryButtonText}>Return to Dashboard</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const routeClass = classifyDashboardActionRoute(module.route);
  const nativeRoute = routeClass.kind === "native_route" || routeClass.kind === "native_shell_route";
  const related = relatedNativeRoutes(group, module);

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={[styles.hero, accentStyle(module.accent)]}>
        <View style={styles.heroTop}>
          <Text style={styles.moduleGlyph}>{module.icon}</Text>
          <View style={styles.statusCluster}>
            <Text style={styles.statusPill}>{module.status.replace("_", " ")}</Text>
            <Text style={[styles.accessPill, module.access === "locked" ? styles.lockedPill : null]}>{module.access}</Text>
          </View>
        </View>
        <Text style={styles.eyebrow}>{group.title}</Text>
        <Text style={styles.heroTitle}>{module.title}</Text>
        <Text style={styles.heroBody}>{module.description}</Text>
        {module.lockReason ? <Text style={styles.lockReason}>{module.lockReason}</Text> : null}
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Module route parity</Text>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>Production route</Text>
          <Text style={styles.infoValue}>{module.route}</Text>
        </View>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>Where it opens</Text>
          <Text style={styles.infoValue}>{routeClass.label}: {routeClass.detail}</Text>
        </View>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>Managed by</Text>
          <Text style={styles.infoValue}>Permissions, access locks, and account rules for this module are decided by PulseSoc, not by this app.</Text>
        </View>
      </View>

      <LiveStateSection panel={liveState} loading={liveLoading} error={liveError} />

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Available actions</Text>
        <View style={styles.actionGrid}>
          <ActionButton
            label={module.access === "locked" ? "Review Access" : nativeRoute ? routeClass.label === "Native shell" ? "Open Native Shell" : "Open Native Surface" : "Open Native Boundary"}
            body={module.access === "locked" ? "Open the entitlement or owner area tied to this card." : nativeRoute ? routeClass.detail : "Stay in the app for this protected workflow."}
            onPress={() => (module.access === "locked" ? openDashboardAccessRoute(navigation, module) : openDashboardRoute(navigation, module.route))}
          />
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Related places in the app</Text>
        <View style={styles.relatedGrid}>
          {related.map((item) => (
            <Pressable key={`${item.label}-${item.route}`} style={styles.relatedCard} onPress={() => openDashboardRoute(navigation, item.route)}>
              <Text style={styles.relatedTitle}>{item.label}</Text>
              <Text style={styles.relatedBody}>{item.body}</Text>
            </Pressable>
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>What you can do here</Text>
        <Text style={styles.bodyText}>
          You can reach this module and see its current state from the app. Its more advanced workflows are available on pulsesoc.com.
        </Text>
      </View>
    </ScrollView>
  );
}

function LiveStateSection({ panel, loading, error }: { panel: DashboardLiveStatePanel | null; loading: boolean; error: string }) {
  if (loading) {
    return (
      <View style={styles.section}>
        <View style={styles.liveHeader}>
          <Text style={styles.sectionTitle}>Live state</Text>
          <ActivityIndicator color={colors.accent} />
        </View>
        <Text style={styles.bodyText}>Loading the latest state for this module.</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Live state</Text>
        <View style={styles.liveWarning}>
          <Text style={styles.warningTitle}>Live dashboard state unavailable</Text>
          <Text style={styles.warningText}>{error}</Text>
        </View>
      </View>
    );
  }

  if (!panel) {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Live state</Text>
        <Text style={styles.bodyText}>No live state is available for this module yet.</Text>
      </View>
    );
  }

  return (
    <View style={styles.section}>
      <View style={styles.liveHeader}>
        <View>
          <Text style={styles.sectionTitle}>Live state</Text>
          <Text style={styles.liveSource}>{panel.source}</Text>
        </View>
        <Text style={[styles.liveMode, panel.loadedFromCache ? styles.offlineMode : null]}>{panel.loadedFromCache ? "Cached" : "Live"}</Text>
      </View>
      <View style={styles.liveMetricGrid}>
        {panel.metrics.map((metric) => (
          <View key={`${metric.label}-${metric.value}`} style={[styles.liveMetricCard, metricStateStyle(metric.state)]}>
            <Text style={styles.liveMetricLabel}>{metric.label}</Text>
            <Text style={styles.liveMetricValue}>{metric.value}</Text>
            <Text style={styles.liveMetricDetail}>{metric.detail}</Text>
          </View>
        ))}
      </View>
      <View style={styles.signalList}>
        {panel.signals.map((signal) => (
          <Text key={signal} style={styles.signalItem}>{signal}</Text>
        ))}
      </View>
      {panel.warnings.length ? (
        <View style={styles.liveWarning}>
          {panel.warnings.map((warning) => (
            <Text key={warning} style={styles.warningText}>{warning}</Text>
          ))}
        </View>
      ) : null}
      <Text style={styles.liveFreshness}>Last refresh: {panel.loadedAt}</Text>
    </View>
  );
}

function relatedNativeRoutes(group: DashboardModuleGroup, module: DashboardModuleItem) {
  const groupKey = group.key;
  const route = module.route;
  if (groupKey === "economy-earnings") {
    return [
      { label: "Marketplace", route: "/pulse/marketplace", body: "Browse listings, detail pages, media, and seller links." },
      { label: "Seller Store", route: "/pulse/seller-store", body: "Manage seller status, listings, inventory, and payout fallback." },
      { label: "Buyer Orders", route: "/pulse/orders", body: "Review purchases, order state, receipts, and transaction history." }
    ];
  }
  if (groupKey === "creator-studio") {
    return [
      { label: "Creator Studio", route: "/pulse/creator-studio", body: "Open dashboard summary, planner links, and creator entry points." },
      { label: "Content Planner", route: "/dashboard/creator/content-planner", body: "Plan posts, drafts, and scheduled content the app supports." },
      { label: "Camera Studio", route: "/pulse/camera/photo?target=feed", body: "Create media for feed, status, reels, and profile handoffs." }
    ];
  }
  if (groupKey === "intelligence" || groupKey === "pulsesoc-ai") {
    return [
      { label: "Intelligence Center", route: "/dashboard/intelligence", body: "Review streams, forecasts, alerts, sources, and recent events." },
      { label: "Alert Management", route: "/dashboard/crypto/alerts", body: "Create, edit, duplicate, pause, and test crypto/market alerts." },
      { label: "UNDX", route: "/pulse/ai", body: "Open the Digital Intelligence Companion." }
    ];
  }
  if (groupKey === "pulse-radio-media") {
    return [
      { label: "Saved Media", route: "/pulse/saved", body: "Open your saved content and collections." },
      { label: "Reels", route: "/pulse/reels", body: "Watch short-form videos." },
      { label: "Status", route: "/pulse/status", body: "Open the status viewer and status creator." }
    ];
  }
  if (groupKey === "crypto-command-center") {
    return [
      { label: "My Alerts", route, body: "Open the crypto alert route represented by this card." },
      { label: "Intelligence", route: "/dashboard/intelligence", body: "Review crypto, market, and source-backed signals." },
      { label: "Activity", route: "/pulse/activity/intelligence_alerts", body: "Review alert and intelligence activity." }
    ];
  }
  if (groupKey === "ads-sponsorships") {
    return [
      { label: "Growth Center", route: "/pulse/growth", body: "Review promotion, campaign, and audience summaries." },
      { label: "Creator Studio", route: "/pulse/creator-studio", body: "Open creator eligibility, performance, and shortcut surfaces." },
      { label: "Marketplace", route: "/pulse/marketplace", body: "Review commerce surfaces tied to sponsored activity." }
    ];
  }
  if (groupKey === "moderation-safety") {
    return [
      { label: "Safety Hub", route: "/pulse/safety", body: "Review reports, blocks, mutes, and safety state." },
      { label: "Account Health", route: "/dashboard/account/health", body: "Review account standing, appeals, and enforcement history." },
      { label: "Verification", route: "/dashboard/account/verification", body: "Review trust requirements and verification state." }
    ];
  }
  if (groupKey === "system-status") {
    return [
      { label: "Activity Inbox", route: "/pulse/activity", body: "See your current activity and notifications." },
      { label: "Intelligence", route: "/dashboard/intelligence", body: "Review system, safety, market, and event intelligence." },
      { label: "Settings", route: "/dashboard/account/settings", body: "Open account and notification controls." }
    ];
  }
  return [
    { label: group.label, route: module.route, body: "Open the production route mapped to this dashboard card." },
    { label: "Dashboard", route: "/pulse/dashboard", body: "Return to your User Dashboard." }
  ];
}

function ActionButton({ label, body, onPress, variant = "primary" }: { label: string; body: string; onPress: () => void; variant?: "primary" | "secondary" }) {
  return (
    <Pressable style={[styles.actionButton, variant === "secondary" ? styles.secondaryButton : null]} onPress={onPress}>
      <Text style={styles.actionLabel}>{label}</Text>
      <Text style={styles.actionBody}>{body}</Text>
    </Pressable>
  );
}

function accentStyle(accent: string) {
  if (accent === "gold") return styles.goldAccent;
  if (accent === "purple") return styles.purpleAccent;
  if (accent === "red") return styles.redAccent;
  if (accent === "emerald") return styles.emeraldAccent;
  return null;
}

function metricStateStyle(state: "ready" | "attention" | "fallback" | "offline") {
  if (state === "attention") return styles.attentionMetric;
  if (state === "fallback") return styles.fallbackMetric;
  if (state === "offline") return styles.offlineMetric;
  return styles.readyMetric;
}

const styles = createThemedStyles(() => ({
  accessPill: {
    backgroundColor: "rgba(79,140,255,0.13)",
    borderColor: "rgba(79,140,255,0.42)",
    borderRadius: 999,
    borderWidth: 1,
    color: colors.text,
    fontSize: 11,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 5,
    textTransform: "uppercase"
  },
  actionBody: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 6
  },
  actionButton: {
    backgroundColor: "rgba(37,208,167,0.12)",
    borderColor: "rgba(37,208,167,0.5)",
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minWidth: 210,
    padding: 14
  },
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  actionLabel: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  bodyText: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerPanel: {
    alignItems: "center",
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: 14,
    borderWidth: 1,
    padding: 20
  },
  centerText: {
    color: colors.muted,
    fontSize: 14,
    marginBottom: 16,
    marginTop: 8,
    textAlign: "center"
  },
  centerTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900",
    textAlign: "center"
  },
  content: {
    gap: 14,
    padding: 16,
    paddingBottom: 34
  },
  emeraldAccent: {
    borderColor: "rgba(37,208,167,0.5)"
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  goldAccent: {
    borderColor: "rgba(243,185,78,0.58)"
  },
  hero: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 12,
    padding: 18
  },
  heroBody: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22
  },
  heroTitle: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  heroTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  infoLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  infoRow: {
    backgroundColor: "rgba(255,255,255,0.03)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 6,
    padding: 12
  },
  infoValue: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 20
  },
  lockedPill: {
    borderColor: "rgba(243,185,78,0.5)",
    color: "#f3d58a"
  },
  lockReason: {
    color: "#f3d58a",
    fontSize: 13,
    fontWeight: "900"
  },
  attentionMetric: {
    borderColor: "rgba(243,185,78,0.54)"
  },
  fallbackMetric: {
    borderColor: "rgba(164,92,255,0.42)"
  },
  liveFreshness: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800"
  },
  liveHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between"
  },
  liveMetricCard: {
    backgroundColor: "rgba(255,255,255,0.03)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minWidth: 190,
    padding: 12
  },
  liveMetricDetail: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 8
  },
  liveMetricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  liveMetricLabel: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  liveMetricValue: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900",
    marginTop: 6
  },
  liveMode: {
    backgroundColor: "rgba(37,208,167,0.13)",
    borderColor: "rgba(37,208,167,0.42)",
    borderRadius: 999,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 5,
    textTransform: "uppercase"
  },
  liveSource: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 4
  },
  liveWarning: {
    backgroundColor: "rgba(243,185,78,0.08)",
    borderColor: "rgba(243,185,78,0.28)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 6,
    padding: 12
  },
  moduleGlyph: {
    backgroundColor: "rgba(79,140,255,0.12)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 18,
    fontWeight: "900",
    minWidth: 52,
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 10,
    textAlign: "center"
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: 18,
    paddingVertical: 12
  },
  primaryButtonText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
  },
  offlineMetric: {
    borderColor: "rgba(114,128,150,0.48)"
  },
  offlineMode: {
    borderColor: "rgba(114,128,150,0.48)",
    color: colors.muted
  },
  purpleAccent: {
    borderColor: "rgba(164,92,255,0.58)"
  },
  redAccent: {
    borderColor: "rgba(255,93,124,0.58)"
  },
  readyMetric: {
    borderColor: "rgba(37,208,167,0.46)"
  },
  relatedBody: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 6
  },
  relatedCard: {
    backgroundColor: "rgba(255,255,255,0.03)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minWidth: 180,
    padding: 12
  },
  relatedGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  relatedTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  secondaryButton: {
    backgroundColor: "rgba(255,255,255,0.03)",
    borderColor: colors.border
  },
  section: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 12,
    padding: 14
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  signalItem: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 19
  },
  signalList: {
    backgroundColor: "rgba(255,255,255,0.025)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 6,
    padding: 12
  },
  statusCluster: {
    alignItems: "flex-end",
    gap: 6
  },
  statusPill: {
    backgroundColor: "rgba(37,208,167,0.13)",
    borderColor: "rgba(37,208,167,0.42)",
    borderRadius: 999,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 5
  },
  warningText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  warningTitle: {
    color: "#f3d58a",
    fontSize: 13,
    fontWeight: "900"
  }
}));
