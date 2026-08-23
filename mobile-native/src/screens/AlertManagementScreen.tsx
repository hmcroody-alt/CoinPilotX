import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  ALERT_CHANNELS,
  ALERT_CONDITIONS,
  AlertChannel,
  ChannelReadiness,
  AlertEvent,
  AlertFormPayload,
  AlertManagementState,
  AlertRule,
  alertConditionLabel,
  alertEnabledChannels,
  alertStatusLabel,
  createCryptoAlert,
  deleteAlert,
  duplicateCryptoAlert,
  getAlertChannelReadiness,
  getAlertManagementState,
  getCryptoAlertHistory,
  loadCachedAlertManagementState,
  loadCachedCryptoAlertHistory,
  pauseAlert,
  resumeAlert,
  testAlert,
  testAlertChannel,
  updateCryptoAlert
} from "../api/alerts";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props =
  | NativeStackScreenProps<RootStackParamList, "AlertManagement">
  | NativeStackScreenProps<RootStackParamList, "CryptoAlertManagement">;

const emptyForm: AlertFormPayload = {
  assetSymbol: "BTC",
  targetValue: "",
  condition: "above",
  notifyInApp: true,
  notifyEmail: false,
  notifyPush: true,
  notifySMS: false,
  notifyTelegram: false,
  note: ""
};

export function AlertManagementScreen({ route, navigation }: Props) {
  const routeParams = route.params as (
    { alertId?: number; alert_id?: number; id?: number; presetSymbol?: string } | undefined
  );
  const routeAlertId = Number(routeParams?.alertId || routeParams?.alert_id || routeParams?.id || 0);
  // "Create alert" from an asset arrives here with that asset already chosen.
  // It seeds the form only — the rest of the flow, its validation and its
  // engine call stay exactly the canonical ones, because a second create path
  // is how two alert systems start.
  const presetSymbol = String(routeParams?.presetSymbol || "").trim().toUpperCase().slice(0, 12);
  const [state, setState] = useState<AlertManagementState | null>(null);
  const [selectedId, setSelectedId] = useState(routeAlertId);
  const selectedIdRef = useRef(selectedId);
  const [historyEvents, setHistoryEvents] = useState<AlertEvent[]>([]);
  const [form, setForm] = useState<AlertFormPayload>(
    presetSymbol ? { ...emptyForm, assetSymbol: presetSymbol } : emptyForm
  );
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);

  const alerts = state?.alerts || [];
  const selectedAlert = useMemo(() => alerts.find((alert) => alert.id === selectedId) || null, [alerts, selectedId]);
  const readiness = (state?.channel_readiness || {}) as Record<AlertChannel, ChannelReadiness>;
  const recentEvents = historyEvents.length ? historyEvents : state?.events || [];

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setNotice("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const next = await getAlertManagementState();
      setState(next);
      const nextSelected = routeAlertId || selectedIdRef.current || next.alerts[0]?.id || 0;
      setSelectedId(nextSelected);
      if (nextSelected) {
        const history = await getCryptoAlertHistory(nextSelected).catch(() => ({ events: [] }));
        setHistoryEvents(history.events || []);
      }
    } catch (loadError) {
      const cached = await loadCachedAlertManagementState();
      if (cached) {
        setState(cached);
        setOffline(true);
        const nextSelected = routeAlertId || selectedIdRef.current || cached.alerts[0]?.id || 0;
        setSelectedId(nextSelected);
        if (nextSelected) {
          const history = await loadCachedCryptoAlertHistory(nextSelected);
          setHistoryEvents(history?.events || []);
        }
      }
      setError(loadError instanceof Error ? loadError.message : "Alert Management could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [routeAlertId]);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  useEffect(() => {
    if (!selectedId) {
      setHistoryEvents([]);
      return;
    }
    getCryptoAlertHistory(selectedId)
      .then((history) => setHistoryEvents(history.events || []))
      .catch(() => loadCachedCryptoAlertHistory(selectedId).then((history) => setHistoryEvents(history?.events || [])).catch(() => undefined));
  }, [selectedId]);

  function startEdit(alert: AlertRule) {
    setEditingId(alert.id);
    setSelectedId(alert.id);
    setNotice("");
    setForm({
      assetSymbol: alert.asset_symbol || alert.symbol || "BTC",
      targetValue: String(alert.threshold ?? alert.threshold_value ?? alert.target_value ?? ""),
      condition: alert.condition || "above",
      notifyInApp: Boolean(alert.channels?.in_app),
      notifyEmail: Boolean(alert.channels?.email),
      notifyPush: Boolean(alert.channels?.push),
      notifySMS: Boolean(alert.channels?.sms),
      notifyTelegram: Boolean(alert.channels?.telegram),
      note: ""
    });
  }

  function resetForm() {
    setEditingId(null);
    // Back to the asset the user came in with, not to the global default. They
    // arrived from SOL; clearing the form to BTC would be a silent retarget.
    setForm(presetSymbol ? { ...emptyForm, assetSymbol: presetSymbol } : emptyForm);
  }

  async function saveForm() {
    const validationError = validateAlertForm(form);
    if (validationError) {
      setError(validationError);
      setNotice("");
      return;
    }
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const result = editingId ? await updateCryptoAlert(editingId, form) : await createCryptoAlert(form);
      resetForm();
      await load("refresh");
      if (result.alert_id) setSelectedId(result.alert_id);
      setNotice(result.message || (editingId ? "Alert updated." : "Alert created."));
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Alert could not be saved.");
    } finally {
      setBusy("");
    }
  }

  async function runAction(alert: AlertRule, action: "pause" | "resume" | "delete" | "duplicate" | "test") {
    setBusy(`${action}:${alert.id}`);
    setError("");
    setNotice("");
    try {
      const result =
        action === "pause" ? await pauseAlert(alert.id) :
        action === "resume" ? await resumeAlert(alert.id) :
        action === "delete" ? await deleteAlert(alert.id) :
        action === "duplicate" ? await duplicateCryptoAlert(alert.id) :
        await testAlert(alert.id);
      if (action === "delete") setPendingDeleteId(null);
      await load("refresh");
      if (action === "duplicate" && result.alert_id) setSelectedId(result.alert_id);
      setNotice(result.message || `${action} complete.`);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Alert action failed.");
    } finally {
      setBusy("");
    }
  }

  function confirmDelete(alert: AlertRule) {
    setPendingDeleteId(alert.id);
    setNotice(`Confirm delete for ${alert.asset_symbol || alert.symbol} ${alertConditionLabel(alert)}.`);
    setError("");
  }

  async function refreshReadiness() {
    setBusy("readiness");
    setError("");
    try {
      const channel_readiness = await getAlertChannelReadiness();
      setState((current) => current ? { ...current, channel_readiness } : current);
      setNotice("Channel readiness refreshed.");
    } catch (readinessError) {
      setError(readinessError instanceof Error ? readinessError.message : "Channel readiness could not refresh.");
    } finally {
      setBusy("");
    }
  }

  async function runChannelTest(channel: AlertChannel) {
    setBusy(`channel:${channel}`);
    setError("");
    try {
      const result = await testAlertChannel(channel);
      setNotice(result.message || `${channel.replace("_", " ")} test complete.`);
      if (result.channel_readiness) setState((current) => current ? { ...current, channel_readiness: result.channel_readiness || current.channel_readiness } : current);
    } catch (channelError) {
      setError(channelError instanceof Error ? channelError.message : "Channel test failed.");
    } finally {
      setBusy("");
    }
  }

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Alert Management</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Alert Management</Text>
        <Text style={styles.subtitle}>{offline ? "Showing cached alert state." : "Your crypto and market alerts, as PulseSoc has them set."}</Text>
      </View>

      <View style={styles.topActions}>
        <ActionButton label={refreshing ? "Refreshing" : "Refresh"} disabled={refreshing} onPress={() => load("refresh").catch(() => undefined)} />
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <Panel>
        <View style={styles.heroRow}>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>Your alerts</Text>
            <Text style={styles.score}>{alerts.length}</Text>
            <Text style={styles.muted}>Create, edit, pause, resume, duplicate, test, and inspect alert history without duplicating alert engine logic.</Text>
          </View>
          <View style={styles.statusPill}>
            <Text style={styles.statusPillText}>{state?.worker?.stale ? "Worker stale" : "Worker linked"}</Text>
          </View>
        </View>
      </Panel>

      <Panel>
        <View style={styles.panelHeader}>
          <Text style={styles.sectionTitle}>Delivery readiness</Text>
          <ActionButton label={busy === "readiness" ? "Checking" : "Check"} variant="secondary" disabled={busy === "readiness"} onPress={refreshReadiness} />
        </View>
        {ALERT_CHANNELS.map((channel) => (
          <View key={channel} style={styles.readinessRow}>
            <View style={styles.readinessCopy}>
              <Text style={styles.rowTitle}>{channel.replace("_", " ")}</Text>
              <Text style={styles.muted}>{readiness[channel]?.message || "PulseSoc decides when this is ready."}</Text>
            </View>
            <Text style={[styles.pill, readiness[channel]?.ready ? styles.readyPill : styles.warnPill]}>{readiness[channel]?.label || "Needs setup"}</Text>
            <ActionButton label={busy === `channel:${channel}` ? "Testing" : "Test"} variant="secondary" disabled={busy === `channel:${channel}`} onPress={() => runChannelTest(channel)} />
          </View>
        ))}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{editingId ? "Edit alert" : "Create alert"}</Text>
        <TextInput
          accessibilityLabel="Alert symbol"
          autoCapitalize="characters"
          placeholder="Symbol"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={form.assetSymbol}
          onChangeText={(assetSymbol) => setForm((current) => ({ ...current, assetSymbol }))}
        />
        <TextInput
          accessibilityLabel="Alert target value"
          keyboardType="numeric"
          placeholder="Target value"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={form.targetValue}
          onChangeText={(targetValue) => setForm((current) => ({ ...current, targetValue }))}
        />
        <View style={styles.segmentWrap}>
          {ALERT_CONDITIONS.map((condition) => (
            <Pressable
              accessibilityRole="button"
              key={condition.value}
              style={[styles.segment, form.condition === condition.value ? styles.segmentActive : undefined]}
              onPress={() => setForm((current) => ({ ...current, condition: condition.value }))}
            >
              <Text style={[styles.segmentText, form.condition === condition.value ? styles.segmentTextActive : undefined]}>{condition.label}</Text>
            </Pressable>
          ))}
        </View>
        <View style={styles.channelGrid}>
          <ChannelToggle label="In-app" value={form.notifyInApp} onPress={() => setForm((current) => ({ ...current, notifyInApp: !current.notifyInApp }))} />
          <ChannelToggle label="Email" value={form.notifyEmail} onPress={() => setForm((current) => ({ ...current, notifyEmail: !current.notifyEmail }))} />
          <ChannelToggle label="Push" value={form.notifyPush} onPress={() => setForm((current) => ({ ...current, notifyPush: !current.notifyPush }))} />
          <ChannelToggle label="SMS" value={form.notifySMS} onPress={() => setForm((current) => ({ ...current, notifySMS: !current.notifySMS }))} />
          <ChannelToggle label="Telegram" value={form.notifyTelegram} onPress={() => setForm((current) => ({ ...current, notifyTelegram: !current.notifyTelegram }))} />
        </View>
        <TextInput
          accessibilityLabel="Alert note"
          placeholder="Optional note"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={form.note}
          onChangeText={(note) => setForm((current) => ({ ...current, note }))}
        />
        <View style={styles.topActions}>
          <ActionButton label={busy === "save" ? "Saving" : editingId ? "Save changes" : "Create alert"} disabled={busy === "save"} onPress={saveForm} />
          {editingId ? <ActionButton label="Cancel edit" variant="secondary" onPress={resetForm} /> : null}
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Crypto and market alerts</Text>
        {alerts.length ? alerts.map((alert) => (
          <AlertCard
            key={alert.id}
            alert={alert}
            selected={selectedId === alert.id}
            busy={busy.endsWith(`:${alert.id}`)}
            onSelect={() => setSelectedId(alert.id)}
            onEdit={() => startEdit(alert)}
            onPause={() => runAction(alert, "pause")}
            onResume={() => runAction(alert, "resume")}
            onDelete={() => confirmDelete(alert)}
            onConfirmDelete={() => runAction(alert, "delete")}
            onCancelDelete={() => {
              setPendingDeleteId(null);
              setNotice("Delete canceled.");
            }}
            onDuplicate={() => runAction(alert, "duplicate")}
            onTest={() => runAction(alert, "test")}
            pendingDelete={pendingDeleteId === alert.id}
          />
        )) : <Text style={styles.muted}>You have no alerts yet. Create your first one above.</Text>}
      </Panel>

      {selectedAlert ? (
        <Panel>
          <Text style={styles.sectionTitle}>Alert detail</Text>
          <Text style={styles.detailTitle}>{selectedAlert.asset_symbol || selectedAlert.symbol} {alertConditionLabel(selectedAlert)}</Text>
          <Text style={styles.muted}>Status: {alertStatusLabel(selectedAlert.status)}. Source: {selectedAlert.source || "server"}. Trigger count: {selectedAlert.trigger_count || 0}.</Text>
          <Text style={styles.muted}>Channels: {alertEnabledChannels(selectedAlert).join(", ") || "server delivery"}</Text>
          <Text style={styles.muted}>Last checked: {selectedAlert.last_checked_at || "not recorded"}</Text>
          <Text style={styles.muted}>Last triggered: {selectedAlert.last_triggered_at || "not triggered"}</Text>
        </Panel>
      ) : null}

      <Panel>
        <Text style={styles.sectionTitle}>Alert history</Text>
        {recentEvents.length ? recentEvents.slice(0, 12).map((event, index) => (
          <View key={`${event.id || index}-${event.created_at || index}`} style={styles.eventRow}>
            <View style={styles.rowHead}>
              <Text style={styles.rowTitle}>{event.symbol || selectedAlert?.asset_symbol || "MARKET"}</Text>
              <Text style={styles.pill}>{event.status || "recorded"}</Text>
            </View>
            <Text style={styles.muted}>{event.message || `${event.condition || "alert"} at ${event.observed_value ?? "unknown"} against ${event.threshold_value ?? "target"}`}</Text>
            <Text style={styles.rowMeta}>{event.created_at || "No timestamp"}</Text>
          </View>
        )) : <Text style={styles.muted}>No alert history yet. Events appear here after an alert fires or you send a test.</Text>}
        {recentEvents.length > 12 ? <Text style={styles.rowMeta}>Showing the newest 12 of {recentEvents.length} alert history events.</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Managed by PulseSoc</Text>
        <Text style={styles.muted}>Account administration, advanced Intelligence editing, data source management, and other alert types are handled on the PulseSoc website. They are not available in the app yet.</Text>
        <View style={styles.topActions}>
          <ActionButton label="Notifications" variant="secondary" onPress={() => navigation.navigate("NotificationCenter")} />
        </View>
      </Panel>
    </ScrollView>
  );
}

function AlertCard({
  alert,
  selected,
  busy,
  onSelect,
  onEdit,
  onPause,
  onResume,
  onDelete,
  onConfirmDelete,
  onCancelDelete,
  onDuplicate,
  onTest,
  pendingDelete
}: {
  alert: AlertRule;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
  onDuplicate: () => void;
  onTest: () => void;
  pendingDelete: boolean;
}) {
  const active = (alert.status || "active") === "active";
  return (
    <View style={[styles.alertCard, selected ? styles.alertCardSelected : undefined]}>
      <Pressable accessibilityRole="button" onPress={onSelect}>
        <View style={styles.rowHead}>
          <Text style={styles.rowTitle}>{alert.asset_symbol || alert.symbol} alert</Text>
          <Text style={[styles.pill, active ? styles.readyPill : styles.warnPill]}>{alertStatusLabel(alert.status)}</Text>
        </View>
        <Text style={styles.muted}>{alertConditionLabel(alert)}</Text>
        <Text style={styles.rowMeta}>{alert.history_count || 0} history events - {alertEnabledChannels(alert).join(", ") || "server delivery"}</Text>
      </Pressable>
      <View style={styles.actionGrid}>
        <ActionButton label="Detail" variant="secondary" onPress={onSelect} />
        <ActionButton label="Edit" variant="secondary" onPress={onEdit} />
        {active ? <ActionButton label="Pause" variant="secondary" disabled={busy} onPress={onPause} /> : <ActionButton label="Resume" variant="secondary" disabled={busy} onPress={onResume} />}
        <ActionButton label="Test" variant="secondary" disabled={busy} onPress={onTest} />
        <ActionButton label="Duplicate" variant="secondary" disabled={busy} onPress={onDuplicate} />
        <ActionButton label="Delete" variant="danger" disabled={busy} onPress={onDelete} />
      </View>
      {pendingDelete ? (
        <View style={styles.confirmBox}>
          <Text style={styles.confirmText}>Delete this alert? It is removed from your PulseSoc account and this cannot be undone.</Text>
          <View style={styles.topActions}>
            <ActionButton label="Cancel" variant="secondary" disabled={busy} onPress={onCancelDelete} />
            <ActionButton label={busy ? "Deleting" : "Confirm delete"} variant="danger" disabled={busy} onPress={onConfirmDelete} />
          </View>
        </View>
      ) : null}
    </View>
  );
}

function validateAlertForm(form: AlertFormPayload) {
  const symbol = form.assetSymbol.trim().toUpperCase();
  const rawTarget = form.targetValue.trim();
  const target = Number(rawTarget);
  const hasChannel = form.notifyInApp || form.notifyEmail || form.notifyPush || form.notifySMS || form.notifyTelegram;
  if (!symbol) return "Add an asset symbol before saving the alert.";
  if (!/^[A-Z0-9.$:-]{2,24}$/.test(symbol)) return "Use a valid asset symbol such as BTC, ETH, or SOL.";
  if (!rawTarget) return "Add a target value before saving the alert.";
  if (!Number.isFinite(target)) return "Use a numeric target value.";
  if (target <= 0) return "Target value must be greater than zero.";
  if (target > 1_000_000_000_000) return "Target value is too large for a safe alert threshold.";
  if (!ALERT_CONDITIONS.some((condition) => condition.value === form.condition)) return "Choose a supported alert condition.";
  if (!hasChannel) return "Choose at least one delivery channel.";
  return "";
}

function ChannelToggle({ label, value, onPress }: { label: string; value: boolean; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={[styles.channelToggle, value ? styles.channelToggleActive : undefined]} onPress={onPress}>
      <Text style={[styles.channelToggleText, value ? styles.channelToggleTextActive : undefined]}>{value ? "On" : "Off"} - {label}</Text>
    </Pressable>
  );
}

function ActionButton({ label, onPress, disabled, variant = "primary" }: { label: string; onPress: () => void; disabled?: boolean; variant?: "primary" | "secondary" | "danger" }) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      style={[
        styles.actionButton,
        variant === "secondary" ? styles.actionButtonSecondary : undefined,
        variant === "danger" ? styles.actionButtonDanger : undefined,
        disabled ? styles.disabled : undefined
      ]}
      onPress={onPress}
    >
      <Text style={[styles.actionButtonText, variant !== "primary" ? styles.actionButtonTextSecondary : undefined]}>{label}</Text>
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 40,
    paddingHorizontal: 12
  },
  actionButtonDanger: {
    backgroundColor: "rgba(255, 107, 107, 0.16)",
    borderColor: "rgba(255, 107, 107, 0.38)",
    borderWidth: 1
  },
  actionButtonSecondary: {
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: colors.border,
    borderWidth: 1
  },
  actionButtonText: {
    color: "#08110f",
    fontSize: 13,
    fontWeight: "900"
  },
  actionButtonTextSecondary: {
    color: colors.text
  },
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 10
  },
  alertCard: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 5,
    paddingBottom: 14,
    paddingTop: 5
  },
  alertCardSelected: {
    backgroundColor: "rgba(37, 208, 167, 0.10)",
    borderRadius: 8,
    padding: 10
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
  confirmBox: {
    backgroundColor: "rgba(255, 107, 107, 0.08)",
    borderColor: "rgba(255, 107, 107, 0.34)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 10,
    marginTop: 10,
    padding: 10
  },
  confirmText: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 18
  },
  channelGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  channelToggle: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 40,
    justifyContent: "center",
    paddingHorizontal: 10
  },
  channelToggleActive: {
    borderColor: colors.accent,
    backgroundColor: "rgba(37, 208, 167, 0.12)"
  },
  channelToggleText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  channelToggleTextActive: {
    color: colors.accent
  },
  content: {
    gap: 14,
    padding: 18,
    paddingBottom: 34
  },
  detailTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
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
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 46,
    paddingHorizontal: 12
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  notice: {
    color: colors.accent,
    fontSize: 13,
    lineHeight: 19
  },
  panelHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between"
  },
  pill: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: 8,
    paddingVertical: 4,
    textTransform: "capitalize"
  },
  readinessCopy: {
    flex: 1
  },
  readinessRow: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 8,
    paddingBottom: 10
  },
  readyPill: {
    borderColor: colors.accent,
    color: colors.accent
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
    fontWeight: "900",
    textTransform: "capitalize"
  },
  score: {
    color: colors.text,
    fontSize: 38,
    fontWeight: "900"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  segment: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 38,
    justifyContent: "center",
    paddingHorizontal: 10
  },
  segmentActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  segmentText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  segmentTextActive: {
    color: "#08110f"
  },
  segmentWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
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
  },
  topActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  warnPill: {
    borderColor: colors.warning,
    color: colors.warning
  }
}));
