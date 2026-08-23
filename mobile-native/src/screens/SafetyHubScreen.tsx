import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  createSafetyBlock,
  createSafetyReport,
  loadCachedSafetyState,
  loadSafetyState,
  openSafetyWebFallback,
  recordMuteHandoff,
  recordUnblockHandoff,
  SafetyActionRecord,
  SafetyState
} from "../api/safety";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "SafetyHub"> | NativeStackScreenProps<RootStackParamList, "SafetyWebHub">;
type SafetyTab = "overview" | "blocks" | "mutes" | "reports";

const reportTypes = ["user", "post", "reel", "message", "marketplace", "status"];
const muteDurations = ["1 hour", "8 hours", "24 hours", "7 days", "until changed"];

export function SafetyHubScreen({ navigation, route }: Props) {
  const { authState } = useAuth();
  // Wrong-subject guard: blocks, mutes and reports are the signed-in viewer's
  // safety state. On another profile's route params this screen refuses rather
  // than showing the viewer's private safety data under that person's name.
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  const initialSection = route.params?.section;
  const [state, setState] = useState<SafetyState | null>(null);
  const [tab, setTab] = useState<SafetyTab>(initialSection === "blocks" || initialSection === "mutes" || initialSection === "reports" ? initialSection : "overview");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [blockHandle, setBlockHandle] = useState(route.params?.blockTarget || "");
  const [blockReason, setBlockReason] = useState("Unsafe interaction");
  const [muteTarget, setMuteTarget] = useState(route.params?.muteTarget || "");
  const [muteDuration, setMuteDuration] = useState("24 hours");
  const [muteReason, setMuteReason] = useState("Reduce noise");
  const [reportType, setReportType] = useState(reportTypes.includes(route.params?.reportType || "") ? route.params?.reportType || "user" : "user");
  const [reportTarget, setReportTarget] = useState(route.params?.reportTarget || "");
  const [reportReason, setReportReason] = useState("");

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      setState(await loadSafetyState());
    } catch (loadError) {
      const cached = await loadCachedSafetyState();
      if (cached) {
        setState(cached);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Safety Hub could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    navigation.setOptions({ title: "Safety Hub" });
  }, [navigation]);

  useEffect(() => {
    // Owner-only fetch: skip entirely on a visitor route (no fetch-then-hide).
    if (!routeContext.isOwnProfile) return;
    load("initial").catch(() => undefined);
  }, [load, routeContext.isOwnProfile]);

  async function submitBlock() {
    if (!blockHandle.trim()) {
      setError("Enter a public PulseSoc ID or user ID to block.");
      setNotice("");
      return;
    }
    setBusy("block");
    setError("");
    setNotice("");
    try {
      const isNumeric = /^\d+$/.test(blockHandle.trim());
      const { response } = await createSafetyBlock({
        blockedUserId: isNumeric ? blockHandle.trim() : undefined,
        publicPlayerId: isNumeric ? undefined : blockHandle.trim().replace(/^@/, ""),
        reason: blockReason.trim() || "Blocked from native Safety Hub"
      });
      setBlockHandle("");
      setNotice(response.message || "User blocked and sent to moderation.");
      await load("refresh");
      setTab("blocks");
    } catch (blockError) {
      setError(blockError instanceof Error ? blockError.message : "Block action failed.");
    } finally {
      setBusy("");
    }
  }

  async function submitReport() {
    if (!reportTarget.trim() || !reportReason.trim()) {
      setError("Enter a report target and reason.");
      setNotice("");
      return;
    }
    setBusy("report");
    setError("");
    setNotice("");
    try {
      const { response } = await createSafetyReport({
        targetType: reportType,
        targetId: reportTarget.trim(),
        reason: reportReason.trim()
      });
      setReportTarget("");
      setReportReason("");
      setNotice(response.message || "Report submitted for review.");
      await load("refresh");
      setTab("reports");
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : "Report could not be submitted.");
    } finally {
      setBusy("");
    }
  }

  async function submitMuteHandoff() {
    if (!muteTarget.trim()) {
      setError("Enter the user or conversation to mute.");
      setNotice("");
      return;
    }
    await recordMuteHandoff({ target: muteTarget.trim(), duration: muteDuration, reason: muteReason.trim() || "Native mute handoff" });
    setNotice("Saved on this device only. Mute them on the PulseSoc website to make it apply everywhere.");
    setMuteTarget("");
    await load("refresh");
    setTab("mutes");
  }

  async function submitUnblockHandoff(target: string) {
    setBusy(`unblock-${target}`);
    setError("");
    setNotice("");
    try {
      await recordUnblockHandoff({ target, reason: "Review unblock request from native Safety Hub" });
      setNotice("Unblock request recorded locally. Open protected safety controls to change server state.");
      await load("refresh");
    } finally {
      setBusy("");
    }
  }

  // Visitor destination with no visitor variant: refuse rather than render the
  // viewer's safety state. All hooks have already run, so order is stable.
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
        <Text style={styles.centerText}>Loading Safety Hub</Text>
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
        <Text style={styles.eyebrow}>PulseSoc safety control layer</Text>
        <Text style={styles.title}>Safety Hub</Text>
        <Text style={styles.subtitle}>
          {offline ? "Showing cached safety controls. Pull to reconnect." : "Blocks, mutes, and reports are handled by PulseSoc moderation. What you set here follows those decisions."}
        </Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <View style={styles.tabs}>
        <TabButton label="Overview" value="overview" active={tab} onPress={setTab} />
        <TabButton label="Blocks" value="blocks" active={tab} onPress={setTab} />
        <TabButton label="Mutes" value="mutes" active={tab} onPress={setTab} />
        <TabButton label="Reports" value="reports" active={tab} onPress={setTab} />
      </View>

      {tab === "overview" ? <Overview state={state} navigation={navigation} /> : null}
      {tab === "blocks" ? (
        <BlocksPanel
          blockHandle={blockHandle}
          blockReason={blockReason}
          busy={busy}
          blocks={state?.blocks || []}
          onBlockHandle={setBlockHandle}
          onBlockReason={setBlockReason}
          onSubmitBlock={submitBlock}
          onSubmitUnblock={submitUnblockHandoff}
        />
      ) : null}
      {tab === "mutes" ? (
        <MutesPanel
          busy={busy}
          muteDuration={muteDuration}
          muteReason={muteReason}
          muteTarget={muteTarget}
          mutes={state?.mutes || []}
          onMuteDuration={setMuteDuration}
          onMuteReason={setMuteReason}
          onMuteTarget={setMuteTarget}
          onSubmitMute={submitMuteHandoff}
        />
      ) : null}
      {tab === "reports" ? (
        <ReportsPanel
          busy={busy}
          cases={state?.cases || []}
          reportReason={reportReason}
          reportTarget={reportTarget}
          reportType={reportType}
          reports={state?.reports || []}
          onReportReason={setReportReason}
          onReportTarget={setReportTarget}
          onReportType={setReportType}
          onSubmitReport={submitReport}
        />
      ) : null}

      <Panel>
        <Text style={styles.panelTitle}>What is handled where</Text>
        <Text style={styles.muted}>
          You can block someone and file a report from here. Your full blocked list, unblocking, muting a person, review outcomes, and moderator decisions live with the PulseSoc safety team — open the protected safety controls below to reach them.
        </Text>
        <View style={styles.buttonGrid}>
          <ActionButton label="Protected safety controls" variant="secondary" onPress={() => openSafetyWebFallback("/dashboard/network/network-security")} />
          <ActionButton label="Account Health" variant="secondary" onPress={() => navigation.navigate("AccountHealth", { title: "Account Health" })} />
          <ActionButton label="Trust & Safety" variant="secondary" onPress={() => navigation.navigate("TrustSafety", { title: "Trust & Safety", mode: "support" })} />
        </View>
      </Panel>
    </ScrollView>
  );
}

function Overview({ state, navigation }: { state: SafetyState | null; navigation: Props["navigation"] }) {
  const network = state?.network;
  return (
    <>
      <View style={styles.metricGrid}>
        <Metric label="Blocked" value={network?.blockedUsers || 0} tone={(network?.blockedUsers || 0) > 0 ? "warn" : "ok"} />
        <Metric label="Muted chats" value={network?.mutedConversations || 0} tone={(network?.mutedConversations || 0) > 0 ? "warn" : "ok"} />
        <Metric label="Trust score" value={network?.networkTrustScore || 0} tone="ok" />
        <Metric label="Safety updates" value={network?.securityUpdates || 0} tone={(network?.securityUpdates || 0) > 0 ? "warn" : "ok"} />
      </View>
      <Panel>
        <Text style={styles.panelTitle}>Safety command center</Text>
        <Text style={styles.muted}>A calm control layer for relationship boundaries, report follow-up, and protected recovery paths.</Text>
        {(network?.recommendations || []).map((item, index) => (
          <Text key={`${item}-${index}`} style={styles.recommendation}>- {item}</Text>
        ))}
        <View style={styles.buttonGrid}>
          <ActionButton label="Open Messenger" variant="secondary" onPress={() => navigation.navigate("Tabs", { screen: "Messenger" })} />
          <ActionButton label="Open Profile" variant="secondary" onPress={() => navigation.navigate("Tabs", { screen: "Profile" })} />
        </View>
      </Panel>
    </>
  );
}

function BlocksPanel({
  blockHandle,
  blockReason,
  blocks,
  busy,
  onBlockHandle,
  onBlockReason,
  onSubmitBlock,
  onSubmitUnblock
}: {
  blockHandle: string;
  blockReason: string;
  blocks: SafetyActionRecord[];
  busy: string;
  onBlockHandle: (value: string) => void;
  onBlockReason: (value: string) => void;
  onSubmitBlock: () => void;
  onSubmitUnblock: (target: string) => void;
}) {
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>Block user</Text>
        <Text style={styles.muted}>Blocking someone removes them from your feeds across PulseSoc and stops them reaching you.</Text>
        <TextInput accessibilityLabel="Block target" autoCapitalize="none" placeholder="@public_id or numeric user ID" placeholderTextColor={colors.muted} style={styles.input} value={blockHandle} onChangeText={onBlockHandle} />
        <TextInput accessibilityLabel="Block reason" placeholder="Reason" placeholderTextColor={colors.muted} style={styles.input} value={blockReason} onChangeText={onBlockReason} />
        <ActionButton label={busy === "block" ? "Blocking..." : "Block user"} disabled={Boolean(busy)} onPress={onSubmitBlock} />
      </Panel>
      <Panel>
        <Text style={styles.panelTitle}>Blocked list visibility</Text>
        <Text style={styles.muted}>PulseSoc keeps your complete blocked list, and shows you a total. The history below covers only the blocks you made from this app on this device.</Text>
        {blocks.length ? blocks.map((item) => <ActionRow key={item.id} action={item} actionLabel="Review unblock" busy={busy === `unblock-${item.targetLabel}`} onAction={() => onSubmitUnblock(item.targetLabel)} />) : <Text style={styles.muted}>No native block actions recorded on this device.</Text>}
      </Panel>
    </>
  );
}

function MutesPanel({
  busy,
  muteDuration,
  muteReason,
  muteTarget,
  mutes,
  onMuteDuration,
  onMuteReason,
  onMuteTarget,
  onSubmitMute
}: {
  busy: string;
  muteDuration: string;
  muteReason: string;
  muteTarget: string;
  mutes: SafetyActionRecord[];
  onMuteDuration: (value: string) => void;
  onMuteReason: (value: string) => void;
  onMuteTarget: (value: string) => void;
  onSubmitMute: () => void;
}) {
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>Mute management</Text>
        <Text style={styles.muted}>You can mute a conversation on PulseSoc. Muting a person outright is not available in the app yet, so this records your request and takes you to the full mute controls.</Text>
        <TextInput accessibilityLabel="Mute target" autoCapitalize="none" placeholder="@public_id, conversation, or username" placeholderTextColor={colors.muted} style={styles.input} value={muteTarget} onChangeText={onMuteTarget} />
        <ChoiceRow options={muteDurations} value={muteDuration} onChange={onMuteDuration} />
        <TextInput accessibilityLabel="Mute reason" placeholder="Reason" placeholderTextColor={colors.muted} style={styles.input} value={muteReason} onChangeText={onMuteReason} />
        <ActionButton label={busy === "mute" ? "Recording..." : "Record mute handoff"} disabled={Boolean(busy)} onPress={onSubmitMute} />
        <ActionButton label="Open server mute controls" variant="secondary" disabled={Boolean(busy)} onPress={() => openSafetyWebFallback("/dashboard/network/messages")} />
      </Panel>
      <Panel>
        <Text style={styles.panelTitle}>Mute handoffs</Text>
        {mutes.length ? mutes.map((item) => <ActionRow key={item.id} action={item} />) : <Text style={styles.muted}>No native mute handoffs recorded on this device.</Text>}
      </Panel>
    </>
  );
}

function ReportsPanel({
  busy,
  cases,
  reportReason,
  reportTarget,
  reportType,
  reports,
  onReportReason,
  onReportTarget,
  onReportType,
  onSubmitReport
}: {
  busy: string;
  cases: SafetyState["cases"];
  reportReason: string;
  reportTarget: string;
  reportType: string;
  reports: SafetyActionRecord[];
  onReportReason: (value: string) => void;
  onReportTarget: (value: string) => void;
  onReportType: (value: string) => void;
  onSubmitReport: () => void;
}) {
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>Create report</Text>
        <ChoiceRow options={reportTypes} value={reportType} onChange={onReportType} />
        <TextInput accessibilityLabel="Report target" autoCapitalize="none" placeholder="Target ID, URL, handle, or message ID" placeholderTextColor={colors.muted} style={styles.input} value={reportTarget} onChangeText={onReportTarget} />
        <TextInput accessibilityLabel="Report reason" multiline placeholder="Describe the issue. Do not paste private keys, passwords, or one-time codes." placeholderTextColor={colors.muted} style={[styles.input, styles.textArea]} value={reportReason} onChangeText={onReportReason} />
        <ActionButton label={busy === "report" ? "Submitting..." : "Submit report"} disabled={Boolean(busy)} onPress={onSubmitReport} />
      </Panel>
      <Panel>
        <Text style={styles.panelTitle}>Report history</Text>
        <Text style={styles.muted}>The PulseSoc safety team reviews every report, though the outcome is not shown in the app yet. Below are the reports you sent from this app, plus your support cases.</Text>
        {reports.length ? reports.map((item) => <ActionRow key={item.id} action={item} />) : <Text style={styles.muted}>No native report actions recorded on this device.</Text>}
        {cases.slice(0, 4).map((ticket) => (
          <View key={ticket.id} style={styles.row}>
            <View style={styles.rowCopy}>
              <Text style={styles.rowTitle}>Case #{ticket.id}</Text>
              <Text style={styles.muted}>{ticket.subject || "Support case"} · {ticket.issue_type || "support"}</Text>
            </View>
            <Text style={styles.statusPill}>{ticket.status || "open"}</Text>
          </View>
        ))}
      </Panel>
    </>
  );
}

function ActionRow({ action, actionLabel, busy, onAction }: { action: SafetyActionRecord; actionLabel?: string; busy?: boolean; onAction?: () => void }) {
  return (
    <View style={styles.row}>
      <View style={styles.rowCopy}>
        <Text style={styles.rowTitle}>{action.targetLabel}</Text>
        <Text style={styles.muted}>{action.reason}</Text>
        <Text style={styles.muted}>{action.message} · {action.createdAt || "recent"}</Text>
      </View>
      <View style={styles.rowActions}>
        <Text style={[styles.statusPill, action.serverAuthoritative && styles.statusPillActive]}>{action.status}</Text>
        {onAction && actionLabel ? <ActionButton label={busy ? "Opening..." : actionLabel} variant="secondary" disabled={busy} onPress={onAction} /> : null}
      </View>
    </View>
  );
}

function ChoiceRow({ options, value, onChange }: { options: string[]; value: string; onChange: (value: string) => void }) {
  return (
    <View style={styles.choiceRow}>
      {options.map((option) => (
        <Pressable key={option} accessibilityRole="button" style={[styles.choicePill, value === option && styles.choicePillActive]} onPress={() => onChange(option)}>
          <Text style={[styles.choiceText, value === option && styles.choiceTextActive]}>{option.replace(/_/g, " ")}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function TabButton({ label, value, active, onPress }: { label: string; value: SafetyTab; active: SafetyTab; onPress: (value: SafetyTab) => void }) {
  return (
    <Pressable accessibilityRole="button" style={[styles.tab, active === value && styles.tabActive]} onPress={() => onPress(value)}>
      <Text style={[styles.tabText, active === value && styles.tabTextActive]}>{label}</Text>
    </Pressable>
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

const styles = createThemedStyles(() => ({
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 42,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  actionText: {
    color: "#08110f",
    fontWeight: "900"
  },
  buttonGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    gap: 12,
    justifyContent: "center"
  },
  centerText: {
    color: colors.text,
    fontWeight: "800"
  },
  choicePill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    minHeight: 34,
    paddingHorizontal: 11
  },
  choicePillActive: {
    backgroundColor: "rgba(37,208,167,0.16)",
    borderColor: colors.accent
  },
  choiceRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  choiceText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  choiceTextActive: {
    color: colors.text
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
    minWidth: 140,
    padding: 12
  },
  metricDanger: {
    backgroundColor: "rgba(255,107,107,0.1)",
    borderColor: colors.danger
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  metricValue: {
    color: colors.text,
    fontSize: 26,
    fontWeight: "900"
  },
  metricWarn: {
    backgroundColor: "rgba(243,185,78,0.1)",
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
    fontSize: 14,
    lineHeight: 21
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  row: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    padding: 10
  },
  rowActions: {
    alignItems: "flex-start",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  rowCopy: {
    gap: 4
  },
  rowTitle: {
    color: colors.text,
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
    alignSelf: "flex-start",
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
    borderColor: colors.accent,
    color: colors.accent
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  tab: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    justifyContent: "center",
    minHeight: 40,
    minWidth: 78
  },
  tabActive: {
    backgroundColor: "rgba(37,208,167,0.14)",
    borderColor: colors.accent
  },
  tabText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  tabTextActive: {
    color: colors.text
  },
  tabs: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
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
}));
