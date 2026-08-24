import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  ALERT_CHANNELS,
  ALERT_CONDITIONS,
  AlertChannel,
  ChannelReadiness,
  AlertClause,
  AlertEvent,
  AlertFormPayload,
  AlertManagementState,
  AlertOptions,
  AlertRule,
  AlertWindowOption,
  alertConditionLabel,
  alertEnabledChannels,
  alertStatusLabel,
  alertSubjectLabel,
  createCryptoAlert,
  deleteAlert,
  duplicateCryptoAlert,
  getAlertChannelReadiness,
  getAlertManagementState,
  getCryptoAlertHistory,
  getCryptoAlertOptions,
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
  note: "",
  mode: "basic",
  logic: "and",
  clauses: [],
  watchlistId: null
};

const emptyClause: AlertClause = { metric: "price", comparator: "above", value: "", windowMinutes: 0 };

// The server publishes each comparator's key and whether it is a level or a
// crossing test, but not a label: its own labels are notification copy, not UI
// strings. These are the app's words for the same four keys.
const COMPARATOR_LABELS: Record<string, string> = {
  above: "Above",
  below: "Below",
  crosses_above: "Crosses above",
  crosses_below: "Crosses below"
};

function comparatorLabel(key: string) {
  return COMPARATOR_LABELS[key] || key.replace(/_/g, " ");
}

// One sentence, said the same way whether the member pressed Advanced before the
// entitlement answer arrived or after it. Two wordings for one refusal would read
// as two different refusals.
const PREMIUM_ALERT_NOTICE = "Multi-condition alerts, watchlist alerts and time windows are part of PulseSoc Premium.";

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
  const [options, setOptions] = useState<AlertOptions | null>(null);
  const [optionsLoading, setOptionsLoading] = useState(true);

  const alerts = state?.alerts || [];
  const selectedAlert = useMemo(() => alerts.find((alert) => alert.id === selectedId) || null, [alerts, selectedId]);
  const readiness = (state?.channel_readiness || {}) as Record<AlertChannel, ChannelReadiness>;
  const recentEvents = historyEvents.length ? historyEvents : state?.events || [];
  const advanced = form.mode === "advanced";
  const clauses = form.clauses || [];
  const offeredWindows = options?.windows || [];
  const premiumLocked = Boolean(options && options.advanced.locked);
  // The list rule and the single-asset rule ask about different assets, so a
  // list being chosen is what decides which the options call is about.
  const symbolQuery = form.watchlistId ? "" : String(form.assetSymbol || "").trim().toUpperCase();
  const watchlistQuery = form.watchlistId || null;
  const windowKey = offeredWindows.map((window) => window.minutes).join(",");
  const clauseWindowKey = clauses.map((clause) => clause.windowMinutes).join(",");
  const watchlistOptions = options?.watchlists || [];
  const maxClauses = options?.advanced.max_clauses || 1;
  const logicItems = (options?.advanced.logic_modes || ["and"]).map((mode) => ({
    key: mode,
    label: mode === "or" ? "Any of these" : "All of these"
  }));
  // Ordered by the server, labelled by the app. Falling back to the local list
  // only covers the case where the options call failed outright — the basic form
  // has always worked offline and must keep working.
  const basicConditionItems = (options?.basic.conditions.length ? options.basic.conditions : ALERT_CONDITIONS.map((item) => item.value))
    .map((value) => ({
      key: value,
      label: ALERT_CONDITIONS.find((item) => item.value === value)?.label || value.replace(/_/g, " ")
    }));

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    // Debounced because the symbol changes on every keystroke: without it a
    // five-character ticker asks five times, and the last answer to arrive is
    // not necessarily the answer to the last question.
    let cancelled = false;
    setOptionsLoading(true);
    const timer = setTimeout(() => {
      // Anything shorter than the server's own minimum is sent as no symbol at
      // all. A half-typed "B" is not a question worth a 400, and the empty
      // answer carries the honest "choose an asset" reason instead.
      getCryptoAlertOptions(symbolQuery.length >= 2 ? symbolQuery : "", watchlistQuery)
        .then((next) => {
          if (!cancelled) setOptions(next);
        })
        .catch(() => {
          // No options means no advanced vocabulary and no windows offered. That
          // is the safe direction to fail in: the basic form still works.
          if (!cancelled) setOptions(null);
        })
        .finally(() => {
          if (!cancelled) setOptionsLoading(false);
        });
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [symbolQuery, watchlistQuery]);

  useEffect(() => {
    // The entitlement answer arrives a moment after the form does, so there is a
    // gap in which the Advanced button is pressable by an account that cannot use
    // it. Leaving it there would hand a free member a builder whose every field
    // the server refuses; this also catches an entitlement lapsing mid-session.
    if (optionsLoading || !premiumLocked) return;
    if (form.mode !== "advanced" && !form.watchlistId) return;
    setForm((current) => ({ ...current, mode: "basic", clauses: [], watchlistId: null }));
    setNotice(PREMIUM_ALERT_NOTICE);
  }, [optionsLoading, premiumLocked, form.mode, form.watchlistId]);

  useEffect(() => {
    // A window chosen for one asset can be unanswerable for the next one. Left
    // in place it would create exactly the rule this whole endpoint exists to
    // prevent: one that looks healthy and can never be decided.
    if (optionsLoading) return;
    const allowed = new Set(offeredWindows.map((window) => window.minutes));
    const stale = clauses.filter((clause) => clause.windowMinutes && !allowed.has(clause.windowMinutes));
    if (!stale.length) return;
    setForm((current) => ({
      ...current,
      clauses: (current.clauses || []).map((clause) =>
        clause.windowMinutes && !allowed.has(clause.windowMinutes) ? { ...clause, windowMinutes: 0 } : clause)
    }));
    setNotice(
      options?.window_message ||
      "That time window is not measurable for this asset yet, so it was cleared."
    );
  }, [windowKey, clauseWindowKey, optionsLoading]); // eslint-disable-line react-hooks/exhaustive-deps

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
    if (alert.is_advanced || alert.is_watchlist_rule) {
      // The edit path saves a single condition and threshold and does not touch
      // the stored clauses or the watched list. Opening one of these rules in it
      // would show a rule the member never wrote and save over half of the one
      // they did. Pause, delete and duplicate all still work on them.
      setSelectedId(alert.id);
      setError("");
      setNotice("Editing a multi-condition or watchlist alert is not available in the app yet. Delete it and create it again to change it.");
      return;
    }
    setEditingId(alert.id);
    setSelectedId(alert.id);
    setNotice("");
    setForm({
      ...emptyForm,
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

  function setClause(index: number, patch: Partial<AlertClause>) {
    setForm((current) => ({
      ...current,
      clauses: (current.clauses || []).map((clause, position) =>
        position === index ? { ...clause, ...patch } : clause)
    }));
  }

  async function saveForm() {
    const validationError = validateAlertForm(form, options);
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
    setNotice(`Confirm delete for ${alertSubjectLabel(alert)} ${alertConditionLabel(alert)}.`);
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

        {!editingId ? (
          <View style={styles.fieldGroup}>
            <SegmentRow
              items={[{ key: "basic", label: "Basic" }, { key: "advanced", label: "Advanced" }]}
              value={advanced ? "advanced" : "basic"}
              onSelect={(mode) => {
                if (mode === "advanced" && premiumLocked) {
                  setError("");
                  setNotice(PREMIUM_ALERT_NOTICE);
                  return;
                }
                setError("");
                setForm((current) => ({
                  ...current,
                  mode: mode === "advanced" ? "advanced" : "basic",
                  // The first clause is not seeded from the basic condition:
                  // the two vocabularies do not line up, and guessing a metric
                  // the member never chose is worse than an empty row.
                  clauses: mode === "advanced" && !(current.clauses || []).length ? [{ ...emptyClause }] : current.clauses
                }));
              }}
            />
            {premiumLocked ? (
              <Text style={styles.muted}>Advanced alerts — several conditions in one rule, whole-watchlist rules, and time windows — are part of PulseSoc Premium.</Text>
            ) : null}
          </View>
        ) : null}

        {!editingId && watchlistOptions.length ? (
          <View style={styles.fieldGroup}>
            <Text style={styles.fieldLabel}>What this alert watches</Text>
            <SegmentRow
              items={[{ key: "asset", label: "One asset" }, { key: "list", label: "A watchlist" }]}
              value={form.watchlistId ? "list" : "asset"}
              onSelect={(target) => {
                setError("");
                if (target === "asset") {
                  setForm((current) => ({ ...current, watchlistId: null }));
                  return;
                }
                const first = watchlistOptions.find((watchlist) => watchlist.eligible) || watchlistOptions[0];
                setForm((current) => ({ ...current, watchlistId: first?.id || null }));
              }}
            />
          </View>
        ) : null}

        {form.watchlistId ? (
          <View style={styles.fieldGroup}>
            {watchlistOptions.map((watchlist) => (
              <Pressable
                accessibilityRole="button"
                key={watchlist.id}
                disabled={!watchlist.eligible}
                style={[
                  styles.watchlistRow,
                  form.watchlistId === watchlist.id ? styles.watchlistRowActive : undefined,
                  watchlist.eligible ? undefined : styles.disabled
                ]}
                onPress={() => setForm((current) => ({ ...current, watchlistId: watchlist.id }))}
              >
                <View style={styles.rowHead}>
                  <Text style={styles.rowTitle}>{watchlist.name || `Watchlist ${watchlist.id}`}</Text>
                  <Text style={[styles.pill, watchlist.eligible ? styles.readyPill : styles.warnPill]}>
                    {watchlist.eligible ? `${watchlist.symbols.length} assets` : "Unavailable"}
                  </Text>
                </View>
                {/* The reason comes from the same check creation runs, so a list
                    shown as usable here is one creation accepts. */}
                {watchlist.eligible ? null : <Text style={styles.rowMeta}>{watchlist.message}</Text>}
              </Pressable>
            ))}
          </View>
        ) : (
          <TextInput
            accessibilityLabel="Alert symbol"
            autoCapitalize="characters"
            placeholder="Symbol"
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={form.assetSymbol}
            onChangeText={(assetSymbol) => setForm((current) => ({ ...current, assetSymbol }))}
          />
        )}

        {advanced && !editingId ? (
          <View style={styles.fieldGroup}>
            <Text style={styles.fieldLabel}>Conditions</Text>
            <SegmentRow
              items={logicItems}
              value={form.logic || "and"}
              onSelect={(logic) => setForm((current) => ({ ...current, logic }))}
            />
            <Text style={styles.rowMeta}>
              {form.logic === "or"
                ? "The alert fires when any one of these is true."
                : "The alert fires only while all of these are true at once."}
            </Text>
            {clauses.map((clause, index) => (
              <ClauseEditor
                key={index}
                clause={clause}
                index={index}
                metrics={options?.advanced.metrics || []}
                comparators={options?.advanced.comparators || []}
                windowComparators={options?.advanced.window_comparators || []}
                windows={offeredWindows}
                windowMessage={options?.window_message || ""}
                canRemove={clauses.length > 1}
                onChange={(patch) => setClause(index, patch)}
                onRemove={() => setForm((current) => ({
                  ...current,
                  clauses: (current.clauses || []).filter((_, position) => position !== index)
                }))}
              />
            ))}
            {clauses.length < maxClauses ? (
              <ActionButton
                label="Add condition"
                variant="secondary"
                onPress={() => setForm((current) => ({ ...current, clauses: [...(current.clauses || []), { ...emptyClause }] }))}
              />
            ) : (
              <Text style={styles.rowMeta}>One alert can hold up to {maxClauses} conditions.</Text>
            )}
          </View>
        ) : (
          <>
            <TextInput
              accessibilityLabel="Alert target value"
              keyboardType="numeric"
              placeholder="Target value"
              placeholderTextColor={colors.muted}
              style={styles.input}
              value={form.targetValue}
              onChangeText={(targetValue) => setForm((current) => ({ ...current, targetValue }))}
            />
            {/* Ordered by the server rather than by this file, so the buttons
                cannot offer a condition creation has stopped accepting. */}
            <SegmentRow
              items={basicConditionItems}
              value={form.condition}
              onSelect={(condition) => setForm((current) => ({ ...current, condition }))}
            />
          </>
        )}

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
          <Text style={styles.detailTitle}>{alertSubjectLabel(selectedAlert)} {alertConditionLabel(selectedAlert)}</Text>
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
          {/* A watchlist rule has no symbol of its own, so naming it by one would
              render every such rule as the same anonymous " alert". */}
          <Text style={styles.rowTitle}>{alertSubjectLabel(alert)} alert</Text>
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

/**
 * The last check before the request goes out, phrased in the member's terms.
 *
 * It is deliberately not the authority: the server validates every one of these
 * again and refuses anything it disagrees with. What this buys is a sentence the
 * member can act on instead of a 400, and — for the window rules especially — a
 * refusal to send a rule that would be accepted but could never be decided.
 *
 * `options` is nullable on purpose. When the options call failed there is no
 * advanced vocabulary on screen either, so the basic path validates exactly as
 * it always did rather than blocking on an answer that never arrived.
 */
function validateAlertForm(form: AlertFormPayload, options: AlertOptions | null) {
  const hasChannel = form.notifyInApp || form.notifyEmail || form.notifyPush || form.notifySMS || form.notifyTelegram;
  const advanced = form.mode === "advanced";

  if (form.watchlistId) {
    // A list rule is about no single asset, so the symbol is not checked at all.
    // Eligibility is the server's own preflight answer, carried in the options
    // payload — re-deriving it here is how the form and the gate start to differ.
    const watchlist = (options?.watchlists || []).find((entry) => entry.id === form.watchlistId);
    if (!watchlist) return "Choose a watchlist for this alert to watch.";
    if (!watchlist.eligible) return watchlist.message || "That watchlist cannot be used for an alert right now.";
  } else {
    const symbol = form.assetSymbol.trim().toUpperCase();
    if (!symbol) return "Add an asset symbol before saving the alert.";
    if (!/^[A-Z0-9.$:-]{2,24}$/.test(symbol)) return "Use a valid asset symbol such as BTC, ETH, or SOL.";
  }

  if (advanced) {
    if (options?.advanced.locked) return "Multi-condition alerts are part of PulseSoc Premium.";
    const clauses = form.clauses || [];
    if (!clauses.length) return "Add at least one condition before saving the alert.";
    const maxClauses = options?.advanced.max_clauses || clauses.length;
    if (clauses.length > maxClauses) return `One alert can hold up to ${maxClauses} conditions.`;
    const offered = new Set((options?.windows || []).map((window) => window.minutes));
    const windowComparators = new Set(options?.advanced.window_comparators || []);
    for (let index = 0; index < clauses.length; index += 1) {
      const clause = clauses[index];
      const position = `Condition ${index + 1}`;
      const metric = (options?.advanced.metrics || []).find((entry) => entry.key === clause.metric);
      if (!metric) return `${position}: choose what to measure.`;
      if (!(options?.advanced.comparators || []).some((entry) => entry.key === clause.comparator)) {
        return `${position}: choose a supported comparison.`;
      }
      const raw = String(clause.value || "").trim();
      if (!raw) return `${position}: add a value.`;
      const value = Number(raw);
      if (!Number.isFinite(value)) return `${position}: use a numeric value.`;
      // A percentage change is genuinely negative half the time; a price, a
      // volume or a market cap never is, and a negative one would arm forever.
      if (!metric.percent && value <= 0) return `${position}: ${metric.label} must be greater than zero.`;
      if (Math.abs(value) > 1_000_000_000_000) return `${position}: that value is too large for a safe threshold.`;
      if (!clause.windowMinutes) continue;
      if (!metric.windowable) return `${position}: ${metric.label} cannot be measured over a time window.`;
      // The offered set comes from what has actually been sampled. Sending a
      // window outside it would create the one failure mode this whole options
      // endpoint exists to prevent: a rule that looks healthy and never decides.
      if (!offered.has(clause.windowMinutes)) {
        return options?.window_message || `${position}: that time window cannot be measured for this asset yet.`;
      }
      // A window's baseline moves with every sample, so a crossing over one
      // would fire on the baseline shifting rather than on the market moving.
      if (windowComparators.size && !windowComparators.has(clause.comparator)) {
        return `${position}: ${comparatorLabel(clause.comparator).toLowerCase()} cannot be used with a time window.`;
      }
    }
  } else {
    const rawTarget = form.targetValue.trim();
    const target = Number(rawTarget);
    const conditions = options?.basic.conditions.length
      ? options.basic.conditions
      : ALERT_CONDITIONS.map((condition) => condition.value);
    if (!rawTarget) return "Add a target value before saving the alert.";
    if (!Number.isFinite(target)) return "Use a numeric target value.";
    if (target <= 0) return "Target value must be greater than zero.";
    if (target > 1_000_000_000_000) return "Target value is too large for a safe alert threshold.";
    if (!conditions.includes(form.condition)) return "Choose a supported alert condition.";
  }

  if (!hasChannel) return "Choose at least one delivery channel.";
  return "";
}

/** A row of mutually exclusive choices. The selected key is owned by the caller. */
function SegmentRow({
  items,
  value,
  disabled,
  onSelect
}: {
  items: { key: string; label: string }[];
  value: string;
  disabled?: boolean;
  onSelect: (key: string) => void;
}) {
  return (
    <View style={styles.segmentWrap}>
      {items.map((item) => {
        const active = item.key === value;
        return (
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ selected: active, disabled: Boolean(disabled) }}
            disabled={disabled}
            key={item.key}
            style={[styles.segment, active ? styles.segmentActive : undefined, disabled ? styles.disabled : undefined]}
            onPress={() => onSelect(item.key)}
          >
            <Text style={[styles.segmentText, active ? styles.segmentTextActive : undefined]}>{item.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/**
 * One clause of a compound rule: what to measure, how to compare it, against
 * what, and optionally over how long.
 *
 * Every vocabulary it renders is passed in from the options payload rather than
 * held here, because two of them are not constants — which windows are
 * answerable depends on how long this asset has been sampled, and which
 * comparators a window allows is a server rule this file must not restate.
 */
function ClauseEditor({
  clause,
  index,
  metrics,
  comparators,
  windowComparators,
  windows,
  windowMessage,
  canRemove,
  onChange,
  onRemove
}: {
  clause: AlertClause;
  index: number;
  metrics: AlertOptions["advanced"]["metrics"];
  comparators: AlertOptions["advanced"]["comparators"];
  windowComparators: string[];
  windows: AlertWindowOption[];
  windowMessage: string;
  canRemove: boolean;
  onChange: (patch: Partial<AlertClause>) => void;
  onRemove: () => void;
}) {
  const metric = metrics.find((entry) => entry.key === clause.metric) || null;
  const windowed = Boolean(clause.windowMinutes);
  // With a window on, only the level comparators are offered — the server
  // refuses the crossings, and offering one it would refuse is worse than not
  // offering it at all.
  const offeredComparators = (windowed && windowComparators.length
    ? comparators.filter((entry) => windowComparators.includes(entry.key))
    : comparators
  ).map((entry) => ({ key: entry.key, label: comparatorLabel(entry.key) }));

  return (
    <View style={styles.clauseCard}>
      <View style={styles.rowHead}>
        <Text style={styles.rowTitle}>Condition {index + 1}</Text>
        {canRemove ? <ActionButton label="Remove" variant="secondary" onPress={onRemove} /> : null}
      </View>

      <SegmentRow
        items={metrics.map((entry) => ({ key: entry.key, label: entry.label }))}
        value={clause.metric}
        onSelect={(next) => {
          const chosen = metrics.find((entry) => entry.key === next);
          // A window makes no sense on a metric that cannot carry one, and a
          // window left behind from the previous metric would be sent anyway.
          onChange({ metric: next, ...(chosen?.windowable ? {} : { windowMinutes: 0 }) });
        }}
      />

      <SegmentRow
        items={offeredComparators}
        value={clause.comparator}
        onSelect={(comparator) => onChange({ comparator })}
      />

      <TextInput
        accessibilityLabel={`Condition ${index + 1} value`}
        keyboardType="numbers-and-punctuation"
        placeholder={metric?.percent ? "Percent, for example -5" : "Value"}
        placeholderTextColor={colors.muted}
        style={styles.input}
        value={clause.value}
        onChangeText={(value) => onChange({ value })}
      />

      {metric?.windowable ? (
        windows.length ? (
          <>
            <Text style={styles.rowMeta}>Measured over</Text>
            <SegmentRow
              items={[{ key: "0", label: "No window" }, ...windows.map((window) => ({ key: String(window.minutes), label: window.label }))]}
              value={String(clause.windowMinutes || 0)}
              onSelect={(next) => {
                const windowMinutes = Number(next) || 0;
                // Turning a window on can invalidate the chosen comparator. Moving
                // it to the first allowed one keeps the clause coherent instead of
                // leaving a selection that no longer appears among the buttons.
                const allowed = windowComparators.length ? windowComparators : comparators.map((entry) => entry.key);
                const comparator = windowMinutes && !allowed.includes(clause.comparator)
                  ? allowed[0] || clause.comparator
                  : clause.comparator;
                onChange({ windowMinutes, comparator });
              }}
            />
          </>
        ) : (
          // Not an error and not a missing feature: the series simply has not
          // observed this asset for long enough to answer any window yet.
          <Text style={styles.rowMeta}>{windowMessage || "No time window can be measured for this asset yet."}</Text>
        )
      ) : null}
    </View>
  );
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
  clauseCard: {
    backgroundColor: "rgba(255,255,255,0.03)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    padding: 10
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
  fieldGroup: {
    gap: 8
  },
  fieldLabel: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
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
  },
  watchlistRow: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 4,
    padding: 10
  },
  watchlistRowActive: {
    backgroundColor: "rgba(37, 208, 167, 0.10)",
    borderColor: colors.accent
  }
}));
