import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, AppState, Pressable, StyleSheet, Text, View } from "react-native";
import {
  getPremiumStatus,
  loadCachedPremiumStatus,
  openPremiumBillingPortal,
  openPremiumHub,
  premiumPlanLabel,
  PremiumStatus,
  premiumStateLabel,
} from "../api/premium";
import { purchasePremium, PremiumPlan } from "../payments/appleIapPremium";
import { DIGITAL_COMMERCE_ENABLED } from "../api/config";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "Premium">>;

export function PremiumScreen({ navigation }: Props) {
  const [status, setStatus] = useState<PremiumStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState<"monthly" | "annual" | "billing" | "web" | "">("");

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      setStatus(await getPremiumStatus());
    } catch (loadError) {
      const cached = await loadCachedPremiumStatus();
      if (cached) {
        setStatus(cached);
        setOffline(true);
        setError(loadError instanceof Error ? loadError.message : "Premium status could not refresh.");
      } else {
        setError(loadError instanceof Error ? loadError.message : "Premium status could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") load("refresh").catch(() => undefined);
    });
    return () => sub.remove();
  }, [load]);

  async function runAction(action: "monthly" | "annual" | "billing" | "web") {
    setBusyAction(action);
    try {
      if (action === "monthly" || action === "annual") {
        const result = await purchasePremium(action as PremiumPlan);
        Alert.alert(
          result.status === "verified" ? "Premium active" : "Premium purchase",
          result.status === "verified"
            ? "Your App Store purchase was verified by PulseSoc."
            : result.status === "cancelled"
              ? "You closed the App Store purchase sheet."
              : "The purchase was not granted. PulseSoc will only activate Premium after server verification."
        );
      } else if (action === "billing") {
        const result = await openPremiumBillingPortal();
        Alert.alert("Billing", result.message || "Opening existing PulseSoc billing portal.");
      } else {
        await openPremiumHub();
      }
    } catch (actionError) {
      Alert.alert("Premium action unavailable", actionError instanceof Error ? actionError.message : "PulseSoc could not open this Premium action.");
    } finally {
      setBusyAction("");
      load("refresh").catch(() => undefined);
    }
  }

  if (loading && !status) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Premium status</Text>
      </View>
    );
  }

  return (
    <Screen title="Premium" subtitle="Your Premium, Founder, and billing status.">
      <RefreshControlShim refreshing={refreshing} onRefresh={() => load("refresh").catch(() => undefined)} />
      {offline ? <Text style={styles.warning}>Showing cached Premium status. Pull to refresh when online.</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Panel>
        <View style={styles.headingRow}>
          <View style={styles.headingCopy}>
            <Text style={styles.eyebrow}>{premiumStateLabel(status)}</Text>
            <Text style={styles.plan}>{premiumPlanLabel(status)}</Text>
            <Text style={styles.body}>{status?.message || "PulseSoc confirms your Premium status."}</Text>
          </View>
          <View style={[styles.statusDot, status?.premium_active ? styles.statusDotActive : undefined]} />
        </View>
        <View style={styles.badges}>
          <Badge label={status?.premium_active ? "Premium" : "Free"} active={Boolean(status?.premium_active)} />
          <Badge label={status?.founder_active ? `Founder #${status.founder_number || ""}`.trim() : "Founder locked"} active={Boolean(status?.founder_active)} />
          <Badge label={status?.subscription_status || "inactive"} active={Boolean(status?.premium_active)} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.title}>Plan details</Text>
        <Meta label="Plan" value={status?.plan || "free"} />
        <Meta label="Subscription" value={status?.subscription_status || "inactive"} />
        <Meta label="Provider" value={status?.provider_status || "not connected"} />
        <Meta label="Renews / ends" value={status?.current_period_end || "not available"} />
        {status?.cancel_at_period_end ? <Text style={styles.warning}>Cancellation is scheduled at period end.</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.title}>Entitlements</Text>
        {status?.entitlements?.length ? (
          status.entitlements.map((item) => <EntitlementRow key={item.key} label={item.label} status={item.status} detail={item.detail} />)
        ) : (
          <Text style={styles.muted}>No entitlement details were returned by the server.</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.title}>Actions</Text>
        {DIGITAL_COMMERCE_ENABLED ? (
          <>
            <Text style={styles.muted}>Premium subscriptions use the App Store. PulseSoc activates access only after server verification.</Text>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyAction) }} style={styles.button} disabled={Boolean(busyAction)} onPress={() => runAction("monthly")}>
              <Text style={styles.buttonText}>{busyAction === "monthly" ? "Opening App Store..." : "Monthly · $9.99"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyAction) }} style={styles.button} disabled={Boolean(busyAction)} onPress={() => runAction("annual")}>
              <Text style={styles.buttonText}>{busyAction === "annual" ? "Opening App Store..." : "Annual · $99.99"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyAction) }} style={styles.secondaryButton} disabled={Boolean(busyAction)} onPress={() => runAction("billing")}>
              <Text style={styles.secondaryText}>{busyAction === "billing" ? "Opening..." : "Manage Billing"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyAction) }} style={styles.secondaryButton} disabled={Boolean(busyAction)} onPress={() => runAction("web")}>
              <Text style={styles.secondaryText}>{busyAction === "web" ? "Opening..." : "Open Premium Web Hub"}</Text>
            </Pressable>
          </>
        ) : (
          <Text style={styles.muted}>Your Premium and Founder access is confirmed by PulseSoc and shown above. Native never grants or sells Premium access.</Text>
        )}
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyAction) }} style={styles.secondaryButton} disabled={Boolean(busyAction)} onPress={() => navigation?.navigate("IntelligenceCenter")}>
          <Text style={styles.secondaryText}>Open Intelligence</Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyAction) }} style={styles.secondaryButton} disabled={Boolean(busyAction)} onPress={() => navigation?.navigate("VerificationCenter", { title: "Verification Center" })}>
          <Text style={styles.secondaryText}>Open Verification Center</Text>
        </Pressable>
      </Panel>

      <Panel>
        <Text style={styles.title}>Profile hooks</Text>
        <Text style={styles.muted}>Premium badge and profile theme rendering reuse the existing Profile APIs. Theme selection remains enforced by the backend.</Text>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation?.navigate("ProfileDetail", undefined)}>
          <Text style={styles.secondaryText}>Open Profile</Text>
        </Pressable>
      </Panel>
    </Screen>
  );
}

function RefreshControlShim({ refreshing, onRefresh }: { refreshing: boolean; onRefresh: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={styles.refreshButton} onPress={onRefresh}>
      <Text style={styles.refreshText}>{refreshing ? "Refreshing..." : "Refresh Premium status"}</Text>
    </Pressable>
  );
}

function Badge({ label, active }: { label: string; active: boolean }) {
  return <Text style={[styles.badge, active ? styles.badgeActive : undefined]}>{label}</Text>;
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metaRow}>
      <Text style={styles.metaLabel}>{label}</Text>
      <Text style={styles.metaValue}>{value}</Text>
    </View>
  );
}

function EntitlementRow({ label, status, detail }: { label: string; status: "active" | "locked" | "unavailable"; detail: string }) {
  return (
    <View style={styles.entitlement}>
      <View style={styles.entitlementHeader}>
        <Text style={styles.entitlementTitle}>{label}</Text>
        <Text style={[styles.entitlementStatus, status === "active" ? styles.active : status === "unavailable" ? styles.unavailable : undefined]}>{status}</Text>
      </View>
      <Text style={styles.muted}>{detail}</Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  active: {
    color: colors.accent
  },
  badge: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    paddingHorizontal: 9,
    paddingVertical: 5,
    textTransform: "capitalize"
  },
  badgeActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent,
    color: colors.accent
  },
  badges: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  body: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 6
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 46,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  buttonText: {
    color: colors.background,
    fontWeight: "900",
    textAlign: "center"
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
    marginTop: 10,
    textAlign: "center"
  },
  entitlement: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 6,
    padding: 12
  },
  entitlementHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between"
  },
  entitlementStatus: {
    color: colors.warning,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "capitalize"
  },
  entitlementTitle: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
    fontWeight: "900"
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
  headingCopy: {
    flex: 1
  },
  headingRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 12
  },
  metaLabel: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  metaRow: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
    paddingBottom: 10
  },
  metaValue: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  plan: {
    color: colors.text,
    fontSize: 25,
    fontWeight: "900",
    marginTop: 4
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
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 46,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900",
    textAlign: "center"
  },
  statusDot: {
    backgroundColor: colors.warning,
    borderRadius: 8,
    height: 16,
    marginTop: 6,
    width: 16
  },
  statusDotActive: {
    backgroundColor: colors.accent
  },
  title: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  unavailable: {
    color: colors.danger
  },
  warning: {
    color: colors.warning,
    fontSize: 13,
    lineHeight: 19
  }
}));
