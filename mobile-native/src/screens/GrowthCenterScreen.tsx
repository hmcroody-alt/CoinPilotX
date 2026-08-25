import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, AppState, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { getGrowthState, growthMoney, GrowthState, loadCachedGrowthState } from "../api/growth";
import { getPremiumStatus, premiumStateLabel, PremiumStatus } from "../api/premium";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "GrowthCenter">;

export function GrowthCenterScreen({ route, navigation }: Props) {
  const { authState } = useAuth();
  // Wrong-subject guard: growth state, wallet and premium status belong to the
  // signed-in viewer. On another profile's route params this screen refuses
  // instead of rendering the viewer's data under that person's name.
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  const [state, setState] = useState<GrowthState | null>(null);
  const [premium, setPremium] = useState<PremiumStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const context = route.params || {};

  const recommendations = useMemo(() => {
    const portal = state?.portal?.recommendations || [];
    if (portal.length) return portal.slice(0, 4);
    const modules = state?.growth?.modules || [];
    return modules.slice(0, 4).map((item) => `Review ${item.replace(/[_-]/g, " ")} readiness.`);
  }, [state]);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const [nextState, nextPremium] = await Promise.all([
        getGrowthState(),
        getPremiumStatus().catch(() => null)
      ]);
      setState(nextState);
      setPremium(nextPremium);
    } catch (loadError) {
      const cached = await loadCachedGrowthState();
      if (cached) {
        setState(cached);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Growth Center could not load.");
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
  // viewer's growth state. All hooks above have already run.
  if (!routeContext.isOwnProfile) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{PRIVATE_CONTENT_MESSAGE}</Text>
      </View>
    );
  }

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Growth Center</Text>
      </View>
    );
  }

  const growth = state?.growth || {};
  const wallet = growth.wallet || {};
  const account = growth.account || {};
  const analytics = growth.analytics || {};
  const audience = growth.audience_categories || [];
  const modules = growth.modules || [];
  const promotedContext = context.contentType && context.contentId ? `${context.contentType} #${context.contentId}` : context.contentType || "";

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Growth Center</Text>
        <Text style={styles.subtitle}>{offline ? "Showing saved growth state" : state?.hero?.body || "Your growth, at a glance"}</Text>
      </View>
      <Pressable style={styles.refreshButton} onPress={() => load("refresh").catch(() => undefined)}>
        <Text style={styles.refreshText}>{refreshing ? "Refreshing..." : "Refresh Growth Center"}</Text>
      </Pressable>
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Panel>
        <View style={styles.heroRow}>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>Growth score</Text>
            <Text style={styles.score}>{growth.growth_score || "Learning"}</Text>
            <Text style={styles.muted}>Premium: {premiumStateLabel(premium)}. PulseSoc decides growth eligibility, promotion readiness, targeting, and billing.</Text>
          </View>
          <View style={styles.statusPill}>
            <Text style={styles.statusPillText}>{account.status || "ready"}</Text>
          </View>
        </View>
      </Panel>

      <View style={styles.metricGrid}>
        <Metric label="Wallet" value={growthMoney(wallet.credits_cents, wallet.currency)} />
        <Metric label="Lifecycle" value={account.lifecycle_stage || "provisioned"} />
        <Metric label="Analytics" value={analytics.status || "ready"} />
        <Metric label="Audience" value={audience.length} />
        <Metric label="Modules" value={modules.length} />
        <Metric label="Promotion" value={promotedContext || "Select content"} />
      </View>

      <Panel>
        <Text style={styles.sectionTitle}>Promote content</Text>
        <Text style={styles.muted}>Promotion starts from real owner content and uses existing promotion policy, billing, targeting, and review rules.</Text>
        <View style={styles.actionGrid}>
          <Action label="Feed" onPress={() => navigation.navigate("Tabs", { screen: "Home" })} />
          <Action label="Reels" onPress={() => navigation.navigate("Reels")} />
          <Action label="Marketplace" onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })} />
          <Action label="Creator Studio" onPress={() => navigation.navigate("CreatorStudio")} />
          <Action label="Intelligence" onPress={() => navigation.navigate("IntelligenceCenter")} />
          <Action label="Profile" onPress={() => navigation.navigate("ProfileDetail", undefined)} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Audience preview</Text>
        {audience.length ? (
          <View style={styles.chips}>
            {audience.slice(0, 12).map((item) => <Text key={item} style={styles.chip}>{item.replace(/[_-]/g, " ")}</Text>)}
          </View>
        ) : (
          <Text style={styles.muted}>Audience categories appear once PulseSoc has enough data on who you are reaching.</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Campaign overview</Text>
        {modules.length ? modules.slice(0, 8).map((item) => (
          <View key={item} style={styles.row}>
            <Text style={styles.rowTitle}>{item.replace(/[_-]/g, " ")}</Text>
            <Text style={styles.muted}>Managed for you by PulseSoc Growth.</Text>
          </View>
        )) : <Text style={styles.muted}>No growth modules returned yet.</Text>}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Analytics snapshot</Text>
        <ContentRow label="Analytics container" value={analytics.container_public_id || "private"} />
        <ContentRow label="Conversion tracking" value={analytics.conversion_tracking_id || "not exposed"} />
        <ContentRow label="Wallet status" value={wallet.status || "inactive"} />
        <ContentRow label="Workspace" value={String(account.default_workspace_id || "pending")} />
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Recommendations</Text>
        {recommendations.length ? recommendations.map((item) => <Text key={item} style={styles.recommendation}>{item}</Text>) : <Text style={styles.muted}>Growth recommendations appear once your account has a little more activity to work from.</Text>}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Advanced tools</Text>
        <Text style={styles.muted}>Campaign launch, wallet funding, billing, targeting, and ad review are managed by PulseSoc. Your campaigns and wallet activity stay in sync automatically.</Text>
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

function Action({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.secondaryButton} onPress={onPress}>
      <Text style={styles.secondaryText}>{label}</Text>
    </Pressable>
  );
}

function ContentRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowTitle}>{label}</Text>
      <Text style={styles.muted}>{value}</Text>
    </View>
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
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  chip: {
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    paddingHorizontal: 9,
    paddingVertical: 6,
    textTransform: "capitalize"
  },
  chips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
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
    minHeight: 76,
    justifyContent: "center",
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
  recommendation: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21
  },
  refreshButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 42,
    justifyContent: "center"
  },
  refreshText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  row: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
    paddingBottom: 10
  },
  rowTitle: {
    color: colors.text,
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
    minHeight: 42,
    justifyContent: "center",
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
