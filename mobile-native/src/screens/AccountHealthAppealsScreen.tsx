import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, AppState, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  AccountHealthAppealItem,
  AccountHealthState,
  loadAccountHealthState,
  loadCachedAccountHealthState,
  openAccountHealthWebFallback,
  submitAccountHealthVerificationAppeal
} from "../api/accountHealth";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props =
  | NativeStackScreenProps<RootStackParamList, "AccountHealth">
  | NativeStackScreenProps<RootStackParamList, "AccountHealthWeb">;

export function AccountHealthAppealsScreen({ navigation }: Props) {
  const [state, setState] = useState<AccountHealthState | null>(null);
  const [appealNote, setAppealNote] = useState("");
  const [selectedAppeal, setSelectedAppeal] = useState<AccountHealthAppealItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const next = await loadAccountHealthState();
      setState(next);
      setSelectedAppeal(next.appeals.find((appeal) => appeal.supported) || next.appeals[0] || null);
    } catch (loadError) {
      const cached = await loadCachedAccountHealthState();
      if (cached) {
        setState(cached);
        setSelectedAppeal(cached.appeals.find((appeal) => appeal.supported) || cached.appeals[0] || null);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Account Health could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    navigation.setOptions({ title: "Account Health" });
  }, [navigation]);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "active") load("refresh").catch(() => undefined);
    });
    return () => sub.remove();
  }, [load]);

  async function submitAppeal() {
    if (!selectedAppeal?.supported || !selectedAppeal.requestId) {
      setError("This appeal path needs the protected Account Health or Verification Center flow.");
      setNotice("");
      return;
    }
    if (appealNote.trim().length < 8) {
      setError("Add at least 8 characters before submitting an appeal.");
      setNotice("");
      return;
    }
    setBusy("appeal");
    setError("");
    setNotice("");
    try {
      const result = await submitAccountHealthVerificationAppeal(selectedAppeal.requestId, appealNote.trim());
      setNotice(result.message || "Appeal submitted.");
      setAppealNote("");
      await load("refresh");
    } catch (appealError) {
      setError(appealError instanceof Error ? appealError.message : "Appeal could not be submitted.");
    } finally {
      setBusy("");
    }
  }

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Account Health</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh").catch(() => undefined)} tintColor={colors.accent} />}
    >
      <View style={styles.header}>
        <Text style={styles.eyebrow}>PulseSoc trust grid</Text>
        <Text style={styles.title}>Account Health</Text>
        <Text style={styles.subtitle}>
          {offline ? "Showing cached account health. Pull to reconnect." : "Warnings, strikes, restrictions, appeals, reports, and recovery actions stay server-authoritative."}
        </Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <Panel>
        <View style={styles.heroRow}>
          <View style={styles.scoreOrb}>
            <Text style={styles.scoreText}>{state?.score || 0}</Text>
            <Text style={styles.scoreLabel}>health</Text>
          </View>
          <View style={styles.heroCopy}>
            <Text style={styles.panelTitle}>{statusLabel(state?.status)}</Text>
            <Text style={styles.muted}>Risk level: {state?.riskLevel || "Low"}</Text>
            <Text style={styles.muted}>Account score: {state?.accountScore || 0}</Text>
            <Text style={styles.muted}>{state?.primaryAction || "Review Account Health"}</Text>
          </View>
        </View>
      </Panel>

      <View style={styles.metricGrid}>
        <Metric label="Warnings" value={state?.warnings || 0} tone={state?.warnings ? "warn" : "ok"} />
        <Metric label="Strikes" value={state?.strikes || 0} tone={state?.strikes ? "danger" : "ok"} />
        <Metric label="Restrictions" value={state?.restrictions || 0} tone={state?.restrictions ? "danger" : "ok"} />
        <Metric label="Appeals" value={state?.appealsAvailable || 0} tone={state?.appealsAvailable ? "warn" : "ok"} />
      </View>

      <Panel>
        <Text style={styles.panelTitle}>Enforcement history</Text>
        {(state?.enforcement || []).map((item) => (
          <View key={item.key} style={styles.row}>
            <View style={styles.rowCopy}>
              <Text style={styles.rowTitle}>{item.label}</Text>
              <Text style={styles.muted}>{item.detail}</Text>
            </View>
            <Text style={[styles.statusPill, item.count > 0 && styles.statusPillActive]}>{item.count} · {item.status}</Text>
          </View>
        ))}
        <ActionButton label="Open protected health details" variant="secondary" onPress={() => openAccountHealthWebFallback("/dashboard/account/health")} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Appeals</Text>
        {(state?.appeals || []).map((appeal) => (
          <Pressable
            key={appeal.key}
            accessibilityRole="button"
            style={[styles.appealCard, selectedAppeal?.key === appeal.key && styles.appealCardActive]}
            onPress={() => setSelectedAppeal(appeal)}
          >
            <View style={styles.rowCopy}>
              <Text style={styles.rowTitle}>{appeal.title}</Text>
              <Text style={styles.muted}>{appeal.detail}</Text>
            </View>
            <Text style={[styles.statusPill, appeal.supported && styles.statusPillActive]}>{appeal.status}</Text>
          </Pressable>
        ))}
        {!state?.appeals.length ? <Text style={styles.muted}>No appeal paths returned by the backend.</Text> : null}
        <TextInput
          accessibilityLabel="Account health appeal note"
          multiline
          onChangeText={setAppealNote}
          placeholder="Add an appeal note when the selected server-owned appeal path supports native submission."
          placeholderTextColor={colors.muted}
          style={[styles.input, styles.textArea]}
          value={appealNote}
        />
        <ActionButton label={busy === "appeal" ? "Submitting..." : "Submit supported appeal"} disabled={Boolean(busy)} onPress={submitAppeal} />
        <ActionButton label="Open Verification Center" variant="secondary" disabled={Boolean(busy)} onPress={() => navigation.navigate("VerificationCenter", { title: "Verification Center" })} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Linked reports and cases</Text>
        {(state?.cases || []).map((item) => (
          <View key={item.id} style={styles.row}>
            <View style={styles.rowCopy}>
              <Text style={styles.rowTitle}>#{item.id} · {item.subject}</Text>
              <Text style={styles.muted}>{item.issueType} · {item.updatedAt || "recent"}</Text>
            </View>
            <Text style={styles.statusPill}>{item.status}</Text>
          </View>
        ))}
        {!state?.cases.length ? <Text style={styles.muted}>No linked support cases returned by the backend.</Text> : null}
        <View style={styles.buttonGrid}>
          <ActionButton label="Trust & Safety" variant="secondary" onPress={() => navigation.navigate("TrustSafety", { title: "Trust & Safety", mode: "support" })} />
          <ActionButton label="Security Center" variant="secondary" onPress={() => navigation.navigate("AccountCenter", { section: "security", title: "Security Center" })} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Recent security signals</Text>
        {(state?.securityEvents || []).slice(0, 4).map((event) => (
          <View key={event.id} style={styles.row}>
            <View style={styles.rowCopy}>
              <Text style={styles.rowTitle}>{event.event_type || "security_event"}</Text>
              <Text style={styles.muted}>{event.device_label || "PulseSoc"} · {event.created_at || "recent"}</Text>
            </View>
          </View>
        ))}
        {!state?.securityEvents.length ? <Text style={styles.muted}>No recent security events returned by the backend.</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Recovery recommendations</Text>
        {(state?.recommendations || []).map((item, index) => (
          <Text key={`${item}-${index}`} style={styles.recommendation}>- {item}</Text>
        ))}
      </Panel>
    </ScrollView>
  );
}

function ActionButton({ label, onPress, disabled, variant = "primary" }: { label: string; onPress: () => void; disabled?: boolean; variant?: "primary" | "secondary" }) {
  return (
    <Pressable accessibilityRole="button" disabled={disabled} style={[styles.actionButton, variant === "secondary" && styles.secondaryButton, disabled && styles.disabled]} onPress={onPress}>
      <Text style={[styles.actionText, variant === "secondary" && styles.secondaryText]}>{label}</Text>
    </Pressable>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: "ok" | "warn" | "danger" }) {
  return (
    <View style={[styles.metric, tone === "warn" && styles.metricWarn, tone === "danger" && styles.metricDanger]}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function statusLabel(status?: string) {
  return String(status || "secure").replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const styles = StyleSheet.create({
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12
  },
  actionText: {
    color: "#08110f",
    fontWeight: "900"
  },
  appealCard: {
    alignItems: "flex-start",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
    padding: 10
  },
  appealCardActive: {
    backgroundColor: "rgba(37,208,167,0.1)",
    borderColor: colors.accent
  },
  buttonGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    gap: 12,
    justifyContent: "center"
  },
  centerText: {
    color: colors.text,
    fontWeight: "800"
  },
  content: {
    gap: 14,
    padding: 16
  },
  disabled: {
    opacity: 0.55
  },
  error: {
    backgroundColor: "rgba(255,107,107,0.12)",
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.danger,
    padding: 10
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  header: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    padding: 16
  },
  heroCopy: {
    flex: 1,
    gap: 4
  },
  heroRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    minHeight: 44,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  metric: {
    backgroundColor: "rgba(37,208,167,0.1)",
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    minWidth: 132,
    padding: 12
  },
  metricDanger: {
    backgroundColor: "rgba(255,107,107,0.11)",
    borderColor: colors.danger
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  metricValue: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  metricWarn: {
    backgroundColor: "rgba(243,185,78,0.11)",
    borderColor: colors.warning
  },
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  notice: {
    backgroundColor: "rgba(37,208,167,0.1)",
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.accent,
    padding: 10
  },
  panelTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  recommendation: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 20
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  row: {
    alignItems: "flex-start",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
    padding: 10
  },
  rowCopy: {
    flex: 1,
    gap: 3
  },
  rowTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  scoreLabel: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  scoreOrb: {
    alignItems: "center",
    aspectRatio: 1,
    backgroundColor: "rgba(79,140,255,0.12)",
    borderColor: colors.accentStrong,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    shadowColor: colors.accentStrong,
    shadowOpacity: 0.25,
    shadowRadius: 16,
    width: 78
  },
  scoreText: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  secondaryButton: {
    backgroundColor: "transparent",
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth
  },
  secondaryText: {
    color: colors.text
  },
  statusPill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    paddingHorizontal: 9,
    paddingVertical: 5,
    textTransform: "uppercase"
  },
  statusPillActive: {
    backgroundColor: "rgba(243,185,78,0.12)",
    borderColor: colors.warning,
    color: colors.warning
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  textArea: {
    minHeight: 96,
    textAlignVertical: "top"
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  }
});
