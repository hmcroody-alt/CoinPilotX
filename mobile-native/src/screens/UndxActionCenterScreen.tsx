import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { fetchUndxActionCenter, fetchUndxPermissions, fetchUndxTools } from "../api/undxActions";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

type Props = NativeStackScreenProps<RootStackParamList, "UndxActionCenter">;
type AnyRecord = Record<string, unknown>;

const DEFAULT_ORG_ID = "coinplotxai";

export function UndxActionCenterScreen({ route }: Props) {
  const orgId = route.params?.orgId || DEFAULT_ORG_ID;
  const actor = route.params?.actor || "";
  const [snapshot, setSnapshot] = useState<AnyRecord | null>(null);
  const [tools, setTools] = useState<AnyRecord[]>([]);
  const [permissions, setPermissions] = useState<AnyRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const [centerResult, toolResult, permissionResult] = await Promise.all([
        fetchUndxActionCenter({ orgId, limit: 80 }),
        fetchUndxTools({ productArea: route.params?.productArea || "marketplace", limit: 80 }),
        fetchUndxPermissions({ orgId, actor, limit: 80 }).catch(() => ({ ok: false, result: {} }))
      ]);
      setSnapshot(asRecord(centerResult.result));
      setTools(asList(asRecord(toolResult.result).tools));
      setPermissions(asList(asRecord(permissionResult.result).permissions));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "UNDX Action Center could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [actor, orgId, route.params?.productArea]);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  const pending = useMemo(() => asList(snapshot?.pending || snapshot?.requests || snapshot?.action_requests), [snapshot]);
  const decisions = useMemo(() => asList(snapshot?.decisions), [snapshot]);
  const receipts = useMemo(() => asList(snapshot?.receipts), [snapshot]);
  const stops = useMemo(() => {
    const list = asList(snapshot?.emergency_stops);
    if (list.length) return list;
    const stop = asRecord(snapshot?.emergency_stop);
    return Object.keys(stop).length ? [stop] : [];
  }, [snapshot]);

  if (loading && !snapshot) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading UNDX Action Center</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl tintColor={colors.accent} refreshing={refreshing} onRefresh={() => load("refresh").catch(() => undefined)} />}
    >
      <View style={styles.hero}>
        <View style={styles.heroCopy}>
          <Text style={styles.eyebrow}>UNDX GOVERNANCE</Text>
          <Text style={styles.title}>Action Center</Text>
          <Text style={styles.subtitle}>Server-authoritative decisions, approvals, receipts, and Marketplace workflow state.</Text>
        </View>
        <View style={styles.signalBadge}>
          <Text style={styles.signalValue}>{pending.length}</Text>
          <Text style={styles.signalLabel}>pending</Text>
        </View>
      </View>

      {error ? (
        <View style={styles.errorPanel}>
          <Text style={styles.errorTitle}>Action Center unavailable</Text>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retry} onPress={() => load("refresh").catch(() => undefined)} accessibilityRole="button">
            <Text style={styles.retryText}>Retry</Text>
          </Pressable>
        </View>
      ) : null}

      <View style={styles.metricGrid}>
        <Metric label="Decisions" value={decisions.length} tone="intelligence" />
        <Metric label="Requests" value={pending.length} tone="accent" />
        <Metric label="Tools" value={tools.length} tone="economy" />
        <Metric label="Permissions" value={permissions.length} tone="safety" />
      </View>

      <Section title="Pending actions" empty="No pending UNDX actions returned by the backend.">
        {pending.slice(0, 12).map((item, index) => <ActionRow key={rowKey(item, index, "pending")} item={item} />)}
      </Section>

      <Section title="Governance decisions" empty="No decisions returned yet. Run evaluation after requests are recorded.">
        {decisions.slice(0, 12).map((item, index) => <ActionRow key={rowKey(item, index, "decision")} item={item} />)}
      </Section>

      <Section title="Marketplace tools" empty="No registered Marketplace tools returned.">
        {tools.slice(0, 12).map((item, index) => <ActionRow key={rowKey(item, index, "tool")} item={item} />)}
      </Section>

      <Section title="Permissions" empty="No actor permissions returned.">
        {permissions.slice(0, 12).map((item, index) => <ActionRow key={rowKey(item, index, "permission")} item={item} />)}
      </Section>

      <Section title="Receipts and emergency stops" empty="No receipts or active emergency stops returned.">
        {[...receipts.slice(0, 8), ...stops.slice(0, 4)].map((item, index) => <ActionRow key={rowKey(item, index, "receipt")} item={item} danger={Boolean(asText(item.active) === "true" || asText(item.reason))} />)}
      </Section>
    </ScrollView>
  );
}

function Section({ title, empty, children }: { title: string; empty: string; children: ReactNode }) {
  const content = Array.isArray(children) ? children.filter(Boolean) : children;
  const isEmpty = Array.isArray(content) ? content.length === 0 : !content;
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {isEmpty ? <Text style={styles.empty}>{empty}</Text> : content}
    </View>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: "accent" | "intelligence" | "economy" | "safety" }) {
  const accent = tone === "intelligence" ? colors.intelligence : tone === "economy" ? colors.economy : tone === "safety" ? colors.safety : colors.accent;
  return (
    <View style={[styles.metric, { borderColor: `${accent}66` }]}>
      <Text style={[styles.metricValue, { color: accent }]}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function ActionRow({ item, danger }: { item: AnyRecord; danger?: boolean }) {
  const title = asText(item.action_type || item.tool_name || item.request_id || item.policy_id || item.receipt_id || item.reason || "Governed action");
  const status = asText(item.effect || item.status || item.decision || item.risk || item.product_area || "server state");
  const detail = asText(item.reason || item.subject_ref || item.canonical_ref || item.feature_flag || item.external_ref || item.actor || "");
  return (
    <View style={[styles.row, danger ? styles.rowDanger : undefined]}>
      <View style={styles.rowHead}>
        <Text style={styles.rowTitle} numberOfLines={1}>{title}</Text>
        <Text style={[styles.rowPill, danger ? styles.rowPillDanger : undefined]} numberOfLines={1}>{status}</Text>
      </View>
      {detail ? <Text style={styles.rowDetail} numberOfLines={2}>{detail}</Text> : null}
      <Text style={styles.rowMeta} numberOfLines={1}>{asText(item.request_id || item.tool_name || item.org_id || item.created_at || "canonical")}</Text>
    </View>
  );
}

function asRecord(value: unknown): AnyRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as AnyRecord : {};
}

function asList(value: unknown): AnyRecord[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is AnyRecord => Boolean(item && typeof item === "object" && !Array.isArray(item)));
}

function asText(value: unknown): string {
  if (value === undefined || value === null) return "";
  return String(value);
}

function rowKey(item: AnyRecord, index: number, prefix: string) {
  return `${prefix}:${asText(item.request_id || item.tool_name || item.permission_id || item.receipt_id || item.policy_id || item.created_at || index)}`;
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  content: {
    gap: logiNexus.spacing.lg,
    padding: logiNexus.spacing.lg,
    paddingBottom: logiNexus.spacing.giant
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center",
    padding: logiNexus.spacing.xxl
  },
  centerText: {
    ...logiNexus.typography.body,
    color: colors.muted,
    marginTop: logiNexus.spacing.md
  },
  hero: {
    backgroundColor: colors.glassStrong,
    borderColor: `${colors.intelligence}88`,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.lg,
    justifyContent: "space-between",
    padding: logiNexus.spacing.xl
  },
  heroCopy: {
    flex: 1,
    gap: logiNexus.spacing.xs
  },
  eyebrow: {
    ...logiNexus.typography.label,
    color: colors.intelligence,
    letterSpacing: 2
  },
  title: {
    ...logiNexus.typography.title,
    color: colors.text
  },
  subtitle: {
    ...logiNexus.typography.body,
    color: colors.muted
  },
  signalBadge: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: colors.signalDim,
    borderColor: `${colors.accent}88`,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    minWidth: 76,
    padding: logiNexus.spacing.md
  },
  signalValue: {
    ...logiNexus.typography.metric,
    color: colors.accent
  },
  signalLabel: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  errorPanel: {
    backgroundColor: colors.dangerSoft,
    borderColor: `${colors.danger}88`,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    gap: logiNexus.spacing.sm,
    padding: logiNexus.spacing.lg
  },
  errorTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  errorText: {
    ...logiNexus.typography.body,
    color: colors.muted
  },
  retry: {
    alignSelf: "flex-start",
    backgroundColor: colors.danger,
    borderRadius: logiNexus.radius.capsule,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  retryText: {
    ...logiNexus.typography.button,
    color: colors.background
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: logiNexus.spacing.md
  },
  metric: {
    backgroundColor: colors.glass,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexBasis: "47%",
    flexGrow: 1,
    padding: logiNexus.spacing.lg
  },
  metricValue: {
    ...logiNexus.typography.metric
  },
  metricLabel: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  section: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    gap: logiNexus.spacing.md,
    padding: logiNexus.spacing.lg
  },
  sectionTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  empty: {
    ...logiNexus.typography.body,
    color: colors.muted
  },
  row: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    gap: logiNexus.spacing.xs,
    padding: logiNexus.spacing.md
  },
  rowDanger: {
    borderColor: `${colors.danger}99`
  },
  rowHead: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.sm,
    justifyContent: "space-between"
  },
  rowTitle: {
    ...logiNexus.typography.body,
    color: colors.text,
    flex: 1
  },
  rowPill: {
    ...logiNexus.typography.metadata,
    backgroundColor: colors.signalDim,
    borderRadius: logiNexus.radius.capsule,
    color: colors.accent,
    overflow: "hidden",
    paddingHorizontal: logiNexus.spacing.sm,
    paddingVertical: logiNexus.spacing.xs
  },
  rowPillDanger: {
    backgroundColor: colors.dangerSoft,
    color: colors.danger
  },
  rowDetail: {
    ...logiNexus.typography.body,
    color: colors.muted
  },
  rowMeta: {
    ...logiNexus.typography.metadata,
    color: colors.disabled
  }
});
